"""E2E Integration Tests - 全能力端到端集成测试.

验证完整请求链路: 认证 → 路由 → Provider → 响应格式化 → 错误处理
覆盖所有11个modality的端到端测试。

关键原则:
- 使用真实的app实例(通过create_app()创建)
- 通过HTTP请求发起(用httpx AsyncClient)
- 带认证头(使用合法的gateway API key)
- 验证响应格式完全正确(不只是status code)
- 测试不需要任何外部API key即可通过
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

API_KEY = "test-e2e-key-integration-12345"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
async def app(tmp_path, monkeypatch):
    """Create a test FastAPI app with isolated config and storage."""
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestAdm!n2024Str0ng",
            "jwt_secret": "e2e-test-secret-must-be-at-least-32-characters-long-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        # Disable background systems to avoid side effects in tests
        discovery={"enabled": False},
        benchmark={"enabled": False},
        optimizer={"enabled": False},
        health={"enabled": False},
    )
    monkeypatch.setattr("moa_gateway.config._settings", test_settings)
    monkeypatch.setattr("moa_gateway.config.get_settings", lambda: test_settings)
    monkeypatch.setattr("moa_gateway.config.DATA_DIR", tmp_path)
    monkeypatch.setattr("moa_gateway.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")

    # Isolate assistant storage
    import moa_gateway.assistant.storage as ast_storage

    ast_storage._storage = None
    original_init = ast_storage.AssistantStorage.__init__

    def patched_init(self, data_dir=None):
        original_init(self, data_dir=str(tmp_path / "assistants"))

    monkeypatch.setattr(ast_storage.AssistantStorage, "__init__", patched_init)

    from moa_gateway.server import create_app

    application = create_app()
    yield application


@pytest.fixture
async def client(app):
    """Async HTTP client bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ============================================================
