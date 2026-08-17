"""3D model generation endpoints."""
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
router = APIRouter(tags=["3d"], dependencies=[Depends(require_capability("three_d"))])


def _apply_mock_label(provider: Any, response: Response) -> None:
    """Audit F24: label mock-provider responses with the X-MOA-Mock header."""
    if provider.__class__.__name__.startswith("Mock"):
        for _hk, _hv in mock_headers(True).items():
            response.headers[_hk] = _hv

# Task ownership mapping (task_id -> key_id) for access control
_task_owners: dict[str, str] = {}


# --- Request/Response Models ---


class ThreeDGenerateRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    prompt: str = Field(default="", max_length=2000, description="Text prompt for 3D generation")
    image_url: str | None = Field(None, description="Image URL for image-to-3D")
    model: str = Field(default="auto", description="Provider: auto/tripo3d/meshy")
    output_format: str = Field(default="glb", pattern=r"^(glb|obj|usd|fbx)$", description="Output format")
    model_version: str | None = Field(None, description="Model version for provider")


class ThreeDCreateResponse(BaseModel):
    task_id: str
    status: str = "processing"
    message: str = "Task created successfully"


class ThreeDTaskResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    task_id: str
    status: str  # processing/completed/failed
    progress: int = 0
    model_url: str | None = None
    thumbnail_url: str | None = None
    error: str | None = None


# --- Endpoints ---


@router.post("/v1/3d/generate", response_model=ThreeDCreateResponse)
async def generate_3d(
    req: ThreeDGenerateRequest,
    response: Response,
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
    _apply_mock_label(provider, response)

    # Config error (503) only for NON-mock providers (mock providers intentionally
    # have empty api_key but return synthetic 200 responses).
    is_mock = provider.__class__.__name__.startswith("Mock")
    if not is_mock and not getattr(provider, "api_key", ""):
        raise HTTPException(
            status_code=503,
            detail="3D generation provider not configured (set TRIPO3D_API_KEY or MESHY_API_KEY)",
        )

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
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("3D generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"3D generation error: {str(e)}") from e


@router.get("/v1/3d/tasks/{task_id}", response_model=ThreeDTaskResponse)
async def get_3d_task(
    task_id: str,
    response: Response,
    model: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Query 3D generation task status."""
    # Ownership check: deny access if task belongs to another user
    owner = _task_owners.get(task_id)
    if owner is not None and owner != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Task not found")

    provider = _get_3d_provider(model)

    # Audit F22 fix: a Mock provider has no real upstream task store, so the
    # ONLY tasks that genuinely exist are the ones created through this
    # gateway (recorded in _task_owners). Querying any other id must return
    # 404 — fabricating a "completed" task for an arbitrary id is false data.
    if provider.__class__.__name__.startswith("Mock") and task_id not in _task_owners:
        raise HTTPException(status_code=404, detail="Task not found")

    _apply_mock_label(provider, response)

    # If the provider has no API key configured, that's a config issue (503),
    # not a per-task error — distinct from a missing task (404) or upstream failure (502).
    # Skip for Mock providers (they intentionally have empty api_key but return 200).
    if not getattr(provider, "api_key", "") and not provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="3D generation provider not configured (set TRIPO3D_API_KEY or MESHY_API_KEY)",
        )

    try:
        result = await provider.query_task(task_id)
        return ThreeDTaskResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("3D task query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Task query error: {str(e)}") from e


# --- Helpers ---


def _get_3d_provider(model: str):
    """Get 3D generation provider by name. Falls back to Mock3DProvider when
    no real key is configured (mock.mode=explicit) so the pipeline returns 200."""
    from ..config import get_settings
    from ..providers.threed_generation_provider import (
        MeshyProvider, Mock3DProvider, Tripo3DProvider,
    )

    tripo_key = os.environ.get("TRIPO3D_API_KEY", "")
    meshy_key = os.environ.get("MESHY_API_KEY", "")

    if model == "tripo3d" and tripo_key:
        return Tripo3DProvider(api_key=tripo_key, api_base=os.environ.get("TRIPO3D_API_BASE", ""))
    elif model == "meshy" and meshy_key:
        return MeshyProvider(api_key=meshy_key, api_base=os.environ.get("MESHY_API_BASE", ""))
    elif tripo_key:
        return Tripo3DProvider(api_key=tripo_key, api_base=os.environ.get("TRIPO3D_API_BASE", ""))
    elif meshy_key:
        return MeshyProvider(api_key=meshy_key, api_base=os.environ.get("MESHY_API_BASE", ""))
    else:
        # No real key — mock provider when explicit mock mode.
        try:
            mock_mode = get_settings().mock.mode
        except Exception:
            mock_mode = "explicit"
        if mock_mode == "explicit":
            return Mock3DProvider()
        # disabled: default real provider (will 503 on call)
        return Tripo3DProvider(api_key="", api_base=os.environ.get("TRIPO3D_API_BASE", ""))
