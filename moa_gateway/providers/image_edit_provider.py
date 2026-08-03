"""Image editing providers — DALL-E Edit / Stable Diffusion Inpaint."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


class ImageEditProvider(ABC):
    """Base class for image editing providers."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def edit_image(
        self,
        image: bytes,
        prompt: str,
        mask: Optional[bytes] = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[str]:
        """Edit an image and return list of result URLs."""
        ...

    @abstractmethod
    async def create_variation(
        self,
        image: bytes,
        n: int = 1,
        size: str = "1024x1024",
    ) -> list[str]:
        """Create variations of an image."""
        ...


class DallEEditProvider(ImageEditProvider):
    """OpenAI DALL-E image editing provider."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.openai.com"

    async def edit_image(
        self,
        image: bytes,
        prompt: str,
        mask: Optional[bytes] = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[str]:
        """Edit image via OpenAI /v1/images/edits API."""
        url = f"{self.api_base}/v1/images/edits"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        files: dict[str, Any] = {
            "image": ("image.png", image, "image/png"),
            "prompt": (None, prompt),
            "size": (None, size),
            "n": (None, str(n)),
            "response_format": (None, "url"),
        }
        if mask:
            files["mask"] = ("mask.png", mask, "image/png")

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, files=files)
            resp.raise_for_status()
            data = resp.json()
            return [item["url"] for item in data.get("data", [])]

    async def create_variation(
        self,
        image: bytes,
        n: int = 1,
        size: str = "1024x1024",
    ) -> list[str]:
        """Create variations via OpenAI /v1/images/variations API."""
        url = f"{self.api_base}/v1/images/variations"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        files = {
            "image": ("image.png", image, "image/png"),
            "n": (None, str(n)),
            "size": (None, size),
            "response_format": (None, "url"),
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, files=files)
            resp.raise_for_status()
            data = resp.json()
            return [item["url"] for item in data.get("data", [])]


class SDInpaintProvider(ImageEditProvider):
    """Stable Diffusion / ComfyUI / A1111 inpaint provider."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "http://127.0.0.1:7860"  # Default A1111 port

    async def edit_image(
        self,
        image: bytes,
        prompt: str,
        mask: Optional[bytes] = None,
        size: str = "1024x1024",
        n: int = 1,
    ) -> list[str]:
        """Edit image via Stable Diffusion img2img/inpaint API."""
        import base64

        url = f"{self.api_base}/sdapi/v1/img2img"

        # Encode images to base64
        img_b64 = base64.b64encode(image).decode()

        payload: dict[str, Any] = {
            "init_images": [img_b64],
            "prompt": prompt,
            "negative_prompt": "blurry, low quality, distorted",
            "steps": 30,
            "cfg_scale": 7.5,
            "denoising_strength": 0.75,
            "batch_size": n,
            "width": int(size.split("x")[0]),
            "height": int(size.split("x")[1]),
        }

        if mask:
            mask_b64 = base64.b64encode(mask).decode()
            payload["mask"] = mask_b64
            payload["inpainting_fill"] = 1  # original
            payload["inpaint_full_res"] = True

        headers: dict[str, str] = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

            # A1111 returns base64 images, convert to data URLs
            result_urls = []
            for img_data in data.get("images", []):
                result_urls.append(f"data:image/png;base64,{img_data}")

            return result_urls[:n]

    async def create_variation(
        self,
        image: bytes,
        n: int = 1,
        size: str = "1024x1024",
    ) -> list[str]:
        """Create variations using img2img with low denoising."""
        return await self.edit_image(
            image=image,
            prompt="high quality image variation",
            mask=None,
            size=size,
            n=n,
        )
