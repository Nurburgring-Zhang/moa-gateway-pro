"""M2 — OmniRoute-style quota telemetry scheduler (v4.1.0 integration).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
provider-neutral quota value model with source-priority merge, rate-limit
header parsing (standard + Anthropic families), per-window saturation
buckets, leased in-flight counters, the DRR + P2C quota-share selector,
change-detected snapshot persistence, and the adaptive monitoring cadence.
See each module's header for the exact upstream file attribution.

Public API
----------
- :class:`QuotaMonitor` / :func:`get_monitor` — adaptive monitor + gate.
- :func:`parse_quota_headers` / :func:`parse_reset_time` — header parsing.
- :class:`SnapshotStore` / :func:`get_snapshot_store` — durable history.
- :func:`select_quota_share_target` — DRR + P2C selector (called by the
  M1 ``quota-share`` strategy; M2 never imports M1).
- Models: ``QuotaValue``, ``QuotaState``, ``ShareTarget``.

Everything is opt-in: with ``settings.quota.enabled`` False the monitor
admits all traffic and never alters pre-existing gateway behaviour.
"""

from __future__ import annotations

from .buckets import (
    SATURATION_THRESHOLD_PCT,
    BucketStore,
    get_buckets,
    reset_buckets_for_tests,
)
from .collection import (
    ConfiguredQuotaAdapter,
    EstimatedQuotaAdapter,
    ResponseHeaderAdapter,
    collect_quota_state,
    fold_values,
)
from .headers import (
    ANTHROPIC_HEADERS,
    STANDARD_HEADERS,
    parse_quota_headers,
    parse_rate_limit_headers,
    parse_reset_time,
    reset_header_to_iso,
    retry_after_ms,
    to_plain_headers,
)
from .inflight import (
    DEFAULT_LEASE_MS,
    InflightTracker,
    get_inflight_tracker,
    reset_inflight_for_tests,
)
from .models import (
    QuotaState,
    QuotaValue,
    merge_quota_values,
    source_rank,
    state_to_summary,
    status_for_values,
    unknown_quota_state,
)
from .monitor import QuotaMonitor, get_monitor, reset_monitor_for_tests
from .quota_share import (
    MAX_DRR_COMBOS,
    DrrState,
    QuotaShareResult,
    ShareTarget,
    apply_drr,
    bare_model_name,
    filter_eligible_by_saturation,
    get_drr_state,
    normalize_weight,
    partition_by_concurrency_cap,
    pick_by_inflight_p2c,
    reset_quota_share_for_tests,
    select_quota_share_target,
)
from .snapshots import (
    SnapshotStore,
    compute_change_key,
    get_snapshot_store,
    reset_snapshot_store_for_tests,
)

__all__ = [
    "ANTHROPIC_HEADERS",
    "BucketStore",
    "ConfiguredQuotaAdapter",
    "DEFAULT_LEASE_MS",
    "DrrState",
    "EstimatedQuotaAdapter",
    "InflightTracker",
    "MAX_DRR_COMBOS",
    "QuotaMonitor",
    "QuotaShareResult",
    "QuotaState",
    "QuotaValue",
    "ResponseHeaderAdapter",
    "SATURATION_THRESHOLD_PCT",
    "STANDARD_HEADERS",
    "ShareTarget",
    "SnapshotStore",
    "apply_drr",
    "bare_model_name",
    "collect_quota_state",
    "compute_change_key",
    "filter_eligible_by_saturation",
    "fold_values",
    "get_buckets",
    "get_drr_state",
    "get_inflight_tracker",
    "get_monitor",
    "get_snapshot_store",
    "merge_quota_values",
    "normalize_weight",
    "parse_quota_headers",
    "parse_rate_limit_headers",
    "parse_reset_time",
    "partition_by_concurrency_cap",
    "pick_by_inflight_p2c",
    "reset_buckets_for_tests",
    "reset_header_to_iso",
    "reset_inflight_for_tests",
    "reset_monitor_for_tests",
    "reset_quota_share_for_tests",
    "reset_snapshot_store_for_tests",
    "retry_after_ms",
    "select_quota_share_target",
    "source_rank",
    "state_to_summary",
    "status_for_values",
    "to_plain_headers",
    "unknown_quota_state",
]
