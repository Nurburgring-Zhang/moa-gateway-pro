"""Video editing providers — Runway Gen-3/4, Kling expansion."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class VideoEditProvider(ABC):
    """Base class for video editing/generation providers."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base

    def _check_api_key(self) -> None:
        """Guard: raise if API key is not configured."""
        if not self.api_key:
            raise RuntimeError(f"API key not configured for {self.__class__.__name__}")

    @abstractmethod
    async def text_to_video(
        self,
        prompt: str,
        duration: int = 5,
        dimensions: str = "1280x720",
        fps: int = 24,
    ) -> str:
        """Create video from text. Returns task_id."""
        ...

    @abstractmethod
    async def image_to_video(
        self,
        image_url: str,
        prompt: str,
        duration: int = 5,
        dimensions: str = "1280x720",
    ) -> str:
        """Create video from image + prompt. Returns task_id."""
        ...

    @abstractmethod
    async def edit_video(
        self,
        video_url: str,
        prompt: str,
        operation: str = "style_transfer",
    ) -> str:
        """Edit existing video. Returns task_id."""
        ...

    @abstractmethod
    async def query_task(self, task_id: str) -> dict[str, Any]:
        """Query task status. Returns {status, progress, output_url, error}."""
        ...


class RunwayVideoProvider(VideoEditProvider):
    """Runway ML Gen-3/Gen-4 video provider."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.runwayml.com/v1"

    def _headers(self) -> dict[str, str]:
        self._check_api_key()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def text_to_video(
        self,
        prompt: str,
        duration: int = 5,
        dimensions: str = "1280x720",
        fps: int = 24,
    ) -> str:
        payload = {
            "model": "gen3",
            "prompt": prompt,
            "duration": duration,
            "dimensions": dimensions,
            "fps": fps,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/text_to_video",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["id"]  # type: ignore[no-any-return]

    async def image_to_video(
        self,
        image_url: str,
        prompt: str,
        duration: int = 5,
        dimensions: str = "1280x720",
    ) -> str:
        payload = {
            "model": "gen3",
            "prompt": prompt,
            "image": image_url,
            "duration": duration,
            "dimensions": dimensions,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/image_to_video",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["id"]  # type: ignore[no-any-return]

    async def edit_video(
        self,
        video_url: str,
        prompt: str,
        operation: str = "style_transfer",
    ) -> str:
        payload = {
            "model": "gen3",
            "prompt": prompt,
            "video": video_url,
            "operation": operation,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/video_edit",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data["id"]  # type: ignore[no-any-return]

    async def query_task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_base}/tasks/{task_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            status = data.get("status", "unknown").lower()
            result: dict[str, Any] = {
                "task_id": task_id,
                "status": "completed"
                if status == "succeeded"
                else ("failed" if status == "failed" else "processing"),
                "progress": data.get("progress", 0),
                "output_url": None,
                "error": None,
            }

            if status == "succeeded":
                output = data.get("output", [])
                result["output_url"] = output[0] if output else None
            elif status == "failed":
                result["error"] = data.get("error", "Unknown error")

            return result


class KlingVideoEditProvider(VideoEditProvider):
    """Kling (Kuaishou) video provider — extends existing KlingVideoProvider."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.klingai.com/v1"

    def _headers(self) -> dict[str, str]:
        self._check_api_key()
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def text_to_video(
        self,
        prompt: str,
        duration: int = 5,
        dimensions: str = "1280x720",
        fps: int = 24,
    ) -> str:
        w, h = dimensions.split("x")
        payload = {
            "prompt": prompt,
            "duration": str(duration),
            "aspect_ratio": f"{w}:{h}",
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/videos/text2video",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("task_id", data.get("task_id", ""))  # type: ignore[no-any-return]

    async def image_to_video(
        self,
        image_url: str,
        prompt: str,
        duration: int = 5,
        dimensions: str = "1280x720",
    ) -> str:
        payload = {
            "prompt": prompt,
            "image_url": image_url,
            "duration": str(duration),
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.api_base}/videos/img2video",
                json=payload,
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", {}).get("task_id", data.get("task_id", ""))  # type: ignore[no-any-return]

    async def edit_video(
        self,
        video_url: str,
        prompt: str,
        operation: str = "style_transfer",
    ) -> str:
        # Kling doesn't have dedicated video edit API yet, use text2video with reference
        return await self.text_to_video(prompt=f"{operation}: {prompt}", duration=5)

    async def query_task(self, task_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_base}/videos/tasks/{task_id}",
                headers=self._headers(),
            )
            resp.raise_for_status()
            data = resp.json()

            task_data = data.get("data", data)
            status_raw = task_data.get("task_status", "").lower()

            result: dict[str, Any] = {
                "task_id": task_id,
                "status": "completed"
                if status_raw == "succeed"
                else ("failed" if status_raw == "failed" else "processing"),
                "progress": task_data.get("progress", 0),
                "output_url": None,
                "error": None,
            }

            if result["status"] == "completed":
                videos = task_data.get("task_result", {}).get("videos", [])
                result["output_url"] = videos[0].get("url") if videos else None
            elif result["status"] == "failed":
                result["error"] = task_data.get("task_status_msg", "Unknown error")

            return result


class MockVideoProvider(VideoEditProvider):
    """Mock video provider — returns fake task_ids + placeholder output URLs
    labeled X-MOA-Mock. Used when no real video key is configured and
    mock.mode=explicit, so the video pipeline returns 200 instead of 503."""

    async def text_to_video(
        self, prompt: str, duration: int = 5, dimensions: str = "1280x720", fps: int = 24,
    ) -> str:
        logger.warning("[mock] video.text_to_video: no real provider configured; returning synthetic task")
        return f"mock-video-task-{abs(hash(prompt)) % 100000:05d}"

    async def image_to_video(
        self, image_url: str, prompt: str, duration: int = 5, dimensions: str = "1280x720",
    ) -> str:
        logger.warning("[mock] video.image_to_video: synthetic")
        return f"mock-video-task-img-{abs(hash(image_url)) % 100000:05d}"

    async def edit_video(
        self, video_url: str, prompt: str, operation: str = "style_transfer",
    ) -> str:
        logger.warning("[mock] video.edit_video: synthetic")
        return f"mock-video-edit-{abs(hash(video_url)) % 100000:05d}"

    async def query_task(self, task_id: str) -> dict[str, Any]:
        logger.warning("[mock] video.query_task: synthetic")
        return {
            "task_id": task_id,
            "status": "completed",
            "progress": 100,
            "output_url": "https://mock.example.com/video.mp4",
            "error": None,
            "mock": True,
        }
