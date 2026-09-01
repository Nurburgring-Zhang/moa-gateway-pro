"""Tests for 3D model generation endpoints."""
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
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            yield create_app()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestThreeDGeneration:
    @pytest.mark.anyio
    async def test_generate_requires_auth(self, client):
        """POST /v1/3d/generate without auth should return 401/403."""
        resp = await client.post("/v1/3d/generate", json={"prompt": "a cute robot"})
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_text_to_3d_success(self, client):
        """Text-to-3D with Tripo3D provider should return task_id."""
        mock_provider = MagicMock()
        mock_provider.text_to_3d = AsyncMock(return_value="tripo-task-001")

        with patch(
            "moa_gateway.routes.threed._get_3d_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/3d/generate",
                json={"prompt": "A medieval castle", "output_format": "glb"},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["task_id"] == "tripo-task-001"
            assert data["status"] == "processing"

    @pytest.mark.anyio
    async def test_image_to_3d_success(self, client):
        """Image-to-3D should call image_to_3d on provider."""
        mock_provider = MagicMock()
        mock_provider.image_to_3d = AsyncMock(return_value="tripo-task-002")

        with patch(
            "moa_gateway.routes.threed._get_3d_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/3d/generate",
                json={
                    "image_url": "https://example.com/chair.png",
                    "output_format": "obj",
                },
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["task_id"] == "tripo-task-002"
            assert data["status"] == "processing"

    @pytest.mark.anyio
    async def test_query_task_completed(self, client):
        """Query task that is completed should return model_url."""
        mock_provider = MagicMock()
        mock_provider.query_task = AsyncMock(
            return_value={
                "task_id": "tripo-task-001",
                "status": "completed",
                "progress": 100,
                "model_url": "https://cdn.tripo3d.ai/models/abc.glb",
                "thumbnail_url": "https://cdn.tripo3d.ai/thumbnails/abc.png",
                "error": None,
            }
        )

        with patch(
            "moa_gateway.routes.threed._get_3d_provider",
            return_value=mock_provider,
        ):
            resp = await client.get(
                "/v1/3d/tasks/tripo-task-001",
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "completed"
            assert data["progress"] == 100
            assert data["model_url"] == "https://cdn.tripo3d.ai/models/abc.glb"
            assert data["thumbnail_url"] == "https://cdn.tripo3d.ai/thumbnails/abc.png"

    @pytest.mark.anyio
    async def test_query_task_processing(self, client):
        """Query task that is still processing should show progress."""
        mock_provider = MagicMock()
        mock_provider.query_task = AsyncMock(
            return_value={
                "task_id": "tripo-task-003",
                "status": "processing",
                "progress": 45,
                "model_url": None,
                "thumbnail_url": None,
                "error": None,
            }
        )

        with patch(
            "moa_gateway.routes.threed._get_3d_provider",
            return_value=mock_provider,
        ):
            resp = await client.get(
                "/v1/3d/tasks/tripo-task-003?model=tripo3d",
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "processing"
            assert data["progress"] == 45
            assert data["model_url"] is None
