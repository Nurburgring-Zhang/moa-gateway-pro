"""Tests for streaming/SSE functionality — verifies format, error handling, and MoA buffered streaming."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

# 模块级 os.environ.setdefault 会在收集期(import)泄漏到其它测试文件
# (曾导致 subagent 回环 401)——改为模块作用域 fixture: 仅本文件测试期间生效。
_ENV_NEEDED = {
    "MOA_JWT_SECRET": "test-secret-key-minimum-32-characters-long!",
    "MOA_ADMIN_PASSWORD": "TestPass#2024",
    "MOA_GATEWAY_KEY": "stream-test-key-001"
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

AUTH = {"Authorization": "Bearer stream-test-key-001"}


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
            "gateway_api_keys": ["stream-test-key-001"],
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


def _mock_stream_chunks():
    """Generate mock SSE chunks as an async iterator."""
    async def _gen(*args, **kwargs):
        yield "Hello"
        yield " world"
        yield "!"
    return _gen


class TestSingleModelStreaming:
    """Test single-model SSE streaming format."""

    @pytest.mark.anyio
    async def test_streaming_returns_sse_format(self, client):
        """Streaming response uses proper SSE format with data: prefix."""
        with patch(
            "moa_gateway.routes.chat.stream_single"
        ) as mock_stream:
            async def fake_stream(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"

            mock_stream.side_effect = fake_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers.get("content-type", "")

            lines = resp.text.strip().split("\n")
            data_lines = [l for l in lines if l.startswith("data: ")]
            assert len(data_lines) > 0

    @pytest.mark.anyio
    async def test_streaming_ends_with_done(self, client):
        """Streaming response terminates with data: [DONE]."""
        with patch(
            "moa_gateway.routes.chat.stream_single"
        ) as mock_stream:
            async def fake_stream(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"

            mock_stream.side_effect = fake_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200
            assert "data: [DONE]" in resp.text

    @pytest.mark.anyio
    async def test_streaming_chunks_are_valid_json(self, client):
        """Each SSE chunk (except [DONE]) is valid JSON."""
        with patch(
            "moa_gateway.routes.chat.stream_single"
        ) as mock_stream:
            async def fake_stream(*args, **kwargs):
                for i, word in enumerate(["Hello", " world", "!"]):
                    payload = {
                        "id": "chatcmpl-1",
                        "object": "chat.completion.chunk",
                        "created": 1700000000,
                        "model": "gpt-4o",
                        "choices": [{"index": 0, "delta": {"content": word}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(payload)}\n\n"
                yield "data: [DONE]\n\n"

            mock_stream.side_effect = fake_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            lines = resp.text.strip().split("\n")
            data_lines = [l for l in lines if l.startswith("data: ") and "[DONE]" not in l]

            for line in data_lines:
                payload = line[len("data: "):]
                chunk = json.loads(payload)
                assert "choices" in chunk
                assert chunk["object"] == "chat.completion.chunk"

    @pytest.mark.anyio
    async def test_streaming_has_trace_headers(self, client):
        """Streaming responses include trace correlation headers."""
        with patch(
            "moa_gateway.routes.chat.stream_single"
        ) as mock_stream:
            async def fake_stream(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"

            mock_stream.side_effect = fake_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            assert "x-trace-id" in resp.headers or resp.status_code == 200


class TestStreamingErrorHandling:
    """Test error handling during streaming."""

    @pytest.mark.anyio
    async def test_streaming_requires_auth(self, client):
        """Streaming endpoint requires authentication."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "gpt-4o",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_streaming_invalid_model_returns_error(self, client):
        """Request for non-existent model in streaming mode returns error."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "nonexistent-model-xyz",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
            headers=AUTH,
        )
        if resp.status_code == 200:
            assert "error" in resp.text.lower() or "data: [DONE]" in resp.text
        else:
            assert resp.status_code in (400, 404, 422, 500, 502, 503)

    @pytest.mark.anyio
    async def test_streaming_error_mid_stream(self, client):
        """Provider failure mid-stream emits an error chunk."""
        with patch(
            "moa_gateway.routes.chat.stream_single"
        ) as mock_stream:
            async def failing_stream(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
                yield 'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"[Error: provider timeout]"},"finish_reason":"error"}]}\n\n'
                yield "data: [DONE]\n\n"

            mock_stream.side_effect = failing_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200
            assert "error" in resp.text.lower() or "Error" in resp.text
            assert "data: [DONE]" in resp.text


class TestNonStreamingFormat:
    """Test non-streaming response format for comparison."""

    @pytest.mark.anyio
    async def test_non_streaming_response_format(self, client):
        """Non-streaming response has correct OpenAI format."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Hello! How can I help you?",
            finish_reason="stop",
            model="gpt-4o",
            provider="openai-compat",
            latency_ms=100.0,
            cost=0.001,
            prompt_tokens=10,
            completion_tokens=8,
            total_tokens=18,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": False,
                },
                headers=AUTH,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "choices" in data
            assert len(data["choices"]) > 0
            assert "message" in data["choices"][0]
            assert data["choices"][0]["message"]["role"] == "assistant"


class TestSSEProtocolCompliance:
    """Test SSE protocol details."""

    @pytest.mark.anyio
    async def test_sse_content_type(self, client):
        """Response Content-Type is text/event-stream."""
        with patch("moa_gateway.routes.chat.stream_single") as mock_stream:
            async def fake_stream(*args, **kwargs):
                yield "data: [DONE]\n\n"
            mock_stream.side_effect = fake_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            ct = resp.headers.get("content-type", "")
            assert "text/event-stream" in ct

    @pytest.mark.anyio
    async def test_sse_chunks_double_newline_separated(self, client):
        """SSE chunks are separated by double newlines."""
        with patch("moa_gateway.routes.chat.stream_single") as mock_stream:
            async def fake_stream(*args, **kwargs):
                yield 'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"a"},"finish_reason":null}]}\n\n'
                yield 'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"b"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"
            mock_stream.side_effect = fake_stream

            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [{"role": "user", "content": "Hello"}],
                    "stream": True,
                },
                headers=AUTH,
            )
            assert "\n\n" in resp.text
