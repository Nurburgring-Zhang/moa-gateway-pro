"""Admin management endpoints — /api/endpoints, /api/api-keys, /api/logs, etc."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .._helpers import err_500
from ..adapters import AdapterContext, GenericOpenAIAdapter, all_adapters
from ..audit import audit_action
from ..auth import require_admin
from ..cache.manager import get_cache_manager
from ..config import get_settings
from ..model_pool import get_model_pool
from ..observability import Metrics
from ..rbac import ROLE_PERMISSIONS, Permission, Role, check_permission_or_raise
from ..storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["admin"])


class EndpointUpsert(BaseModel):
    endpoint_id: str
    provider: str
    model: str
    tier: str = "standard"
    api_base: str = ""
    api_key_plain: str | None = None
    api_key_env: str | None = None
    cost_per_1k_input: float = 0.001
    cost_per_1k_output: float = 0.002
    max_tokens: int = 8192
    timeout: int = 120
    weight: int = 100
    enabled: bool = True
    tags: list[str] = Field(default_factory=list)


class CreateAPIKeyRequest(BaseModel):
    name: str
    quota_rpm: int = 60
    quota_daily_tokens: int = 5_000_000


# ========== Model Endpoints Management ==========
@router.get("/api/endpoints")
async def list_endpoints(admin: dict[str, Any] = Depends(require_admin)):
    pool = get_model_pool()
    result = pool.snapshot()
    return result


@router.post("/api/endpoints")
async def upsert_endpoint(req: EndpointUpsert, admin: dict[str, Any] = Depends(require_admin)):
    pool = get_model_pool()
    ep_dict = req.model_dump()
    try:
        ep = pool.upsert_endpoint(ep_dict)
    except HTTPException:
        raise
    except Exception as e:
        raise err_500(e, "upsert failed:")
    return {"ok": True, "id": ep.id}


@router.delete("/api/endpoints/{eid}")
async def delete_endpoint(eid: str, admin: dict[str, Any] = Depends(require_admin)):
    pool = get_model_pool()
    ok = pool.remove_endpoint(eid)
    if not ok:
        raise HTTPException(404, "endpoint not found")
    return {"ok": True}


@router.post("/api/endpoints/{eid}/toggle")
async def toggle_endpoint(eid: str, admin: dict[str, Any] = Depends(require_admin)):
    pool = get_model_pool()
    if eid not in pool.endpoints:
        raise HTTPException(404, "endpoint not found")
    ep = pool.endpoints[eid]
    ep.config.enabled = not ep.config.enabled
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
    except Exception:
        pass
    return {"ok": True, "enabled": ep.config.enabled}


@router.post("/api/endpoints/{eid}/reset-breaker")
async def reset_breaker(eid: str, admin: dict[str, Any] = Depends(require_admin)):
    pool = get_model_pool()
    if eid not in pool.endpoints:
        raise HTTPException(404, "endpoint not found")
    ep = pool.endpoints[eid]
    ep.recover_breaker()
    return {"ok": True}


# ========== API Keys Management ==========
@router.get("/api/api-keys")
async def list_api_keys(admin: dict[str, Any] = Depends(require_admin)):
    return get_storage().list_api_keys()


@router.post("/api/api-keys")
async def create_api_key(
    req: CreateAPIKeyRequest, admin: dict[str, Any] = Depends(require_admin)
):
    return get_storage().create_api_key(req.name, req.quota_rpm, req.quota_daily_tokens)


@router.delete("/api/api-keys/{key_id}")
async def delete_api_key(key_id: str, admin: dict[str, Any] = Depends(require_admin)):
    ok = get_storage().delete_api_key(key_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


# ========== Logs & Stats ==========
@router.get("/api/logs")
async def list_logs(
    limit: int = 100,
    api_key_id: str | None = None,
    admin: dict[str, Any] = Depends(require_admin),
):
    return get_storage().list_logs(limit=limit, api_key_id=api_key_id)


@router.get("/api/stats")
async def stats(days: int = 7, admin: dict[str, Any] = Depends(require_admin)):
    since = time.time() - days * 86400
    return get_storage().aggregate_stats(since_ts=since)


@router.get("/api/metrics")
async def metrics_endpoint(admin: dict[str, Any] = Depends(require_admin)):
    return Metrics.instance().snapshot()


# ========== Adapters & Setup ==========
@router.get("/api/adapters")
async def get_adapters_config(admin: dict[str, Any] = Depends(require_admin)):
    """Return connection config for various Agents"""
    s = get_settings()
    ctx = AdapterContext(
        gateway_host=s.server.host if s.server.host != "0.0.0.0" else "127.0.0.1",
        gateway_port=s.server.port,
        api_key=(
            get_storage().list_api_keys()[0]["key_id"]
            if get_storage().list_api_keys()
            else "demo-key-please-change"
        ),
        https=False,
    )
    return all_adapters(ctx)


@router.get("/api/adapters/curl")
async def adapters_curl(admin: dict[str, Any] = Depends(require_admin)):
    s = get_settings()
    ctx = AdapterContext(
        gateway_host="127.0.0.1",
        gateway_port=s.server.port,
        api_key="YOUR-API-KEY",
    )
    return {
        "curl": GenericOpenAIAdapter(ctx).get_curl_example(),
        "python": GenericOpenAIAdapter(ctx).get_python_example(),
    }


# ========== RBAC User & Role Management ==========
class UpdateRoleRequest(BaseModel):
    role: str = Field(..., description="New role: admin/operator/user/readonly")


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


@router.get("/api/admin/users")
async def list_users(request: Request, admin: dict[str, Any] = Depends(require_admin)):
    """List all users with their roles."""
    check_permission_or_raise(admin, Permission.READ_USERS)
    storage = get_storage()
    users = storage.list_admin_users()
    await audit_action(request, "list_users", "users")
    return {"users": users}


@router.get("/api/admin/users/{user_id}")
async def get_user(user_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin)):
    """Get a specific user."""
    check_permission_or_raise(admin, Permission.READ_USERS)
    storage = get_storage()
    user = storage.get_admin_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@router.post("/api/admin/users")
async def create_user(
    req: CreateUserRequest, request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Create a new user with specified role."""
    check_permission_or_raise(admin, Permission.WRITE_USERS)
    storage = get_storage()
    try:
        user = storage.create_admin_user(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not user:
        raise HTTPException(409, "Username already exists")
    await audit_action(
        request, "create_user", "users",
        resource_id=str(user["id"]),
        detail={"username": req.username, "role": req.role},
    )
    return {"ok": True, "user": user}


@router.put("/api/admin/users/{user_id}/role")
async def update_user_role(
    user_id: int, req: UpdateRoleRequest, request: Request,
    admin: dict[str, Any] = Depends(require_admin),
):
    """Update a user's role. Requires admin:rbac permission."""
    check_permission_or_raise(admin, Permission.ADMIN_RBAC)
    storage = get_storage()
    target_user = storage.get_admin_user(user_id)
    if not target_user:
        raise HTTPException(404, "User not found")
    try:
        ok = storage.update_user_role(user_id, req.role)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not ok:
        raise HTTPException(500, "Failed to update role")
    await audit_action(
        request, "update_role", "users",
        resource_id=str(user_id),
        detail={
            "username": target_user["username"],
            "old_role": target_user["role"],
            "new_role": req.role,
        },
    )
    return {"ok": True, "user_id": user_id, "new_role": req.role}


@router.delete("/api/admin/users/{user_id}")
async def delete_user(
    user_id: int, request: Request, admin: dict[str, Any] = Depends(require_admin)
):
    """Delete a user. Cannot delete yourself."""
    check_permission_or_raise(admin, Permission.WRITE_USERS)
    storage = get_storage()
    target_user = storage.get_admin_user(user_id)
    if not target_user:
        raise HTTPException(404, "User not found")
    # Prevent self-deletion
    if target_user["username"] == admin.get("sub"):
        raise HTTPException(400, "Cannot delete yourself")
    ok = storage.delete_admin_user(user_id)
    if not ok:
        raise HTTPException(500, "Failed to delete user")
    await audit_action(
        request, "delete_user", "users",
        resource_id=str(user_id),
        detail={"username": target_user["username"]},
    )
    return {"ok": True}


@router.get("/api/admin/roles")
async def list_roles(admin: dict[str, Any] = Depends(require_admin)):
    """List all available roles and their permissions."""
    result = {}
    for role in Role:
        perms = ROLE_PERMISSIONS[role]
        result[role.value] = {
            "permissions": sorted([p.value for p in perms]),
            "permission_count": len(perms),
        }
    return {"roles": result}


@router.get("/api/admin/audit-log")
async def get_audit_log(
    limit: int = 100,
    request: Request = None,
    admin: dict[str, Any] = Depends(require_admin),
):
    """Retrieve recent audit log entries from the log file."""
    check_permission_or_raise(admin, Permission.READ_LOGS)
    import json
    from pathlib import Path

    log_path = Path("data/logs/audit.jsonl")
    if not log_path.exists():
        return {"entries": [], "total": 0}

    # Read last N lines efficiently
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    recent = lines[-limit:] if len(lines) > limit else lines
    recent.reverse()  # Most recent first

    entries = []
    for line in recent:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    return {"entries": entries, "total": len(lines)}

# ========== Cache Management ==========
@router.get("/api/admin/cache/stats")
async def cache_stats(admin: dict[str, Any] = Depends(require_admin)):
    """Get cache hit/miss statistics."""
    cache_mgr = get_cache_manager()
    stats = cache_mgr.get_stats()
    return stats


@router.post("/api/admin/cache/clear")
async def cache_clear(request: Request, admin: dict[str, Any] = Depends(require_admin)):
    """Clear all cache layers."""
    cache_mgr = get_cache_manager()
    await cache_mgr.clear_all()
    await audit_action(request, "cache_clear", "cache")
    return {"ok": True, "message": "All cache layers cleared"}


@router.get("/api/admin/cache/config")
async def cache_config(admin: dict[str, Any] = Depends(require_admin)):
    """Get current cache configuration."""
    cache_mgr = get_cache_manager()
    return cache_mgr.get_config()

