"""moa_gateway.providers.audio_asr_provider -- Automatic Speech Recognition (ASR) providers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ASRProvider(ABC):
    """Abstract base class for speech-to-text (ASR) providers."""

    def __init__(self, api_base: str, api_key: str, timeout: int = 120):
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    @abstractmethod
    async def transcribe(self, audio_data: bytes, language: str = "zh") -> str:
        """Return transcribed text."""
        raise NotImplementedError


class OpenAIASRProvider(ASRProvider):
    """OpenAI-compatible ASR provider (Whisper format)."""

    async def transcribe(self, audio_data: bytes, language: str = "zh") -> str:
        url = f"{self.api_base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": ("audio.mp3", audio_data, "audio/mpeg")}
        data = {"language": language, "model": "whisper-1"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, files=files, data=data, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"ASR failed: HTTP {resp.status_code}")
            return resp.json().get("text", "")


class QwenASRProvider(ASRProvider):
    """Qwen (DashScope) ASR provider."""

    async def transcribe(self, audio_data: bytes, language: str = "zh") -> str:
        url = f"{self.api_base}/api/v1/services/audio/asr/transcription"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": ("audio.mp3", audio_data, "audio/mpeg")}
        data = {"language": language, "model": "paraformer-v2"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, files=files, data=data, headers=headers)
            if resp.status_code != 200:
                raise RuntimeError(f"Qwen ASR failed: HTTP {resp.status_code}")
            return resp.json().get("output", {}).get("text", "")


class IFlytekASRProvider(ASRProvider):
    """iFlytek Spark ASR provider."""

    async def transcribe(self, audio_data: bytes, language: str = "zh") -> str:
        url = f"{self.api_base}/v1/asr"
        headers = {"Content-Type": "application/octet-stream", "Authorization": f"Bearer {self.api_key}"}
        params = {"language": language}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, content=audio_data, headers=headers, params=params)
            if resp.status_code != 200:
                raise RuntimeError(f"iFlytek ASR failed: HTTP {resp.status_code}")
            return resp.json().get("text", "")
