"""Tests for MOA strategy system."""
from __future__ import annotations

import pytest


def test_moa_imports():
    """Verify core MOA module can be imported."""
    from moa_gateway import moa


def test_strategy_registry_available():
    """Verify strategy registry is available and populated."""
    from moa_gateway.moa_strategies import STRATEGY_REGISTRY, list_strategies

    # Registry should have content
    strategies = list_strategies()
    assert len(strategies) > 0


def test_known_strategies_registered():
    """Verify known strategies are in the registry."""
    from moa_gateway.moa_strategies import get_strategy

    known = [
        "cost_first",
        "latency_first",
        "diversity_moa",
        "capability_aware",
        "adaptive_ensemble",
    ]
    for name in known:
        strat = get_strategy(name)
        assert strat is not None, f"Strategy '{name}' not found in registry"


def test_strategy_classes_imported():
    """Verify strategy classes are accessible."""
    from moa_gateway.moa_strategies import (
        CostFirstStrategy,
        LatencyFirstStrategy,
        DiversityMoAStrategy,
        CapabilityAwareStrategy,
        AdaptiveEnsembleStrategy,
    )

    assert CostFirstStrategy is not None
    assert LatencyFirstStrategy is not None
    assert DiversityMoAStrategy is not None
    assert CapabilityAwareStrategy is not None
    assert AdaptiveEnsembleStrategy is not None


def test_moa_orchestrator_class_exists():
    """Verify MoAOrchestrator class can be imported."""
    from moa_gateway.moa import MoAOrchestrator

    assert MoAOrchestrator is not None


def test_version_consistency():
    """Verify version is consistent across modules."""
    from moa_gateway import __version__

    assert __version__ == "1.9.0"
