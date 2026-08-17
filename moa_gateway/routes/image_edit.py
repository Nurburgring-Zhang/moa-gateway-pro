"""Image editing endpoints — OpenAI /v1/images/edits compatible."""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..config import get_settings  # noqa: F401

logger = logging.getLogger(__name__)
router = APIRouter(tags=["image_edit"], dependencies=[Depends(require_capability("image_gen"))])


def _label_mock(provider, response: Response) -> None:
    if provider.__class__.__name__.startswith("Mock"):
        for _k, _v in mock_headers(True).items():
            response.headers[_k] = _v


class ImageEditResponse(BaseModel):
    """Response for image edit/variation operations."""

    created: int
    data: list[dict[str, Any]]


@router.post("/v1/images/edits", response_model=ImageEditResponse)
async def edit_image(
    response: Response,
    image: UploadFile = File(..., description="Original image file (PNG/JPG)"),
    prompt: str = Form(..., description="Edit instruction"),
    mask: UploadFile | None = File(None, description="Mask image (transparent areas = edit regions)"),
    model: str = Form(default="auto", description="Provider: openai/sd/auto"),
    n: int = Form(default=1, ge=1, le=10),
    size: str = Form(default="1024x1024"),
    response_format: str = Form(default="url"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Edit an image given a prompt and optional mask.

    Compatible with OpenAI /v1/images/edits API.
    Supports DALL-E and Stable Diffusion backends.
    """
    # Read uploaded files
    image_bytes = await image.read()
    mask_bytes = await mask.read() if mask else None

    # Validate image
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty")
    if len(image_bytes) > 20 * 1024 * 1024:  # 20MB limit
        raise HTTPException(status_code=400, detail="Image file too large (max 20MB)")

    # Select provider
    provider = _get_image_edit_provider(model)
    _label_mock(provider, response)

    try:
        urls = await provider.edit_image(
            image=image_bytes,
            prompt=prompt,
            mask=mask_bytes,
            size=size,
            n=n,
        )

        data = [{"url": url, "revised_prompt": prompt} for url in urls]
        return ImageEditResponse(created=int(time.time()), data=data)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Image edit failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Image edit error: {str(e)}") from e


@router.post("/v1/images/variations", response_model=ImageEditResponse)
async def create_image_variation(
    response: Response,
    image: UploadFile = File(..., description="Source image file"),
    model: str = Form(default="auto", description="Provider: openai/sd/auto"),
    n: int = Form(default=1, ge=1, le=10),
    size: str = Form(default="1024x1024"),
    response_format: str = Form(default="url"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Create variations of an image.

    Compatible with OpenAI /v1/images/variations API.
    """
    image_bytes = await image.read()

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Image file is empty")
    if len(image_bytes) > 20 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Image file too large (max 20MB)")

    provider = _get_image_edit_provider(model)
    _label_mock(provider, response)

    try:
        urls = await provider.create_variation(image=image_bytes, n=n, size=size)
        data = [{"url": url} for url in urls]
        return ImageEditResponse(created=int(time.time()), data=data)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Image variation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Image variation error: {str(e)}") from e


def _get_image_edit_provider(model: str):
    """Get image edit provider by name.

    Falls back to a labeled MockImageEditProvider when no real backend is
    configured and mock.mode=explicit (audit F26) — keeping the multimodal
    pipeline runnable and honest instead of returning 502.
    """
    import os

    from ..providers.image_edit_provider import (
        DallEEditProvider,
        MockImageEditProvider,
        SDInpaintProvider,
    )

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    sd_base = os.environ.get("SD_API_BASE", "")

    if model == "auto":
        if openai_key:
            model = "openai"
        elif sd_base:
            model = "sd"
        else:
            model = "none"

    if model == "openai":
        api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com")
        return DallEEditProvider(api_key=openai_key, api_base=api_base)
    elif model == "sd":
        api_key = os.environ.get("SD_API_KEY", "")
        return SDInpaintProvider(api_key=api_key, api_base=sd_base or "http://127.0.0.1:7860")
    elif model == "none":
        try:
            from ..config import get_settings

            mock_mode = get_settings().mock.mode
        except Exception:
            mock_mode = "explicit"
        if mock_mode == "explicit":
            return MockImageEditProvider()
        # mock disabled: return the default SD backend (will surface a real error)
        return SDInpaintProvider(api_key="", api_base="http://127.0.0.1:7860")
    else:
        raise HTTPException(status_code=400, detail=f"Unknown image edit provider: {model}")
