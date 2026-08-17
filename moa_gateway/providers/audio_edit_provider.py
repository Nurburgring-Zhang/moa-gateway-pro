"""Audio editing providers — ElevenLabs voice clone/edit + open-source fallback."""
from __future__ import annotations

import logging
import struct
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

        elif operation == "speed_adjust":
            speed_factor = params.get("speed", 1.0)
            logger.info("Audio speed_adjust: factor=%s (local processing)", speed_factor)
            return _adjust_speed(audio_data, speed_factor)

        elif operation == "pitch_shift":
            semitones = params.get("semitones", 0)
            logger.info("Audio pitch_shift: semitones=%s (local processing)", semitones)
            return _shift_pitch(audio_data, semitones)

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
                return resp.json()  # type: ignore[no-any-return]
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
            return _denoise(audio_data)
        elif operation == "speed_adjust":
            speed_factor = params.get("speed", 1.0)
            return _adjust_speed(audio_data, speed_factor)
        elif operation == "pitch_shift":
            semitones = params.get("semitones", 0)
            return _shift_pitch(audio_data, semitones)
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
        """Mock TTS: returns a minimal valid audio buffer labeled X-MOA-Mock.

        No real TTS provider configured — this lets the pipeline return 200
        with labeled synthetic output for development/testing. Configure
        ELEVENLABS_API_KEY for real speech synthesis (the ElevenLabs provider
        takes priority in _get_audio_provider).
        """
        logger.warning(
            "[mock] TTS: no real provider configured (set ELEVENLABS_API_KEY); "
            "returning synthetic mock audio"
        )
        # Minimal valid WAV header (44 bytes) + 0 data frames — a real,
        # decodable (silent) WAV that satisfies format validation.
        import struct
        num_channels, sample_rate, bits_per_sample = 1, 8000, 16
        byte_rate = sample_rate * num_channels * bits_per_sample // 8
        block_align = num_channels * bits_per_sample // 8
        data_size = 0
        header = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + data_size, b"WAVE", b"fmt ", 16,
            1, num_channels, sample_rate, byte_rate, block_align,
            bits_per_sample, b"data", data_size,
        )
        return header

    async def transcribe(
        self,
        audio_data: bytes,
        language: str = "auto",
        response_format: str = "json",
    ) -> dict[str, Any]:
        """Mock ASR: returns a canned transcription labeled X-MOA-Mock.

        Configure a real Whisper-compatible ASR provider for actual
        transcription (the real ElevenLabs/OpenAI ASR path takes priority).
        """
        logger.warning(
            "[mock] ASR: no real provider configured; returning synthetic mock transcription"
        )
        return {
            "text": "[Mock transcription] This is a synthetic placeholder transcription. No real ASR provider configured.",
            "language": language,
            "duration": 1.0,
            "mock": True,
        }

# =============================================================================
# Pure-Python audio processing helpers (no external dependencies)
# =============================================================================


