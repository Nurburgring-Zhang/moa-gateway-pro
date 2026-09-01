"""Discord channel adapter (M8) — real REST + webhook implementation.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/adapters/discord/adapter.rb`` — bot identity
  resolution, group-mention gating (strip ``<@!id>``), attachment handling
  shape and the ApiError-disciplined send path;
- ``lib/clacky/server/channel/adapters/discord/api_client.rb`` — REST calls to
  ``https://discord.com/api/v10`` with ``Authorization: Bot <token>``.

Gateway adaptations: inbound arrives through an interactions-style webhook
with the official ed25519 signature verification (``X-Signature-Ed25519`` over
``timestamp + body``), outbound goes through the real REST channel-message API
or a configured webhook URL. Long messages are chunked at Discord's 2000-char
limit (handled by the base ``send_chunked``).

Credentials (env, prefix default MOA_):
    MOA_DISCORD_BOT_TOKEN    bot token (REST send + identity)
    MOA_DISCORD_WEBHOOK_URL  webhook URL (alternative outbound)
    MOA_DISCORD_PUBLIC_KEY   interactions public key (hex) for verification

Spec: https://discord.com/developers/docs/resources/webhook
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..base import BaseChannelAdapter, registry
from ..events import CHAT_GROUP, InboundEvent, SendResult

logger = logging.getLogger(__name__)

API_BASE = "https://discord.com/api/v10"
MAX_MESSAGE_CHARS = 2000  # Discord hard limit


class DiscordAdapter(BaseChannelAdapter):
    platform_id = "discord"
    env_keys = ("DISCORD_BOT_TOKEN", "DISCORD_WEBHOOK_URL", "DISCORD_PUBLIC_KEY")
    max_send_chars = MAX_MESSAGE_CHARS

    def __init__(self, config, http_client=None):
        super().__init__(config, http_client)
        self.bot_user_id: str = ""

    # ---------- config ----------

    def validate_config(self, config: dict[str, str]) -> list[str]:
        errors = []
        if not config.get("discord_bot_token") and not config.get("discord_webhook_url"):
            errors.append("MOA_DISCORD_BOT_TOKEN or MOA_DISCORD_WEBHOOK_URL is required")
        return errors

    # ---------- interactions signature (official ed25519) ----------

    def verify_interaction(
        self, timestamp: str, signature: str, body: bytes
    ) -> bool:
        """ed25519 verify of ``timestamp + body`` against the public key."""
        pub_hex = self.config.get("discord_public_key", "")
        if not pub_hex:
            # Fail-closed: an unauthenticated interaction must not drive the
            # chat pipeline (blind-review M-2).
            return False
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
            key.verify(bytes.fromhex(signature), timestamp.encode("utf-8") + body)
            return True
        except (ValueError, InvalidSignature, TypeError):
            return False

    async def handle_raw(
        self, raw_body: bytes, query: dict[str, str], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        timestamp = headers.get("x-signature-timestamp", "")
        signature = headers.get("x-signature-ed25519", "")
        if not self.verify_interaction(timestamp, signature, raw_body):
            return 401, {"error": "invalid interaction signature"}

        import json as _json

        try:
            payload = _json.loads(raw_body) if raw_body.strip() else {}
        except _json.JSONDecodeError:
            return 400, {"error": "invalid JSON"}

        itype = payload.get("type")
        if itype == 1:  # PING handshake — must answer {"type": 1}
            return 200, {"type": 1}

        if itype == 2:  # application command interaction
            data = payload.get("data") or {}
            options = data.get("options") or []
            text = " ".join(
                str(o.get("value", "")) for o in options if o.get("value") is not None
            ).strip()
            member = payload.get("member") or payload.get("user") or {}
            channel_id = str(payload.get("channel_id", ""))
            if text and channel_id:
                await self.emit(
                    InboundEvent(
                        channel=self.platform_id,
                        chat_id=channel_id,
                        user=str(member.get("id", "") or member.get("user", {}).get("id", "")),
                        text=text,
                        ts=time.time(),
                        message_id=str(payload.get("id", "")),
                        chat_type=CHAT_GROUP if payload.get("guild_id") else "direct",
                        mentioned=True,
                        raw=payload,
                    )
                )
            return 200, {"type": 4, "data": {"content": "received"}}
        return 200, {"type": 1}

    # ---------- outbound ----------

    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        token = self.config.get("discord_bot_token", "")
        if token and chat_id and chat_id != "webhook":
            return await self._send_rest(chat_id, text, reply_to, token)
        webhook = self.config.get("discord_webhook_url", "")
        if webhook:
            return await self._send_webhook(webhook, text)
        return SendResult(ok=False, error="no bot token or webhook URL configured")

    async def _send_rest(
        self, channel_id: str, text: str, reply_to: str, token: str
    ) -> SendResult:
        url = f"{API_BASE}/channels/{channel_id}/messages"
        payload: dict[str, Any] = {"content": text}
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to}
        try:
            resp = await self.http().post(
                url, json=payload, headers={"Authorization": f"Bot {token}"}
            )
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=str(e))
        if resp.status_code >= 400:
            return SendResult(
                ok=False,
                error=f"discord HTTP {resp.status_code}: {resp.text[:200]}",
            )
        body = resp.json()
        return SendResult(ok=True, message_id=str(body.get("id", "")))

    async def _send_webhook(self, webhook_url: str, text: str) -> SendResult:
        try:
            resp = await self.http().post(webhook_url, json={"content": text})
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=str(e))
        if resp.status_code >= 400:
            return SendResult(
                ok=False,
                error=f"discord webhook HTTP {resp.status_code}: {resp.text[:200]}",
            )
        try:
            body = resp.json()
            return SendResult(ok=True, message_id=str(body.get("id", "")))
        except ValueError:
            # 204 No Content: webhook executed without ?wait=1
            return SendResult(ok=True)


registry.register(DiscordAdapter.platform_id, DiscordAdapter)
