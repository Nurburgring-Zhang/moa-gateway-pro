"""Agent TaskBoard CRUD endpoints — /v1/agent/tasks/* (D13).

Backed by the persistent :class:`SqliteTaskBoard`, so tasks survive gateway
restarts (agent_tasks table) instead of living only in process memory.

Design note (intentional): the board is the *shared* coordination surface
between sub-agents (see capability/subagent_comms), so any authenticated
gateway key may read/update tasks — mirroring the other /v1/agent/* routes.
Per-key ownership isolation deliberately does not apply here.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["agent-tasks"])

_VALID_STATUS = ("pending", "in_progress", "completed", "failed")


class CreateAgentTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    assignee_session: str | None = None
    parent_task_id: str | None = None


class UpdateAgentTaskRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    assignee_session: str | None = None
    status: str | None = None


def _task_dict(task: Any) -> dict[str, Any]:
    return task.to_dict()  # type: ignore[no-any-return]


@router.get("/v1/agent/tasks")
async def list_agent_tasks(
    status: str | None = None,
    assignee: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """List tasks with optional status / assignee filters.

    Paginated: ``has_more`` tells the client the board continues past this
    page (the backing store caps a single page, it never silently truncates).
    """
    from ..capability.subagent_comms import get_task_board

    if status is not None and status not in _VALID_STATUS:
        raise HTTPException(422, f"status must be one of {_VALID_STATUS}")
    board = get_task_board()
    tasks, has_more = board.list_page(
        status=status, assignee=assignee, limit=limit, offset=offset
    )
    return {
        "object": "list",
        "data": [_task_dict(t) for t in tasks],
        "has_more": has_more,
        "total": board.count_tasks(status=status, assignee=assignee),
        "limit": limit,
        "offset": offset,
    }


@router.get("/v1/agent/tasks/{task_id}")
async def get_agent_task(
    task_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    from ..capability.subagent_comms import get_task_board

    task = get_task_board().get_task(task_id)
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return _task_dict(task)


@router.post("/v1/agent/tasks")
async def create_agent_task(
    req: CreateAgentTaskRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    from ..capability.subagent_comms import get_task_board

    board = get_task_board()
    try:
        task_id = board.create_task(
            title=req.title,
            assignee=req.assignee_session,
            parent=req.parent_task_id,
        )
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    task = board.get_task(task_id)
    if task is None:
        raise HTTPException(500, "task vanished right after creation")
    return _task_dict(task)


@router.put("/v1/agent/tasks/{task_id}")
async def update_agent_task(
    task_id: str,
    req: UpdateAgentTaskRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    from ..capability.subagent_comms import get_task_board

    board = get_task_board()
    # "assignee_session": null present in the body == explicit unassign;
    # key absent == leave unchanged (None alone cannot distinguish the two).
    clear_assignee = (
        "assignee_session" in req.model_fields_set and req.assignee_session is None
    )
    try:
        board.update_task(
            task_id,
            title=req.title,
            assignee=req.assignee_session,
            status=req.status,
            clear_assignee=clear_assignee,
        )
    except KeyError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    task = board.get_task(task_id)
    if task is None:
        raise HTTPException(404, f"task not found: {task_id}")
    return _task_dict(task)


@router.delete("/v1/agent/tasks/{task_id}")
async def delete_agent_task(
    task_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    from ..capability.subagent_comms import get_task_board

    if not get_task_board().delete_task(task_id):
        raise HTTPException(404, f"task not found: {task_id}")
    return {"id": task_id, "object": "agent_task.deleted", "deleted": True}
