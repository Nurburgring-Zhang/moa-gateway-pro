"""moa_gateway.routes.benchmark — Benchmark and capability API routes.

Provides REST endpoints for:
- Performance benchmark overview and detail
- Manual benchmark triggers
- Tier-based endpoint filtering
- Capability overview and detail
- Capability-based endpoint filtering
- Manual capability probe triggers
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..benchmark import (
    Capability,
    PerformanceTier,
    get_benchmark_engine,
    get_capability_probe,
)
from ..auth import require_api_key

logger = logging.getLogger(__name__)

# Single router — no prefix; routes use full paths to support both
# /v1/benchmark/* and /v1/capabilities/* prefixes on the same router.
router = APIRouter(tags=["benchmark"])


# ========== Benchmark Endpoints ==========


@router.get("/v1/benchmark")
async def get_benchmark_overview() -> dict[str, Any]:
    """Get performance tier overview for all endpoints."""
    engine = get_benchmark_engine()
    if engine is None:
        return {"total_tracked": 0, "tier_counts": {}, "endpoints": []}
    return engine.get_tier_summary()


@router.get("/v1/benchmark/tier/{tier}")
async def get_endpoints_by_tier(tier: str) -> dict[str, Any]:
    """Filter endpoints by performance tier (S/A/B/C)."""
    try:
        perf_tier = PerformanceTier(tier.upper())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid tier '{tier}'. Must be one of: S, A, B, C",
        )
    engine = get_benchmark_engine()
    if engine is None:
        return {"tier": perf_tier.value, "endpoints": []}
    endpoint_ids = engine.get_endpoints_by_tier(perf_tier)
    return {"tier": perf_tier.value, "endpoints": endpoint_ids}


@router.post("/v1/benchmark/run")
async def run_benchmark_all(key_info: dict[str, Any] = Depends(require_api_key)) -> dict[str, Any]:
    """Manually trigger benchmark for all healthy endpoints."""
    engine = get_benchmark_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="Benchmark engine not initialized")
    results = await engine.benchmark_all()
    return {
        "status": "completed",
        "total": len(results),
        "successful": sum(1 for r in results.values() if r.success),
        "failed": sum(1 for r in results.values() if not r.success),
    }


@router.post("/v1/benchmark/{endpoint_id}/run")
async def run_benchmark_one(endpoint_id: str, key_info: dict[str, Any] = Depends(require_api_key)) -> dict[str, Any]:
    """Manually trigger benchmark for a single endpoint."""
    engine = get_benchmark_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="Benchmark engine not initialized")
    result = await engine.benchmark_endpoint(endpoint_id)
    return {
        "endpoint_id": endpoint_id,
        "success": result.success,
        "latency_ms": round(result.latency_ms, 2),
        "tokens_per_second": result.tokens_per_second,
        "error": result.error,
    }


@router.get("/v1/benchmark/{endpoint_id}")
async def get_endpoint_benchmark(endpoint_id: str) -> dict[str, Any]:
    """Get detailed benchmark metrics for a specific endpoint."""
    engine = get_benchmark_engine()
    if engine is None:
        raise HTTPException(status_code=404, detail="Benchmark engine not initialized")
    metrics = engine.get_metrics(endpoint_id)
    if metrics is None:
        raise HTTPException(
            status_code=404,
            detail=f"No benchmark data for endpoint '{endpoint_id}'",
        )
    return metrics.summary()


# ========== Capability Endpoints ==========


@router.get("/v1/capabilities")
async def get_capabilities_overview() -> dict[str, Any]:
    """Get capability overview for all endpoints."""
    probe = get_capability_probe()
    if probe is None:
        return {"total_probed": 0, "capability_counts": {}, "endpoints": []}
    return probe.get_summary()


@router.get("/v1/capabilities/filter/by-capability")
async def filter_by_capability(
    capability: str = Query(..., description="Capability to filter by"),
) -> dict[str, Any]:
    """Filter endpoints by capability."""
    try:
        cap = Capability(capability.lower())
    except ValueError:
        valid = [c.value for c in Capability]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid capability '{capability}'. Must be one of: {valid}",
        )
    probe = get_capability_probe()
    if probe is None:
        return {"capability": cap.value, "endpoints": []}
    endpoint_ids = probe.get_endpoints_by_capability(cap)
    return {"capability": cap.value, "endpoints": endpoint_ids}


@router.post("/v1/capabilities/run")
async def run_capability_probe_all(key_info: dict[str, Any] = Depends(require_api_key)) -> dict[str, Any]:
    """Manually trigger capability probe for all healthy endpoints."""
    probe = get_capability_probe()
    if probe is None:
        raise HTTPException(status_code=503, detail="Capability probe not initialized")
    results = await probe.probe_all()
    return {
        "status": "completed",
        "total": len(results),
    }


@router.get("/v1/capabilities/{endpoint_id}")
async def get_endpoint_capabilities(endpoint_id: str) -> dict[str, Any]:
    """Get detailed capabilities for a specific endpoint."""
    probe = get_capability_probe()
    if probe is None:
        raise HTTPException(status_code=404, detail="Capability probe not initialized")
    result = probe.get_result(endpoint_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"No capability data for endpoint '{endpoint_id}'",
        )
    return result.summary()
