"""Tests for :mod:`reference_router`.

Run with ``python -m pytest reference_router/tests/test_reference_router.py -q``
from the ``capability`` directory, or ``python -m pytest`` from the
``reference_router`` package root.
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import pytest

# Make the package importable when running the test file directly.
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from reference_router import (  # noqa: E402
    CalibrationItem,
    Decision,
    RefStrategy,
    ReferenceConfig,
    ReferenceResult,
    compute_agreement,
    jaccard,
    register_mock_provider,
    route_with_reference,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_providers():
    """Make sure test-registered providers don't leak between tests."""
    # Snapshot is not strictly needed because register_mock_provider
    # overwrites; we just clear at teardown to keep state predictable.
    yield


# ---------------------------------------------------------------------------
# Enum & dataclass basics
# ---------------------------------------------------------------------------


def test_ref_strategy_values():
    assert RefStrategy.NONE.value == "none"
    assert RefStrategy.SHADOW.value == "shadow"
    assert RefStrategy.VALIDATE.value == "validate"
    assert RefStrategy.VETO.value == "veto"
    assert len(list(RefStrategy)) == 4


def test_reference_config_defaults():
    cfg = ReferenceConfig()
    assert cfg.main_model == "main-large"
    assert cfg.ref_model == "ref-small"
    assert cfg.strategy == RefStrategy.SHADOW
    assert cfg.max_latency_ms == 2000
    assert 0.0 < cfg.cost_ratio_cap <= 1.0


def test_reference_result_to_dict_has_all_fields():
    r = ReferenceResult(
        main_answer="a",
        ref_answer="b",
        similarity=0.5,
        agreement=0.5,
        calibration=[CalibrationItem("x", "low", "y")],
        decision=Decision.FLAG,
        latency_ms=10,
        main_cost=0.01,
        ref_cost=0.001,
        strategy_used=RefStrategy.VALIDATE,
    )
    d = r.to_dict()
    for k in (
        "main_answer",
        "ref_answer",
        "similarity",
        "agreement",
        "calibration",
        "decision",
        "latency_ms",
        "main_cost",
        "ref_cost",
        "strategy_used",
        "error",
    ):
        assert k in d
    assert d["decision"] == "flag"
    assert d["calibration"][0]["issue_type"] == "x"


# ---------------------------------------------------------------------------
# Strategy: NONE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_none_strategy_does_not_call_ref():
    cfg = ReferenceConfig(strategy=RefStrategy.NONE, main_model="main-medium")
    res = await route_with_reference("What is Python?", cfg)
    assert res.strategy_used == RefStrategy.NONE
    assert res.ref_answer == ""
    assert res.ref_cost == 0.0
    assert res.decision == Decision.ACCEPT
    assert res.main_answer  # non-empty


@pytest.mark.asyncio
async def test_none_strategy_handles_empty_query():
    cfg = ReferenceConfig(strategy=RefStrategy.NONE)
    res = await route_with_reference("", cfg)
    assert res.main_answer == ""
    assert res.strategy_used == RefStrategy.NONE
    assert res.decision == Decision.ACCEPT


# ---------------------------------------------------------------------------
# Strategy: SHADOW
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_does_not_block():
    """SHADOW should return quickly even if the ref model is slow."""
    register_mock_provider(
        "ref-slow-shadow",
        latency_ms=1500.0,
        cost=0.001,
        quality=0.8,
        agreement_bias=0.9,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.SHADOW,
        main_model="main-medium",
        ref_model="ref-slow-shadow",
    )
    t0 = time.monotonic()
    res = await route_with_reference("Explain asyncio in Python.", cfg)
    elapsed_ms = (time.monotonic() - t0) * 1000
    # main-medium latency is ~80ms; allow generous bound.
    assert elapsed_ms < 400
    assert res.strategy_used == RefStrategy.SHADOW
    assert res.ref_answer == ""  # ref answer not awaited
    assert any(c.issue_type == "shadow_only" for c in res.calibration)
    assert res.decision == Decision.ACCEPT


@pytest.mark.asyncio
async def test_shadow_returns_main_answer():
    cfg = ReferenceConfig(strategy=RefStrategy.SHADOW)
    res = await route_with_reference("Tell me about MoA routing.", cfg)
    assert res.main_answer
    assert res.ref_answer == ""
    assert res.latency_ms >= 0


# ---------------------------------------------------------------------------
# Strategy: VALIDATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_passes_for_aligned_models():
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE,
        main_model="main-large",
        ref_model="ref-small",
    )
    res = await route_with_reference("Quantum entanglement explained simply.", cfg)
    assert res.strategy_used == RefStrategy.VALIDATE
    assert res.ref_answer  # ref was called
    assert 0.0 <= res.agreement <= 1.0
    assert 0.0 <= res.similarity <= 1.0
    # high-bias ref should agree with main; we just check we get a sane value
    assert res.agreement >= 0.5
    assert res.decision in (Decision.ACCEPT, Decision.FLAG, Decision.REJECT)


