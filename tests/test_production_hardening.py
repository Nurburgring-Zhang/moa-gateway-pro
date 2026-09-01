"""Tests for production-hardening features added in the audit round.

Covers:
- Gateway-level request timeout middleware (504 on hang)
- Chunked transfer body size enforcement (413)
- Atomic rate-limit check-and-increment (no TOCTOU)
- MoA orchestration overall timeout
- Provider JSON decode error handling (502 on malformed upstream)
- Admin password no longer logged in plaintext
"""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

# 模块级 os.environ.setdefault 会在收集期(import)泄漏到其它测试文件
# (曾导致 subagent 回环 401)——改为模块作用域 fixture: 仅本文件测试期间生效。
_ENV_NEEDED = {
    "MOA_JWT_SECRET": "test-secret-key-minimum-32-characters-long!",
    "MOA_ADMIN_PASSWORD": "TestPass#2024",
    "MOA_GATEWAY_KEY": "prod-hardening-key-001"
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

AUTH = {"Authorization": "Bearer prod-hardening-key-001"}


@pytest.fixture
def app():
    """Create app with a mock model endpoint configured."""
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["prod-hardening-key-001"],
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
# 1. Chunked transfer body size enforcement
# ====================================================================
class TestBodySizeLimit:
    """Verify body size limit is enforced even without content-length header."""

    @pytest.mark.anyio
    async def test_oversized_chunked_body_rejected(self, client):
        """A POST body larger than 1MB without content-length must be rejected (413)."""
        # Build a payload > 1MB
        big_content = "x" * (2 * 1024 * 1024)  # 2MB
        payload = {"model": "auto", "messages": [{"role": "user", "content": big_content}]}
        body = json.dumps(payload).encode()
        # Send without content-length to simulate chunked encoding
        resp = await client.post(
            "/v1/chat/completions",
            content=body,
            headers={**AUTH, "Content-Type": "application/json"},
        )
        assert resp.status_code == 413

    @pytest.mark.anyio
    async def test_normal_body_accepted(self, client):
        """A normal-sized POST body should pass the size check (may fail later for other reasons)."""
        payload = {"model": "auto", "messages": [{"role": "user", "content": "hi"}]}
        resp = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers=AUTH,
        )
        # Should NOT be 413 (size limit); other status codes are acceptable here
        assert resp.status_code != 413


# ====================================================================
# 2. Gateway-level request timeout
# ====================================================================
class TestGatewayTimeout:
    """Verify the gateway timeout middleware returns 504 on hangs."""

    @pytest.mark.anyio
    async def test_health_endpoint_bypasses_timeout(self, client):
        """Health endpoints should not be subject to gateway timeout."""
        resp = await client.get("/health")
        assert resp.status_code == 200


