"""Tests for embodied AI endpoints."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


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


class TestEmbodiedPlan:
    @pytest.mark.anyio
    async def test_plan_requires_auth(self, client):
        resp = await client.post(
            "/v1/embodied/plan",
            json={"observation": {"scene": "table"}, "goal": "pick up cup"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_plan_actions_success(self, client):
        mock_provider = MagicMock()
        mock_provider.plan_actions = AsyncMock(
            return_value={
                "actions": [
                    {"step": 1, "action": "move_to", "target": "table", "params": {"speed": "normal"}, "reasoning": "Approach table"}
                ],
                "confidence": 0.9,
                "estimated_time_seconds": 15.0,
                "risks": [],
            }
        )

        with patch(
            "moa_gateway.routes.embodied._get_embodied_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/embodied/plan",
                json={"observation": {"scene": "kitchen"}, "goal": "pick up the red cup"},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["actions"]) == 1
            assert data["confidence"] == 0.9
            assert data["actions"][0]["action"] == "move_to"

    @pytest.mark.anyio
    async def test_plan_validates_goal(self, client):
        """Empty goal should return 422 validation error."""
        with patch(
            "moa_gateway.routes.embodied._get_embodied_provider",
            return_value=MagicMock(),
        ):
            resp = await client.post(
                "/v1/embodied/plan",
                json={"observation": {"scene": "room"}, "goal": ""},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 422


class TestEmbodiedExecute:
    @pytest.mark.anyio
    async def test_execute_action_simulated(self, client):
        mock_provider = MagicMock()
        mock_provider.execute_action = AsyncMock(
            return_value={
                "success": True,
                "result": "Simulated: pick on cup",
                "new_state": {"position": "updated", "gripper": "closed"},
                "simulated": True,
            }
        )

        with patch(
            "moa_gateway.routes.embodied._get_embodied_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/embodied/execute",
                json={"action": {"action": "pick", "target": "cup", "params": {"gripper": "right"}}},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["success"] is True
            assert data["simulated"] is True


class TestEmbodiedStatus:
    @pytest.mark.anyio
    async def test_get_status(self, client):
        mock_provider = MagicMock()
        mock_provider.get_status = AsyncMock(
            return_value={
                "robot_id": "robot-1",
                "state": "idle",
                "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                "battery": 95,
                "sensors": {"camera": "active"},
                "last_action": None,
                "mode": "simulation",
            }
        )

        with patch(
            "moa_gateway.routes.embodied._get_embodied_provider",
            return_value=mock_provider,
        ):
            resp = await client.get(
                "/v1/embodied/status?robot_id=robot-1",
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["robot_id"] == "robot-1"
            assert data["state"] == "idle"
            assert data["battery"] == 95
