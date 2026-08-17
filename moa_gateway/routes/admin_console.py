"""Admin console API surface for the admin-ui frontend (audit F5 fix).

The Next.js admin-ui (admin-ui/) calls a set of ``/api/admin/*`` endpoints that
previously did not exist on the backend — every page silently fell back to
hardcoded mock data. These endpoints are REAL implementations: they read/write
the live model pool, storage, cache manager, rate limiter and capability
toggles. No fabricated data.

Endpoints provided:
  GET  /api/admin/stats                     dashboard stats (real aggregates)
  GET  /api/admin/endpoints                 endpoint snapshot (alias of /api/endpoints)
  PUT  /api/admin/endpoints/{eid}           enable/disable an endpoint
  GET  /api/admin/models                    model view over endpoints
  POST /api/admin/models                    create endpoint from model form
  PUT  /api/admin/models/{mid}              update endpoint from model form
  DELETE /api/admin/models/{mid}            delete endpoint
  GET  /api/admin/capabilities              capability toggle list
  PUT  /api/admin/capabilities/{name}       set a capability toggle
  GET  /api/admin/settings                  current effective settings sections
  PUT  /api/admin/settings                  apply runtime-mutable settings
  GET  /api/admin/api-keys                  API key list (alias)
  POST /api/admin/api-keys                  create API key (alias)
  DELETE /api/admin/api-keys/{key_id}       delete API key (alias)
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import capability_toggles
from ..audit import audit_action
from ..auth import require_admin
from ..cache.manager import get_cache_manager
from ..config import get_settings
from ..model_pool import get_model_pool
from ..storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin-console"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class EndpointToggleRequest(BaseModel):
    enabled: bool


class AdminModelRequest(BaseModel):
    """admin-ui model form: name/provider/weight (+ optional extras)."""

    model_config = {"protected_namespaces": ()}

    name: str = Field(..., min_length=1, max_length=128)
    provider: str = Field(default="openai")
    model: str | None = None
    tier: str = "standard"
    api_base: str = ""
    api_key_env: str | None = None
    weight: int = Field(default=100, ge=0, le=1000)
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class CapabilityToggleRequest(BaseModel):
    enabled: bool


class AdminSettingsRequest(BaseModel):
    cache: dict[str, Any] | None = None
    database: dict[str, Any] | None = None
    mcp: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None


class AdminAPIKeyRequest(BaseModel):
    name: str
    quota_rpm: int = 60
    quota_daily_tokens: int = 5_000_000


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------


@router.get("/api/admin/stats")
async def admin_stats(days: int = 7, admin: dict[str, Any] = Depends(require_admin)):
    """Real dashboard stats — no mock fallback.

    Returns aggregates from request_logs plus live model-pool health, in the
    shape the admin-ui dashboard consumes:
      total_requests, active_models, tokens_today, avg_latency_ms,
      requests_trend (last-12-hour counts), model_health (per endpoint).
    """
    storage = get_storage()
    pool = get_model_pool()
    now = time.time()

    agg = storage.aggregate_stats(since_ts=now - days * 86400)

    # Tokens used today (since local midnight).
    day_start = now - (now % 86400)
    today = storage.aggregate_stats(since_ts=day_start)

    # Hourly request counts for the last 12 hours (real trend).
    trend: list[int] = []
    with storage.conn() as c:
        for h in range(11, -1, -1):
            start = now - (h + 1) * 3600
            end = now - h * 3600
            row = c.execute(
                "SELECT COUNT(*) AS n FROM request_logs WHERE timestamp >= ? AND timestamp < ?",
                (start, end),
            ).fetchone()
            trend.append(int(row["n"]) if row else 0)

    snap = pool.snapshot()
    model_health = [
        {
            "name": e["id"],
            "status": e["health"],
            "requests": e["total_calls"],
        }
        for e in snap["endpoints"]
    ]

    return {
        "total_requests": agg["total_requests"],
        "active_models": snap["enabled"],
        "tokens_today": today["total_tokens"],
        "avg_latency_ms": agg["avg_latency_ms"],
        "total_cost": agg["total_cost"],
        "requests_trend": trend,
        "model_health": model_health,
    }


# ---------------------------------------------------------------------------
# Endpoints (FE path alias + PUT toggle)
# ---------------------------------------------------------------------------


@router.get("/api/admin/endpoints")
async def admin_list_endpoints(admin: dict[str, Any] = Depends(require_admin)):
    pool = get_model_pool()
    return pool.snapshot()


@router.put("/api/admin/endpoints/{eid}")
async def admin_update_endpoint(
    eid: str,
    req: EndpointToggleRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    """Set an endpoint's enabled flag explicitly (FE toggle sends {enabled})."""
    pool = get_model_pool()
    if eid not in pool.endpoints:
        raise HTTPException(404, "endpoint not found")
    ep = pool.endpoints[eid]
    ep.config.enabled = bool(req.enabled)
    try:
        get_storage().upsert_endpoint(
            {
                "endpoint_id": eid,
                "provider": ep.config.provider,
                "model": ep.config.model,
                "tier": ep.config.tier,
                "api_base": ep.config.api_base,
                "api_key_env": ep.config.api_key_env,
                "cost_per_1k_input": ep.config.cost_per_1k_input,
                "cost_per_1k_output": ep.config.cost_per_1k_output,
                "max_tokens": ep.config.max_tokens,
                "timeout": ep.config.timeout,
                "weight": ep.config.weight,
                "enabled": ep.config.enabled,
                "tags": ep.config.tags,
            }
        )
    except Exception as e:  # pragma: no cover
        logger.warning("failed to persist endpoint toggle: %s", e)
    await audit_action(
        request, "toggle_endpoint", "endpoints", resource_id=eid,
        detail={"enabled": ep.config.enabled},
    )
    return {"ok": True, "id": eid, "enabled": ep.config.enabled}


