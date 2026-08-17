"""moa_gateway.providers.image_generation_provider -- Image generation providers."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ImageGenerationProvider(ABC):
    """Abstract base class for text-to-image generation providers."""

    def __init__(self, api_base: str, api_key: str, timeout: int = 120):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _check_api_key(self) -> None:
        """Guard: raise if API key is not configured."""
        if not self.api_key:
            raise RuntimeError(f"API key not configured for {self.__class__.__name__}")

    @abstractmethod
    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        """Generate images from a text prompt. Returns list of URLs or base64 strings."""
        raise NotImplementedError


class DallECompatImageProvider(ImageGenerationProvider):
    """OpenAI DALL-E compatible image generation provider."""

    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        self._check_api_key()
        url = f"{self.api_base}/images/generations"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"prompt": prompt, "n": n, "size": size, "response_format": "url"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(
                    f"Image generation failed: HTTP {resp.status_code}: {resp.text[:500]}"
                )
            data = resp.json()
        images: list[str] = []
        for item in data.get("data", []):
            if "url" in item:
                images.append(item["url"])
            elif "b64_json" in item:
                images.append(item["b64_json"])
        return images


class WanxImageProvider(ImageGenerationProvider):
    """Tongyi Wanxiang (Wanx) image generation provider. Uses async task format."""

    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        self._check_api_key()
        task_id = await self._create_task(prompt, n)
        return await self._poll_task(task_id)

    async def _create_task(self, prompt: str, n: int) -> str:
        self._check_api_key()
        url = f"{self.api_base}/services/aigc/text2image/image-synthesis"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "X-DashScope-Async": "enable",
        }
        payload: dict[str, Any] = {
            "model": "wanx-v1",
            "input": {"prompt": prompt},
            "parameters": {"n": n},
        }
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Wanx task creation failed: HTTP {resp.status_code}")
            data = resp.json()
        task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(f"Wanx: no task_id in response: {data}")
        return task_id  # type: ignore[no-any-return]

    async def _poll_task(
        self, task_id: str, interval: float = 2.0, max_wait: float = 120.0
    ) -> list[str]:
        url = f"{self.api_base}/tasks/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        start = time.time()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            while time.time() - start < max_wait:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    raise RuntimeError(f"Wanx poll failed: HTTP {resp.status_code}")
                data = resp.json()
                status = data.get("output", {}).get("task_status", "")
                if status == "SUCCEEDED":
                    results = data.get("output", {}).get("results", [])
                    return [r.get("url", "") for r in results if r.get("url")]
                if status == "FAILED":
                    raise RuntimeError(f"Wanx task failed: {data}")
                await asyncio.sleep(interval)
        raise TimeoutError(f"Wanx task {task_id} timed out after {max_wait}s")


class CogViewImageProvider(ImageGenerationProvider):
    """Zhipu CogView image generation provider."""

    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        self._check_api_key()
        url = f"{self.api_base}/images/generations"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"model": "cogview-3", "prompt": prompt, "n": n}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"CogView failed: HTTP {resp.status_code}: {resp.text[:500]}")
            data = resp.json()
        images: list[str] = []
        for item in data.get("data", []):
            if "url" in item:
                images.append(item["url"])
            elif "b64_json" in item:
                images.append(item["b64_json"])
        return images


class MockImageProvider(ImageGenerationProvider):
    """Mock image generation provider — returns placeholder image URLs labeled
    X-MOA-Mock. Used when no real image key is configured and mock.mode=explicit,
    so /v1/images/generations returns 200 instead of 503."""

    def __init__(self):
        super().__init__(api_base="https://mock.example.com", api_key="")

    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        logger.warning("[mock] image.generate_image: no real provider configured; returning synthetic URLs")
        return [f"https://mock.example.com/generated-{i}.png?size={size}" for i in range(max(1, min(n, 10)))]

    async def edit_image(self, image: bytes, prompt: str, mask: bytes | None = None, size: str = "1024x1024") -> list[str]:
        logger.warning("[mock] image.edit_image: synthetic")
        return ["https://mock.example.com/edited.png"]

    async def create_variation(self, image: bytes, n: int = 1, size: str = "1024x1024") -> list[str]:
        logger.warning("[mock] image.create_variation: synthetic")
        return [f"https://mock.example.com/variation-{i}.png" for i in range(max(1, min(n, 10)))]
