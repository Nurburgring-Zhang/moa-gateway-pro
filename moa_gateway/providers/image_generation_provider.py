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

    @abstractmethod
    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        """Generate images from a text prompt. Returns list of URLs or base64 strings."""
        raise NotImplementedError


class DallECompatImageProvider(ImageGenerationProvider):
    """OpenAI DALL-E compatible image generation provider."""

    async def generate_image(self, prompt: str, size: str = "1024x1024", n: int = 1) -> list[str]:
        url = f"{self.api_base}/images/generations"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"prompt": prompt, "n": n, "size": size, "response_format": "url"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Image generation failed: HTTP {resp.status_code}: {resp.text[:500]}")
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
        task_id = await self._create_task(prompt, n)
        return await self._poll_task(task_id)

    async def _create_task(self, prompt: str, n: int) -> str:
        url = f"{self.api_base}/services/aigc/text2image/image-synthesis"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "X-DashScope-Async": "enable"}
        payload: dict[str, Any] = {"model": "wanx-v1", "input": {"prompt": prompt}, "parameters": {"n": n}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Wanx task creation failed: HTTP {resp.status_code}")
            data = resp.json()
        task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(f"Wanx: no task_id in response: {data}")
        return task_id

    async def _poll_task(self, task_id: str, interval: float = 2.0, max_wait: float = 120.0) -> list[str]:
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
