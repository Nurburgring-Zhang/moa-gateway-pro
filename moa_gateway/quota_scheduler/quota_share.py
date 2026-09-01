"""Quota-share selector: bucket gating + DRR + P2C in-flight (M2).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``open-sse/services/combo/quotaShareStrategy.ts`` — the dedicated selection
strategy for internal quota-share pools. Three mechanisms in sequence:

1. **Per-model bucket gating** (``filterEligibleBySaturation``): connections
   whose 5h / 7d / 7d:<model> window is saturated are DEPRIORITIZED to the
   tail (never dropped); if every target is saturated all stay eligible
   (fail-open — a quota-share pool is never hard-blocked).
2. **DRR** (``applyDrr``): deficit round-robin with quantum =
   weight/totalWeight; the max-deficit target wins and pays a unit cost so
   long-run frequency converges exactly to the weight ratio. Per-group state
   capped at ``MAX_DRR_COMBOS`` with oldest-entry eviction.
3. **P2C over in-flight** (``pickByInflightP2C``): between the top two DRR
   candidates the one with fewer live in-flight requests wins; ties keep the
   DRR winner. The winner's slot is reserved immediately and an idempotent
   release callback is returned (OmniRoute #11371).

``commit=False`` supports dry-run ranking (M1 HTTP surface): the pipeline
runs against a scratch copy of the DRR state and reserves no in-flight slot.
All state is in-process; the clock is injectable (``now_ms``).
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

from .buckets import BucketStore, get_buckets
from .inflight import InflightTracker, get_inflight_tracker

# OmniRoute MAX_DRR_COMBOS
MAX_DRR_COMBOS = 200


@dataclass(frozen=True)
class ShareTarget:
    """Duck-typed dispatch target handed over from M1 (no M1 import)."""

    execution_key: str
    endpoint_id: str
    connection_id: str = ""
    provider: str = ""
    weight: float = 100.0


@dataclass
class QuotaShareResult:
    target: ShareTarget | None
    # Winner → remaining eligible → at-cap → saturated (all dispatchable).
    ordered_keys: list[str] = field(default_factory=list)
    release: Callable[[], None] = field(default=lambda: None)
    # Diagnostics: which tier each key landed in.
    deprioritized_keys: list[str] = field(default_factory=list)
    at_cap_keys: list[str] = field(default_factory=list)


class DrrState:
    """Per-group deficit tables with oldest-entry eviction."""

    def __init__(self, max_groups: int = MAX_DRR_COMBOS) -> None:
        self._max_groups = max_groups
        self._state: OrderedDict[str, dict[str, float]] = OrderedDict()
        self._lock = threading.Lock()

    def deficits(self, group_key: str) -> dict[str, float]:
        with self._lock:
            table = self._state.get(group_key)
            if table is None:
                if len(self._state) >= self._max_groups:
                    self._state.popitem(last=False)
                table = {}
                self._state[group_key] = table
            else:
                self._state.move_to_end(group_key)
            return table

    def snapshot(self, group_key: str) -> dict[str, float]:
        with self._lock:
            return dict(self._state.get(group_key, {}))

    def clear(self) -> None:
        with self._lock:
            self._state.clear()


_drr_state: DrrState | None = None
_drr_lock = threading.Lock()


def get_drr_state() -> DrrState:
    global _drr_state
    with _drr_lock:
        if _drr_state is None:
            _drr_state = DrrState()
        return _drr_state


def reset_quota_share_for_tests() -> None:
    global _drr_state
    with _drr_lock:
        _drr_state = None


# ---------------------------------------------------------------------------
# Mechanism helpers (faithful ports)
# ---------------------------------------------------------------------------


def bare_model_name(model_str: str) -> str:
    """Port of ``bareModelName``: strip the "<provider>/" prefix."""
    slash = model_str.find("/")
    return model_str[slash + 1 :] if slash >= 0 else model_str


def normalize_weight(weight: float | None) -> float:
    """Port of ``normalizeWeight``: explicit 0 stays 0, invalid → 1."""
    if weight == 0:
        return 0.0
    if isinstance(weight, (int, float)) and math.isfinite(weight) and weight > 0:
        return float(weight)
    return 1.0


def filter_eligible_by_saturation(
    targets: list[ShareTarget], model_str: str, now_ms: float, buckets: BucketStore
) -> list[ShareTarget]:
    """Port of ``filterEligibleBySaturation`` (fail-open when all saturated)."""
    model_name = bare_model_name(model_str)
    eligible = []
    for target in targets:
        conn_id = target.connection_id or ""
        if conn_id == "":
            eligible.append(target)
            continue
        saturated = (
            buckets.is_saturated(conn_id, "5h", now_ms)
            or buckets.is_saturated(conn_id, "7d", now_ms)
            or (model_name != "" and buckets.is_saturated(conn_id, f"7d:{model_name}", now_ms))
        )
        if not saturated:
            eligible.append(target)
    return eligible if eligible else list(targets)


def partition_by_concurrency_cap(
    targets: list[ShareTarget],
    caps: dict[str, int] | None,
    now_ms: float,
    inflight: InflightTracker,
) -> tuple[list[ShareTarget], list[ShareTarget]]:
    """Port of ``partitionByConcurrencyCap`` (fail-open: never demote all)."""
    if not caps:
        return list(targets), []
    with_room: list[ShareTarget] = []
    at_cap: list[ShareTarget] = []
    for target in targets:
        conn_id = target.connection_id or ""
        cap = caps.get(conn_id) if conn_id else None
        if cap is None or not math.isfinite(cap) or cap <= 0:
            with_room.append(target)
            continue
        if inflight.get(conn_id, now_ms) >= cap:
            at_cap.append(target)
        else:
            with_room.append(target)
    if not with_room:
        return list(targets), []
    return with_room, at_cap


def apply_drr(
    targets: list[ShareTarget], group_key: str, deficits: dict[str, float]
) -> list[ShareTarget]:
    """Port of ``applyDrr`` over an explicit deficit table (mutates it)."""
    if len(targets) <= 1:
        return list(targets)
    total_weight = sum(normalize_weight(t.weight) for t in targets)
    if total_weight <= 0:
        return list(targets)
    for target in targets:
        quantum = normalize_weight(target.weight) / total_weight
        deficits[target.execution_key] = deficits.get(target.execution_key, 0.0) + quantum
    winner = targets[0]
    best_deficit = deficits.get(winner.execution_key, 0.0)
    for target in targets[1:]:
        deficit = deficits.get(target.execution_key, 0.0)
        if deficit > best_deficit:
            best_deficit = deficit
            winner = target
    deficits[winner.execution_key] = best_deficit - 1
    rest = [t for t in targets if t.execution_key != winner.execution_key]
    return [winner] + rest


def pick_by_inflight_p2c(
    first: ShareTarget, second: ShareTarget, now_ms: float, inflight: InflightTracker
) -> int:
    """Port of ``pickByInflightP2C``: 1 when the runner-up is less loaded."""
    load_first = inflight.get(first.connection_id or "", now_ms)
    load_second = inflight.get(second.connection_id or "", now_ms)
    return 1 if load_second < load_first else 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def select_quota_share_target(
    targets: list[ShareTarget],
    group_key: str,
    model_str: str = "",
    now_ms: float | None = None,
    max_concurrent_by_connection: dict[str, int] | None = None,
    commit: bool = True,
    buckets: BucketStore | None = None,
    inflight: InflightTracker | None = None,
    drr_state: DrrState | None = None,
) -> QuotaShareResult:
    """Select the quota-share winner and the full fallback order.

    ``commit=True`` (production) mutates DRR state and reserves the winner's
    in-flight slot; the returned ``release`` MUST be called exactly once when
    the request settles. ``commit=False`` runs the identical pipeline on a
    scratch DRR copy with no reservation (dry-run ranking).
    """
    if now_ms is None:
        now_ms = time.time() * 1000
    buckets = buckets or get_buckets()
    inflight = inflight or get_inflight_tracker()
    drr = drr_state or get_drr_state()

    if not targets:
        return QuotaShareResult(target=None, ordered_keys=[])

    eligible = filter_eligible_by_saturation(targets, model_str, now_ms, buckets)
    eligible_keys = {t.execution_key for t in eligible}
    saturated = [t for t in targets if t.execution_key not in eligible_keys]

    with_room, at_cap = partition_by_concurrency_cap(
        eligible, max_concurrent_by_connection, now_ms, inflight
    )

    if commit:
        deficits = drr.deficits(group_key)
        ordered = apply_drr(with_room, group_key, deficits)
    else:
        scratch = dict(drr.snapshot(group_key))
        ordered = apply_drr(with_room, group_key, scratch)

    if len(ordered) >= 2 and pick_by_inflight_p2c(ordered[0], ordered[1], now_ms, inflight) == 1:
        winner = ordered[1]
        rest = [ordered[0]] + ordered[2:]
    else:
        winner = ordered[0]
        rest = ordered[1:]

    winner_conn = winner.connection_id or ""
    if commit and winner_conn:
        inflight.increment(winner_conn, now_ms=now_ms)

    ordered_keys = (
        [winner.execution_key]
        + [t.execution_key for t in rest]
        + [t.execution_key for t in at_cap]
        + [t.execution_key for t in saturated]
    )

    released = {"done": False}

    def release() -> None:
        if released["done"]:
            return
        released["done"] = True
        if winner_conn:
            # Selection-time clock: the slot stamped now_ms+lease is live here.
            inflight.decrement(winner_conn, now_ms)

    return QuotaShareResult(
        target=winner,
        ordered_keys=ordered_keys,
        release=release if commit else (lambda: None),
        deprioritized_keys=[t.execution_key for t in saturated],
        at_cap_keys=[t.execution_key for t in at_cap],
    )
