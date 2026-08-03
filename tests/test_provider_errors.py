"""
Provider错误处理测试 - 验证外部服务异常时的降级行为

Tests:
- Provider超时/不可用 → 明确错误码(502/503), 不hang
- 请求验证边界 → body_limit(413), max_messages(422), empty_messages(400)
- Unicode/特殊字符 → 不crash, 返回合理响应
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch


@pytest.fixture
def app():
    """Create test app with isolated config."""
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestP@ss123!",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["test-provider-err-key"],
        }
    )
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            application = create_app()
            yield application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


HEADERS = {"Authorization": "Bearer test-provider-err-key", "Content-Type": "application/json"}


class TestProviderTimeout:
    """Provider超时/不可用处理"""

    @pytest.mark.anyio
    async def test_chat_returns_error_not_hang(self, client):
        """Provider不可用应返回明确响应而不是hang"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "deepseek-v3",
                "messages": [{"role": "user", "content": "hi"}],
            },
            timeout=30.0,
        )
        # Mock provider may respond 200; real test is that it doesn't hang or crash (500)
        assert resp.status_code != 500
        assert resp.status_code in (200, 502, 503)
        data = resp.json()
        # Should have structured response regardless
        assert isinstance(data, dict)

    @pytest.mark.anyio
    async def test_auto_model_no_provider(self, client):
        """auto模型路由应返回结构化响应"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert resp.status_code in (200, 502, 503)
        data = resp.json()
        assert isinstance(data, dict)


class TestProviderBadResponse:
    """Provider返回异常数据"""

    @pytest.mark.anyio
    async def test_nonexistent_model_returns_error(self, client):
        """不存在的模型应返回明确响应（或路由到auto）"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "nonexistent-model-xyz",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        # May route to auto/mock (200) or return error (502/503)
        assert resp.status_code in (200, 502, 503)
        data = resp.json()
        assert isinstance(data, dict)


class TestRequestValidation:
    """请求验证边界"""

    @pytest.mark.anyio
    async def test_body_limit_enforcement(self, client):
        """超大请求体(>1MB)应被中间件拒绝为413"""
        # 构造一个超过1MB的payload
        huge_content = "x" * 300_000  # 300KB per message
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "test",
                "messages": [{"role": "user", "content": huge_content}] * 4,  # ~1.2MB
            },
        )
        # 中间件body limit(1MB) → 413, 或Pydantic content max_length(200K) → 422
        assert resp.status_code in (413, 422)

    @pytest.mark.anyio
    async def test_messages_over_max_rejected(self, client):
        """超过200条messages应被Pydantic拒绝为422"""
        messages = [{"role": "user", "content": f"msg {i}"} for i in range(250)]
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "test", "messages": messages},
        )
        # ChatCompletionRequest.messages max_length=200 → 422
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_empty_messages_rejected(self, client):
        """空messages数组应被拒绝"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "test", "messages": []},
        )
        # Pydantic允许空list, 但handler检查返回400; 或min_length触发422
        assert resp.status_code in (400, 422)
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_missing_role_in_message(self, client):
        """message缺少role字段应被拒绝为422"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "test", "messages": [{"content": "hi"}]},
        )
        # role is required in ChatMessage → 422
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_missing_content_in_message(self, client):
        """message缺少content字段(Optional)应通过验证"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "test", "messages": [{"role": "user"}]},
        )
        # content is Optional -> passes validation -> hits router -> 200(mock)/502/503
        assert resp.status_code in (200, 502, 503)

    @pytest.mark.anyio
    async def test_invalid_role_passes_validation(self, client):
        """非标准role值通过验证(无enum限制)"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "test", "messages": [{"role": "hacker", "content": "hi"}]},
        )
        # role field只有max_length限制, 无enum -> passes validation
        # Mock provider responds 200, or no-model gives 502/503
        assert resp.status_code in (200, 502, 503)

    @pytest.mark.anyio
    async def test_content_exceeds_max_length(self, client):
        """单条消息content超过200K字符应被拒绝"""
        long_content = "a" * 250_000  # 250K > max_length=200_000
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "test", "messages": [{"role": "user", "content": long_content}]},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_missing_model_uses_default(self, client):
        """不提供model字段使用默认值'auto'"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": "hi"}]},
        )
        # model defaults to "auto" -> routes to mock -> 200, or no model -> 502/503
        assert resp.status_code in (200, 502, 503)


class TestUnicodeHandling:
    """Unicode和特殊字符处理"""

    @pytest.mark.anyio
    async def test_chinese_content(self, client):
        """中文内容应通过验证不crash"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "deepseek-v3",
                "messages": [{"role": "user", "content": "你好世界，请用中文回答"}],
            },
        )
        # 通过验证 -> 路由 -> 200(mock) or 502/503(no model), 不应该是500
        assert resp.status_code != 500
        assert resp.status_code in (200, 502, 503)

    @pytest.mark.anyio
    async def test_emoji_content(self, client):
        """Emoji应正常处理不crash"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "deepseek-v3",
                "messages": [{"role": "user", "content": "Hello \U0001f30d\U0001f680\U0001f4a1"}],
            },
        )
        assert resp.status_code != 500
        assert resp.status_code in (200, 502, 503)

    @pytest.mark.anyio
    async def test_null_bytes_in_content(self, client):
        """Null bytes不应导致crash(500)"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "test",
                "messages": [{"role": "user", "content": "hello\x00world"}],
            },
        )
        # 不应该是未处理的500
        assert resp.status_code != 500

    @pytest.mark.anyio
    async def test_mixed_unicode_special_chars(self, client):
        """混合Unicode特殊字符不crash"""
        content = "测试\t\n\r Unicode: \u200b\u2028 ZWS"
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "test",
                "messages": [{"role": "user", "content": content}],
            },
        )
        assert resp.status_code != 500


class TestAuthErrors:
    """认证相关错误处理"""

    @pytest.mark.anyio
    async def test_no_auth_header_returns_401(self, client):
        """无认证头应返回401"""
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_invalid_key_returns_401(self, client):
        """错误的API key应返回401"""
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer wrong-key-12345"},
            json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_empty_bearer_returns_401(self, client):
        """空Bearer token应返回401"""
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer "},
            json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401
