"""tests/test_routing_strategies.py — M1 routing-strategy engine (Agent A).

Covers moa_gateway/routing_strategies/* and moa_gateway/routes/routing_strategies.py.
All strategy algorithms are faithful ports of OmniRoute
(https://github.com/diegosouzapw/OmniRoute, MIT license); each test asserts the
ported semantics with injected RNG/clocks (deterministic, no network).

The HTTP app is self-built per the frozen architecture contract:
    app = FastAPI(); app.include_router(routes.routing_strategies.router)
so these tests never depend on moa_gateway/server.py or routes/__init__.py.

Controlled test doubles: NONE on the real path. The only boundary fixtures are
the isolated Settings/Storage fixtures required by the shared conftest policy.
"""
from __future__ import annotations

import math
import random
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.moa_strategies.base import ModelCandidate, get_strategy, list_strategies
from moa_gateway.routing_strategies.auto_scoring import (
    DEFAULT_WEIGHTS,
    calculate_tier_score,
    get_task_fitness,
    normalize_scoring_weights,
)
from moa_gateway.routing_strategies.engine import (
    RoutingDisabledError,
    RoutingStrategyEngine,
    UnknownStrategyError,
    reset_engine_for_tests,
)
from moa_gateway.routing_strategies.models import (
    EndpointCandidate,
    QuotaSnapshot,
    QuotaWindow,
    RoutingContext,
)
from moa_gateway.routing_strategies.moa_bridge import (
    STRATEGY_NAME as FUSION_STRATEGY_NAME,
    RoutingFusionStrategy,
    endpoint_from_model_candidate,
    register_routing_fusion,
)
from moa_gateway.routing_strategies.quota_scoring import (
    compute_headroom,
    resolve_reset_aware_config,
    score_reset_aware_quota,
)
from moa_gateway.routing_strategies.strategies import (
    STRATEGIES,
    LkgpRecord,
    StrategyState,
    normalize_strategy_name,
    order_cache_optimized,
    order_context_optimized,
    order_context_relay,
    order_cost_optimized,
    order_fill_first,
    order_fusion,
    order_headroom,
    order_least_used,
    order_lkgp,
    order_p2c,
    order_pipeline,
    order_priority,
    order_random,
    order_reset_aware,
    order_reset_window,
    order_round_robin,
    order_strict_random,
    order_weighted,
)
from moa_gateway.routing_strategies.telemetry import TelemetryStore

API_KEY = "routing-test-key-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
NOW_MS = 1_750_000_000_000.0  # fixed clock for deterministic quota math

EXPECTED_STRATEGIES = {
    "priority", "weighted", "round-robin", "context-relay", "fill-first",
    "p2c", "random", "least-used", "cost-optimized", "reset-aware",
    "reset-window", "headroom", "strict-random", "auto", "lkgp",
    "context-optimized", "cache-optimized", "fusion", "pipeline", "quota-share",
}


# ============ fixtures & helpers ============


@pytest.fixture(autouse=True)
def _reset_routing_singletons():
    """Reset M1 + the M2 singletons the quota-share strategy delegates to."""
    from moa_gateway.quota_scheduler.buckets import reset_buckets_for_tests
    from moa_gateway.quota_scheduler.inflight import reset_inflight_for_tests
    from moa_gateway.quota_scheduler.quota_share import reset_quota_share_for_tests

    reset_engine_for_tests()
    reset_quota_share_for_tests()
    reset_buckets_for_tests()
    reset_inflight_for_tests()
    yield
    reset_engine_for_tests()
    reset_quota_share_for_tests()
    reset_buckets_for_tests()
    reset_inflight_for_tests()


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
def engine(gateway_settings):
    return RoutingStrategyEngine(seed=42)


@pytest.fixture
def routing_storage(tmp_path, make_settings):
    """Isolated real Storage registered as the process singleton so the
    engine/telemetry lazy ``get_storage()`` calls hit this exact DB."""
    from moa_gateway.storage import Storage

    settings = make_settings()
    with patch("moa_gateway.storage.get_settings", return_value=settings):
        with patch("moa_gateway.storage.DATA_DIR", tmp_path):
            Storage._instance = None
            s = Storage(db_path=tmp_path / "routing.db")
            Storage._instance = s
            yield s
            Storage._instance = None


def cand(endpoint_id: str, **overrides) -> EndpointCandidate:
    return EndpointCandidate(endpoint_id=endpoint_id, **overrides)


def mk_state(seed: int = 42, now_ms: float = NOW_MS, dry_run: bool = False, **kw) -> StrategyState:
    return StrategyState(rng=random.Random(seed), now_ms=now_ms, dry_run=dry_run, **kw)


def ctx(**kw) -> RoutingContext:
    return RoutingContext(**kw)


# ============ registry & normalization ============


def test_registry_contains_all_20_strategies():
    assert set(STRATEGIES.keys()) == EXPECTED_STRATEGIES
    assert STRATEGIES["quota-share"].internal is True
    assert STRATEGIES["fusion"].mode == "fanout"
    assert STRATEGIES["pipeline"].mode == "sequential"
    public = [name for name, spec in STRATEGIES.items() if not spec.internal]
    assert len(public) == 19


