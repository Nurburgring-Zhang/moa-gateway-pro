"""M8 IM-channels tests — adapters, protocols, session routing, bridge, routes.

Controlled test-double boundary (per task rule 7): outbound/inbound platform
HTTP is driven through ``httpx.MockTransport`` injected as the adapter's
transport — the *transport layer only*. Every line of adapter/protocol logic
(URL shapes, signing, crypto, token caches, state machine, chunking) runs for
real. The gateway chat pipeline is exercised through the real ModelPool whose
key-less endpoints resolve to the gateway's own documented MockProvider
behavior (asserted via ``provider == "mock"``).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse
from unittest.mock import patch

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from moa_gateway.channels import (
    BaseChannelAdapter,
    ChannelGatewayBridge,
    InboundEvent,
    SendResult,
    SessionRouter,
    channel_key,
    registry,
    sanitize_outbound,
    split_message,
)
from moa_gateway.channels.adapters.dingtalk import DingTalkAdapter, dingtalk_sign
from moa_gateway.channels.adapters.discord import DiscordAdapter
from moa_gateway.channels.adapters.feishu import FeishuAdapter
from moa_gateway.channels.adapters.telegram import TelegramAdapter
from moa_gateway.channels.adapters.wecom import WeComAdapter

ALL_PLATFORMS = {"telegram", "feishu", "dingtalk", "wecom", "discord"}

CLEAN_ENV_KEYS = [
    "MOA_TELEGRAM_TOKEN", "MOA_TELEGRAM_BASE_URL", "MOA_TELEGRAM_PARSE_MODE",
    "MOA_TELEGRAM_SECRET_TOKEN",
    "MOA_FEISHU_APP_ID", "MOA_FEISHU_APP_SECRET", "MOA_FEISHU_DOMAIN",
    "MOA_FEISHU_VERIFICATION_TOKEN", "MOA_FEISHU_ENCRYPT_KEY",
    "MOA_DINGTALK_CLIENT_ID", "MOA_DINGTALK_CLIENT_SECRET", "MOA_DINGTALK_WEBHOOK_URL",
    "MOA_WECOM_CORP_ID", "MOA_WECOM_CORP_SECRET", "MOA_WECOM_AGENT_ID",
    "MOA_WECOM_TOKEN", "MOA_WECOM_ENCODING_AES_KEY",
    "MOA_DISCORD_BOT_TOKEN", "MOA_DISCORD_WEBHOOK_URL", "MOA_DISCORD_PUBLIC_KEY",
    "MOA_CHANNELS_SYNC_WEBHOOK",
]


# ---------- fixtures ----------


@pytest.fixture
def clean_env(monkeypatch):
    """Guarantee no platform credentials leak from the host environment."""
    for key in CLEAN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def storage(tmp_path, make_settings):
    """Real isolated Storage registered as the singleton (session bindings)."""
    from moa_gateway.storage import Storage

    settings = make_settings()
    with patch("moa_gateway.storage.get_settings", return_value=settings):
        Storage._instance = None
        s = Storage(db_path=tmp_path / "channels.db")
        Storage._instance = s
        yield s
        Storage._instance = None


@pytest.fixture
def mock_pool(monkeypatch):
    """Settings + ModelPool with one key-less (=> gateway MockProvider) endpoint."""
    import moa_gateway.config as cfg
    import moa_gateway.model_pool as mp
    from moa_gateway.config import ModelEndpointConfig, Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "ChannelsP@ss!2024",
            "jwt_secret": "channels-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [],
        },
        models=[
            ModelEndpointConfig(
                id="mock-standard",
                provider="deepseek",
                model="deepseek-chat",
                tier="standard",
                enabled=True,
            )
        ],
    )
    monkeypatch.setattr(cfg, "_settings", settings)
    monkeypatch.setattr(mp, "_pool", None)
    pool = mp.get_model_pool()
    yield pool
    monkeypatch.setattr(mp, "_pool", None)


class RecordingAdapter(BaseChannelAdapter):
    """Capture-only adapter for bridge writeback tests (transport boundary)."""

    platform_id = "testrec"

    def __init__(self):
        super().__init__({})
        self.sent: list[tuple[str, str, str]] = []

    def validate_config(self, config):
        return []

    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        self.sent.append((chat_id, text, reply_to))
        return SendResult(ok=True, message_id=str(len(self.sent)))


# ---------- base: chunking ----------


def test_split_message_boundary():
    chunks = split_message("aaaa\n\nbbbb", 8)
    assert chunks == ["aaaa\n\n", "bbbb"]
    assert all(len(c) <= 8 for c in chunks)


def test_split_message_hard_cut():
    assert split_message("abcdefghij", 4) == ["abcd", "efgh", "ij"]
    assert split_message("short", 100) == ["short"]


# ---------- registry + state machine ----------


def test_registry_build_all_empty_env_all_unconfigured():
    adapters = registry.build_all(env={}, env_prefix="MOA_")
    assert set(adapters) == ALL_PLATFORMS
    for ad in adapters.values():
        st = ad.status()
        assert st["state"] == "unconfigured"
        assert st["configured"] is False
        assert st["running"] is False
        assert st["missing_env"], f"{ad.platform_id} must report missing credentials"


def test_from_env_telegram_configured():
    ad = TelegramAdapter.from_env({"MOA_TELEGRAM_TOKEN": "123:abc"}, "MOA_")
    st = ad.status()
    assert st["state"] == "configured"
    assert st["configured"] is True
    assert st["missing_env"] == []


def test_status_fields_missing_env():
    ad = TelegramAdapter({})
    st = ad.status()
    assert st["platform"] == "telegram"
    assert st["state"] == "unconfigured"
    assert "MOA_TELEGRAM_TOKEN" in st["required_env"]
    assert st["missing_env"] == ["MOA_TELEGRAM_TOKEN is required"]


async def test_start_unconfigured_raises():
    ad = TelegramAdapter({})
    with pytest.raises(RuntimeError):
        await ad.start(lambda ev: None)


# ---------- Telegram ----------


def _telegram_adapter(handler) -> tuple[TelegramAdapter, httpx.AsyncClient]:
    # Controlled test double: transport layer only (httpx.MockTransport).
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramAdapter({"telegram_token": "TKN"}, http_client=client)
    adapter.validate_and_set_state()
    return adapter, client


async def test_telegram_send_text_success():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    adapter, client = _telegram_adapter(handler)
    try:
        result = await adapter.send_text("77", "hello", reply_to="5")
    finally:
        await client.aclose()
    assert result.ok and result.message_id == "9"
    assert captured["url"] == "https://api.telegram.org/botTKN/sendMessage"
    assert captured["body"]["chat_id"] == "77"
    assert captured["body"]["parse_mode"] == "Markdown"
    assert captured["body"]["reply_to_message_id"] == "5"


async def test_telegram_send_markdown_fallback():
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        attempts.append(dict(body))
        if "parse_mode" in body:
            return httpx.Response(
                400,
                json={"ok": False, "error_code": 400,
                      "description": "Bad Request: can't parse entities"},
            )
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 11}})

    adapter, client = _telegram_adapter(handler)
    try:
        result = await adapter.send_text("77", "**broken markdown")
    finally:
        await client.aclose()
    assert result.ok and result.message_id == "11"
    assert len(attempts) == 2
    assert "parse_mode" not in attempts[1]  # retried as plain text


async def test_telegram_send_chunked_respects_cap(monkeypatch):
    from moa_gateway.config import get_settings

    payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(payloads)}})

    adapter, client = _telegram_adapter(handler)
    monkeypatch.setattr(get_settings().channels, "max_message_chars", 40)
    try:
        result = await adapter.send_chunked("77", "para one\n\npara two\n\npara three\n\npara four")
    finally:
        await client.aclose()
    assert result.ok
    assert result.chunks == len(payloads) >= 2
    assert all(len(p) <= 40 for p in payloads)
    assert "".join(payloads) == "para one\n\npara two\n\npara three\n\npara four"


async def test_telegram_poll_loop_emits_event():
    update = {
        "update_id": 5,
        "message": {
            "message_id": 42,
            "date": 1700000000,
            "text": "hello from telegram",
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 7},
        },
    }
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        assert request.url.path == "/botTKN/getUpdates"
        result = [update] if calls["n"] == 1 else []
        return httpx.Response(200, json={"ok": True, "result": result})

    adapter, client = _telegram_adapter(handler)
    received: list[InboundEvent] = []

    async def on_event(ev: InboundEvent):
        received.append(ev)
        adapter._running = False  # stop the loop after the first batch

    try:
        await adapter.start(on_event)
        await asyncio.wait_for(adapter._task, timeout=5)
    finally:
        await client.aclose()
    assert len(received) == 1
    ev = received[0]
    assert ev.channel == "telegram" and ev.chat_id == "55" and ev.user == "7"
    assert ev.text == "hello from telegram" and ev.message_id == "42"
    assert ev.chat_type == "direct"


async def test_telegram_group_mention_gating():
    adapter, client = _telegram_adapter(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))

    def msg(extra: dict) -> dict:
        base = {"message_id": 1, "text": "hi", "chat": {"id": 3, "type": "supergroup"}, "from": {"id": 4}}
        base.update(extra)
        return {"update_id": 1, "message": base}

    try:
        await adapter._handle_update(msg({}))  # no mention
        await adapter._handle_update(msg({"reply_to_message": {"message_id": 0}}))
        await adapter._handle_update(msg({"entities": [{"type": "mention"}]}))
    finally:
        await client.aclose()
    assert [e.mentioned for e in received] == [False, True, True]
    assert all(e.chat_type == "group" for e in received)


async def test_telegram_webhook_secret_token_enforced():
    adapter, client = _telegram_adapter(lambda r: httpx.Response(200, json={"ok": True, "result": []}))
    adapter.config["telegram_secret_token"] = "SEKRIT"
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))
    update = {
        "update_id": 2,
        "message": {"message_id": 3, "text": "x", "chat": {"id": 1, "type": "private"}, "from": {"id": 2}},
    }
    try:
        bad = await adapter.handle_webhook(update, {"x-telegram-bot-api-secret-token": "wrong"})
        good = await adapter.handle_webhook(update, {"x-telegram-bot-api-secret-token": "SEKRIT"})
    finally:
        await client.aclose()
    assert bad["ok"] is False and "invalid secret token" in bad["error"]
    assert good["ok"] is True
    assert len(received) == 1  # only the verified update was processed


# ---------- Feishu ----------


def _feishu_adapter(handler, **extra) -> tuple[FeishuAdapter, httpx.AsyncClient]:
    # Controlled test double: transport layer only (httpx.MockTransport).
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = {"feishu_app_id": "cli_a", "feishu_app_secret": "s3cret"}
    config.update(extra)
    adapter = FeishuAdapter(config, http_client=client)
    adapter.validate_and_set_state()
    return adapter, client


async def test_feishu_send_caches_tenant_token():
    state = {"token_calls": 0, "sends": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            state["token_calls"] += 1
            return httpx.Response(200, json={
                "code": 0, "tenant_access_token": "t-1", "expire": 7200})
        state["sends"] += 1
        assert request.headers["authorization"] == "Bearer t-1"
        assert "receive_id_type=chat_id" in str(request.url)
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})

    adapter, client = _feishu_adapter(handler)
    try:
        r1 = await adapter.send_text("oc_1", "first")
        r2 = await adapter.send_text("oc_1", "second")
    finally:
        await client.aclose()
    assert r1.ok and r2.ok and r1.message_id == "om_1"
    assert state["token_calls"] == 1  # cached, not re-fetched
    assert state["sends"] == 2


async def test_feishu_send_token_retry_on_expiry():
    state = {"token_calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tenant_access_token/internal"):
            state["token_calls"] += 1
            return httpx.Response(200, json={
                "code": 0, "tenant_access_token": f"t-{state['token_calls']}", "expire": 7200})
        if request.headers["authorization"] == "Bearer t-1":
            return httpx.Response(200, json={"code": 99991663, "msg": "token invalid"})
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_2"}})

    adapter, client = _feishu_adapter(handler)
    try:
        result = await adapter.send_text("oc_1", "retry me")
    finally:
        await client.aclose()
    assert result.ok and result.message_id == "om_2"
    assert state["token_calls"] == 2  # refreshed once after 99991663


async def test_feishu_url_verification_challenge():
    adapter, client = _feishu_adapter(
        lambda r: httpx.Response(200, json={"ok": True}),
        feishu_verification_token="VT",
    )
    try:
        good = await adapter.handle_webhook(
            {"type": "url_verification", "challenge": "abc123", "token": "VT"}, {})
        bad = await adapter.handle_webhook(
            {"type": "url_verification", "challenge": "abc123", "token": "nope"}, {})
    finally:
        await client.aclose()
    assert good == {"challenge": "abc123"}
    assert "error" in bad


async def test_feishu_event_message_emits():
    adapter, client = _feishu_adapter(
        lambda r: httpx.Response(200, json={"ok": True}),
        feishu_verification_token="VT",
    )
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))
    payload = {
        "schema": "2.0",
        "header": {
            "token": "VT",
            "event_type": "im.message.receive_v1",
            "create_time": "1700000000000",
        },
        "event": {
            "sender": {"sender_id": {"open_id": "ou_1"}},
            "message": {
                "chat_id": "oc_9", "message_id": "m5", "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "hello feishu"}),
            },
        },
    }
    try:
        resp = await adapter.handle_webhook(payload, {})
    finally:
        await client.aclose()
    assert resp == {"ok": True}
    assert len(received) == 1
    ev = received[0]
    assert ev.channel == "feishu" and ev.chat_id == "oc_9" and ev.user == "ou_1"
    assert ev.text == "hello feishu" and ev.chat_type == "group"
    assert ev.message_id == "m5"


async def test_feishu_raw_signature_verification():
    adapter, client = _feishu_adapter(
        lambda r: httpx.Response(200, json={"ok": True}),
        feishu_encrypt_key="EK",
        feishu_verification_token="VT",
    )
    body = json.dumps({"type": "url_verification", "challenge": "ch-1", "token": "VT"})
    ts, nonce = "1700000001", "n-42"
    sig = hashlib.sha256(f"{ts}{nonce}EK{body}".encode("utf-8")).hexdigest()
    headers = {
        "x-lark-request-timestamp": ts,
        "x-lark-request-nonce": nonce,
        "x-lark-signature": sig,
    }
    try:
        ok_status, ok_body = await adapter.handle_raw(body.encode("utf-8"), {}, headers)
        bad_headers = dict(headers, **{"x-lark-signature": "0" * 64})
        bad_status, bad_body = await adapter.handle_raw(body.encode("utf-8"), {}, bad_headers)
    finally:
        await client.aclose()
    assert ok_status == 200 and ok_body == {"challenge": "ch-1"}
    assert bad_status == 403 and "signature" in bad_body["error"].lower()


# ---------- DingTalk ----------


def _dingtalk_adapter(handler, **extra) -> tuple[DingTalkAdapter, httpx.AsyncClient]:
    # Controlled test double: transport layer only (httpx.MockTransport).
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    config = {"dingtalk_client_id": "cid", "dingtalk_client_secret": "SEC-1"}
    config.update(extra)
    adapter = DingTalkAdapter(config, http_client=client)
    adapter.validate_and_set_state()
    return adapter, client


def test_dingtalk_sign_algorithm_matches_official():
    ts, secret = 1700000000000, "SEC-test"
    string_to_sign = f"{ts}\n{secret}"
    digest = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    expected = urllib.parse.quote_plus(base64.b64encode(digest).decode("ascii"))
    assert dingtalk_sign(ts, secret) == expected


async def test_dingtalk_webhook_rejects_bad_signature():
    adapter, client = _dingtalk_adapter(lambda r: httpx.Response(200, json={"errcode": 0}))
    try:
        resp = await adapter.handle_webhook(
            {"msgtype": "text", "text": {"content": "x"}},
            {"timestamp": str(int(time.time() * 1000)), "sign": "forged"},
        )
    finally:
        await client.aclose()
    assert "invalid signature" in resp["error"]


def _dingtalk_text_payload(**over) -> dict:
    payload = {
        "msgtype": "text",
        "text": {"content": "hi ding"},
        "conversationId": "cid1",
        "senderId": "sid1",
        "conversationType": "1",
        "msgId": "dm1",
        "createAt": 1700000000000,
        "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=x",
        "sessionWebhookExpiredTime": time.time() * 1000 + 3600_000,
        "atUsers": [],
    }
    payload.update(over)
    return payload


async def test_dingtalk_webhook_text_event_caches_session_webhook():
    adapter, client = _dingtalk_adapter(lambda r: httpx.Response(200, json={"errcode": 0}))
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))
    ts = int(time.time() * 1000)
    headers = {"timestamp": str(ts), "sign": dingtalk_sign(ts, "SEC-1")}
    try:
        resp = await adapter.handle_webhook(_dingtalk_text_payload(), headers)
    finally:
        await client.aclose()
    assert resp == {"ok": True}
    assert len(received) == 1
    ev = received[0]
    assert ev.channel == "dingtalk" and ev.chat_id == "cid1" and ev.text == "hi ding"
    assert ev.chat_type == "direct" and ev.mentioned is True
    assert adapter._session_webhooks["cid1"][0].endswith("session=x")


async def test_dingtalk_reply_via_cached_session_webhook():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json={"errcode": 0, "msgid": "dm2"})

    adapter, client = _dingtalk_adapter(handler)
    ts = int(time.time() * 1000)
    headers = {"timestamp": str(ts), "sign": dingtalk_sign(ts, "SEC-1")}
    try:
        await adapter.handle_webhook(_dingtalk_text_payload(), headers)
        result = await adapter.send_text("cid1", "reply text")
    finally:
        await client.aclose()
    assert result.ok and result.message_id == "dm2"
    assert captured == ["https://oapi.dingtalk.com/robot/sendBySession?session=x"]


async def test_dingtalk_expired_session_webhook_falls_back_to_signed_url():
    captured = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(str(request.url))
        return httpx.Response(200, json={"errcode": 0, "msgid": "dm3"})

    adapter, client = _dingtalk_adapter(
        handler, dingtalk_webhook_url="https://oapi.dingtalk.com/robot/send?access_token=abc")
    ts = int(time.time() * 1000)
    headers = {"timestamp": str(ts), "sign": dingtalk_sign(ts, "SEC-1")}
    try:
        await adapter.handle_webhook(
            _dingtalk_text_payload(sessionWebhookExpiredTime=time.time() * 1000 - 1000),
            headers,
        )
        result = await adapter.send_text("cid1", "fallback push")
    finally:
        await client.aclose()
    assert result.ok
    assert len(captured) == 1
    assert "access_token=abc" in captured[0]
    assert "timestamp=" in captured[0] and "sign=" in captured[0]


async def test_dingtalk_group_mention_gating():
    adapter, client = _dingtalk_adapter(lambda r: httpx.Response(200, json={"errcode": 0}))
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))
    ts = int(time.time() * 1000)
    headers = {"timestamp": str(ts), "sign": dingtalk_sign(ts, "SEC-1")}
    try:
        await adapter.handle_webhook(
            _dingtalk_text_payload(conversationType="2", atUsers=[]), headers)
        await adapter.handle_webhook(
            _dingtalk_text_payload(conversationType="2", atUsers=[{"dingtalkId": "b"}]), headers)
    finally:
        await client.aclose()
    assert [e.chat_type for e in received] == ["group", "group"]
    assert [e.mentioned for e in received] == [False, True]


# ---------- WeCom ----------


def _wecom_aes_key() -> str:
    """43-char EncodingAESKey (official WeCom shape: 43 chars + '=' -> 32 bytes)."""
    return base64.b64encode(os.urandom(32)).decode("ascii")[:-1]


def _wecom_encrypt(aes_b64: str, corp_id: str, plaintext: str) -> str:
    """Test-side encoder matching the official WeCom callback layout:
    random16 + msg_len4(big-endian) + msg + receiveid, PKCS#7 (block 32)."""
    aes_key = base64.b64decode(aes_b64 + "=")
    msg = plaintext.encode("utf-8")
    body = os.urandom(16) + len(msg).to_bytes(4, "big") + msg + corp_id.encode("utf-8")
    pad = 32 - (len(body) % 32)
    body += bytes([pad]) * pad
    enc = Cipher(algorithms.AES(aes_key), modes.CBC(aes_key[:16])).encryptor()
    return base64.b64encode(enc.update(body) + enc.finalize()).decode("ascii")


