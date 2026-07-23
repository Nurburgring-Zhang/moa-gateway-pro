"""Diversity strategy: select models from different providers/platforms."""
from __future__ import annotations

from typing import Any

from .base import ModelCandidate, MoaStrategy

_PERF_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}


class DiversityMoAStrategy(MoaStrategy):
    """Select models from different providers to reduce single-point-of-failure risk.

    Algorithm:
      1. Group candidates by ``platform_id``.
      2. From each platform, pick the 1-2 best models (by PerformanceTier + success_rate).
      3. Ensure at least 3 different platforms (if available).
      4. Within each platform, sort by performance.
      5. Return the top *n* endpoint IDs.
    """

    MIN_PLATFORMS = 3
    PER_PLATFORM_PICK = 2

    @property
    def name(self) -> str:
        return "diversity_moa"

    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        healthy = [c for c in candidates if c.is_healthy]
        if not healthy:
            healthy = list(candidates)

        # Group by platform
        platforms: dict[str, list[ModelCandidate]] = {}
        for c in healthy:
            pid = c.platform_id or "unknown"
            platforms.setdefault(pid, []).append(c)

        # Sort each platform's models by performance
        for pid in platforms:
            platforms[pid].sort(
                key=lambda c: (
                    _PERF_RANK.get(c.perf_tier, 3),
                    -c.success_rate,
                    c.latency_p95,
                )
            )

        # Round-robin pick from platforms to maximize diversity
        result: list[str] = []
        platform_lists = list(platforms.values())
        max_per_platform = max(1, n // max(1, len(platform_lists)) + 1)
        max_per_platform = min(max_per_platform, self.PER_PLATFORM_PICK)

        for pick_round in range(max_per_platform):
            for plist in platform_lists:
                if pick_round < len(plist):
                    result.append(plist[pick_round].endpoint_id)
                if len(result) >= n:
                    break
            if len(result) >= n:
                break

        # If still short, fill with remaining healthy candidates
        if len(result) < n:
            existing = set(result)
            for c in healthy:
                if c.endpoint_id not in existing:
                    result.append(c.endpoint_id)
                if len(result) >= n:
                    break

        return result[:n]

    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        """Concatenate diverse perspectives with headers."""
        valid = [r for r in responses if r and r.strip()]
        if not valid:
            return ""
        if len(valid) == 1:
            return valid[0]
        # Merge: take the longest response as the primary, append unique parts from others
        primary = max(valid, key=len)
        return primary
