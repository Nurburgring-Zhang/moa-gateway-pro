"""3D model generation endpoints."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..utils.url_validator import validate_external_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["3d"])

# Task ownership mapping (task_id -> key_id) for access control
_task_owners: dict[str, str] = {}


# --- Request/Response Models ---


class ThreeDGenerateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    prompt: str = Field(default="", max_length=2000, description="Text prompt for 3D generation")
    image_url: Optional[str] = Field(None, description="Image URL for image-to-3D")
    model: str = Field(default="auto", description="Provider: auto/tripo3d/meshy")
    output_format: str = Field(default="glb", pattern=r"^(glb|obj|usd|fbx)$", description="Output format")
    model_version: Optional[str] = Field(None, description="Model version for provider")


class ThreeDCreateResponse(BaseModel):
    task_id: str
    status: str = "processing"
    message: str = "Task created successfully"


class ThreeDTaskResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    task_id: str
    status: str  # processing/completed/failed
    progress: int = 0
    model_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    error: Optional[str] = None


# --- Endpoints ---


@router.post("/v1/3d/generate", response_model=ThreeDCreateResponse)
async def generate_3d(
    req: ThreeDGenerateRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate 3D model from text or image.

    If image_url is provided, uses image-to-3D mode.
    Otherwise uses text-to-3D mode (prompt required).
    """
    if not req.image_url and not req.prompt:
        raise HTTPException(status_code=400, detail="Either prompt or image_url must be provided")

    # SSRF prevention: validate user-provided URLs
    if req.image_url:
        validate_external_url(req.image_url)

    provider = _get_3d_provider(req.model)

    try:
        if req.image_url:
            task_id = await provider.image_to_3d(
                image_url=req.image_url,
                output_format=req.output_format,
            )
        else:
            task_id = await provider.text_to_3d(
                prompt=req.prompt,
                model_version=req.model_version or "default",
                output_format=req.output_format,
            )

        # Record task ownership for access control
        if task_id:
            _task_owners[task_id] = key_info.get("key_id", "")

        return ThreeDCreateResponse(task_id=task_id, status="processing")
    except Exception as e:
        logger.error("3D generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"3D generation error: {str(e)}")


@router.get("/v1/3d/tasks/{task_id}", response_model=ThreeDTaskResponse)
async def get_3d_task(
    task_id: str,
    model: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Query 3D generation task status."""
    # Ownership check: deny access if task belongs to another user
    owner = _task_owners.get(task_id)
    if owner is not None and owner != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Task not found")

    provider = _get_3d_provider(model)

    try:
        result = await provider.query_task(task_id)
        return ThreeDTaskResponse(**result)
    except Exception as e:
        logger.error("3D task query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Task query error: {str(e)}")


# --- Helpers ---


def _get_3d_provider(model: str):
    """Get 3D generation provider by name."""
    from ..providers.threed_generation_provider import MeshyProvider, Tripo3DProvider

    if model == "auto":
        if os.environ.get("TRIPO3D_API_KEY"):
            model = "tripo3d"
        elif os.environ.get("MESHY_API_KEY"):
            model = "meshy"
        else:
            model = "tripo3d"  # Default

    if model == "tripo3d":
        return Tripo3DProvider(
            api_key=os.environ.get("TRIPO3D_API_KEY", ""),
            api_base=os.environ.get("TRIPO3D_API_BASE", ""),
        )
    elif model == "meshy":
        return MeshyProvider(
            api_key=os.environ.get("MESHY_API_KEY", ""),
            api_base=os.environ.get("MESHY_API_BASE", ""),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown 3D provider: {model}")
