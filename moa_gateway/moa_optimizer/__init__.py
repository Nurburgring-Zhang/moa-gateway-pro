"""moa_gateway.moa_optimizer — Automatic MOA strategy optimisation system.

Continuously A/B-tests different model-selection strategies and
model combinations to find the best configuration for the current
set of available endpoints.
"""
from .ab_tester import ABTester
from .optimizer import MoaOptimizer, OptimizationResult

__all__ = [
    "MoaOptimizer",
    "OptimizationResult",
    "ABTester",
]