def _wecom_adapter(**extra) -> WeComAdapter:
    config = {
        "wecom_corp_id": "corp1",
        "wecom_corp_secret": "cs",
        "wecom_agent_id": "1000002",
        "wecom_token": "TK",
        "wecom_encoding_aes_key": AES_KEY,
    }
    config.update(extra)
    adapter = WeComAdapter(config)
    adapter.validate_and_set_state()
    return adapter


AES_KEY = _wecom_aes_key()


def test_wecom_compute_signature_official():
    sig = WeComAdapter.compute_signature("TK", "1700", "nn", "ENC")
    expected = hashlib.sha1("".join(sorted(["TK", "1700", "nn", "ENC"])).encode()).hexdigest()
    assert sig == expected


def test_wecom_decrypt_roundtrip():
    adapter = _wecom_adapter()
    xml = "<xml><Content>hello</Content></xml>"
    encrypt = _wecom_encrypt(AES_KEY, "corp1", xml)
    ts, nonce = "1700000002", "n1"
    sig = WeComAdapter.compute_signature("TK", ts, nonce, encrypt)
    assert adapter.verify_and_decrypt(sig, ts, nonce, encrypt) == xml
    with pytest.raises(Exception):
        adapter.verify_and_decrypt("0" * 40, ts, nonce, encrypt)


