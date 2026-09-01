"""Vision and Image generation endpoints."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["vision"])


# --- Request/Response Models ------------------------------------------------


class ImageContent(BaseModel):
    """Single image input - URL or base64."""

    type: str = "image_url"  # "image_url" or "image_base64"
    url: str | None = None
    base64_data: str | None = None
    media_type: str = "image/png"


class VisionAnalyzeRequest(BaseModel):
    """Vision analysis request."""

    images: list[ImageContent] = Field(
        ..., min_length=1, description="One or more images to analyze"
    )
    prompt: str = Field(
        default="Describe this image in detail.", description="Analysis prompt"
    )
    model: str = Field(
        default="auto", description="Model to use (auto selects best available)"
    )
    max_tokens: int = Field(default=2000, ge=1, le=16384)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)


class VisionAnalyzeResponse(BaseModel):
    """Vision analysis response."""

    id: str
    model: str
    description: str
    usage: dict[str, int] = {}


class ImageGenerateRequest(BaseModel):
    """Image generation request."""

    prompt: str = Field(..., min_length=1, max_length=4000)
    model: str = Field(
        default="auto", description="Provider: cogview/wanx/openai/auto"
    )
    n: int = Field(default=1, ge=1, le=10)
    size: str = Field(default="1024x1024", pattern=r"^\d+x\d+$")
    response_format: str = Field(default="url", pattern=r"^(url|b64_json)$")


class ImageGenerateResponse(BaseModel):
    """Image generation response."""

    created: int
    data: list[dict[str, Any]]


# --- Vision Analysis Endpoint -----------------------------------------------


@router.post(
    "/v1/vision/analyze",
    response_model=VisionAnalyzeResponse,
    dependencies=[Depends(require_capability("vision"))],
)
async def vision_analyze(
    req: VisionAnalyzeRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Analyze images using vision-capable models.

    Accepts one or more images (URL or base64) with a prompt,
    routes to the best available vision model.
    """
    settings = get_settings()

    # Build multimodal content array for chat completions
    content_parts: list[dict[str, Any]] = []

    # Add text prompt
    content_parts.append({"type": "text", "text": req.prompt})

    # Add images
    for img in req.images:
        if img.url:
            content_parts.append(
                {"type": "image_url", "image_url": {"url": img.url}}
            )
        elif img.base64_data:
            data_url = f"data:{img.media_type};base64,{img.base64_data}"
            content_parts.append(
                {"type": "image_url", "image_url": {"url": data_url}}
            )
        else:
            raise HTTPException(
                status_code=400, detail="Each image must have url or base64_data"
            )

    # Select vision model
    model = req.model
    if model == "auto":
        model = _select_vision_model(settings)

    # Route through model pool (same pattern as chat endpoint)
    from ..model_pool import get_model_pool

    pool = get_model_pool()

    messages = [{"role": "user", "content": content_parts}]

    try:
        resp = await pool.call(
            model,
            messages,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            stream=False,
        )

        # Audit F24: label mock-backed vision responses with X-MOA-Mock header.
        if getattr(resp, "provider", "") == "mock":
            for _hk, _hv in mock_headers(True).items():
                response.headers[_hk] = _hv

        return VisionAnalyzeResponse(
            id=f"vision-{uuid.uuid4().hex[:12]}",
            model=resp.model or model,
            description=resp.content or "",
            usage={
                "prompt_tokens": resp.prompt_tokens,
                "completion_tokens": resp.completion_tokens,
                "total_tokens": resp.total_tokens,
            },
        )
    except Exception as e:
        logger.error("Vision analysis failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Vision model error: {e}") from e


# --- Image Generation Endpoint ----------------------------------------------


