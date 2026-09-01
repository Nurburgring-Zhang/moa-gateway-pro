"""HTTP routes for the MemoraX-Code-style memory layer (M10 + M11).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT) —
the hook endpoints mirror MemoraX's memory hook protocol
(``memory/hook-command.ts``): JSON commands from coding clients, validated
fail-closed against per-command/per-client key whitelists.

M10 endpoints (cross-session memory):
    POST   /v1/memory/turn-start       recall injection + turn state
    POST   /v1/memory/writeback        redact -> buffer -> chunk -> store
    POST   /v1/memory/skill-reminder   reminder trace recording
    GET    /v1/memory/recall           explicit hybrid recall (operator)
    GET    /v1/memory/items            list stored memories for a scope
    DELETE /v1/memory/items/{item_id}  delete one memory (scope-checked)

M11 endpoints (workspace/repository memory):
    GET  /v1/workspace-memory/status   .moa_memory status
    POST /v1/workspace-memory/update   policy-driven rebuild/skip

All routes are gated by the ``memory`` capability toggle and require a valid
API key.  Hook bodies are parsed from the raw request so the fail-closed
whitelist sees the *exact* key set the client sent (a Pydantic body model
would silently drop unknown keys and defeat the whitelist).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..config import get_settings
from ..memory.hook_protocol import (
    INVALID_MEMORY_HOOK_COMMAND,
    parse_skill_reminder_command,
    parse_turn_start_command,
    parse_writeback_command,
)
from ..memory.service import get_memory_service
from ..workspace_memory.service import get_workspace_memory_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory"])

_MEMORY_DEP = [Depends(require_capability("memory"))]


def _base_user_id(key_info: dict[str, Any]) -> str:
    """Derive the memory base user id from the authenticated key identity.

    Never uses raw key material — only the resolved name/key_id.
    """
    for field in ("name", "key_id", "sub"):
        value = key_info.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "anonymous"


async def _read_command(request: Request) -> Any:
    """Read the raw JSON hook body (exact key set preserved)."""
    try:
        return await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc


# ===========================================================================
# M10 — hook protocol endpoints
# ===========================================================================
@router.post("/v1/memory/turn-start", dependencies=_MEMORY_DEP)
async def memory_turn_start(request: Request, key_info: dict = Depends(require_api_key)):
    """MemoraX turn-start hook: record turn state and recall memories."""
    body = await _read_command(request)
    ok, parsed = parse_turn_start_command(body)
    if not ok:
        raise HTTPException(status_code=400, detail=str(parsed or INVALID_MEMORY_HOOK_COMMAND))
    return get_memory_service().handle_turn_start(parsed, _base_user_id(key_info))


@router.post("/v1/memory/writeback", dependencies=_MEMORY_DEP)
async def memory_writeback(request: Request, key_info: dict = Depends(require_api_key)):
    """MemoraX writeback hook: redact -> buffer -> maybe flush to store."""
    body = await _read_command(request)
    ok, parsed = parse_writeback_command(body)
    if not ok:
        raise HTTPException(status_code=400, detail=str(parsed or INVALID_MEMORY_HOOK_COMMAND))
    service = get_memory_service()
    service.sweep()  # opportunistically flush expired buffers
    return service.handle_writeback(parsed, _base_user_id(key_info))


@router.post("/v1/memory/skill-reminder", dependencies=_MEMORY_DEP)
async def memory_skill_reminder(request: Request, key_info: dict = Depends(require_api_key)):
    """MemoraX skill-reminder hook: persist a reminder trace."""
    body = await _read_command(request)
    ok, parsed = parse_skill_reminder_command(body)
    if not ok:
        raise HTTPException(status_code=400, detail=str(parsed or INVALID_MEMORY_HOOK_COMMAND))
    return get_memory_service().handle_skill_reminder(parsed, _base_user_id(key_info))


# ===========================================================================
# M10 — operator/management endpoints
# ===========================================================================
@router.get("/v1/memory/recall", dependencies=_MEMORY_DEP)
async def memory_recall(
    request: Request,
    key_info: dict = Depends(require_api_key),
    query: str = Query(..., min_length=1, description="Recall query text"),
    repository: str | None = Query(None, description="Repository slug scoping the memory space"),
    cwd: str | None = Query(None, description="Workspace path used to derive the scope"),
):
    """Explicit hybrid recall for one scope (respects retrieval_enabled)."""
    cfg = get_settings().memory
    if not cfg.retrieval_enabled:
        return {"retrieved": False, "skip_reason": "disabled", "item_count": 0}
    result = get_memory_service().recall(
        query=query, base_user_id=_base_user_id(key_info), repository_slug=repository, cwd=cwd
    )
    return result.to_dict()


@router.get("/v1/memory/items", dependencies=_MEMORY_DEP)
async def memory_items(
    request: Request,
    key_info: dict = Depends(require_api_key),
    repository: str = Query(..., min_length=1, description="Repository slug scoping the memory space"),
    memory_type: str | None = Query(None, description="core|episodic|semantic|procedural|unclassified"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    """List stored memories for the caller's scoped memory space."""
    service = get_memory_service()
    base_user = _base_user_id(key_info)
    items = service.list_items(
        base_user_id=base_user,
        repository_slug=repository,
        memory_type=memory_type,
        limit=limit,
        offset=offset,
    )
    return {
        "items": items,
        "count": len(items),
        "total": service.count_items(base_user_id=base_user, repository_slug=repository),
    }


@router.delete("/v1/memory/items/{item_id}", dependencies=_MEMORY_DEP)
async def memory_delete_item(
    item_id: int,
    request: Request,
    key_info: dict = Depends(require_api_key),
    repository: str = Query(..., min_length=1, description="Repository slug scoping the memory space"),
):
    """Delete one memory item; scope-checked against the caller's space."""
    deleted = get_memory_service().delete_item(
        item_id=item_id, base_user_id=_base_user_id(key_info), repository_slug=repository
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="memory item not found in this scope")
    return {"deleted": True, "id": item_id}


# ===========================================================================
# M11 — workspace (repository) memory endpoints
# ===========================================================================
class WorkspaceUpdateRequest(BaseModel):
    path: str = Field(..., min_length=1, description="Workspace directory path")
    force: bool = Field(False, description="Rebuild even when the policy says skip")


@router.get("/v1/workspace-memory/status", dependencies=_MEMORY_DEP)
async def workspace_memory_status(
    request: Request,
    key_info: dict = Depends(require_api_key),
    path: str = Query(..., min_length=1, description="Workspace directory path"),
):
    """Status of the .moa_memory layer for one workspace."""
    cfg = get_settings().memory
    if not cfg.workspace_enabled:
        return {"enabled": False, "status": "disabled", "workspace": path}
    try:
        return get_workspace_memory_service().status(path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/v1/workspace-memory/update", dependencies=_MEMORY_DEP)
async def workspace_memory_update(
    body: WorkspaceUpdateRequest,
    request: Request,
    key_info: dict = Depends(require_api_key),
):
    """Policy-driven workspace memory rebuild (lock-guarded, idempotent)."""
    cfg = get_settings().memory
    if not cfg.workspace_enabled:
        return {"enabled": False, "status": "disabled", "workspace": body.path}
    try:
        return get_workspace_memory_service().update(body.path, force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
