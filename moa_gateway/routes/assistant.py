"""OpenAI Assistant API compatible endpoints."""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..assistant.models import Assistant, Thread, Message, Run
from ..assistant.storage import get_storage
from ..assistant.executor import execute_run, submit_tool_outputs

logger = logging.getLogger(__name__)
router = APIRouter(tags=["assistants"])


# --- Request Models ---

class CreateAssistantRequest(BaseModel):
    model: str = "gpt-4o"
    name: Optional[str] = None
    description: Optional[str] = None
    instructions: Optional[str] = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    temperature: float = 1.0
    top_p: float = 1.0


class CreateThreadRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateMessageRequest(BaseModel):
    role: str = "user"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateRunRequest(BaseModel):
    assistant_id: str
    model: Optional[str] = None
    instructions: Optional[str] = None
    tools: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    temperature: Optional[float] = None
    stream: bool = False


class SubmitToolOutputsRequest(BaseModel):
    tool_outputs: list[dict[str, Any]]


# --- Assistant CRUD ---

@router.post("/v1/assistants")
async def create_assistant(
    req: CreateAssistantRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    assistant = Assistant(**req.model_dump(), owner_key_id=key_info.get("key_id", ""))
    storage = get_storage()
    storage.save_assistant(assistant)
    return assistant.model_dump()


@router.get("/v1/assistants/{assistant_id}")
async def get_assistant(
    assistant_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    assistant = storage.get_assistant(assistant_id)
    if not assistant or assistant.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Assistant not found")
    return assistant.model_dump()


@router.delete("/v1/assistants/{assistant_id}")
async def delete_assistant(
    assistant_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    assistant = storage.get_assistant(assistant_id)
    if not assistant or assistant.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Assistant not found")
    storage.delete_assistant(assistant_id)
    return {"id": assistant_id, "object": "assistant.deleted", "deleted": True}


@router.get("/v1/assistants")
async def list_assistants(
    limit: int = 20,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    assistants = storage.list_assistants(limit=limit)
    owner_id = key_info.get("key_id", "")
    filtered = [a for a in assistants if a.owner_key_id == owner_id]
    return {"object": "list", "data": [a.model_dump() for a in filtered]}


# --- Thread CRUD ---

@router.post("/v1/threads")
async def create_thread(
    req: CreateThreadRequest = CreateThreadRequest(),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = Thread(metadata=req.metadata, owner_key_id=key_info.get("key_id", ""))
    storage.save_thread(thread)

    # Add initial messages if provided
    for msg_data in req.messages:
        msg = Message(
            thread_id=thread.id,
            role=msg_data.get("role", "user"),
            content=[{"type": "text", "text": {"value": msg_data.get("content", "")}}],
        )
        storage.save_message(msg)

    return thread.model_dump()


@router.get("/v1/threads/{thread_id}")
async def get_thread(
    thread_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    return thread.model_dump()


@router.delete("/v1/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    storage.delete_thread(thread_id)
    return {"id": thread_id, "object": "thread.deleted", "deleted": True}


# --- Messages ---

@router.post("/v1/threads/{thread_id}/messages")
async def create_message(
    thread_id: str,
    req: CreateMessageRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")

    msg = Message(
        thread_id=thread_id,
        role=req.role,
        content=[{"type": "text", "text": {"value": req.content}}],
        metadata=req.metadata,
    )
    storage.save_message(msg)
    return msg.model_dump()


@router.get("/v1/threads/{thread_id}/messages")
async def list_messages(
    thread_id: str,
    limit: int = 100,
    order: str = "desc",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    messages = storage.list_messages(thread_id, limit=limit, order=order)
    return {"object": "list", "data": [m.model_dump() for m in messages]}


# --- Runs ---

@router.post("/v1/threads/{thread_id}/runs")
async def create_run(
    thread_id: str,
    req: CreateRunRequest,
    background_tasks: BackgroundTasks,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()

    # Validate thread exists and belongs to current user
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")

    # Validate assistant exists and belongs to current user
    assistant = storage.get_assistant(req.assistant_id)
    if not assistant or assistant.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Assistant not found")

    # Create run
    run = Run(
        thread_id=thread_id,
        assistant_id=req.assistant_id,
        model=req.model or assistant.model,
        instructions=req.instructions,
        tools=req.tools or assistant.tools,
        metadata=req.metadata,
    )
    storage.save_run(run)

    # Execute in background
    background_tasks.add_task(_run_in_background, run)

    return run.model_dump()


@router.get("/v1/threads/{thread_id}/runs/{run_id}")
async def get_run(
    thread_id: str,
    run_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    run = storage.get_run(run_id)
    if not run or run.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Run not found")
    return run.model_dump()


@router.get("/v1/threads/{thread_id}/runs")
async def list_runs(
    thread_id: str,
    limit: int = 20,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    runs = storage.list_runs(thread_id, limit=limit)
    return {"object": "list", "data": [r.model_dump() for r in runs]}


@router.post("/v1/threads/{thread_id}/runs/{run_id}/submit_tool_outputs")
async def submit_run_tool_outputs(
    thread_id: str,
    run_id: str,
    req: SubmitToolOutputsRequest,
    background_tasks: BackgroundTasks,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    run = storage.get_run(run_id)
    if not run or run.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status != "requires_action":
        raise HTTPException(status_code=400, detail="Run is not awaiting tool outputs")

    # Process in background
    background_tasks.add_task(_submit_outputs_bg, run, req.tool_outputs)

    run.status = "in_progress"
    return run.model_dump()


@router.post("/v1/threads/{thread_id}/runs/{run_id}/cancel")
async def cancel_run(
    thread_id: str,
    run_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    run = storage.get_run(run_id)
    if not run or run.thread_id != thread_id:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=400, detail=f"Cannot cancel run in {run.status} state")

    import time
    run.status = "cancelled"
    run.completed_at = int(time.time())
    storage.save_run(run)
    return run.model_dump()


# --- Run Steps ---

@router.get("/v1/threads/{thread_id}/runs/{run_id}/steps")
async def list_run_steps(
    thread_id: str,
    run_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    storage = get_storage()
    thread = storage.get_thread(thread_id)
    if not thread or thread.owner_key_id != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Thread not found")
    steps = storage.list_steps(run_id)
    return {"object": "list", "data": [s.model_dump() for s in steps]}


# --- Background Tasks ---

async def _run_in_background(run: Run):
    """Execute run in background."""
    try:
        await execute_run(run)
    except Exception as e:
        logger.error("Background run execution failed: %s", e)


async def _submit_outputs_bg(run: Run, tool_outputs: list[dict[str, Any]]):
    """Submit tool outputs in background."""
    try:
        await submit_tool_outputs(run, tool_outputs)
    except Exception as e:
        logger.error("Background tool output submission failed: %s", e)
