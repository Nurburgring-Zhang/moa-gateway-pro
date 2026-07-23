"""Base classes for MOA strategies."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Forward-declared type aliases (avoid circular imports)
try:
    from ..benchmark.benchmark_engine import PerformanceTier
except Exception:  # pragma: no cover
    class PerformanceTier:  # type: ignore[no-redef]
        S = "S"; A = "A"; B = "B"; C = "C"

try:
    from ..benchmark.capability_probe import Capability
except Exception:  # pragma: no cover
    class Capability:  # type: ignore[no-redef]
        TEXT = "text"; CODE = "code"; REASONING = "reasoning"
        VISION = "vision"; FUNCTION_CALL = "function_call"
        JSON_MODE = "json_mode"; MULTILINGUAL = "multilingual"
        CREATIVE = "creative"; STREAMING = "streaming"

try:
    from ..health.health_checker import HealthStatus
except Exception:  # pragma: no cover
    class HealthStatus:  # type: ignore[no-redef]
        HEALTHY = "healthy"; DEGRADED = "degraded"
        UNHEALTHY = "unhealthy"; DEAD = "dead"


@dataclass
class ModelCandidate:
    """A model endpoint augmented with runtime metadata for strategy decisions."""

    endpoint_id: str
    model_id: str = ""
    platform_id: str = ""          # provider name, e.g. "openai", "google", "deepseek"
    tier_value: str = "standard"  # ModelTier value: free/lite/standard/premium/flagship
    perf_tier: str = "C"           # PerformanceTier value: S/A/B/C
    capabilities: list[str] = field(default_factory=list)
    health_status: str = "healthy"
    latency_p95: float = 0.0       # milliseconds
    success_rate: float = 0.0      # 0.0-1.0
    cost_per_1k_input: float = 0.0
    cost_per_1k_output: float = 0.0
    weight: int = 100
    tags: list[str] = field(default_factory=list)

    @property
    def total_cost_per_1k(self) -> float:
        return self.cost_per_1k_input + self.cost_per_1k_output

    @property
    def is_free(self) -> bool:
        return self.total_cost_per_1k == 0.0

    @property
    def is_healthy(self) -> bool:
        return self.health_status in ("healthy", "degraded", "unknown")


class MoaStrategy(ABC):
    """Abstract base class for MOA model-selection strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique strategy identifier (e.g. ``"cost_first"``)."""
        ...

    @abstractmethod
    def select_models(
        self,
        candidates: list[ModelCandidate],
        context: dict[str, Any] | None = None,
        n: int = 3,
    ) -> list[str]:
        """Select *n* endpoint IDs from *candidates* for MOA orchestration."""
        ...

    @abstractmethod
    def aggregate(
        self,
        responses: list[str],
        candidates: list[ModelCandidate] | None = None,
        selected_ids: list[str] | None = None,
    ) -> str:
        """Combine multiple model responses into a single final answer."""
        ...


# ============================================================
# Strategy Registry
# ============================================================
STRATEGY_REGISTRY: dict[str, MoaStrategy] = {}


def register_strategy(strategy: MoaStrategy) -> None:
    """Register a strategy instance in the global registry."""
    STRATEGY_REGISTRY[strategy.name] = strategy
    logger.debug("Registered MOA strategy: %s", strategy.name)


def get_strategy(name: str) -> MoaStrategy | None:
    """Look up a strategy by name (returns ``None`` if not found)."""
    return STRATEGY_REGISTRY.get(name)


def list_strategies() -> list[str]:
    """Return all registered strategy names."""
    return list(STRATEGY_REGISTRY.keys())


# ============================================================
# Candidate builder — bridges runtime subsystems into ModelCandidate
# ============================================================
def build_candidates(
    model_pool: Any | None = None,
    benchmark_engine: Any | None = None,
    capability_probe: Any | None = None,
    health_checker: Any | None = None,
) -> list[ModelCandidate]:
    """Build a list of :class:`ModelCandidate` from available runtime subsystems.

    This function is resilient: if a subsystem is ``None`` or lacks data,
    it falls back to sensible defaults so that strategies still work.
    """
    if model_pool is None:
        try:
            from ..model_pool import get_model_pool
            model_pool = get_model_pool()
        except Exception:
            return []

    candidates: list[ModelCandidate] = []
    endpoints = getattr(model_pool, "endpoints", {})

    # Collect benchmark metrics
    bench_metrics: dict[str, Any] = {}
    if benchmark_engine is not None:
        try:
            bench_metrics = benchmark_engine.get_all_metrics()
        except Exception:
            pass

    # Collect capability results
    cap_results: dict[str, Any] = {}
    if capability_probe is not None:
        try:
            cap_results = capability_probe.get_all_results()
        except Exception:
            pass

    # Collect health records
    health_records: dict[str, Any] = {}
    if health_checker is not None:
        try:
            health_records = health_checker.get_all_health()
        except Exception:
            pass

    for ep_id, ep in endpoints.items():
        cfg = getattr(ep, "config", None)
        if cfg is None:
            continue
        if not getattr(cfg, "enabled", True):
            continue

        # tier value
        tier_val = getattr(cfg, "tier", "standard")

        # performance metrics
        perf = bench_metrics.get(ep_id)
        perf_tier = "C"
        latency_p95 = 0.0
        success_rate = 0.0
        if perf is not None:
            pt = getattr(perf, "tier", None)
            perf_tier = getattr(pt, "value", "C") if pt else "C"
            latency_p95 = getattr(perf, "latency_p95", 0.0)
            success_rate = getattr(perf, "success_rate", 0.0)

        # capabilities
        caps: list[str] = []
        cap_res = cap_results.get(ep_id)
        if cap_res is not None:
            raw_caps = getattr(cap_res, "capabilities", [])
            caps = [getattr(c, "value", str(c)) for c in raw_caps]

        # health
        health_status = getattr(ep, "health_status", "unknown")
        hr = health_records.get(ep_id)
        if hr is not None:
            hs = getattr(hr, "status", None)
            health_status = getattr(hs, "value", health_status) if hs else health_status

        candidates.append(ModelCandidate(
            endpoint_id=ep_id,
            model_id=getattr(cfg, "model", ""),
            platform_id=getattr(cfg, "provider", ""),
            tier_value=tier_val,
            perf_tier=perf_tier,
            capabilities=caps,
            health_status=health_status,
            latency_p95=latency_p95,
            success_rate=success_rate,
            cost_per_1k_input=getattr(cfg, "cost_per_1k_input", 0.0),
            cost_per_1k_output=getattr(cfg, "cost_per_1k_output", 0.0),
            weight=getattr(cfg, "weight", 100),
            tags=list(getattr(cfg, "tags", [])),
        ))

    return candidates
