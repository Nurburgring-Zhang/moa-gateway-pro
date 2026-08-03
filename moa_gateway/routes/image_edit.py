"""Image editing endpoints — OpenAI /v1/images/edits compatible."""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from ..auth import require_api_key
from ..config import get_settings  # noqa: F401

logger = logging.getLogger(__name__)
router = APIRouter(tags=["image_edit"])


class ImageEditResponse(BaseModel):
    """Response for image edit/variation operations."""

    created: int
    data: list[dict[str, Any]]


@router.post("/v1/images/edits", response_model=ImageEditResponse)
async def edit_image(
    image: UploadFile = File(..., description="Original image file (PNG/JPG)"),
    prompt: str = Form(..., description="Edit instruction"),
    mask: Optional[UploadFile] = File(None, description="Mask image (transparent areas = edit regions)"),
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
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}")
    except Exception as e:
        logger.error("Image edit failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Image edit error: {str(e)}")


@router.post("/v1/images/variations", response_model=ImageEditResponse)
async def create_image_variation(
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

    try:
        urls = await provider.create_variation(image=image_bytes, n=n, size=size)
        data = [{"url": url} for url in urls]
        return ImageEditResponse(created=int(time.time()), data=data)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}")
    except Exception as e:
        logger.error("Image variation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Image variation error: {str(e)}")


def _get_image_edit_provider(model: str):
    """Get image edit provider by name."""
    import os

    from ..providers.image_edit_provider import DallEEditProvider, SDInpaintProvider

    if model == "auto":
        # Prefer OpenAI if key available, else SD
        if os.environ.get("OPENAI_API_KEY"):
            model = "openai"
        else:
            model = "sd"

    if model == "openai":
        api_key = os.environ.get("OPENAI_API_KEY", "")
        api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com")
        return DallEEditProvider(api_key=api_key, api_base=api_base)
    elif model == "sd":
        api_base = os.environ.get("SD_API_BASE", "http://127.0.0.1:7860")
        api_key = os.environ.get("SD_API_KEY", "")
        return SDInpaintProvider(api_key=api_key, api_base=api_base)
    else:
        raise HTTPException(status_code=400, detail=f"Unknown image edit provider: {model}")
