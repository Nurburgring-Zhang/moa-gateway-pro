"""Tests for Round 6 strengthening: mock fallback providers + cache-key strategy isolation."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

os.environ.setdefault("MOA_JWT_SECRET", "test-secret-key-minimum-32-characters-long!")
os.environ.setdefault("MOA_ADMIN_PASSWORD", "TestPass#2024")
os.environ.setdefault("MOA_GATEWAY_KEY", "r6-key-001")

from httpx import ASGITransport, AsyncClient  # noqa: E402

AUTH = {"Authorization": "Bearer r6-key-001"}


@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass#2024",
            "jwt_secret": "test-secret-key-minimum-32-characters-long!",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["r6-key-001"],
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
# R6-1: Audio TTS/ASR mock fallback returns 200
# ====================================================================
class TestAudioMockFallback:
    @pytest.mark.anyio
    async def test_tts_returns_200_mock_audio(self, client, monkeypatch):
        """Without ELEVENLABS/OPENAI key + mock.mode=explicit → 200 mock audio."""
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        resp = await client.post(
            "/v1/audio/speech",
            headers=AUTH,
            json={"model": "tts-1", "input": "hello", "voice": "alloy"},
        )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("audio/")


# ====================================================================
# R6-2/3: World model + embodied mock fallback returns 200
# ====================================================================
class TestWorldEmbodiedMockFallback:
    @pytest.mark.anyio
    async def test_world_simulate_returns_200_mock(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        resp = await client.post(
            "/v1/world/simulate",
            headers=AUTH,
            json={"scenario": "ball drop", "steps": 3},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "states" in data or "mock" in data

    @pytest.mark.anyio
    async def test_embodied_plan_returns_200_mock(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        resp = await client.post(
            "/v1/embodied/plan",
            headers=AUTH,
            json={"observation": {"description": "room"}, "goal": "go to table"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "actions" in data or "mock" in data

    @pytest.mark.anyio
    async def test_embodied_status_returns_200_mock(self, client, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        resp = await client.get("/v1/embodied/status", headers=AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("battery") == 85  # int per schema


# ====================================================================
# R6-4: 3D/Video mock fallback (generate + task query) returns 200
# ====================================================================
class Test3DVideoMockFallback:
    @pytest.mark.anyio
    async def test_3d_generate_returns_200_mock(self, client, monkeypatch):
        monkeypatch.delenv("TRIPO3D_API_KEY", raising=False)
        monkeypatch.delenv("MESHY_API_KEY", raising=False)
        resp = await client.post(
            "/v1/3d/generate",
            headers=AUTH,
            json={"model": "auto", "prompt": "cube"},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()

    @pytest.mark.anyio
    async def test_video_generate_returns_200_mock(self, client, monkeypatch):
        monkeypatch.delenv("KLING_API_KEY", raising=False)
        monkeypatch.delenv("RUNWAY_API_KEY", raising=False)
        resp = await client.post(
            "/v1/video/generate",
            headers=AUTH,
            json={"model": "auto", "prompt": "sunset"},
        )
        assert resp.status_code == 200
        assert "task_id" in resp.json()


# ====================================================================
# R6-5: Image generation mock fallback returns 200
# ====================================================================
class TestImageMockFallback:
    @pytest.mark.anyio
    async def test_image_generate_returns_200_mock(self, client, monkeypatch):
        resp = await client.post(
            "/v1/images/generations",
            headers=AUTH,
            json={"model": "dall-e-3", "prompt": "cat", "n": 1, "size": "256x256"},
        )
        assert resp.status_code == 200


# ====================================================================
# R6-7: Cache key isolates strategy (single ≠ parallel)
# ====================================================================
class TestCacheKeyStrategyIsolation:
    def test_compute_key_differs_by_strategy(self):
        from moa_gateway.cache.exact import ExactMatchCache

        msgs = [{"role": "user", "content": "same input"}]
        k_single = ExactMatchCache.compute_key(msgs, "balanced", strategy="single")
        k_parallel = ExactMatchCache.compute_key(msgs, "balanced", strategy="parallel")
        k_none = ExactMatchCache.compute_key(msgs, "balanced")
        assert k_single != k_parallel, "single and parallel must have different cache keys"
        assert k_single != k_none, "explicit strategy must differ from unset strategy"

    def test_compute_key_differs_by_preset(self):
        from moa_gateway.cache.exact import ExactMatchCache

        msgs = [{"role": "user", "content": "same input"}]
        k_fast = ExactMatchCache.compute_key(msgs, "balanced", preset="fast")
        k_bal = ExactMatchCache.compute_key(msgs, "balanced", preset="balanced")
        assert k_fast != k_bal


# ====================================================================
# R6-6: GDPR export returns real user data
# ====================================================================
class TestGDPRExportRealData:
    def test_export_with_db_returns_profile_keys(self, storage_instance):
        """GDPR export with a real db_conn returns structured data sections."""
        import asyncio

        from moa_gateway.compliance.gdpr import GDPRManager

        mgr = GDPRManager()
        # storage_instance.conn() yields a sqlite connection
        with storage_instance.conn() as c:
            result = asyncio.run(mgr.export_user_data("admin", db_conn=c))
        assert result["user_id"] == "admin"
        assert result["format"] == "json"
        data = result["data"]
        # All four sections must be present (real query, not stub)
        assert "profile" in data
        assert "api_keys" in data
        assert "usage_history" in data
        assert "preferences" in data
        # admin user exists (bootstrapped) so profile should have fields
        assert isinstance(data["profile"], dict)

    def test_export_without_db_returns_skeleton(self):
        import asyncio

        from moa_gateway.compliance.gdpr import GDPRManager

        mgr = GDPRManager()
        result = asyncio.run(mgr.export_user_data("nobody", db_conn=None))
        assert "note" in result["data"]  # best-effort skeleton marker
