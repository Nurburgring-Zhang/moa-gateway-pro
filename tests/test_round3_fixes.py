"""Tests for Round 3 audit fixes: MCP internal error non-leak, MCP tool-call
error non-leak."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

# 模块级 os.environ.setdefault 会在收集期(import)泄漏到其它测试文件
# (曾导致 subagent 回环 401)——改为模块作用域 fixture: 仅本文件测试期间生效。
_ENV_NEEDED = {
    "MOA_JWT_SECRET": "test-secret-key-minimum-32-characters-long!",
    "MOA_ADMIN_PASSWORD": "TestPass#2024",
    "MOA_GATEWAY_KEY": "round3-key-001"
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

AUTH = {"Authorization": "Bearer round3-key-001"}


@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["round3-key-001"],
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
# 1. MCP internal errors don't leak exception details
# ====================================================================
class TestMCPErrorNonLeak:
    """Verify internal exceptions are not surfaced to API clients."""

    @pytest.mark.anyio
    async def test_unknown_method_returns_32601_without_exception(self, client):
        """Method-not-found must return the error code, not an exception trace."""
        resp = await client.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "nonexistent/method"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"]["code"] == -32601
        # The message must not contain a traceback or internal class names
        assert "Traceback" not in body["error"]["message"]

    @pytest.mark.anyio
    async def test_tool_call_failure_doesnt_leak_internals(self, client):
        """A failing tool handler must return a generic error, not the exception."""
        from moa_gateway.mcp.protocol import ToolDefinition
        from moa_gateway.routes.mcp import get_mcp_server

        server = get_mcp_server()
        reg = server.registry

        async def _boom(arguments):
            raise RuntimeError("SECRET: db_url=postgres://admin:p4ss@10.0.0.1/db")

        td = ToolDefinition(
            name="boom_tool",
            description="test tool that raises",
            inputSchema={"type": "object", "properties": {}},
        )
        reg.register(td, handler=_boom, allowed_roles={"admin", "operator", "user"})

        try:
            resp = await client.post(
                "/v1/mcp/tools/boom_tool/call",
                json={"arguments": {}},
                headers=AUTH,
            )
            # REST endpoint returns 400 on error result, or 200 — depends on path.
            # The key assertion: the sensitive string must NOT appear.
            text = resp.text
            assert "SECRET: db_url" not in text
            assert "postgres://admin:p4ss" not in text
        finally:
            reg.unregister("boom_tool")
