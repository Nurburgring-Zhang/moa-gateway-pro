"""Per-endpoint rolling telemetry for the routing-strategy engine.

Real in-memory rolling statistics (deque bounded by
``settings.routing_strategies.history_window``) plus durable persistence in a
self-created ``routing_telemetry`` table so latency/success history survives
restarts. Persistence is throttled (every N records per endpoint, plus an
explicit ``flush``) and fail-soft: a storage outage degrades to in-memory
operation and is logged, never raised into the request path.

New-code table (created with IF NOT EXISTS; shared gateway files untouched):

    routing_telemetry(endpoint_id TEXT PRIMARY KEY,
                      samples_json TEXT NOT NULL,
                      updated_at REAL NOT NULL)
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS routing_telemetry (
    endpoint_id TEXT PRIMARY KEY,
    samples_json TEXT NOT NULL,
    updated_at REAL NOT NULL
)
"""

# Persist at most every PERSIST_EVERY records per endpoint (per flush window).
PERSIST_EVERY = 10
MAX_PERSISTED_SAMPLES = 500


@dataclass(frozen=True)
class TelemetrySnapshot:
    endpoint_id: str
    request_count: int  # lifetime (monotonic)
    window_count: int  # samples inside the rolling window
    avg_latency_ms: float
    p95_latency_ms: float
    stddev_latency_ms: float
    success_rate: float  # over the rolling window; 0.0 with no samples
    error_rate: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "endpoint_id": self.endpoint_id,
            "request_count": self.request_count,
            "window_count": self.window_count,
            "avg_latency_ms": round(self.avg_latency_ms, 3),
            "p95_latency_ms": round(self.p95_latency_ms, 3),
            "stddev_latency_ms": round(self.stddev_latency_ms, 3),
            "success_rate": round(self.success_rate, 4),
            "error_rate": round(self.error_rate, 4),
        }


class _EndpointStats:
    __slots__ = ("window", "request_count", "dirty_count", "lifetime_success", "lifetime_error")

    def __init__(self, maxlen: int) -> None:
        self.window: deque = deque(maxlen=maxlen)
        self.request_count = 0
        self.dirty_count = 0
        self.lifetime_success = 0
        self.lifetime_error = 0


