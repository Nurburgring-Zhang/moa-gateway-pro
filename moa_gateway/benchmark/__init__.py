"""moa_gateway.benchmark — Performance tier benchmarking and capability probing.

Provides:
- BenchmarkEngine: periodically sends standard requests to measure latency/success rate
- CapabilityProbe: discovers actual model capabilities (code, reasoning, json, etc.)
- MetricsStore: JSON-based persistence for benchmark and capability results

Singleton management follows the same pattern as moa_gateway.health.
"""
from __future__ import annotations

from typing import Any

from .benchmark_engine import BenchmarkEngine, BenchmarkResult, PerformanceMetrics, PerformanceTier
from .capability_probe import Capability, CapabilityProbe, CapabilityResult
from .metrics_store import MetricsStore

# Re-export legacy benchmark suite (BENCHMARK_PROMPTS, run_benchmark, run_pareto)
from .suite import BENCHMARK_PROMPTS, run_benchmark, run_pareto

__all__ = [
    "BenchmarkEngine",
    "BenchmarkResult",
    "PerformanceMetrics",
    "PerformanceTier",
    "Capability",
    "CapabilityProbe",
    "CapabilityResult",
    "MetricsStore",
    "get_benchmark_engine",
    "get_capability_probe",
    "get_metrics_store",
    "init_benchmark_system",
    "shutdown_benchmark_system",
    # Legacy benchmark suite
    "BENCHMARK_PROMPTS",
    "run_benchmark",
    "run_pareto",
]

# ========== Singleton Management ==========
_benchmark_engine: BenchmarkEngine | None = None
_capability_probe: CapabilityProbe | None = None
_metrics_store: MetricsStore | None = None


def get_metrics_store() -> MetricsStore:
    """Get the singleton MetricsStore instance."""
    global _metrics_store
    if _metrics_store is None:
        _metrics_store = MetricsStore()
    return _metrics_store


def get_benchmark_engine() -> BenchmarkEngine | None:
    """Get the singleton BenchmarkEngine instance (or None if not initialized)."""
    return _benchmark_engine


def get_capability_probe() -> CapabilityProbe | None:
    """Get the singleton CapabilityProbe instance (or None if not initialized)."""
    return _capability_probe


def init_benchmark_system(
    model_pool: Any | None = None,
    health_checker: Any | None = None,
    settings: Any | None = None,
) -> tuple[BenchmarkEngine, CapabilityProbe]:
    """Initialize the benchmark system. Called during server startup.

    Returns (benchmark_engine, capability_probe) singletons.
    """
    global _benchmark_engine, _capability_probe, _metrics_store

    from ..config import get_settings
    settings = settings or get_settings()

    bench_cfg = getattr(settings, "benchmark", None)
    max_concurrent = getattr(bench_cfg, "max_concurrent", 5) if bench_cfg else 5
    probe_timeout = getattr(bench_cfg, "probe_timeout", 30) if bench_cfg else 30
    interval_seconds = getattr(bench_cfg, "interval_seconds", 3600) if bench_cfg else 3600

    _metrics_store = MetricsStore()

    _benchmark_engine = BenchmarkEngine(
        health_checker=health_checker,
        model_pool=model_pool,
        metrics_store=_metrics_store,
        max_concurrent=max_concurrent,
        probe_timeout=probe_timeout,
        interval_seconds=interval_seconds,
    )

    _capability_probe = CapabilityProbe(
        model_pool=model_pool,
        health_checker=health_checker,
        metrics_store=_metrics_store,
        max_concurrent=max_concurrent,
        probe_timeout=probe_timeout,
    )

    return _benchmark_engine, _capability_probe


async def shutdown_benchmark_system() -> None:
    """Shutdown the benchmark system. Called during server shutdown."""
    global _benchmark_engine, _capability_probe, _metrics_store

    if _benchmark_engine is not None:
        await _benchmark_engine.stop()
    if _capability_probe is not None:
        await _capability_probe.stop()

    _benchmark_engine = None
    _capability_probe = None
    _metrics_store = None
