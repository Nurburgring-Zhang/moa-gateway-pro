"""Core endpoint integration tests.

Validates HTTP status codes, response structure, and auth enforcement
for all critical API endpoints without requiring external LLM services.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture
async def app():
    """Create a test FastAPI app with isolated config."""
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestP@ss123!",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["test-api-key-12345"],
        }
    )
    with patch("moa_gateway.config.get_settings", return_value=test_settings):
        with patch("moa_gateway.config._settings", test_settings):
            from moa_gateway.server import create_app

            application = create_app()
            yield application


@pytest.fixture
async def client(app):
    """Async HTTP client bound to the test app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def admin_token(app):
    """Generate a valid admin JWT token for testing."""
    from moa_gateway.auth import create_jwt_token

    return create_jwt_token("admin", role="admin")


# ============================================================
# 1. Health Check
# ============================================================
class TestHealthEndpoints:
    """Health check endpoint tests."""

    @pytest.mark.anyio
    async def test_health_returns_200(self, client):
        """GET /health should return 200 with status ok."""
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert data["status"] == "ok"

    @pytest.mark.anyio
    async def test_health_detailed_returns_200(self, client):
        """GET /api/health/detailed should return 200."""
        resp = await client.get("/api/health/detailed")
        assert resp.status_code == 200