class TelemetryStore:
    """Rolling per-endpoint outcome statistics with durable persistence."""

    def __init__(self, history_window: int = 100, persist_every: int = PERSIST_EVERY) -> None:
        self._history_window = max(1, int(history_window))
        self._persist_every = max(1, int(persist_every))
        self._stats: dict[str, _EndpointStats] = {}
        self._lock = threading.Lock()
        self._table_ready = False

    # ------------------------------------------------------------------ api

    @property
    def history_window(self) -> int:
        return self._history_window

    def record(self, endpoint_id: str, latency_ms: float, success: bool) -> None:
        """Record one request outcome (real telemetry input for p2c/auto)."""
        if not endpoint_id:
            return
        with self._lock:
            stats = self._stats.get(endpoint_id)
            if stats is None:
                stats = _EndpointStats(self._history_window)
                self._stats[endpoint_id] = stats
            latency = float(latency_ms) if math.isfinite(latency_ms) and latency_ms >= 0 else 0.0
            stats.window.append((time.time(), latency, bool(success)))
            stats.request_count += 1
            if success:
                stats.lifetime_success += 1
            else:
                stats.lifetime_error += 1
            stats.dirty_count += 1
            should_persist = stats.dirty_count >= self._persist_every
        if should_persist:
            self.flush()

    def snapshot(self, endpoint_id: str) -> TelemetrySnapshot | None:
        with self._lock:
            stats = self._stats.get(endpoint_id)
            if stats is None:
                return None
            return self._snapshot_locked(endpoint_id, stats)

    def all_snapshots(self) -> list[TelemetrySnapshot]:
        with self._lock:
            return [
                self._snapshot_locked(endpoint_id, stats)
                for endpoint_id, stats in sorted(self._stats.items())
            ]

    def quality_scores(self) -> dict[str, float]:
        """Feedback quality signal for the auto strategy: window success rate
        for endpoints with observations; absent endpoints stay neutral (the
        scorer applies the 0.5 default — OmniRoute quality contract)."""
        result: dict[str, float] = {}
        for snap in self.all_snapshots():
            if snap.window_count > 0:
                result[snap.endpoint_id] = snap.success_rate
        return result

    # ------------------------------------------------------------ internals

    def _snapshot_locked(self, endpoint_id: str, stats: _EndpointStats) -> TelemetrySnapshot:
        samples = list(stats.window)
        latencies = [latency for _, latency, _ in samples]
        successes = sum(1 for _, _, ok in samples if ok)
        count = len(samples)
        if count:
            avg = sum(latencies) / count
            p95 = self._percentile(latencies, 0.95)
            variance = sum((x - avg) ** 2 for x in latencies) / count
            stddev = math.sqrt(variance)
            success_rate = successes / count
        else:
            avg = p95 = stddev = success_rate = 0.0
        return TelemetrySnapshot(
            endpoint_id=endpoint_id,
            request_count=stats.request_count,
            window_count=count,
            avg_latency_ms=avg,
            p95_latency_ms=p95,
            stddev_latency_ms=stddev,
            success_rate=success_rate,
            error_rate=(1.0 - success_rate) if count else 0.0,
        )

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        rank = pct * (len(ordered) - 1)
        lower = int(math.floor(rank))
        upper = min(lower + 1, len(ordered) - 1)
        fraction = rank - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction

    # ---------------------------------------------------------- persistence

    def _storage(self):
        from ..storage import get_storage

        return get_storage()

    def _ensure_table(self) -> bool:
        if self._table_ready:
            return True
        try:
            with self._storage().conn() as conn:
                conn.execute(_CREATE_TABLE_SQL)
                conn.commit()
            self._table_ready = True
            return True
        except Exception:
            logger.warning("routing_telemetry: storage unavailable, memory-only mode", exc_info=True)
            return False

    def flush(self) -> int:
        """Persist dirty endpoints; returns the number of rows written."""
        with self._lock:
            dirty = [
                (endpoint_id, stats)
                for endpoint_id, stats in self._stats.items()
                if stats.dirty_count > 0
            ]
            payload = []
            for endpoint_id, stats in dirty:
                samples = [
                    [round(ts, 3), round(latency, 3), ok] for ts, latency, ok in stats.window
                ][-MAX_PERSISTED_SAMPLES:]
                payload.append(
                    (
                        endpoint_id,
                        json.dumps(
                            {
                                "samples": samples,
                                "request_count": stats.request_count,
                                "lifetime_success": stats.lifetime_success,
                                "lifetime_error": stats.lifetime_error,
                            },
                            ensure_ascii=False,
                        ),
                        time.time(),
                    )
                )
                stats.dirty_count = 0
        if not payload:
            return 0
        if not self._ensure_table():
            with self._lock:
                for endpoint_id, _ in dirty:
                    stats = self._stats.get(endpoint_id)
                    if stats is not None:
                        stats.dirty_count = self._persist_every  # retry next flush
            return 0
        try:
            with self._storage().conn() as conn:
                conn.executemany(
                    "INSERT INTO routing_telemetry (endpoint_id, samples_json, updated_at) "
                    "VALUES (?, ?, ?) "
                    "ON CONFLICT(endpoint_id) DO UPDATE SET "
                    "samples_json = excluded.samples_json, updated_at = excluded.updated_at",
                    payload,
                )
                conn.commit()
            return len(payload)
        except Exception:
            logger.warning("routing_telemetry: flush failed", exc_info=True)
            return 0

    def load(self) -> int:
        """Restore rolling windows from storage; returns endpoints restored."""
        if not self._ensure_table():
            return 0
        try:
            with self._storage().conn() as conn:
                rows = conn.execute(
                    "SELECT endpoint_id, samples_json FROM routing_telemetry"
                ).fetchall()
        except Exception:
            logger.warning("routing_telemetry: load failed", exc_info=True)
            return 0
        restored = 0
        with self._lock:
            for row in rows:
                try:
                    data = json.loads(row["samples_json"])
                    samples = data.get("samples", [])
                    stats = _EndpointStats(self._history_window)
                    for sample in samples[-self._history_window :]:
                        ts, latency, ok = sample[0], float(sample[1]), bool(sample[2])
                        stats.window.append((ts, latency, ok))
                    stats.request_count = int(data.get("request_count", len(samples)))
                    stats.lifetime_success = int(data.get("lifetime_success", 0))
                    stats.lifetime_error = int(data.get("lifetime_error", 0))
                    stats.dirty_count = 0
                    self._stats[row["endpoint_id"]] = stats
                    restored += 1
                except Exception:
                    logger.warning(
                        "routing_telemetry: skipping corrupt row endpoint_id=%s",
                        row["endpoint_id"],
                        exc_info=True,
                    )
        return restored
