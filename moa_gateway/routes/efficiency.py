"""Token-efficiency HTTP routes (M6, OpenClacky port).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License)
— the HTTP surface is NEW to the gateway (OpenClacky exposes these behaviors
in-process); the underlying engines in ``moa_gateway.efficiency`` are ports.

Endpoints:
- POST /v1/efficiency/prepare           — apply the double cache-marker
                                          strategy to a message sequence
- POST /v1/efficiency/compress-session  — Insert-then-Compress one session
- GET  /v1/efficiency/metrics           — cache hit-rate / savings counters

Auth: ``require_api_key``. Capability gate: ``token_efficiency`` (503 when
disabled in admin-ui).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..efficiency.compressor import SessionCompressor
from ..efficiency.markers import MARKER_COUNT, apply_cache_markers
from ..efficiency.metrics import get_metrics
from ..efficiency.system_prompt import strip_internal_fields

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/efficiency", tags=["efficiency"])

# Hard safety cap on request bodies (messages per call). The compression
# thresholds themselves live in settings.efficiency.
_MAX_MESSAGES = 5000


# ─────────────────────────── request models ───────────────────────────


class PrepareRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = Field(
        default=True,
        description="False returns deep-copied messages without markers.",
    )
    marker_count: int = Field(
        default=MARKER_COUNT,
        ge=0,
        le=8,
        description="Trailing breakpoints to mark (OpenClacky default: 2).",
    )
    strip_internal: bool = Field(
        default=False,
        description="Strip system_injected/compressed_summary/etc. fields from the response.",
    )


class CompressSessionRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str = Field(default="adhoc", min_length=1, max_length=200)
    force: bool = Field(
        default=False,
        description=(
            "False = threshold gate (150K tokens / 200 msgs). "
            "True = idle gate (20K-token floor, > max_recent+1 msgs)."
        ),
    )
    strip_internal: bool = Field(default=False)


# ─────────────────────────── endpoints ───────────────────────────


@router.post("/prepare")
async def prepare_messages(
    body: PrepareRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("token_efficiency")),
) -> dict[str, Any]:
    """Apply the OpenClacky double cache-marker strategy to *messages*.

    The last ``marker_count`` eligible trailing messages receive
    ``content[-1].cache_control = {"type": "ephemeral"}`` (system-injected
    scaffolding messages are skipped). On the next turn the provider serves
    everything up to the OLD marker from cache (READ) and writes the new
    tail (WRITE) — one cache rebuild per turn instead of per message.
    """
    if len(body.messages) > _MAX_MESSAGES:
        raise HTTPException(status_code=413, detail="too many messages")
    marked, indices = apply_cache_markers(
        body.messages, enabled=body.enabled, marker_count=body.marker_count
    )
    metrics = get_metrics()
    metrics.record_prepare(len(body.messages), len(indices))
    out_messages = strip_internal_fields(marked) if body.strip_internal else marked
    return {
        "messages": out_messages,
        "cache_control_indices": indices,
        "markers_applied": len(indices),
        "enabled": body.enabled,
        "strategy": "ephemeral-double-marker",
    }


@router.post("/compress-session")
async def compress_session(
    body: CompressSessionRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("token_efficiency")),
) -> dict[str, Any]:
    """Run Insert-then-Compress over one session's messages.

    Archives the compressed-away turns as a chunk MD file under
    ``settings.efficiency.archive_dir`` (real files on disk) and returns the
    rebuilt history: system + framed compressed summary + recent window with
    tool-call pairs preserved.
    """
    if len(body.messages) > _MAX_MESSAGES:
        raise HTTPException(status_code=413, detail="too many messages")
    engine = SessionCompressor()
    try:
        result = engine.compress(list(body.messages), body.session_id, force=body.force)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    get_metrics().record_compression(
        result.compressed,
        tokens_before=result.tokens_before,
        tokens_after=result.tokens_after,
        archived_messages=result.archived_messages,
    )
    payload = result.to_dict()
    if body.strip_internal:
        payload["messages"] = strip_internal_fields(result.messages)
    return payload


@router.get("/metrics")
async def efficiency_metrics(
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("token_efficiency")),
) -> dict[str, Any]:
    """Cache hit-rate and savings counters (process lifetime, resettable)."""
    return get_metrics().snapshot()


__all__ = ["router", "PrepareRequest", "CompressSessionRequest"]
