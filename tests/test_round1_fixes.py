"""Tests for Round 1 audit fixes: SSRF guard, circuit breaker half-open reset,
MCP JSON-RPC invalid request, and cache single-flight (stampede protection)."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest

# 模块级 os.environ.setdefault 会在收集期(import)泄漏到其它测试文件
# (曾导致 subagent 回环 401)——改为模块作用域 fixture: 仅本文件测试期间生效。
_ENV_NEEDED = {
    "MOA_JWT_SECRET": "test-secret-key-minimum-32-characters-long!",
    "MOA_ADMIN_PASSWORD": "TestPass#2024",
    "MOA_GATEWAY_KEY": "round1-key-001"
}


@pytest.fixture(autouse=True, scope="module")
def _isolate_module_env():
    saved = {k: os.environ.get(k) for k in _ENV_NEEDED}
    for k, v in _ENV_NEEDED.items():
        os.environ.setdefault(k, v)
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


from httpx import ASGITransport, AsyncClient  # noqa: E402

AUTH = {"Authorization": "Bearer round1-key-001"}


@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["round1-key-001"],
        },
        models=[
            {
                "id": "gpt-4o",
                "provider": "openai",
                "model": "gpt-4o",
                "api_base": "https://api.openai.com/v1",
                "api_key_env": "OPENAI_API_KEY",
                "tier": "standard",
            }
        ],
    )
    with patch("moa_gateway.config.get_settings", return_value=settings):
        with patch("moa_gateway.config._settings", settings):
            from moa_gateway.server import create_app

            application = create_app()
            yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ====================================================================
# 1. SSRF guard on MCP external server registration
# ====================================================================
class TestSSRFGuard:
    """Verify private/loopback URLs are rejected."""

    @pytest.mark.anyio
    async def test_loopback_url_rejected(self, client, monkeypatch):
        monkeypatch.delenv("MOA_ALLOW_SSRF_INTERNAL", raising=False)
        resp = await client.post(
            "/v1/mcp/servers",
            json={"url": "http://127.0.0.1:8080/internal", "name": "evil"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "blocked" in resp.json()["detail"].lower() or "public" in resp.json()["detail"].lower()

    @pytest.mark.anyio
    async def test_metadata_url_rejected(self, client, monkeypatch):
        """Cloud metadata endpoint must be blocked (169.254.169.254)."""
        monkeypatch.delenv("MOA_ALLOW_SSRF_INTERNAL", raising=False)
        resp = await client.post(
            "/v1/mcp/servers",
            json={"url": "http://169.254.169.254/latest/meta-data/", "name": "meta"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_private_network_url_rejected(self, client, monkeypatch):
        """10.x private network must be blocked."""
        monkeypatch.delenv("MOA_ALLOW_SSRF_INTERNAL", raising=False)
        resp = await client.post(
            "/v1/mcp/servers",
            json={"url": "http://10.0.0.1/api", "name": "internal"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    @pytest.mark.anyio
    async def test_public_url_accepted(self, client, monkeypatch):
        """Public URL should pass the SSRF check (may proceed to register).

        v3.1.1: the hardened validator resolves DNS and fails closed on
        unresolvable hosts, so mock a public resolution for determinism.
        """
        monkeypatch.delenv("MOA_ALLOW_SSRF_INTERNAL", raising=False)
        import socket as _socket

        def _fake_getaddrinfo(host, *a, **k):
            return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

        monkeypatch.setattr(
            "moa_gateway.utils.url_validator.socket.getaddrinfo", _fake_getaddrinfo
        )
        resp = await client.post(
            "/v1/mcp/servers",
            json={"url": "https://api.example.com/mcp", "name": "public"},
            headers=AUTH,
        )
        # Should NOT be 400 SSRF rejection
        assert resp.status_code != 400

    @pytest.mark.anyio
    async def test_dns_rebinding_to_internal_rejected(self, client, monkeypatch):
        """v3.1.1 P1-6: a public-looking name resolving to an internal IP
        (DNS rebinding) must be rejected by the hardened validator."""
        monkeypatch.delenv("MOA_ALLOW_SSRF_INTERNAL", raising=False)
        import socket as _socket

        def _evil_getaddrinfo(host, *a, **k):
            return [(_socket.AF_INET, _socket.SOCK_STREAM, 6, "", ("169.254.169.254", 80))]

        monkeypatch.setattr(
            "moa_gateway.utils.url_validator.socket.getaddrinfo", _evil_getaddrinfo
        )
        resp = await client.post(
            "/v1/mcp/servers",
            json={"url": "http://evil.rebind.example/mcp", "name": "rebind"},
            headers=AUTH,
        )
        assert resp.status_code == 400

    def test_is_safe_url_unit(self, monkeypatch):
        from moa_gateway.routes.mcp import _is_safe_external_url

        monkeypatch.delenv("MOA_ALLOW_SSRF_INTERNAL", raising=False)
        assert not _is_safe_external_url("http://127.0.0.1/x")
        assert not _is_safe_external_url("http://localhost/x")
        assert not _is_safe_external_url("http://169.254.169.254/x")
        assert not _is_safe_external_url("http://10.0.0.1/x")
        assert not _is_safe_external_url("http://192.168.1.1/x")
        assert not _is_safe_external_url("ftp://example.com/x")  # bad scheme
        assert _is_safe_external_url("https://api.openai.com/v1")
        assert _is_safe_external_url("http://93.184.216.34/x")  # public IP


# ====================================================================
# 2. Circuit breaker half-open reset on failure
# ====================================================================
class TestCircuitBreakerHalfOpenReset:
    """Verify a failed half-open probe resets probe counters (no stuck state)."""

    def test_half_open_failure_resets_probe_counters(self):
        from moa_gateway.ha.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        cfg = CircuitBreakerConfig(
            failure_threshold=2, recovery_timeout=0.01, half_open_max_calls=3, success_threshold=2
        )
        cb = CircuitBreaker("test", config=cfg)

        # Trip the breaker: 2 failures → OPEN
        cb.record_failure()
        cb.record_failure()
        assert cb.state.value == "open"

        # Wait for recovery timeout → HALF_OPEN on next access
        import time

        time.sleep(0.05)

        # Consume one half-open probe slot, then fail it
        assert cb.allow_request()  # consumes one slot
        cb.record_failure()  # probe failed → back to OPEN

        # CRITICAL: half_open_calls must be reset so the NEXT recovery window
        # can actually probe. Without the fix, the slot stays saturated.
        assert cb._half_open_calls == 0, "half_open_calls must reset on failure"
        assert cb._success_count == 0, "success_count must reset on failure"

        # After another recovery window, probing should work again
        time.sleep(0.05)
        assert cb.allow_request() is True


# ====================================================================
# 3. MCP JSON-RPC invalid request returns -32600, not 500
# ====================================================================
class TestMCPInvalidRequest:
    """Verify malformed JSON-RPC requests return proper error codes."""

    @pytest.mark.anyio
    async def test_missing_method_returns_invalid_request(self, client):
        """A request without 'method' must return -32600, not crash with 500."""
        resp = await client.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1},  # no method
            headers=AUTH,
        )
        assert resp.status_code == 200  # JSON-RPC errors return 200 with error body
        body = resp.json()
        assert body.get("error", {}).get("code") == -32600

    @pytest.mark.anyio
    async def test_valid_ping_returns_ok(self, client):
        """A valid ping request should succeed."""
        resp = await client.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers=AUTH,
        )
        assert resp.status_code == 200


# ====================================================================
# 4. Cache single-flight (stampede protection)
# ====================================================================
class TestCacheSingleFlight:
    """Verify concurrent identical misses produce only ONE upstream call."""

    @pytest.mark.anyio
    async def test_concurrent_miss_single_upstream_call(self):
        from moa_gateway.cache.manager import CacheManager
        from moa_gateway.config import CacheConfig

        cm = CacheManager(config=CacheConfig(enabled=False))
        # Enable for this test
        cm.enabled = True

        call_count = 0

        async def compute():
            nonlocal call_count
            call_count += 1
            await asyncio.sleep(0.05)  # simulate slow upstream
            return {"content": "computed"}

        messages = [{"role": "user", "content": "hello"}]

        # Fire 10 concurrent identical requests
        results = await asyncio.gather(
            *[cm.get_or_compute(messages, "test-model", compute) for _ in range(10)]
        )

        # Only the FIRST caller should invoke compute (single-flight)
        assert call_count == 1, f"Expected 1 upstream call, got {call_count}"
        # All should get the same result
        for value, hit in results:
            assert value == {"content": "computed"}