def _adjust_speed(audio_data: bytes, speed_factor: float) -> bytes:
    """Adjust audio playback speed via linear-interpolation resampling.

    Operates on raw WAV (PCM 16-bit) data. Preserves the 44-byte header and
    updates relevant header fields (file size, data chunk size).

    Args:
        audio_data: WAV file bytes (must include standard 44-byte header).
        speed_factor: >1.0 speeds up (shorter), <1.0 slows down (longer).

    Returns:
        Processed WAV bytes.
    """
    if abs(speed_factor - 1.0) < 0.01:
        return audio_data
    if speed_factor <= 0:
        return audio_data
    if len(audio_data) < 44:
        return audio_data

    header = audio_data[:44]
    pcm_data = audio_data[44:]

    # Determine sample size from header (bytes 34-35 = bits per sample)
    bits_per_sample = struct.unpack_from("<H", header, 34)[0]
    num_channels = struct.unpack_from("<H", header, 22)[0]
    sample_size = (bits_per_sample // 8) * num_channels
    if sample_size == 0:
        return audio_data

    # For 16-bit mono/stereo, process per-frame
    bytes_per_frame = sample_size
    num_frames = len(pcm_data) // bytes_per_frame
    if num_frames < 2:
        return audio_data

    # Decode samples (16-bit signed)
    samples_per_frame = num_channels
    total_samples = num_frames * samples_per_frame
    fmt = f"<{total_samples}h"
    if len(pcm_data) < total_samples * 2:
        total_samples = len(pcm_data) // 2
        fmt = f"<{total_samples}h"
        num_frames = total_samples // samples_per_frame

    samples = struct.unpack(fmt, pcm_data[: total_samples * 2])

    # Linear interpolation resampling
    new_num_frames = int(num_frames / speed_factor)
    new_num_frames = max(new_num_frames, 1)

    new_samples: list[int] = []
    for i in range(new_num_frames):
        src_pos = i * speed_factor
        idx = int(src_pos)
        frac = src_pos - idx
        for ch in range(samples_per_frame):
            pos_a = idx * samples_per_frame + ch
            pos_b = (idx + 1) * samples_per_frame + ch
            if pos_b < total_samples:
                val = int(samples[pos_a] * (1 - frac) + samples[pos_b] * frac)
            elif pos_a < total_samples:
                val = samples[pos_a]
            else:
                val = 0
            new_samples.append(max(-32768, min(32767, val)))

    # Encode back
    new_pcm = struct.pack(f"<{len(new_samples)}h", *new_samples)

    # Update WAV header
    new_header = bytearray(header)
    data_size = len(new_pcm)
    file_size = 36 + data_size
    struct.pack_into("<I", new_header, 4, file_size)   # RIFF chunk size
    struct.pack_into("<I", new_header, 40, data_size)  # data sub-chunk size

    return bytes(new_header) + new_pcm


def _shift_pitch(audio_data: bytes, semitones: int) -> bytes:
    """Shift pitch by resampling at a different rate.

    This is a simplified pitch shift: it changes pitch by resampling, which
    also changes duration (like speeding up/slowing down a vinyl record).
    For production use, a proper phase vocoder would preserve duration.

    Args:
        audio_data: WAV file bytes.
        semitones: Number of semitones to shift (positive=higher, negative=lower).

    Returns:
        Processed WAV bytes.
    """
    if semitones == 0:
        return audio_data

    # Semitone ratio: 2^(n/12)
    pitch_factor = 2.0 ** (semitones / 12.0)
    # Higher pitch = faster playback = speed_factor > 1
    return _adjust_speed(audio_data, pitch_factor)


def _denoise(audio_data: bytes) -> bytes:
    """Simple noise reduction using a moving-average low-pass filter.

    Applies a 5-point moving average to smooth out high-frequency noise.
    This is a basic approach suitable for light noise reduction.

    Args:
        audio_data: WAV file bytes.

    Returns:
        Filtered WAV bytes.
    """
    if len(audio_data) < 44:
        return audio_data

    header = audio_data[:44]
    pcm_data = audio_data[44:]

    num_channels = struct.unpack_from("<H", header, 22)[0]
    bits_per_sample = struct.unpack_from("<H", header, 34)[0]
    if bits_per_sample != 16:
        return audio_data  # Only handle 16-bit

    samples_per_frame = num_channels
    total_samples = len(pcm_data) // 2
    if total_samples < 5 * samples_per_frame:
        return audio_data

    fmt = f"<{total_samples}h"
    samples = list(struct.unpack(fmt, pcm_data[: total_samples * 2]))

    num_frames = total_samples // samples_per_frame
    window = 5

    # Apply moving average per channel
    filtered = [0] * total_samples
    for ch in range(samples_per_frame):
        for i in range(num_frames):
            start = max(0, i - window // 2)
            end = min(num_frames, i + window // 2 + 1)
            total = 0
            for j in range(start, end):
                total += samples[j * samples_per_frame + ch]
            filtered[i * samples_per_frame + ch] = total // (end - start)

    new_pcm = struct.pack(f"<{len(filtered)}h", *filtered)

    # Header stays the same size (same number of samples for denoise)
    new_header = bytearray(header)
    data_size = len(new_pcm)
    struct.pack_into("<I", new_header, 4, 36 + data_size)
    struct.pack_into("<I", new_header, 40, data_size)

    return bytes(new_header) + new_pcm

