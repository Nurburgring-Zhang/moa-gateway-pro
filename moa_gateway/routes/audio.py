"""Unified audio endpoints — TTS, ASR, Edit, Clone."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..providers.audio_asr_provider import ASRProvider
from ..providers.audio_tts_provider import TTSProvider

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
    provider: str = Field(
        default="auto",
        pattern=r"^(auto|openai|dashscope|iflytek)$",
        description=(
            "TTS backend: auto picks by available key priority "
            "(OPENAI_API_KEY → IFLYTEK_API_KEY → DASHSCOPE_TTS_API_KEY), "
            "then falls back to the ElevenLabs/open-source path"
        ),
    )


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

@router.post("/v1/audio/speech", dependencies=[Depends(require_capability("tts"))])
async def text_to_speech(
    req: TTSRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate speech from text. OpenAI /v1/audio/speech compatible.

    provider selects the TTS backend:
    - auto: dedicated providers by key priority (OPENAI_API_KEY →
      IFLYTEK_API_KEY → DASHSCOPE_TTS_API_KEY); without those keys the
      legacy ElevenLabs / labeled open-source path is kept.
    - openai / dashscope / iflytek: force the dedicated provider.
    """
    provider_name = req.provider
    tts_impl = None
    tts_is_mock = False

    if provider_name == "auto":
        selected = _auto_select_tts()
        if selected is not None:
            provider_name = selected
        else:
            # Legacy path: ElevenLabs (real key) or labeled open-source mock.
            tts_impl = _get_audio_provider()
            tts_is_mock = tts_impl.__class__.__name__ == "OpenSourceAudioEditProvider"
            if tts_is_mock and _mock_mode() == "disabled":
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "TTS provider not configured (set OPENAI_API_KEY, IFLYTEK_API_KEY, "
                        "DASHSCOPE_TTS_API_KEY or ELEVENLABS_API_KEY)"
                    ),
                )

    if tts_impl is None:
        api_key, api_base = _resolve_tts_credentials(provider_name)
        if api_key:
            tts_impl = _build_tts_provider(provider_name, api_key, api_base)
        elif _mock_mode() == "explicit":
            # No real key + explicit mock policy → labeled synthetic audio.
            from ..providers.audio_edit_provider import OpenSourceAudioEditProvider

            tts_impl = OpenSourceAudioEditProvider()
            tts_is_mock = True
        else:
            raise HTTPException(
                status_code=503,
                detail=f"TTS provider '{provider_name}' not configured (missing API key)",
            )

    # Map OpenAI model names to ElevenLabs model IDs
    elevenlabs_model = _TTS_MODEL_MAP.get(req.model, req.model)

    try:
        if isinstance(tts_impl, TTSProvider):
            # Dedicated TTS providers implement synthesize().
            audio_bytes = await tts_impl.synthesize(
                text=req.input,
                voice=req.voice,
                audio_format=req.response_format,
            )
        else:
            # Audit F24: the open-source provider's TTS is a labeled mock (no
            # real synthesis); mark the response with X-MOA-Mock so clients know.
            audio_bytes = await tts_impl.text_to_speech(
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

        headers = {"Content-Disposition": f"attachment; filename=speech.{req.response_format}"}
        headers.update(mock_headers(tts_is_mock))
        return Response(
            content=audio_bytes,
            media_type=content_type_map.get(req.response_format, "audio/mpeg"),
            headers=headers,
        )
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        logger.error("TTS failed: %s", e)
        raise HTTPException(status_code=502, detail=f"TTS error: {str(e)}") from e


# ─── Transcription Endpoint (OpenAI compatible) ───────────────────────────────────

@router.post(
    "/v1/audio/transcriptions",
    response_model=TranscriptionResponse,
    dependencies=[Depends(require_capability("stt"))],
)
async def transcribe_audio(
    response: Response,
    file: UploadFile = File(..., description="Audio file (mp3/wav/m4a/webm)"),
    model: str = Form(default="whisper-1"),
    language: str | None = Form(default=None),
    response_format: str = Form(default="json"),
    provider: str = Form(
        default="auto",
        description=(
            "ASR backend: auto picks by available key priority "
            "(WHISPER_API_KEY/OPENAI_API_KEY → IFLYTEK_API_KEY → DASHSCOPE_ASR_API_KEY), "
            "then falls back to the ElevenLabs/open-source path"
        ),
    ),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Transcribe audio to text. OpenAI /v1/audio/transcriptions compatible.

    provider selects the ASR backend:
    - auto: dedicated providers by key priority (openai/Whisper → iflytek →
      dashscope/paraformer); without those keys the legacy ElevenLabs /
      labeled open-source path is kept.
    - openai / dashscope / iflytek: force the dedicated provider.
    """
    if provider not in ("auto", "openai", "dashscope", "iflytek"):
        raise HTTPException(status_code=400, detail=f"Unknown ASR provider: {provider}")

    audio_bytes = await file.read()

    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Audio file is empty")
    if len(audio_bytes) > 25 * 1024 * 1024:  # 25MB limit
        raise HTTPException(status_code=400, detail="Audio file too large (max 25MB)")

    provider_name = provider
    asr_impl = None
    asr_is_mock = False

    if provider_name == "auto":
        selected = _auto_select_asr()
        if selected is not None:
            provider_name = selected
        else:
            # Legacy path: ElevenLabs (real key) or labeled open-source mock.
            asr_impl = _get_audio_provider()
            asr_is_mock = asr_impl.__class__.__name__ == "OpenSourceAudioEditProvider"
            if asr_is_mock and _mock_mode() == "disabled":
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "ASR provider not configured (set WHISPER_API_KEY/OPENAI_API_KEY, "
                        "IFLYTEK_API_KEY or DASHSCOPE_ASR_API_KEY)"
                    ),
                )

    if asr_impl is None:
        api_key, api_base = _resolve_asr_credentials(provider_name)
        if api_key:
            asr_impl = _build_asr_provider(provider_name, api_key, api_base)
        elif _mock_mode() == "explicit":
            # No real key + explicit mock policy → labeled synthetic text.
            from ..providers.audio_edit_provider import OpenSourceAudioEditProvider

            asr_impl = OpenSourceAudioEditProvider()
            asr_is_mock = True
        else:
            raise HTTPException(
                status_code=503,
                detail=f"ASR provider '{provider_name}' not configured (missing API key)",
            )

    try:
        if isinstance(asr_impl, ASRProvider):
            # Dedicated ASR providers implement transcribe() -> str.
            text = await asr_impl.transcribe(
                audio_data=audio_bytes,
                language=language or "zh",
            )
        else:
            result = await asr_impl.transcribe(
                audio_data=audio_bytes,
                language=language or "auto",
                response_format=response_format,
            )
            text = result.get("text", "")
        for _hk, _hv in mock_headers(asr_is_mock).items():
            response.headers[_hk] = _hv
        return TranscriptionResponse(text=text)
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        logger.error("Transcription failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Transcription error: {str(e)}") from e


# ─── Audio Edit Endpoint ──────────────────────────────────────────────────

@router.post("/v1/audio/edit", dependencies=[Depends(require_capability("stt"))])
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
        raise HTTPException(status_code=400, detail="Invalid JSON in params") from None

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
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.error("Audio edit failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Audio edit error: {str(e)}") from e


# ─── Voice Clone Endpoint ─────────────────────────────────────────────────

@router.post(
    "/v1/audio/clone",
    response_model=VoiceCloneResponse,
    dependencies=[Depends(require_capability("tts"))],
)
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
        raise HTTPException(status_code=501, detail=str(e)) from e
    except Exception as e:
        logger.error("Voice clone failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Voice clone error: {str(e)}") from e


# ─── Helpers ──────────────────────────────────────────────────────────────

def _get_audio_provider():
    """Get the best available audio edit provider."""
    from ..providers.audio_edit_provider import ElevenLabsEditProvider, OpenSourceAudioEditProvider

    elevenlabs_key = os.environ.get("ELEVENLABS_API_KEY", "")
    if elevenlabs_key:
        return ElevenLabsEditProvider(api_key=elevenlabs_key)

    # Fallback to open-source provider
    return OpenSourceAudioEditProvider()


# ─── Dedicated ASR/TTS provider wiring ──────────────────────────────────────
# audio_asr_provider.py (Whisper / DashScope paraformer / iFlytek) and
# audio_tts_provider.py (OpenAI / DashScope sambert / iFlytek) are wired
# here. Key rules:
# - Placeholder keys ("your-...", "mock") count as absent (is_mock_key).
# - auto picks by available-key priority; the platform-wide DASHSCOPE_API_KEY
#   is deliberately NOT an auto trigger — it is the qwen-chat/wanx key and
#   DashScope audio services require separate activation. Set the dedicated
#   DASHSCOPE_ASR_API_KEY / DASHSCOPE_TTS_API_KEY to auto-select DashScope.
# - Explicit provider selection accepts the plain DASHSCOPE_API_KEY.
# - No real key → settings.mock.mode decides: explicit = labeled open-source
#   mock (200 + X-MOA-Mock), disabled = 503.


def _clean_key(value: str) -> str:
    from ..providers import is_mock_key

    return "" if is_mock_key(value) else value


def _mock_mode() -> str:
    try:
        from ..config import get_settings

        return get_settings().mock.mode
    except Exception:
        return "explicit"


def _resolve_asr_credentials(provider_name: str) -> tuple[str, str]:
    """(api_key, api_base) for an explicit ASR provider, read from env."""
    if provider_name == "openai":
        key = _clean_key(
            os.environ.get("WHISPER_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        )
        base = (
            os.environ.get("WHISPER_API_BASE", "")
            or os.environ.get("OPENAI_API_BASE", "")
            or "https://api.openai.com/v1"
        )
    elif provider_name == "dashscope":
        key = _clean_key(
            os.environ.get("DASHSCOPE_ASR_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
        )
        base = os.environ.get("DASHSCOPE_API_BASE", "") or "https://dashscope.aliyuncs.com"
    elif provider_name == "iflytek":
        key = _clean_key(os.environ.get("IFLYTEK_API_KEY", ""))
        base = os.environ.get("IFLYTEK_API_BASE", "") or "https://spark-api-open.xf-yun.com"
    else:
        key, base = "", ""
    return key, base


def _resolve_tts_credentials(provider_name: str) -> tuple[str, str]:
    """(api_key, api_base) for an explicit TTS provider, read from env."""
    if provider_name == "openai":
        key = _clean_key(os.environ.get("OPENAI_API_KEY", ""))
        base = os.environ.get("OPENAI_API_BASE", "") or "https://api.openai.com/v1"
    elif provider_name == "dashscope":
        key = _clean_key(
            os.environ.get("DASHSCOPE_TTS_API_KEY", "") or os.environ.get("DASHSCOPE_API_KEY", "")
        )
        base = os.environ.get("DASHSCOPE_API_BASE", "") or "https://dashscope.aliyuncs.com"
    elif provider_name == "iflytek":
        key = _clean_key(os.environ.get("IFLYTEK_API_KEY", ""))
        base = os.environ.get("IFLYTEK_API_BASE", "") or "https://spark-api-open.xf-yun.com"
    else:
        key, base = "", ""
    return key, base


def _build_asr_provider(provider_name: str, api_key: str, api_base: str) -> ASRProvider:
    from ..providers.audio_asr_provider import (
        IFlytekASRProvider,
        OpenAIASRProvider,
        QwenASRProvider,
    )

    if provider_name == "openai":
        return OpenAIASRProvider(api_base=api_base, api_key=api_key)
    if provider_name == "dashscope":
        return QwenASRProvider(api_base=api_base, api_key=api_key)
    return IFlytekASRProvider(api_base=api_base, api_key=api_key)


def _build_tts_provider(provider_name: str, api_key: str, api_base: str) -> TTSProvider:
    from ..providers.audio_tts_provider import (
        IFlytekTTSProvider,
        OpenAITTSProvider,
        QwenTTSProvider,
    )

    if provider_name == "openai":
        return OpenAITTSProvider(api_base=api_base, api_key=api_key)
    if provider_name == "dashscope":
        return QwenTTSProvider(api_base=api_base, api_key=api_key)
    return IFlytekTTSProvider(api_base=api_base, api_key=api_key)


def _auto_select_asr() -> str | None:
    """Pick an ASR provider by available-key priority, or None for legacy."""
    for candidate in ("openai", "iflytek", "dashscope"):
        if candidate == "dashscope":
            # Dedicated audio key only — see module note above.
            key = _clean_key(os.environ.get("DASHSCOPE_ASR_API_KEY", ""))
        else:
            key, _base = _resolve_asr_credentials(candidate)
        if key:
            return candidate
    return None


def _auto_select_tts() -> str | None:
    """Pick a TTS provider by available-key priority, or None for legacy."""
    for candidate in ("openai", "iflytek", "dashscope"):
        if candidate == "dashscope":
            # Dedicated audio key only — see module note above.
            key = _clean_key(os.environ.get("DASHSCOPE_TTS_API_KEY", ""))
        else:
            key, _base = _resolve_tts_credentials(candidate)
        if key:
            return candidate
    return None
