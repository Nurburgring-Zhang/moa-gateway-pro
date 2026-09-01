"""HTTP surface for the quota scheduler (M2).

Endpoints (all gated by API key + the ``quota_scheduler`` capability):

- ``GET  /v1/quota/status``    — live per-endpoint quota states.
- ``GET  /v1/quota/snapshots`` — durable change-detected history.
- ``POST /v1/quota/check``     — admission gate (can_afford_request).
- ``POST /v1/quota/refresh``   — drop rolled-over windows, re-classify.

The router is self-contained: it never mutates shared gateway modules. When
``settings.quota.enabled`` is False the query/mutation surfaces return 503 —
the module is opt-in and cannot influence pre-existing routes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..config import get_settings
from ..quota_scheduler.monitor import QuotaMonitor, get_monitor
from ..quota_scheduler.snapshots import SnapshotStore, get_snapshot_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/quota", tags=["quota_scheduler"])

_CAPABILITY = "quota_scheduler"


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: str = ""
    connection_id: str = ""
    endpoint_id: str = ""
    now_ms: float | None = Field(default=None, description="Injectable clock (tests)")


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    now_ms: float | None = Field(default=None, description="Injectable clock (tests)")


def _monitor(request: Request) -> QuotaMonitor:
    """app.state override (tests) wins; production uses the module singleton."""
    monitor = getattr(request.app.state, "quota_monitor", None)
    return monitor if monitor is not None else get_monitor()


def _snapshot_store(request: Request) -> SnapshotStore:
    store = getattr(request.app.state, "quota_snapshot_store", None)
    return store if store is not None else get_snapshot_store()


def _require_enabled() -> None:
    if not get_settings().quota.enabled:
        raise HTTPException(
            status_code=503,
            detail="quota_scheduler is disabled in gateway settings",
        )


@router.get("/status")
async def quota_status(
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    return _monitor(request).status_summary()


@router.get("/snapshots")
async def quota_snapshots(
    request: Request,
    endpoint_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    rows = _snapshot_store(request).list(endpoint_id=endpoint_id, limit=limit)
    return {"count": len(rows), "snapshots": rows}


@router.post("/check")
async def quota_check(
    payload: CheckRequest,
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    decision = _monitor(request).can_afford_request(
        provider_id=payload.provider_id,
        connection_id=payload.connection_id,
        endpoint_id=payload.endpoint_id,
        now_ms=payload.now_ms,
    )
    return decision


@router.post("/refresh")
async def quota_refresh(
    request: Request,
    payload: RefreshRequest | None = None,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    now_ms = payload.now_ms if payload is not None else None
    result = _monitor(request).refresh(now_ms=now_ms)
    return result
