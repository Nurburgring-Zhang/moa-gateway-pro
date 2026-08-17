"""Health check endpoints — liveness, readiness, startup probes + legacy /health."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..auth import require_admin, require_api_key
from ..ha import health_checker
from ..model_pool import get_model_pool
from .admin import EndpointUpsert

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# v3.1.1: unified version constant (audit P3 — was hardcoded "1.0.0")
_HEALTH_VERSION = "3.1.1"


@router.get("/health")
async def health():
    """Legacy health endpoint — backward compatible."""
    pool = get_model_pool()
    snap = pool.snapshot()
    return {
        "status": "ok",
        "version": _HEALTH_VERSION,
        "endpoints_total": snap["total"],
        "endpoints_enabled": snap["enabled"],
        "endpoints_healthy": snap["healthy"],
        # D6: explicit mock visibility
        "mock_endpoints_count": snap.get("mock_backed", 0),
        "real_endpoints_count": snap.get("real_backed", 0),
        "mock_mode": _mock_mode(),
    }


def _mock_mode() -> str:
    try:
        from ..config import get_settings

        return get_settings().mock.mode
    except Exception:
        return "explicit"


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
    Returns 503 if not ready (K8s/load-balancer compatible).
    """
    result = await health_checker.readiness()
    if result.get("status") in ("unhealthy", "not_ready"):
        return JSONResponse(content=result, status_code=503)
    return result


@router.get("/health/startup")
async def health_startup():
    """Startup probe — has initialization completed?

    Used by K8s to know when the app has finished starting up.
    """
    return await health_checker.startup()


@router.get("/api/health/detailed")
async def health_detailed(key_info: dict = Depends(require_api_key)):
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
async def api_health_overview(key_info: dict = Depends(require_api_key)):
    """All endpoint health overview."""
    from ..health import get_health_checker

    checker = get_health_checker()
    return checker.get_summary()


@router.get("/v1/health/{endpoint_id}")
async def api_health_detail(endpoint_id: str, key_info: dict = Depends(require_api_key)):
    """Single endpoint detailed health status."""
    from fastapi import HTTPException

    from ..health import get_health_checker

    checker = get_health_checker()
    if endpoint_id not in checker.get_all_health():
        raise HTTPException(status_code=404, detail=f"Endpoint {endpoint_id} not tracked")
    health = checker.get_health(endpoint_id)
    return health.summary()


@router.post("/v1/health/{endpoint_id}/probe")
async def api_health_probe(endpoint_id: str, key_info: dict = Depends(require_api_key)):
    """Manually trigger a probe for an endpoint."""
    from fastapi import HTTPException

    from ..health import get_health_checker, get_probe_engine

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
async def api_purge_history(key_info: dict = Depends(require_admin)):
    """Get purge history (admin-only: records may contain endpoint metadata)."""
    from ..health import get_purge_manager

    manager = get_purge_manager()
    return {
        "purge_history": manager.get_purge_history(),
        "total_purged": len(manager.get_purge_history()),
    }


@router.post("/v1/health/purge/run")
async def api_purge_run(admin: dict = Depends(require_admin)):
    """Manually trigger a purge check (admin-only: destructive ops action)."""
    from ..health import get_purge_manager

    manager = get_purge_manager()
    purged = await manager.check_and_purge()
    return {
        "purged_endpoints": purged,
        "total_purged": len(purged),
    }


@router.post("/v1/health/{endpoint_id}/restore")
async def api_health_restore(
    endpoint_id: str,
    config: "EndpointUpsert",
    admin: dict = Depends(require_admin),
):
    """Restore a purged endpoint.

    v3.1.1 audit P1-3 fix:
    - admin-only (a plain API key could inject arbitrary endpoint configs
      into the model pool before — SSRF / cross-tenant poisoning surface)
    - config is validated by the strict EndpointUpsert schema instead of a
      raw dict, and its endpoint_id must match the path parameter.
    """
    from ..health import get_purge_manager

    if config.endpoint_id != endpoint_id:
        raise HTTPException(
            status_code=400,
            detail=f"config.endpoint_id '{config.endpoint_id}' does not match "
            f"path endpoint_id '{endpoint_id}'",
        )
    manager = get_purge_manager()
    success = await manager.restore_endpoint(endpoint_id, config.model_dump())
    if not success:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to restore endpoint {endpoint_id} "
            "(upsert rejected — check provider/api_base/field validity)",
        )
    return {"endpoint_id": endpoint_id, "restored": True}
