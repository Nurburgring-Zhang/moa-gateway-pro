"""Video generation and editing endpoints."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..utils.url_validator import validate_external_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["video"])

# Task ownership mapping (task_id -> key_id) for access control
_task_owners: dict[str, str] = {}


# ─── Request/Response Models ───────────────────────────────────────────────


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    image_url: Optional[str] = Field(None, description="Source image for img2video")
    model: str = Field(default="auto", description="Provider: runway/kling/auto")
    duration: int = Field(default=5, ge=1, le=30, description="Duration in seconds")
    dimensions: str = Field(default="1280x720", pattern=r"^\d+x\d+$")
    fps: int = Field(default=24, ge=1, le=60)


class VideoEditRequest(BaseModel):
    video_url: str = Field(..., description="Source video URL to edit")
    prompt: str = Field(..., min_length=1, max_length=2000)
    model: str = Field(default="auto")
    operation: str = Field(default="style_transfer", description="Edit operation type")


class VideoTaskResponse(BaseModel):
    task_id: str
    status: str  # processing/completed/failed
    progress: int = 0
    output_url: Optional[str] = None
    error: Optional[str] = None


class VideoCreateResponse(BaseModel):
    task_id: str
    status: str = "processing"
    message: str = "Task created successfully"


# ─── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/v1/video/generate", response_model=VideoCreateResponse)
async def generate_video(
    req: VideoGenerateRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate video from text or image.

    If image_url is provided, uses image-to-video mode.
    Otherwise uses text-to-video mode.
    """
    # SSRF prevention: validate user-provided URLs
    if req.image_url:
        validate_external_url(req.image_url)

    provider = _get_video_provider(req.model)

    try:
        if req.image_url:
            task_id = await provider.image_to_video(
                image_url=req.image_url,
                prompt=req.prompt,
                duration=req.duration,
                dimensions=req.dimensions,
            )
        else:
            task_id = await provider.text_to_video(
                prompt=req.prompt,
                duration=req.duration,
                dimensions=req.dimensions,
                fps=req.fps,
            )

        # Record task ownership for access control
        if task_id:
            _task_owners[task_id] = key_info.get("key_id", "")

        return VideoCreateResponse(task_id=task_id, status="processing")
    except Exception as e:
        logger.error("Video generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Video generation error: {str(e)}")


@router.post("/v1/video/edit", response_model=VideoCreateResponse)
async def edit_video(
    req: VideoEditRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Edit an existing video with a prompt."""
    provider = _get_video_provider(req.model)

    try:
        task_id = await provider.edit_video(
            video_url=req.video_url,
            prompt=req.prompt,
            operation=req.operation,
        )
        # Record task ownership for access control
        if task_id:
            _task_owners[task_id] = key_info.get("key_id", "")
        return VideoCreateResponse(task_id=task_id, status="processing")
    except Exception as e:
        logger.error("Video edit failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Video edit error: {str(e)}")


@router.get("/v1/video/tasks/{task_id}", response_model=VideoTaskResponse)
async def get_video_task(
    task_id: str,
    model: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Query video generation/editing task status."""
    # Ownership check: deny access if task belongs to another user
    owner = _task_owners.get(task_id)
    if owner is not None and owner != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Task not found")

    provider = _get_video_provider(model)

    try:
        result = await provider.query_task(task_id)
        return VideoTaskResponse(**result)
    except Exception as e:
        logger.error("Video task query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Task query error: {str(e)}")


# ─── Helpers ───────────────────────────────────────────────────────────────


def _get_video_provider(model: str):
    """Get video edit provider by name."""
    from ..providers.video_edit_provider import KlingVideoEditProvider, RunwayVideoProvider

    if model == "auto":
        if os.environ.get("RUNWAY_API_KEY"):
            model = "runway"
        elif os.environ.get("KLING_API_KEY"):
            model = "kling"
        else:
            model = "runway"  # Default

    if model == "runway":
        return RunwayVideoProvider(
            api_key=os.environ.get("RUNWAY_API_KEY", ""),
            api_base=os.environ.get("RUNWAY_API_BASE", ""),
        )
    elif model == "kling":
        return KlingVideoEditProvider(
            api_key=os.environ.get("KLING_API_KEY", ""),
            api_base=os.environ.get("KLING_API_BASE", ""),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unknown video provider: {model}")