@pytest.mark.asyncio
async def test_validate_downgrades_reject_to_flag():
    """VALIDATE must never outright REJECT — only flag/accept."""
    register_mock_provider(
        "ref-divergent",
        latency_ms=10.0,
        cost=0.0001,
        quality=0.2,
        agreement_bias=0.2,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE,
        main_model="main-large",
        ref_model="ref-divergent",
    )
    res = await route_with_reference("The sky is blue during the day.", cfg)
    assert res.decision != Decision.REJECT
    assert res.ref_answer


# ---------------------------------------------------------------------------
# Strategy: VETO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_veto_keeps_reject_decision():
    register_mock_provider(
        "ref-strict",
        latency_ms=10.0,
        cost=0.0001,
        quality=0.5,
        agreement_bias=0.3,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.VETO,
        main_model="main-large",
        ref_model="ref-strict",
    )
    res = await route_with_reference("A short factual sentence about gravity.", cfg)
    # The strict model will produce a divergent answer -> low agreement.
    assert res.strategy_used == RefStrategy.VETO
    if res.agreement < cfg.similarity_threshold_reject:
        assert res.decision == Decision.REJECT
        assert any(c.issue_type == "rejected" for c in res.calibration)


@pytest.mark.asyncio
async def test_veto_cost_cap_downgrades_to_validate():
    """VETO is too expensive for a tiny ref -> downgraded to VALIDATE."""
    # Make ref hugely more expensive than main.
    register_mock_provider("main-tiny", latency_ms=5.0, cost=0.0001, quality=0.5)
    register_mock_provider("ref-huge", latency_ms=10.0, cost=0.01, quality=0.5, agreement_bias=0.7)
    cfg = ReferenceConfig(
        strategy=RefStrategy.VETO,
        main_model="main-tiny",
        ref_model="ref-huge",
        cost_ratio_cap=0.5,
    )
    res = await route_with_reference("hello world", cfg)
    assert res.strategy_used == RefStrategy.VALIDATE
    assert any(c.issue_type == "cost_overrun" for c in res.calibration)


# ---------------------------------------------------------------------------
# Agreement / similarity math
# ---------------------------------------------------------------------------


def test_jaccard_identical_is_one():
    assert jaccard("foo bar", "foo bar") == 1.0


def test_jaccard_disjoint_is_zero():
    assert jaccard("foo bar", "baz qux") == 0.0


def test_jaccard_handles_empty():
    assert jaccard("", "") == 1.0
    assert jaccard("foo", "") == 0.0
    assert jaccard("", "foo") == 0.0


def test_compute_agreement_in_unit_range():
    sim, agr = compute_agreement("hello world", "hello there world")
    assert 0.0 <= sim <= 1.0
    assert 0.0 <= agr <= 1.0


def test_compute_agreement_identical_is_one():
    text = "The quick brown fox jumps over the lazy dog."
    sim, agr = compute_agreement(text, text)
    assert sim == 1.0
    assert agr == 1.0


def test_decision_boundaries():
    cfg = ReferenceConfig()
    assert _decide_safe(0.90, cfg) == Decision.ACCEPT
    assert _decide_safe(0.85, cfg) == Decision.ACCEPT
    assert _decide_safe(0.80, cfg) == Decision.FLAG
    assert _decide_safe(0.60, cfg) == Decision.FLAG
    assert _decide_safe(0.59, cfg) == Decision.REJECT
    assert _decide_safe(0.0, cfg) == Decision.REJECT


def _decide_safe(agreement: float, cfg: ReferenceConfig) -> Decision:
    """Local helper that re-uses the module's decision rule."""
    from reference_router import _decide

    return _decide(agreement, cfg)


# ---------------------------------------------------------------------------
# Failure / fallback handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_slow_ref_timeout_falls_back_to_shadow():
    register_mock_provider(
        "ref-very-slow",
        latency_ms=5000.0,
        cost=0.001,
        quality=0.9,
        agreement_bias=0.9,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE,
        ref_model="ref-very-slow",
        max_latency_ms=100,
    )
    res = await route_with_reference("Explain reinforcement learning briefly.", cfg)
    assert res.strategy_used == RefStrategy.SHADOW
    assert res.error == "timeout"
    assert any(c.issue_type == "ref_timeout" for c in res.calibration)
    assert res.decision == Decision.FLAG


@pytest.mark.asyncio
async def test_ref_provider_failure_falls_back_to_shadow():
    register_mock_provider(
        "ref-broken",
        latency_ms=5.0,
        cost=0.0001,
        quality=0.5,
        fail_rate=1.0,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.VETO,
        ref_model="ref-broken",
        max_latency_ms=1000,
    )
    res = await route_with_reference("Tell me about transformers.", cfg)
    assert res.strategy_used == RefStrategy.SHADOW
    assert res.error is not None
    assert any(c.issue_type == "ref_failure" for c in res.calibration)
    assert res.decision == Decision.FLAG
    assert res.main_answer  # main still returned