# ============================================================
# 2. Authentication
# ============================================================
class TestAuthEndpoints:
    """Authentication endpoint tests."""

    @pytest.mark.anyio
    async def test_login_missing_credentials_422(self, client):
        """POST /api/auth/login with no body returns 422."""
        resp = await client.post("/api/auth/login", json={})
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_login_wrong_credentials_401(self, client):
        """POST /api/auth/login with wrong creds returns 401."""
        resp = await client.post(
            "/api/auth/login",
            json={"username": "baduser", "password": "badpass"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_login_correct_credentials_200(self, client):
        """POST /api/auth/login with correct creds returns 200 + token."""
        from moa_gateway.storage import get_storage

        storage = get_storage()
        # Delete existing admin user if any, then create with known password
        with storage.conn() as c:
            existing = c.execute(
                "SELECT id FROM admin_users WHERE username = ?", ("testlogin_user",)
            ).fetchone()
            if existing:
                c.execute("DELETE FROM admin_users WHERE id = ?", (existing["id"],))
        storage.create_admin_user("testlogin_user", "TestP@ss123!", role="admin")

        resp = await client.post(
            "/api/auth/login",
            json={"username": "testlogin_user", "password": "TestP@ss123!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert isinstance(data["token"], str)
        assert len(data["token"]) > 0

    @pytest.mark.anyio
    async def test_auth_me_without_token_401(self, client):
        """GET /api/auth/me without token returns 401."""
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_auth_me_with_valid_token(self, client, admin_token):
        """GET /api/auth/me with valid JWT returns 200."""
        resp = await client.get(
            "/api/auth/me",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200


# ============================================================
# 3. Models Endpoint
# ============================================================
class TestModelsEndpoint:
    """Model listing endpoint tests."""

    @pytest.mark.anyio
    async def test_models_returns_200(self, client):
        """GET /v1/models should return 200 with model list."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "data" in data
        assert isinstance(data["data"], list)
        assert len(data["data"]) > 0
        for model in data["data"]:
            assert "id" in model
            assert "object" in model
            assert model["object"] == "model"

    @pytest.mark.anyio
    async def test_models_no_auth_returns_401(self, client):
        """GET /v1/models without auth should return 401 (P1-1 security fix)."""
        resp = await client.get("/v1/models")
        assert resp.status_code == 401


# ============================================================
# 4. Chat Completions
# ============================================================
class TestChatCompletions:
    """Chat completions endpoint tests."""

    @pytest.mark.anyio
    async def test_chat_no_auth_401(self, client):
        """POST /v1/chat/completions without auth returns 401."""
        resp = await client.post(
            "/v1/chat/completions",
            json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_chat_empty_messages_422(self, client):
        """POST /v1/chat/completions with invalid body returns 422."""
        resp = await client.post(
            "/v1/chat/completions",
            json={},
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 422

    @pytest.mark.anyio
    async def test_chat_invalid_model_field(self, client):
        """POST /v1/chat/completions with oversized model name returns 422."""
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "x" * 200,
                "messages": [{"role": "user", "content": "hi"}],
            },
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 422


# ============================================================
# 5. Admin Endpoints (require admin JWT)
# ============================================================
class TestAdminEndpoints:
    """Admin management endpoint tests."""

    @pytest.mark.anyio
    async def test_endpoints_list_no_auth_401(self, client):
        """GET /api/endpoints without admin JWT returns 401."""
        resp = await client.get("/api/endpoints")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_endpoints_list_with_admin(self, client, admin_token):
        """GET /api/endpoints with admin JWT returns 200."""
        resp = await client.get(
            "/api/endpoints",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_api_keys_list_no_auth_401(self, client):
        """GET /api/api-keys without admin JWT returns 401."""
        resp = await client.get("/api/api-keys")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_api_keys_list_with_admin(self, client, admin_token):
        """GET /api/api-keys with admin JWT returns 200."""
        resp = await client.get(
            "/api/api-keys",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_stats_no_auth_401(self, client):
        """GET /api/stats without admin JWT returns 401."""
        resp = await client.get("/api/stats")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data

    @pytest.mark.anyio
    async def test_logs_no_auth_401(self, client):
        """GET /api/logs without admin JWT returns 401."""
        resp = await client.get("/api/logs")
        assert resp.status_code == 401
        data = resp.json()
        assert "detail" in data


# ============================================================
# 6. MoA Endpoints
# ============================================================
class TestMoAEndpoints:
    """MoA orchestration endpoint tests."""

    @pytest.mark.anyio
    async def test_moa_execute_no_auth_401(self, client):
        """POST /v1/moa/execute without auth returns 401."""
        resp = await client.post(
            "/v1/moa/execute",
            json={"prompt": "test"},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_moa_presets_with_auth(self, client):
        """GET /v1/moa/presets with auth returns 200."""
        resp = await client.get(
            "/v1/moa/presets",
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_moa_prompts_with_auth(self, client):
        """GET /v1/moa/prompts with auth returns 200."""
        resp = await client.get(
            "/v1/moa/prompts",
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 200


# ============================================================
# 7. Capability Endpoints
# ============================================================
class TestCapabilityEndpoints:
    """Capability sub-system endpoint tests."""

    @pytest.mark.anyio
    async def test_capability_no_auth_401(self, client):
        """POST /v1/capability/ensemble-vote without auth returns 401."""
        resp = await client.post(
            "/v1/capability/ensemble-vote",
            json={"votes": []},
        )
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_capability_models_with_auth(self, client):
        """GET /v1/capability/models with auth returns 200."""
        resp = await client.get(
            "/v1/capability/models",
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 200


# ============================================================
# 8. Metrics & OpenAPI
# ============================================================
class TestMetricsAndDocs:
    """Metrics and documentation endpoint tests."""

    @pytest.mark.anyio
    async def test_metrics_returns_200(self, client):
        """GET /metrics returns 200 (Prometheus metrics)."""
        resp = await client.get("/metrics")
        assert resp.status_code == 200

    @pytest.mark.anyio
    async def test_openapi_schema_accessible(self, client):
        """GET /openapi.json returns valid OpenAPI schema."""
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert "openapi" in data
        assert "paths" in data
        assert len(data["paths"]) > 50  # We have 121 routes


# ============================================================
# 9. Route Preview
# ============================================================
class TestRoutePreview:
    """Route preview endpoint tests."""

    @pytest.mark.anyio
    async def test_route_preview_no_auth_401(self, client):
        """GET /v1/route/preview without auth returns 401."""
        resp = await client.get("/v1/route/preview")
        assert resp.status_code == 401

    @pytest.mark.anyio
    async def test_route_preview_with_auth(self, client):
        """GET /v1/route/preview with auth and query param returns 200."""
        resp = await client.get(
            "/v1/route/preview",
            params={"q": "hello world"},
            headers={"Authorization": "Bearer test-api-key-12345"},
        )
        assert resp.status_code == 200