async def test_wecom_handle_raw_text_message_event():
    adapter = _wecom_adapter()
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))
    inner = (
        "<xml><ToUserName><![CDATA[corp1]]></ToUserName>"
        "<FromUserName><![CDATA[user1]]></FromUserName>"
        "<CreateTime>1700000000</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        "<Content><![CDATA[hello wecom]]></Content>"
        "<MsgId>123</MsgId><AgentID>1000002</AgentID></xml>"
    )
    encrypt = _wecom_encrypt(AES_KEY, "corp1", inner)
    ts, nonce = "1700000003", "n2"
    sig = WeComAdapter.compute_signature("TK", ts, nonce, encrypt)
    outer = f"<xml><ToUserName>corp1</ToUserName><Encrypt>{encrypt}</Encrypt></xml>"
    status, body = await adapter.handle_raw(
        outer.encode("utf-8"),
        {"msg_signature": sig, "timestamp": ts, "nonce": nonce},
        {},
    )
    assert status == 200 and body == {"ok": True}
    assert len(received) == 1
    ev = received[0]
    assert ev.channel == "wecom" and ev.user == "user1" and ev.chat_id == "user1"
    assert ev.text == "hello wecom" and ev.message_id == "123"


async def test_wecom_handle_raw_bad_signature_403():
    adapter = _wecom_adapter()
    outer = "<xml><Encrypt>AAAA</Encrypt></xml>"
    status, body = await adapter.handle_raw(
        outer.encode("utf-8"),
        {"msg_signature": "0" * 40, "timestamp": "1", "nonce": "n"},
        {},
    )
    assert status == 403
    assert "verification failed" in body["error"] or "mismatch" in body["error"]


