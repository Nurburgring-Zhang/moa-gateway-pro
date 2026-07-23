"""moa_gateway.providers.audio_tts_provider -- Text-to-Speech (TTS) providers."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class TTSProvider(ABC):
    """Abstract base class for text-to-speech providers."""

    def __init__(self, api_base: str, api_key: str, timeout: int = 60):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def synthesize(self, text: str, voice: str = "default", audio_format: str = "mp3") -> bytes:
        """Return audio bytes."""
        raise NotImplementedError


class OpenAITTSProvider(TTSProvider):
    """OpenAI-compatible TTS provider."""

    async def synthesize(self, text: str, voice: str = "alloy", audio_format: str = "mp3") -> bytes:
        url = f"{self.api_base}/audio/speech"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"model": "tts-1", "input": text, "voice": voice, "response_format": audio_format}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"TTS failed: HTTP {resp.status_code}")
            return resp.content


class QwenTTSProvider(TTSProvider):
    """Qwen (DashScope) TTS provider with async task."""

    async def synthesize(self, text: str, voice: str = "longxiaochun", audio_format: str = "mp3") -> bytes:
        url = f"{self.api_base}/api/v1/services/audio/tts"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}", "X-DashScope-Async": "enable"}
        payload: dict[str, Any] = {"model": "sambert-zhichu-v1", "input": {"text": text}, "parameters": {"voice": voice, "format": audio_format}}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Qwen TTS failed: HTTP {resp.status_code}")
            data = resp.json()
        task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            return resp.content
        poll_url = f"{self.api_base}/api/v1/tasks/{task_id}"
        start = time.time()
        while time.time() - start < 60:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                poll_resp = await client.get(poll_url, headers={"Authorization": f"Bearer {self.api_key}"})
            if poll_resp.status_code == 200:
                poll_data = poll_resp.json()
                status = poll_data.get("output", {}).get("task_status", "")
                if status == "SUCCEEDED":
                    audio_url = poll_data.get("output", {}).get("result", {}).get("audio_url", "")
                    if audio_url:
                        async with httpx.AsyncClient(timeout=self.timeout) as client:
                            return (await client.get(audio_url)).content
                if status == "FAILED":
                    raise RuntimeError(f"Qwen TTS task failed: {poll_data}")
            await asyncio.sleep(1)
        raise TimeoutError("Qwen TTS task timed out")


class IFlytekTTSProvider(TTSProvider):
    """iFlytek Spark TTS provider."""

    async def synthesize(self, text: str, voice: str = "xiaoyan", audio_format: str = "mp3") -> bytes:
        url = f"{self.api_base}/v1/tts"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        payload: dict[str, Any] = {"text": text, "voice": voice, "format": audio_format}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"iFlytek TTS failed: HTTP {resp.status_code}")
            return resp.content
