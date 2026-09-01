"""In-flight request counters with lease expiry (M2).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``open-sse/services/combo/quotaShareInflight.ts`` — per-connection in-flight
slots where every increment stamps ``now_ms + lease_ms``; explicit decrement
clears the slot when the request settles, and an aborted request's slot
auto-expires after ``DEFAULT_LEASE_MS`` so the counter can never leak
monotonically upward. Fail-open: unknown/empty ids read as 0.

The clock is injectable (``now_ms``) so tests are deterministic.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# OmniRoute DEFAULT_LEASE_MS
DEFAULT_LEASE_MS = 120_000.0


@dataclass
class _InflightSlot:
    count: int
    expires_at_ms: float


class InflightTracker:
    """Leased in-flight counters keyed by connection id."""

    def __init__(self, default_lease_ms: float = DEFAULT_LEASE_MS) -> None:
        self._default_lease_ms = float(default_lease_ms)
        self._slots: dict[str, _InflightSlot] = {}
        self._lock = threading.Lock()

    def increment(
        self, connection_id: str, lease_ms: float | None = None, now_ms: float | None = None
    ) -> int:
        """Increment and return the new count (empty id → no-op, 0)."""
        if not connection_id:
            return 0
        if now_ms is None:
            now_ms = time.time() * 1000
        lease = self._default_lease_ms if lease_ms is None else float(lease_ms)
        with self._lock:
            self._prune_locked(now_ms)
            slot = self._slots.get(connection_id)
            base = slot.count if slot is not None and slot.expires_at_ms > now_ms else 0
            new_count = base + 1
            self._slots[connection_id] = _InflightSlot(
                count=new_count, expires_at_ms=now_ms + lease
            )
            return new_count

    def decrement(self, connection_id: str, now_ms: float | None = None) -> None:
        """Decrement flooring at 0; removes the slot at zero/expiry."""
        if not connection_id:
            return
        if now_ms is None:
            now_ms = time.time() * 1000
        with self._lock:
            slot = self._slots.get(connection_id)
            if slot is None or slot.expires_at_ms <= now_ms:
                self._slots.pop(connection_id, None)
                return
            new_count = max(0, slot.count - 1)
            if new_count == 0:
                del self._slots[connection_id]
            else:
                self._slots[connection_id] = _InflightSlot(
                    count=new_count, expires_at_ms=slot.expires_at_ms
                )

    def get(self, connection_id: str, now_ms: float | None = None) -> int:
        """Current in-flight count (0 when unknown/empty/expired)."""
        if not connection_id:
            return 0
        if now_ms is None:
            now_ms = time.time() * 1000
        with self._lock:
            slot = self._slots.get(connection_id)
            if slot is None or slot.expires_at_ms <= now_ms:
                return 0
            return slot.count

    def size(self) -> int:
        with self._lock:
            return len(self._slots)

    def clear(self) -> None:
        with self._lock:
            self._slots.clear()

    def _prune_locked(self, now_ms: float) -> None:
        for key, slot in list(self._slots.items()):
            if slot.expires_at_ms <= now_ms:
                del self._slots[key]


_inflight: InflightTracker | None = None
_inflight_lock = threading.Lock()


def get_inflight_tracker() -> InflightTracker:
    global _inflight
    with _inflight_lock:
        if _inflight is None:
            _inflight = InflightTracker()
        return _inflight


def reset_inflight_for_tests() -> None:
    global _inflight
    with _inflight_lock:
        _inflight = None
