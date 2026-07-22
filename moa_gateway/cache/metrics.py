"""Cache metrics — hit/miss tracking and statistics."""
from __future__ import annotations

import time
from collections import defaultdict
from threading import Lock


class CacheMetrics:
    """Thread-safe cache hit/miss metrics collector."""

    def __init__(self):
        self._lock = Lock()
        self._hits: dict[str, int] = defaultdict(int)
        self._misses: int = 0
        self._start_time: float = time.time()
        self._total_latency_ms: float = 0.0
        self._lookup_count: int = 0

    def record_hit(self, layer: str) -> None:
        with self._lock:
            self._hits[layer] += 1

    def record_miss(self) -> None:
        with self._lock:
            self._misses += 1

    def record_lookup_latency(self, latency_ms: float) -> None:
        with self._lock:
            self._total_latency_ms += latency_ms
            self._lookup_count += 1

    @property
    def total_requests(self) -> int:
        with self._lock:
            return sum(self._hits.values()) + self._misses

    @property
    def total_hits(self) -> int:
        with self._lock:
            return sum(self._hits.values())

    @property
    def hit_rate(self) -> float:
        total = self.total_requests
        if total == 0:
            return 0.0
        return self.total_hits / total

    def get_stats(self) -> dict:
        with self._lock:
            total = sum(self._hits.values()) + self._misses
            hits = sum(self._hits.values())
            avg_latency = (
                self._total_latency_ms / self._lookup_count
                if self._lookup_count > 0
                else 0
            )
            return {
                "enabled": True,
                "total_requests": total,
                "total_hits": hits,
                "total_misses": self._misses,
                "hit_rate_pct": round(hits / total * 100, 2) if total > 0 else 0,
                "hits_by_layer": dict(self._hits),
                "avg_lookup_latency_ms": round(avg_latency, 3),
                "uptime_seconds": round(time.time() - self._start_time, 1),
            }

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()
            self._misses = 0
            self._total_latency_ms = 0.0
            self._lookup_count = 0
            self._start_time = time.time()