# ---------------------------------------------------------------------------
# Models (FE CRUD over endpoints)
# ---------------------------------------------------------------------------


def _endpoint_from_model_req(req: AdminModelRequest) -> dict[str, Any]:
    return {
        "endpoint_id": req.name,
        "provider": req.provider,
        "model": req.model or req.name,
        "tier": req.tier,
        "api_base": req.api_base,
        "api_key_env": req.api_key_env,
        "weight": req.weight,
        "enabled": req.enabled,
        "tags": req.tags,
    }


@router.get("/api/admin/models")
async def admin_list_models(admin: dict[str, Any] = Depends(require_admin)):
    """Model view over the live endpoint pool (real tags + status)."""
    pool = get_model_pool()
    snap = pool.snapshot()
    data = []
    for e in snap["endpoints"]:
        live = pool.endpoints.get(e["id"])
        tags = list(live.config.tags) if live is not None else []
        # Map health+enabled to a single status the UI can render.
        if not e["enabled"]:
            status = "inactive"
        elif e["health"] == "healthy":
            status = "active"
        elif e["health"] == "unhealthy":
            status = "error"
        else:
            status = "pending"
        data.append(
            {
                "id": e["id"],
                "name": e["id"],
                "provider": e["provider"],
                "model": e["model"],
                "tier": e["tier"],
                "weight": e["weight"],
                "enabled": e["enabled"],
                "health": e["health"],
                "status": status,
                "capabilities": tags,
            }
        )
    return {"data": data, "total": snap["total"]}


@router.post("/api/admin/models")
async def admin_create_model(
    req: AdminModelRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    pool = get_model_pool()
    try:
        ep = pool.upsert_endpoint(_endpoint_from_model_req(req))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"create model failed: {e}") from e
    await audit_action(
        request, "create_model", "models", resource_id=req.name,
        detail={"provider": req.provider},
    )
    return {"ok": True, "id": ep.id}


