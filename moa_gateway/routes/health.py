"""Health check endpoints — liveness, readiness, startup probes + legacy /health."""
from __future__ import annotations

import logging

from fastapi import APIRouter

from ..ha import health_checker
from ..model_pool import get_model_pool

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
