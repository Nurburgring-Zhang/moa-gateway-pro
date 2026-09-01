"""Multimodal wiring regression tests (audit wiring-defect fixes).

Covers the fixed wiring paths:
- /v1/images/generations reads real env keys per platform (P0 empty-key bug)
- /v1/audio/music (POST + GET task query) with MiniMax / Tiangong providers
- /v1/audio/transcriptions + /v1/audio/speech provider parameter
  (auto/openai/dashscope/iflytek) wired to audio_asr/audio_tts providers
- /v1/video/generations platform parameter (kling/runway) wiring
  KlingVideoProvider

Behavior matrix per endpoint:
- real key configured        -> real provider class is used (no X-MOA-Mock)
- no key + mock.mode=explicit -> 200 + X-MOA-Mock header (labeled synthetic)
- no key + mock.mode=disabled -> 503 (fail fast, zero simulated output)
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

API_KEY = "test-mm-wiring-key-123"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
MOCK_HEADER = "X-MOA-Mock"

# Every multimodal env var the wired routes read — cleared per test so the
# matrix is deterministic regardless of the host environment.
MM_ENV_VARS = [
    "ZHIPU_API_KEY", "ZHIPU_API_BASE", "COGVIEW_API_BASE",
    "OPENAI_API_KEY", "OPENAI_API_BASE",
    "WANX_API_KEY", "WANX_API_BASE",
    "DASHSCOPE_API_KEY", "DASHSCOPE_API_BASE",
    "DASHSCOPE_ASR_API_KEY", "DASHSCOPE_TTS_API_KEY",
    "MINIMAX_API_KEY", "MINIMAX_API_BASE",
    "TIANGONG_API_KEY", "TIANGONG_API_BASE",
    "KLING_API_KEY", "KLING_API_BASE",
    "RUNWAY_API_KEY", "RUNWAY_API_BASE",
    "WHISPER_API_KEY", "WHISPER_API_BASE",
    "IFLYTEK_API_KEY", "IFLYTEK_API_BASE",
    "ELEVENLABS_API_KEY",
    "TRIPO3D_API_KEY", "TRIPO3D_API_BASE",
    "MESHY_API_KEY", "MESHY_API_BASE",
    "SD_API_KEY", "SD_API_BASE",
]


@pytest.fixture(autouse=True)
def clean_mm_env(monkeypatch):
    """Strip all multimodal keys so each test controls the matrix itself."""
    for var in MM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
async def build_client(monkeypatch):
    """Build a TestClient-style async client for a given mock.mode."""
    clients: list[AsyncClient] = []

    def _make(mock_mode: str = "explicit") -> AsyncClient:
        from moa_gateway.config import Settings

        test_settings = Settings(
            auth={
                "admin_username": "admin",
                "admin_password": "TestAdm!n2024Str0ng",
                "jwt_secret": "mm-wiring-secret-must-be-at-least-32-characters-xyz",
                "jwt_expire_minutes": 60,
                "gateway_api_keys": [API_KEY],
            },
            mock={"mode": mock_mode},
            discovery={"enabled": False},
            benchmark={"enabled": False},
            optimizer={"enabled": False},
            health={"enabled": False},
        )
        # Patch the settings global (not get_settings) so every module — even
        # ones that bound get_settings at import time — sees the test settings.
        monkeypatch.setattr("moa_gateway.config._settings", test_settings)

        from moa_gateway.server import create_app

        app = create_app()
        client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        clients.append(client)
        return client

    yield _make

    for c in clients:
        await c.aclose()


# ============================================================
# 1. OpenAPI visibility / route registration
# ============================================================
class TestRouteRegistration:
    @pytest.mark.anyio
    async def test_openapi_lists_new_endpoints(self, build_client):
        client = build_client()
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json()["paths"]
        assert "/v1/audio/music" in paths
        assert "post" in paths["/v1/audio/music"]
        assert "/v1/audio/music/tasks/{task_id}" in paths
        assert "get" in paths["/v1/audio/music/tasks/{task_id}"]
        assert "/v1/video/generations" in paths
        assert "post" in paths["/v1/video/generations"]
        assert "/v1/video/generations/tasks/{task_id}" in paths
        assert "get" in paths["/v1/video/generations/tasks/{task_id}"]
        # existing endpoints still registered
        for p in ("/v1/images/generations", "/v1/audio/speech", "/v1/audio/transcriptions"):
            assert p in paths

    @pytest.mark.anyio
    async def test_music_requires_auth(self, build_client):
        client = build_client()
        resp = await client.post("/v1/audio/music", json={"prompt": "a song"})
        assert resp.status_code in (401, 403)

    @pytest.mark.anyio
    async def test_video_generations_requires_auth(self, build_client):
        client = build_client()
        resp = await client.post("/v1/video/generations", json={"prompt": "sunset"})
        assert resp.status_code in (401, 403)


# ============================================================
# 2. Image generation — P0 empty-key bug fix
# ============================================================
class TestImageGenerationWiring:
    @pytest.mark.anyio
    async def test_nokey_explicit_returns_labeled_mock(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/images/generations",
            json={"prompt": "a cat", "model": "cogview"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"
        assert resp.json()["data"]

    @pytest.mark.anyio
    async def test_nokey_disabled_returns_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post(
            "/v1/images/generations",
            json={"prompt": "a cat", "model": "cogview"},
            headers=AUTH,
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_zhipu_key_uses_real_cogview_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "real-zhipu-key")
        gen = AsyncMock(return_value=["https://real.cogview.example/img.png"])
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.image_generation_provider.CogViewImageProvider.generate_image",
            gen,
        ):
            resp = await client.post(
                "/v1/images/generations",
                json={"prompt": "a cat", "model": "cogview"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers  # real provider, not mock
        assert resp.json()["data"][0]["url"] == "https://real.cogview.example/img.png"
        gen.assert_awaited_once()

    @pytest.mark.anyio
    async def test_dashscope_key_uses_real_wanx_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-dashscope-key")
        gen = AsyncMock(return_value=["https://real.wanx.example/img.png"])
        client = build_client("disabled")  # disabled mode: must still work with a real key
        with patch(
            "moa_gateway.providers.image_generation_provider.WanxImageProvider.generate_image",
            gen,
        ):
            resp = await client.post(
                "/v1/images/generations",
                json={"prompt": "a mountain", "model": "wanx"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        gen.assert_awaited_once()

    @pytest.mark.anyio
    async def test_openai_key_uses_real_dalle_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
        gen = AsyncMock(return_value=["https://real.dalle.example/img.png"])
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.image_generation_provider.DallECompatImageProvider.generate_image",
            gen,
        ):
            resp = await client.post(
                "/v1/images/generations",
                json={"prompt": "a dog", "model": "openai"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        gen.assert_awaited_once()

    @pytest.mark.anyio
    async def test_auto_selects_platform_with_key(self, build_client, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "real-zhipu-key")
        gen = AsyncMock(return_value=["https://real.cogview.example/auto.png"])
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.image_generation_provider.CogViewImageProvider.generate_image",
            gen,
        ):
            resp = await client.post(
                "/v1/images/generations",
                json={"prompt": "auto pick", "model": "auto"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        gen.assert_awaited_once()

    @pytest.mark.anyio
    async def test_placeholder_key_treated_as_absent(self, build_client, monkeypatch):
        monkeypatch.setenv("ZHIPU_API_KEY", "your-zhipu-key")  # placeholder
        client = build_client("explicit")
        resp = await client.post(
            "/v1/images/generations",
            json={"prompt": "a cat", "model": "cogview"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"


# ============================================================
# 3. Music generation — new route
# ============================================================
class TestMusicWiring:
    @pytest.mark.anyio
    async def test_nokey_explicit_returns_labeled_mock(self, build_client):
        client = build_client("explicit")
        resp = await client.post("/v1/audio/music", json={"prompt": "lofi beat"}, headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"
        assert resp.json()["task_id"]
        assert resp.json()["status"] == "processing"

    @pytest.mark.anyio
    async def test_nokey_disabled_returns_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post("/v1/audio/music", json={"prompt": "lofi beat"}, headers=AUTH)
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_minimax_key_uses_real_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "real-minimax-key")
        create = AsyncMock(return_value="mm-task-001")
        client = build_client("disabled")  # real key must work even with mock disabled
        with patch(
            "moa_gateway.providers.music_generation_provider.MiniMaxMusicProvider.create_music_task",
            create,
        ):
            resp = await client.post("/v1/audio/music", json={"prompt": "jazz"}, headers=AUTH)
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["task_id"] == "mm-task-001"
        create.assert_awaited_once()

    @pytest.mark.anyio
    async def test_tiangong_key_uses_real_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("TIANGONG_API_KEY", "real-tiangong-key")
        create = AsyncMock(return_value="tg-task-002")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.music_generation_provider.TiangongMusicProvider.create_music_task",
            create,
        ):
            resp = await client.post(
                "/v1/audio/music",
                json={"prompt": "rock", "provider": "tiangong"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["task_id"] == "tg-task-002"

    @pytest.mark.anyio
    async def test_mock_task_query_roundtrip(self, build_client):
        client = build_client("explicit")
        created = await client.post("/v1/audio/music", json={"prompt": "roundtrip"}, headers=AUTH)
        assert created.status_code == 200
        task_id = created.json()["task_id"]
        resp = await client.get(f"/v1/audio/music/tasks/{task_id}", headers=AUTH)
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"
        assert resp.json()["status"] == "completed"
        assert resp.json()["music_url"]

    @pytest.mark.anyio
    async def test_mock_task_query_unknown_id_404(self, build_client):
        """Audit F22: mock providers must not fabricate unknown tasks."""
        client = build_client("explicit")
        resp = await client.get("/v1/audio/music/tasks/never-created-999", headers=AUTH)
        assert resp.status_code == 404

    @pytest.mark.anyio
    async def test_real_task_query(self, build_client, monkeypatch):
        monkeypatch.setenv("MINIMAX_API_KEY", "real-minimax-key")
        query = AsyncMock(return_value={"status": "Success", "music_url": "https://x/m.mp3", "error": None})
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.music_generation_provider.MiniMaxMusicProvider.query_music_task",
            query,
        ):
            resp = await client.get(
                "/v1/audio/music/tasks/mm-task-777",
                params={"provider": "minimax"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"  # normalized from "Success"
        assert body["music_url"] == "https://x/m.mp3"

    @pytest.mark.anyio
    async def test_validation_rejects_empty_prompt_and_bad_provider(self, build_client):
        client = build_client("explicit")
        resp = await client.post("/v1/audio/music", json={"prompt": ""}, headers=AUTH)
        assert resp.status_code == 422
        resp = await client.post(
            "/v1/audio/music", json={"prompt": "x", "provider": "spotify"}, headers=AUTH
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_task_query_invalid_provider_400(self, build_client):
        client = build_client("explicit")
        resp = await client.get(
            "/v1/audio/music/tasks/any", params={"provider": "bogus"}, headers=AUTH
        )
        assert resp.status_code == 400


# ============================================================
# 4. ASR/TTS provider parameter wiring
# ============================================================
class TestTTSWiring:
    @pytest.mark.anyio
    async def test_nokey_auto_explicit_returns_labeled_mock(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/audio/speech", json={"input": "hello", "voice": "alloy"}, headers=AUTH
        )
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"
        assert resp.headers["content-type"].startswith("audio/")

    @pytest.mark.anyio
    async def test_nokey_auto_disabled_returns_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post(
            "/v1/audio/speech", json={"input": "hello", "voice": "alloy"}, headers=AUTH
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_explicit_openai_nokey_explicit_labeled_mock(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/audio/speech",
            json={"input": "hello", "voice": "alloy", "provider": "openai"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"

    @pytest.mark.anyio
    async def test_explicit_openai_nokey_disabled_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post(
            "/v1/audio/speech",
            json={"input": "hello", "voice": "alloy", "provider": "openai"},
            headers=AUTH,
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_openai_key_uses_real_tts_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
        synth = AsyncMock(return_value=b"REAL-TTS-BYTES")
        client = build_client("disabled")
        with patch(
            "moa_gateway.providers.audio_tts_provider.OpenAITTSProvider.synthesize", synth
        ):
            resp = await client.post(
                "/v1/audio/speech",
                json={"input": "hello", "voice": "alloy", "provider": "openai"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.content == b"REAL-TTS-BYTES"
        synth.assert_awaited_once()

    @pytest.mark.anyio
    async def test_dashscope_key_uses_real_sambert_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-dashscope-key")
        synth = AsyncMock(return_value=b"SAMBERT-BYTES")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.audio_tts_provider.QwenTTSProvider.synthesize", synth
        ):
            resp = await client.post(
                "/v1/audio/speech",
                json={"input": "你好", "provider": "dashscope"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.content == b"SAMBERT-BYTES"

    @pytest.mark.anyio
    async def test_iflytek_key_uses_real_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("IFLYTEK_API_KEY", "real-iflytek-key")
        synth = AsyncMock(return_value=b"IFLYTEK-BYTES")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.audio_tts_provider.IFlytekTTSProvider.synthesize", synth
        ):
            resp = await client.post(
                "/v1/audio/speech",
                json={"input": "你好", "provider": "iflytek"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.content == b"IFLYTEK-BYTES"

    @pytest.mark.anyio
    async def test_auto_picks_openai_when_key_present(self, build_client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
        synth = AsyncMock(return_value=b"AUTO-OPENAI-BYTES")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.audio_tts_provider.OpenAITTSProvider.synthesize", synth
        ):
            resp = await client.post(
                "/v1/audio/speech", json={"input": "auto tts"}, headers=AUTH
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.content == b"AUTO-OPENAI-BYTES"

    @pytest.mark.anyio
    async def test_invalid_provider_rejected_422(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/audio/speech",
            json={"input": "hello", "provider": "elevenlabs"},
            headers=AUTH,
        )
        assert resp.status_code == 422


class TestASRWiring:
    @pytest.mark.anyio
    async def test_nokey_auto_explicit_returns_labeled_mock(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/audio/transcriptions",
            data={"model": "whisper-1"},
            files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
            headers=AUTH,
        )
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"
        assert resp.json()["text"]

    @pytest.mark.anyio
    async def test_nokey_auto_disabled_returns_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post(
            "/v1/audio/transcriptions",
            data={"model": "whisper-1"},
            files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
            headers=AUTH,
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_openai_key_uses_real_whisper_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "real-openai-key")
        transcribe = AsyncMock(return_value="real whisper text")
        client = build_client("disabled")
        with patch(
            "moa_gateway.providers.audio_asr_provider.OpenAIASRProvider.transcribe", transcribe
        ):
            resp = await client.post(
                "/v1/audio/transcriptions",
                data={"provider": "openai"},
                files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["text"] == "real whisper text"
        transcribe.assert_awaited_once()

    @pytest.mark.anyio
    async def test_whisper_key_triggers_auto_selection(self, build_client, monkeypatch):
        monkeypatch.setenv("WHISPER_API_KEY", "real-whisper-key")
        transcribe = AsyncMock(return_value="auto whisper text")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.audio_asr_provider.OpenAIASRProvider.transcribe", transcribe
        ):
            resp = await client.post(
                "/v1/audio/transcriptions",
                data={},
                files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["text"] == "auto whisper text"

    @pytest.mark.anyio
    async def test_dashscope_key_uses_real_paraformer_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "real-dashscope-key")
        transcribe = AsyncMock(return_value="paraformer text")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.audio_asr_provider.QwenASRProvider.transcribe", transcribe
        ):
            resp = await client.post(
                "/v1/audio/transcriptions",
                data={"provider": "dashscope"},
                files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["text"] == "paraformer text"

    @pytest.mark.anyio
    async def test_iflytek_key_uses_real_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("IFLYTEK_API_KEY", "real-iflytek-key")
        transcribe = AsyncMock(return_value="iflytek text")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.audio_asr_provider.IFlytekASRProvider.transcribe", transcribe
        ):
            resp = await client.post(
                "/v1/audio/transcriptions",
                data={"provider": "iflytek"},
                files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["text"] == "iflytek text"

    @pytest.mark.anyio
    async def test_explicit_nokey_disabled_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post(
            "/v1/audio/transcriptions",
            data={"provider": "iflytek"},
            files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
            headers=AUTH,
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_unknown_provider_400(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/audio/transcriptions",
            data={"provider": "bogus"},
            files={"file": ("audio.mp3", b"fake-audio-bytes", "audio/mpeg")},
            headers=AUTH,
        )
        assert resp.status_code == 400


# ============================================================
# 5. Video generations — Kling wiring
# ============================================================
class TestVideoGenerationsWiring:
    @pytest.mark.anyio
    async def test_nokey_explicit_returns_labeled_mock(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/video/generations", json={"prompt": "sunset"}, headers=AUTH
        )
        assert resp.status_code == 200
        assert resp.headers.get(MOCK_HEADER) == "true"
        assert resp.json()["task_id"]

    @pytest.mark.anyio
    async def test_nokey_disabled_returns_503(self, build_client):
        client = build_client("disabled")
        resp = await client.post(
            "/v1/video/generations", json={"prompt": "sunset"}, headers=AUTH
        )
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_kling_key_uses_real_kling_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("KLING_API_KEY", "real-kling-key")
        create = AsyncMock(return_value="kling-task-001")
        client = build_client("disabled")  # real key must work even with mock disabled
        with patch(
            "moa_gateway.providers.video_generation_provider.KlingVideoProvider.create_video_task",
            create,
        ):
            resp = await client.post(
                "/v1/video/generations",
                json={"prompt": "a rocket launch", "platform": "kling"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["task_id"] == "kling-task-001"
        create.assert_awaited_once()

    @pytest.mark.anyio
    async def test_runway_key_uses_real_runway_provider(self, build_client, monkeypatch):
        monkeypatch.setenv("RUNWAY_API_KEY", "real-runway-key")
        t2v = AsyncMock(return_value="runway-task-002")
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.video_edit_provider.RunwayVideoProvider.text_to_video", t2v
        ):
            resp = await client.post(
                "/v1/video/generations",
                json={"prompt": "waves", "platform": "runway"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        assert MOCK_HEADER not in resp.headers
        assert resp.json()["task_id"] == "runway-task-002"

    @pytest.mark.anyio
    async def test_kling_task_query_normalizes_status(self, build_client, monkeypatch):
        monkeypatch.setenv("KLING_API_KEY", "real-kling-key")
        query = AsyncMock(
            return_value={
                "status": "succeed",
                "video_url": "https://kling.example/v.mp4",
                "error": None,
            }
        )
        client = build_client("explicit")
        with patch(
            "moa_gateway.providers.video_generation_provider.KlingVideoProvider.query_video_task",
            query,
        ):
            resp = await client.get(
                "/v1/video/generations/tasks/kling-task-001",
                params={"platform": "kling"},
                headers=AUTH,
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"  # normalized from kling "succeed"
        assert body["output_url"] == "https://kling.example/v.mp4"

    @pytest.mark.anyio
    async def test_invalid_platform_rejected_422(self, build_client):
        client = build_client("explicit")
        resp = await client.post(
            "/v1/video/generations",
            json={"prompt": "x", "platform": "pika"},
            headers=AUTH,
        )
        assert resp.status_code == 422