@router.post(
    "/v1/images/generations",
    response_model=ImageGenerateResponse,
    dependencies=[Depends(require_capability("image_gen"))],
)
async def generate_images(
    req: ImageGenerateRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate images from text prompts.

    Routes to the configured image generation provider
    (CogView, Wanx, DALL-E compatible).
    """
    settings = get_settings()

    # Determine provider
    platform = req.model if req.model != "auto" else _select_image_provider(settings)

    # Build provider
    try:
        from ..providers import build_multimodal_provider

        # P0 wiring fix: read the REAL credentials for the selected platform
        # from the environment (same pattern as image_edit.py). Hardcoding
        # api_key="" here previously forced the mock fallback even when a
        # real key was configured.
        api_key, api_base = _resolve_image_credentials(platform)
        provider = build_multimodal_provider(
            modality="image",
            platform_id=platform,
            api_key=api_key,
            api_base=api_base,
        )

        # No real key + mock.mode=explicit → MockImageProvider (200, labeled mock)
        is_mock_provider = provider is not None and provider.__class__.__name__.startswith("Mock")
        if provider is None or (not is_mock_provider and not getattr(provider, "api_key", "")):
            try:
                mock_mode = settings.mock.mode
            except Exception:
                mock_mode = "explicit"
            if mock_mode == "explicit":
                from ..providers.image_generation_provider import MockImageProvider
                provider = MockImageProvider()
            else:
                raise HTTPException(
                    status_code=503,
                    detail=f"No image generation provider available for platform: {platform}",
                )

        # Audit F24: label mock-generated images with the X-MOA-Mock header.
        if provider.__class__.__name__.startswith("Mock"):
            for _hk, _hv in mock_headers(True).items():
                response.headers[_hk] = _hv

        # Generate images
        image_urls = await provider.generate_image(  # type: ignore[attr-defined]
            prompt=req.prompt,
            size=req.size,
            n=req.n,
        )

        # Format response (OpenAI compatible)
        data = []
        for url in image_urls:
            if req.response_format == "b64_json":
                # Attempt to download and convert to base64
                try:
                    import base64

                    import httpx

                    async with httpx.AsyncClient(timeout=30.0) as client:
                        img_resp = await client.get(url)
                        img_resp.raise_for_status()
                        b64_data = base64.b64encode(img_resp.content).decode("ascii")
                    data.append({"b64_json": b64_data, "revised_prompt": req.prompt})
                except Exception as dl_err:
                    logger.warning("Failed to convert image URL to b64: %s", dl_err)
                    # Fallback: return URL with a warning header
                    data.append({"url": url, "revised_prompt": req.prompt})
            else:
                data.append({"url": url, "revised_prompt": req.prompt})

        return ImageGenerateResponse(
            created=int(time.time()),
            data=data,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Image generation failed: %s", e)
        raise HTTPException(
            status_code=502, detail=f"Image generation error: {e}"
        ) from e


# --- Helper Functions -------------------------------------------------------


def _select_vision_model(settings: Any) -> str:
    """Auto-select the best available vision model."""
    vision_models = [
        "gpt-4o",
        "gpt-4o-mini",
        "qwen-vl-max",
        "qwen-vl-plus",
        "glm-4v",
    ]

    try:
        from ..model_pool import get_model_pool

        pool = get_model_pool()
        available = set(pool.endpoints.keys())
        for model in vision_models:
            if model in available:
                return model
    except Exception:
        pass

    # Default fallback
    return "gpt-4o"


def _resolve_image_credentials(platform: str) -> tuple[str, str]:
    """Resolve real (api_key, api_base) for an image platform from the env.

    Mirrors the env-reading pattern of routes/image_edit.py. Placeholder
    keys ("your-...", "mock") are treated as absent (providers.is_mock_key)
    so the mock.mode policy downstream stays in charge of the no-key case.
    Unknown platforms get ("", "") — build_multimodal_provider returns None
    for them and the caller applies the mock.mode branch.
    """
    import os

    from ..providers import is_mock_key

    def _clean(value: str) -> str:
        return "" if is_mock_key(value) else value

    if platform in ("cogview", "zhipu"):
        key = _clean(os.environ.get("ZHIPU_API_KEY", ""))
        base = os.environ.get("ZHIPU_API_BASE", "") or os.environ.get("COGVIEW_API_BASE", "")
    elif platform == "openai":
        key = _clean(os.environ.get("OPENAI_API_KEY", ""))
        base = os.environ.get("OPENAI_API_BASE", "")
    elif platform == "wanx":
        key = _clean(os.environ.get("WANX_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", ""))
        base = os.environ.get("WANX_API_BASE", "") or os.environ.get("DASHSCOPE_API_BASE", "")
    else:
        key, base = "", ""
    return key, base


def _select_image_provider(settings: Any) -> str:
    """Auto-select image generation provider.

    Prefers a platform that actually has credentials configured so "auto"
    reaches a real provider whenever one is available; falls back to the
    first registered provider (mock.mode policy handles the no-key case).
    """
    from ..providers import PROVIDER_MODALITY_MAP

    for candidate in ("cogview", "wanx", "openai"):
        key, _base = _resolve_image_credentials(candidate)
        if key:
            return candidate

    image_providers = PROVIDER_MODALITY_MAP.get("image", [])
    if image_providers:
        return image_providers[0][0]  # First registered provider
    return "wanx"
