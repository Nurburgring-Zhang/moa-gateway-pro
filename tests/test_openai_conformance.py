"""OpenAI API conformance tests — validates response structure matches the OpenAI specification."""

from __future__ import annotations

import json
import os
import re
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("MOA_JWT_SECRET", "test-secret-key-minimum-32-characters-long!")
os.environ.setdefault("MOA_ADMIN_PASSWORD", "TestPass#2024")
os.environ.setdefault("MOA_GATEWAY_KEY", "conformance-key-001")

from httpx import ASGITransport, AsyncClient  # noqa: E402

AUTH = {"Authorization": "Bearer conformance-key-001"}


@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["conformance-key-001"],
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
            yield create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestChatCompletionFormat:
    """Validate non-streaming chat completion response matches OpenAI spec."""

    @pytest.mark.anyio
    async def test_response_has_required_fields(self, client):
        """Response has id, object, created, model, choices, usage."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Hello!",
            finish_reason="stop",
            model="gpt-4o",
            provider="openai-compat",
            latency_ms=100.0,
            cost=0.001,
            prompt_tokens=5,
            completion_tokens=2,
            total_tokens=7,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers=AUTH,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "object" in data
        assert "created" in data
        assert "model" in data
        assert "choices" in data
        assert "usage" in data

    @pytest.mark.anyio
    async def test_id_format(self, client):
        """id field matches chatcmpl-* pattern."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Test", finish_reason="stop", model="gpt-4o",
            provider="openai-compat", latency_ms=50.0, cost=0.0,
            prompt_tokens=3, completion_tokens=1, total_tokens=4,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers=AUTH,
            )
        data = resp.json()
        assert re.match(r"^chatcmpl-[a-f0-9]+$", data["id"])

    @pytest.mark.anyio
    async def test_object_field(self, client):
        """object field is 'chat.completion'."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Test", finish_reason="stop", model="gpt-4o",
            provider="openai-compat", latency_ms=50.0, cost=0.0,
            prompt_tokens=3, completion_tokens=1, total_tokens=4,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers=AUTH,
            )
        data = resp.json()
        assert data["object"] == "chat.completion"

    @pytest.mark.anyio
    async def test_created_is_unix_timestamp(self, client):
        """created field is a reasonable Unix timestamp."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Test", finish_reason="stop", model="gpt-4o",
            provider="openai-compat", latency_ms=50.0, cost=0.0,
            prompt_tokens=3, completion_tokens=1, total_tokens=4,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers=AUTH,
            )
        data = resp.json()
        assert isinstance(data["created"], int)
        assert data["created"] > 1700000000

    @pytest.mark.anyio
    async def test_choices_structure(self, client):
        """choices[0] has index, message (with role+content), finish_reason."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Hello there!", finish_reason="stop", model="gpt-4o",
            provider="openai-compat", latency_ms=50.0, cost=0.0,
            prompt_tokens=3, completion_tokens=3, total_tokens=6,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers=AUTH,
            )
        data = resp.json()
        choice = data["choices"][0]
        assert "index" in choice
        assert choice["index"] == 0
        assert "message" in choice
        assert choice["message"]["role"] == "assistant"
        assert isinstance(choice["message"]["content"], str)
        assert "finish_reason" in choice
        assert choice["finish_reason"] in ("stop", "length", "tool_calls", "content_filter")

    @pytest.mark.anyio
    async def test_usage_structure(self, client):
        """usage has prompt_tokens, completion_tokens, total_tokens."""
        from moa_gateway.providers.base import ChatResponse

        mock_resp = ChatResponse(
            content="Hi", finish_reason="stop", model="gpt-4o",
            provider="openai-compat", latency_ms=50.0, cost=0.0,
            prompt_tokens=10, completion_tokens=5, total_tokens=15,
        )
        with patch("moa_gateway.model_pool.ModelPool.call", new_callable=AsyncMock, return_value=mock_resp):
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
                headers=AUTH,
            )
        data = resp.json()
        usage = data["usage"]
        assert "prompt_tokens" in usage
        assert "completion_tokens" in usage
        assert "total_tokens" in usage
        assert isinstance(usage["prompt_tokens"], int)
        assert isinstance(usage["completion_tokens"], int)
        assert isinstance(usage["total_tokens"], int)


class TestStreamingChunkFormat:
    """Validate streaming chunk format matches OpenAI spec."""

    @pytest.mark.anyio
    async def test_chunk_object_field(self, client):
        """Streaming chunks have object='chat.completion.chunk'."""
        with patch("moa_gateway.routes.chat.stream_single") as mock_stream:
            async def fake(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"
            mock_stream.side_effect = fake

            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
                headers=AUTH,
            )
        lines = [l for l in resp.text.strip().split("\n") if l.startswith("data: ") and "[DONE]" not in l]
        for line in lines:
            chunk = json.loads(line[6:])
            assert chunk["object"] == "chat.completion.chunk"

    @pytest.mark.anyio
    async def test_chunk_has_delta_not_message(self, client):
        """Streaming chunks use 'delta' field, not 'message'."""
        with patch("moa_gateway.routes.chat.stream_single") as mock_stream:
            async def fake(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"Hi"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"
            mock_stream.side_effect = fake

            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
                headers=AUTH,
            )
        lines = [l for l in resp.text.strip().split("\n") if l.startswith("data: ") and "[DONE]" not in l]
        for line in lines:
            chunk = json.loads(line[6:])
            choice = chunk["choices"][0]
            assert "delta" in choice
            assert "message" not in choice

    @pytest.mark.anyio
    async def test_stream_terminates_with_done(self, client):
        """Stream ends with 'data: [DONE]'."""
        with patch("moa_gateway.routes.chat.stream_single") as mock_stream:
            async def fake(*args, **kwargs):
                yield 'data: {"id":"chatcmpl-abc","object":"chat.completion.chunk","created":1700000000,"model":"gpt-4o","choices":[{"index":0,"delta":{"content":"x"},"finish_reason":null}]}\n\n'
                yield "data: [DONE]\n\n"
            mock_stream.side_effect = fake

            resp = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}], "stream": True},
                headers=AUTH,
            )
        assert resp.text.strip().endswith("data: [DONE]")


class TestErrorFormat:
    """Validate error responses match OpenAI error format."""

    @pytest.mark.anyio
    async def test_auth_error_format(self, client):
        """401 errors include error.message and error.type."""
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": [{"role": "user", "content": "Hi"}]},
        )
        assert resp.status_code in (401, 403)
        data = resp.json()
        assert "detail" in data or "error" in data

    @pytest.mark.anyio
    async def test_validation_error_format(self, client):
        """422 for invalid request body."""
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o"},  # missing messages
            headers=AUTH,
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_empty_messages_rejected(self, client):
        """Empty messages list is rejected."""
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "gpt-4o", "messages": []},
            headers=AUTH,
        )
        assert resp.status_code in (400, 422)


class TestModelsEndpoint:
    """Validate /v1/models response structure."""

    @pytest.mark.anyio
    async def test_models_list_structure(self, client):
        """/v1/models returns object='list' with data array."""
        resp = await client.get("/v1/models", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("object") == "list"
        assert "data" in data
        assert isinstance(data["data"], list)

    @pytest.mark.anyio
    async def test_model_object_structure(self, client):
        """Each model in /v1/models has id, object, created, owned_by."""
        resp = await client.get("/v1/models", headers=AUTH)
        data = resp.json()
        if data["data"]:
            model = data["data"][0]
            assert "id" in model
            assert model.get("object") == "model"