async def test_wecom_echostr_verification():
    adapter = _wecom_adapter()
    echo_plain = "echo-ok-123"
    echo_enc = _wecom_encrypt(AES_KEY, "corp1", echo_plain)
    ts, nonce = "1700000004", "n3"
    sig = WeComAdapter.compute_signature("TK", ts, nonce, echo_enc)
    status, body = await adapter.handle_raw(
        b"<xml/>",
        {"msg_signature": sig, "timestamp": ts, "nonce": nonce, "echostr": echo_enc},
        {},
    )
    assert status == 200 and body == {"echostr": echo_plain}


async def test_wecom_send_text_real_api():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/cgi-bin/gettoken":
            assert request.url.params["corpid"] == "corp1"
            return httpx.Response(200, json={"errcode": 0, "access_token": "AT", "expires_in": 7200})
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "msgid": "wm1"})

    # Controlled test double: transport layer only (httpx.MockTransport).
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = WeComAdapter(
        {"wecom_corp_id": "corp1", "wecom_corp_secret": "cs", "wecom_agent_id": "1000002"},
        http_client=client,
    )
    adapter.validate_and_set_state()
    try:
        result = await adapter.send_text("user1", "outbound message")
    finally:
        await client.aclose()
    assert result.ok and result.message_id == "wm1"
    assert "access_token=AT" in captured["url"]
    assert captured["body"]["touser"] == "user1"
    assert captured["body"]["agentid"] == 1000002
    assert captured["body"]["text"]["content"] == "outbound message"