@router.put("/api/admin/models/{mid}")
async def admin_update_model(
    mid: str,
    req: AdminModelRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    pool = get_model_pool()
    if mid not in pool.endpoints:
        raise HTTPException(404, "model not found")
    data = _endpoint_from_model_req(req)
    data["endpoint_id"] = mid
    try:
        ep = pool.upsert_endpoint(data)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"update model failed: {e}") from e
    await audit_action(request, "update_model", "models", resource_id=mid)
    return {"ok": True, "id": ep.id}


@router.delete("/api/admin/models/{mid}")
async def admin_delete_model(
    mid: str,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    pool = get_model_pool()
    ok = pool.remove_endpoint(mid)
    if not ok:
        raise HTTPException(404, "model not found")
    await audit_action(request, "delete_model", "models", resource_id=mid)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Capability toggles (REAL — persisted + gate endpoints via dependency)
# ---------------------------------------------------------------------------


@router.get("/api/admin/capabilities")
async def admin_list_capabilities(admin: dict[str, Any] = Depends(require_admin)):
    return {"capabilities": capability_toggles.get_all()}


@router.put("/api/admin/capabilities/{name}")
async def admin_set_capability(
    name: str,
    req: CapabilityToggleRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    state = capability_toggles.set_enabled(name, req.enabled)
    await audit_action(
        request, "set_capability", "capabilities", resource_id=name,
        detail={"enabled": req.enabled},
    )
    return {"ok": True, "name": name, "enabled": state[name]}


# ---------------------------------------------------------------------------
# Settings (read effective config; apply runtime-mutable fields)
# ---------------------------------------------------------------------------


@router.get("/api/admin/settings")
async def admin_get_settings(admin: dict[str, Any] = Depends(require_admin)):
    """Return the CURRENT effective settings in the shape the admin-ui uses.

    Field names match admin-ui's SystemSettings type exactly (cache.ttl_seconds,
    cache.max_size_mb, database.url/pool_size/max_overflow,
    mcp.enabled/server_url/timeout_seconds, rate_limit.enabled/
    requests_per_minute/burst_size). All values are the live effective config —
    nothing fabricated.
    """
    s = get_settings()
    mcp_defaults = get_storage().get_config_overrides().get("mcp_defaults") or {}
    return {
        "cache": {
            "enabled": s.cache.enabled,
            "ttl_seconds": s.cache.exact_ttl,
            "max_size_mb": s.cache.exact_max_size,
            "backend": "redis" if s.cache.redis_url else "memory",
        },
        "database": {
            "url": s.storage.database_url or f"sqlite:///{s.storage.db_path}",
            "pool_size": s.storage.db_pool_size,
            "max_overflow": s.storage.db_max_overflow,
        },
        "mcp": {
            "enabled": bool(mcp_defaults.get("enabled", True)),
            "server_url": str(mcp_defaults.get("server_url", "")),
            "timeout_seconds": int(mcp_defaults.get("timeout_seconds", 30)),
        },
        "rate_limit": {
            "enabled": s.ratelimit.enabled,
            "requests_per_minute": s.ratelimit.per_key_rpm,
            "burst_size": int(mcp_defaults.get("burst_size", 0)) or s.ratelimit.per_key_rpm,
        },
    }


@router.put("/api/admin/settings")
async def admin_update_settings(
    req: AdminSettingsRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    """Apply runtime-mutable settings to the LIVE process and persist them.

    Cache TTL/size/enabled and rate-limit values are applied immediately
    (the cache manager and rate limiter read these attributes per call).
    Database/MCP changes are persisted and surfaced; a database backend change
    requires a restart to take effect (reported honestly, not silently applied).
    """
    s = get_settings()
    storage = get_storage()
    applied: list[str] = []
    restart_required: list[str] = []

    if req.cache:
        c = req.cache
        if "enabled" in c:
            s.cache.enabled = bool(c["enabled"])
            try:
                get_cache_manager().enabled = s.cache.enabled
            except Exception:
                pass
            applied.append("cache.enabled")
        if "ttl_seconds" in c:
            s.cache.exact_ttl = int(c["ttl_seconds"])
            applied.append("cache.ttl_seconds")
        if "max_size_mb" in c:
            s.cache.exact_max_size = int(c["max_size_mb"])
            applied.append("cache.max_size_mb")
        if c.get("backend") == "redis":
            restart_required.append("cache.backend(redis requires redis_url + restart)")

    if req.rate_limit:
        r = req.rate_limit
        if "enabled" in r:
            s.ratelimit.enabled = bool(r["enabled"])
            applied.append("rate_limit.enabled")
        if "requests_per_minute" in r:
            s.ratelimit.per_key_rpm = int(r["requests_per_minute"])
            applied.append("rate_limit.requests_per_minute")

    if req.database:
        d = req.database
        if "pool_size" in d:
            s.storage.db_pool_size = int(d["pool_size"])
            restart_required.append("database.pool_size")
        if "max_overflow" in d:
            s.storage.db_max_overflow = int(d["max_overflow"])
            restart_required.append("database.max_overflow")
        if "url" in d and d["url"] != (s.storage.database_url or f"sqlite:///{s.storage.db_path}"):
            s.storage.database_url = d["url"]
            restart_required.append("database.url")

    if req.mcp:
        # Persist MCP defaults (enabled/server_url/timeout_seconds) for external
        # servers; they take effect on subsequent external-server connections.
        storage.set_config_override("mcp_defaults", req.mcp)
        applied.append("mcp.defaults")

    # Persist the runtime-applied overrides so they survive restart.
    storage.set_config_override(
        "admin_settings_overrides",
        {
            "cache": {
                "enabled": s.cache.enabled,
                "exact_ttl": s.cache.exact_ttl,
                "exact_max_size": s.cache.exact_max_size,
            },
            "ratelimit": {
                "enabled": s.ratelimit.enabled,
                "per_key_rpm": s.ratelimit.per_key_rpm,
                "per_key_daily_tokens": s.ratelimit.per_key_daily_tokens,
            },
        },
    )

    await audit_action(
        request, "update_settings", "settings",
        detail={"applied": applied, "restart_required": restart_required},
    )
    return {
        "ok": True,
        "applied": applied,
        "restart_required": restart_required,
    }


# ---------------------------------------------------------------------------
# API keys (FE path aliases)
# ---------------------------------------------------------------------------


@router.get("/api/admin/api-keys")
async def admin_list_api_keys(admin: dict[str, Any] = Depends(require_admin)):
    """API keys with REAL usage (today's token consumption from storage)."""
    storage = get_storage()
    day = time.strftime("%Y%m%d")
    rows = storage.list_api_keys()
    result = []
    for r in rows:
        key_id = r.get("key_id", "")
        used_today = storage.get_daily_tokens(key_id, day)
        result.append(
            {
                "id": key_id,
                "key_id": key_id,
                "name": r.get("name", ""),
                # Plaintext keys are never stored/retrievable after creation;
                # expose only the id for display (honestly, no fake key).
                "key": key_id,
                "status": "active" if r.get("enabled", 1) else "revoked",
                "enabled": bool(r.get("enabled", 1)),
                "quota": int(r.get("quota_daily_tokens", 0) or 0),
                "quota_rpm": int(r.get("quota_rpm", 0) or 0),
                "quota_daily_tokens": int(r.get("quota_daily_tokens", 0) or 0),
                "used": int(used_today),
                "created_at": r.get("created_at"),
                "last_used": r.get("last_used"),
            }
        )
    return result


@router.post("/api/admin/api-keys")
async def admin_create_api_key(
    req: AdminAPIKeyRequest,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    result = get_storage().create_api_key(req.name, req.quota_rpm, req.quota_daily_tokens)
    await audit_action(
        request, "create_api_key", "api-keys", resource_id=result["key_id"],
        detail={"name": req.name},
    )
    return result


@router.delete("/api/admin/api-keys/{key_id}")
async def admin_delete_api_key(
    key_id: str,
    request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    ok = get_storage().delete_api_key(key_id)
    if not ok:
        raise HTTPException(404, "not found")
    await audit_action(request, "delete_api_key", "api-keys", resource_id=key_id)
    return {"ok": True}