# 1. Authentication Tests
# ============================================================
class TestAuthentication:
    """验证所有endpoint都要求认证，且认证机制正确工作。"""

    @pytest.mark.anyio
    async def test_no_auth_returns_401(self, client):
        """无认证头应该被拒绝 - 返回401。"""
        endpoints = [
            ("POST", "/v1/chat/completions"),
            ("POST", "/v1/vision/analyze"),
            ("POST", "/v1/3d/generate"),
            ("POST", "/v1/world/simulate"),
            ("POST", "/v1/embodied/plan"),
            ("POST", "/v1/assistants"),
            ("GET", "/v1/models"),
            ("POST", "/v1/audio/speech"),
            ("POST", "/v1/video/generate"),
        ]
        for method, path in endpoints:
            resp = await client.request(method, path, json={})
            assert resp.status_code == 401, (
                f"{method} {path} should require auth, got {resp.status_code}"
            )

    @pytest.mark.anyio
    async def test_invalid_key_returns_401(self, client):
        """无效key应该返回401。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer invalid-key-xxx-000"},
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_valid_key_passes_auth(self, client):
        """有效key应该通过认证（即使后续请求可能因其他原因失败）。"""
        resp = await client.get("/v1/models", headers=HEADERS)
        # Valid key passes auth - should get 200 (models endpoint always works)
        assert resp.status_code == 200


# ============================================================
# 2. Health Check E2E
# ============================================================
class TestHealth:
    """健康检查端到端测试 - 不需要认证。"""

    @pytest.mark.anyio
    async def test_health_endpoint(self, client):
        """GET /health 应返回200和状态信息。"""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"
        assert "endpoints_total" in data

    @pytest.mark.anyio
    async def test_health_liveness(self, client):
        """GET /health/live 应返回200。"""
        resp = await client.get("/health/live")
        assert resp.status_code == 200


# ============================================================
# 3. Models E2E
# ============================================================
class TestModels:
    """模型列表端到端测试。"""

    @pytest.mark.anyio
    async def test_list_models(self, client):
        """GET /v1/models 应返回可用模型列表（至少包含preset别名）。"""
        resp = await client.get("/v1/models", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert data.get("object") == "list"
        assert len(data["data"]) > 0
        # Verify preset models are included
        model_ids = [m["id"] for m in data["data"]]
        assert "auto" in model_ids
        assert "fast" in model_ids
        assert "balanced" in model_ids

    @pytest.mark.anyio
    async def test_list_models_structure(self, client):
        """验证模型列表中每个条目的结构。"""
        resp = await client.get("/v1/models", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        for model in data["data"]:
            assert "id" in model
            assert "object" in model
            assert model["object"] == "model"
            assert "created" in model
            assert "owned_by" in model


# ============================================================
# 4. Chat Completions E2E
# ============================================================
class TestChatCompletions:
    """核心LLM调用端到端测试。"""

    @pytest.mark.anyio
    async def test_chat_missing_messages_422(self, client):
        """缺少messages字段应返回422。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "auto"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_chat_empty_messages_400(self, client):
        """空messages列表应返回400。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "auto", "messages": []},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_chat_invalid_temperature(self, client):
        """temperature超出范围应返回422。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "hi"}],
                "temperature": 5.0,  # > 2.0 max
            },
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_chat_no_available_model_503(self, client):
        """无可用模型时应返回503。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "auto",
                "messages": [{"role": "user", "content": "Hello"}],
                "max_tokens": 50,
            },
        )
        # With no model endpoints configured, expect 503
        assert resp.status_code == 503
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_chat_specific_model_not_found(self, client):
        """指定不存在的模型ID应返回503。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "nonexistent-model-xyz",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        # model not in pool -> routes to auto -> 503
        assert resp.status_code == 503

    @pytest.mark.anyio
    async def test_chat_model_oversized_name_422(self, client):
        """过长的模型名应返回422。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={
                "model": "x" * 200,
                "messages": [{"role": "user", "content": "hi"}],
            },
        )
        assert resp.status_code == 422


# ============================================================
# 5. Vision E2E
# ============================================================
class TestVision:
    """Vision分析端到端测试。"""

    @pytest.mark.anyio
    async def test_vision_analyze_validation(self, client):
        """Vision请求缺少images字段应返回422。"""
        resp = await client.post(
            "/v1/vision/analyze",
            headers=HEADERS,
            json={"prompt": "Describe this image"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_vision_analyze_empty_images_422(self, client):
        """Vision请求images为空列表应返回422。"""
        resp = await client.post(
            "/v1/vision/analyze",
            headers=HEADERS,
            json={"images": [], "prompt": "Describe"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_vision_analyze_provider_call(self, client):
        """Vision分析请求传入正确格式 - 无配置provider时返回502。"""
        resp = await client.post(
            "/v1/vision/analyze",
            headers=HEADERS,
            json={
                "images": [{"type": "image_url", "url": "https://example.com/test.jpg"}],
                "prompt": "Describe this image",
                "model": "auto",
            },
        )
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0


# ============================================================
# 6. Image Edit E2E
# ============================================================
class TestImageEdit:
    """图片编辑端到端测试。"""

    @pytest.mark.anyio
    async def test_image_edit_no_file_422(self, client):
        """图片编辑缺少文件应返回422。"""
        resp = await client.post("/v1/images/edits", headers=HEADERS)
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_image_edit_with_file(self, client):
        """图片编辑上传文件 - 无配置provider时返回502。"""
        # Create a minimal PNG file (1x1 pixel)
        png_data = (
            b'\x89PNG\r\n\x1a\n'
            b'\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x02\x00\x00\x00\x90wS\xde'
            b'\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05'
            b'\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        files = {"image": ("test.png", io.BytesIO(png_data), "image/png")}
        data = {"prompt": "Make it blue"}
        # Remove Content-Type from headers for multipart
        headers = {"Authorization": f"Bearer {API_KEY}"}
        resp = await client.post("/v1/images/edits", headers=headers, files=files, data=data)
        assert resp.status_code == 502
        resp_data = resp.json()
        assert "detail" in resp_data
        assert isinstance(resp_data["detail"], str)
        assert len(resp_data["detail"]) > 0

    @pytest.mark.anyio
    async def test_image_variations_no_file_422(self, client):
        """图片变体缺少文件应返回422。"""
        resp = await client.post("/v1/images/variations", headers=HEADERS)
        assert resp.status_code == 422


# ============================================================
# 7. 3D Generation E2E
# ============================================================
class TestThreeDGeneration:
    """3D生成端到端测试。"""

    @pytest.mark.anyio
    async def test_3d_generate_no_input_400(self, client):
        """3D生成无prompt也无image_url应返回400。"""
        resp = await client.post(
            "/v1/3d/generate",
            headers=HEADERS,
            json={"model": "auto"},
        )
        # prompt defaults to "" and image_url is None -> custom 400 check
        assert resp.status_code == 400
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_3d_generate_with_prompt(self, client):
        """3D生成带prompt - 无配置provider时返回502。"""
        resp = await client.post(
            "/v1/3d/generate",
            headers=HEADERS,
            json={"prompt": "A red sports car", "model": "auto"},
        )
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_3d_generate_invalid_format(self, client):
        """3D生成无效输出格式应返回422。"""
        resp = await client.post(
            "/v1/3d/generate",
            headers=HEADERS,
            json={"prompt": "A car", "output_format": "invalid_fmt"},
        )
        assert resp.status_code == 422


# ============================================================
# 8. World Model E2E
# ============================================================
class TestWorldModel:
    """世界模型端到端测试。"""

    @pytest.mark.anyio
    async def test_world_simulate_validation(self, client):
        """世界模型模拟缺少scenario应返回422。"""
        resp = await client.post(
            "/v1/world/simulate",
            headers=HEADERS,
            json={"steps": 5},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_world_simulate(self, client):
        """世界模型模拟请求 - 无配置provider时返回502。"""
        resp = await client.post(
            "/v1/world/simulate",
            headers=HEADERS,
            json={
                "scenario": "A ball is dropped from 10 meters height",
                "steps": 5,
            },
        )
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_world_predict(self, client):
        """状态预测请求 - 无配置provider时返回502。"""
        resp = await client.post(
            "/v1/world/predict",
            headers=HEADERS,
            json={
                "current_state": {"ball": {"position": [0, 10, 0]}},
                "action": "release ball",
            },
        )
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_world_simulate_invalid_steps(self, client):
        """超出范围的steps应返回422。"""
        resp = await client.post(
            "/v1/world/simulate",
            headers=HEADERS,
            json={"scenario": "test", "steps": 100},  # max is 50
        )
        assert resp.status_code == 422


# ============================================================
# 9. Embodied AI E2E
# ============================================================
class TestEmbodied:
    """具身模型端到端测试。"""

    @pytest.mark.anyio
    async def test_embodied_plan_validation(self, client):
        """动作规划缺少必需字段应返回422。"""
        resp = await client.post(
            "/v1/embodied/plan",
            headers=HEADERS,
            json={"goal": "Pick up cup"},  # missing observation
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_embodied_plan(self, client):
        """动作规划请求 - 无配置provider时返回502。"""
        resp = await client.post(
            "/v1/embodied/plan",
            headers=HEADERS,
            json={
                "observation": {"description": "A cup on a table 2 meters away"},
                "goal": "Pick up the cup",
            },
        )
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_embodied_execute(self, client):
        """动作执行请求 - 内置模拟器处理返回200。"""
        resp = await client.post(
            "/v1/embodied/execute",
            headers=HEADERS,
            json={
                "action": {"action": "move", "target": {"x": 2.0, "y": 0.0, "z": 0.0}},
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "success" in data
        assert data["success"] is True
        assert "new_state" in data

    @pytest.mark.anyio
    async def test_embodied_status(self, client):
        """状态查询请求 - 返回模拟器当前状态。"""
        resp = await client.get("/v1/embodied/status", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "robot_id" in data
        assert "state" in data
        assert "position" in data
        assert "battery" in data


# ============================================================
# 10. Audio E2E
# ============================================================
class TestAudio:
    """音频能力端到端测试。"""

    @pytest.mark.anyio
    async def test_audio_tts_validation(self, client):
        """TTS缺少input字段应返回422。"""
        resp = await client.post(
            "/v1/audio/speech",
            headers=HEADERS,
            json={"model": "tts-1", "voice": "alloy"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_audio_tts(self, client):
        """TTS请求 - 无配置provider时返回501。"""
        resp = await client.post(
            "/v1/audio/speech",
            headers=HEADERS,
            json={
                "model": "tts-1",
                "input": "Hello world, this is a test.",
                "voice": "alloy",
            },
        )
        assert resp.status_code == 501
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_audio_tts_invalid_format(self, client):
        """TTS无效输出格式应返回422。"""
        resp = await client.post(
            "/v1/audio/speech",
            headers=HEADERS,
            json={
                "model": "tts-1",
                "input": "Hello",
                "voice": "alloy",
                "response_format": "invalid_format",
            },
        )
        assert resp.status_code == 422


# ============================================================
# 11. Video E2E
# ============================================================
class TestVideo:
    """视频能力端到端测试。"""

    @pytest.mark.anyio
    async def test_video_generate_validation(self, client):
        """视频生成缺少prompt应返回422。"""
        resp = await client.post(
            "/v1/video/generate",
            headers=HEADERS,
            json={"duration": 4},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_video_generate(self, client):
        """视频生成请求 - 无配置provider时返回502。"""
        resp = await client.post(
            "/v1/video/generate",
            headers=HEADERS,
            json={
                "prompt": "A sunset over the ocean",
                "duration": 4,
            },
        )
        assert resp.status_code == 502
        data = resp.json()
        assert "detail" in data
        assert isinstance(data["detail"], str)
        assert len(data["detail"]) > 0

    @pytest.mark.anyio
    async def test_video_generate_invalid_duration(self, client):
        """视频生成duration超出范围应返回422。"""
        resp = await client.post(
            "/v1/video/generate",
            headers=HEADERS,
            json={"prompt": "test", "duration": 100},  # max is 30
        )
        assert resp.status_code == 422


# ============================================================
# 12. Assistant API E2E
# ============================================================
class TestAssistantE2E:
    """Assistant API完整生命周期测试。"""

    @pytest.mark.anyio
    async def test_create_assistant(self, client):
        """创建Assistant应返回完整的assistant对象。"""
        resp = await client.post(
            "/v1/assistants",
            headers=HEADERS,
            json={
                "name": "E2E Test Assistant",
                "model": "deepseek-v3",
                "instructions": "You are a helpful assistant",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "id" in data
        assert data["id"].startswith("asst_")
        assert data["object"] == "assistant"
        assert data["name"] == "E2E Test Assistant"
        assert data["model"] == "deepseek-v3"

    @pytest.mark.anyio
    async def test_full_assistant_lifecycle(self, client):
        """Assistant创建->Thread->Message->列出Messages->删除 完整流程。"""
        # 1. Create Assistant
        resp = await client.post(
            "/v1/assistants",
            headers=HEADERS,
            json={
                "name": "Lifecycle Test",
                "model": "gpt-4o",
                "instructions": "Reply with Hello",
            },
        )
        assert resp.status_code == 200
        assistant_id = resp.json()["id"]

        # 2. Create Thread
        resp = await client.post("/v1/threads", headers=HEADERS, json={})
        assert resp.status_code == 200
        thread = resp.json()
        assert thread["object"] == "thread"
        thread_id = thread["id"]

        # 3. Add Message
        resp = await client.post(
            f"/v1/threads/{thread_id}/messages",
            headers=HEADERS,
            json={"role": "user", "content": "What is 2+2?"},
        )
        assert resp.status_code == 200
        msg = resp.json()
        assert msg["role"] == "user"
        assert msg["thread_id"] == thread_id

        # 4. List Messages
        resp = await client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS)
        assert resp.status_code == 200
        messages = resp.json()
        assert messages["object"] == "list"
        assert len(messages["data"]) == 1

        # 5. Delete Assistant
        resp = await client.delete(f"/v1/assistants/{assistant_id}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

        # 6. Delete Thread
        resp = await client.delete(f"/v1/threads/{thread_id}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    @pytest.mark.anyio
    async def test_assistant_thread_with_initial_messages(self, client):
        """创建Thread时可附带初始messages。"""
        resp = await client.post(
            "/v1/threads",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": "Initial message"}]},
        )
        assert resp.status_code == 200
        thread_id = resp.json()["id"]

        # Verify message was added
        resp = await client.get(f"/v1/threads/{thread_id}/messages", headers=HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1

    @pytest.mark.anyio
    async def test_assistant_run_lifecycle(self, client):
        """Run创建和查询。"""
        # Create assistant
        resp = await client.post(
            "/v1/assistants",
            headers=HEADERS,
            json={"model": "gpt-4o", "name": "Run Test"},
        )
        assistant_id = resp.json()["id"]

        # Create thread with message
        resp = await client.post(
            "/v1/threads",
            headers=HEADERS,
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        thread_id = resp.json()["id"]

        # Create run (mock background execution)
        with patch("moa_gateway.routes.assistant._run_in_background", new_callable=AsyncMock):
            resp = await client.post(
                f"/v1/threads/{thread_id}/runs",
                headers=HEADERS,
                json={"assistant_id": assistant_id},
            )
        assert resp.status_code == 200
        run = resp.json()
        assert run["status"] == "queued"
        assert run["thread_id"] == thread_id
        run_id = run["id"]

        # Poll run
        resp = await client.get(
            f"/v1/threads/{thread_id}/runs/{run_id}", headers=HEADERS
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == run_id

        # List runs
        resp = await client.get(f"/v1/threads/{thread_id}/runs", headers=HEADERS)
        assert resp.status_code == 200
        assert len(resp.json()["data"]) == 1


# ============================================================
# 13. Error Handling
# ============================================================
class TestErrorHandling:
    """统一错误处理验证。"""

    @pytest.mark.anyio
    async def test_404_unknown_route(self, client):
        """未知路由返回404。"""
        resp = await client.get("/v1/nonexistent/endpoint", headers=HEADERS)
        assert resp.status_code == 404
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_method_not_allowed(self, client):
        """错误的HTTP方法返回405。"""
        resp = await client.get("/v1/chat/completions", headers=HEADERS)
        assert resp.status_code == 405

    @pytest.mark.anyio
    async def test_invalid_json_body(self, client):
        """无效JSON body应返回422。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers={**HEADERS, "Content-Type": "application/json"},
            content=b"not valid json{{{",
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_request_body_too_large(self, client):
        """超大请求体应被拒绝(413)。"""
        large_content = "x" * (2 * 1024 * 1024)  # 2MB > 1MB limit
        resp = await client.post(
            "/v1/chat/completions",
            headers={
                **HEADERS,
                "Content-Type": "application/json",
                "Content-Length": str(len(large_content)),
            },
            content=large_content.encode(),
        )
        assert resp.status_code == 413