# ---------- Discord ----------


def _discord_keypair():
    priv = Ed25519PrivateKey.generate()
    pub_hex = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    return priv, pub_hex


async def test_discord_ping_handshake_ed25519():
    priv, pub_hex = _discord_keypair()
    adapter = DiscordAdapter({"discord_public_key": pub_hex, "discord_bot_token": "BT"})
    adapter.validate_and_set_state()
    ts = "1700000005"
    body = b'{"type":1}'
    sig = priv.sign(ts.encode("utf-8") + body).hex()
    status, resp = await adapter.handle_raw(
        body, {}, {"x-signature-timestamp": ts, "x-signature-ed25519": sig})
    assert status == 200 and resp == {"type": 1}


async def test_discord_bad_signature_401():
    _, pub_hex = _discord_keypair()
    adapter = DiscordAdapter({"discord_public_key": pub_hex, "discord_bot_token": "BT"})
    adapter.validate_and_set_state()
    status, resp = await adapter.handle_raw(
        b'{"type":1}', {},
        {"x-signature-timestamp": "1", "x-signature-ed25519": "ab" * 64})
    assert status == 401
    assert "signature" in resp["error"]


async def test_discord_command_interaction_event():
    priv, pub_hex = _discord_keypair()
    adapter = DiscordAdapter({"discord_public_key": pub_hex, "discord_bot_token": "BT"})
    adapter.validate_and_set_state()
    received: list[InboundEvent] = []
    adapter.set_event_handler(lambda ev: received.append(ev) or asyncio.sleep(0))
    payload = {
        "type": 2,
        "id": "int1",
        "channel_id": "111",
        "guild_id": "g9",
        "member": {"id": "u1"},
        "data": {"name": "ask", "options": [{"name": "q", "value": "hello world"}]},
    }
    body = json.dumps(payload).encode("utf-8")
    ts = "1700000006"
    sig = priv.sign(ts.encode("utf-8") + body).hex()
    status, resp = await adapter.handle_raw(
        body, {}, {"x-signature-timestamp": ts, "x-signature-ed25519": sig})
    assert status == 200 and resp["type"] == 4
    assert len(received) == 1
    ev = received[0]
    assert ev.chat_id == "111" and ev.user == "u1" and ev.text == "hello world"
    assert ev.chat_type == "group"  # guild_id present


