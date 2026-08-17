"""3D model generation providers - Tripo3D (primary), Meshy (fallback)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ThreeDGenerationProvider(ABC):
    """Base class for 3D model generation providers."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base

    def _check_api_key(self) -> None:
        """Guard: raise if API key is not configured."""
        if not self.api_key:
            raise RuntimeError(f"API key not configured for {self.__class__.__name__}")

    @abstractmethod
    async def text_to_3d(
        self,
        prompt: str,
        model_version: str = "default",
        output_format: str = "glb",
    ) -> str:
        """Generate 3D model from text prompt. Returns task_id."""
        ...

    @abstractmethod
    async def image_to_3d(
        self,
        image_url: str,
        output_format: str = "glb",
    ) -> str:
        """Generate 3D model from image. Returns task_id."""
        ...

    @abstractmethod
    async def query_task(self, task_id: str) -> dict[str, Any]:
        """Query task status.

        Returns:
            dict with keys: task_id, status, progress, model_url, thumbnail_url, error
        """
        ...


class Tripo3DProvider(ThreeDGenerationProvider):
    """Tripo3D API provider - primary 3D generation backend.

    Supports output formats: glb, obj, usd, fbx.
    """

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.tripo3d.ai/v2/openapi"

    def _headers(self) -> dict[str, str]:
        self._check_api_key()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def text_to_3d(
        self,
        prompt: str,
        model_version: str = "default",
        output_format: str = "glb",
    ) -> str:
        payload: dict[str, Any] = {
            "type": "text_to_model",
            "prompt": prompt,
            "model_version": model_version,
        }
        if output_format != "glb":
            payload["output_format"] = output_format

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/task",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"]["task_id"]  # type: ignore[no-any-return]

    async def image_to_3d(
        self,
        image_url: str,
        output_format: str = "glb",
    ) -> str:
        payload: dict[str, Any] = {
            "type": "image_to_model",
            "file": {"url": image_url},
        }
        if output_format != "glb":
            payload["output_format"] = output_format

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/task",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"]["task_id"]  # type: ignore[no-any-return]

    async def query_task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_base}/task/{task_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

        task_data = data.get("data", {})
        status_raw = task_data.get("status", "unknown").lower()

        # Map Tripo3D statuses: queued/running/success/failed
        if status_raw == "success":
            mapped_status = "completed"
        elif status_raw == "failed":
            mapped_status = "failed"
        else:
            mapped_status = "processing"

        output = task_data.get("output", {})
        result: dict[str, Any] = {
            "task_id": task_id,
            "status": mapped_status,
            "progress": task_data.get("progress", 0),
            "model_url": output.get("model") if output else None,
            "thumbnail_url": output.get("rendered_image") if output else None,
            "error": None,
        }

        if mapped_status == "failed":
            result["error"] = task_data.get("message", "Unknown error")

        return result


class MeshyProvider(ThreeDGenerationProvider):
    """Meshy API provider - fallback 3D generation backend.

    Supports output formats: glb, obj, fbx.
    """

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.meshy.ai/v2"

    def _headers(self) -> dict[str, str]:
        self._check_api_key()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def text_to_3d(
        self,
        prompt: str,
        model_version: str = "default",
        output_format: str = "glb",
    ) -> str:
        payload: dict[str, Any] = {
            "mode": "preview",
            "prompt": prompt,
            "art_style": "realistic",
            "negative_prompt": "ugly, distorted",
        }
        if output_format != "glb":
            payload["output_format"] = output_format

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/text-to-3d",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["result"]  # type: ignore[no-any-return]

    async def image_to_3d(
        self,
        image_url: str,
        output_format: str = "glb",
    ) -> str:
        payload: dict[str, Any] = {
            "image_url": image_url,
        }
        if output_format != "glb":
            payload["output_format"] = output_format

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/image-to-3d",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["result"]  # type: ignore[no-any-return]

    async def query_task(self, task_id: str) -> dict[str, Any]:
        # Meshy uses different endpoints for text-to-3d and image-to-3d tasks
        # Try text-to-3d first, fallback to image-to-3d
        task_data: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_base}/text-to-3d/{task_id}",
                headers=self._headers(),
            )
            if resp.status_code == 404:
                resp = await client.get(
                    f"{self.api_base}/image-to-3d/{task_id}",
                    headers=self._headers(),
                )
            resp.raise_for_status()
            task_data = resp.json()

        status_raw = task_data.get("status", "PENDING").upper()

        # Map Meshy statuses: PENDING/IN_PROGRESS/SUCCEEDED/FAILED
        if status_raw == "SUCCEEDED":
            mapped_status = "completed"
        elif status_raw == "FAILED":
            mapped_status = "failed"
        else:
            mapped_status = "processing"

        model_urls = task_data.get("model_urls", {})
        model_url = model_urls.get("glb") or model_urls.get("obj") or None

        result: dict[str, Any] = {
            "task_id": task_id,
            "status": mapped_status,
            "progress": task_data.get("progress", 0),
            "model_url": model_url,
            "thumbnail_url": task_data.get("thumbnail_url"),
            "error": None,
        }

        if mapped_status == "failed":
            result["error"] = task_data.get("message", "Unknown error")

        return result


class Mock3DProvider(ThreeDGenerationProvider):
    """Mock 3D generation provider — returns fake task_ids + placeholder model
    URLs labeled X-MOA-Mock. Used when no real 3D key is configured and
    mock.mode=explicit, so the 3D pipeline returns 200 instead of 503."""

    async def text_to_3d(
        self, prompt: str, model_version: str = "default", output_format: str = "glb",
    ) -> str:
        logger.warning("[mock] 3d.text_to_3d: no real provider configured; returning synthetic task")
        return f"mock-3d-task-{abs(hash(prompt)) % 100000:05d}"

    async def image_to_3d(self, image_url: str, output_format: str = "glb") -> str:
        logger.warning("[mock] 3d.image_to_3d: synthetic")
        return f"mock-3d-task-img-{abs(hash(image_url)) % 100000:05d}"

    async def query_task(self, task_id: str) -> dict[str, Any]:
        logger.warning("[mock] 3d.query_task: synthetic")
        return {
            "task_id": task_id,
            "status": "completed",
            "progress": 100,
            "model_url": "https://mock.example.com/model.glb",
            "thumbnail_url": "https://mock.example.com/thumbnail.png",
            "error": None,
            "mock": True,
        }