def test_normalize_aliases():
    assert normalize_strategy_name("usage") == "least-used"
    assert normalize_strategy_name("context") == "context-optimized"
    assert normalize_strategy_name("weekly-reset") == "reset-window"
    assert normalize_strategy_name("reset-window-order") == "reset-window"


def test_normalize_underscore_and_case_spellings():
    assert normalize_strategy_name("round_robin") == "round-robin"
    assert normalize_strategy_name("COST_OPTIMIZED") == "cost-optimized"
    assert normalize_strategy_name("  P2C ") == "p2c"


def test_normalize_unknown_falls_back_to_priority():
    assert normalize_strategy_name("no-such-strategy") == "priority"
    assert normalize_strategy_name(None) == "priority"
    assert normalize_strategy_name(123) == "priority"


def test_register_routing_fusion_idempotent():
    first = register_routing_fusion()
    second = register_routing_fusion()
    assert FUSION_STRATEGY_NAME in list_strategies()
    assert isinstance(get_strategy(FUSION_STRATEGY_NAME), RoutingFusionStrategy)
    # Second call never re-registers (idempotent contract).
    assert second is False
    assert isinstance(get_strategy(FUSION_STRATEGY_NAME), RoutingFusionStrategy)
    del first  # first may be True or False depending on import order


# ============ pure strategy ordering ============


