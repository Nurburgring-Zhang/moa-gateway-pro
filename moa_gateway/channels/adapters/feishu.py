"""Feishu (Lark) channel adapter (M8) — real Open-Platform implementation.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/adapters/feishu/bot.rb`` — tenant_access_token
  acquisition (``POST /open-apis/auth/v3/tenant_access_token/internal``) with
  the 2h-minus-5min cache, message send via ``POST /open-apis/im/v1/messages
  ?receive_id_type=chat_id`` and the token-refresh retry on error code
  ``99991663``;
- ``lib/clacky/server/channel/adapters/feishu/adapter.rb`` — event-callback
  handling (schema 2.0 event unwrapping, ``url_verification`` challenge echo,
  signature verification).

Credentials (env, prefix default MOA_):
    MOA_FEISHU_APP_ID / MOA_FEISHU_APP_SECRET      app credentials (required)
    MOA_FEISHU_DOMAIN                              default https://open.feishu.cn
    MOA_FEISHU_VERIFICATION_TOKEN                  event callback token
    MOA_FEISHU_ENCRYPT_KEY                         enables X-Lark-Signature check

Spec: https://open.feishu.cn/document/server-docs/im-v1/message/create
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from ..base import BaseChannelAdapter, registry
from ..events import CHAT_GROUP, InboundEvent, SendResult

logger = logging.getLogger(__name__)

DEFAULT_DOMAIN = "https://open.feishu.cn"
TOKEN_EXPIRY_MARGIN = 300  # refresh 5 minutes before expiry (OpenClacky bot.rb)
TOKEN_RETRY_CODE = 99991663  # invalid/expired tenant token -> refresh once


class FeishuAdapter(BaseChannelAdapter):
    platform_id = "feishu"
    env_keys = (
        "FEISHU_APP_ID",
        "FEISHU_APP_SECRET",
        "FEISHU_DOMAIN",
        "FEISHU_VERIFICATION_TOKEN",
        "FEISHU_ENCRYPT_KEY",
    )
    max_send_chars = 4000

    def __init__(self, config, http_client=None):
        super().__init__(config, http_client)
        self._token: str = ""
        self._token_expires_at: float = 0.0

    # ---------- config ----------

    def validate_config(self, config: dict[str, str]) -> list[str]:
        errors = []
        if not config.get("feishu_app_id"):
            errors.append("MOA_FEISHU_APP_ID is required")
        if not config.get("feishu_app_secret"):
            errors.append("MOA_FEISHU_APP_SECRET is required")
        return errors

    @property
    def _domain(self) -> str:
        return (self.config.get("feishu_domain") or DEFAULT_DOMAIN).rstrip("/")

    # ---------- tenant access token (OpenClacky bot.rb cache) ----------

    async def _tenant_token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._token_expires_at:
            return self._token
        url = f"{self._domain}/open-apis/auth/v3/tenant_access_token/internal"
        resp = await self.http().post(
            url,
            json={
                "app_id": self.config.get("feishu_app_id", ""),
                "app_secret": self.config.get("feishu_app_secret", ""),
            },
        )
        body = resp.json()
        if body.get("code") != 0 or not body.get("tenant_access_token"):
            raise RuntimeError(f"feishu token error {body.get('code')}: {body.get('msg')}")
        self._token = body["tenant_access_token"]
        expire = float(body.get("expire", 7200))
        self._token_expires_at = now + max(60.0, expire - TOKEN_EXPIRY_MARGIN)
        return self._token

    # ---------- outbound ----------

    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        for attempt in (1, 2):
            try:
                token = await self._tenant_token(force=attempt == 2)
            except (RuntimeError, httpx.HTTPError) as e:
                return SendResult(ok=False, error=str(e))
            url = f"{self._domain}/open-apis/im/v1/messages?receive_id_type=chat_id"
            payload = {
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            }
            try:
                resp = await self.http().post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.HTTPError as e:
                return SendResult(ok=False, error=str(e))
            body = resp.json()
            code = body.get("code", -1)
            if code == 0:
                msg_id = ((body.get("data") or {}).get("message_id")) or ""
                return SendResult(ok=True, message_id=str(msg_id))
            if code == TOKEN_RETRY_CODE and attempt == 1:
                logger.info("channels: feishu token expired, refreshing and retrying")
                continue
            return SendResult(ok=False, error=f"feishu error {code}: {body.get('msg')}")
        return SendResult(ok=False, error="feishu send exhausted retries")

    # ---------- inbound: event callback ----------

    def verify_signature(
        self, body_text: str, timestamp: str, nonce: str, signature: str
    ) -> bool:
        """Official Feishu event signature:
        ``sha256(timestamp + nonce + encrypt_key + body)`` (hex digest)."""
        key = self.config.get("feishu_encrypt_key", "")
        if not key:
            return True  # signature checking not enabled
        base = f"{timestamp}{nonce}{key}{body_text}"
        expected = hashlib.sha256(base.encode("utf-8")).hexdigest()
        return _consteq(expected, signature)

    async def handle_raw(
        self, raw_body: bytes, query: dict[str, str], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """Verify the official Feishu event signature over the raw body when
        an encrypt key is configured, then delegate to handle_webhook."""
        key = self.config.get("feishu_encrypt_key", "")
        if key:
            timestamp = headers.get("x-lark-request-timestamp", "")
            nonce = headers.get("x-lark-request-nonce", "")
            signature = headers.get("x-lark-signature", "")
            body_text = raw_body.decode("utf-8", errors="replace")
            if not self.verify_signature(body_text, timestamp, nonce, signature):
                return 403, {"error": "invalid X-Lark-Signature"}
        return await super().handle_raw(raw_body, query, headers)

    async def handle_webhook(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        # URL verification handshake (echo challenge verbatim)
        if payload.get("type") == "url_verification":
            token_ok = self._verification_token_ok(payload.get("token", ""))
            if not token_ok:
                return {"error": "invalid verification token"}
            return {"challenge": payload.get("challenge", "")}

        header = payload.get("header") or {}
        if not self._verification_token_ok(header.get("token", "")):
            return {"error": "invalid verification token"}

        event_type = header.get("event_type", "")
        if event_type != "im.message.receive_v1":
            return {"ok": True, "ignored": event_type}

        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = (event.get("sender") or {}).get("sender_id") or {}
        text = ""
        if message.get("message_type") == "text":
            try:
                text = (json.loads(message.get("content") or "{}")).get("text", "")
            except json.JSONDecodeError:
                text = ""
        text = (text or "").strip()
        if not text:
            return {"ok": True, "ignored": "empty or non-text message"}

        await self.emit(
            InboundEvent(
                channel=self.platform_id,
                chat_id=str(message.get("chat_id", "")),
                user=str(sender.get("open_id") or sender.get("user_id") or ""),
                text=text,
                ts=float(header.get("create_time", 0)) / 1000.0 or time.time(),
                message_id=str(message.get("message_id", "")),
                chat_type=CHAT_GROUP if message.get("chat_type") == "group" else "direct",
                mentioned=True,
                raw=payload,
            )
        )
        return {"ok": True}

    def _verification_token_ok(self, token: str) -> bool:
        expected = self.config.get("feishu_verification_token", "")
        if not expected:
            # Fail-closed: an unauthenticated webhook must not drive the
            # chat pipeline (blind-review M-2).
            return False
        return _consteq(expected, token or "")


def _consteq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


registry.register(FeishuAdapter.platform_id, FeishuAdapter)
