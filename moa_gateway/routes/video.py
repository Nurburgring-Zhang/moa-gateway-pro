"""Video generation and editing endpoints."""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..utils.url_validator import validate_external_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["video"], dependencies=[Depends(require_capability("video"))])


def _apply_mock_label(provider: Any, response: Response) -> None:
    """Audit F24: label mock-provider responses with the X-MOA-Mock header."""
    if provider.__class__.__name__.startswith("Mock"):
        for _hk, _hv in mock_headers(True).items():
            response.headers[_hk] = _hv

# Task ownership mapping (task_id -> key_id) for access control
_task_owners: dict[str, str] = {}


# ─── Request/Response Models ───────────────────────────────────────────────


class VideoGenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    image_url: str | None = Field(None, description="Source image for img2video")
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
    output_url: str | None = None
    error: str | None = None


class VideoCreateResponse(BaseModel):
    task_id: str
    status: str = "processing"
    message: str = "Task created successfully"


# ─── Endpoints ─────────────────────────────────────────────────────────────


@router.post("/v1/video/generate", response_model=VideoCreateResponse)
async def generate_video(
    req: VideoGenerateRequest,
    response: Response,
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
    _apply_mock_label(provider, response)

    # Config error (503) distinct from upstream failure (502): no provider key.
    if not getattr(provider, "api_key", "") and not provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="Video provider not configured (set KLING_API_KEY or RUNWAY_API_KEY)",
        )

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
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Video generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Video generation error: {str(e)}") from e


@router.post("/v1/video/edit", response_model=VideoCreateResponse)
async def edit_video(
    req: VideoEditRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Edit an existing video with a prompt."""
    # v3.1.1 audit P2-2 fix: validate the caller-supplied video URL. The
    # generate path already validated image_url; edit was missing the check,
    # letting a key holder point the provider at internal addresses.
    if req.video_url:
        validate_external_url(req.video_url)

    provider = _get_video_provider(req.model)
    _apply_mock_label(provider, response)

    if not getattr(provider, "api_key", "") and not provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="Video provider not configured (set KLING_API_KEY or RUNWAY_API_KEY)",
        )

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
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Video edit failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Video edit error: {str(e)}") from e


@router.get("/v1/video/tasks/{task_id}", response_model=VideoTaskResponse)
async def get_video_task(
    task_id: str,
    response: Response,
    model: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Query video generation/editing task status."""
    # Ownership check: deny access if task belongs to another user
    owner = _task_owners.get(task_id)
    if owner is not None and owner != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Task not found")

    provider = _get_video_provider(model)

    # Audit F22 fix: a Mock provider has no real upstream task store, so the
    # ONLY tasks that genuinely exist are the ones created through this
    # gateway (recorded in _task_owners). Querying any other id must return
    # 404 — fabricating a "completed" task for an arbitrary id is false data.
    if provider.__class__.__name__.startswith("Mock") and task_id not in _task_owners:
        raise HTTPException(status_code=404, detail="Task not found")

    _apply_mock_label(provider, response)

    # If the provider has no API key configured, that's a config issue (503),
    # distinct from a missing task (404) or upstream failure (502).
    if not getattr(provider, "api_key", "") and not provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="Video provider not configured (set KLING_API_KEY or RUNWAY_API_KEY)",
        )

    try:
        result = await provider.query_task(task_id)
        return VideoTaskResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Video task query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Task query error: {str(e)}") from e


# ─── OpenAI-style generations endpoint (Kling video-generation wiring) ──────


class VideoGenerationsRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)
    platform: str = Field(
        default="auto",
        pattern=r"^(auto|kling|runway)$",
        description=(
            "Video generation platform: kling uses KlingVideoProvider "
            "(KLING_API_KEY), runway uses RunwayVideoProvider (RUNWAY_API_KEY), "
            "auto picks the first platform with a configured key"
        ),
    )
    duration: int = Field(default=5, ge=1, le=30, description="Duration in seconds")
    dimensions: str = Field(default="1280x720", pattern=r"^\d+x\d+$")
    fps: int = Field(default=24, ge=1, le=60)


