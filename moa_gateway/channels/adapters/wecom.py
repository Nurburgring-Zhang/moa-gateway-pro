"""WeCom (Enterprise WeChat) channel adapter (M8) — real API implementation.

Ported/adapted from OpenClacky (https://github.com/clacky-ai/openclacky,
MIT License):
- ``lib/clacky/server/channel/adapters/wecom/adapter.rb`` — event shape
  (chat/user ids, msgtype dispatch, unsupported-type marker) and the
  bot-credential validation discipline.

OpenClacky receives WeCom over its WS client; the gateway instead implements
the documented HTTPS callback + application-message APIs (the standard
server-to-server integration), fully and for real:
- access token: ``GET /cgi-bin/gettoken`` with expiry cache;
- message push: ``POST /cgi-bin/message/send`` (text messages);
- inbound callback crypto: ``msg_signature = sha1(sort([token, timestamp,
  nonce, encrypt]))`` and AES-CBC decryption with the EncodingAESKey
  (``random16 + msg_len4 + msg + receiveid`` layout, PKCS#7 padding).

Credentials (env, prefix default MOA_):
    MOA_WECOM_CORP_ID / MOA_WECOM_CORP_SECRET / MOA_WECOM_AGENT_ID
    MOA_WECOM_TOKEN / MOA_WECOM_ENCODING_AES_KEY   (callback verification)

Spec: https://developer.work.weixin.qq.com/document/path/90236
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from ..base import BaseChannelAdapter, registry
from ..events import InboundEvent, SendResult

logger = logging.getLogger(__name__)

API_BASE = "https://qyapi.weixin.qq.com"
TOKEN_EXPIRY_MARGIN = 300


class WeComCallbackError(Exception):
    """Callback verification/decryption failure (rejected by the route)."""


class WeComAdapter(BaseChannelAdapter):
    platform_id = "wecom"
    env_keys = (
        "WECOM_CORP_ID",
        "WECOM_CORP_SECRET",
        "WECOM_AGENT_ID",
        "WECOM_TOKEN",
        "WECOM_ENCODING_AES_KEY",
    )
    max_send_chars = 2048  # WeCom text message limit

    def __init__(self, config, http_client=None):
        super().__init__(config, http_client)
        self._token: str = ""
        self._token_expires_at: float = 0.0

    # ---------- config ----------

    def validate_config(self, config: dict[str, str]) -> list[str]:
        errors = []
        if not config.get("wecom_corp_id"):
            errors.append("MOA_WECOM_CORP_ID is required")
        if not config.get("wecom_corp_secret"):
            errors.append("MOA_WECOM_CORP_SECRET is required")
        if not config.get("wecom_agent_id"):
            errors.append("MOA_WECOM_AGENT_ID is required")
        return errors

    # ---------- official callback crypto ----------

    @staticmethod
    def compute_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
        """sha1(sorted([token, timestamp, nonce, encrypt])) — official algo."""
        parts = sorted([token, timestamp, nonce, encrypt])
        return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()

    def verify_and_decrypt(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> str:
        """Verify msg_signature then AES-decrypt; returns plaintext XML."""
        token = self.config.get("wecom_token", "")
        aes_b64 = self.config.get("wecom_encoding_aes_key", "")
        if not token or not aes_b64:
            raise WeComCallbackError("callback crypto not configured")
        expected = self.compute_signature(token, timestamp, nonce, encrypt)
        if expected != (msg_signature or ""):
            raise WeComCallbackError("msg_signature mismatch")
        aes_key = base64.b64decode(aes_b64 + "=")  # 43-char key + '=' -> 32 bytes
        data = base64.b64decode(encrypt)
        cipher = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16]))
        dec = cipher.decryptor()
        plain = dec.update(data) + dec.finalize()
        pad = plain[-1]
        if 1 <= pad <= 32:
            plain = plain[:-pad]
        # layout: random(16) + msg_len(4 big-endian) + msg + receiveid
        msg_len = int.from_bytes(plain[16:20], "big")
        msg = plain[20:20 + msg_len].decode("utf-8")
        receiveid = plain[20 + msg_len:].decode("utf-8")
        if receiveid != self.config.get("wecom_corp_id", ""):
            raise WeComCallbackError("receiveid does not match corp id")
        return msg

    @staticmethod
    def parse_message_xml(xml_text: str) -> dict[str, str]:
        root = ET.fromstring(xml_text)
        return {child.tag: (child.text or "") for child in root}

    async def handle_raw(
        self, raw_body: bytes, query: dict[str, str], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """URL verification (GET semantics) and encrypted message callback."""
        msg_signature = query.get("msg_signature", "")
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")

        if not raw_body.strip():
            return 400, {"error": "empty body"}

        # URL verification handshake: body XML carries <Encrypt>echostr</Encrypt>
        if query.get("echostr"):
            try:
                echo = self.verify_and_decrypt(
                    msg_signature, timestamp, nonce, query["echostr"]
                )
            except WeComCallbackError as e:
                return 403, {"error": str(e)}
            return 200, {"echostr": echo}

        try:
            outer = self.parse_message_xml(raw_body.decode("utf-8"))
            encrypt = outer.get("Encrypt", "")
            xml_text = self.verify_and_decrypt(msg_signature, timestamp, nonce, encrypt)
        except (WeComCallbackError, ET.ParseError, UnicodeDecodeError) as e:
            return 403, {"error": f"callback verification failed: {e}"}

        msg = self.parse_message_xml(xml_text)
        msg_type = msg.get("MsgType", "")
        from_user = msg.get("FromUserName", "")
        if msg_type != "text":
            await self.emit(
                InboundEvent(
                    channel=self.platform_id,
                    chat_id=from_user,
                    user=from_user,
                    text="",
                    message_id=msg.get("MsgId", ""),
                    unsupported=True,
                    raw=msg,
                )
            )
            return 200, {"ok": True, "ignored": msg_type}
        text = (msg.get("Content") or "").strip()
        if not text:
            return 200, {"ok": True, "ignored": "empty"}
        try:
            create_ts = float(msg.get("CreateTime", "") or 0)
        except ValueError:
            create_ts = 0.0
        await self.emit(
            InboundEvent(
                channel=self.platform_id,
                chat_id=from_user,  # single chat: reply targets the sender
                user=from_user,
                text=text,
                ts=create_ts or time.time(),
                message_id=msg.get("MsgId", ""),
                chat_type="direct",
                mentioned=True,
                raw=msg,
            )
        )
        return 200, {"ok": True}

    # ---------- access token + outbound ----------

    async def _access_token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and now < self._token_expires_at:
            return self._token
        resp = await self.http().get(
            f"{API_BASE}/cgi-bin/gettoken",
            params={
                "corpid": self.config.get("wecom_corp_id", ""),
                "corpsecret": self.config.get("wecom_corp_secret", ""),
            },
        )
        body = resp.json()
        if body.get("errcode") != 0 or not body.get("access_token"):
            raise RuntimeError(f"wecom token error {body.get('errcode')}: {body.get('errmsg')}")
        self._token = body["access_token"]
        self._token_expires_at = now + max(
            60.0, float(body.get("expires_in", 7200)) - TOKEN_EXPIRY_MARGIN
        )
        return self._token

    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        try:
            token = await self._access_token()
        except (RuntimeError, httpx.HTTPError) as e:
            return SendResult(ok=False, error=str(e))
        payload = {
            "touser": chat_id or "@all",
            "msgtype": "text",
            "agentid": int(self.config.get("wecom_agent_id") or 0),
            "text": {"content": text},
        }
        try:
            resp = await self.http().post(
                f"{API_BASE}/cgi-bin/message/send",
                params={"access_token": token},
                json=payload,
            )
        except httpx.HTTPError as e:
            return SendResult(ok=False, error=str(e))
        body = resp.json()
        if body.get("errcode") == 0:
            return SendResult(ok=True, message_id=str(body.get("msgid", "")))
        return SendResult(ok=False, error=f"wecom errcode {body.get('errcode')}: {body.get('errmsg')}")


registry.register(WeComAdapter.platform_id, WeComAdapter)
