"""Latency-first strategy: select the fastest models based on benchmark data."""
from __future__ import annotations

from typing import Any

from .base import ModelCandidate, MoaStrategy

_PERF_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}


class LatencyFirstStrategy(MoaStrategy):
    """Select models with the lowest p95 latency.

    Filters:
      1. Exclude UNHEALTHY / DEAD endpoints.
      2. Sort by ``latency_p95`` ascending (unknown latency treated as +inf).
      3. Prefer PerformanceTier S/A as a tie-breaker.
      4. Return the top *n* endpoint IDs.
    """

    @property
    def name(self) -> str:
        return "latency_first"

    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        healthy = [
            c for c in candidates
            if c.health_status not in ("unhealthy", "dead")
        ]
        if not healthy:
            healthy = list(candidates)

        def sort_key(c: ModelCandidate):
            # latency_p95 == 0 means "no data" → push to back
            lat = c.latency_p95 if c.latency_p95 > 0 else float("inf")
            perf_rank = _PERF_RANK.get(c.perf_tier, 3)
            return (lat, perf_rank, -c.success_rate)

        healthy.sort(key=sort_key)
        return [c.endpoint_id for c in healthy[:n]]

    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        """Pick the first non-empty response (fastest model already first)."""
        for r in responses:
            if r and r.strip():
                return r
        return ""
