"""A/B tester for comparing MOA strategies head-to-head."""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ExperimentResult:
    """Result of a single A/B experiment."""
    experiment_id: str
    name: str
    strategy_a: str
    strategy_b: str
    cases_total: int = 0
    cases_a_success: int = 0
    cases_b_success: int = 0
    avg_score_a: float = 0.0
    avg_score_b: float = 0.0
    avg_latency_a: float = 0.0
    avg_latency_b: float = 0.0
    winner: str = ""
    confidence: float = 0.0  # 0.0-1.0
    details_a: list[dict] = field(default_factory=list)
    details_b: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "name": self.name,
            "strategy_a": self.strategy_a,
            "strategy_b": self.strategy_b,
            "cases_total": self.cases_total,
            "cases_a_success": self.cases_a_success,
            "cases_b_success": self.cases_b_success,
            "avg_score_a": round(self.avg_score_a, 4),
            "avg_score_b": round(self.avg_score_b, 4),
            "avg_latency_a": round(self.avg_latency_a, 1),
            "avg_latency_b": round(self.avg_latency_b, 1),
            "winner": self.winner,
            "confidence": round(self.confidence, 4),
        }


class ABTester:
    """Run A/B tests between two MOA strategies on a set of test cases."""

    MAX_EXPERIMENTS = 500

    def __init__(self) -> None:
        # P2-9: Use OrderedDict with max size to bound experiment records
        self._experiments: OrderedDict[str, ExperimentResult] = OrderedDict()

    async def run_experiment(
        self,
        name: str,
        strategy_a: str,
        strategy_b: str,
        test_cases: list[dict[str, Any]],
        evaluator: Callable[..., float],
        moa_orchestrator: Any | None = None,
    ) -> ExperimentResult:
        """Run an A/B experiment.

        For each test case, runs both strategies through the MOA orchestrator
        and evaluates the output quality using *evaluator*.

        Parameters
        ----------
        name : str
            Human-readable experiment name.
        strategy_a, strategy_b : str
            Strategy names to compare.
        test_cases : list[dict]
            Each dict must have ``messages`` and optionally ``task_type``.
        evaluator : callable
            ``evaluator(response: str, expected_keywords: list[str] | None) -> float``
        moa_orchestrator : MoAOrchestrator | None
            The orchestrator to use. If ``None``, uses ``get_moa()``.
        """
        exp_id = "exp_" + uuid.uuid4().hex[:8]
        result = ExperimentResult(
            experiment_id=exp_id,
            name=name,
            strategy_a=strategy_a,
            strategy_b=strategy_b,
            cases_total=len(test_cases),
        )

        if moa_orchestrator is None:
            try:
                from ..moa import get_moa
                moa_orchestrator = get_moa()
            except Exception as e:
                logger.error("Cannot get MoA orchestrator: %s", e)
                result.winner = "error"
                return result

        scores_a: list[float] = []
        scores_b: list[float] = []
        latencies_a: list[float] = []
        latencies_b: list[float] = []

        for tc in test_cases:
            messages = tc.get("messages", [])
            task_type = tc.get("task_type", "")
            expected_kw = tc.get("expected_keywords")

            # Run strategy A
            try:
                t0 = time.time()
                res_a = await moa_orchestrator.execute(
                    query=messages[-1].get("content", "") if messages else "",
                    context=messages[:-1] if len(messages) > 1 else None,
                    strategy=strategy_a,
                )
                lat_a = (time.time() - t0) * 1000
                score_a = evaluator(res_a.final_content, expected_kw)
            except Exception as e:
                logger.warning("Strategy A failed on case: %s", e)
                res_a = None
                lat_a = 0.0
                score_a = 0.0
            scores_a.append(score_a)
            latencies_a.append(lat_a)
            result.details_a.append({
                "task_type": task_type,
                "score": round(score_a, 4),
                "latency_ms": round(lat_a, 1),
                "success": res_a is not None,
            })
            if res_a is not None:
                result.cases_a_success += 1

            # Run strategy B
            try:
                t0 = time.time()
                res_b = await moa_orchestrator.execute(
                    query=messages[-1].get("content", "") if messages else "",
                    context=messages[:-1] if len(messages) > 1 else None,
                    strategy=strategy_b,
                )
                lat_b = (time.time() - t0) * 1000
                score_b = evaluator(res_b.final_content, expected_kw)
            except Exception as e:
                logger.warning("Strategy B failed on case: %s", e)
                res_b = None
                lat_b = 0.0
                score_b = 0.0
            scores_b.append(score_b)
            latencies_b.append(lat_b)
            result.details_b.append({
                "task_type": task_type,
                "score": round(score_b, 4),
                "latency_ms": round(lat_b, 1),
                "success": res_b is not None,
            })
            if res_b is not None:
                result.cases_b_success += 1

        # Aggregate
        result.avg_score_a = sum(scores_a) / max(1, len(scores_a))
        result.avg_score_b = sum(scores_b) / max(1, len(scores_b))
        result.avg_latency_a = sum(latencies_a) / max(1, len(latencies_a))
        result.avg_latency_b = sum(latencies_b) / max(1, len(latencies_b))

        # Determine winner with simple confidence metric
        diff = result.avg_score_a - result.avg_score_b
        if abs(diff) < 0.02:
            # Too close to call — prefer lower latency
            if result.avg_latency_a <= result.avg_latency_b:
                result.winner = strategy_a
            else:
                result.winner = strategy_b
            result.confidence = 0.3
        else:
            result.winner = strategy_a if diff > 0 else strategy_b
            # Confidence scales with margin (max ~0.95)
            result.confidence = min(0.95, 0.5 + abs(diff) * 2.0)

        self._experiments[exp_id] = result
        # P2-9: Evict oldest experiments if exceeding max
        while len(self._experiments) > self.MAX_EXPERIMENTS:
            self._experiments.popitem(last=False)
        logger.info(
            "A/B experiment '%s' complete: winner=%s confidence=%.2f "
            "(A: score=%.3f lat=%.0fms | B: score=%.3f lat=%.0fms)",
            name, result.winner, result.confidence,
            result.avg_score_a, result.avg_latency_a,
            result.avg_score_b, result.avg_latency_b,
        )
        return result

    def get_experiment(self, exp_id: str) -> ExperimentResult | None:
        return self._experiments.get(exp_id)

    def list_experiments(self) -> list[dict[str, Any]]:
        return [r.to_dict() for r in self._experiments.values()]
