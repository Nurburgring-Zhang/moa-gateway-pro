"""
Auth real path tests - no dependency_overrides.
Validates all endpoints auth protection works under real conditions.

Key design:
- Does NOT use app.dependency_overrides
- Uses env-var configured real gateway key for validation
- Tests auth rejection, valid access, admin isolation, public endpoints, edge cases
"""
import pytest
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True, scope="function")
def _test_env(monkeypatch, tmp_path):
    """Isolate test environment per-test.

    Sets credentials and DATA_DIR to tmp_path so that admin user seeding
    and config DB writes never touch the production database.
    Also resets the settings cache so monkeypatched env vars take effect.
    """
    # Reset cached settings so env vars are re-read by get_settings()
    import moa_gateway.config as _cfg
    _cfg._settings = None

    monkeypatch.setenv("MOA_GATEWAY_KEY", "real-test-key-auth-verify")
    monkeypatch.setenv("MOA_ADMIN_PASSWORD", "RealTestP@ss99!")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    yield


from moa_gateway.server import create_app  # noqa: E402


@pytest.fixture
def app(_test_env):
    """Create app without any dependency_overrides."""
    return create_app()


@pytest.fixture
async def client(app):
    """Real client without dependency_overrides."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


VALID_KEY = "real-test-key-auth-verify"  # must match _test_env fixture
VALID_HEADERS = {"Authorization": f"Bearer {VALID_KEY}"}
INVALID_HEADERS = {"Authorization": "Bearer totally-invalid-key-xyz"}
NO_HEADERS: dict = {}


class TestNoAuthRejection:
    """Verify sensitive endpoints reject requests without auth header."""

    SENSITIVE_ENDPOINTS = [
        ("POST", "/v1/chat/completions", {"model": "test", "messages": [{"role": "user", "content": "hi"}]}),
        ("GET", "/v1/models", None),
        ("POST", "/v1/vision/analyze", {"images": ["http://x.com/a.jpg"], "prompt": "desc"}),
        ("POST", "/v1/3d/generate", {"prompt": "car", "source_type": "text"}),
        ("POST", "/v1/world/simulate", {"scenario": "test", "steps": 1}),
        ("POST", "/v1/embodied/plan", {"observation": {}, "goal": "test"}),
        ("POST", "/v1/audio/speech", {"model": "tts-1", "input": "hi", "voice": "alloy"}),
        ("POST", "/v1/video/generate", {"prompt": "test"}),
        ("POST", "/v1/assistants", {"model": "test", "name": "test", "instructions": "test"}),
        ("GET", "/v1/assistants", None),
        ("POST", "/v1/threads", {}),
        ("GET", "/v1/workflows", None),
        ("POST", "/v1/workflows", {"name": "test"}),
        ("GET", "/v1/health", None),
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("method,path,body", SENSITIVE_ENDPOINTS)
    async def test_no_auth_rejected(self, client, method, path, body):
        """Requests without auth header must be rejected."""
        if body is not None:
            resp = await client.request(method, path, json=body)
        else:
            resp = await client.request(method, path)
        assert resp.status_code in (401, 403), (
            f"{method} {path} without auth returned {resp.status_code}, expected 401/403"
        )


class TestInvalidKeyRejection:
    """Verify invalid tokens are rejected."""

    @pytest.mark.asyncio
    async def test_invalid_bearer_token(self, client):
        """Invalid Bearer token should return 401 or 403."""
        resp = await client.post(
            "/v1/chat/completions",
            headers=INVALID_HEADERS,
            json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_empty_bearer_token(self, client):
        """Empty Bearer token should be rejected."""
        resp = await client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer "},
            json={"model": "test", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_malformed_auth_header(self, client):
        """Malformed Authorization headers should be rejected."""
        malformed_values = [
            "NotBearer valid-key",
            "Basic dXNlcjpwYXNz",
            "",
        ]
        for header_value in malformed_values:
            resp = await client.get(
                "/v1/models",
                headers={"Authorization": header_value} if header_value else {},
            )
            assert resp.status_code in (401, 403, 422), (
                f"Auth header \'{header_value}\' returned {resp.status_code}"
            )

    @pytest.mark.asyncio
    async def test_bearer_case_insensitive(self, client):
        """Lowercase 'bearer' prefix is accepted (_bearer_or_raw is case-insensitive)."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"bearer {VALID_KEY}"},
        )
        assert resp.status_code == 200


