"""moa_gateway.providers.video_generation_provider -- Video generation providers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class VideoGenerationProvider(ABC):
    """Abstract base class for text-to-video generation providers."""

    def __init__(self, api_base: str, api_key: str, timeout: int = 300):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def create_video_task(self, prompt: str, duration: int = 5) -> str:
        """Create a video generation task, return task_id."""
        raise NotImplementedError

    @abstractmethod
    async def query_video_task(self, task_id: str) -> dict[str, Any]:
        """Query task status and result."""
        raise NotImplementedError


class KlingVideoProvider(VideoGenerationProvider):
    """Kuaishou Kling video generation provider."""

    async def create_video_task(self, prompt: str, duration: int = 5) -> str:
        url = f"{self.api_base}/videos/text2video"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"model": "kling-v1", "prompt": prompt, "duration": str(duration)}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Kling task failed: HTTP {resp.status_code}")
            data = resp.json()
        task_id = data.get("data", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(f"Kling: no task_id: {data}")
        return task_id

    async def query_video_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self.api_base}/videos/text2video/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Kling query failed: HTTP {resp.status_code}")
            data = resp.json()
        task_data = data.get("data", {})
        status = task_data.get("task_status", "UNKNOWN")
        video_url = None
        if task_data.get("task_result"):
            videos = task_data["task_result"].get("videos", [{}])
            if videos:
                video_url = videos[0].get("url")
        return {"status": status, "video_url": video_url, "error": task_data.get("task_error_msg") if status == "FAILED" else None}
