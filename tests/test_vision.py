"""Tests for Vision and Image Generation endpoints."""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport


@pytest.fixture
async def app():
    """Create test app with isolated config."""
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestPass123!",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["test-key-123"],
        }
    )
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            application = create_app()
            yield application


@pytest.fixture
async def client(app):
    """Create async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestVisionAnalyze:
    """Tests for /v1/vision/analyze endpoint."""

    @pytest.mark.anyio
    async def test_vision_analyze_requires_auth(self, client):
        """Vision endpoint requires API key."""
        resp = await client.post("/v1/vision/analyze", json={
            "images": [{"type": "image_url", "url": "https://example.com/img.jpg"}],
            "prompt": "Describe this"
        })
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_vision_analyze_validates_input(self, client):
        """Empty images list rejected."""
        resp = await client.post(
            "/v1/vision/analyze",
            json={"images": [], "prompt": "test"},
            headers={"Authorization": "Bearer test-key-123"}
        )
        assert resp.status_code == 422  # Validation error

    @pytest.mark.anyio
    async def test_vision_analyze_success(self, client):
        """Successful vision analysis with mocked pool."""
        mock_resp = MagicMock()
        mock_resp.content = "A cat sitting on a table"
        mock_resp.model = "gpt-4o"
        mock_resp.prompt_tokens = 100
        mock_resp.completion_tokens = 50
        mock_resp.total_tokens = 150

        with patch("moa_gateway.model_pool.get_model_pool") as mock_get_pool:
            mock_pool = MagicMock()
            mock_pool.call = AsyncMock(return_value=mock_resp)
            mock_pool.endpoints = {"gpt-4o": MagicMock()}
            mock_get_pool.return_value = mock_pool

            resp = await client.post(
                "/v1/vision/analyze",
                json={
                    "images": [{"type": "image_url", "url": "https://example.com/cat.jpg"}],
                    "prompt": "What is in this image?",
                    "model": "gpt-4o"
                },
                headers={"Authorization": "Bearer test-key-123"}
            )

            assert resp.status_code == 200
            data = resp.json()
            assert data["description"] == "A cat sitting on a table"
            assert data["model"] == "gpt-4o"
            assert "usage" in data
            assert data["usage"]["total_tokens"] == 150


class TestImageGeneration:
    """Tests for /v1/images/generations endpoint."""

    @pytest.mark.anyio
    async def test_image_gen_requires_auth(self, client):
        """Image generation requires API key."""
        resp = await client.post("/v1/images/generations", json={
            "prompt": "A beautiful sunset"
        })
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_image_gen_validates_prompt(self, client):
        """Empty prompt rejected."""
        resp = await client.post(
            "/v1/images/generations",
            json={"prompt": ""},
            headers={"Authorization": "Bearer test-key-123"}
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_image_gen_success(self, client):
        """Successful image generation with mocked provider."""
        mock_provider = MagicMock()
        mock_provider.generate_image = AsyncMock(
            return_value=["https://cdn.example.com/img1.png"]
        )

        with patch(
            "moa_gateway.providers.build_multimodal_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/images/generations",
                json={
                    "prompt": "A beautiful sunset over mountains",
                    "model": "wanx",
                    "n": 1,
                    "size": "1024x1024"
                },
                headers={"Authorization": "Bearer test-key-123"}
            )

            assert resp.status_code == 200
            data = resp.json()
            assert "data" in data
            assert len(data["data"]) == 1
            assert "url" in data["data"][0]
            assert data["created"] > 0
