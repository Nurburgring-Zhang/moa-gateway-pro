"""M1 — OmniRoute-style routing-strategy engine (v4.1.0 integration).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
the 19 public routing strategies + the internal quota-share selector, the
strategy registry/dispatch, the auto multi-factor scorer, prompt-cache
affinity (rendezvous hashing), reset-aware/reset-window/headroom quota math,
LKGP pinning, strict-random decks and rolling telemetry. See each module's
header for the exact upstream file attribution.

Public API
----------
- :class:`RoutingStrategyEngine` / :func:`get_engine` — the engine.
- :data:`STRATEGIES` — the 20 canonical strategy specs.
- :func:`normalize_strategy_name` — OmniRoute alias normalization.
- Models: ``EndpointCandidate``, ``RoutingContext``, ``RoutingDecision``.

Importing this package idempotently registers the ``routing_fusion`` MoA
strategy (contract integration point); it never overrides an existing
registration and never touches shared gateway modules at import time.
"""

from __future__ import annotations

from .engine import (
    RoutingDisabledError,
    RoutingStrategyEngine,
    UnknownStrategyError,
    get_engine,
    reset_engine_for_tests,
)
from .models import (
    EndpointCandidate,
    QuotaSnapshot,
    QuotaWindow,
    RoutingContext,
    RoutingDecision,
    Selection,
)
from .moa_bridge import RoutingFusionStrategy, register_routing_fusion
from .strategies import (
    STRATEGIES,
    STRATEGY_ALIASES,
    StrategySpec,
    normalize_strategy_name,
)
from .telemetry import TelemetrySnapshot, TelemetryStore

# Contract integration point: idempotent MoA registration on package import.
register_routing_fusion()

__all__ = [
    "EndpointCandidate",
    "QuotaSnapshot",
    "QuotaWindow",
    "RoutingContext",
    "RoutingDecision",
    "RoutingDisabledError",
    "RoutingFusionStrategy",
    "RoutingStrategyEngine",
    "STRATEGIES",
    "STRATEGY_ALIASES",
    "Selection",
    "StrategySpec",
    "TelemetrySnapshot",
    "TelemetryStore",
    "UnknownStrategyError",
    "get_engine",
    "normalize_strategy_name",
    "register_routing_fusion",
    "reset_engine_for_tests",
]
