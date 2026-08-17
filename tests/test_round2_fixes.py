"""Tests for Round 2 audit fixes: streaming error spec compliance, graceful
shutdown counter non-negativity, and graceful shutdown task reference retention."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("MOA_JWT_SECRET", "test-secret-key-minimum-32-characters-long!")
os.environ.setdefault("MOA_ADMIN_PASSWORD", "TestPass#2024")
os.environ.setdefault("MOA_GATEWAY_KEY", "round2-key-001")

from httpx import ASGITransport, AsyncClient  # noqa: E402

AUTH = {"Authorization": "Bearer round2-key-001"}


@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["round2-key-001"],
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
# 1. Streaming error chunk is OpenAI-spec compliant
# ====================================================================
class TestStreamingErrorSpecCompliance:
    """Verify streaming errors use spec-valid finish_reason and don't leak internals."""

    @pytest.mark.anyio
    async def test_stream_error_uses_valid_finish_reason(self, client):
        """When the provider fails mid-stream, finish_reason must be spec-valid
        (not the non-spec 'error' value)."""
        with patch("moa_gateway.routes.chat.stream_single") as mock_stream:
            # Simulate a provider that raises mid-stream — the real stream_single
            # is invoked here (not mocked) so we test its error format.
            from moa_gateway._helpers import stream_single

            # Force the inner provider to raise
            async def failing_chat_stream(*args, **kwargs):
                raise RuntimeError("SECRET_DB_CONNECTION_STRING=postgres://user:pass@db")
                yield  # noqa: unreachable — make it an async generator

            mock_stream.side_effect = stream_single  # use real implementation

            # Patch the pool's provider to fail
            pool = app._client_pool if hasattr(app, "_client_pool") else None
            from moa_gateway.model_pool import get_model_pool

            real_pool = get_model_pool()
            ep = real_pool.endpoints.get("gpt-4o")
            if ep:
                ep.provider_obj = type(
                    "FailingProvider",
                    (),
                    {
                        "chat_stream": staticmethod(failing_chat_stream),
                        "chat": AsyncMock(side_effect=RuntimeError("secret")),
                    },
                )()

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
            text = resp.text
            # The internal secret must NOT appear in the response
            assert "SECRET_DB_CONNECTION_STRING" not in text
            assert "postgres://user:pass" not in text
            # finish_reason must be spec-valid if present
            for line in text.split("\n"):
                if line.startswith("data:") and "[DONE]" not in line:
                    chunk = line[len("data:"):].strip()
                    if chunk:
                        try:
                            d = json.loads(chunk)
                            for ch in d.get("choices", []):
                                fr = ch.get("finish_reason")
                                if fr is not None:
                                    assert fr in (
                                        "stop",
                                        "length",
                                        "tool_calls",
                                        "content_filter",
                                    ), f"invalid finish_reason: {fr}"
                        except json.JSONDecodeError:
                            pass


# ====================================================================
# 2. Graceful shutdown counter never goes negative
# ====================================================================
class TestGracefulShutdownCounter:
    """Verify decrement never produces negative active_requests."""

    def test_decrement_below_zero_clamped(self):
        from moa_gateway.ha.graceful import GracefulShutdown

        gs = GracefulShutdown()
        assert gs.active_requests == 0
        gs.decrement_requests()  # would go to -1 without the guard
        assert gs.active_requests == 0  # clamped at 0

    def test_increment_decrement_balanced(self):
        from moa_gateway.ha.graceful import GracefulShutdown

        gs = GracefulShutdown()
        for _ in range(10):
            gs.increment_requests()
        assert gs.active_requests == 10
        for _ in range(10):
            gs.decrement_requests()
        assert gs.active_requests == 0


# ====================================================================
# 3. Circuit breaker fully recovers from repeated half-open failures
# ====================================================================
class TestCircuitBreakerRepeatedRecovery:
    """Verify a breaker can recover even after multiple failed probe cycles."""

    def test_multiple_failed_probes_still_recoverable(self):
        from moa_gateway.ha.circuit_breaker import CircuitBreaker, CircuitBreakerConfig

        import time

        cfg = CircuitBreakerConfig(
            failure_threshold=2,
            recovery_timeout=0.01,
            half_open_max_calls=2,
            success_threshold=1,
        )
        cb = CircuitBreaker("recovery-test", config=cfg)

        # Trip it
        cb.record_failure()
        cb.record_failure()
        assert cb.state.value == "open"

        # Two failed probe cycles — each should reset and allow re-probing
        for _ in range(3):
            time.sleep(0.05)
            # Must be able to get a probe slot each cycle
            allowed = cb.allow_request()
            assert allowed, "Breaker got stuck — probe slot not available after recovery window"
            cb.record_failure()  # probe fails, back to open

        # Finally, a successful recovery
        time.sleep(0.05)
        assert cb.allow_request()  # probe slot
        cb.record_success()  # success_threshold=1 → CLOSED
        assert cb.state.value == "closed"
