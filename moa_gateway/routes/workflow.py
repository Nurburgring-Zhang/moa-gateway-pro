"""Workflow API routes — execute, list, and manage YAML workflows."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workflow"])


# --- Request/Response models ---


class WorkflowExecuteRequest(BaseModel):
    """Request to execute a workflow."""

    name: str = Field(..., description="Workflow name")
    context: dict[str, Any] = Field(
        default_factory=dict, description="Input variables for the workflow"
    )


class WorkflowCreateRequest(BaseModel):
    """Request to create/upload a new workflow."""

    name: str = Field(..., description="Workflow name")
    yaml_content: str = Field(..., description="Raw YAML content")


# --- Helper ---


def _get_loader():
    """Get a WorkflowLoader instance."""
    from moa_gateway.workflows.workflow_loader import WorkflowLoader

    return WorkflowLoader()


# --- Routes ---


@router.post("/v1/workflows/execute")
async def execute_workflow(req: WorkflowExecuteRequest) -> dict[str, Any]:
    """Execute a named workflow with the given context.

    The workflow must exist in the workflow directory. Execution
    follows topological order with parallel execution of independent steps.
    """
    loader = _get_loader()
    wf = loader.get_workflow(req.name)
    if wf is None:
        raise HTTPException(404, f"Workflow '{req.name}' not found")

    try:
        result = await wf.execute(req.context)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Workflow execution failed: %s", exc)
        raise HTTPException(502, f"Workflow execution failed: {exc}")

    return result


@router.get("/v1/workflows")
async def list_workflows() -> dict[str, Any]:
    """List all available workflows."""
    loader = _get_loader()
    workflows = loader.list_workflows()
    return {"workflows": workflows, "total": len(workflows)}


@router.get("/v1/workflows/{name}")
async def get_workflow(name: str) -> dict[str, Any]:
    """Get details of a specific workflow."""
    loader = _get_loader()
    wf = loader.get_workflow(name)
    if wf is None:
        raise HTTPException(404, f"Workflow '{name}' not found")

    return {
        "name": wf.name,
        "description": wf.description,
        "version": wf.version,
        "steps": [
            {
                "id": s.id,
                "type": s.type,
                "depends_on": s.depends_on,
                "outputs": s.outputs,
            }
            for s in wf.steps
        ],
    }


@router.post("/v1/workflows")
async def create_workflow(req: WorkflowCreateRequest) -> dict[str, Any]:
    """Create or upload a new workflow.

    The YAML content is validated before saving. If a workflow with
    the same name exists, it will be overwritten.
    """
    loader = _get_loader()
    try:
        path = loader.save_workflow(req.name, req.yaml_content)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid workflow YAML: {exc}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to save workflow: %s", exc)
        raise HTTPException(500, f"Failed to save workflow: {exc}")

    return {
        "name": req.name,
        "path": path,
        "saved": True,
    }