class TestValidKeyAccess:
    """Verify valid key grants access."""

    @pytest.mark.asyncio
    async def test_valid_key_models_list(self, client):
        """Valid key should access model list."""
        resp = await client.get("/v1/models", headers=VALID_HEADERS)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_key_health(self, client):
        """Valid key should access /v1/health."""
        resp = await client.get("/v1/health", headers=VALID_HEADERS)
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_key_chat(self, client):
        """Valid key should be able to initiate chat (may 503 but not 401/403)."""
        resp = await client.post(
            "/v1/chat/completions",
            headers=VALID_HEADERS,
            json={"model": "deepseek-v3", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert resp.status_code not in (401, 403)

    @pytest.mark.asyncio
    async def test_valid_key_raw_no_bearer_prefix(self, client):
        """Valid key without Bearer prefix should also authenticate (project supports both)."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": VALID_KEY},
        )
        assert resp.status_code == 200


class TestAdminIsolation:
    """Verify Admin endpoints are isolated from regular API keys (require JWT)."""

    @pytest.mark.asyncio
    async def test_admin_endpoint_with_user_key(self, client):
        """Regular API key should not access admin endpoints."""
        admin_endpoints = [
            ("GET", "/api/endpoints"),
            ("GET", "/api/api-keys"),
            ("GET", "/api/stats"),
            ("GET", "/api/logs"),
        ]
        for method, path in admin_endpoints:
            resp = await client.request(method, path, headers=VALID_HEADERS)
            assert resp.status_code in (401, 403, 404), (
                f"Admin {method} {path} with user key returned {resp.status_code}"
            )

    @pytest.mark.asyncio
    async def test_admin_endpoint_no_auth(self, client):
        """No auth header should not access admin endpoints."""
        resp = await client.get("/api/endpoints")
        assert resp.status_code in (401, 403)


class TestPublicEndpoints:
    """Verify public endpoints require no authentication."""

    PUBLIC_ENDPOINTS = [
        "/health",
        "/health/live",
        "/health/ready",
        "/health/startup",
    ]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PUBLIC_ENDPOINTS)
    async def test_public_no_auth_needed(self, client, path):
        """Public endpoints should not require auth."""
        resp = await client.get(path)
        # Readiness probe may return 503 if not ready, but shouldn't require auth (no 401/403)
        assert resp.status_code in (200, 503), (
            f"Public endpoint {path} returned {resp.status_code}, expected 200 or 503"
        )


class TestTokenEdgeCases:
    """Token edge case / boundary tests."""

    @pytest.mark.asyncio
    async def test_token_exceeding_max_length(self, client):
        """Token exceeding _MAX_TOKEN_LEN (256) should be rejected."""
        long_token = "x" * 300
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {long_token}"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_token_at_max_length(self, client):
        """Token exactly at 256 chars (invalid) should return 401."""
        token_256 = "a" * 256
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {token_256}"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_special_chars_token(self, client):
        """Special characters in token should be handled safely (not crash)."""
        garbled_token = "!@#$%^&*()_+-=[]{}|;:',.<>?/~`"
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {garbled_token}"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_null_bytes_in_token(self, client):
        """Null bytes should not cause crash."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": "Bearer test\x00key"},
        )
        assert resp.status_code in (401, 403, 400)

    @pytest.mark.asyncio
    async def test_sql_injection_in_token(self, client):
        """SQL injection attempts should be handled safely."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": "Bearer \' OR \'1\'=\'1"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_comma_separated_tokens(self, client):
        """Comma-separated tokens - _bearer_or_raw takes first one."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer invalid-token, Bearer {VALID_KEY}"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_comma_separated_valid_first(self, client):
        """Comma-separated with valid key first should pass."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {VALID_KEY}, Bearer invalid"},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_jwt_like_but_invalid(self, client):
        """Fake JWT-format token should be rejected."""
        fake_jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.invalidsignature"
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": f"Bearer {fake_jwt}"},
        )
        assert resp.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_whitespace_only_token(self, client):
        """Whitespace-only token should be rejected."""
        resp = await client.get(
            "/v1/models",
            headers={"Authorization": "Bearer    "},
        )
        assert resp.status_code in (401, 403)