async def test_discord_send_rest():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": "m9"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = DiscordAdapter({"discord_bot_token": "BT"}, http_client=client)
    adapter.validate_and_set_state()
    try:
        result = await adapter.send_text("111", "hi discord", reply_to="m0")
    finally:
        await client.aclose()
    assert result.ok and result.message_id == "m9"
    assert captured["url"] == "https://discord.com/api/v10/channels/111/messages"
    assert captured["auth"] == "Bot BT"
    assert captured["body"]["message_reference"] == {"message_id": "m0"}


async def test_discord_send_webhook_204():
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["content"] == "via webhook"
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = DiscordAdapter(
        {"discord_webhook_url": "https://discord.com/api/webhooks/1/abc"},
        http_client=client,
    )
    adapter.validate_and_set_state()
    try:
        result = await adapter.send_text("", "via webhook")
    finally:
        await client.aclose()
    assert result.ok  # 204 = executed without ?wait=1


# ---------- session routing ----------


def _event(chat="C1", user="U1", text="hi", **kw) -> InboundEvent:
    return InboundEvent(channel="telegram", chat_id=chat, user=user, text=text, **kw)


def test_channel_key_modes():
    ev = _event()
    assert channel_key("telegram", ev, ":chat") == "telegram:chat:C1"
    assert channel_key("telegram", ev, ":user") == "telegram:user:U1"
    assert channel_key("telegram", ev, ":chat_user") == "telegram:chat_user:C1:U1"
    with pytest.raises(ValueError):
        SessionRouter(binding_mode=":bogus")


