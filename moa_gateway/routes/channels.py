"""IM channel HTTP routes (M8) — /v1/channels*.

Design ported from OpenClacky (https://github.com/clacky-ai/openclacky,
MIT License): the channel-manager surface (adapter inventory with real
connection state, sending to a chat, inbound callback dispatch) re-expressed
as gateway REST endpoints.

Endpoints:
- GET  /v1/channels                     adapter list + real status
- GET  /v1/channels/bindings            persisted channel-session bindings
- POST /v1/channels/{name}/send         outbound message (real platform call)
- POST /v1/channels/{name}/webhook      inbound callback (real per-platform
                                        signature verification + writeback)

Inbound writeback runs asynchronously by default (fast platform ack, real
background processing via the gateway chat pipeline); set
``MOA_CHANNELS_SYNC_WEBHOOK=1`` to process synchronously (debug/CI).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import require_admin, require_api_key
from ..capability_toggles import require_capability
from ..channels import ChannelGatewayBridge, SessionRouter, registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channels"])

#: references kept so background writeback tasks are never GC'd mid-flight
_BACKGROUND_TASKS: set[asyncio.Task] = set()


class SendRequest(BaseModel):
    chat_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=50_000)


def _build_adapters() -> dict[str, Any]:
    """Instantiate every registered adapter from the current environment.

    Built per request on purpose: credential changes (env) take effect without
    a restart, and deployments with no credentials get honest 'unconfigured'
    adapters instead of adapters pretending to be online.
    """
    return registry.build_all()


def _sync_webhook() -> bool:
    return os.environ.get("MOA_CHANNELS_SYNC_WEBHOOK", "") == "1"


# ---------- inventory ----------


@router.get("/v1/channels")
async def list_channels(
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("channels")),
):
    """All adapters with their real state machine status."""
    adapters = _build_adapters()
    items = [a.status() for a in adapters.values()]
    configured = [i for i in items if i["configured"]]
    return {
        "channels": items,
        "count": len(items),
        "configured": len(configured),
        "enabled": [i["platform"] for i in configured],
    }


@router.get("/v1/channels/bindings")
async def list_bindings(
    platform: str | None = None,
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("channels")),
):
    """Persisted channel->gateway-session bindings."""
    bindings = SessionRouter().list_bindings(platform=platform)
    return {"bindings": bindings, "count": len(bindings)}


# ---------- outbound ----------


@router.post("/v1/channels/{name}/send")
async def send_to_channel(
    name: str,
    req: SendRequest,
    _admin: dict = Depends(require_admin),
    _cap: None = Depends(require_capability("channels")),
):
    """Send a message out through the named platform's real API."""
    adapters = _build_adapters()
    adapter = adapters.get(name)
    if adapter is None:
        return JSONResponse(status_code=404, content={"error": f"unknown channel '{name}'"})
    status = adapter.status()
    if not status["configured"]:
        return JSONResponse(
            status_code=409,
            content={
                "error": f"channel '{name}' is unconfigured",
                "missing_env": status["missing_env"],
            },
        )
    try:
        result = await adapter.send_chunked(req.chat_id, req.text)
    finally:
        await adapter.aclose()
    if not result.ok:
        return JSONResponse(status_code=502, content=result.to_dict())
    return result.to_dict()


# ---------- inbound webhook ----------


@router.post("/v1/channels/{name}/webhook")
async def channel_webhook(
    name: str,
    request: Request,
    _cap: None = Depends(require_capability("channels")),
):
    """Inbound platform callback.

    The adapter performs the platform's real verification (Telegram secret
    token, Feishu verification token / X-Lark-Signature, DingTalk HMAC sign,
    WeCom msg_signature + AES decrypt, Discord ed25519) and answers with the
    platform's expected ack/challenge body. Verified messages are routed into
    gateway sessions and written back through the real chat pipeline.
    No auth header is required on inbound webhooks — the platform signature
    IS the authentication.
    """
    adapters = _build_adapters()
    adapter = adapters.get(name)
    if adapter is None:
        return JSONResponse(status_code=404, content={"error": f"unknown channel '{name}'"})
    status = adapter.status()
    if not status["configured"]:
        return JSONResponse(
            status_code=409,
            content={"error": f"channel '{name}' is unconfigured"},
        )

    raw_body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    query = dict(request.query_params)

    received: list[Any] = []

    async def _on_event(event) -> None:
        received.append(event)

    adapter.set_event_handler(_on_event)
    try:
        http_status, response = await adapter.handle_raw(raw_body, query, headers)
    finally:
        adapter.set_event_handler(None)

    # writeback cycle for every verified inbound event
    if received:
        bridge = ChannelGatewayBridge()
        if _sync_webhook():
            for event in received:
                await bridge.process_event(adapter, event)
        else:
            for event in received:
                task = asyncio.create_task(_writeback(bridge, adapter, event))
                _BACKGROUND_TASKS.add(task)
                task.add_done_callback(_BACKGROUND_TASKS.discard)

    await adapter.aclose()
    return JSONResponse(status_code=http_status, content=response)


async def _writeback(bridge: ChannelGatewayBridge, adapter, event) -> None:
    started = time.perf_counter()
    try:
        result = await bridge.process_event(adapter, event)
        logger.info(
            "channels: writeback done channel=%s session=%s sent=%s in %.0fms",
            event.channel, result.get("session_id"), result.get("sent"),
            (time.perf_counter() - started) * 1000.0,
        )
    except Exception as e:
        logger.error("channels: writeback failed on %s: %s", event.channel, e)
    finally:
        await adapter.aclose()
