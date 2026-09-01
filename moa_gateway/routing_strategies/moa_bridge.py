"""MoA bridge: registers the ``routing_fusion`` strategy with moa_strategies.

Integration point required by the v4.1.0 contract: the routing-strategy
engine exposes OmniRoute's *fusion* behaviour to the MoA orchestration layer
as a standard :class:`MoaStrategy`. Registration is IDEMPOTENT — importing
this package twice (or re-importing after tests reset registries) never
duplicates or shadows entries.

Semantics
---------
- ``select_models``: filters healthy candidates, converts them to routing
  candidates and runs the engine's ``fusion`` strategy (fan-out panel order).
  The first *n* panel members are returned for MoA orchestration.
- ``aggregate``: fusion's judge synthesis without an extra model call — the
  distinct non-empty panel responses are merged in panel order.
- Opt-in rule: when ``settings.routing_strategies.enabled`` is False (or the
  engine errors), selection falls back to a plain weight-ordered healthy
  list, so the MoA layer keeps working exactly as before the integration.
"""

from __future__ import annotations

import logging
from typing import Any

from ..moa_strategies.base import (
    STRATEGY_REGISTRY,
    ModelCandidate,
    MoaStrategy,
    get_strategy,
    register_strategy,
)
from .models import EndpointCandidate, RoutingContext

logger = logging.getLogger(__name__)

STRATEGY_NAME = "routing_fusion"

_HEALTH_TO_BREAKER = {
    "healthy": "CLOSED",
    "degraded": "HALF_OPEN",
    "unknown": "CLOSED",
    "unhealthy": "HALF_OPEN",
    "dead": "OPEN",
}


def endpoint_from_model_candidate(candidate: ModelCandidate) -> EndpointCandidate:
    """Bridge MoA runtime metadata into the routing candidate contract."""
    return EndpointCandidate(
        endpoint_id=candidate.endpoint_id,
        provider=candidate.platform_id,
        model_id=candidate.model_id,
        weight=float(candidate.weight or 0),
        latency_p95_ms=max(0.0, candidate.latency_p95 or 0.0),
        success_rate=max(0.0, min(1.0, candidate.success_rate or 0.0)),
        request_count=1 if (candidate.success_rate or 0.0) > 0 else 0,
        cost_per_1k_input=max(0.0, candidate.cost_per_1k_input or 0.0),
        cost_per_1k_output=max(0.0, candidate.cost_per_1k_output or 0.0),
        breaker_state=_HEALTH_TO_BREAKER.get(candidate.health_status, "CLOSED"),
        enabled=True,
        tags=list(candidate.tags),
    )


class RoutingFusionStrategy(MoaStrategy):
    """OmniRoute fusion exposed to MoA orchestration."""

    @property
    def name(self) -> str:
        return STRATEGY_NAME

    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        if not candidates:
            return []
        healthy = [c for c in candidates if c.is_healthy] or list(candidates)

        try:
            from .engine import get_engine

            engine = get_engine()
            routing_candidates = [endpoint_from_model_candidate(c) for c in healthy]
            ctx = RoutingContext(
                group_key=str((context or {}).get("group_key", "moa")),
                session_key=(context or {}).get("session_key"),
                task_type=str((context or {}).get("task_type", "default")),
            )
            decision, _release = engine.resolve(routing_candidates, strategy="fusion", context=ctx)
            ordered = decision.ordered
            if ordered:
                return ordered[: max(1, n)]
        except Exception:
            # Opt-in semantics: engine disabled / unavailable → pre-integration
            # behaviour (weight-ordered healthy candidates).
            logger.debug("routing_fusion fell back to weight ordering", exc_info=True)

        fallback = sorted(healthy, key=lambda c: (-(c.weight or 0), c.endpoint_id))
        return [c.endpoint_id for c in fallback[: max(1, n)]]

    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        """Judge-style synthesis: merge distinct panel answers in panel order."""
        valid: list[str] = []
        seen: set[str] = set()
        for response in responses:
            if not response:
                continue
            text = response.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            valid.append(text)
        if not valid:
            return ""
        if len(valid) == 1:
            return valid[0]
        return "\n\n---\n\n".join(valid)


def register_routing_fusion() -> bool:
    """Idempotent registration into the MoA strategy registry.

    Returns True when this call performed the registration, False when the
    strategy was already present.
    """
    if get_strategy(STRATEGY_NAME) is not None:
        return False
    register_strategy(RoutingFusionStrategy())
    logger.info("registered MoA strategy '%s' (OmniRoute fusion bridge)", STRATEGY_NAME)
    return STRATEGY_NAME in STRATEGY_REGISTRY
