"""moa_gateway.moa_strategies — Pluggable MOA model-selection strategies.

Each strategy implements ``select_models`` (pick N endpoints from candidates)
and ``aggregate`` (combine multiple model responses into one).

Strategies are registered in ``STRATEGY_REGISTRY`` and can be looked up
by name via ``get_strategy(name)``.
"""
from __future__ import annotations

from .adaptive_ensemble import AdaptiveEnsembleStrategy
from .base import (
    STRATEGY_REGISTRY,
    MoaStrategy,
    ModelCandidate,
    build_candidates,
    get_strategy,
    list_strategies,
    register_strategy,
)
from .capability_aware import CapabilityAwareStrategy
from .cost_first import CostFirstStrategy
from .diversity_moa import DiversityMoAStrategy
from .latency_first import LatencyFirstStrategy

# --- auto-register built-in strategies ---
register_strategy(CostFirstStrategy())
register_strategy(LatencyFirstStrategy())
register_strategy(DiversityMoAStrategy())
register_strategy(CapabilityAwareStrategy())
register_strategy(AdaptiveEnsembleStrategy())

__all__ = [
    "ModelCandidate",
    "MoaStrategy",
    "STRATEGY_REGISTRY",
    "get_strategy",
    "list_strategies",
    "register_strategy",
    "build_candidates",
    "CostFirstStrategy",
    "LatencyFirstStrategy",
    "DiversityMoAStrategy",
    "CapabilityAwareStrategy",
    "AdaptiveEnsembleStrategy",
]
