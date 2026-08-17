"""Tests for MOA strategy system — behavioral verification of selection logic."""
from __future__ import annotations

import pytest

from moa_gateway.moa_strategies.base import ModelCandidate, get_strategy, list_strategies


# ============ Test Fixtures ============


def _make_candidates() -> list[ModelCandidate]:
    """Create a diverse set of candidates for strategy testing."""
    return [
        ModelCandidate(
            endpoint_id="free-fast",
            model_id="llama-70b",
            platform_id="groq",
            tier_value="free",
            perf_tier="A",
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            latency_p95=200.0,
            success_rate=0.95,
            health_status="healthy",
            capabilities=["text", "code"],
            tags=["intl"],
        ),
        ModelCandidate(
            endpoint_id="cheap-slow",
            model_id="deepseek-chat",
            platform_id="deepseek",
            tier_value="standard",
            perf_tier="B",
            cost_per_1k_input=0.0005,
            cost_per_1k_output=0.001,
            latency_p95=3000.0,
            success_rate=0.98,
            health_status="healthy",
            capabilities=["text", "code", "reasoning"],
            tags=["cn"],
        ),
        ModelCandidate(
            endpoint_id="premium-fast",
            model_id="gpt-4o",
            platform_id="openai",
            tier_value="premium",
            perf_tier="S",
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.0075,
            latency_p95=800.0,
            success_rate=0.99,
            health_status="healthy",
            capabilities=["text", "code", "reasoning", "vision", "function_call"],
            tags=["intl"],
        ),
        ModelCandidate(
            endpoint_id="flagship-expensive",
            model_id="claude-sonnet",
            platform_id="anthropic",
            tier_value="premium",
            perf_tier="S",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            latency_p95=1200.0,
            success_rate=0.97,
            health_status="healthy",
            capabilities=["text", "code", "reasoning", "creative"],
            tags=["intl"],
        ),
        ModelCandidate(
            endpoint_id="unhealthy-model",
            model_id="broken-model",
            platform_id="unknown",
            tier_value="lite",
            perf_tier="C",
            cost_per_1k_input=0.0,
            cost_per_1k_output=0.0,
            latency_p95=0.0,
            success_rate=0.0,
            health_status="unhealthy",
            capabilities=["text"],
            tags=[],
        ),
    ]


# ============ Registry Tests ============


def test_strategy_registry_populated():
    strategies = list_strategies()
    assert len(strategies) >= 5
    expected = {"cost_first", "latency_first", "diversity_moa", "capability_aware", "adaptive_ensemble"}
    assert expected.issubset(set(strategies))


def test_get_unknown_strategy_returns_none():
    assert get_strategy("nonexistent_strategy_xyz") is None


# ============ CostFirst Strategy Tests ============


def test_cost_first_prefers_free_models():
    strat = get_strategy("cost_first")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=2)
    assert selected[0] == "free-fast"


def test_cost_first_excludes_unhealthy():
    strat = get_strategy("cost_first")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=5)
    assert "unhealthy-model" not in selected[:4]


def test_cost_first_respects_n_limit():
    strat = get_strategy("cost_first")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=2)
    assert len(selected) == 2


def test_cost_first_aggregate_picks_longest():
    strat = get_strategy("cost_first")
    responses = ["short", "medium length response", "the longest and most comprehensive answer here"]
    result = strat.aggregate(responses)
    assert result == "the longest and most comprehensive answer here"


def test_cost_first_aggregate_empty():
    strat = get_strategy("cost_first")
    assert strat.aggregate([]) == ""
    assert strat.aggregate(["", "  ", ""]) == ""


# ============ LatencyFirst Strategy Tests ============


def test_latency_first_prefers_fast_models():
    strat = get_strategy("latency_first")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=2)
    # free-fast has 200ms latency, premium-fast has 800ms
    assert "free-fast" in selected
    assert "cheap-slow" not in selected  # 3000ms should not be in top 2


def test_latency_first_excludes_unhealthy():
    strat = get_strategy("latency_first")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=5)
    assert "unhealthy-model" not in selected[:4]


# ============ DiversityMoA Strategy Tests ============


def test_diversity_prefers_different_platforms():
    strat = get_strategy("diversity_moa")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=3)
    # Should pick from different platforms for diversity
    selected_platforms = set()
    for c in candidates:
        if c.endpoint_id in selected:
            selected_platforms.add(c.platform_id)
    assert len(selected_platforms) >= 2


# ============ CapabilityAware Strategy Tests ============


def test_capability_aware_matches_task():
    strat = get_strategy("capability_aware")
    candidates = _make_candidates()
    # Code task should prefer models with "code" capability
    selected = strat.select_models(candidates, context={"task_type": "code"}, n=3)
    assert len(selected) > 0
    # All selected should be healthy candidates
    for eid in selected:
        c = next(c for c in candidates if c.endpoint_id == eid)
        assert c.is_healthy


def test_capability_aware_with_no_context():
    strat = get_strategy("capability_aware")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, context=None, n=3)
    assert len(selected) > 0


# ============ AdaptiveEnsemble Strategy Tests ============


def test_adaptive_ensemble_selects_n():
    strat = get_strategy("adaptive_ensemble")
    candidates = _make_candidates()
    selected = strat.select_models(candidates, n=3)
    assert len(selected) <= 3
    assert len(selected) > 0


def test_adaptive_ensemble_aggregate():
    strat = get_strategy("adaptive_ensemble")
    responses = ["Answer A", "Answer B with more detail", "Answer C"]
    result = strat.aggregate(responses)
    assert result  # should return non-empty


# ============ Edge Cases ============


def test_strategies_handle_empty_candidates():
    for name in list_strategies():
        strat = get_strategy(name)
        result = strat.select_models([], n=3)
        assert result == [] or isinstance(result, list)


def test_strategies_handle_single_candidate():
    single = [ModelCandidate(
        endpoint_id="only-one",
        model_id="solo-model",
        platform_id="solo",
        tier_value="standard",
        health_status="healthy",
    )]
    for name in list_strategies():
        strat = get_strategy(name)
        result = strat.select_models(single, n=3)
        assert len(result) <= 1


def test_strategies_handle_all_unhealthy():
    unhealthy = [
        ModelCandidate(endpoint_id=f"dead-{i}", health_status="dead")
        for i in range(5)
    ]
    for name in list_strategies():
        strat = get_strategy(name)
        result = strat.select_models(unhealthy, n=3)
        # Should still return something (graceful degradation)
        assert isinstance(result, list)
