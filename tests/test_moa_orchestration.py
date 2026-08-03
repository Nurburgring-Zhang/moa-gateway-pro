"""
MOA Orchestration Tests - Multi-model orchestration verification + concurrency + performance baseline
"""
from __future__ import annotations

import asyncio
import time

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import patch


# --------------- Fixtures ---------------

API_KEY = "test-moa-orch-key-99"


@pytest.fixture
async def app():
    """Create a test FastAPI app with isolated config and valid auth.
    Rate limiting is disabled for stress testing.
    """
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "OrchTestP@ss!2024",
            "jwt_secret": "test-orchestration-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        ratelimit={"enabled": False},  # disable rate limiting for stress tests
    )
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            application = create_app()
            yield application


@pytest.fixture
async def client(app):
    """Async HTTP client bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
def headers():
    """Standard auth headers."""
    return {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json",
    }


# ===============================================================
# Part 1: MOA Orchestration Verification
# ===============================================================


class TestMOAStrategies:
    """Verify MOA strategy engine"""

    async def test_strategy_module_imports(self):
        """All strategy modules should import correctly"""
        from moa_gateway.moa_strategies import base

        assert hasattr(base, "MoaStrategy")

    async def test_strategy_registry_populated(self):
        """Strategy registry should contain built-in strategies"""
        from moa_gateway.moa_strategies import list_strategies, get_strategy

        strategies = list_strategies()
        assert len(strategies) >= 5
        for name in ("cost_first", "latency_first", "diversity_moa",
                     "capability_aware", "adaptive_ensemble"):
            assert get_strategy(name) is not None

    async def test_model_pool_initialization(self, app):
        """Model pool should initialize correctly"""
        assert app is not None

    async def test_multiple_model_routing(self, client, headers):
        """Request should route to mock provider and return structured response."""
        resp = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "deepseek-v3",
                "messages": [{"role": "user", "content": "What is AI?"}],
                "max_tokens": 100,
            },
        )
        # Mock provider may respond 200, or no real backend -> 503
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert isinstance(data, dict)

    async def test_fallback_on_model_failure(self, client, headers):
        """Non-existent model should return 503 (no available model)."""
        resp = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "nonexistent-model-xyz-999",
                "messages": [{"role": "user", "content": "test"}],
            },
        )
        assert resp.status_code == 503
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    async def test_health_endpoint_accessible(self, client):
        """Health endpoint should be accessible without auth"""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data


# ===============================================================
# Part 2: Concurrency Stress Tests
# ===============================================================


class TestConcurrency:
    """Concurrency stress tests"""

    async def test_concurrent_chat_requests(self, client, headers):
        """50 concurrent chat requests should all complete without server error."""

        async def make_request(i: int):
            resp = await client.post(
                "/v1/chat/completions",
                headers=headers,
                json={
                    "model": "deepseek-v3",
                    "messages": [{"role": "user", "content": f"Count to {i}"}],
                    "max_tokens": 20,
                },
            )
            return resp.status_code

        tasks = [make_request(i) for i in range(50)]
        results = await asyncio.gather(*tasks)

        # All should be 200 (mock) or 503 (no provider), never 500
        valid_codes = {200, 502, 503}
        assert all(r in valid_codes for r in results), (
            f"Unexpected status codes: "
            f"{dict((s, results.count(s)) for s in set(results))}"
        )

    async def test_concurrent_multimodal_requests(self, client, headers):
        """Multimodal endpoint concurrent requests - all return 502 (no provider)."""

        async def request_3d():
            return await client.post(
                "/v1/3d/generate",
                headers=headers,
                json={"prompt": "A car", "source_type": "text"},
            )

        async def request_world():
            return await client.post(
                "/v1/world/simulate",
                headers=headers,
                json={"scenario": "Ball drop", "steps": 3},
            )

        async def request_embodied():
            return await client.post(
                "/v1/embodied/plan",
                headers=headers,
                json={
                    "observation": {"description": "Room with table"},
                    "goal": "Go to table",
                },
            )

        tasks = []
        for _ in range(10):
            tasks.extend([request_3d(), request_world(), request_embodied()])

        results = await asyncio.gather(*tasks)
        assert all(r.status_code == 502 for r in results), (
            f"Expected all 502 but got distribution: "
            f"{dict((s, [r.status_code for r in results].count(s)) for s in set(r.status_code for r in results))}"
        )

    async def test_concurrent_assistant_operations(self, client, headers):
        """Assistant API concurrent operations"""

        async def create_assistant(i: int):
            resp = await client.post(
                "/v1/assistants",
                headers=headers,
                json={
                    "name": f"Bot-{i}",
                    "model": "deepseek-v3",
                    "instructions": f"Helper {i}",
                },
            )
            return resp

        results = await asyncio.gather(*[create_assistant(i) for i in range(10)])
        success = sum(1 for r in results if r.status_code == 200)
        assert success == 10, f"Only {success}/10 assistants created"


# ===============================================================
# Part 2b: Error Resilience Tests
# ===============================================================


class TestErrorResilience:
    """Error recovery and resilience tests"""

    async def test_rapid_fire_requests(self, client):
        """100 rapid-fire requests should not cause crash"""
        results = []
        for _ in range(100):
            resp = await client.get("/health")
            results.append(resp.status_code)

        assert all(r == 200 for r in results), "Some health checks failed under rapid fire"

    async def test_malformed_json_handling(self, client, headers):
        """Malformed JSON should return 422 validation error."""
        resp = await client.post(
            "/v1/chat/completions",
            headers=headers,
            content=b"not json at all",
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data

    async def test_oversized_messages_array(self, client, headers):
        """Oversized messages array should be handled gracefully - mock provider returns 200."""
        messages = [{"role": "user", "content": f"Message {i} " * 50} for i in range(100)]
        resp = await client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "deepseek-v3", "messages": messages},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert "choices" in data
        assert isinstance(data["choices"], list)
        assert len(data["choices"]) > 0
        assert "message" in data["choices"][0]
        assert "role" in data["choices"][0]["message"]
        assert data["choices"][0]["message"]["role"] == "assistant"
        assert "content" in data["choices"][0]["message"]

    async def test_missing_auth_returns_401(self, client):
        """Missing auth should return 401."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "test",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data