# ============================================================
# 14. Response Format Validation
# ============================================================
class TestResponseFormat:
    """验证响应格式符合规范。"""

    @pytest.mark.anyio
    async def test_health_response_format(self, client):
        """Health响应包含所有必需字段。"""
        resp = await client.get("/health")
        data = resp.json()
        assert "status" in data
        assert "endpoints_total" in data
        assert "endpoints_enabled" in data
        assert "endpoints_healthy" in data

    @pytest.mark.anyio
    async def test_models_response_openai_format(self, client):
        """Models响应符合OpenAI格式: {object: "list", data: [...]}。"""
        resp = await client.get("/v1/models", headers=HEADERS)
        data = resp.json()
        assert data["object"] == "list"
        assert isinstance(data["data"], list)
        for item in data["data"]:
            assert "id" in item
            assert "object" in item
            assert item["object"] == "model"
            assert "owned_by" in item
            assert "created" in item
            assert isinstance(item["created"], int)

    @pytest.mark.anyio
    async def test_validation_error_format(self, client):
        """422响应应包含detail和errors。"""
        resp = await client.post(
            "/v1/chat/completions",
            headers=HEADERS,
            json={"model": "auto"},  # missing messages
        )
        assert resp.status_code == 422
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_auth_error_format(self, client):
        """401响应应包含detail说明。"""
        resp = await client.get("/v1/models")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data


# ============================================================
# 15. Security Headers
# ============================================================
class TestSecurityHeaders:
    """验证安全响应头。"""

    @pytest.mark.anyio
    async def test_security_headers_present(self, client):
        """响应应包含安全头。"""
        resp = await client.get("/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"
        assert "content-security-policy" in resp.headers
