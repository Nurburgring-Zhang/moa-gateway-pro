"""Health check endpoints — liveness, readiness, startup probes + legacy /health."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from ..ha import health_checker
from ..model_pool import get_model_pool
from ..auth import require_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Legacy health endpoint — backward compatible."""
    pool = get_model_pool()
    snap = pool.snapshot()
    return {
        "status": "ok",
        "version": "1.0.0",
        "endpoints_total": snap["total"],
        "endpoints_enabled": snap["enabled"],
        "endpoints_healthy": snap["healthy"],
    }


@router.get("/health/live")
async def health_liveness():
    """Liveness probe — is the process alive?

    Used by K8s/Docker to determine if the container should be restarted.
    """
    return await health_checker.liveness()


@router.get("/health/ready")
async def health_readiness():
    """Readiness probe — can the instance accept traffic?

    Used by load balancers to route traffic only to ready instances.
    """
    result = await health_checker.readiness()
    return result


@router.get("/health/startup")
async def health_startup():
    """Startup probe — has initialization completed?

    Used by K8s to know when the app has finished starting up.
    """
    return await health_checker.startup()


@router.get("/api/health/detailed")
async def health_detailed():
    """Detailed health check with component-level status."""
    pool = get_model_pool()
    snapshot = pool.snapshot()
    readiness = await health_checker.readiness()
    return {
        **snapshot,
        "ha": readiness,
    }


# ========== API Health Management Endpoints (Task #43) ==========

@router.get("/v1/health")
async def api_health_overview():
    """All endpoint health overview."""
    from ..health import get_health_checker
    checker = get_health_checker()
    return checker.get_summary()


@router.get("/v1/health/{endpoint_id}")
async def api_health_detail(endpoint_id: str):
    """Single endpoint detailed health status."""
    from ..health import get_health_checker
    from fastapi import HTTPException
    checker = get_health_checker()
    if endpoint_id not in checker.get_all_health():
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id} not tracked")
    health = checker.get_health(endpoint_id)
    return health.summary()


@router.post("/v1/health/{endpoint_id}/probe")
async def api_health_probe(endpoint_id: str, key_info: dict = Depends(require_api_key)):
    """Manually trigger a probe for an endpoint."""
    from ..health import get_probe_engine, get_health_checker
    from fastapi import HTTPException
    checker = get_health_checker()
    if endpoint_id not in checker.get_all_health():
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id} not tracked")
    engine = get_probe_engine()
    success = await engine.probe_endpoint(endpoint_id)
    if not success:
        # Probe failed but we still return 200 with the result
        pass
    health = checker.get_health(endpoint_id)
    return {
        "endpoint_id": endpoint_id,
        "probe_success": success,
        "health": health.summary(),
    }


@router.get("/v1/health/purge/history")
async def api_purge_history():
    """Get purge history."""
    from ..health import get_purge_manager
    manager = get_purge_manager()
    return {
        "purge_history": manager.get_purge_history(),
        "total_purged": len(manager.get_purge_history()),
    }


@router.post("/v1/health/purge/run")
async def api_purge_run(key_info: dict = Depends(require_api_key)):
    """Manually trigger a purge check."""
    from ..health import get_purge_manager
    manager = get_purge_manager()
    purged = await manager.check_and_purge()
    return {
        "purged_endpoints": purged,
        "total_purged": len(purged),
    }


@router.post("/v1/health/{endpoint_id}/restore")
async def api_health_restore(endpoint_id: str, config: dict, key_info: dict = Depends(require_api_key)):
    """Restore a purged endpoint."""
    from ..health import get_purge_manager
    from fastapi import HTTPException
    manager = get_purge_manager()
    success = await manager.restore_endpoint(endpoint_id, config)
    if not success:
        raise HTTPException(status_code=500, detail=f"Failed to restore endpoint {endpoint_id}")
    return {"endpoint_id": endpoint_id, "restored": True}