def test_priority_stable_order():
    cands = [cand("c", priority=2), cand("a", priority=0), cand("b", priority=1), cand("d", priority=1)]
    result = order_priority(cands, ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["a", "b", "d", "c"]  # stable tie


def test_weighted_zero_total_weight_picks_member():
    cands = [cand("a", weight=0), cand("b", weight=0), cand("c", weight=0)]
    result = order_weighted(cands, ctx(), mk_state(seed=7))
    assert result.ordered[0].endpoint_id in {"a", "b", "c"}
    assert {c.endpoint_id for c in result.ordered} == {"a", "b", "c"}


def test_weighted_fallback_chain_weight_descending():
    cands = [cand("low", weight=1), cand("high", weight=100), cand("mid", weight=20)]
    for seed in range(5):
        result = order_weighted(cands, ctx(), mk_state(seed=seed))
        winner = result.ordered[0]
        rest_weights = [c.weight for c in result.ordered[1:]]
        assert rest_weights == sorted(rest_weights, reverse=True)
        assert winner in cands


def test_weighted_single_dominant_candidate_wins():
    # weight 1 vs 0 vs 0: the roulette can only land on the positive weight.
    cands = [cand("only", weight=1), cand("zero-a", weight=0), cand("zero-b", weight=0)]
    for seed in range(5):
        result = order_weighted(cands, ctx(), mk_state(seed=seed))
        assert result.ordered[0].endpoint_id == "only"


def test_round_robin_rotates_on_commit_and_freezes_on_dry_run():
    cands = [cand("a"), cand("b"), cand("c")]
    state = mk_state()
    r1 = order_round_robin(cands, ctx(group_key="g1"), state)
    r2 = order_round_robin(cands, ctx(group_key="g1"), state)
    r3 = order_round_robin(cands, ctx(group_key="g1"), state)
    assert [r.ordered[0].endpoint_id for r in (r1, r2, r3)] == ["a", "b", "c"]
    # Dry run must not advance the counter.
    dry1 = order_round_robin(cands, ctx(group_key="g1"), mk_state(dry_run=True, rr_counters=state.rr_counters))
    dry2 = order_round_robin(cands, ctx(group_key="g1"), mk_state(dry_run=True, rr_counters=state.rr_counters))
    assert dry1.ordered[0].endpoint_id == dry2.ordered[0].endpoint_id == "a"
    # Commit after the dry runs continues where it left off.
    r4 = order_round_robin(cands, ctx(group_key="g1"), state)
    assert r4.ordered[0].endpoint_id == "a"


def test_round_robin_groups_are_independent():
    cands = [cand("a"), cand("b")]
    state = mk_state()
    order_round_robin(cands, ctx(group_key="g1"), state)
    other = order_round_robin(cands, ctx(group_key="g2"), state)
    assert other.ordered[0].endpoint_id == "a"  # g2 counter untouched by g1


def test_context_relay_pins_bound_endpoint():
    cands = [cand("a", priority=0, provider="p1"), cand("b", priority=1, provider="p2")]
    bindings = {"s1": LkgpRecord(provider="p2", endpoint_id="b")}
    result = order_context_relay(cands, ctx(session_key="s1"), mk_state(session_bindings=bindings))
    assert result.ordered[0].endpoint_id == "b"
    assert result.applied is True


def test_context_relay_no_binding_keeps_priority():
    cands = [cand("a", priority=0), cand("b", priority=1)]
    result = order_context_relay(cands, ctx(session_key="s-none"), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["a", "b"]
    assert result.applied is False


def test_context_relay_provider_match_when_endpoint_gone():
    cands = [cand("x", priority=0, provider="p1"), cand("y", priority=1, provider="p2")]
    bindings = {"s1": LkgpRecord(provider="p2", endpoint_id="retired")}
    result = order_context_relay(cands, ctx(session_key="s1"), mk_state(session_bindings=bindings))
    assert result.ordered[0].endpoint_id == "y"


def test_fill_first_preserves_priority_order():
    cands = [cand("b", priority=1), cand("a", priority=0)]
    result = order_fill_first(cands, ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["a", "b"]


def test_p2c_picks_the_better_of_the_two_sampled():
    good = cand("good", request_count=50, success_rate=0.99, avg_latency_ms=60)
    bad = cand("bad", request_count=50, success_rate=0.10, avg_latency_ms=3000)
    for seed in range(6):  # with 2 candidates every seed samples exactly this pair
        result = order_p2c([good, bad], ctx(), mk_state(seed=seed))
        assert result.ordered[0].endpoint_id == "good"
        result_rev = order_p2c([bad, good], ctx(), mk_state(seed=seed))
        assert result_rev.ordered[0].endpoint_id == "good"


def test_p2c_open_breaker_scores_negative_infinity():
    open_ep = cand("open", breaker_state="OPEN", request_count=9, success_rate=1.0, avg_latency_ms=1)
    closed_ep = cand("closed", breaker_state="CLOSED", request_count=9, success_rate=0.5, avg_latency_ms=900)
    for seed in range(6):
        result = order_p2c([open_ep, closed_ep], ctx(), mk_state(seed=seed))
        assert result.ordered[0].endpoint_id == "closed"


def test_p2c_half_open_penalty_loses_to_clean_peer():
    half = cand("half", breaker_state="HALF_OPEN", request_count=10, success_rate=0.8, avg_latency_ms=100)
    clean = cand("clean", breaker_state="CLOSED", request_count=10, success_rate=0.8, avg_latency_ms=100)
    for seed in range(6):
        result = order_p2c([half, clean], ctx(), mk_state(seed=seed))
        assert result.ordered[0].endpoint_id == "clean"


def test_random_is_a_permutation():
    cands = [cand(f"e{i}") for i in range(8)]
    result = order_random(cands, ctx(), mk_state(seed=99))
    assert sorted(c.endpoint_id for c in result.ordered) == sorted(c.endpoint_id for c in cands)


def test_least_used_ascending_with_stable_ties():
    cands = [cand("a", request_count=5), cand("b", request_count=1),
             cand("c", request_count=3), cand("d", request_count=1)]
    result = order_least_used(cands, ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["b", "d", "c", "a"]


def test_cost_optimized_input_price_ascending():
    cands = [cand("expensive", cost_per_1k_input=3.0), cand("cheap", cost_per_1k_input=0.05),
             cand("mid", cost_per_1k_input=0.6)]
    result = order_cost_optimized(cands, ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["cheap", "mid", "expensive"]


def _quota(percent_5h=None, reset_5h=None, percent_7d=None, reset_7d=None,
           percent_used=None, limit_reached=False, top_reset=None) -> QuotaSnapshot:
    return QuotaSnapshot(
        percentUsed=percent_used,
        limitReached=limit_reached,
        resetAt=top_reset,
        window5h=QuotaWindow(percentUsed=percent_5h, resetAt=reset_5h) if percent_5h is not None or reset_5h else None,
        window7d=QuotaWindow(percentUsed=percent_7d, resetAt=reset_7d) if percent_7d is not None or reset_7d else None,
    )


def test_reset_aware_reset_pressure_orders_burn_candidate_first():
    # Same reset instants for both: the reset-pressure term rewards the
    # heavily-used quota (OmniRoute "use it before it resets" semantics).
    healthy = cand("plenty", quota=_quota(percent_used=0.1, percent_5h=0.1,
                                          reset_5h=str(NOW_MS + 2 * 3600_000),
                                          percent_7d=0.2, reset_7d=str(NOW_MS + 3 * 86400_000)))
    burn = cand("burn-first", quota=_quota(percent_used=0.9, percent_5h=0.9,
                                           reset_5h=str(NOW_MS + 2 * 3600_000),
                                           percent_7d=0.9, reset_7d=str(NOW_MS + 3 * 86400_000)))
    limited = cand("limited", quota=_quota(percent_used=0.2, limit_reached=True))
    result = order_reset_aware([burn, limited, healthy], ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["burn-first", "plenty", "limited"]
    assert result.scores["burn-first"] > result.scores["plenty"]
    assert "limited" not in result.scores  # -inf scores are not reported


def test_reset_aware_score_semantics_directly():
    config = resolve_reset_aware_config()
    assert score_reset_aware_quota(None, config, NOW_MS) == 0.5  # neutral
    assert score_reset_aware_quota(_quota(limit_reached=True), config, NOW_MS) == -math.inf
    # Reset far beyond both window horizons -> urgency clamps to 0, the reset
    # pressure term vanishes, and remaining budget purely dominates. (With NO
    # reset_at at all urgency is the neutral 0.5 and the weekly window's large
    # pressure weight slightly favours the heavily-used quota — faithful
    # OmniRoute behaviour, not asserted here.)
    far = str(NOW_MS + 10 * 86400_000)
    high = score_reset_aware_quota(_quota(percent_used=0.1, percent_5h=0.1, reset_5h=far,
                                          percent_7d=0.1, reset_7d=far), config, NOW_MS)
    low = score_reset_aware_quota(_quota(percent_used=0.9, percent_5h=0.9, reset_5h=far,
                                         percent_7d=0.9, reset_7d=far), config, NOW_MS)
    assert 0.0 <= low < high <= 1.0


def test_reset_aware_exhaustion_guard_collapses_near_empty_session():
    config = resolve_reset_aware_config()
    near_empty = _quota(percent_used=0.5, percent_5h=0.99, percent_7d=0.2)
    score = score_reset_aware_quota(near_empty, config, NOW_MS)
    baseline = score_reset_aware_quota(_quota(percent_used=0.5, percent_5h=0.5, percent_7d=0.2), config, NOW_MS)
    assert score < baseline  # session remaining 1% < guard 10% collapses the score
    assert score >= 0.0


def test_reset_window_soonest_reset_first_unknown_last():
    soon = cand("soon", quota=_quota(reset_7d=str(NOW_MS + 600_000)))
    later = cand("later", quota=_quota(reset_7d=str(NOW_MS + 3 * 3600_000)))
    unknown = cand("unknown", quota=None)
    result = order_reset_window([later, unknown, soon], ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["soon", "later", "unknown"]


def test_reset_window_tie_band_rotates_on_commit_only():
    # Resets 10s apart -> inside the 60s tie band.
    a = cand("a", quota=_quota(reset_7d=str(NOW_MS + 100_000)))
    b = cand("b", quota=_quota(reset_7d=str(NOW_MS + 110_000)))
    state = mk_state()
    first = order_reset_window([a, b], ctx(group_key="rw"), state)
    second = order_reset_window([a, b], ctx(group_key="rw"), state)
    assert first.ordered[0].endpoint_id != second.ordered[0].endpoint_id
    # Dry run freezes the rotation.
    dry1 = order_reset_window([a, b], ctx(group_key="rw"), mk_state(dry_run=True, rr_counters=state.rr_counters))
    dry2 = order_reset_window([a, b], ctx(group_key="rw"), mk_state(dry_run=True, rr_counters=state.rr_counters))
    assert dry1.ordered[0].endpoint_id == dry2.ordered[0].endpoint_id


def test_headroom_most_free_first_missing_quota_full_headroom():
    busy = cand("busy", quota=_quota(percent_5h=0.8, percent_7d=0.3))
    fresh = cand("fresh", quota=None)
    idle = cand("idle", quota=_quota(percent_5h=0.1, percent_7d=0.1))
    result = order_headroom([busy, fresh, idle], ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["fresh", "idle", "busy"]
    assert result.scores["fresh"] == pytest.approx(1.0)
    assert result.scores["busy"] == pytest.approx(0.2)
    assert compute_headroom(None, None) == 1.0
    assert compute_headroom(1.5, -0.3) == 0.0  # clamped


def test_strict_random_deals_full_cycle_without_repeats_and_no_immediate_repeat(gateway_settings):
    cands = [cand("a"), cand("b"), cand("c")]
    engine = RoutingStrategyEngine(seed=11)
    winners = []
    for _ in range(9):  # three full deck cycles
        decision, _ = engine.resolve(cands, strategy="strict-random",
                                     context=ctx(group_key="deck"))
        winners.append(decision.selected)
    assert len(winners) == 9
    for start in (0, 3, 6):
        cycle = winners[start:start + 3]
        assert len(set(cycle)) == 3  # full cycle without replacement
    for i in range(len(winners) - 1):
        assert winners[i] != winners[i + 1]  # anti-repeat across cycle boundary


def test_strict_random_dry_run_does_not_touch_deck_state(gateway_settings):
    cands = [cand("a"), cand("b"), cand("c")]
    engine = RoutingStrategyEngine(seed=5)
    decision, release = engine.resolve(cands, strategy="strict-random",
                                       context=ctx(group_key="d"), dry_run=True)
    assert decision.selected in {"a", "b", "c"}
    assert engine._decks == {}  # no deck committed in dry run
    assert release is None


def test_lkgp_moves_pinned_endpoint_first():
    cands = [cand("a", priority=0, provider="p1"), cand("b", priority=1, provider="p2"),
             cand("c", priority=2, provider="p3")]
    lkgp = {"grp": LkgpRecord(provider="p3", endpoint_id="c")}
    result = order_lkgp(cands, ctx(group_key="grp"), mk_state(lkgp=lkgp))
    assert result.ordered[0].endpoint_id == "c"
    assert result.applied is True


def test_lkgp_no_pin_keeps_priority_order():
    cands = [cand("a", priority=0), cand("b", priority=1)]
    result = order_lkgp(cands, ctx(group_key="empty"), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["a", "b"]
    assert result.applied is False


def test_context_optimized_largest_context_first():
    cands = [cand("small", context_limit=8_000), cand("huge", context_limit=200_000),
             cand("none", context_limit=0), cand("mid", context_limit=32_000)]
    result = order_context_optimized(cands, ctx(), mk_state())
    assert [c.endpoint_id for c in result.ordered] == ["huge", "mid", "small", "none"]


def test_cache_optimized_explicit_key_deterministic_winner():
    cands = [cand(f"ep{i}", connection_id=f"conn{i}") for i in range(4)]
    body = {"prompt_cache_key": "session-fixed-key"}
    first = order_cache_optimized(cands, ctx(body=body), mk_state())
    second = order_cache_optimized(cands, ctx(body=body), mk_state(seed=999))
    assert first.applied is True
    assert [c.endpoint_id for c in first.ordered] == [c.endpoint_id for c in second.ordered]
    assert set(c.endpoint_id for c in first.ordered) == {f"ep{i}" for i in range(4)}


def test_cache_optimized_without_key_keeps_order():
    cands = [cand("a"), cand("b")]
    result = order_cache_optimized(cands, ctx(body={}), mk_state())
    assert result.applied is False
    assert [c.endpoint_id for c in result.ordered] == ["a", "b"]


def test_cache_optimized_prefix_key_from_messages_stable():
    cands = [cand(f"ep{i}") for i in range(3)]
    body = {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "hi"}]}
    r1 = order_cache_optimized(cands, ctx(body=body), mk_state())
    r2 = order_cache_optimized(cands, ctx(body=body), mk_state())
    assert r1.applied is True
    assert [c.endpoint_id for c in r1.ordered] == [c.endpoint_id for c in r2.ordered]


def test_fusion_panel_in_priority_order_fanout_mode():
    cands = [cand("b", priority=1), cand("a", priority=0), cand("c", priority=2)]
    result = order_fusion(cands, ctx(), mk_state())
    assert result.mode == "fanout"
    assert [c.endpoint_id for c in result.ordered] == ["a", "b", "c"]


def test_pipeline_keeps_step_order_sequential_mode():
    cands = [cand("step2"), cand("step1")]
    result = order_pipeline(cands, ctx(), mk_state())
    assert result.mode == "sequential"
    assert [c.endpoint_id for c in result.ordered] == ["step2", "step1"]


# ============ auto scoring ============


def test_auto_orders_clear_best_first_with_scores(gateway_settings):
    best = cand("best", request_count=100, success_rate=0.99, avg_latency_ms=80,
                latency_p95_ms=100, cost_per_1k_input=0.1, quota_remaining_pct=95,
                breaker_state="CLOSED", account_tier="ultra", tags=["coder"],
                model_id="deepseek-coder")
    worst = cand("worst", request_count=100, success_rate=0.2, avg_latency_ms=2500,
                 latency_p95_ms=4000, cost_per_1k_input=5.0, quota_remaining_pct=10,
                 breaker_state="HALF_OPEN", account_tier="free", model_id="mystery-model")
    engine = RoutingStrategyEngine(seed=1)
    decision, release = engine.resolve([worst, best], strategy="auto",
                                       context=ctx(task_type="coding"))
    assert decision.ordered[0] == "best"
    assert decision.scores["best"] > decision.scores["worst"]
    for score in decision.scores.values():
        assert 0.0 <= score <= 1.0
    assert release is None


def test_normalize_scoring_weights_sanitizes_and_renormalizes():
    assert normalize_scoring_weights(None) == DEFAULT_WEIGHTS
    focused = normalize_scoring_weights({"quota": 3.0, "health": 1.0})
    assert focused["quota"] == pytest.approx(0.75)
    assert focused["health"] == pytest.approx(0.25)
    assert sum(focused.values()) == pytest.approx(1.0)
    # All-invalid input falls back to the OmniRoute defaults.
    assert normalize_scoring_weights({"quota": -1.0, "health": float("nan")}) == DEFAULT_WEIGHTS


def test_task_fitness_table_boost_and_neutral():
    assert get_task_fitness("o3", "coding") >= 0.9  # versioned table row
    # Unknown task types fall back to the versioned "default" table.
    assert get_task_fitness("gpt-4o", "not-a-task") == 0.85
    # Models absent from every table stay neutral.
    assert get_task_fitness("totally-unknown-model", "coding") == 0.5
    assert get_task_fitness("totally-unknown-model", "not-a-task") == 0.5
    # Wildcard boost: "fast" segment + coding task -> 0.5 + 0.05.
    assert get_task_fitness("my-fast-model", "coding") == pytest.approx(0.55)


def test_tier_score_base_and_reset_bonus():
    assert calculate_tier_score("ultra", None) == pytest.approx(0.8)
    assert calculate_tier_score("free", None) == pytest.approx(0.0)
    assert calculate_tier_score("unknown-tier", None) == pytest.approx(0.33 * 0.8)
    # 15-day reset interval -> bonus 0.5 -> 0.67*0.8 + 0.5*0.2
    assert calculate_tier_score("pro", 1_296_000) == pytest.approx(0.67 * 0.8 + 0.1)
    assert calculate_tier_score("ultra", 1) <= 1.0  # capped at 1


# ============ quota-share (M1 -> M2 delegation) ============


def test_quota_share_drr_converges_to_weight_ratio(gateway_settings):
    from moa_gateway.quota_scheduler.inflight import get_inflight_tracker

    a = cand("ep-a", connection_id="conn-a", weight=3)
    b = cand("ep-b", connection_id="conn-b", weight=1)
    engine = RoutingStrategyEngine(seed=3)
    wins = {"ep-a": 0, "ep-b": 0}
    for _ in range(4):
        decision, release = engine.resolve([a, b], strategy="quota-share",
                                           context=ctx(group_key="pool"))
        wins[decision.selected] += 1
        assert release is not None
        release()
    assert wins == {"ep-a": 3, "ep-b": 1}
    assert get_inflight_tracker().get("conn-a", NOW_MS) == 0
    assert get_inflight_tracker().get("conn-b", NOW_MS) == 0


def test_quota_share_commit_reserves_inflight_and_release_is_idempotent(gateway_settings):
    from moa_gateway.quota_scheduler.inflight import get_inflight_tracker

    a = cand("ep-a", connection_id="conn-a", weight=1)
    b = cand("ep-b", connection_id="conn-b", weight=1)
    engine = RoutingStrategyEngine(seed=3)
    decision, release = engine.resolve([a, b], strategy="quota-share",
                                       context=ctx(group_key="pool2"))
    winner_conn = "conn-a" if decision.selected == "ep-a" else "conn-b"
    assert get_inflight_tracker().get(winner_conn, NOW_MS) == 1
    release()
    assert get_inflight_tracker().get(winner_conn, NOW_MS) == 0
    release()  # idempotent by contract (#11371)
    assert get_inflight_tracker().get(winner_conn, NOW_MS) == 0


def test_quota_share_dry_run_reserves_nothing(gateway_settings):
    from moa_gateway.quota_scheduler.inflight import get_inflight_tracker
    from moa_gateway.quota_scheduler.quota_share import get_drr_state

    a = cand("ep-a", connection_id="conn-a", weight=1)
    b = cand("ep-b", connection_id="conn-b", weight=1)
    engine = RoutingStrategyEngine(seed=3)
    decision, release = engine.resolve([a, b], strategy="quota-share",
                                       context=ctx(group_key="pool3"), dry_run=True)
    assert decision.selected in {"ep-a", "ep-b"}
    assert release is None
    assert get_inflight_tracker().size() == 0
    assert get_drr_state().snapshot("pool3") == {}


def test_quota_share_saturated_bucket_deprioritizes_not_drops(gateway_settings):
    from moa_gateway.quota_scheduler.buckets import get_buckets

    get_buckets().record_usage("conn-a", "5h", 100.0, None, now_ms=NOW_MS)
    a = cand("ep-a", connection_id="conn-a", weight=100)
    b = cand("ep-b", connection_id="conn-b", weight=1)
    engine = RoutingStrategyEngine(seed=3)
    decision, release = engine.resolve(
        [a, b], strategy="quota-share",
        context=ctx(group_key="pool4", model="openai/gpt-4o"))
    assert decision.selected == "ep-b"  # saturated conn-a demoted to the tail
    assert decision.ordered == ["ep-b", "ep-a"]  # still dispatchable, never dropped
    if release is not None:
        release()


# ============ engine behaviour ============


def test_engine_disabled_raises(monkeypatch):
    from moa_gateway.config import Settings

    settings = Settings(routing_strategies={"enabled": False})
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    engine = RoutingStrategyEngine(seed=1)
    with pytest.raises(RoutingDisabledError):
        engine.resolve([cand("a")], strategy="priority")


def test_engine_unknown_strategy_raises(gateway_settings):
    engine = RoutingStrategyEngine(seed=1)
    with pytest.raises(UnknownStrategyError):
        engine.resolve([cand("a")], strategy="definitely-not-a-strategy")


def test_engine_default_strategy_from_settings(monkeypatch):
    from moa_gateway.config import Settings

    settings = Settings(routing_strategies={"default_strategy": "cost-optimized"})
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    engine = RoutingStrategyEngine(seed=1)
    cands = [cand("exp", cost_per_1k_input=9.0), cand("cheap", cost_per_1k_input=0.01)]
    decision, _ = engine.resolve(cands)
    assert decision.strategy == "cost-optimized"
    assert decision.selected == "cheap"


def test_engine_context_strategy_override_with_alias(gateway_settings):
    engine = RoutingStrategyEngine(seed=1)
    cands = [cand("busy", request_count=99), cand("idle", request_count=1)]
    decision, _ = engine.resolve(cands, context=ctx(strategy="usage"))
    assert decision.strategy == "least-used"
    assert decision.selected == "idle"


def test_engine_session_binding_commit_vs_dry_run(gateway_settings):
    engine = RoutingStrategyEngine(seed=1)
    cands = [cand("a", priority=0, provider="p1"), cand("b", priority=1, provider="p2")]
    # Dry run must not create a session binding.
    engine.resolve(cands, strategy="priority", context=ctx(session_key="s9"), dry_run=True)
    assert engine._session_bindings == {}
    # Commit binds the session to the winner.
    engine.resolve(cands, strategy="priority", context=ctx(session_key="s9"))
    assert engine._session_bindings["s9"].endpoint_id == "a"
    # context-relay now pins "a" even when the pool order changes.
    reordered = [cand("b", priority=0, provider="p2"), cand("a", priority=1, provider="p1")]
    decision, _ = engine.resolve(reordered, strategy="context-relay", context=ctx(session_key="s9"))
    assert decision.selected == "a"


def test_engine_record_outcome_feeds_telemetry_and_quality(gateway_settings):
    engine = RoutingStrategyEngine(seed=1)
    engine.record_outcome("ep-x", 100.0, True)
    engine.record_outcome("ep-x", 300.0, False)
    snapshot = engine.telemetry.snapshot("ep-x")
    assert snapshot is not None
    assert snapshot.request_count == 2
    assert snapshot.success_rate == pytest.approx(0.5)
    quality = engine.telemetry.quality_scores()
    assert quality["ep-x"] == pytest.approx(0.5)
    assert "never-observed" not in quality  # absent endpoints stay neutral


def test_engine_lkgp_persists_to_storage_and_reloads(gateway_settings, routing_storage):
    engine = RoutingStrategyEngine(seed=1)
    cands = [cand("a", provider="p1"), cand("b", provider="p2")]
    engine.resolve(cands, strategy="priority", context=ctx(group_key="grp-x"))
    engine.record_outcome("b", 120.0, True, group_key="grp-x", provider="p2")

    fresh = RoutingStrategyEngine(seed=1)
    decision, _ = fresh.resolve(list(reversed(cands)), strategy="lkgp",
                                context=ctx(group_key="grp-x"))
    assert decision.selected == "b"  # pin survived the engine restart via DB


def test_engine_fitness_override_clamped(gateway_settings):
    engine = RoutingStrategyEngine(seed=1)
    engine.set_fitness_override("mystery-model", 5.0)
    assert engine._fitness_overrides["mystery-model"] == 1.0
    engine.set_fitness_override("mystery-model", -3.0)
    assert engine._fitness_overrides["mystery-model"] == 0.0
    engine.clear_fitness_override("MYSTERY-model")
    assert engine._fitness_overrides == {}


# ============ telemetry store ============


def test_telemetry_window_statistics(routing_storage):
    store = TelemetryStore(history_window=100)
    for latency, ok in [(100.0, True), (200.0, True), (300.0, False)]:
        store.record("ep", latency, ok)
    snap = store.snapshot("ep")
    assert snap.request_count == 3
    assert snap.avg_latency_ms == pytest.approx(200.0)
    assert snap.p95_latency_ms == pytest.approx(290.0)  # interpolated percentile
    assert snap.success_rate == pytest.approx(2 / 3)
    assert snap.error_rate == pytest.approx(1 / 3)
    assert snap.stddev_latency_ms > 0


def test_telemetry_history_window_bounds(routing_storage):
    store = TelemetryStore(history_window=5)
    for i in range(12):
        store.record("ep", float(i), True)
    snap = store.snapshot("ep")
    assert snap.request_count == 12  # lifetime monotonic
    assert snap.window_count == 5  # rolling window capped
    assert snap.avg_latency_ms == pytest.approx((7 + 8 + 9 + 10 + 11) / 5)


def test_telemetry_persist_and_load_roundtrip(routing_storage):
    store = TelemetryStore(history_window=50)
    for i in range(7):
        store.record("ep-a", 100.0 + i, i % 2 == 0)
    store.record("ep-b", 42.0, True)
    written = store.flush()
    assert written == 2

    restored = TelemetryStore(history_window=50)
    count = restored.load()
    assert count == 2
    snap = restored.snapshot("ep-a")
    assert snap.request_count == 7
    assert snap.window_count == 7
    assert snap.success_rate == pytest.approx(4 / 7)
    assert restored.snapshot("ep-b").avg_latency_ms == pytest.approx(42.0)


def test_telemetry_load_skips_corrupt_rows(routing_storage):
    store = TelemetryStore(history_window=10)
    store.record("good", 10.0, True)
    store.flush()
    # Corrupt one row directly in the self-created table.
    with routing_storage.conn() as conn:
        conn.execute("INSERT INTO routing_telemetry (endpoint_id, samples_json, updated_at) "
                     "VALUES ('bad', '{not json', 0)")
        conn.commit()
    restored = TelemetryStore(history_window=10)
    assert restored.load() == 1  # corrupt row skipped, good row restored
    assert restored.snapshot("good") is not None
    assert restored.snapshot("bad") is None


# ============ MoA bridge ============


def _moa_candidate(endpoint_id, **kw) -> ModelCandidate:
    defaults = dict(model_id="m", platform_id="prov", health_status="healthy", weight=100)
    defaults.update(kw)
    return ModelCandidate(endpoint_id=endpoint_id, **defaults)


def test_endpoint_from_model_candidate_maps_fields():
    mc = _moa_candidate("ep1", platform_id="anthropic", health_status="degraded",
                        latency_p95=250.0, success_rate=0.9, weight=55,
                        cost_per_1k_input=0.2, tags=["coder"])
    endpoint = endpoint_from_model_candidate(mc)
    assert endpoint.endpoint_id == "ep1"
    assert endpoint.provider == "anthropic"
    assert endpoint.breaker_state == "HALF_OPEN"  # degraded maps to HALF_OPEN
    assert endpoint.latency_p95_ms == 250.0
    assert endpoint.success_rate == pytest.approx(0.9)
    assert endpoint.weight == 55.0
    assert endpoint.tags == ["coder"]
    dead = endpoint_from_model_candidate(_moa_candidate("ep2", health_status="dead"))
    assert dead.breaker_state == "OPEN"


def test_routing_fusion_select_models_filters_and_limits_n(gateway_settings):
    register_routing_fusion()
    strategy = get_strategy(FUSION_STRATEGY_NAME)
    healthy = [_moa_candidate(f"h{i}", weight=100 - i) for i in range(3)]
    dead = _moa_candidate("dead", health_status="dead", weight=999)
    selected = strategy.select_models([dead] + healthy, context={"group_key": "moa"}, n=2)
    assert len(selected) == 2
    assert "dead" not in selected
    assert set(selected) <= {f"h{i}" for i in range(3)}


def test_routing_fusion_fallback_when_engine_disabled(monkeypatch):
    from moa_gateway.config import Settings

    settings = Settings(routing_strategies={"enabled": False})
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    strategy = RoutingFusionStrategy()
    cands = [_moa_candidate("low", weight=10), _moa_candidate("high", weight=500)]
    selected = strategy.select_models(cands, n=2)
    assert selected == ["high", "low"]  # pre-integration weight ordering preserved


def test_routing_fusion_aggregate_dedupes_and_joins():
    strategy = RoutingFusionStrategy()
    merged = strategy.aggregate(["alpha", "", "beta", "alpha", "  "])
    assert merged == "alpha\n\n---\n\nbeta"
    assert strategy.aggregate([]) == ""
    assert strategy.aggregate(["only"]) == "only"


# ============ HTTP surface ============


@pytest.fixture
def app(gateway_settings):
    from moa_gateway.routes.routing_strategies import router

    application = FastAPI()
    application.include_router(router)
    application.state.routing_engine = RoutingStrategyEngine(seed=2024)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://routing.test") as ac:
        yield ac


async def test_http_requires_api_key(client):
    assert (await client.get("/v1/routing/strategies")).status_code == 401
    assert (await client.post("/v1/routing/resolve", json={"candidates": []})).status_code == 401
    assert (await client.get("/v1/routing/telemetry")).status_code == 401


async def test_http_strategies_catalogue(client):
    response = await client.get("/v1/routing/strategies", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["default_strategy"] == "auto"
    assert payload["count"] == 20
    names = {s["name"] for s in payload["strategies"]}
    assert names == EXPECTED_STRATEGIES
    internal = {s["name"] for s in payload["strategies"] if s["internal"]}
    assert internal == {"quota-share"}


async def test_http_resolve_dry_run_ranking(client):
    body = {
        "candidates": [
            {"endpoint_id": "exp", "cost_per_1k_input": 5.0},
            {"endpoint_id": "cheap", "cost_per_1k_input": 0.01},
        ],
        "strategy": "cost-optimized",
        "dry_run": True,
    }
    response = await client.post("/v1/routing/resolve", json=body, headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "cost-optimized"
    assert payload["selected"] == "cheap"
    assert payload["ordered"] == ["cheap", "exp"]
    assert payload["dry_run"] is True
    assert payload["selections"][0]["rank"] == 0
    # Dry run must not mutate engine counters: identical repeat -> identical order.
    again = (await client.post("/v1/routing/resolve", json=body, headers=AUTH)).json()
    assert again["ordered"] == payload["ordered"]


async def test_http_resolve_rejects_unknown_fields(client):
    body = {"candidates": [], "bogus_field": 1}
    response = await client.post("/v1/routing/resolve", json=body, headers=AUTH)
    assert response.status_code == 422


async def test_http_resolve_unknown_strategy_400(client):
    body = {"candidates": [{"endpoint_id": "a"}], "strategy": "nope"}
    response = await client.post("/v1/routing/resolve", json=body, headers=AUTH)
    assert response.status_code == 400
    assert "nope" in response.json()["detail"]


async def test_http_resolve_disabled_503(app, monkeypatch):
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        routing_strategies={"enabled": False},
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://routing.test") as ac:
        response = await ac.post("/v1/routing/resolve",
                                 json={"candidates": [{"endpoint_id": "a"}]}, headers=AUTH)
        assert response.status_code == 503
        # /strategies stays reachable (read-only catalogue incl. enabled flag).
        catalogue = await ac.get("/v1/routing/strategies", headers=AUTH)
        assert catalogue.status_code == 200
        assert catalogue.json()["enabled"] is False


async def test_http_telemetry_reflects_outcomes(app):
    app.state.routing_engine.record_outcome("ep-web", 150.0, True)
    app.state.routing_engine.record_outcome("ep-web", 250.0, False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://routing.test") as ac:
        response = await ac.get("/v1/routing/telemetry", headers=AUTH)
    assert response.status_code == 200
    payload = response.json()
    assert payload["endpoint_count"] == 1
    entry = payload["endpoints"][0]
    assert entry["endpoint_id"] == "ep-web"
    assert entry["request_count"] == 2
    assert entry["success_rate"] == pytest.approx(0.5)


async def test_http_capability_toggle_503(app, gateway_settings, routing_storage):
    from moa_gateway import capability_toggles

    capability_toggles.set_enabled("routing_strategies", False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://routing.test") as ac:
        response = await ac.get("/v1/routing/strategies", headers=AUTH)
    assert response.status_code == 503
    assert "routing_strategies" in response.json()["detail"]
    capability_toggles.set_enabled("routing_strategies", True)
