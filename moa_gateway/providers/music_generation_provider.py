"""moa_gateway.providers.music_generation_provider -- Music generation providers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class MusicGenerationProvider(ABC):
    """Abstract base class for text-to-music generation providers."""

    def __init__(self, api_base: str, api_key: str, timeout: int = 300):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def create_music_task(self, prompt: str, duration: int = 30) -> str:
        """Create a music generation task, return task_id."""
        raise NotImplementedError

    @abstractmethod
    async def query_music_task(self, task_id: str) -> dict[str, Any]:
        """Query music task status."""
        raise NotImplementedError


class MiniMaxMusicProvider(MusicGenerationProvider):
    """MiniMax music generation provider."""

    async def create_music_task(self, prompt: str, duration: int = 30) -> str:
        url = f"{self.api_base}/music_generation"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"model": "music-01", "prompt": prompt, "duration": duration}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"MiniMax music failed: HTTP {resp.status_code}")
            data = resp.json()
        task_id = data.get("data", {}).get("task_id", "") or data.get("task_id", "")
        if not task_id:
            raise RuntimeError(f"MiniMax music: no task_id: {data}")
        return task_id

    async def query_music_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self.api_base}/music_generation/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"MiniMax music query failed: HTTP {resp.status_code}")
            data = resp.json()
        task_data = data.get("data", data)
        status = task_data.get("status", "UNKNOWN")
        music_url = task_data.get("music_url") or task_data.get("audio_url")
        return {"status": status, "music_url": music_url, "error": task_data.get("error") if status == "FAILED" else None}


class TiangongMusicProvider(MusicGenerationProvider):
    """Tiangong SkyMusic generation provider."""

    async def create_music_task(self, prompt: str, duration: int = 30) -> str:
        url = f"{self.api_base}/music/generate"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"prompt": prompt, "duration": duration, "model": "skymusic-v1"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Tiangong music failed: HTTP {resp.status_code}")
            data = resp.json()
        task_id = data.get("task_id", "") or data.get("data", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(f"Tiangong music: no task_id: {data}")
        return task_id

    async def query_music_task(self, task_id: str) -> dict[str, Any]:
        url = f"{self.api_base}/music/generate/{task_id}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Tiangong music query failed: HTTP {resp.status_code}")
            data = resp.json()
        task_data = data.get("data", data)
        status = task_data.get("status", "UNKNOWN")
        music_url = task_data.get("music_url") or task_data.get("audio_url")
        return {"status": status, "music_url": music_url, "error": task_data.get("error") if status == "FAILED" else None}
