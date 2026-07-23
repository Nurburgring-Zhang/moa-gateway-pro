"""Adaptive ensemble strategy: dynamic weighting based on historical success."""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Any

from .base import ModelCandidate, MoaStrategy

logger = logging.getLogger(__name__)

_PERF_RANK = {"S": 0, "A": 1, "B": 2, "C": 3}


class AdaptiveEnsembleStrategy(MoaStrategy):
    """Dynamically weight model selection and response aggregation by historical performance.

    Maintains per-endpoint success history and quality scores.  Models that
    consistently produce good results get higher influence in both selection
    and aggregation.
    """

    HISTORY_SIZE = 50
    EMA_ALPHA = 0.3  # exponential moving average factor

    def __init__(self) -> None:
        self._model_weights: dict[str, float] = {}
        self._history: dict[str, deque] = {}   # endpoint_id -> success history (bool)
        self._quality_scores: dict[str, float] = {}  # endpoint_id -> avg quality

    @property
    def name(self) -> str:
        return "adaptive_ensemble"

    def _get_weight(self, endpoint_id: str) -> float:
        """Compute dynamic weight = base(1.0) * success_rate * recency_factor."""
        hist = self._history.get(endpoint_id)
        if not hist or len(hist) == 0:
            return 1.0
        # Success rate
        sr = sum(1 for h in hist if h) / len(hist)
        # Recency factor: more recent data matters more (exponential decay)
        recency = sum(
            (self.EMA_ALPHA ** i) * (1.0 if list(hist)[-(i + 1)] else 0.0)
            for i in range(min(len(hist), 10))
        ) / max(1, min(len(hist), 10))
        # Quality score boost
        qs = self._quality_scores.get(endpoint_id, 1.0)
        return sr * (0.5 + 0.5 * recency) * qs

    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        healthy = [c for c in candidates if c.is_healthy]
        if not healthy:
            healthy = list(candidates)

        # Compute combined score: dynamic weight * performance tier * success_rate
        def score(c: ModelCandidate) -> float:
            dw = self._get_weight(c.endpoint_id)
            perf_bonus = 1.0 / (1.0 + _PERF_RANK.get(c.perf_tier, 3))
            return dw * perf_bonus * max(c.success_rate, 0.01)

        healthy.sort(key=lambda c: -score(c))
        return [c.endpoint_id for c in healthy[:n]]

    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        """Weighted aggregation: responses from higher-weight models have more influence.

        Uses a weighted selection: if responses converge (high similarity),
        merge them; otherwise, pick the response from the highest-weighted model.
        """
        valid = [r for r in responses if r and r.strip()]
        if not valid:
            return ""
        if len(valid) == 1:
            return valid[0]

        # Use selected_ids to map responses to endpoint_ids for weight lookup.
        # selected_ids[i] corresponds to responses[i] (aligned by caller).
        if selected_ids and len(selected_ids) == len(responses):
            weighted = [
                (i, self._get_weight(selected_ids[i]))
                for i in range(len(responses))
                if responses[i] and responses[i].strip()
            ]
            if weighted:
                best_idx = max(weighted, key=lambda x: x[1])[0]
                return responses[best_idx]

        # Fallback: if candidates provided, use weights to pick the best response
        if candidates:
            weights = [self._get_weight(c.endpoint_id) for c in candidates[:len(valid)]]
            if weights:
                best_idx = max(range(len(weights)), key=lambda i: weights[i])
                if best_idx < len(valid):
                    return valid[best_idx]

        # Fallback: pick the longest response
        return max(valid, key=len)

    def update_weights(
        self,
        endpoint_id: str,
        success: bool,
        quality_score: float = 1.0,
    ) -> None:
        """Update the success history and quality score for an endpoint.

        Uses exponential moving average for quality and a bounded deque for
        success history.
        """
        if endpoint_id not in self._history:
            self._history[endpoint_id] = deque(maxlen=self.HISTORY_SIZE)
        self._history[endpoint_id].append(success)

        # EMA update for quality
        old_q = self._quality_scores.get(endpoint_id, 1.0)
        new_q = self.EMA_ALPHA * quality_score + (1 - self.EMA_ALPHA) * old_q
        self._quality_scores[endpoint_id] = max(0.0, min(new_q, 2.0))

        # Update weight cache
        self._model_weights[endpoint_id] = self._get_weight(endpoint_id)
        logger.debug(
            "AdaptiveEnsemble: updated %s success=%s quality=%.2f weight=%.3f",
            endpoint_id, success, quality_score, self._model_weights[endpoint_id],
        )