def test_session_router_persists_across_instances(storage):
    ev = _event()
    s1 = SessionRouter().resolve_session(ev)
    s2 = SessionRouter().resolve_session(ev)  # fresh instance -> same binding
    assert s1 == s2 and len(s1) == 32
    bindings = SessionRouter().list_bindings(platform="telegram")
    assert len(bindings) == 1
    assert bindings[0]["channel_key"] == "telegram:chat:C1"
    assert bindings[0]["session_id"] == s1


def test_session_router_ttl_expiry(storage):
    router = SessionRouter()
    ev = _event()
    first = router.resolve_session(ev)
    with storage.conn() as c:
        c.execute(
            "UPDATE channel_session_bindings SET updated_at = ?",
            (time.time() - 4000,),  # default session_ttl_s=3600 -> expired
        )
    second = SessionRouter().resolve_session(ev)
    assert second != first  # stale binding -> fresh gateway session


def test_session_router_dedup_window(storage):
    router = SessionRouter()
    ev = _event(text="same message")
    assert router.is_duplicate("k1", ev) is False
    assert router.is_duplicate("k1", _event(text="same message")) is True
    assert router.is_duplicate("k1", _event(text="different")) is False
    assert router.is_duplicate("k2", _event(text="same message")) is False


def test_session_router_bind_unbind_list(storage):
    router = SessionRouter()
    router.bind("telegram:chat:X9", "sess-42")
    rows = router.list_bindings()
    assert any(r["channel_key"] == "telegram:chat:X9" and r["session_id"] == "sess-42" for r in rows)
    assert router.unbind("telegram:chat:X9") is True
    assert router.unbind("telegram:chat:X9") is False
    assert all(r["channel_key"] != "telegram:chat:X9" for r in router.list_bindings())


# ---------- bridge (writeback) ----------


def test_sanitize_outbound_strips_file_links():
    text = "result ready\nfile:///tmp/secret/out.png\n\n\nsee above"
    cleaned = sanitize_outbound(text)
    assert "file://" not in cleaned
    assert "result ready" in cleaned and "see above" in cleaned
    assert "\n\n\n" not in cleaned


async def test_bridge_process_event_real_pipeline_writeback(storage, mock_pool):
    adapter = RecordingAdapter()
    bridge = ChannelGatewayBridge()
    ev = _event(text="say hi", message_id="m1")
    result = await bridge.process_event(adapter, ev)
    assert result["sent"] is True and result["chunks"] == 1
    assert result["session_id"] and len(result["session_id"]) == 32
    assert ev.session_id == result["session_id"]
    assert len(adapter.sent) == 1
    chat_id, reply, reply_to = adapter.sent[0]
    assert chat_id == "C1" and reply_to == "m1"
    assert reply.startswith("[Mock:")  # gateway's real key-less endpoint semantics


async def test_bridge_group_unmentioned_ignored(storage, mock_pool):
    adapter = RecordingAdapter()
    bridge = ChannelGatewayBridge()
    ev = _event(chat_type="group", mentioned=False)
    result = await bridge.process_event(adapter, ev)
    assert result == {"ignored": "group message not addressed to bot"}
    assert adapter.sent == []


async def test_bridge_unsupported_and_dedup(storage, mock_pool):
    adapter = RecordingAdapter()
    bridge = ChannelGatewayBridge()
    unsup = _event(unsupported=True)
    assert await bridge.process_event(adapter, unsup) == {"ignored": "unsupported message type"}
    ev = _event(text="ping", message_id="m2")
    first = await bridge.process_event(adapter, ev)
    assert first["sent"] is True
    second = await bridge.process_event(adapter, _event(text="ping", message_id="m2"))
    assert second == {"ignored": "duplicate within dedup window"}
    assert len(adapter.sent) == 1


# ---------- HTTP routes ----------


@pytest.fixture
def app():
    from fastapi import FastAPI

    from moa_gateway.auth import require_admin, require_api_key
    from moa_gateway.routes.channels import router

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[require_api_key] = lambda: {"key": "test-key"}
    test_app.dependency_overrides[require_admin] = lambda: {
        "username": "admin",
        "role": "admin",
    }
    return test_app


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_route_list_all_unconfigured(client, clean_env):
    resp = await client.get("/v1/channels")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 5
    assert data["configured"] == 0
    assert data["enabled"] == []
    assert {c["platform"] for c in data["channels"]} == ALL_PLATFORMS
    assert all(c["state"] == "unconfigured" for c in data["channels"])


async def test_route_list_with_telegram_creds(client, clean_env, monkeypatch):
    monkeypatch.setenv("MOA_TELEGRAM_TOKEN", "123:abc")
    resp = await client.get("/v1/channels")
    data = resp.json()
    assert data["configured"] == 1
    assert data["enabled"] == ["telegram"]
    states = {c["platform"]: c["state"] for c in data["channels"]}
    assert states["telegram"] == "configured"
    assert states["feishu"] == "unconfigured"


