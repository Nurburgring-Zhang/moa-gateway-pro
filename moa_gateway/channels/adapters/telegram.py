"""Telegram channel adapter (M8) — real Bot API implementation.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/adapters/telegram/adapter.rb`` — long-poll
  ``getUpdates`` receive loop, group-mention gating, ``MAX_MESSAGE_CHARS =
  4000`` chunking and the markdown-failure plain-text fallback;
- ``lib/clacky/server/channel/adapters/telegram/api_client.rb`` — request
  shape ``POST https://<base>/bot<TOKEN>/<method>`` with the ``body["ok"]``
  unwrap, configurable ``base_url`` for self-hosted Bot API servers.

Credentials (env, with the gateway prefix, default MOA_):
    MOA_TELEGRAM_TOKEN        bot token (required)
    MOA_TELEGRAM_BASE_URL     API base (default https://api.telegram.org)
    MOA_TELEGRAM_PARSE_MODE   Markdown | HTML | "" (default Markdown)
    MOA_TELEGRAM_SECRET_TOKEN webhook secret header value (optional)

Spec: https://core.telegram.org/bots/api
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from ..base import BaseChannelAdapter, registry
from ..events import CHAT_GROUP, InboundEvent, SendResult

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.telegram.org"
LONG_POLL_TIMEOUT = 25   # server holds getUpdates open up to this long
MAX_MESSAGE_CHARS = 4000  # OpenClacky telegram adapter cap (API hard limit 4096)


class TelegramAdapter(BaseChannelAdapter):
    platform_id = "telegram"
    env_keys = ("TELEGRAM_TOKEN", "TELEGRAM_BASE_URL", "TELEGRAM_PARSE_MODE", "TELEGRAM_SECRET_TOKEN")
    max_send_chars = MAX_MESSAGE_CHARS

    # ---------- config ----------

    def validate_config(self, config: dict[str, str]) -> list[str]:
        errors = []
        if not config.get("telegram_token"):
            errors.append("MOA_TELEGRAM_TOKEN is required")
        return errors

    @property
    def _token(self) -> str:
        return self.config.get("telegram_token", "")

    @property
    def _base_url(self) -> str:
        return (self.config.get("telegram_base_url") or DEFAULT_BASE_URL).rstrip("/")

    @property
    def _parse_mode(self) -> str:
        return self.config.get("telegram_parse_mode") or "Markdown"

    # ---------- Bot API plumbing ----------

    async def _api(self, method: str, payload: dict[str, Any], timeout: float = 35.0) -> Any:
        """POST https://<base>/bot<TOKEN>/<method>; unwrap ``body['ok']``."""
        url = f"{self._base_url}/bot{self._token}/{method}"
        resp = await self.http().post(url, json=payload, timeout=timeout)
        try:
            body = resp.json()
        except ValueError as e:
            raise RuntimeError(
                f"telegram {method}: non-JSON response (HTTP {resp.status_code})"
            ) from e
        if body.get("ok"):
            return body.get("result")
        code = body.get("error_code", resp.status_code)
        desc = body.get("description", "")
        raise RuntimeError(f"telegram API error {code} on {method}: {desc}")

    # ---------- inbound: long-poll loop ----------

    async def _run_loop(self) -> None:
        """Long-poll getUpdates until stopped (OpenClacky receive loop)."""
        offset: int | None = None
        from ...config import get_settings

        interval = get_settings().channels.poll_interval_s
        while self._running:
            try:
                params: dict[str, Any] = {
                    "timeout": LONG_POLL_TIMEOUT,
                    "allowed_updates": ["message"],
                }
                if offset is not None:
                    params["offset"] = offset
                updates = await self._api("getUpdates", params, timeout=LONG_POLL_TIMEOUT + 10)
                self._error = ""
                for upd in updates or []:
                    offset = max(offset or 0, int(upd.get("update_id", 0))) + 1
                    await self._handle_update(upd)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self._error = str(e)
                logger.warning("channels: telegram poll error: %s (retrying)", e)
                await asyncio.sleep(max(1.0, interval))

    async def _handle_update(self, upd: dict[str, Any]) -> None:
        msg = upd.get("message") or {}
        text = (msg.get("text") or "").strip()
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id or not text:
            return
        await self.emit(
            InboundEvent(
                channel=self.platform_id,
                chat_id=chat_id,
                user=str((msg.get("from") or {}).get("id", "")),
                text=text,
                ts=float(msg.get("date", 0)) or time.time(),
                message_id=str(msg.get("message_id", "")),
                chat_type=CHAT_GROUP if chat.get("type") in ("group", "supergroup") else "direct",
                mentioned=self._group_mentioned(msg),
                raw=upd,
            )
        )

    @staticmethod
    def _group_mentioned(msg: dict[str, Any]) -> bool:
        """OpenClacky group_mention?: bot addressed via @mention or reply."""
        if msg.get("reply_to_message"):
            return True
        entities = msg.get("entities") or []
        return any(e.get("type") in ("mention", "text_mention") for e in entities)

    # ---------- outbound ----------

    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if self._parse_mode:
            payload["parse_mode"] = self._parse_mode
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            result = await self._api("sendMessage", payload)
            return SendResult(ok=True, message_id=str(result.get("message_id", "")))
        except RuntimeError as e:
            # OpenClacky fallback: markdown parse failure -> retry plain text
            if "parse" in str(e).lower() and payload.get("parse_mode"):
                payload.pop("parse_mode", None)
                try:
                    result = await self._api("sendMessage", payload)
                    return SendResult(ok=True, message_id=str(result.get("message_id", "")))
                except RuntimeError as e2:
                    return SendResult(ok=False, error=str(e2))
            return SendResult(ok=False, error=str(e))
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=str(e))

    # ---------- inbound: webhook mode ----------

    async def handle_webhook(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """Telegram webhook delivery. When MOA_TELEGRAM_SECRET_TOKEN is set the
        ``X-Telegram-Bot-Api-Secret-Token`` header must match (official
        mechanism), otherwise the update is rejected."""
        expected = self.config.get("telegram_secret_token", "")
        if not expected:
            return {
                "ok": False,
                "error": "webhook secret not configured; set MOA_TELEGRAM_SECRET_TOKEN",
            }
        got = headers.get("x-telegram-bot-api-secret-token", "")
        if got != expected:
            return {"ok": False, "error": "invalid secret token"}
        await self._handle_update(payload)
        return {"ok": True}


registry.register(TelegramAdapter.platform_id, TelegramAdapter)
