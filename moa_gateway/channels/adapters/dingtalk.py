"""DingTalk channel adapter (M8) — real robot webhook implementation.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/adapters/dingtalk/adapter.rb`` — inbound robot
  callback parsing (``msgtype`` extraction, group @-mention gating), the
  ``sessionWebhook`` reply cache with expiry (5-minute safety margin) and
  markdown/text outbound via the webhook.

Credentials (env, prefix default MOA_):
    MOA_DINGTALK_CLIENT_ID      robot clientId (required, identification)
    MOA_DINGTALK_CLIENT_SECRET  robot clientSecret (required, signing key)
    MOA_DINGTALK_WEBHOOK_URL    fallback custom-robot webhook for proactive push

Signature (official DingTalk algorithm, used both for verifying inbound robot
callbacks and for signing outbound webhook URLs):
    stringToSign = "{timestamp_ms}\\n{secret}"
    sign = url_quote(base64(HMAC-SHA256(key=secret, msg=stringToSign)))
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Any

import httpx

from ..base import BaseChannelAdapter, registry
from ..events import CHAT_GROUP, InboundEvent, SendResult

logger = logging.getLogger(__name__)

#: OpenClacky dingtalk adapter: retire cached sessionWebhooks 5 min early
WEBHOOK_EXPIRY_MARGIN_MS = 5 * 60 * 1000
#: inbound signatures older than this are rejected (replay protection)
MAX_SIGNATURE_AGE_MS = 60 * 60 * 1000


def dingtalk_sign(timestamp_ms: int | str, secret: str) -> str:
    """Official DingTalk sign algorithm (url-quoted base64 HMAC-SHA256)."""
    string_to_sign = f"{timestamp_ms}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    return urllib.parse.quote_plus(base64.b64encode(digest).decode("ascii"))


class DingTalkAdapter(BaseChannelAdapter):
    platform_id = "dingtalk"
    env_keys = ("DINGTALK_CLIENT_ID", "DINGTALK_CLIENT_SECRET", "DINGTALK_WEBHOOK_URL")
    max_send_chars = 4000

    def __init__(self, config, http_client=None):
        super().__init__(config, http_client)
        #: conversationId -> (sessionWebhook, expiredAt_ms)
        self._session_webhooks: dict[str, tuple[str, float]] = {}

    # ---------- config ----------

    def validate_config(self, config: dict[str, str]) -> list[str]:
        errors = []
        if not config.get("dingtalk_client_id"):
            errors.append("MOA_DINGTALK_CLIENT_ID is required")
        if not config.get("dingtalk_client_secret"):
            errors.append("MOA_DINGTALK_CLIENT_SECRET is required")
        return errors

    @property
    def _secret(self) -> str:
        return self.config.get("dingtalk_client_secret", "")

    # ---------- signature verification ----------

    def verify_callback(self, timestamp: str, sign: str) -> bool:
        """Verify inbound robot callback headers per the official algorithm."""
        if not self._secret:
            return False
        try:
            ts = int(timestamp)
        except (TypeError, ValueError):
            return False
        now_ms = int(time.time() * 1000)
        if abs(now_ms - ts) > MAX_SIGNATURE_AGE_MS:
            return False
        expected = dingtalk_sign(ts, self._secret)
        return hmac.compare_digest(expected, sign or "")

    # ---------- inbound ----------

    async def handle_webhook(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        timestamp = headers.get("timestamp", "")
        sign = headers.get("sign", "")
        if not self.verify_callback(timestamp, sign):
            return {"error": "invalid signature"}

        msgtype = payload.get("msgtype", "")
        conversation_id = str(payload.get("conversationId", ""))
        sender_id = str(payload.get("senderId", "") or payload.get("senderNick", ""))
        conversation_type = str(payload.get("conversationType", "1"))
        chat_type = CHAT_GROUP if conversation_type == "2" else "direct"

        text = ""
        if msgtype == "text":
            text = ((payload.get("text") or {}).get("content") or "").strip()
        elif msgtype == "richText":
            parts = ((payload.get("content") or {}).get("richText")) or []
            text = " ".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
        else:
            # picture/file/audio: record the event but mark unsupported
            await self.emit(
                InboundEvent(
                    channel=self.platform_id,
                    chat_id=conversation_id,
                    user=sender_id,
                    text="",
                    ts=time.time(),
                    message_id=str(payload.get("msgId", "")),
                    chat_type=chat_type,
                    unsupported=True,
                    raw=payload,
                )
            )
            return {"ok": True, "ignored": msgtype}

        if not text or not conversation_id:
            return {"ok": True, "ignored": "empty"}

        # OpenClacky: cache the sessionWebhook for replying, honoring expiry
        webhook = payload.get("sessionWebhook", "")
        expired_at = float(payload.get("sessionWebhookExpiredTime", 0))
        if webhook and conversation_id:
            self._session_webhooks[conversation_id] = (webhook, expired_at)

        at_users = payload.get("atUsers") or []
        await self.emit(
            InboundEvent(
                channel=self.platform_id,
                chat_id=conversation_id,
                user=sender_id,
                text=text,
                ts=float(payload.get("createAt", 0)) / 1000.0 or time.time(),
                message_id=str(payload.get("msgId", "")),
                chat_type=chat_type,
                mentioned=(chat_type != CHAT_GROUP) or bool(at_users),
                raw=payload,
            )
        )
        return {"ok": True}

    # ---------- outbound ----------

    def _reply_webhook(self, chat_id: str) -> str | None:
        entry = self._session_webhooks.get(chat_id)
        if not entry:
            return None
        webhook, expired_at = entry
        now_ms = time.time() * 1000
        if expired_at and now_ms > (expired_at - WEBHOOK_EXPIRY_MARGIN_MS):
            self._session_webhooks.pop(chat_id, None)
            logger.info("channels: dingtalk sessionWebhook expired for %s", chat_id)
            return None
        return webhook

    def _signed_url(self, base_url: str) -> str:
        ts = int(time.time() * 1000)
        sign = dingtalk_sign(ts, self._secret)
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}timestamp={ts}&sign={sign}"

    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        """Reply via cached sessionWebhook; otherwise push through the
        configured custom-robot webhook URL (signed)."""
        url = self._reply_webhook(chat_id)
        signed = False
        if url is None:
            fallback = self.config.get("dingtalk_webhook_url", "")
            if not fallback:
                return SendResult(
                    ok=False,
                    error="no sessionWebhook cached and MOA_DINGTALK_WEBHOOK_URL not set",
                )
            url = self._signed_url(fallback)
            signed = True
        payload = {"msgtype": "text", "text": {"content": text}}
        try:
            resp = await self.http().post(url, json=payload)
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=str(e))
        try:
            body = resp.json()
        except ValueError:
            return SendResult(ok=False, error=f"non-JSON response (HTTP {resp.status_code})")
        errcode = body.get("errcode", -1)
        if errcode == 0:
            return SendResult(ok=True, message_id=str(body.get("msgid", "")))
        # stale session webhook: drop it so next send re-resolves
        if not signed:
            self._session_webhooks.pop(chat_id, None)
        return SendResult(ok=False, error=f"dingtalk errcode {errcode}: {body.get('errmsg')}")


registry.register(DingTalkAdapter.platform_id, DingTalkAdapter)
