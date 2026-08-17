"""Gateway-wide capability toggles (admin-ui "能力管理" page backend).

Real implementation:
- Toggle state persists in the ``config_overrides`` storage table (survives restart).
- ``require_capability(name)`` is a FastAPI dependency that returns 503 when the
  capability is disabled, so toggles genuinely gate the corresponding endpoints.
- Defaults: every capability enabled.

Capability name mapping (frontend card name -> gated routes):
    chat          -> /v1/chat/completions
    vision        -> /v1/vision/*
    image_gen     -> /v1/images/generations, /v1/images/edits, /v1/images/variations
    tts           -> /v1/audio/speech
    stt           -> /v1/audio/transcriptions, /v1/audio/edit, /v1/audio/clone
    embedding     -> /v1/embeddings, /v1/capability/embeddings, /v1/capability/semantic-search
    video         -> /v1/video/*
    three_d       -> /v1/3d/*
    world_model   -> /v1/world/*
    embodied      -> /v1/embodied/*
    code / reasoning / search / function_call -> agent-loop tools & routing hints
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Canonical capabilities. The admin UI ships 11 cards; backend-only modalities
# (three_d / world_model / embodied) are managed too so the toggle surface is
# complete and honest.
DEFAULT_CAPABILITIES: dict[str, bool] = {
    "chat": True,
    "vision": True,
    "image_gen": True,
    "tts": True,
    "stt": True,
    "embedding": True,
    "video": True,
    "three_d": True,
    "world_model": True,
    "embodied": True,
    "code": True,
    "reasoning": True,
    "search": True,
    "function_call": True,
}

_STORAGE_KEY = "capability_toggles"

_lock = threading.Lock()
_cache: dict[str, bool] | None = None


def _storage():
    from .storage import get_storage

    return get_storage()


def load_toggles() -> dict[str, bool]:
    """Load toggles from storage, falling back to defaults for unknown keys."""
    global _cache
    with _lock:
        if _cache is not None:
            return dict(_cache)
        state = dict(DEFAULT_CAPABILITIES)
        try:
            stored = _storage().get_config_overrides().get(_STORAGE_KEY)
            if isinstance(stored, dict):
                for k, v in stored.items():
                    state[k] = bool(v)
        except Exception:  # pragma: no cover - storage not ready yet
            logger.warning("capability toggles: storage unavailable, using defaults")
        _cache = state
        return dict(state)


def get_all() -> list[dict[str, Any]]:
    toggles = load_toggles()
    return [{"name": k, "enabled": v} for k, v in sorted(toggles.items())]


def is_enabled(name: str) -> bool:
    return load_toggles().get(name, True)


def set_enabled(name: str, enabled: bool) -> dict[str, bool]:
    """Persist a toggle value and update the in-memory cache atomically."""
    global _cache
    with _lock:
        state = dict(_cache) if _cache is not None else dict(DEFAULT_CAPABILITIES)
        state[name] = bool(enabled)
        try:
            _storage().set_config_override(_STORAGE_KEY, state)
        except Exception as e:
            # Do not pretend a persist happened when it did not.
            raise HTTPException(500, f"failed to persist capability toggle: {e}") from e
        _cache = state
        logger.info("capability toggle %s -> %s", name, enabled)
        return dict(state)


def require_capability(name: str):
    """FastAPI dependency factory: 503 when the capability is disabled."""

    def _dep():
        if not is_enabled(name):
            raise HTTPException(
                status_code=503,
                detail=f"capability '{name}' is disabled by administrator",
            )

    return _dep