@router.post("/v1/video/generations", response_model=VideoCreateResponse)
async def generate_video_v2(
    req: VideoGenerationsRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate video from text (platform-selectable).

    platform=kling routes to KlingVideoProvider (video_generation_provider),
    platform=runway to RunwayVideoProvider; both coexist with the legacy
    /v1/video/generate path. Without a real key the D6 mock policy applies:
    mock.mode=explicit → labeled synthetic task (200 + X-MOA-Mock),
    mock.mode=disabled → 503.
    """
    provider = _get_video_generation_provider(req.platform)
    _apply_mock_label(provider, response)

    # Config error (503) distinct from upstream failure (502): no provider key.
    if not getattr(provider, "api_key", "") and not provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="Video provider not configured (set KLING_API_KEY or RUNWAY_API_KEY)",
        )

    try:
        if hasattr(provider, "create_video_task"):
            # KlingVideoProvider (dedicated video-generation interface)
            task_id = await provider.create_video_task(
                prompt=req.prompt,
                duration=req.duration,
            )
        else:
            # RunwayVideoProvider (video-edit interface)
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
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Video generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Video generation error: {str(e)}") from e


@router.get("/v1/video/generations/tasks/{task_id}", response_model=VideoTaskResponse)
async def get_video_generation_task(
    task_id: str,
    response: Response,
    platform: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Query a /v1/video/generations task (pass the platform used to create it)."""
    if platform not in ("auto", "kling", "runway"):
        raise HTTPException(status_code=400, detail=f"Unknown video platform: {platform}")

    # Ownership check: deny access if task belongs to another user
    owner = _task_owners.get(task_id)
    if owner is not None and owner != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Task not found")

    provider = _get_video_generation_provider(platform)

    # Audit F22: mock providers have no upstream task store — only tasks
    # created through this gateway exist; anything else is 404.
    if provider.__class__.__name__.startswith("Mock") and task_id not in _task_owners:
        raise HTTPException(status_code=404, detail="Task not found")

    _apply_mock_label(provider, response)

    if not getattr(provider, "api_key", "") and not provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="Video provider not configured (set KLING_API_KEY or RUNWAY_API_KEY)",
        )

    try:
        if hasattr(provider, "query_video_task"):
            # KlingVideoProvider returns {status, video_url, error} with raw
            # upstream statuses (succeed/failed/...) — normalize them.
            raw = await provider.query_video_task(task_id)
            status_raw = str(raw.get("status", "")).lower()
            if status_raw in ("succeed", "succeeded", "success", "completed", "complete"):
                status = "completed"
            elif status_raw in ("failed", "error", "timeout"):
                status = "failed"
            else:
                status = "processing"
            result: dict[str, Any] = {
                "task_id": task_id,
                "status": status,
                "progress": 100 if status == "completed" else 0,
                "output_url": raw.get("video_url"),
                "error": raw.get("error"),
            }
        else:
            result = await provider.query_task(task_id)
        return VideoTaskResponse(**result)
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Video task query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Task query error: {str(e)}") from e


# ─── Helpers ───────────────────────────────────────────────────────────────


def _get_video_generation_provider(platform: str):
    """Provider for /v1/video/generations.

    kling → KlingVideoProvider (video_generation_provider.py, KLING_API_KEY),
    runway → RunwayVideoProvider (RUNWAY_API_KEY), auto → first available key
    (runway preferred, matching the legacy endpoint's order). No real key:
    mock.mode=explicit → MockVideoProvider (labeled 200); disabled → keyless
    real provider so the route raises a clean 503.
    """
    from ..config import get_settings
    from ..providers import is_mock_key
    from ..providers.video_edit_provider import MockVideoProvider, RunwayVideoProvider
    from ..providers.video_generation_provider import KlingVideoProvider

    def _clean(value: str) -> str:
        return "" if is_mock_key(value) else value

    runway_key = _clean(os.environ.get("RUNWAY_API_KEY", ""))
    kling_key = _clean(os.environ.get("KLING_API_KEY", ""))
    kling_base = os.environ.get("KLING_API_BASE", "") or "https://api.klingai.com/v1"

    if platform == "kling" and kling_key:
        return KlingVideoProvider(api_base=kling_base, api_key=kling_key)
    elif platform == "runway" and runway_key:
        return RunwayVideoProvider(api_key=runway_key, api_base=os.environ.get("RUNWAY_API_BASE", ""))
    elif platform == "auto" and runway_key:
        return RunwayVideoProvider(api_key=runway_key, api_base=os.environ.get("RUNWAY_API_BASE", ""))
    elif platform == "auto" and kling_key:
        return KlingVideoProvider(api_base=kling_base, api_key=kling_key)
    else:
        try:
            mock_mode = get_settings().mock.mode
        except Exception:
            mock_mode = "explicit"
        if mock_mode == "explicit":
            return MockVideoProvider()
        # disabled: keyless provider for the requested platform (route 503s)
        if platform == "kling":
            return KlingVideoProvider(api_base=kling_base, api_key="")
        return RunwayVideoProvider(api_key="", api_base=os.environ.get("RUNWAY_API_BASE", ""))


def _get_video_provider(model: str):
    """Get video edit provider by name. Falls back to MockVideoProvider when
    no real key is configured (mock.mode=explicit) so the pipeline returns 200."""
    from ..config import get_settings
    from ..providers.video_edit_provider import (
        KlingVideoEditProvider, MockVideoProvider, RunwayVideoProvider,
    )

    runway_key = os.environ.get("RUNWAY_API_KEY", "")
    kling_key = os.environ.get("KLING_API_KEY", "")

    if model == "runway" and runway_key:
        return RunwayVideoProvider(api_key=runway_key, api_base=os.environ.get("RUNWAY_API_BASE", ""))
    elif model == "kling" and kling_key:
        return KlingVideoEditProvider(api_key=kling_key, api_base=os.environ.get("KLING_API_BASE", ""))
    elif runway_key:
        return RunwayVideoProvider(api_key=runway_key, api_base=os.environ.get("RUNWAY_API_BASE", ""))
    elif kling_key:
        return KlingVideoEditProvider(api_key=kling_key, api_base=os.environ.get("KLING_API_BASE", ""))
    else:
        try:
            mock_mode = get_settings().mock.mode
        except Exception:
            mock_mode = "explicit"
        if mock_mode == "explicit":
            return MockVideoProvider()
        return RunwayVideoProvider(api_key="", api_base=os.environ.get("RUNWAY_API_BASE", ""))
