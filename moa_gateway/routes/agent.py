"""Agent Dispatch endpoints — /v1/agent/*."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_api_key
from ..req_models import *  # noqa: F403,F401

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


@router.get("/v1/agent/list")
async def agent_list(key_info: dict[str, Any] = Depends(require_api_key)):
    """List all registered service/method."""
    from ..services.dispatcher import get_dispatcher

    return {"agents": get_dispatcher().list_agents()}


@router.post("/v1/agent/dispatch")
async def agent_dispatch(
    body: CreateAgentDispatchRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Agent Dispatch — unified entry point to call any Service.method.

    Body: {"service": "moa", "method": "run_three_layer", "payload": {...}}
    Returns: ServiceResult envelope (ok, data, error, error_code, latency_ms, ...)
    """
    from ..services.dispatcher import get_dispatcher

    service_name = body.get("service", "")
    method_name = body.get("method", "")
    payload = body.get("payload") or {}
    if not service_name or not method_name:
        raise HTTPException(422, "service and method are required")
    result = await get_dispatcher().dispatch(service_name, method_name, payload)
    result.raise_if_failed()
    return result.to_dict()


@router.post("/v1/agent/dispatch_batch")
async def agent_dispatch_batch(
    body: CreateAgentDispatchBatchRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Batch dispatch, execute multiple service.method calls in parallel."""
    import time as _t

    from ..services.dispatcher import get_dispatcher

    calls = body.get("calls") or []
    if not isinstance(calls, list):
        raise HTTPException(422, "calls must be a list")
    t0 = _t.perf_counter()
    results = await get_dispatcher().dispatch_batch(calls)
    return {
        "results": [r.to_dict() for r in results],
        "latency_ms": (_t.perf_counter() - t0) * 1000.0,
    }


@router.get("/v1/agent/workflows")
async def agent_workflows(key_info: dict[str, Any] = Depends(require_api_key)):
    """List all registered workflow templates."""
    from ..services.dispatcher import get_dispatcher

    return {"workflows": get_dispatcher().list_workflows()}


@router.post("/v1/agent/workflow/register")
async def agent_workflow_register(
    body: CreateAgentWorkflowRegisterRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Dynamically register a workflow template."""
    from ..services.dispatcher import Workflow, WorkflowStep, get_dispatcher

    name = body.get("name", "")
    description = body.get("description", "")
    steps_data = body.get("steps") or []
    if not name or not isinstance(steps_data, list):
        raise HTTPException(422, "name and steps (list) are required")
    steps = []
    for s in steps_data:
        steps.append(
            WorkflowStep(
                name=s.get("name", ""),
                service=s.get("service", ""),
                method=s.get("method", ""),
                payload=s.get("payload") or {},
                depends_on=s.get("depends_on") or [],
                input_map=s.get("input_map") or {},
                optional=bool(s.get("optional", False)),
                description=s.get("description", ""),
            )
        )
    wf = Workflow(name=name, description=description, steps=steps)
    get_dispatcher().register_workflow(name, wf)
    return {"name": name, "steps_count": len(steps), "ok": True}


@router.post("/v1/agent/workflow/run")
async def agent_workflow_run(
    body: CreateAgentWorkflowRunRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Execute a workflow template — multi service.method DAG execution."""
    from ..services.dispatcher import get_dispatcher

    name = body.get("name", "")
    input_payload = body.get("input") or {}
    if not name:
        raise HTTPException(422, "name is required")
    wf_result = await get_dispatcher().run_workflow(name, input_payload)
    if not wf_result.ok:
        if "not found" in (wf_result.error or ""):
            raise HTTPException(404, wf_result.error)
        raise HTTPException(500, f"workflow failed: {wf_result.error}")
    return wf_result.to_dict()
