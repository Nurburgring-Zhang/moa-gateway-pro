"""Capability-aware strategy: select models matching the task's required capabilities."""
from __future__ import annotations

from typing import Any

from .base import ModelCandidate, MoaStrategy

_PERF_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}

# Task type → required capability tags
TASK_CAPABILITY_MAP: dict[str, list[str]] = {
    "code_generation": ["code", "reasoning"],
    "creative_writing": ["creative", "text"],
    "data_analysis": ["json_mode", "reasoning"],
    "function_calling": ["function_call"],
    "multilingual": ["multilingual"],
    "reasoning": ["reasoning"],
    "translation": ["multilingual"],
    "summarization": ["text"],
    "qa": ["text", "reasoning"],
}


class CapabilityAwareStrategy(MoaStrategy):
    """Select models that possess the capabilities required by the task type.

    Falls back gracefully:
      1. Filter models that have ALL required capabilities.
      2. If too few, relax to models with at least ONE required capability.
      3. If still none, fall back to all healthy candidates.
    """

    @property
    def name(self) -> str:
        return "capability_aware"

    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        ctx = context or {}
        task_type = ctx.get("task_type", "")
        required_caps = TASK_CAPABILITY_MAP.get(task_type, [])

        healthy = [c for c in candidates if c.is_healthy]
        if not healthy:
            healthy = list(candidates)

        if not required_caps:
            # No specific capability requirement → sort by performance
            healthy.sort(key=lambda c: (_PERF_RANK.get(c.perf_tier, 3), -c.success_rate, c.latency_p95))
            return [c.endpoint_id for c in healthy[:n]]

        # Tier 1: models with ALL required capabilities
        full_match = [
            c for c in healthy
            if all(cap in c.capabilities for cap in required_caps)
        ]

        # Tier 2: models with at least ONE required capability
        partial_match = [
            c for c in healthy
            if any(cap in c.capabilities for cap in required_caps)
        ]

        # Build pool: full_match first, then partial, then fallback
        pool = list(full_match)
        if len(pool) < n:
            for c in partial_match:
                if c not in pool:
                    pool.append(c)
                if len(pool) >= n:
                    break
        if len(pool) < n:
            for c in healthy:
                if c not in pool:
                    pool.append(c)
                if len(pool) >= n:
                    break

        # Sort: full_match models get priority (rank 0), partial get rank 1, fallback rank 2
        match_rank = {}
        for c in full_match:
            match_rank[id(c)] = 0
        for c in pool:
            if id(c) not in match_rank:
                if c in partial_match:
                    match_rank[id(c)] = 1
                else:
                    match_rank[id(c)] = 2

        pool.sort(key=lambda c: (
            match_rank.get(id(c), 2),
            _PERF_RANK.get(c.perf_tier, 3),
            -c.success_rate,
            c.latency_p95,
        ))
        return [c.endpoint_id for c in pool[:n]]

    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        valid = [r for r in responses if r and r.strip()]
        if not valid:
            return ""
        if len(valid) == 1:
            return valid[0]
        return max(valid, key=len)