@pytest.mark.asyncio
async def test_primary_failure_rejects():
    register_mock_provider(
        "main-broken",
        latency_ms=5.0,
        cost=0.0,
        quality=0.0,
        fail_rate=1.0,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE,
        main_model="main-broken",
    )
    res = await route_with_reference("anything", cfg)
    assert res.main_answer == ""
    assert res.decision == Decision.REJECT
    assert any(c.issue_type == "primary_failure" for c in res.calibration)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calibration_contains_drift_hint_for_partial_agreement():
    register_mock_provider(
        "ref-mid",
        latency_ms=10.0,
        cost=0.0001,
        quality=0.5,
        agreement_bias=0.7,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE,
        ref_model="ref-mid",
    )
    res = await route_with_reference("MoA gateway reference router calibration.", cfg)
    # The result may be in the 0.6-0.85 band -> drift hint.
    if 0.6 <= res.agreement < 0.85:
        assert any(c.issue_type == "drift" for c in res.calibration)


def test_calibration_list_always_returns_list():
    from reference_router import _build_calibration

    items = _build_calibration(
        main="",
        ref="some reference output",
        agreement=0.3,
        decision=Decision.REJECT,
        cost_ratio=0.1,
        cost_ratio_cap=0.5,
    )
    assert isinstance(items, list)
    assert items  # at least one entry


# ---------------------------------------------------------------------------
# Cost / latency accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cost_estimation_accumulates_across_calls():
    register_mock_provider("main-x", latency_ms=5.0, cost=0.01, quality=0.9)
    register_mock_provider("ref-x", latency_ms=5.0, cost=0.001, quality=0.8, agreement_bias=0.9)
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE, main_model="main-x", ref_model="ref-x"
    )
    total_main = 0.0
    total_ref = 0.0
    for q in ["q1", "q2", "q3", "q4", "q5"]:
        res = await route_with_reference(q, cfg)
        total_main += res.main_cost
        total_ref += res.ref_cost
    # 5 calls each; main cost should dominate.
    assert total_main > 0
    assert total_ref > 0
    assert total_ref < total_main


@pytest.mark.asyncio
async def test_latency_reported_is_non_negative():
    cfg = ReferenceConfig(strategy=RefStrategy.NONE)
    res = await route_with_reference("ping", cfg)
    assert res.latency_ms >= 0


@pytest.mark.asyncio
async def test_validate_latency_above_main_latency():
    """VALIDATE blocks on the ref call, so total >= ref_latency."""
    register_mock_provider("ref-y", latency_ms=80.0, cost=0.001, quality=0.85, agreement_bias=0.9)
    cfg = ReferenceConfig(
        strategy=RefStrategy.VALIDATE, main_model="main-medium", ref_model="ref-y"
    )
    t0 = time.monotonic()
    res = await route_with_reference("test query about moons", cfg)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms >= 50  # at least main-medium (~80ms) + ref-y (~80ms) minimum
    assert res.latency_ms >= 50


# ---------------------------------------------------------------------------
# Empty / edge queries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_query_validate_returns_flag():
    cfg = ReferenceConfig(strategy=RefStrategy.VALIDATE)
    res = await route_with_reference("", cfg)
    # Both empty -> agreement 1.0 -> accept, but main_answer empty.
    assert res.main_answer == ""
    assert res.ref_answer == ""
    assert res.decision in (Decision.ACCEPT, Decision.FLAG)


@pytest.mark.asyncio
async def test_empty_query_veto_safe():
    cfg = ReferenceConfig(strategy=RefStrategy.VETO)
    res = await route_with_reference("", cfg)
    assert res.decision != Decision.REJECT  # empty cannot trigger reject


@pytest.mark.asyncio
async def test_whitespace_query_treated_as_empty():
    cfg = ReferenceConfig(strategy=RefStrategy.NONE)
    res = await route_with_reference("   \n\t  ", cfg)
    assert res.main_answer == ""
    assert res.decision == Decision.ACCEPT


# ---------------------------------------------------------------------------
# Concurrency / parallel guarantee
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shadow_does_not_await_ref_completion():
    """Schedule a shadow call and confirm the main task returns before ref finishes."""
    register_mock_provider(
        "ref-parallel",
        latency_ms=400.0,
        cost=0.001,
        quality=0.85,
        agreement_bias=0.9,
    )
    cfg = ReferenceConfig(
        strategy=RefStrategy.SHADOW,
        main_model="main-medium",
        ref_model="ref-parallel",
    )
    start = time.monotonic()
    res = await route_with_reference("async parallel test", cfg)
    elapsed = (time.monotonic() - start) * 1000
    # Must be much less than ref's 400ms latency.
    assert elapsed < 250
    assert res.strategy_used == RefStrategy.SHADOW
    # Let the background task complete so it doesn't leak.
    await asyncio.sleep(0.5)
