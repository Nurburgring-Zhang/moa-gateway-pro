"""Adaptive quota monitor (M2).

New gateway code on top of the ported OmniRoute primitives (MIT,
https://github.com/diegosouzapw/OmniRoute):

- :mod:`.headers` (port of ``rateLimitManager/headers.ts`` and
  ``providerQuotaTelemetry.ts``) parses provider response headers.
- :mod:`.collection` (port of ``collectQuotaState``) merges observations
  per dimension with source priority ``provider_api > response_headers >
  configured > estimated``.
- :mod:`.buckets` (port of ``accountBuckets.ts``) tracks window saturation.
- :mod:`.snapshots` persists only real changes (port of
  ``quotaSnapshotChanged``).

:class:`QuotaMonitor` ties them together: it ingests header observations
after every provider response, folds them into the stored per-endpoint
state, classifies status, persists genuine changes, answers ``can_afford``
admission checks (fail-open per ``settings.quota.fail_open``), and exposes
the adaptive polling cadence (normal ``poll_interval_s`` → fast
``fast_poll_interval_s`` once utilization crosses ``warn_threshold``).

All settings access is lazy; disabling ``settings.quota.enabled`` makes the
monitor inert (admission always allows, cadence stays nominal) so the module
cannot alter pre-existing gateway behaviour.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from .buckets import get_buckets
from .collection import fold_values
from .headers import parse_quota_headers
from .models import (
    QuotaState,
    QuotaValue,
    state_to_summary,
    status_for_values,
    unknown_quota_state,
)
from .snapshots import SnapshotStore, get_snapshot_store

logger = logging.getLogger(__name__)


def _utc_now_iso(now_ms: float | None = None) -> str:
    if now_ms is None:
        now = datetime.now(timezone.utc)
    else:
        now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def _parse_iso_ms(text: str | None) -> float | None:
    if not text:
        return None
    candidate = str(text)
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000


class QuotaMonitor:
    """Per-endpoint quota bookkeeping + admission gate + adaptive cadence."""

    def __init__(self, snapshots: SnapshotStore | None = None) -> None:
        self._states: dict[str, QuotaState] = {}
        self._lock = threading.Lock()
        self._snapshots = snapshots

    # ------------------------------------------------------------- settings

    def _cfg(self):
        from ..config import get_settings

        return get_settings().quota

    def _store(self) -> SnapshotStore:
        return self._snapshots if self._snapshots is not None else get_snapshot_store()

    @staticmethod
    def _state_key(provider_id: str, connection_id: str, endpoint_id: str) -> str:
        return endpoint_id or connection_id or provider_id

    # ---------------------------------------------------------- observation

    def observe_headers(
        self,
        headers: Any,
        provider_id: str,
        connection_id: str = "",
        endpoint_id: str = "",
        persist: bool = True,
        now_ms: float | None = None,
    ) -> QuotaState:
        """Ingest one provider response's rate-limit headers.

        Parses both quota dimensions, folds them into the stored state with
        source-priority merge, re-classifies status, persists genuine changes
        and returns the resulting state. Headers carrying no quota data leave
        the existing state untouched.
        """
        values, _snapshot = parse_quota_headers(
            headers, provider_id, connection_id, now_ms=now_ms
        )
        if not values:
            with self._lock:
                existing = self._states.get(
                    self._state_key(provider_id, connection_id, endpoint_id)
                )
            if existing is not None:
                return existing
            return unknown_quota_state(provider_id, connection_id, endpoint_id)

        key = self._state_key(provider_id, connection_id, endpoint_id)
        threshold = self._approaching_threshold()
        with self._lock:
            current = self._states.get(key)
            if current is None:
                current = QuotaState(
                    provider_id=provider_id,
                    connection_id=connection_id,
                    endpoint_id=endpoint_id,
                    supported=True,
                    fetched_at=_utc_now_iso(now_ms),
                    values=[],
                    status="unknown",
                )
            updated = fold_values(current, values, threshold)
            updated = updated.model_copy(
                update={
                    "provider_id": provider_id,
                    "connection_id": connection_id or current.connection_id,
                    "endpoint_id": endpoint_id or current.endpoint_id,
                    "fetched_at": _utc_now_iso(now_ms),
                }
            )
            self._states[key] = updated
        if persist:
            self._store().record(updated)
        return updated

    def observe_state(
        self, state: QuotaState, persist: bool = True
    ) -> QuotaState:
        """Ingest a full externally-collected state (provider_api adapter)."""
        key = self._state_key(state.provider_id, state.connection_id, state.endpoint_id)
        threshold = self._approaching_threshold()
        with self._lock:
            current = self._states.get(key)
            if current is None:
                updated = state
            else:
                updated = fold_values(current, state.values, threshold)
                updated = updated.model_copy(
                    update={
                        "supported": state.supported or updated.supported,
                        "fetched_at": state.fetched_at or updated.fetched_at,
                        "error": state.error,
                    }
                )
            self._states[key] = updated
        if persist:
            self._store().record(updated)
        return updated

    def observe_usage_quotas(
        self, connection_id: str, quotas: dict | None, now_ms: float | None = None
    ) -> None:
        """Feed Claude-style usage-quota maps into the saturation buckets."""
        get_buckets().update_from_usage_quotas(connection_id, quotas, now_ms)

    def set_usage(
        self,
        provider_id: str,
        endpoint_id: str = "",
        connection_id: str = "",
        values: list[QuotaValue] | None = None,
        persist: bool = True,
        now_ms: float | None = None,
    ) -> QuotaState:
        """Record an authoritative provider-API observation (highest rank)."""
        stamped = [
            v.model_copy(update={"source": "provider_api", "confidence": "authoritative"})
            if v.source == "unknown"
            else v
            for v in (values or [])
        ]
        key = self._state_key(provider_id, connection_id, endpoint_id)
        threshold = self._approaching_threshold()
        with self._lock:
            current = self._states.get(key)
            if current is None:
                current = QuotaState(
                    provider_id=provider_id,
                    connection_id=connection_id,
                    endpoint_id=endpoint_id,
                    supported=True,
                    fetched_at=_utc_now_iso(now_ms),
                    values=[],
                    status="unknown",
                )
            updated = fold_values(current, stamped, threshold)
            updated = updated.model_copy(update={"fetched_at": _utc_now_iso(now_ms)})
            self._states[key] = updated
        if persist:
            self._store().record(updated)
        return updated

    # ---------------------------------------------------------------- query

    def get_state(
        self, provider_id: str = "", connection_id: str = "", endpoint_id: str = ""
    ) -> QuotaState | None:
        key = self._state_key(provider_id, connection_id, endpoint_id)
        with self._lock:
            state = self._states.get(key)
            if state is not None:
                return state
            if endpoint_id:
                for candidate in self._states.values():
                    if candidate.endpoint_id == endpoint_id:
                        return candidate
            return None

    def states(self) -> list[QuotaState]:
        with self._lock:
            return list(self._states.values())

    def status_summary(self) -> dict[str, Any]:
        cfg = self._cfg()
        with self._lock:
            states = list(self._states.values())
        return {
            "enabled": cfg.enabled,
            "fail_open": cfg.fail_open,
            "poll_interval_s": cfg.poll_interval_s,
            "fast_poll_interval_s": cfg.fast_poll_interval_s,
            "warn_threshold": cfg.warn_threshold,
            "exhaust_threshold": cfg.exhaust_threshold,
            "endpoint_count": len(states),
            "endpoints": [state_to_summary(state) for state in states],
        }

    # ------------------------------------------------------------- admission

    def can_afford_request(
        self,
        provider_id: str = "",
        connection_id: str = "",
        endpoint_id: str = "",
        now_ms: float | None = None,
    ) -> dict[str, Any]:
        """Admission gate. Uncertainty never blocks (fail-open policy).

        Returns ``{"allowed", "reason", "status", "retry_after_ms"}``.
        Exhausted endpoints are blocked only when ``settings.quota.fail_open``
        is False; saturated account buckets count as exhaustion.
        """
        cfg = self._cfg()
        if not cfg.enabled:
            return {"allowed": True, "reason": "scheduler_disabled", "status": "unknown",
                    "retry_after_ms": None}

        state = self.get_state(provider_id, connection_id, endpoint_id)
        retry_after = None
        if state is None or not state.values:
            return {"allowed": True, "reason": "no_quota_data", "status": "unknown",
                    "retry_after_ms": None}

        exhausted_values = [v for v in state.values if v.is_exhausted()]
        # Window saturation (buckets) is an independent exhaustion signal.
        buckets = get_buckets()
        if now_ms is None:
            now_ms = time.time() * 1000
        conn_id = connection_id or state.connection_id
        bucket_saturated = bool(conn_id) and (
            buckets.is_saturated(conn_id, "5h", now_ms)
            or buckets.is_saturated(conn_id, "7d", now_ms)
        )

        if exhausted_values or bucket_saturated or state.status == "exhausted":
            for value in exhausted_values:
                reset_ms = _parse_iso_ms(value.reset_at)
                if reset_ms is not None and reset_ms > now_ms:
                    candidate = reset_ms - now_ms
                    retry_after = candidate if retry_after is None else min(retry_after, candidate)
            if cfg.fail_open:
                return {"allowed": True, "reason": "exhausted_fail_open",
                        "status": "exhausted", "retry_after_ms": retry_after}
            return {"allowed": False, "reason": "quota_exhausted",
                    "status": "exhausted", "retry_after_ms": retry_after}

        if state.status == "approaching_limit":
            return {"allowed": True, "reason": "approaching_limit",
                    "status": "approaching_limit", "retry_after_ms": None}
        return {"allowed": True, "reason": "healthy", "status": state.status,
                "retry_after_ms": None}

    # -------------------------------------------------------------- cadence

    def next_poll_delay(
        self, provider_id: str = "", connection_id: str = "", endpoint_id: str = ""
    ) -> float:
        """Adaptive cadence: fast polling near exhaustion, nominal otherwise.

        Fast mode triggers when any dimension's utilization crosses
        ``warn_threshold`` (used/limit) — or remaining/limit drops below
        ``1 - warn_threshold`` — or the endpoint is exhausted/unsupported
        states stay nominal (no data → never hammer providers).
        """
        cfg = self._cfg()
        state = self.get_state(provider_id, connection_id, endpoint_id)
        if state is None or not state.values:
            return cfg.poll_interval_s
        if state.status == "exhausted":
            return cfg.fast_poll_interval_s
        warn = cfg.warn_threshold
        for value in state.values:
            if (
                value.used is not None
                and value.limit is not None
                and value.limit > 0
                and (value.used / value.limit) >= warn
            ):
                return cfg.fast_poll_interval_s
            if (
                value.remaining is not None
                and value.limit is not None
                and value.limit > 0
                and (value.remaining / value.limit) <= (1.0 - warn)
            ):
                return cfg.fast_poll_interval_s
        return cfg.poll_interval_s

    def next_poll_delay_global(self) -> float:
        """Fastest cadence across all tracked endpoints (monitor loop view)."""
        cfg = self._cfg()
        delays = [
            self.next_poll_delay(
                provider_id=state.provider_id,
                connection_id=state.connection_id,
                endpoint_id=state.endpoint_id,
            )
            for state in self.states()
        ]
        return min(delays) if delays else cfg.poll_interval_s

    # -------------------------------------------------------------- refresh

    def refresh(self, now_ms: float | None = None) -> dict[str, Any]:
        """Drop stale window values (reset instant passed) and re-classify.

        A value whose ``reset_at`` lies in the past describes a window that
        has already rolled over — the observation no longer reflects the
        provider's current budget, so it is removed before re-scoring.
        Genuine status changes are persisted via the snapshot store.
        """
        if now_ms is None:
            now_ms = time.time() * 1000
        threshold = self._approaching_threshold()
        changed: list[str] = []
        with self._lock:
            items = list(self._states.items())
        for key, state in items:
            kept = []
            dropped = 0
            for value in state.values:
                reset_ms = _parse_iso_ms(value.reset_at)
                if reset_ms is not None and reset_ms <= now_ms:
                    dropped += 1
                    continue
                kept.append(value)
            if dropped == 0:
                continue
            if kept:
                new_status = status_for_values(kept, threshold)
            else:
                new_status = "unknown"
            updated = state.model_copy(
                update={
                    "values": kept,
                    "status": new_status,
                    "supported": bool(kept),
                    "fetched_at": _utc_now_iso(now_ms),
                }
            )
            with self._lock:
                self._states[key] = updated
            if self._store().record(updated):
                changed.append(key)
        return {"checked": len(items), "refreshed": len(changed), "changed": changed}

    # ---------------------------------------------------------------- admin

    def reset(self) -> None:
        with self._lock:
            self._states.clear()

    def _approaching_threshold(self) -> float:
        try:
            return 1.0 - self._cfg().warn_threshold
        except Exception:
            return 0.2


_monitor: QuotaMonitor | None = None
_monitor_lock = threading.Lock()


def get_monitor() -> QuotaMonitor:
    global _monitor
    with _monitor_lock:
        if _monitor is None:
            _monitor = QuotaMonitor()
        return _monitor


def reset_monitor_for_tests() -> None:
    global _monitor
    with _monitor_lock:
        _monitor = None
