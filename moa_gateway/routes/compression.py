"""Compression HTTP routes — /v1/compression/*.

Real implementation backed by :mod:`moa_gateway.compression` (ported from
OmniRoute, https://github.com/diegosouzapw/OmniRoute, MIT License):
RTK CLI-output filters, Caveman semantic condensation, lite whitespace/dedup
passes, ultra heuristic pruning, aggressive and stacked two-stage pipelines,
all guarded by the deterministic fidelity gate.

Auth: valid API key + the ``stacked_compression`` capability toggle.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..compression import DEFAULT_STACKED_PIPELINE, MODES, get_engine, get_stats_store
from ..config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["compression"])

_MODE_DESCRIPTIONS: dict[str, str] = {
    "off": "Pass-through; no compression is applied.",
    "lite": "Whitespace collapse, system-prompt dedup, tool-result truncation, "
    "consecutive duplicate removal, image placeholders for non-vision models.",
    "standard": "Caveman semantic condensation at lite intensity "
    "(filler / hedging / redundant-phrase rules).",
    "aggressive": "RTK tool-result filters + lite passes + Caveman full "
    "intensity on every message except the live user turn.",
    "ultra": "Information-density token pruning over prose (structured "
    "content stays verbatim).",
    "rtk": "CLI / structured tool-output compression using the bundled "
    "RTK filter set (git, docker, npm, pytest, ...).",
    "stacked": "Two-stage pipeline: RTK (standard) followed by Caveman (full).",
}


class CompressRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2_000_000)
    messages: list[dict[str, Any]] | None = None
    mode: str | None = Field(default=None, description="One of: " + ", ".join(MODES))
    model: str | None = Field(default=None, max_length=200)
    stacked_pipeline: list[dict[str, str]] | None = Field(
        default=None,
        description="Optional stacked-mode engine list, e.g. "
        "[{\"engine\": \"rtk\", \"intensity\": \"standard\"}]",
    )

    @model_validator(mode="after")
    def _check_payload(self) -> "CompressRequest":
        if not self.text and not self.messages:
            raise ValueError("either 'text' or 'messages' must be provided")
        if self.text and self.messages:
            raise ValueError("provide either 'text' or 'messages', not both")
        if self.mode is not None and self.mode not in MODES:
            raise ValueError(f"mode must be one of: {', '.join(MODES)}")
        if self.messages is not None:
            if len(self.messages) > 500:
                raise ValueError("messages exceeds the 500-entry limit")
            for message in self.messages:
                if "role" not in message:
                    raise ValueError("every message requires a 'role' field")
        if self.stacked_pipeline is not None:
            if len(self.stacked_pipeline) > 8:
                raise ValueError("stacked_pipeline supports at most 8 steps")
            for step in self.stacked_pipeline:
                if "engine" not in step:
                    raise ValueError("every stacked step requires an 'engine' field")
        return self


@router.post("/v1/compression/compress")
async def compress(
    payload: CompressRequest,
    _auth: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("stacked_compression")),
) -> dict[str, Any]:
    """Compress a plain text or a chat-style messages list."""
    settings = get_settings()
    if not settings.compression.enabled:
        raise HTTPException(status_code=503, detail="compression is disabled by configuration")

    engine = get_engine()
    mode = payload.mode or settings.compression.default_mode or "off"

    if payload.text is not None:
        try:
            result = engine.compress_text(payload.text, mode=mode)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"result": result}

    try:
        outcome = engine.compress_body(
            {"messages": payload.messages, **({"model": payload.model} if payload.model else {})},
            mode=mode,
            model=payload.model,
            stacked_pipeline=payload.stacked_pipeline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "result": {
            "body": outcome.body,
            **outcome.to_dict(),
        }
    }


@router.get("/v1/compression/modes")
async def list_modes(
    _auth: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("stacked_compression")),
) -> dict[str, Any]:
    settings = get_settings()
    config = settings.compression
    return {
        "modes": [
            {
                "name": name,
                "description": _MODE_DESCRIPTIONS[name],
                "is_default": name == (config.default_mode or "off"),
            }
            for name in MODES
        ],
        "config": {
            "enabled": config.enabled,
            "apply_to_chat": config.apply_to_chat,
            "default_mode": config.default_mode,
            "fidelity_gate": config.fidelity_gate,
            "preserve_cache_control": config.preserve_cache_control,
            "hard_budget_chars": config.hard_budget_chars,
            "max_input_chars": config.max_input_chars,
        },
        "stacked_default_pipeline": [
            {"engine": step["engine"], "intensity": step["intensity"]}
            for step in DEFAULT_STACKED_PIPELINE
        ],
    }


@router.get("/v1/compression/stats")
async def compression_stats(
    _auth: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("stacked_compression")),
) -> dict[str, Any]:
    store = get_stats_store()
    return store.snapshot()
