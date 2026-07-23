"""Cost-first strategy: prefer free models, fall back to paid only when needed."""
from __future__ import annotations

from typing import Any

from .base import ModelCandidate, MoaStrategy

# Tier ranking for cost preference (lower = more preferred)
_TIER_COST_RANK = {
    "free": 0,
    "lite": 1,
    "standard": 2,
    "premium": 3,
    "flagship": 4,
}

# Performance tier ranking (lower index = better)
_PERF_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}


class CostFirstStrategy(MoaStrategy):
    """Select models prioritising zero-cost endpoints.

    Sort order:
      1. ``total_cost_per_1k == 0`` (free) before any paid model
      2. Within the same cost bucket, sort by ModelTier (free > lite > standard > premium)
      3. Within the same tier, sort by PerformanceTier (S > A > B > C)
      4. Return the top *n* endpoint IDs
    """

    @property
    def name(self) -> str:
        return "cost_first"

    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        healthy = [c for c in candidates if c.is_healthy]
        if not healthy:
            healthy = list(candidates)

        def sort_key(c: ModelCandidate):
            cost_rank = 0 if c.is_free else 1
            tier_rank = _TIER_COST_RANK.get(c.tier_value, 99)
            perf_rank = _PERF_RANK.get(c.perf_tier, 3)
            # Secondary: higher success rate, lower latency
            return (cost_rank, tier_rank, perf_rank, -c.success_rate, c.latency_p95)

        healthy.sort(key=sort_key)
        return [c.endpoint_id for c in healthy[:n]]

    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        """Simple majority-style aggregation: pick the longest non-empty response."""
        valid = [r for r in responses if r and r.strip()]
        if not valid:
            return ""
        if len(valid) == 1:
            return valid[0]
        # Pick the longest response as the most comprehensive
        return max(valid, key=len)
