"""moa_gateway.routes.a2a — A2A HTTP surface (Agent Card + JSON-RPC 2.0).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT license):
  - src/app/.well-known/agent.json/route.ts  ->  GET /.well-known/agent.json
  - src/app/a2a/route.ts                     ->  POST /v1/a2a

Auth layering (M5 contract):
  - /.well-known/agent.json is PUBLIC (A2A discovery protocol semantics —
    other agents must be able to fetch the card without credentials); it is
    still gated by the ``a2a`` capability toggle (503 when disabled).
  - POST /v1/a2a requires a gateway API key (``require_api_key``), same
    credential surface as /v1/chat/completions.

Task ownership (OmniRoute GHSA-jcm5-6wpp-wjj8 port): every task is scoped to
a stable hash of the caller's API-key identity, so one key can never read or
cancel another key's tasks. The raw key is never stored.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response

from ..a2a.agent_card import build_agent_card
from ..a2a.protocol import handle_raw_body
from ..auth import require_api_key
from ..capability_toggles import require_capability

logger = logging.getLogger(__name__)

router = APIRouter(tags=["a2a"])


def _owner_from_key_info(key_info: dict[str, Any]) -> str:
    """Stable owner id = sha256(key identity)[:32] (OmniRoute resolveA2AOwner).

    Uses the key_id/name resolved by auth — never the raw key material.
    """
    identity = str(key_info.get("key_id") or key_info.get("name") or "anonymous")
    source = f"{key_info.get('source', '')}:{identity}"
    return hashlib.sha256(source.encode("utf-8")).hexdigest()[:32]


@router.get(
    "/.well-known/agent.json",
    dependencies=[Depends(require_capability("a2a"))],
)
async def agent_card(request: Request):
    """A2A Agent Card — public discovery endpoint (A2A v0.3)."""
    base_url = str(request.base_url).rstrip("/") if request.base_url else ""
    card = build_agent_card(base_url)
    return JSONResponse(
        content=card,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.post(
    "/v1/a2a",
    dependencies=[Depends(require_capability("a2a"))],
)
async def a2a_jsonrpc(request: Request, key_info: dict[str, Any] = Depends(require_api_key)):
    """A2A JSON-RPC 2.0 endpoint.

    Methods: skills/list, skills/invoke, message/send, tasks/get,
    tasks/cancel (+ JSON-RPC batches). The raw body is parsed here (instead
    of a Pydantic body model) so malformed JSON yields the JSON-RPC
    ``-32700 Parse error`` envelope rather than a FastAPI 422.
    """
    raw = await request.body()
    owner = _owner_from_key_info(key_info)
    payload, status = await handle_raw_body(raw, owner=owner)
    if payload is None:
        # Lone notification (or all-notification batch): no response body per
        # JSON-RPC 2.0 semantics.
        return Response(status_code=status)
    return JSONResponse(content=payload, status_code=status)
