"""Tests for video generation/editing endpoints."""
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


class TestVideoGenerate:
    @pytest.mark.anyio
    async def test_generate_requires_auth(self, client):
        resp = await client.post("/v1/video/generate", json={"prompt": "test"})
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_text_to_video(self, client):
        mock_provider = MagicMock()
        mock_provider.text_to_video = AsyncMock(return_value="task-123")

        with patch(
            "moa_gateway.routes.video._get_video_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/video/generate",
                json={"prompt": "A sunset over mountains", "duration": 5},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.json()["task_id"] == "task-123"

    @pytest.mark.anyio
    async def test_image_to_video(self, client):
        mock_provider = MagicMock()
        mock_provider.image_to_video = AsyncMock(return_value="task-456")

        with patch(
            "moa_gateway.routes.video._get_video_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/video/generate",
                json={
                    "prompt": "Animate this scene",
                    "image_url": "https://example.com/img.jpg",
                },
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.json()["task_id"] == "task-456"


class TestVideoEdit:
    @pytest.mark.anyio
    async def test_edit_video(self, client):
        mock_provider = MagicMock()
        mock_provider.edit_video = AsyncMock(return_value="task-789")

        with patch(
            "moa_gateway.routes.video._get_video_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/video/edit",
                json={
                    "video_url": "https://example.com/video.mp4",
                    "prompt": "Make it cinematic",
                },
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.json()["task_id"] == "task-789"


class TestVideoTaskQuery:
    @pytest.mark.anyio
    async def test_query_task(self, client):
        mock_provider = MagicMock()
        mock_provider.query_task = AsyncMock(
            return_value={
                "task_id": "task-123",
                "status": "completed",
                "progress": 100,
                "output_url": "https://cdn.example.com/video.mp4",
                "error": None,
            }
        )

        with patch(
            "moa_gateway.routes.video._get_video_provider",
            return_value=mock_provider,
        ):
            resp = await client.get(
                "/v1/video/tasks/task-123",
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "completed"
            assert data["output_url"] == "https://cdn.example.com/video.mp4"
