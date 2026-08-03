"""Tests for audio editing endpoints."""
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


class TestTTS:
    @pytest.mark.anyio
    async def test_tts_requires_auth(self, client):
        resp = await client.post("/v1/audio/speech", json={"input": "hello", "voice": "alloy"})
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_tts_success(self, client):
        mock_provider = MagicMock()
        mock_provider.text_to_speech = AsyncMock(return_value=b"fake-audio-bytes")

        with patch("moa_gateway.routes.audio._get_audio_provider", return_value=mock_provider):
            resp = await client.post(
                "/v1/audio/speech",
                json={"input": "Hello world", "voice": "alloy", "model": "tts-1"},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "audio/mpeg"
            assert resp.content == b"fake-audio-bytes"


class TestTranscription:
    @pytest.mark.anyio
    async def test_transcribe_success(self, client):
        mock_provider = MagicMock()
        mock_provider.transcribe = AsyncMock(return_value={"text": "Hello world"})

        with patch("moa_gateway.routes.audio._get_audio_provider", return_value=mock_provider):
            resp = await client.post(
                "/v1/audio/transcriptions",
                data={"model": "whisper-1"},
                files={"file": ("audio.mp3", b"fake-audio", "audio/mpeg")},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.json()["text"] == "Hello world"


class TestAudioEdit:
    @pytest.mark.anyio
    async def test_edit_denoise(self, client):
        mock_provider = MagicMock()
        mock_provider.edit_audio = AsyncMock(return_value=b"denoised-audio")

        with patch("moa_gateway.routes.audio._get_audio_provider", return_value=mock_provider):
            resp = await client.post(
                "/v1/audio/edit",
                data={"operation": "denoise", "params": "{}"},
                files={"file": ("audio.mp3", b"noisy-audio", "audio/mpeg")},
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.content == b"denoised-audio"

    @pytest.mark.anyio
    async def test_edit_invalid_params(self, client):
        resp = await client.post(
            "/v1/audio/edit",
            data={"operation": "denoise", "params": "not-json"},
            files={"file": ("audio.mp3", b"audio", "audio/mpeg")},
            headers={"Authorization": "Bearer test-key-123"},
        )
        assert resp.status_code == 400


class TestVoiceClone:
    @pytest.mark.anyio
    async def test_clone_success(self, client):
        mock_provider = MagicMock()
        mock_provider.clone_voice = AsyncMock(
            return_value={
                "voice_id": "voice-abc123",
                "name": "My Voice",
                "status": "created",
            }
        )

        with patch("moa_gateway.routes.audio._get_audio_provider", return_value=mock_provider):
            resp = await client.post(
                "/v1/audio/clone",
                data={"name": "My Voice", "description": "Test voice"},
                files=[("samples", ("s1.mp3", b"sample1", "audio/mpeg"))],
                headers={"Authorization": "Bearer test-key-123"},
            )
            assert resp.status_code == 200
            assert resp.json()["voice_id"] == "voice-abc123"
