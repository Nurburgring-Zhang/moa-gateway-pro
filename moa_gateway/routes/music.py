"""Music generation endpoints (MiniMax / Tiangong SkyMusic).

Wiring for moa_gateway.providers.music_generation_provider — both providers
are asynchronous (create task -> poll task), so the route exposes POST
/v1/audio/music to create a task and GET /v1/audio/music/tasks/{task_id}
to query it. Follows the routes/threed.py pattern: require_api_key +
per-key rate limiting (via the shared auth dependency) + the D6 mock.mode
policy (explicit -> labeled MockMusicProvider, disabled -> 503).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..req_models import (
    CreateMusicGenerationRequest,
    MusicCreateResponseModel,
    MusicTaskResponseModel,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["music"], dependencies=[Depends(require_capability("music"))])

# Task ownership mapping (task_id -> key_id) for access control
_task_owners: dict[str, str] = {}


def _apply_mock_label(provider: Any, response: Response) -> None:
    """Audit F24: label mock-provider responses with the X-MOA-Mock header."""
    if provider.__class__.__name__.startswith("Mock"):
        for _hk, _hv in mock_headers(True).items():
            response.headers[_hk] = _hv


# --- Endpoints ---


@router.post("/v1/audio/music", response_model=MusicCreateResponseModel)
async def generate_music(
    req: CreateMusicGenerationRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Generate music from a text prompt (async task).

    provider selects the platform:
    - auto: first available of MINIMAX_API_KEY, TIANGONG_API_KEY
    - minimax / tiangong: force that platform (needs its key)

    Without any real key the D6 mock policy applies: mock.mode=explicit
    returns a labeled synthetic task (200 + X-MOA-Mock), mock.mode=disabled
    fails fast with 503.
    """
    provider = _get_music_provider(req.provider)
    _apply_mock_label(provider, response)

    # Config error (503) only for NON-mock providers (mock providers
    # intentionally have an empty api_key but return synthetic 200s).
    is_mock = provider.__class__.__name__.startswith("Mock")
    if not is_mock and not getattr(provider, "api_key", ""):
        raise HTTPException(
            status_code=503,
            detail="Music provider not configured (set MINIMAX_API_KEY or TIANGONG_API_KEY)",
        )

    try:
        task_id = await provider.create_music_task(
            prompt=req.prompt,
            duration=req.duration,
        )

        # Record task ownership for access control
        if task_id:
            _task_owners[task_id] = key_info.get("key_id", "")

        return MusicCreateResponseModel(task_id=task_id, status="processing")
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Music generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Music generation error: {str(e)}") from e


@router.get("/v1/audio/music/tasks/{task_id}", response_model=MusicTaskResponseModel)
async def get_music_task(
    task_id: str,
    response: Response,
    provider: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Query music generation task status."""
    if provider not in ("auto", "minimax", "tiangong"):
        raise HTTPException(status_code=400, detail=f"Unknown music provider: {provider}")

    # Ownership check: deny access if task belongs to another user
    owner = _task_owners.get(task_id)
    if owner is not None and owner != key_info.get("key_id", ""):
        raise HTTPException(status_code=404, detail="Task not found")

    music_provider = _get_music_provider(provider)

    # Audit F22 fix: a Mock provider has no real upstream task store, so the
    # ONLY tasks that genuinely exist are the ones created through this
    # gateway (recorded in _task_owners). Querying any other id must return
    # 404 — fabricating a "completed" task for an arbitrary id is false data.
    if music_provider.__class__.__name__.startswith("Mock") and task_id not in _task_owners:
        raise HTTPException(status_code=404, detail="Task not found")

    _apply_mock_label(music_provider, response)

    # No API key configured is a config issue (503), distinct from a
    # missing task (404) or upstream failure (502). Skip for Mock providers.
    if not getattr(music_provider, "api_key", "") and not music_provider.__class__.__name__.startswith("Mock"):
        raise HTTPException(
            status_code=503,
            detail="Music provider not configured (set MINIMAX_API_KEY or TIANGONG_API_KEY)",
        )

    try:
        result = await music_provider.query_music_task(task_id)
        status = str(result.get("status", "UNKNOWN")).lower()
        # Normalize upstream statuses (MiniMax/Tiangong return e.g. Success/
        # Processing/Failed) to the gateway's processing/completed/failed set.
        if status in ("succeed", "succeeded", "success", "completed", "complete"):
            normalized = "completed"
        elif status in ("failed", "error", "timeout"):
            normalized = "failed"
        else:
            normalized = "processing"
        return MusicTaskResponseModel(
            task_id=task_id,
            status=normalized,
            music_url=result.get("music_url"),
            error=result.get("error"),
        )
    except HTTPException:
        raise
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Music task query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Task query error: {str(e)}") from e


# --- Helpers ---


def _get_music_provider(provider: str):
    """Get music generation provider by name.

    Key lookup: MINIMAX_API_KEY / TIANGONG_API_KEY (with optional
    MINIMAX_API_BASE / TIANGONG_API_BASE overrides). Falls back to
    MockMusicProvider when no real key is configured and mock.mode=explicit
    so the pipeline returns a labeled 200; with mock.mode=disabled a keyless
    real provider is returned and the route raises 503.
    """
    from ..config import get_settings
    from ..providers import build_multimodal_provider, is_mock_key
    from ..providers.music_generation_provider import (
        MiniMaxMusicProvider,
        MockMusicProvider,
    )

    def _clean(value: str) -> str:
        return "" if is_mock_key(value) else value

    minimax_key = _clean(os.environ.get("MINIMAX_API_KEY", ""))
    tiangong_key = _clean(os.environ.get("TIANGONG_API_KEY", ""))
    minimax_base = os.environ.get("MINIMAX_API_BASE", "")
    tiangong_base = os.environ.get("TIANGONG_API_BASE", "")

    # build_multimodal_provider fills api_base from the free-model catalog
    # when the env override is empty (minimax_music / tiangong_music).
    if provider == "minimax" and minimax_key:
        return build_multimodal_provider(
            "music", "minimax_music", api_key=minimax_key, api_base=minimax_base
        )
    elif provider == "tiangong" and tiangong_key:
        return build_multimodal_provider(
            "music", "tiangong_music", api_key=tiangong_key, api_base=tiangong_base
        )
    elif minimax_key:
        return build_multimodal_provider(
            "music", "minimax_music", api_key=minimax_key, api_base=minimax_base
        )
    elif tiangong_key:
        return build_multimodal_provider(
            "music", "tiangong_music", api_key=tiangong_key, api_base=tiangong_base
        )
    else:
        # No real key — mock provider when explicit mock mode.
        try:
            mock_mode = get_settings().mock.mode
        except Exception:
            mock_mode = "explicit"
        if mock_mode == "explicit":
            return MockMusicProvider()
        # disabled: keyless real provider (route raises 503 before calling it)
        return MiniMaxMusicProvider(api_base=minimax_base, api_key="")
