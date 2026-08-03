"""Unified audio endpoints — TTS, ASR, Edit, Clone."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..auth import require_api_key

logger = logging.getLogger(__name__)
router = APIRouter(tags=["audio"])

MAX_AUDIO_SIZE = 25 * 1024 * 1024  # 25MB


# ─── Request/Response Models ───────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    """OpenAI-compatible TTS request."""
    model: str = Field(default="tts-1", description="TTS model")
    input: str = Field(..., min_length=1, max_length=4096, description="Text to synthesize")
    voice: str = Field(default="alloy", description="Voice name/ID")
    response_format: str = Field(default="mp3", pattern=r"^(mp3|opus|aac|flac|wav)$")
    speed: float = Field(default=1.0, ge=0.25, le=4.0)


class TranscriptionResponse(BaseModel):
    text: str


class AudioEditRequest(BaseModel):
    operation: str = Field(..., description="Operation: denoise/speed_adjust/pitch_shift/style_transfer")
    params: dict[str, Any] = Field(default_factory=dict)


class VoiceCloneResponse(BaseModel):
    voice_id: str
    name: str
    status: str


# ─── TTS Model Mapping ─────────────────────────────────────────────────────

_TTS_MODEL_MAP = {
    "tts-1": "eleven_monolingual_v1",
    "tts-1-hd": "eleven_multilingual_v2",
    "gpt-4o-mini-tts": "eleven_monolingual_v1",
    "gpt-4o-tts": "eleven_multilingual_v2",
}


# ─── TTS Endpoint (OpenAI compatible) ─────────────────────────────────────────

@router.post("/v1/audio/speech")
async def text_to_speech(
    req: TTSRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate speech from text. OpenAI /v1/audio/speech compatible."""
    provider = _get_audio_provider()

    # Map OpenAI model names to ElevenLabs model IDs
    elevenlabs_model = _TTS_MODEL_MAP.get(req.model, req.model)

    try:
        audio_bytes = await provider.text_to_speech(
            text=req.input,
            voice=req.voice,
            model=elevenlabs_model,
            output_format=req.response_format,
        )

        content_type_map = {
            "mp3": "audio/mpeg",
            "opus": "audio/opus",
            "aac": "audio/aac",
            "flac": "audio/flac",
            "wav": "audio/wav",
        }

        return Response(
            content=audio_bytes,
            media_type=content_type_map.get(req.response_format, "audio/mpeg"),
            headers={"Content-Disposition": f"attachment; filename=speech.{req.response_format}"},
        )
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error("TTS failed: %s", e)
        raise HTTPException(status_code=502, detail=f"TTS error: {str(e)}")


# ─── Transcription Endpoint (OpenAI compatible) ───────────────────────────────────

@router.post("/v1/audio/transcriptions", response_model=TranscriptionResponse)
async def transcribe_audio(
    file: UploadFile = File(..., description="Audio file (mp3/wav/m4a/webm)"),
    model: str = Form(default="whisper-1"),
    language: Optional[str] = Form(default=None),
    response_format: str = Form(default="json"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Transcribe audio to text. OpenAI /v1/audio/transcriptions compatible."""
    audio_bytes = await file.read()

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(audio_bytes) > 25 * 1024 * 1024:  # 25MB limit
        raise HTTPException(status_code=400, detail="Audio file too large (max 25MB)")

    provider = _get_audio_provider()

    try:
        result = await provider.transcribe(
            audio_data=audio_bytes,
            language=language or "auto",
            response_format=response_format,
        )
        return TranscriptionResponse(text=result.get("text", ""))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Transcription error: {str(e)}")


# ─── Audio Edit Endpoint ──────────────────────────────────────────────────

@router.post("/v1/audio/edit")
async def edit_audio(
    file: UploadFile = File(..., description="Audio file to edit"),
    operation: str = Form(..., description="Operation: denoise/speed_adjust/pitch_shift/style_transfer"),
    params: str = Form(default="{}", description="JSON params for the operation"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Edit audio with specified operation.

    Operations:
    - denoise: Remove background noise
    - speed_adjust: Change speed (params: {"speed": 1.5})
    - pitch_shift: Shift pitch (params: {"semitones": 2})
    - style_transfer: Transfer voice style (params: {"voice_id": "..."})
    """
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="File too large. Maximum size is 25MB")

    try:
        operation_params = json.loads(params) if params else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON in params")

    provider = _get_audio_provider()

    try:
        result_bytes = await provider.edit_audio(
            audio_data=audio_bytes,
            operation=operation,
            params=operation_params,
        )

        return Response(
            content=result_bytes,
            media_type="audio/mpeg",
            headers={"Content-Disposition": "attachment; filename=edited_audio.mp3"},
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Audio edit failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Audio edit error: {str(e)}")


# ─── Voice Clone Endpoint ─────────────────────────────────────────────────

@router.post("/v1/audio/clone", response_model=VoiceCloneResponse)
async def clone_voice(
    name: str = Form(..., description="Name for the cloned voice"),
    description: str = Form(default="", description="Voice description"),
    samples: list[UploadFile] = File(..., description="Voice sample files (1-5 files)"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Clone a voice from audio samples.

    Requires 1-5 audio samples of the target voice.
    Each sample should be 30s-5min of clear speech.
    """
    if len(samples) > 5:
        raise HTTPException(status_code=400, detail="Maximum 5 voice samples allowed")

    sample_bytes = []
    for sample in samples:
        data = await sample.read()
        if not data:
            raise HTTPException(status_code=400, detail=f"Empty sample file: {sample.filename}")
        if len(data) > MAX_AUDIO_SIZE:
            raise HTTPException(status_code=413, detail="Sample file too large. Maximum size is 25MB")
        sample_bytes.append(data)

    provider = _get_audio_provider()

    try:
        result = await provider.clone_voice(
            samples=sample_bytes,
            name=name,
            description=description,
        )
        return VoiceCloneResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))
    except Exception as e:
        logger.error("Voice clone failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Voice clone error: {str(e)}")


# ─── Helpers ──────────────────────────────────────────────────────────────

def _get_audio_provider():
    """Get the best available audio edit provider."""
    from ..providers.audio_edit_provider import ElevenLabsEditProvider, OpenSourceAudioEditProvider

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if elevenlabs_key:
        return ElevenLabsEditProvider(api_key=elevenlabs_key)

    # Fallback to open-source provider
    return OpenSourceAudioEditProvider()
