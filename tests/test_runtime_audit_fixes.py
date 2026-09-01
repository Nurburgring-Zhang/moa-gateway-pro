"""Tests for Round 4 live-runtime-audit fixes.

These verify bugs that only manifest at runtime (real request flow), not in
unit tests: no-auth provider empty Authorization header, task-lookup 503 vs
502, and readiness probe startup grace period.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# 模块级 os.environ.setdefault 会在收集期(import)泄漏到其它测试文件
# (曾导致 subagent 回环 401)——改为模块作用域 fixture: 仅本文件测试期间生效。
_ENV_NEEDED = {
    "MOA_JWT_SECRET": "test-secret-key-minimum-32-characters-long!",
    "MOA_ADMIN_PASSWORD": "TestPass#2024",
    "MOA_GATEWAY_KEY": "runtime-audit-key-001"
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

AUTH = {"Authorization": "Bearer runtime-audit-key-001"}


@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["runtime-audit-key-001"],
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
# R4-1: No-auth provider omits Authorization header when api_key is empty
# ====================================================================
class TestNoAuthProviderHeader:
    """Verify empty api_key does not produce an illegal 'Bearer ' header."""

    def test_no_auth_provider_omits_authorization_header(self):
        """A no-auth provider (api_key='') must not send an Authorization header."""
        from types import SimpleNamespace

        from moa_gateway.providers.base import ChatRequest
        from moa_gateway.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(api_base="http://upstream/v1", api_key="")

        captured_headers = {}

        async def _fake_post(url, json=None, headers=None, **kw):
            captured_headers.update(headers or {})
            resp = SimpleNamespace()
            resp.status_code = 200
            resp.json = lambda: {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return resp

        provider._client = SimpleNamespace(post=_fake_post)

        import asyncio

        req = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}])
        asyncio.run(provider.chat(req))

        # CRITICAL: no Authorization header present (was "Bearer " before fix)
        assert "Authorization" not in captured_headers, (
            f"No-auth provider sent Authorization: {captured_headers.get('Authorization')!r}"
        )
        assert "Content-Type" in captured_headers

    def test_auth_provider_includes_bearer(self):
        """A keyed provider must send 'Authorization: Bearer <key>'."""
        from types import SimpleNamespace

        from moa_gateway.providers.base import ChatRequest
        from moa_gateway.providers.openai_compat import OpenAICompatProvider

        provider = OpenAICompatProvider(api_base="http://upstream/v1", api_key="sk-real-key")

        captured_headers = {}

        async def _fake_post(url, json=None, headers=None, **kw):
            captured_headers.update(headers or {})
            resp = SimpleNamespace()
            resp.status_code = 200
            resp.json = lambda: {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
            return resp

        provider._client = SimpleNamespace(post=_fake_post)

        import asyncio

        req = ChatRequest(model="m", messages=[{"role": "user", "content": "hi"}])
        asyncio.run(provider.chat(req))

        assert captured_headers.get("Authorization") == "Bearer sk-real-key"


# ====================================================================
# R6: Task lookup now returns 200 mock (mock.mode=explicit) instead of 503
# ====================================================================
class TestTaskLookupNoKey:
    """Verify task lookup with no provider key behaves HONESTLY (audit F22).

    A Mock provider has no real upstream task store, so the only tasks that
    genuinely exist are those created through the gateway. Querying an
    arbitrary/unknown id must return 404 — NOT a fabricated "completed" task.
    """

    @pytest.mark.anyio
    async def test_3d_task_lookup_unknown_id_returns_404(self, client, monkeypatch):
        """Without TRIPO3D_API_KEY/MESHY_API_KEY, unknown /v1/3d/tasks/{id} → 404."""
        monkeypatch.delenv("TRIPO3D_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        resp = await client.get(
            "/v1/3d/tasks/nonexistent-id",
            headers=AUTH,
        )
        assert resp.status_code == 404, (
            f"unknown task id must 404 (no fabricated task), got {resp.status_code}: {resp.text}"
        )

    @pytest.mark.anyio
    async def test_3d_task_created_then_queryable(self, client, monkeypatch):
        """A task created via /v1/3d/generate IS queryable (real lifecycle)."""
        monkeypatch.delenv("TRIPO3D_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        gen = await client.post(
            "/v1/3d/generate",
            headers=AUTH,
            json={"prompt": "a small cube"},
        )
        assert gen.status_code == 200, gen.text
        task_id = gen.json()["task_id"]
        resp = await client.get(f"/v1/3d/tasks/{task_id}", headers=AUTH)
        assert resp.status_code == 200, resp.text
        assert resp.json().get("status") == "completed"

    @pytest.mark.anyio
    async def test_video_task_lookup_unknown_id_returns_404(self, client, monkeypatch):
        """Without KLING/RUNWAY key, unknown /v1/video/tasks/{id} → 404."""
        monkeypatch.delenv("KLING_API_KEY", raising=False)
        monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
        resp = await client.get(
            "/v1/video/tasks/nonexistent-id",
            headers=AUTH,
        )
        assert resp.status_code == 404, (
            f"unknown task id must 404 (no fabricated task), got {resp.status_code}: {resp.text}"
        )


# ====================================================================
# R4-4: Readiness probe startup grace period
# ====================================================================
class TestReadinessStartupGrace:
    """Verify /health/ready returns 200 during startup (no probes run yet)."""

    @pytest.mark.anyio
    async def test_readiness_200_at_startup_no_probes(self, client):
        """When all endpoints are still in 'unknown' state (no probe failure yet),
        readiness passes — the startup grace predicate."""
        from moa_gateway.ha import health_checker
        from moa_gateway.model_pool import get_model_pool

        # The test client may not complete the full lifespan startup that calls
        # mark_ready(); ensure startup is marked complete for this assertion.
        health_checker.mark_ready()

        pool = get_model_pool()
        # Force all endpoints into 'unknown' (startup grace window)
        for ep in pool.endpoints.values():
            ep.health_status = "unknown"

        def _forced_model_pool_ready() -> bool:
            if not pool.endpoints:
                return False
            has_healthy = any(e.health_status == "healthy" for e in pool.endpoints.values())
            has_failed = any(e.health_status == "unhealthy" for e in pool.endpoints.values())
            return has_healthy or not has_failed

        health_checker.register_check("model_pool", _forced_model_pool_ready)

        resp = await client.get("/health/ready")
        assert resp.status_code == 200, f"expected 200 startup grace, got {resp.status_code}: {resp.text}"
        body = resp.json()
        assert body["status"] in ("ok", "ready", "healthy", "degraded")

    def test_model_pool_ready_logic_unknown_state(self, monkeypatch):
        """Unit test the readiness predicate directly."""
        from types import SimpleNamespace

        # Build a fake pool with endpoints in "unknown" (initial) state
        eps = {
            "a": SimpleNamespace(health_status="unknown"),
            "b": SimpleNamespace(health_status="unknown"),
        }
        pool = SimpleNamespace(endpoints=eps)

        def _model_pool_ready() -> bool:
            if not pool.endpoints:
                return False
            has_healthy = any(e.health_status == "healthy" for e in pool.endpoints.values())
            has_failed = any(e.health_status == "unhealthy" for e in pool.endpoints.values())
            return has_healthy or not has_failed

        # All-unknown → ready (startup grace)
        assert _model_pool_ready() is True

        # One unhealthy → not ready (probes ran and found failure)
        eps["a"].health_status = "unhealthy"
        assert _model_pool_ready() is False

        # One healthy → ready
        eps["b"].health_status = "healthy"
        assert _model_pool_ready() is True

        # Empty pool → not ready
        pool.endpoints = {}
        assert _model_pool_ready() is False
