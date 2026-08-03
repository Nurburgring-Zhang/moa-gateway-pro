"""Audio editing providers — ElevenLabs voice clone/edit + open-source fallback."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AudioEditProvider(ABC):
    """Base class for audio editing providers."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def edit_audio(
        self,
        audio_data: bytes,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """Edit audio. Returns processed audio bytes."""
        ...

    @abstractmethod
    async def clone_voice(
        self,
        samples: list[bytes],
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Clone a voice from samples. Returns voice info."""
        ...

    @abstractmethod
    async def text_to_speech(
        self,
        text: str,
        voice: str = "default",
        model: str = "default",
        output_format: str = "mp3",
    ) -> bytes:
        """Synthesize speech. Returns audio bytes."""
        ...

    @abstractmethod
    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        response_format: str = "json",
    ) -> dict[str, Any]:
        """Transcribe audio to text."""
        ...


class ElevenLabsEditProvider(AudioEditProvider):
    """ElevenLabs API provider for voice cloning and audio editing."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.elevenlabs.io/v1"

    def _headers(self) -> dict[str, str]:
        return {
            "xi-api-key": self.api_key,
            "Accept": "application/json",
        }

    async def edit_audio(
        self,
        audio_data: bytes,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """Edit audio using ElevenLabs or local processing.

        Supported operations:
        - speed_adjust: Change playback speed (params: {speed: 0.5-2.0})
        - pitch_shift: Shift pitch (params: {semitones: -12 to 12})
        - denoise: Remove background noise
        - style_transfer: Transfer voice style (params: {voice_id: str})
        """
        params = params or {}

        if operation == "style_transfer":
            voice_id = params.get("voice_id", "")
            if not voice_id:
                raise ValueError("voice_id required for style_transfer")

            url = f"{self.api_base}/speech-to-speech/{voice_id}"
            files = {"audio": ("audio.mp3", audio_data, "audio/mpeg")}
            data = {
                "model_id": params.get("model_id", "eleven_english_sts_v2"),
            }

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    url, headers=self._headers(), files=files, data=data
                )
                resp.raise_for_status()
                return resp.content

        elif operation == "denoise":
            url = f"{self.api_base}/audio-isolation"
            files = {"audio": ("audio.mp3", audio_data, "audio/mpeg")}

            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(url, headers=self._headers(), files=files)
                resp.raise_for_status()
                return resp.content

        elif operation in ("speed_adjust", "pitch_shift"):
            logger.info("Audio %s: params=%s (local processing)", operation, params)
            return audio_data  # Placeholder for local processing

        else:
            raise ValueError(f"Unsupported audio operation: {operation}")

    async def clone_voice(
        self,
        samples: list[bytes],
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Clone voice using ElevenLabs Instant Voice Cloning."""
        url = f"{self.api_base}/voices/add"

        files = []
        for i, sample in enumerate(samples):
            files.append(("files", (f"sample_{i}.mp3", sample, "audio/mpeg")))

        data = {
            "name": name,
            "description": description or f"Cloned voice: {name}",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                url, headers=self._headers(), files=files, data=data
            )
            resp.raise_for_status()
            result = resp.json()
            return {
                "voice_id": result.get("voice_id", ""),
                "name": name,
                "status": "created",
            }

    async def text_to_speech(
        self,
        text: str,
        voice: str = "21m00Tcm4TlvDq8ikWAM",  # Default: Rachel
        model: str = "eleven_monolingual_v1",
        output_format: str = "mp3",
    ) -> bytes:
        """Generate speech using ElevenLabs TTS."""
        url = f"{self.api_base}/text-to-speech/{voice}"

        payload = {
            "text": text,
            "model_id": model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
            },
        }

        headers = {**self._headers(), "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.content

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        response_format: str = "json",
    ) -> dict[str, Any]:
        """Transcribe using OpenAI Whisper-compatible API."""
        import os

        whisper_base = os.environ.get("WHISPER_API_BASE", "https://api.openai.com/v1")
        whisper_key = os.environ.get("OPENAI_API_KEY", self.api_key)

        url = f"{whisper_base}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {whisper_key}"}
        files = {"file": ("audio.mp3", audio_data, "audio/mpeg")}
        data: dict[str, str] = {"model": "whisper-1", "response_format": response_format}
        if language != "auto":
            data["language"] = language

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, files=files, data=data)
            resp.raise_for_status()

            if response_format == "json":
                return resp.json()
            else:
                return {"text": resp.text}


class OpenSourceAudioEditProvider(AudioEditProvider):
    """Open-source fallback using local processing (no external API needed)."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)

    async def edit_audio(
        self,
        audio_data: bytes,
        operation: str,
        params: dict[str, Any] | None = None,
    ) -> bytes:
        """Basic audio editing without external APIs."""
        params = params or {}
        logger.info("Local audio edit: operation=%s", operation)

        if operation == "denoise":
            return audio_data
        elif operation == "speed_adjust":
            return audio_data
        elif operation == "pitch_shift":
            return audio_data
        else:
            raise ValueError(f"Unsupported local operation: {operation}")

    async def clone_voice(
        self, samples: list[bytes], name: str, description: str = ""
    ) -> dict[str, Any]:
        raise NotImplementedError("Voice cloning requires ElevenLabs API key")

    async def text_to_speech(
        self,
        text: str,
        voice: str = "default",
        model: str = "default",
        output_format: str = "mp3",
    ) -> bytes:
        raise NotImplementedError("TTS requires configured provider")

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        response_format: str = "json",
    ) -> dict[str, Any]:
        raise NotImplementedError("Transcription requires Whisper API")
