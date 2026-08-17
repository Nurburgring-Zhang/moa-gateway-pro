"""Tests for image editing endpoints."""
from __future__ import annotations

import struct
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app():
    """Create test app with mocked settings."""
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


def _fake_image() -> bytes:
    """Create minimal valid PNG bytes."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    raw = b"\x00\x00\x00\x00"
    idat_data = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + idat_data) & 0xFFFFFFFF
    idat = struct.pack(">I", len(idat_data)) + b"IDAT" + idat_data + struct.pack(">I", idat_crc)
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return sig + ihdr + idat + iend


class TestImageEdit:
    @pytest.mark.anyio
    async def test_edit_requires_auth(self, client):
        resp = await client.post(
            "/v1/images/edits",
            data={"prompt": "test"},
            files={"image": ("img.png", b"fake", "image/png")},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_edit_requires_image(self, client):
        resp = await client.post(
            "/v1/images/edits",
            data={"prompt": "make it blue"},
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_edit_success(self, client):
        fake_img = _fake_image()
        mock_provider = MagicMock()
        mock_provider.edit_image = AsyncMock(
            return_value=["https://cdn.example.com/edited.png"]
        )

        with patch(
            "moa_gateway.routes.image_edit._get_image_edit_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/images/edits",
                data={
                    "prompt": "add a hat",
                    "model": "openai",
                    "n": "1",
                    "size": "1024x1024",
                },
                files={"image": ("test.png", fake_img, "image/png")},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["data"][0]["url"] == "https://cdn.example.com/edited.png"
            assert data["created"] > 0

    @pytest.mark.anyio
    async def test_variation_success(self, client):
        fake_img = _fake_image()
        mock_provider = MagicMock()
        mock_provider.create_variation = AsyncMock(
            return_value=["https://cdn.example.com/var1.png"]
        )

        with patch(
            "moa_gateway.routes.image_edit._get_image_edit_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/images/variations",
                data={"model": "openai", "n": "1", "size": "1024x1024"},
                files={"image": ("test.png", fake_img, "image/png")},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["data"]) == 1

    @pytest.mark.anyio
    async def test_edit_with_mask(self, client):
        fake_img = _fake_image()
        mock_provider = MagicMock()
        mock_provider.edit_image = AsyncMock(
            return_value=["https://cdn.example.com/inpaint.png"]
        )

        with patch(
            "moa_gateway.routes.image_edit._get_image_edit_provider",
            return_value=mock_provider,
        ):
            resp = await client.post(
                "/v1/images/edits",
                data={"prompt": "fill with flowers", "model": "sd"},
                files={
                    "image": ("test.png", fake_img, "image/png"),
                    "mask": ("mask.png", fake_img, "image/png"),
                },
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            mock_provider.edit_image.assert_called_once()
            # Verify mask was passed
            call_kwargs = mock_provider.edit_image.call_args
            assert call_kwargs.kwargs.get("mask") is not None or (
                len(call_kwargs.args) > 2 and call_kwargs.args[2] is not None
            )
