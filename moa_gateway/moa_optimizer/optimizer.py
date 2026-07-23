"""MOA automatic optimiser — continuously finds the best model combination."""
from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .ab_tester import ABTester

logger = logging.getLogger(__name__)


@dataclass
class OptimizationResult:
    """Result of one optimisation round."""
    timestamp: datetime
    best_strategy: str
    best_model_combination: list[str]
    quality_score: float
    avg_latency_ms: float
    cost_score: float        # lower = cheaper
    experiments_run: int
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "best_strategy": self.best_strategy,
            "best_model_combination": self.best_model_combination,
            "quality_score": round(self.quality_score, 4),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "cost_score": round(self.cost_score, 4),
            "experiments_run": self.experiments_run,
            "recommendation": self.recommendation,
        }


class MoaOptimizer:
    """MOA automatic optimiser.

    Periodically runs A/B tests across all registered strategies to find
    the best-performing model combination for the current set of endpoints.

    Uses a simple Thompson-sampling (Beta distribution) approach to balance
    exploration vs. exploitation.
    """

    MAX_EXPERIMENTS = 50
    CONVERGENCE_THRESHOLD = 0.95

    def __init__(
        self,
        benchmark_engine: Any | None = None,
        capability_probe: Any | None = None,
        health_checker: Any | None = None,
        model_pool: Any | None = None,
        moa_orchestrator: Any | None = None,
    ) -> None:
        self._benchmark = benchmark_engine
        self._capability = capability_probe
        self._health = health_checker
        self._model_pool = model_pool
        self._moa = moa_orchestrator
        self._ab_tester = ABTester()
        self._history: list[OptimizationResult] = []

        # Bayesian priors: strategy_name → (alpha, beta)
        self._beta_priors: dict[str, tuple[float, float]] = {}

        # Last optimisation result
        self._last_result: OptimizationResult | None = None

    # ============================================================
    # Test case generation
    # ============================================================
    def _generate_test_cases(self) -> list[dict[str, Any]]:
        """Generate a standard set of test cases covering common task types."""
        return [
            {
                "task_type": "code_generation",
                "messages": [{"role": "user", "content": "Write a Python function that returns the nth Fibonacci number."}],
                "expected_keywords": ["def", "fibonacci", "return"],
            },
            {
                "task_type": "creative_writing",
                "messages": [{"role": "user", "content": "Write a short poem about artificial intelligence."}],
                "expected_keywords": ["ai", "intelligence"],
            },
            {
                "task_type": "reasoning",
                "messages": [{"role": "user", "content": "If all cats are animals, and Whiskers is a cat, what can we conclude?"}],
                "expected_keywords": ["whiskers", "animal"],
            },
            {
                "task_type": "data_analysis",
                "messages": [{"role": "user", "content": "Summarize the key principles of data privacy in JSON format."}],
                "expected_keywords": ["privacy", "json"],
            },
            {
                "task_type": "multilingual",
                "messages": [{"role": "user", "content": "Translate 'Hello world' to French, Spanish, and Japanese."}],
                "expected_keywords": ["bonjour", "hola", "konnichiwa"],
            },
        ]

    # ============================================================
    # Quality evaluator
    # ============================================================
    def _simple_evaluator(self, response: str, expected_keywords: list[str] | None = None) -> float:
        """Simple response quality evaluator (0.0-1.0)."""
        if not response or not response.strip():
            return 0.0
        score = 0.0
        # Length-based scoring
        if len(response) > 50:
            score += 0.3
        if len(response) > 200:
            score += 0.2
        # Keyword coverage
        if expected_keywords:
            matched = sum(1 for kw in expected_keywords if kw.lower() in response.lower())
            score += 0.5 * (matched / len(expected_keywords))
        else:
            score += 0.5
        return min(score, 1.0)

    # ============================================================
    # Bayesian update
    # ============================================================
    def _bayesian_update(self, strategy_name: str, reward: float) -> None:
        """Update the Beta(α, β) prior for a strategy.

        reward ∈ [0, 1] — higher is better.
        α += reward, β += (1 - reward).
        Expected value = α / (α + β).
        """
        alpha, beta = self._beta_priors.get(strategy_name, (1.0, 1.0))
        alpha += reward
        beta += max(0.0, 1.0 - reward)
        self._beta_priors[strategy_name] = (alpha, beta)

    def _bayesian_expected(self, strategy_name: str) -> float:
        alpha, beta = self._beta_priors.get(strategy_name, (1.0, 1.0))
        return alpha / (alpha + beta)

    def _thompson_sample(self, strategy_name: str) -> float:
        """Sample from Beta(α, β) for Thompson sampling."""
        alpha, beta = self._beta_priors.get(strategy_name, (1.0, 1.0))
        # Use stdlib random.betavariate (avoids numpy dependency)
        import random
        try:
            return random.betavariate(alpha, beta)
        except Exception:
            return alpha / (alpha + beta)

    # ============================================================
    # Main optimisation loop
    # ============================================================
    async def optimize(self) -> OptimizationResult:
        """Execute one round of optimisation search."""
        from ..moa_strategies import list_strategies, get_strategy, build_candidates

        strategies = list_strategies()
        if not strategies:
            return OptimizationResult(
                timestamp=datetime.now(),
                best_strategy="parallel",
                best_model_combination=[],
                quality_score=0.0,
                avg_latency_ms=0.0,
                cost_score=0.0,
                experiments_run=0,
                recommendation="No strategies registered, using default parallel.",
            )

        # Build candidates from available subsystems
        candidates = build_candidates(
            model_pool=self._model_pool,
            benchmark_engine=self._benchmark,
            capability_probe=self._capability,
            health_checker=self._health,
        )

        if not candidates:
            return OptimizationResult(
                timestamp=datetime.now(),
                best_strategy="parallel",
                best_model_combination=[],
                quality_score=0.0,
                avg_latency_ms=0.0,
                cost_score=0.0,
                experiments_run=0,
                recommendation="No model candidates available.",
            )

        test_cases = self._generate_test_cases()
        # Use a subset of test cases to keep optimisation fast
        test_cases = test_cases[:3]

        # Get MOA orchestrator
        if self._moa is None:
            try:
                from ..moa import get_moa
                self._moa = get_moa()
            except Exception as e:
                logger.error("Cannot get MoA orchestrator: %s", e)
                return OptimizationResult(
                    timestamp=datetime.now(),
                    best_strategy="parallel",
                    best_model_combination=[c.endpoint_id for c in candidates[:3]],
                    quality_score=0.0,
                    avg_latency_ms=0.0,
                    cost_score=0.0,
                    experiments_run=0,
                    recommendation=f"MoA orchestrator unavailable: {e}",
                )

        # Thompson-sampling: select the two most promising strategies to A/B test
        samples = {s: self._thompson_sample(s) for s in strategies}
        sorted_strats = sorted(samples.items(), key=lambda x: -x[1])
        strat_a = sorted_strats[0][0]
        strat_b = sorted_strats[1][0] if len(sorted_strats) > 1 else "parallel"

        # Run A/B test
        exp = await self._ab_tester.run_experiment(
            name=f"opt_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            strategy_a=strat_a,
            strategy_b=strat_b,
            test_cases=test_cases,
            evaluator=self._simple_evaluator,
            moa_orchestrator=self._moa,
        )

        # Bayesian update
        self._bayesian_update(strat_a, exp.avg_score_a)
        self._bayesian_update(strat_b, exp.avg_score_b)

        # Also update the other strategies with neutral priors
        for s in strategies:
            if s not in (strat_a, strat_b):
                self._bayesian_update(s, 0.5)

        # Determine best strategy
        if exp.winner == strat_a:
            best_strat = strat_a
            best_score = exp.avg_score_a
            best_latency = exp.avg_latency_a
        else:
            best_strat = strat_b
            best_score = exp.avg_score_b
            best_latency = exp.avg_latency_b

        # Get the model combination the best strategy would select
        strat_obj = get_strategy(best_strat)
        if strat_obj:
            best_models = strat_obj.select_models(candidates, n=3)
        else:
            best_models = [c.endpoint_id for c in candidates[:3]]

        # Compute cost score (lower = cheaper) — average total_cost_per_1k of selected
        selected_costs = [
            c.total_cost_per_1k for c in candidates
            if c.endpoint_id in best_models
        ]
        cost_score = sum(selected_costs) / max(1, len(selected_costs))

        # Build recommendation text
        recommendation = self._build_recommendation(
            best_strat, best_score, best_latency, cost_score, len(candidates)
        )

        result = OptimizationResult(
            timestamp=datetime.now(),
            best_strategy=best_strat,
            best_model_combination=best_models,
            quality_score=best_score,
            avg_latency_ms=best_latency,
            cost_score=cost_score,
            experiments_run=1,
            recommendation=recommendation,
        )

        self._last_result = result
        logger.info(
            "Optimisation complete: best=%s score=%.3f latency=%.0fms cost=%.4f",
            best_strat, best_score, best_latency, cost_score,
        )
        return result

    def _build_recommendation(
        self, strategy: str, score: float, latency: float, cost: float, n_candidates: int
    ) -> str:
        """Generate a human-readable recommendation string."""
        parts = [f"Best strategy: {strategy}"]
        if score >= 0.8:
            parts.append(f"quality is excellent ({score:.1%})")
        elif score >= 0.5:
            parts.append(f"quality is moderate ({score:.1%})")
        else:
            parts.append(f"quality needs improvement ({score:.1%})")
        if latency > 0:
            parts.append(f"avg latency {latency:.0f}ms")
        if cost == 0:
            parts.append("using free models only")
        else:
            parts.append(f"cost ${cost:.4f}/1k tokens")
        parts.append(f"from {n_candidates} available models")
        return "; ".join(parts) + "."

    # ============================================================
    # Recommendation & history
    # ============================================================
    def get_recommendation(self) -> dict[str, Any]:
        """Get the current best combination recommendation."""
        if self._last_result is None:
            return {
                "strategy": "parallel",
                "models": [],
                "quality_score": 0.0,
                "avg_latency_ms": 0.0,
                "recommendation": "No optimisation run yet. Default: parallel strategy.",
            }
        r = self._last_result
        return {
            "strategy": r.best_strategy,
            "models": r.best_model_combination,
            "quality_score": r.quality_score,
            "avg_latency_ms": r.avg_latency_ms,
            "cost_score": r.cost_score,
            "recommendation": r.recommendation,
        }

    def get_history(self, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent optimisation results."""
        return [r.to_dict() for r in self._history[-limit:]]

    def get_experiments(self) -> list[dict[str, Any]]:
        """Return A/B test experiment results."""
        return self._ab_tester.list_experiments()

    # ============================================================
    # Daily optimisation
    # ============================================================
    async def run_daily_optimization(self) -> OptimizationResult:
        """Run one optimisation round (called by scheduler or manually)."""
        try:
            result = await self.optimize()
            self._history.append(result)
            # Keep last 30 results
            self._history = self._history[-30:]
            return result
        except Exception as e:
            logger.error("Daily optimisation failed: %s", e, exc_info=True)
            return OptimizationResult(
                timestamp=datetime.now(),
                best_strategy="parallel",
                best_model_combination=[],
                quality_score=0.0,
                avg_latency_ms=0.0,
                cost_score=0.0,
                experiments_run=0,
                recommendation=f"Optimisation failed: {e}",
            )

    # ============================================================
    # Beta priors introspection
    # ============================================================
    def get_strategy_stats(self) -> dict[str, dict[str, float]]:
        """Return Bayesian expected value and priors for each strategy."""
        stats = {}
        for name, (alpha, beta) in self._beta_priors.items():
            stats[name] = {
                "alpha": round(alpha, 2),
                "beta": round(beta, 2),
                "expected_value": round(alpha / (alpha + beta), 4),
            }
        return stats