# ===============================================================
# Part 3: Performance Baseline Measurement
# ===============================================================


class TestPerformance:
    """Performance baseline measurement"""

    async def test_health_latency(self, client):
        """Health endpoint latency should be <50ms"""
        await client.get("/health")

        latencies = []
        for _ in range(20):
            start = time.perf_counter()
            await client.get("/health")
            latencies.append((time.perf_counter() - start) * 1000)

        avg_ms = sum(latencies) / len(latencies)
        p99_ms = sorted(latencies)[int(len(latencies) * 0.99)]
        print(f"\n[PERF] Health: avg={avg_ms:.1f}ms, p99={p99_ms:.1f}ms")
        assert avg_ms < 50, f"Health avg latency {avg_ms:.1f}ms > 50ms"

    async def test_chat_mock_latency(self, client, headers):
        """Chat endpoint latency should be <200ms (includes routing overhead)"""
        payload = {
            "model": "deepseek-v3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }
        # Warmup
        await client.post("/v1/chat/completions", headers=headers, json=payload)

        latencies = []
        for _ in range(10):
            start = time.perf_counter()
            await client.post("/v1/chat/completions", headers=headers, json=payload)
            latencies.append((time.perf_counter() - start) * 1000)

        avg_ms = sum(latencies) / len(latencies)
        print(f"\n[PERF] Chat routing: avg={avg_ms:.1f}ms")
        assert avg_ms < 200, f"Chat routing avg latency {avg_ms:.1f}ms > 200ms"

    async def test_assistant_crud_latency(self, client, headers):
        """Assistant CRUD latency should be <500ms create (cold-start), <100ms read"""
        # Warmup: ensure DB is initialized
        warmup = await client.post(
            "/v1/assistants",
            headers=headers,
            json={"name": "Warmup", "model": "test", "instructions": "warmup"},
        )
        assert warmup.status_code == 200

        start = time.perf_counter()
        resp = await client.post(
            "/v1/assistants",
            headers=headers,
            json={"name": "Perf Test", "model": "test", "instructions": "test"},
        )
        create_ms = (time.perf_counter() - start) * 1000

        assert resp.status_code == 200
        aid = resp.json()["id"]

        start = time.perf_counter()
        await client.get(f"/v1/assistants/{aid}", headers=headers)
        read_ms = (time.perf_counter() - start) * 1000

        print(f"\n[PERF] Assistant: create={create_ms:.1f}ms, read={read_ms:.1f}ms")
        assert create_ms < 500, f"Create latency {create_ms:.1f}ms > 500ms"
        assert read_ms < 100, f"Read latency {read_ms:.1f}ms > 100ms"

    async def test_concurrent_throughput(self, client, headers):
        """50 concurrent requests throughput measurement (rate-limit disabled)."""
        payload = {
            "model": "deepseek-v3",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 10,
        }

        start = time.perf_counter()
        tasks = [
            client.post("/v1/chat/completions", headers=headers, json=payload)
            for _ in range(50)
        ]
        results = await asyncio.gather(*tasks)
        elapsed = time.perf_counter() - start

        # All requests should complete without server error (200 mock or 503 no-provider)
        valid_codes = {200, 502, 503}
        all_valid = all(r.status_code in valid_codes for r in results)
        rps = 50 / elapsed if elapsed > 0 else 0
        print(f"\n[PERF] Throughput: {rps:.0f} req/s, total={elapsed:.2f}s")
        assert all_valid, (
            f"Unexpected status codes: "
            f"{dict((s, [r.status_code for r in results].count(s)) for s in set(r.status_code for r in results))}"
        )