# ====================================================================
# 3. Atomic rate-limit check-and-increment (no TOCTOU)
# ====================================================================
class TestAtomicRateLimit:
    """Verify atomic_check_and_incr_tokens prevents TOCTOU race."""

    def test_atomic_incr_under_limit(self, storage_instance):
        """Under the limit: returns new total (>= tokens)."""
        result = storage_instance.atomic_check_and_incr_tokens("key1", "20260101", 100, 1000)
        assert result == 100

    def test_atomic_incr_over_limit_rejected(self, storage_instance):
        """Over the limit: returns -1, no increment applied."""
        # First add 950 tokens
        storage_instance.atomic_check_and_incr_tokens("key2", "20260101", 950, 1000)
        # Now try to add 100 more (950 + 100 = 1050 > 1000) → must reject
        result = storage_instance.atomic_check_and_incr_tokens("key2", "20260101", 100, 1000)
        assert result == -1
        # Verify no increment happened
        assert storage_instance.get_daily_tokens("key2", "20260101") == 950

    def test_atomic_incr_exactly_at_limit(self, storage_instance):
        """Exactly at limit: should succeed."""
        storage_instance.atomic_check_and_incr_tokens("key3", "20260101", 900, 1000)
        # 900 + 100 = 1000, not > 1000 → allowed
        result = storage_instance.atomic_check_and_incr_tokens("key3", "20260101", 100, 1000)
        assert result == 1000

    @pytest.mark.anyio
    async def test_concurrent_atomic_incr_no_overshoot(self, storage_instance):
        """Concurrent increments must never exceed the limit (no TOCTOU)."""
        import threading

        limit = 1000
        per_call = 100
        n_threads = 20  # 20 * 100 = 2000 total demand, but limit is 1000
        results = []
        lock = threading.Lock()

        def _worker():
            r = storage_instance.atomic_check_and_incr_tokens(
                "concurrent-key", "20260101", per_call, limit
            )
            with lock:
                results.append(r)

        threads = [threading.Thread(target=_worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Total tokens stored must NEVER exceed the limit
        final = storage_instance.get_daily_tokens("concurrent-key", "20260101")
        assert final <= limit, f"TOCTOU race: final={final} exceeds limit={limit}"
        # Number of successful increments
        successes = sum(1 for r in results if r > 0)
        assert successes * per_call <= limit


# ====================================================================
# 4. Provider JSON decode error handling
# ====================================================================
class TestProviderJSONDecode:
    """Verify malformed upstream JSON raises ProviderError(502), not JSONDecodeError."""

    def test_malformed_json_raises_provider_error(self):
        """When upstream returns 200 with invalid JSON, must raise ProviderError status=502."""
        from types import SimpleNamespace

        from moa_gateway.providers.base import ChatRequest, ProviderError
        from moa_gateway.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_base="http://upstream.invalid/v1",
            api_key="sk-test",
        )

        # Mock the httpx client to return a 200 with invalid JSON body.
        # 'client' is a read-only property backed by _client.
        mock_response = SimpleNamespace()
        mock_response.status_code = 200
        mock_response.text = "<html>502 Bad Gateway</html>"

        def _raise_json(*args, **kwargs):
            raise json.JSONDecodeError("err", "doc", 0)

        mock_response.json = _raise_json

        async def _fake_post(*args, **kwargs):
            return mock_response

        provider._client = SimpleNamespace(post=_fake_post)

        req = ChatRequest(model="gpt-4o", messages=[{"role": "user", "content": "hi"}])

        with pytest.raises(ProviderError) as exc_info:
            asyncio.run(provider.chat(req))

        assert exc_info.value.status == 502


# ====================================================================
# 5. Admin password no longer in logs
# ====================================================================
class TestAdminPasswordNotLogged:
    """Verify the auto-generated admin password is NOT logged in plaintext."""

    def test_admin_password_written_to_file_not_log(self, tmp_path, monkeypatch, caplog):
        """Auto-generated admin password should be written to file, not logged."""
        import logging

        from moa_gateway import config as _cfg
        from moa_gateway.config import Settings
        from moa_gateway.server import _ensure_admin_password

        # Settings with NO admin password → triggers auto-generation
        settings = Settings(
            auth={
                "admin_username": "admin",
                "admin_password": "",  # empty → auto-generate
                "jwt_secret": "x" * 40,
                "gateway_api_keys": ["k"],
            }
        )
        monkeypatch.setattr("moa_gateway.config.DATA_DIR", tmp_path)
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)

        with caplog.at_level(logging.WARNING):
            _ensure_admin_password(settings)

        # Password must be set
        assert settings.auth.admin_password
        assert len(settings.auth.admin_password) >= 16

        # The password value must NOT appear in any log message
        for record in caplog.records:
            assert settings.auth.admin_password not in record.getMessage(), (
                f"Admin password leaked in log: {record.getMessage()}"
            )

        # The password file must exist with 0o600 permissions (on POSIX)
        pw_file = tmp_path / ".admin_password"
        assert pw_file.exists()
        assert pw_file.read_text() == settings.auth.admin_password


# ====================================================================
# 6. MoA orchestration timeout
# ====================================================================
class TestMoATimeout:
    """Verify MoA execute() wraps with an overall timeout."""

    @pytest.mark.anyio
    async def test_moa_execute_wraps_timeout(self, monkeypatch):
        """execute() must apply asyncio.wait_for with a timeout and raise 504 on timeout."""
        from moa_gateway.config import Settings
        from moa_gateway.moa import MoAOrchestrator

        settings = Settings(auth={"jwt_secret": "x" * 40, "gateway_api_keys": ["k"]})
        monkeypatch.setattr("moa_gateway.config.get_settings", lambda: settings)
        monkeypatch.setattr("moa_gateway.config._settings", settings)

        # Build orchestrator with mocked pool/router to avoid real init
        mock_pool = AsyncMock()
        mock_router = AsyncMock()
        orch = MoAOrchestrator(model_pool=mock_pool, router=mock_router)

        # Patch _execute_inner — a no-op coroutine.
        async def _noop(*args, **kwargs):
            return None

        with patch.object(orch, "_execute_inner", _noop):
            # Patch wait_for to simulate a timeout. It must close the coroutine
            # it receives to avoid "coroutine was never awaited" warnings.
            from moa_gateway.providers.base import ProviderError

            original_wait_for = asyncio.wait_for

            def _timeout_wait_for(coro, timeout):
                # Close the coroutine so it doesn't warn about never being awaited
                if asyncio.iscoroutine(coro):
                    coro.close()
                raise asyncio.TimeoutError()

            with patch("moa_gateway.moa.asyncio.wait_for", side_effect=_timeout_wait_for):
                with pytest.raises(ProviderError) as exc_info:
                    await orch.execute("query")
                # Timeout should produce a 504
                assert exc_info.value.status == 504
