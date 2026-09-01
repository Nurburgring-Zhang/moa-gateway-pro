"""Saturating per-connection, per-window quota buckets (M2).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``src/lib/quota/accountBuckets.ts`` — 100%-saturation buckets with lazy
reset on read, fail-open semantics, injectable clock, and the
``updateAccountBuckets`` mapping of Claude-style quota maps
("session (5h)" → "5h", "weekly (7d)" → "7d", "weekly <model> (7d)" →
"7d:<model>").

State is encapsulated in :class:`BucketStore`; the module exposes a shared
default store (``get_buckets``) so the quota-share selector and the monitor
operate on one consistent view, mirroring the upstream single-Map cohesion
rule.
"""

from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

# OmniRoute SATURATION_THRESHOLD_PCT
SATURATION_THRESHOLD_PCT = 100.0

_WEEKLY_MODEL_RE = re.compile(r"^weekly (.+) \(7d\)$")


@dataclass
class _BucketEntry:
    saturated: bool
    resets_at_ms: float  # 0 when the reset instant is unknown


def _parse_reset_at_ms(reset_at: str | None) -> float:
    if not reset_at:
        return 0.0
    candidate = str(reset_at)
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return 0.0
    ms = parsed.timestamp() * 1000
    return ms if ms > 0 else 0.0


class BucketStore:
    """In-process saturation buckets keyed by (connection_id, window_key)."""

    def __init__(self) -> None:
        self._buckets: dict[str, _BucketEntry] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(connection_id: str, window_key: str) -> str:
        return f"{connection_id}::{window_key}"

    def is_saturated(self, connection_id: str, window_key: str, now_ms: float | None = None) -> bool:
        """Lazy-reset saturation probe (fail-open on missing/empty inputs)."""
        if not connection_id or not window_key:
            return False
        if now_ms is None:
            now_ms = time.time() * 1000
        with self._lock:
            entry = self._buckets.get(self._key(connection_id, window_key))
            if entry is None:
                return False
            if entry.resets_at_ms > 0 and now_ms >= entry.resets_at_ms:
                del self._buckets[self._key(connection_id, window_key)]
                return False
            return entry.saturated

    def record_usage(
        self,
        connection_id: str,
        window_key: str,
        used_pct: float,
        reset_at: str | None,
        now_ms: float | None = None,
    ) -> None:
        """Record one utilization observation (port of ``recordUsage``)."""
        if not connection_id or not window_key:
            return
        if now_ms is None:
            now_ms = time.time() * 1000
        key = self._key(connection_id, window_key)
        resets_at_ms = _parse_reset_at_ms(reset_at)
        with self._lock:
            if resets_at_ms > 0 and now_ms >= resets_at_ms:
                self._buckets.pop(key, None)
                return
            saturated = math.isfinite(used_pct) and used_pct >= SATURATION_THRESHOLD_PCT
            if not saturated:
                self._buckets.pop(key, None)
                return
            self._buckets[key] = _BucketEntry(saturated=True, resets_at_ms=resets_at_ms)

    def update_from_usage_quotas(
        self, connection_id: str, quotas: dict | None, now_ms: float | None = None
    ) -> None:
        """Port of ``updateAccountBuckets`` (Claude-style quota maps)."""
        if not connection_id or not quotas:
            return
        self._process_entry(connection_id, "5h", quotas.get("session (5h)"), now_ms)
        self._process_entry(connection_id, "7d", quotas.get("weekly (7d)"), now_ms)
        for key, entry in quotas.items():
            match = _WEEKLY_MODEL_RE.match(str(key))
            if match and match.group(1):
                self._process_entry(connection_id, f"7d:{match.group(1)}", entry, now_ms)

    def _process_entry(self, connection_id: str, window_key: str, entry, now_ms) -> None:
        if not isinstance(entry, dict):
            return
        used = entry.get("used")
        if not isinstance(used, (int, float)):
            return
        self.record_usage(connection_id, window_key, float(used), entry.get("resetAt") or entry.get("reset_at"), now_ms)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._buckets)


_buckets: BucketStore | None = None
_buckets_lock = threading.Lock()


def get_buckets() -> BucketStore:
    global _buckets
    with _buckets_lock:
        if _buckets is None:
            _buckets = BucketStore()
        return _buckets


def reset_buckets_for_tests() -> None:
    global _buckets
    with _buckets_lock:
        _buckets = None