async def test_route_send_unknown_404(client, clean_env):
    resp = await client.post("/v1/channels/slack/send", json={"chat_id": "c", "text": "t"})
    assert resp.status_code == 404


async def test_route_send_unconfigured_409(client, clean_env):
    resp = await client.post(
        "/v1/channels/telegram/send", json={"chat_id": "c", "text": "t"})
    assert resp.status_code == 409
    assert "MOA_TELEGRAM_TOKEN is required" in resp.json()["missing_env"]


async def test_route_webhook_unconfigured_409(client, clean_env):
    resp = await client.post("/v1/channels/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 409


async def test_route_webhook_dingtalk_bad_signature(client, clean_env, monkeypatch):
    monkeypatch.setenv("MOA_DINGTALK_CLIENT_ID", "cid")
    monkeypatch.setenv("MOA_DINGTALK_CLIENT_SECRET", "SEC-1")
    resp = await client.post(
        "/v1/channels/dingtalk/webhook",
        json={"msgtype": "text", "text": {"content": "x"}},
        headers={"timestamp": str(int(time.time() * 1000)), "sign": "forged"},
    )
    assert resp.status_code == 200  # platform ack shape, event rejected
    assert "invalid signature" in resp.json()["error"]


async def test_route_webhook_telegram_sync_writeback(
    client, clean_env, storage, mock_pool, monkeypatch
):
    """Full inbound cycle over HTTP: webhook -> verify -> session routing ->
    real gateway pipeline (Mock endpoint semantics) -> outbound sendMessage."""
    sent_payloads = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent_payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 77}})

    # Controlled test double: transport layer only (httpx.MockTransport) —
    # every line of adapter/bridge/route logic runs for real.
    shared = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(BaseChannelAdapter, "http", lambda self: shared)
    monkeypatch.setenv("MOA_TELEGRAM_TOKEN", "123:abc")
    monkeypatch.setenv("MOA_TELEGRAM_SECRET_TOKEN", "st-1")
    monkeypatch.setenv("MOA_CHANNELS_SYNC_WEBHOOK", "1")

    update = {
        "update_id": 1,
        "message": {
            "message_id": 42,
            "date": 1700000000,
            "text": "hello bot",
            "chat": {"id": 55, "type": "private"},
            "from": {"id": 7},
        },
    }
    resp = await client.post(
        "/v1/channels/telegram/webhook",
        json=update,
        headers={"X-Telegram-Bot-Api-Secret-Token": "st-1"},
    )
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert len(sent_payloads) == 1
    assert sent_payloads[0]["chat_id"] == "55"
    assert sent_payloads[0]["reply_to_message_id"] == "42"
    assert sent_payloads[0]["text"].startswith("[Mock:")

    # session binding was persisted by the writeback
    bindings = await client.get("/v1/channels/bindings")
    assert bindings.status_code == 200
    rows = bindings.json()["bindings"]
    assert len(rows) == 1 and rows[0]["channel_key"] == "telegram:chat:55"


async def test_route_bindings_endpoint_empty(client, storage):
    resp = await client.get("/v1/channels/bindings")
    assert resp.status_code == 200
    assert resp.json() == {"bindings": [], "count": 0}


async def test_route_capability_disabled_503(client, clean_env, monkeypatch):
    import moa_gateway.capability_toggles as toggles

    state = dict(toggles.DEFAULT_CAPABILITIES)
    state["channels"] = False
    monkeypatch.setattr(toggles, "_cache", state)
    resp = await client.get("/v1/channels")
    assert resp.status_code == 503


# ---------- blind-review M-2 regression guards: webhook fail-closed ----------

async def test_telegram_webhook_rejects_when_secret_unconfigured():
    adapter = TelegramAdapter({"telegram_token": "123:abc"})
    result = await adapter.handle_webhook(
        {"update_id": 1, "message": {"text": "hi", "chat": {"id": 1}}},
        {"x-telegram-bot-api-secret-token": "whatever"},
    )
    assert result["ok"] is False
    assert "not configured" in result["error"]


async def test_feishu_webhook_rejects_when_token_unconfigured():
    adapter, client = _feishu_adapter(
        lambda r: httpx.Response(200, json={"ok": True})
    )
    try:
        status, body = await adapter.handle_raw(
            json.dumps({"header": {"token": "x"}, "event": {}}).encode(), {}, {}
        )
    finally:
        await client.aclose()
    assert "error" in body
    assert "token" in body["error"].lower()
    assert "event" not in body or body.get("ok") is not True


async def test_discord_webhook_rejects_when_public_key_unconfigured():
    adapter = DiscordAdapter({"discord_bot_token": "t"})
    status, body = await adapter.handle_raw(b'{"type": 1}', {}, {})
    assert status == 401
    assert "signature" in body["error"].lower()
