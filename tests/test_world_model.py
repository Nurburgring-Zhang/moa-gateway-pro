"""Tests for world model endpoints."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app():
    from moa_gateway.config import Settings

    test_settings = Settings()
    test_settings.auth.gateway_api_keys = ["test-key-123"]
    test_settings.auth.admin_password = "TestPass123!"
    test_settings.auth.jwt_secret = "test-secret-long-enough-for-hs256-signing-key-xyz"

    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        from moa_gateway.server import create_app

        yield create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestWorldModel:
    @pytest.mark.anyio
    async def test_simulate_requires_auth(self, client):
        resp = await client.post(
            "/v1/world/simulate", json={"scenario": "ball drops"}
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_simulate_success(self, client):
        mock_provider = MagicMock()
        mock_provider.simulate = AsyncMock(
            return_value={
                "states": [
                    {"step": 1, "description": "Ball begins to fall"}
                ],
                "summary": "Ball falls due to gravity",
                "confidence": 0.9,
            }
        )

        with patch(
            "moa_gateway.routes.world_model._get_world_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/world/simulate",
                json={"scenario": "A ball is dropped from 10m height", "steps": 3},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["states"]) == 1
            assert data["confidence"] == 0.9

    @pytest.mark.anyio
    async def test_predict_next_state(self, client):
        mock_provider = MagicMock()
        mock_provider.predict_next_state = AsyncMock(
            return_value={
                "next_state": {"description": "Ball on the ground"},
                "probability": 0.95,
                "reasoning": "Gravity pulls ball down",
                "side_effects": ["bounce"],
            }
        )

        with patch(
            "moa_gateway.routes.world_model._get_world_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/world/predict",
                json={
                    "current_state": {"description": "Ball in air at 5m"},
                    "action": "wait 1 second",
                },
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["probability"] == 0.95
            assert "bounce" in data["side_effects"]

    @pytest.mark.anyio
    async def test_understand_scene_with_description(self, client):
        mock_provider = MagicMock()
        mock_provider.understand_scene = AsyncMock(
            return_value={
                "entities": [{"name": "table", "type": "furniture"}],
                "relationships": [],
                "physical_properties": {"gravity": "normal"},
                "affordances": ["place objects on table"],
                "environment": {"type": "indoor"},
            }
        )

        with patch(
            "moa_gateway.routes.world_model._get_world_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/world/scene",
                json={"description": "A wooden table in a room"},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["entities"]) == 1
            assert data["entities"][0]["name"] == "table"

    @pytest.mark.anyio
    async def test_scene_requires_input(self, client):
        """Scene endpoint requires at least image_url or description."""
        resp = await client.post(
            "/v1/world/scene",
            json={},
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.status_code == 400
        assert "required" in resp.json()["detail"].lower()
