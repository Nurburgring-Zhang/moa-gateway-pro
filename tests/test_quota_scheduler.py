"""tests/test_quota_scheduler.py — M2 quota telemetry scheduler (Agent A).

Covers moa_gateway/quota_scheduler/* and moa_gateway/routes/quota.py.
All mechanisms are faithful ports of OmniRoute
(https://github.com/diegosouzapw/OmniRoute, MIT license): header parsing,
source-priority merge, saturation buckets, leased in-flight counters,
DRR + P2C quota-share selection, change-detected snapshots and the adaptive
monitor cadence. Clocks are injected everywhere (deterministic, no network).

The HTTP app is self-built per the frozen architecture contract:
    app = FastAPI(); app.include_router(routes.quota.router)
so these tests never depend on moa_gateway/server.py or routes/__init__.py.

Controlled test doubles: NONE on the real path — persistence tests run against
a real isolated SQLite Storage registered as the process singleton.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.quota_scheduler.buckets import BucketStore, get_buckets, reset_buckets_for_tests
from moa_gateway.quota_scheduler.collection import (
    ConfiguredQuotaAdapter,
    EstimatedQuotaAdapter,
    ResponseHeaderAdapter,
    collect_quota_state,
)
from moa_gateway.quota_scheduler.headers import (
    parse_quota_headers,
    parse_rate_limit_headers,
    parse_reset_time,
    reset_header_to_iso,
    retry_after_ms,
)
from moa_gateway.quota_scheduler.inflight import InflightTracker, get_inflight_tracker, reset_inflight_for_tests
from moa_gateway.quota_scheduler.models import (
    QuotaState,
    QuotaValue,
    merge_quota_values,
    source_rank,
    status_for_values,
)
from moa_gateway.quota_scheduler.monitor import QuotaMonitor, reset_monitor_for_tests
from moa_gateway.quota_scheduler.quota_share import (
    DrrState,
    ShareTarget,
    bare_model_name,
    normalize_weight,
    reset_quota_share_for_tests,
    select_quota_share_target,
)
from moa_gateway.quota_scheduler.snapshots import (
    SnapshotStore,
    compute_change_key,
    reset_snapshot_store_for_tests,
)

API_KEY = "quota-test-key-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
NOW_MS = 1_750_000_000_000.0  # fixed clock


def iso(now_ms: float) -> str:
    dt = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# ============ fixtures ============


@pytest.fixture(autouse=True)
def _reset_quota_singletons():
    reset_monitor_for_tests()
    reset_snapshot_store_for_tests()
    reset_buckets_for_tests()
    reset_inflight_for_tests()
    reset_quota_share_for_tests()
    yield
    reset_monitor_for_tests()
    reset_snapshot_store_for_tests()
    reset_buckets_for_tests()
    reset_inflight_for_tests()
    reset_quota_share_for_tests()


@pytest.fixture
def gateway_settings(monkeypatch):
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        }
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    return settings


@pytest.fixture
def quota_storage(tmp_path, make_settings):
    """Isolated real Storage registered as the process singleton so the lazy
    ``get_storage()`` calls inside the scheduler hit this exact DB."""
    from moa_gateway.storage import Storage

    settings = make_settings()
    with patch("moa_gateway.storage.get_settings", return_value=settings):
        with patch("moa_gateway.storage.DATA_DIR", tmp_path):
            Storage._instance = None
            s = Storage(db_path=tmp_path / "quota.db")
            Storage._instance = s
            yield s
            Storage._instance = None


@pytest.fixture
def snapshot_store(quota_storage):
    return SnapshotStore(max_snapshots=100)


@pytest.fixture
def monitor(snapshot_store):
    return QuotaMonitor(snapshots=snapshot_store)


def quota_settings(monkeypatch, **quota_overrides):
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        quota=quota_overrides or {},
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    return settings


# ============ header parsing ============


def test_parse_reset_time_duration_strings():
    assert parse_reset_time("1h30m", NOW_MS) == 5_400_000
    assert parse_reset_time("45s", NOW_MS) == 45_000
    assert parse_reset_time("500ms", NOW_MS) == 500
    assert parse_reset_time("2h", NOW_MS) == 7_200_000
    assert parse_reset_time("1m", NOW_MS) == 60_000


def test_parse_reset_time_plain_seconds_and_unix_timestamp():
    assert parse_reset_time("120", NOW_MS) == 120_000
    future_unix = (NOW_MS + 3_600_000) / 1000  # one hour ahead, epoch seconds
    delta = parse_reset_time(str(future_unix), NOW_MS)
    assert delta == pytest.approx(3_600_000, abs=1000)


def test_parse_reset_time_iso_and_garbage():
    delta = parse_reset_time(iso(NOW_MS + 90_000), NOW_MS)
    assert delta == pytest.approx(90_000, abs=1000)
    assert parse_reset_time("not-a-time", NOW_MS) is None
    assert parse_reset_time(None, NOW_MS) is None
    assert parse_reset_time("", NOW_MS) is None


def test_reset_header_to_iso_seconds_vs_millis_vs_garbage():
    seconds_iso = reset_header_to_iso("1750000000")
    millis_iso = reset_header_to_iso("1750000000000")
    assert seconds_iso == millis_iso  # same instant, different scales
    assert seconds_iso.startswith("2025-06-15T")
    assert reset_header_to_iso("garbage") is None
    assert reset_header_to_iso(None) is None
    # ISO input round-trips to a UTC Z-suffixed ISO string.
    assert reset_header_to_iso(iso(NOW_MS)).endswith("Z")


def test_parse_rate_limit_headers_standard_family():
    headers = {
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-remaining-requests": "42",
        "x-ratelimit-reset-requests": str(int((NOW_MS + 60_000) / 1000)),
    }
    mapping = {
        "limit": "x-ratelimit-limit-requests",
        "remaining": "x-ratelimit-remaining-requests",
        "reset": "x-ratelimit-reset-requests",
        "retryAfter": "retry-after",
        "dimension": "requests",
        "unit": "requests",
    }
    parsed = parse_rate_limit_headers(headers, "openai", "conn1", mapping)
    assert parsed is not None
    value: QuotaValue = parsed["value"]
    assert value.dimension == "requests"
    assert value.limit == 100.0
    assert value.remaining == 42.0
    assert value.source == "response_headers"
    assert value.confidence == "high"
    assert value.reset_at is not None
    snapshot = parsed["snapshot"]
    assert snapshot["provider_id"] == "openai"
    assert snapshot["request_limit"] == 100.0
    assert snapshot["requests_remaining"] == 42.0


def test_parse_rate_limit_headers_case_insensitive_and_absent():
    headers = {"X-RateLimit-Limit-Requests": "5", "X-RATELIMIT-REMAINING-REQUESTS": "3"}
    mapping = {
        "limit": "x-ratelimit-limit-requests",
        "remaining": "x-ratelimit-remaining-requests",
        "reset": None,
        "dimension": "requests",
    }
    parsed = parse_rate_limit_headers(headers, "p", "c", mapping)
    assert parsed is not None and parsed["value"].limit == 5.0
    assert parse_rate_limit_headers({"unrelated": "1"}, "p", "c", mapping) is None


def test_parse_quota_headers_requests_and_tokens_dimensions():
    headers = {
        "x-ratelimit-limit-requests": "1000",
        "x-ratelimit-remaining-requests": "900",
        "x-ratelimit-limit-tokens": "100000",
        "x-ratelimit-remaining-tokens": "80000",
    }
    values, snapshot = parse_quota_headers(headers, "openai", "conn1")
    dimensions = {v.dimension for v in values}
    assert dimensions == {"requests", "tokens"}
    by_dim = {v.dimension: v for v in values}
    assert by_dim["tokens"].remaining == 80_000.0
    assert snapshot is not None and snapshot["request_limit"] == 1000.0


def test_parse_quota_headers_anthropic_family():
    headers = {
        "anthropic-ratelimit-requests-limit": "50",
        "anthropic-ratelimit-requests-remaining": "7",
        "anthropic-ratelimit-input-tokens-limit": "30000",
        "anthropic-ratelimit-input-tokens-remaining": "12000",
    }
    values, _ = parse_quota_headers(headers, "anthropic", "conn-a")
    by_dim = {v.dimension: v for v in values}
    assert by_dim["requests"].limit == 50.0
    assert by_dim["tokens"].remaining == 12_000.0
    # Non-anthropic providers must NOT read the anthropic header family.
    values_none, snapshot_none = parse_quota_headers(headers, "openai", "conn-a")
    assert values_none == [] and snapshot_none is None


def test_parse_quota_headers_over_limit_coerces_remaining_zero():
    headers = {
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-remaining-requests": "5",
        "x-ratelimit-over-limit": "true",
    }
    values, _ = parse_quota_headers(headers, "openai")
    assert values[0].remaining == 0.0  # provider said over limit


def test_retry_after_ms_numeric_and_absent():
    assert retry_after_ms({"Retry-After": "30"}) == 30_000
    assert retry_after_ms({"other": "1"}) is None


# ============ models ============


def test_quota_value_exhaustion_semantics():
    assert QuotaValue(dimension="requests", remaining=0.0).is_exhausted()
    assert QuotaValue(dimension="requests", used=100.0, limit=100.0).is_exhausted()
    assert not QuotaValue(dimension="requests", remaining=5.0).is_exhausted()
    # Infinite remaining (unknown) is NOT exhausted.
    assert not QuotaValue(dimension="requests", remaining=float("inf")).is_exhausted()
    assert not QuotaValue(dimension="requests").is_exhausted()


def test_status_for_values_classification():
    exhausted = [QuotaValue(dimension="requests", remaining=0.0, limit=100.0)]
    approaching = [QuotaValue(dimension="requests", remaining=10.0, limit=100.0)]
    healthy = [QuotaValue(dimension="requests", remaining=90.0, limit=100.0)]
    assert status_for_values(exhausted) == "exhausted"
    assert status_for_values(approaching) == "approaching_limit"
    assert status_for_values(healthy) == "healthy"
    assert status_for_values([]) == "unknown"


def test_merge_quota_values_source_priority_per_dimension():
    configured = QuotaValue(dimension="requests", limit=10.0, source="configured")
    headers = QuotaValue(dimension="requests", limit=20.0, source="response_headers")
    api = QuotaValue(dimension="requests", limit=30.0, source="provider_api")
    credits = QuotaValue(dimension="credits", limit=5.0, source="configured")
    merged = merge_quota_values([configured, credits], [headers, api])
    by_dim = {v.dimension: v for v in merged}
    assert by_dim["requests"].limit == 30.0  # provider_api wins
    assert by_dim["credits"].limit == 5.0  # untouched dimension kept
    assert source_rank("provider_api") < source_rank("response_headers")
    assert source_rank("response_headers") < source_rank("configured")
    assert source_rank("configured") < source_rank("estimated")


# ============ collection ============


def test_collect_quota_state_merges_adapters_by_priority():
    header_adapter = ResponseHeaderAdapter(
        values_by_provider={"openai": [
            QuotaValue(dimension="requests", remaining=9.0, source="response_headers")
        ]}
    )
    configured_adapter = ConfiguredQuotaAdapter({
        "openai": {
            "requests": {"limit": 100.0, "used": 50.0},
            "credits": {"limit": 25.0, "used": 1.0},
        }
    })
    state = collect_quota_state(
        {"id": "conn1", "provider": "openai", "endpoint_id": "ep1"},
        [configured_adapter, header_adapter],
    )
    by_dim = {v.dimension: v for v in state.values}
    assert by_dim["requests"].source == "response_headers"  # higher rank wins
    assert by_dim["requests"].remaining == 9.0
    assert by_dim["credits"].source == "configured"
    assert state.supported is True
    assert state.status == "healthy"
    assert state.endpoint_id == "ep1"


def test_collect_quota_state_adapter_error_is_recorded_not_fatal():
    class BrokenAdapter:
        kind = "configured"

        def supports(self, provider_id):
            return True

        def read(self, connection):
            raise RuntimeError("provider api down")

    good = ConfiguredQuotaAdapter({"openai": {"requests": {"limit": 10.0, "used": 1.0}}})
    state = collect_quota_state(
        {"id": "c", "provider": "openai"}, [BrokenAdapter(), good]
    )
    assert state.error is not None and "provider api down" in state.error
    assert len(state.values) == 1  # the healthy adapter still merged


def test_configured_adapter_derives_remaining():
    adapter = ConfiguredQuotaAdapter({"p": {"requests": {"limit": 100.0, "used": 30.0}}})
    values = adapter.read({"provider": "p"})
    assert values[0].remaining == 70.0
    assert values[0].confidence == "medium"


def test_estimated_adapter_from_usage_counters():
    limits = {"p": {"requests": {"limit": 100.0}}}
    adapter = EstimatedQuotaAdapter({"p": {"requests": 40.0}}, limits)
    assert adapter.supports("p")
    values = adapter.read({"provider": "p"})
    assert values[0].used == 40.0 and values[0].remaining == 60.0
    assert values[0].source == "estimated" and values[0].confidence == "low"
    assert not adapter.supports("other")


# ============ buckets ============


def test_bucket_saturation_with_lazy_reset():
    store = BucketStore()
    future = iso(NOW_MS + 600_000)
    store.record_usage("conn", "5h", 100.0, future, now_ms=NOW_MS)
    assert store.is_saturated("conn", "5h", NOW_MS)
    assert not store.is_saturated("conn", "7d", NOW_MS)
    # After the reset instant the lazy read clears the bucket (fail-open).
    assert not store.is_saturated("conn", "5h", NOW_MS + 600_001)
    assert store.size() == 0


def test_bucket_below_threshold_and_nonfinite_not_saturated():
    store = BucketStore()
    store.record_usage("conn", "5h", 99.9, None, now_ms=NOW_MS)
    assert not store.is_saturated("conn", "5h", NOW_MS)
    store.record_usage("conn", "5h", float("inf"), None, now_ms=NOW_MS)
    assert not store.is_saturated("conn", "5h", NOW_MS)  # isFinite guard
    store.record_usage("conn", "5h", float("nan"), None, now_ms=NOW_MS)
    assert not store.is_saturated("conn", "5h", NOW_MS)
    assert store.is_saturated("", "5h", NOW_MS) is False  # empty id fail-open


def test_bucket_update_from_usage_quotas_mapping():
    store = BucketStore()
    quotas = {
        "session (5h)": {"used": 100, "resetAt": iso(NOW_MS + 3600_000)},
        "weekly (7d)": {"used": 40, "resetAt": iso(NOW_MS + 3600_000)},
        "weekly claude-opus (7d)": {"used": 100, "resetAt": iso(NOW_MS + 3600_000)},
    }
    store.update_from_usage_quotas("conn", quotas, now_ms=NOW_MS)
    assert store.is_saturated("conn", "5h", NOW_MS)
    assert not store.is_saturated("conn", "7d", NOW_MS)  # 40% only
    assert store.is_saturated("conn", "7d:claude-opus", NOW_MS)


# ============ inflight ============


def test_inflight_increment_decrement_floor():
    tracker = InflightTracker()
    assert tracker.increment("c", now_ms=NOW_MS) == 1
    assert tracker.increment("c", now_ms=NOW_MS) == 2
    tracker.decrement("c", now_ms=NOW_MS)
    assert tracker.get("c", NOW_MS) == 1
    tracker.decrement("c", now_ms=NOW_MS)
    tracker.decrement("c", now_ms=NOW_MS)  # floors at zero
    assert tracker.get("c", NOW_MS) == 0
    assert tracker.increment("", now_ms=NOW_MS) == 0  # empty id no-op


def test_inflight_lease_expiry():
    tracker = InflightTracker(default_lease_ms=1000)
    tracker.increment("c", now_ms=NOW_MS)
    assert tracker.get("c", NOW_MS + 500) == 1
    assert tracker.get("c", NOW_MS + 1001) == 0  # lease expired
    # Expired slots are pruned on the next increment.
    tracker.increment("c", now_ms=NOW_MS + 2000)
    assert tracker.size() == 1
    assert tracker.get("c", NOW_MS + 2000) == 1


# ============ quota-share selector ============


def _targets(*pairs):
    return [
        ShareTarget(execution_key=key, endpoint_id=key, connection_id=conn, weight=weight)
        for key, conn, weight in pairs
    ]


def test_quota_share_drr_converges_to_weight_ratio():
    targets = _targets(("A", "ca", 3.0), ("B", "cb", 1.0))
    drr = DrrState()
    buckets, inflight = BucketStore(), InflightTracker()
    wins = {"A": 0, "B": 0}
    for _ in range(4):
        result = select_quota_share_target(
            targets, "g", now_ms=NOW_MS, commit=True,
            buckets=buckets, inflight=inflight, drr_state=drr)
        wins[result.target.execution_key] += 1
        result.release()
    assert wins == {"A": 3, "B": 1}


def test_quota_share_p2c_prefers_less_loaded_runner_up():
    targets = _targets(("A", "ca", 1.0), ("B", "cb", 1.0))
    drr = DrrState()
    buckets, inflight = BucketStore(), InflightTracker()
    inflight.increment("ca", now_ms=NOW_MS)
    inflight.increment("ca", now_ms=NOW_MS)  # A carries 2 live requests
    result = select_quota_share_target(
        targets, "g", now_ms=NOW_MS, commit=True,
        buckets=buckets, inflight=inflight, drr_state=drr)
    assert result.target.execution_key == "B"  # P2C flip over the DRR winner
    result.release()


def test_quota_share_commit_false_touches_no_state():
    targets = _targets(("A", "ca", 1.0), ("B", "cb", 1.0))
    drr = DrrState()
    buckets, inflight = BucketStore(), InflightTracker()
    result = select_quota_share_target(
        targets, "g", now_ms=NOW_MS, commit=False,
        buckets=buckets, inflight=inflight, drr_state=drr)
    assert result.target is not None
    assert drr.snapshot("g") == {}  # scratch copy only
    assert inflight.size() == 0  # no reservation
    result.release()  # no-op by contract
    assert inflight.size() == 0


def test_quota_share_concurrency_cap_deprioritizes_not_blocks():
    targets = _targets(("A", "ca", 1.0), ("B", "cb", 1.0))
    drr = DrrState()
    buckets, inflight = BucketStore(), InflightTracker()
    inflight.increment("ca", now_ms=NOW_MS)  # A at cap=1
    result = select_quota_share_target(
        targets, "g", now_ms=NOW_MS, commit=True,
        max_concurrent_by_connection={"ca": 1},
        buckets=buckets, inflight=inflight, drr_state=drr)
    assert result.target.execution_key == "B"
    assert result.at_cap_keys == ["A"]
    assert result.ordered_keys[-1] == "A"  # still dispatchable as last resort
    result.release()
    # All-at-cap fails OPEN: everyone stays eligible.
    inflight.increment("cb", now_ms=NOW_MS)
    result_all = select_quota_share_target(
        targets, "g", now_ms=NOW_MS, commit=False,
        max_concurrent_by_connection={"ca": 1, "cb": 1},
        buckets=buckets, inflight=inflight, drr_state=drr)
    assert result_all.at_cap_keys == []
    assert set(result_all.ordered_keys) == {"A", "B"}


def test_quota_share_all_saturated_fails_open():
    targets = _targets(("A", "ca", 1.0), ("B", "cb", 1.0))
    buckets = BucketStore()
    buckets.record_usage("ca", "5h", 100.0, None, now_ms=NOW_MS)
    buckets.record_usage("cb", "5h", 100.0, None, now_ms=NOW_MS)
    result = select_quota_share_target(
        targets, "g", now_ms=NOW_MS, commit=False,
        buckets=buckets, inflight=InflightTracker(), drr_state=DrrState())
    assert set(result.ordered_keys) == {"A", "B"}  # nobody dropped
    assert result.deprioritized_keys == []  # fail-open keeps all eligible


def test_quota_share_saturated_target_deprioritized_to_tail():
    targets = _targets(("A", "ca", 100.0), ("B", "cb", 1.0))
    buckets = BucketStore()
    buckets.record_usage("ca", "5h", 100.0, None, now_ms=NOW_MS)
    result = select_quota_share_target(
        targets, "g", model_str="openai/gpt-4o", now_ms=NOW_MS, commit=False,
        buckets=buckets, inflight=InflightTracker(), drr_state=DrrState())
    assert result.target.execution_key == "B"
    assert result.deprioritized_keys == ["A"]
    assert result.ordered_keys == ["B", "A"]


def test_quota_share_per_model_bucket_gating():
    targets = _targets(("A", "ca", 1.0), ("B", "cb", 1.0))
    buckets = BucketStore()
    # Only the opus-specific window is saturated.
    buckets.record_usage("ca", "7d:claude-opus", 100.0, None, now_ms=NOW_MS)
    opus = select_quota_share_target(
        targets, "g", model_str="anthropic/claude-opus", now_ms=NOW_MS, commit=False,
        buckets=buckets, inflight=InflightTracker(), drr_state=DrrState())
    assert opus.deprioritized_keys == ["A"]
    other = select_quota_share_target(
        targets, "g", model_str="anthropic/claude-sonnet", now_ms=NOW_MS, commit=False,
        buckets=buckets, inflight=InflightTracker(), drr_state=DrrState())
    assert other.deprioritized_keys == []  # different model: A eligible again


def test_quota_share_weight_normalization_and_bare_model():
    assert normalize_weight(0) == 0.0
    assert normalize_weight(None) == 1.0
    assert normalize_weight(-5) == 1.0
    assert normalize_weight(2.5) == 2.5
    assert bare_model_name("openai/gpt-4o") == "gpt-4o"
    assert bare_model_name("plain") == "plain"


def test_quota_share_empty_targets():
    result = select_quota_share_target([], "g", now_ms=NOW_MS)
    assert result.target is None
    assert result.ordered_keys == []


# ============ snapshot persistence ============


def _state(status="healthy", remaining=90.0, fetched=NOW_MS, endpoint="ep1"):
    return QuotaState(
        provider_id="openai",
        connection_id="conn1",
        endpoint_id=endpoint,
        supported=True,
        fetched_at=iso(fetched),
        values=[QuotaValue(dimension="requests", limit=100.0, used=100.0 - remaining,
                           remaining=remaining, source="response_headers")],
        status=status,
    )


def test_snapshot_change_detection_skips_identical_states(quota_storage, snapshot_store):
    assert snapshot_store.record(_state(fetched=NOW_MS)) is True
    # Identical numbers, later timestamp: NOT a change (timestamps excluded).
    assert snapshot_store.record(_state(fetched=NOW_MS + 60_000)) is False
    # A real change persists.
    assert snapshot_store.record(_state(remaining=80.0, fetched=NOW_MS + 120_000)) is True
    assert snapshot_store.count() == 2


def test_snapshot_change_key_excludes_timestamps():
    first = compute_change_key(_state(fetched=NOW_MS))
    second = compute_change_key(_state(fetched=NOW_MS + 999_999))
    assert first == second
    assert first != compute_change_key(_state(remaining=10.0))


def test_snapshot_row_cap_prunes_oldest(quota_storage):
    store = SnapshotStore(max_snapshots=3)
    for i in range(6):
        store.record(_state(remaining=float(i), endpoint=f"ep{i}"))
    rows = store.list(limit=100)
    assert len(rows) == 3
    assert [r["endpoint_id"] for r in rows] == ["ep5", "ep4", "ep3"]  # newest-first


def test_snapshot_list_endpoint_filter(quota_storage, snapshot_store):
    snapshot_store.record(_state(endpoint="ep1", remaining=90.0))
    snapshot_store.record(_state(endpoint="ep2", remaining=50.0))
    snapshot_store.record(_state(endpoint="ep1", remaining=70.0))
    ep1 = snapshot_store.list(endpoint_id="ep1")
    assert len(ep1) == 2
    assert all(row["endpoint_id"] == "ep1" for row in ep1)
    assert ep1[0]["id"] > ep1[1]["id"]  # newest first
    assert ep1[0]["values"][0]["remaining"] == 70.0


def test_snapshot_store_restart_recovery(quota_storage):
    store_a = SnapshotStore(max_snapshots=100)
    assert store_a.record(_state()) is True
    # A fresh store (process restart) recovers the last change key from DB.
    store_b = SnapshotStore(max_snapshots=100)
    assert store_b.record(_state(fetched=NOW_MS + 5000)) is False  # unchanged
    assert store_b.record(_state(remaining=55.0)) is True
    assert store_b.count() == 2


# ============ monitor ============


def test_monitor_observe_headers_builds_state_and_persists(quota_storage, monitor, snapshot_store, gateway_settings):
    headers = {
        "x-ratelimit-limit-requests": "100",
        "x-ratelimit-remaining-requests": "90",
        "x-ratelimit-limit-tokens": "10000",
        "x-ratelimit-remaining-tokens": "9000",
    }
    state = monitor.observe_headers(headers, "openai", connection_id="conn1",
                                    endpoint_id="ep1", now_ms=NOW_MS)
    assert state.status == "healthy"
    assert {v.dimension for v in state.values} == {"requests", "tokens"}
    assert monitor.get_state(endpoint_id="ep1") is not None
    assert snapshot_store.count() == 1
    # Identical re-observation: state merged but no duplicate snapshot row.
    monitor.observe_headers(headers, "openai", connection_id="conn1",
                            endpoint_id="ep1", now_ms=NOW_MS + 1000)
    assert snapshot_store.count() == 1


def test_monitor_provider_api_source_overrides_headers(quota_storage, monitor, gateway_settings):
    monitor.observe_headers(
        {"x-ratelimit-limit-requests": "100", "x-ratelimit-remaining-requests": "90"},
        "openai", endpoint_id="ep1", now_ms=NOW_MS)
    state = monitor.set_usage(
        "openai", endpoint_id="ep1",
        values=[QuotaValue(dimension="requests", limit=200.0, remaining=10.0,
                           source="unknown")],
        now_ms=NOW_MS)
    by_dim = {v.dimension: v for v in state.values}
    assert by_dim["requests"].source == "provider_api"
    assert by_dim["requests"].limit == 200.0
    assert state.status == "approaching_limit"  # 10/200 = 5% remaining


def test_monitor_can_afford_no_data_fail_open(gateway_settings):
    decision = QuotaMonitor().can_afford_request(provider_id="ghost", now_ms=NOW_MS)
    assert decision["allowed"] is True
    assert decision["reason"] == "no_quota_data"


def test_monitor_can_afford_exhausted_fail_open_true(monkeypatch):
    quota_settings(monkeypatch, fail_open=True)
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    monitor.observe_state(_state(status="exhausted", remaining=0.0), persist=False)
    decision = monitor.can_afford_request(endpoint_id="ep1", now_ms=NOW_MS)
    assert decision["allowed"] is True  # uncertainty/short block never hard-fails
    assert decision["reason"] == "exhausted_fail_open"
    assert decision["status"] == "exhausted"


def test_monitor_can_afford_exhausted_fail_open_false_blocks(monkeypatch):
    quota_settings(monkeypatch, fail_open=False)
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    reset_at = iso(NOW_MS + 60_000)
    state = QuotaState(
        provider_id="openai", endpoint_id="ep1", supported=True, fetched_at=iso(NOW_MS),
        values=[QuotaValue(dimension="requests", limit=100.0, used=100.0,
                           remaining=0.0, reset_at=reset_at, source="response_headers")],
        status="exhausted")
    monitor.observe_state(state, persist=False)
    decision = monitor.can_afford_request(endpoint_id="ep1", now_ms=NOW_MS)
    assert decision["allowed"] is False
    assert decision["reason"] == "quota_exhausted"
    assert decision["retry_after_ms"] == pytest.approx(60_000, abs=1500)


def test_monitor_can_afford_disabled_scheduler_allows(monkeypatch):
    quota_settings(monkeypatch, enabled=False)
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    monitor.observe_state(_state(status="exhausted", remaining=0.0), persist=False)
    decision = monitor.can_afford_request(endpoint_id="ep1", now_ms=NOW_MS)
    assert decision["allowed"] is True
    assert decision["reason"] == "scheduler_disabled"


def test_monitor_can_afford_saturated_bucket_blocks_when_strict(monkeypatch):
    quota_settings(monkeypatch, fail_open=False)
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    monitor.observe_headers(
        {"x-ratelimit-limit-requests": "100", "x-ratelimit-remaining-requests": "50"},
        "openai", connection_id="conn1", endpoint_id="ep1", now_ms=NOW_MS)
    get_buckets().record_usage("conn1", "5h", 100.0, None, now_ms=NOW_MS)
    decision = monitor.can_afford_request(
        provider_id="openai", connection_id="conn1", endpoint_id="ep1", now_ms=NOW_MS)
    assert decision["allowed"] is False
    assert decision["status"] == "exhausted"


def test_monitor_next_poll_delay_adaptive_cadence(monkeypatch):
    quota_settings(monkeypatch)  # defaults: 60s normal, 15s fast, warn 0.80
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    assert monitor.next_poll_delay(endpoint_id="missing") == 60.0  # no data: nominal
    monitor.observe_state(QuotaState(
        provider_id="p", endpoint_id="ok", supported=True, fetched_at=iso(NOW_MS),
        values=[QuotaValue(dimension="requests", limit=100.0, used=10.0,
                           remaining=90.0, source="provider_api")],
        status="healthy"), persist=False)
    assert monitor.next_poll_delay(endpoint_id="ok") == 60.0
    monitor.observe_state(QuotaState(
        provider_id="p", endpoint_id="warn", supported=True, fetched_at=iso(NOW_MS),
        values=[QuotaValue(dimension="requests", limit=100.0, used=85.0,
                           remaining=15.0, source="provider_api")],
        status="healthy"), persist=False)
    assert monitor.next_poll_delay(endpoint_id="warn") == 15.0  # crosses warn 0.80
    monitor.observe_state(_state(status="exhausted", remaining=0.0, endpoint="dead"),
                          persist=False)
    assert monitor.next_poll_delay(endpoint_id="dead") == 15.0
    assert monitor.next_poll_delay_global() == 15.0  # fastest across endpoints


def test_monitor_refresh_drops_rolled_over_windows(quota_storage, monitor, snapshot_store, gateway_settings):
    past = iso(NOW_MS - 1000)
    future = iso(NOW_MS + 3_600_000)
    state = QuotaState(
        provider_id="openai", endpoint_id="ep1", supported=True, fetched_at=iso(NOW_MS),
        values=[
            QuotaValue(dimension="requests", limit=100.0, used=100.0, remaining=0.0,
                       reset_at=past, source="response_headers"),
            QuotaValue(dimension="tokens", limit=1000.0, used=100.0, remaining=900.0,
                       reset_at=future, source="response_headers"),
        ],
        status="exhausted")
    monitor.observe_state(state, persist=True)
    assert monitor.get_state(endpoint_id="ep1").status == "exhausted"
    result = monitor.refresh(now_ms=NOW_MS)
    assert result["refreshed"] == 1
    refreshed = monitor.get_state(endpoint_id="ep1")
    assert [v.dimension for v in refreshed.values] == ["tokens"]
    assert refreshed.status == "healthy"  # re-classified after the drop
    assert snapshot_store.count() == 2  # original + genuine change persisted
    # Second refresh: nothing left to roll over.
    assert monitor.refresh(now_ms=NOW_MS)["refreshed"] == 0


def test_monitor_status_summary_shape(gateway_settings):
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    monitor.observe_state(_state(), persist=False)
    summary = monitor.status_summary()
    assert summary["enabled"] is True
    assert summary["endpoint_count"] == 1
    assert summary["endpoints"][0]["endpoint_id"] == "ep1"
    assert summary["endpoints"][0]["values"][0]["dimension"] == "requests"


def test_monitor_observe_usage_quotas_feeds_buckets(gateway_settings):
    monitor = QuotaMonitor(snapshots=SnapshotStore(max_snapshots=100))
    monitor.observe_usage_quotas("conn9", {
        "session (5h)": {"used": 100, "resetAt": iso(NOW_MS + 60_000)},
    }, now_ms=NOW_MS)
    assert get_buckets().is_saturated("conn9", "5h", NOW_MS)


# ============ HTTP surface ============


@pytest.fixture
def quota_app(gateway_settings):
    from moa_gateway.routes.quota import router

    store = SnapshotStore(max_snapshots=100)
    application = FastAPI()
    application.include_router(router)
    application.state.quota_monitor = QuotaMonitor(snapshots=store)
    application.state.quota_snapshot_store = store
    return application


@pytest.fixture
async def client(quota_app):
    transport = ASGITransport(app=quota_app)
    async with AsyncClient(transport=transport, base_url="http://quota.test") as ac:
        yield ac


async def test_http_requires_api_key(client):
    assert (await client.get("/v1/quota/status")).status_code == 401
    assert (await client.get("/v1/quota/snapshots")).status_code == 401
    assert (await client.post("/v1/quota/check", json={})).status_code == 401
    assert (await client.post("/v1/quota/refresh", json={})).status_code == 401


async def test_http_status_summary(client, quota_app):
    quota_app.state.quota_monitor.observe_state(_state(), persist=False)
    response = await client.get("/v1/quota/status", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["endpoint_count"] == 1
    assert payload["endpoints"][0]["status"] == "healthy"


async def test_http_check_no_data_and_exhausted(client, quota_app):
    response = await client.post("/v1/quota/check",
                                 json={"endpoint_id": "ghost"}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["allowed"] is True
    assert response.json()["reason"] == "no_quota_data"

    quota_app.state.quota_monitor.observe_state(
        _state(status="exhausted", remaining=0.0), persist=False)
    response = await client.post("/v1/quota/check",
                                 json={"endpoint_id": "ep1", "now_ms": NOW_MS},
                                 headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["allowed"] is True  # default fail_open=True
    assert body["reason"] == "exhausted_fail_open"


async def test_http_check_rejects_unknown_fields(client):
    response = await client.post("/v1/quota/check", json={"bogus": 1}, headers=AUTH)
    assert response.status_code == 422


async def test_http_snapshots_listing(client, quota_app):
    monitor = quota_app.state.quota_monitor
    monitor.observe_state(_state(remaining=90.0), persist=True)
    monitor.observe_state(_state(remaining=40.0), persist=True)
    response = await client.get("/v1/quota/snapshots",
                                params={"endpoint_id": "ep1", "limit": 10}, headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 2
    assert payload["snapshots"][0]["values"][0]["remaining"] == 40.0  # newest first


async def test_http_refresh_endpoint(client, quota_app):
    past = iso(NOW_MS - 1000)
    quota_app.state.quota_monitor.observe_state(QuotaState(
        provider_id="openai", endpoint_id="ep1", supported=True, fetched_at=iso(NOW_MS),
        values=[QuotaValue(dimension="requests", limit=100.0, used=100.0, remaining=0.0,
                           reset_at=past, source="response_headers")],
        status="exhausted"), persist=False)
    response = await client.post("/v1/quota/refresh", json={"now_ms": NOW_MS}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["refreshed"] == 1
    status = (await client.get("/v1/quota/status", headers=AUTH)).json()
    assert status["endpoints"][0]["status"] == "unknown"  # value dropped


async def test_http_disabled_503(quota_app, monkeypatch):
    quota_settings(monkeypatch, enabled=False)
    transport = ASGITransport(app=quota_app)
    async with AsyncClient(transport=transport, base_url="http://quota.test") as ac:
        assert (await ac.post("/v1/quota/check", json={}, headers=AUTH)).status_code == 503
        assert (await ac.get("/v1/quota/snapshots", headers=AUTH)).status_code == 503
        assert (await ac.post("/v1/quota/refresh", json={}, headers=AUTH)).status_code == 503
        # /status stays reachable and reports the disabled flag honestly.
        status = await ac.get("/v1/quota/status", headers=AUTH)
        assert status.status_code == 200
        assert status.json()["enabled"] is False


async def test_http_capability_toggle_503(quota_app, gateway_settings, quota_storage):
    from moa_gateway import capability_toggles

    capability_toggles.set_enabled("quota_scheduler", False)
    transport = ASGITransport(app=quota_app)
    async with AsyncClient(transport=transport, base_url="http://quota.test") as ac:
        response = await ac.get("/v1/quota/status", headers=AUTH)
    assert response.status_code == 503
    assert "quota_scheduler" in response.json()["detail"]
    capability_toggles.set_enabled("quota_scheduler", True)
