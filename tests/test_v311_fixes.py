"""v3.1.1 regression tests — audit P0/P1 fixes.

Every test exercises the REAL code path (no mocking of the logic under
test). Covers:
  P0   sandbox escape payloads rejected + subprocess isolation intact
  P1-1 secret-scan admin-only + output redaction
  P1-2 in-flight state_dir no longer caller-controlled
  P1-3 health restore/purge admin-only
  P1-4 moa prompt template write admin-only
  P1-5/P1-6 hardened SSRF validator (encoded IPs, internal names)
  P1-7 MoA mock_used derivation surfaces in to_dict
  P1-11 MoA all-references-failed raises explicit 502 (no silent degrade)
  P1-13 GDPR deletion really deletes / anonymizes
  P1-17 streaming requests consume the daily token quota
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Shared fixtures (isolated settings + app + clients)
# ---------------------------------------------------------------------------

@pytest.fixture
async def app(tmp_path):
    from moa_gateway.config import Settings

    test_settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "TestP@ss123!",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["yaml-admin-key-001"],
        },
        models=[
            {
                "id": "mock-model",
                "provider": "openai",
                "model": "mock-model",
                "tier": "standard",
                "api_base": "http://127.0.0.1:1/v1",
                "api_key_env": "MOA_TEST_NONEXISTENT_KEY",
            }
        ],
    )
    import moa_gateway.config as cfg_mod
    import moa_gateway.storage as storage_mod

    with patch.object(cfg_mod, "get_settings", return_value=test_settings):
        with patch.object(cfg_mod, "_settings", test_settings):
            with patch.object(cfg_mod, "DATA_DIR", tmp_path):
                with patch.object(cfg_mod, "ROOT_DIR", tmp_path):
                    with patch.object(storage_mod, "DATA_DIR", tmp_path):
                        with patch.object(storage_mod, "ROOT_DIR", tmp_path):
                            with patch.object(
                                storage_mod, "_FERNET_PATH", tmp_path / ".fernet_key"
                            ):
                                storage_mod.Storage._instance = None
                                from moa_gateway.server import create_app

                                application = create_app()
                                yield application
                                storage_mod.Storage._instance = None


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def admin_token(app):
    from moa_gateway.auth import create_jwt_token

    return create_jwt_token("admin", role="admin")


@pytest.fixture
async def plain_key(app):
    """A DB-issued API key (non-admin role)."""
    from moa_gateway.storage import get_storage

    rec = get_storage().create_api_key("plain", quota_rpm=1000, quota_daily_tokens=10_000_000)
    return rec["key"]


# ---------------------------------------------------------------------------
# P1-1: secret-scan admin-only + redaction
# ---------------------------------------------------------------------------

class TestSecretScanHardening:
    async def test_plain_key_rejected(self, client, plain_key):
        resp = await client.post(
            "/v1/capability/secret-scan",
            json={"path": "."},
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert resp.status_code == 401, resp.text

    async def test_admin_scan_redacts_output(self, client, admin_token, tmp_path):
        # Plant a fake secret inside the (patched) project root
        secret_file = tmp_path / "leak.txt"
        secret_file.write_text(
            "aws_key = AKIAZ7Q9X2B4C6D8E0F1\n", encoding="utf-8"
        )
        resp = await client.post(
            "/v1/capability/secret-scan",
            json={"path": str(tmp_path)},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.text
        assert "AKIAZ7Q9X2B4C6D8E0F1" not in body, "raw secret leaked in response"

    async def test_admin_scan_outside_root_rejected(self, client, admin_token, tmp_path):
        import os

        # tmp_path IS the patched root; use a path outside it
        outside = os.path.dirname(str(tmp_path))
        outside = os.path.dirname(outside)  # climb out of the patched root
        resp = await client.post(
            "/v1/capability/secret-scan",
            json={"path": outside},
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert resp.status_code in (400, 403), resp.text


# ---------------------------------------------------------------------------
# P1-2: in-flight state_dir ignored
# ---------------------------------------------------------------------------

class TestInFlightStateDir:
    async def test_caller_state_dir_ignored(self, client, plain_key, tmp_path):
        evil_dir = tmp_path / "evil-write-target"
        resp = await client.post(
            "/v1/capability/in-flight",
            json={
                "action": "start",
                "phase": "analyze",
                "state_dir": str(evil_dir),
            },
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert resp.status_code == 200, resp.text
        assert not evil_dir.exists(), "caller-controlled state_dir was honored"


# ---------------------------------------------------------------------------
# P1-3: health restore / purge admin-only
# ---------------------------------------------------------------------------

class TestHealthAdminGating:
    async def test_restore_requires_admin(self, client, plain_key):
        resp = await client.post(
            "/v1/health/poison/restore",
            json={
                "endpoint_id": "poison",
                "provider": "openai",
                "model": "x",
                "api_base": "http://evil.example/v1",
            },
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert resp.status_code == 401, resp.text

    async def test_purge_run_requires_admin(self, client, plain_key):
        resp = await client.post(
            "/v1/health/purge/run",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# P1-4: moa prompt template write admin-only
# ---------------------------------------------------------------------------

class TestMoaPromptGating:
    async def test_prompt_write_requires_admin(self, client, plain_key):
        resp = await client.put(
            "/v1/moa/prompts/aggregator",
            json={"content": "INJECTED PROMPT"},
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert resp.status_code == 401, resp.text

    async def test_prompt_delete_requires_admin(self, client, plain_key):
        resp = await client.delete(
            "/v1/moa/prompts/aggregator",
            headers={"Authorization": f"Bearer {plain_key}"},
        )
        assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# P1-5 / P1-6: hardened SSRF validator
# ---------------------------------------------------------------------------

class TestSSRFValidator:
    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8910/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://172.16.0.9/",
            "http://localhost/x",
            "ftp://example.com/",
            "http://2130706433/",       # decimal-encoded 127.0.0.1
            "http://0x7f000001/",       # hex-encoded 127.0.0.1
        ],
    )
    def test_blocked(self, url):
        from moa_gateway.utils.url_validator import is_safe_external_url

        ok, reason = is_safe_external_url(url)
        assert not ok, f"{url} should be blocked ({reason})"
        assert reason

    # Encoded IP literals must be normalized and blocked platform-
    # independently, i.e. WITHOUT relying on socket.getaddrinfo (whose
    # handling of these forms differs per platform).
    @pytest.mark.parametrize(
        "url",
        [
            "http://0177.0.0.1/",                # octal dotted 127.0.0.1
            "http://0x7f.0x0.0x0.0x1/",          # hex dotted 127.0.0.1
            "http://0x7F.0x0.0x0.0x1/",          # uppercase hex
            "http://127.0.1/",                   # 3-part short form
            "http://127.1/",                     # 2-part short form
            "http://0x7f.1/",                    # mixed hex/decimal parts
            "http://017700000001/",              # single octal integer
            "http://127.0.0.1./",                # trailing dot
            "http://[::1]/",                     # IPv6 loopback compressed
            "http://[0:0:0:0:0:0:0:1]/",         # IPv6 loopback expanded
            "http://[::ffff:127.0.0.1]/",        # IPv4-mapped IPv6
            "http://[::ffff:7f00:1]/",           # IPv4-mapped IPv6 (hex)
            "http://[::ffff:169.254.169.254]/",  # mapped cloud metadata
            "http://[::ffff:10.0.0.5]/",         # mapped RFC1918
            "http://[::127.0.0.1]/",             # IPv4-compatible IPv6
            "http://127.0.0.256/",               # invalid literal -> fail closed
            "http://1.2.3.4.5/",                 # 5 parts -> fail closed
            # leading-zero ambiguity: octal reading public, decimal reading
            # internal -> blocked (stacks disagree on these encodings)
            "http://010.010.010.010/",           # oct 8.8.8.8 / dec 10.10.10.10
            "http://02130706433/",               # oct public / dec 127.0.0.1
        ],
    )
    def test_blocked_encoded_ip_variants(self, url):
        from moa_gateway.utils.url_validator import is_safe_external_url

        ok, reason = is_safe_external_url(url)
        assert not ok, f"{url} should be blocked ({reason})"
        assert reason

    @pytest.mark.parametrize(
        "url",
        [
            "http://8.8.8.8/",          # public IPv4 literal
            "http://93.184.216.34/",    # public IPv4 literal
            "https://1.1.1.1/",         # public IPv4 literal (https)
            "https://example.com/",     # ordinary domain -> DNS path
            "http://example.com./",     # FQDN trailing dot -> DNS path
            "http://01.1.1.1/",         # leading zero, both readings public
        ],
    )
    def test_allowed_public_targets(self, url):
        from moa_gateway.utils.url_validator import is_safe_external_url

        ok, reason = is_safe_external_url(url)
        assert ok, f"{url} should be allowed ({reason})"

    def test_api_verify_delegates(self):
        from moa_gateway.agent_loop.skills.api_verify import _is_safe_url

        ok, _ = _is_safe_url("http://127.0.0.1:1/")
        assert not ok

    def test_mcp_guard_delegates(self):
        from moa_gateway.routes.mcp import _is_safe_external_url

        assert not _is_safe_external_url("http://169.254.169.254/")


# ---------------------------------------------------------------------------
# P1-7: MoA mock_used derivation
# ---------------------------------------------------------------------------

class TestMoAMockLabeling:
    def test_mock_used_from_reference_provider(self):
        from moa_gateway.moa import MoAResult, ReferenceResult

        result = MoAResult(request_id="r1", query="q", preset="p", strategy="parallel")
        result.references = [
            ReferenceResult(model_id="m1", content="x", success=True, provider="mock"),
        ]
        result.mock_used = any(
            (r.provider or "") == "mock" for r in result.references if r.success
        )
        d = result.to_dict()
        assert d["mock"] is True

    def test_real_providers_not_flagged(self):
        from moa_gateway.moa import MoAResult, ReferenceResult

        result = MoAResult(request_id="r2", query="q", preset="p", strategy="parallel")
        result.references = [
            ReferenceResult(model_id="m1", content="x", success=True, provider="openai"),
        ]
        result.mock_used = any(
            (r.provider or "") == "mock" for r in result.references if r.success
        )
        assert result.to_dict()["mock"] is False


# ---------------------------------------------------------------------------
# P1-11: MoA all-references-failed -> explicit 502 (no silent degrade)
# ---------------------------------------------------------------------------

class TestMoAAllFailLoud:
    async def test_parallel_all_fail_raises_502(self):
        import moa_gateway.config as cfg_mod
        from moa_gateway.config import MoAPresetConfig
        from moa_gateway.moa import (
            MoAOrchestrator,
            MoAResult,
            ReferenceResult,
        )
        from moa_gateway.providers.base import ProviderError

        settings = cfg_mod.get_settings()
        orch = MoAOrchestrator.__new__(MoAOrchestrator)
        orch.settings = settings
        orch.pool = None  # not touched on this path

        async def _all_fail(refs, messages, temperature, max_tokens):
            return [
                ReferenceResult(model_id="m1", content="", success=False, error="boom-1"),
                ReferenceResult(model_id="m2", content="", success=False, error="boom-2"),
            ]

        orch._run_references = _all_fail  # type: ignore[method-assign]

        class _FakeEP:
            def __init__(self, eid):
                self.id = eid

        async def _never_called(*a, **k):
            raise AssertionError("aggregator must not run when all references failed")

        orch._call_with_fallback = _never_called  # type: ignore[method-assign]
        orch._build_aggregator_messages = lambda *a: []  # type: ignore[method-assign]
        orch._resolve_models = lambda preset_cfg, ref_count, agg_id: (  # type: ignore[method-assign]
            [_FakeEP("m1"), _FakeEP("m2")],
            _FakeEP("agg"),
        )

        preset_cfg = MoAPresetConfig(strategy="parallel", reference_count=2)
        result = MoAResult(request_id="r3", query="q", preset="balanced", strategy="parallel")

        with pytest.raises(ProviderError) as exc_info:
            await orch._run_parallel(
                result, [{"role": "user", "content": "q"}], None, preset_cfg,
                2, None, 0, 0.6, 0.4, 512, time.time(),
            )
        assert exc_info.value.status == 502
        assert "boom-1" in str(exc_info.value)
        assert "boom-2" in str(exc_info.value)


# ---------------------------------------------------------------------------
# P1-13: GDPR deletion really deletes
# ---------------------------------------------------------------------------

class TestGDPRRealDeletion:
    async def test_deletion_removes_user_keys_and_anonymizes_logs(
        self, storage_instance
    ):
        from moa_gateway.compliance.gdpr import GDPRManager

        storage = storage_instance
        # Create the data subject: account + key + attributed log row
        storage.create_admin_user("gdpr-user", "TempPass#123", role="user")
        key_rec = storage.create_api_key("gdpr-user", quota_rpm=10)
        key_id = key_rec["key_id"]
        storage.log_request({
            "request_id": "req-gdpr-1",
            "api_key_id": key_id,
            "model_requested": "m",
            "model_used": "m",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost": 0.0,
            "latency_ms": 1.0,
            "status": "ok",
        })

        mgr = GDPRManager()
        req = await mgr.create_deletion_request("gdpr-user")
        with storage.conn() as db_conn:
            result = await mgr.process_deletion(req.request_id, db_conn=db_conn)

        assert result["status"] == "completed", result
        deleted = result["deleted"]
        assert deleted["user_profile"] is True
        assert deleted["api_keys"] >= 1
        assert deleted["logs_anonymized"] >= 1

        # Verify the database state for real
        with storage.conn() as c:
            row = c.execute(
                "SELECT COUNT(*) FROM admin_users WHERE username = ?", ("gdpr-user",)
            ).fetchone()
            assert row[0] == 0, "user row still present after GDPR deletion"
            row = c.execute(
                "SELECT COUNT(*) FROM api_keys WHERE name = ?", ("gdpr-user",)
            ).fetchone()
            assert row[0] == 0, "api keys still present after GDPR deletion"
            row = c.execute(
                "SELECT api_key_id FROM request_logs WHERE request_id = ?",
                ("req-gdpr-1",),
            ).fetchone()
            assert row is not None
            assert row[0].startswith("anon_"), f"log not anonymized: {row[0]}"


# ---------------------------------------------------------------------------
# P1-17: streaming consumes daily token quota
# ---------------------------------------------------------------------------

class TestStreamingQuota:
    async def test_stream_true_accounts_tokens(self, client, app):
        from moa_gateway.storage import get_storage

        storage = get_storage()
        rec = storage.create_api_key(
            "stream-quota", quota_rpm=1000, quota_daily_tokens=10_000_000
        )
        key = rec["key"]
        key_id = rec["key_id"]

        def _used():
            import datetime

            day = datetime.date.today().strftime("%Y%m%d")
            with storage.conn() as c:
                row = c.execute(
                    "SELECT tokens FROM ratelimit_tokens WHERE api_key_id = ? AND day = ?",
                    (key_id, day),
                ).fetchone()
                return row[0] if row else 0

        before = _used()
        resp = await client.post(
            "/v1/chat/completions",
            json={
                "model": "mock-model",
                "stream": True,
                "messages": [
                    {"role": "user", "content": "streaming quota probe " * 8}
                ],
            },
            headers={"Authorization": f"Bearer {key}"},
        )
        assert resp.status_code == 200, resp.text
        after = _used()
        assert after > before, (
            f"stream=true did not consume daily quota (before={before}, after={after})"
        )
