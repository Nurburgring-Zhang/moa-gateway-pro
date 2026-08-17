"""v3.1.1 second-round regression tests — adversarial-review P1/P2 fixes.

Covers:
  P1-A  sandbox operator.attrgetter / dunder-string / chr-format escapes
  P1-B  SSRF CGNAT (RFC 6598) + IANA special-purpose ranges
  P2-C  cache mock-label wrap/unwrap round-trip
  P2-D  self_heal promote/demote wiring
  P2-E  GDPR salted irreversible anonymization + user_id scrub
  P2-F  DELETE /v1/moa/prompts returns cleanly (no NameError)
"""
from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------------------
# P1-A: sandbox escape regressions
# ---------------------------------------------------------------------------

class TestSandboxSecondRound:
    def _run(self, code: str) -> str:
        from moa_gateway.agent_loop.skills.code_execute import code_execute

        return asyncio.run(code_execute(code))

    def test_operator_import_rejected(self):
        out = self._run("import operator\nprint(operator.attrgetter('__builtins__'))")
        assert "Security violation" in out
        assert "operator" in out

    def test_string_module_import_rejected(self):
        out = self._run("import string\nprint(string.Formatter)")
        assert "Security violation" in out

    def test_dunder_string_literal_rejected(self):
        # attrgetter-style dunder argument as a plain literal
        out = self._run("x = '__builtins__'\nprint(x)")
        assert "Security violation" in out

    def test_split_dunder_fragment_rejected(self):
        # the '.__dict__' fragment used in the concatenation attack
        out = self._run("frag = '.__dict__'\nprint(frag)")
        assert "Security violation" in out

    def test_chr_built_format_blocked_at_runtime(self):
        # No dunder literal in source; the runtime module proxy must still
        # block the attribute walk when str.format resolves it.
        code = (
            "import json\n"
            "u = chr(95)\n"
            "fmt = chr(123) + '0.' + u+u + 'dict' + u+u + chr(125)\n"
            "print(fmt.format(json))\n"
        )
        out = self._run(code)
        assert "Security violation" in out
        assert "forbidden" in out

    def test_benign_json_still_works(self):
        out = self._run("import json\nprint(json.dumps({'k': [1, 2]}))")
        assert '{"k": [1, 2]}' in out

    def test_benign_math_still_works(self):
        out = self._run("import math\nprint(math.gcd(12, 8))")
        assert "4" in out


# ---------------------------------------------------------------------------
# P1-B: SSRF CGNAT + IANA special ranges
# ---------------------------------------------------------------------------

class TestSSRFSecondRound:
    @pytest.mark.parametrize(
        "ip",
        [
            "100.100.100.200",  # Alibaba Cloud metadata (CGNAT)
            "100.64.0.1",       # RFC 6598 CGNAT
            "100.127.255.254",  # CGNAT upper bound
            "192.0.2.5",        # TEST-NET-1
            "198.18.0.1",       # benchmarking
            "198.51.100.9",     # TEST-NET-2
            "203.0.113.7",      # TEST-NET-3
            "240.0.0.1",        # reserved
            "0.0.0.1",          # "this" network
        ],
    )
    def test_special_range_blocked(self, ip):
        from moa_gateway.utils.url_validator import _ip_is_dangerous

        assert _ip_is_dangerous(ip), f"{ip} must be blocked"

    @pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "8.8.4.4"])
    def test_public_allowed(self, ip):
        from moa_gateway.utils.url_validator import _ip_is_dangerous

        assert not _ip_is_dangerous(ip), f"{ip} must be allowed"

    def test_cgnat_metadata_url_blocked(self):
        from moa_gateway.utils.url_validator import is_safe_external_url

        ok, reason = is_safe_external_url("http://100.100.100.200/latest/meta-data/")
        assert not ok
        assert reason


# ---------------------------------------------------------------------------
# P2-C: cache mock-label round-trip
# ---------------------------------------------------------------------------

class TestCacheMockEnvelope:
    def test_wrap_unwrap_roundtrip(self):
        from moa_gateway.cache.manager import CacheManager

        body = {"choices": [{"message": {"content": "hi"}}]}
        wrapped = CacheManager._wrap(body, mock=True)
        out_body, out_mock = CacheManager._unwrap(wrapped)
        assert out_body == body
        assert out_mock is True

    def test_unwrap_real_flag(self):
        from moa_gateway.cache.manager import CacheManager

        wrapped = CacheManager._wrap({"x": 1}, mock=False)
        _, out_mock = CacheManager._unwrap(wrapped)
        assert out_mock is False

    def test_unwrap_legacy_entry(self):
        from moa_gateway.cache.manager import CacheManager

        # A pre-v3.1.1 entry (plain value) unwraps with mock=None.
        body, mock = CacheManager._unwrap({"legacy": True})
        assert body == {"legacy": True}
        assert mock is None


# ---------------------------------------------------------------------------
# P2-D: self_heal promote/demote wiring
# ---------------------------------------------------------------------------

class TestSelfHealWiring:
    def test_promote_calls_promote_not_demote(self):
        from moa_gateway.services.quota_service import QuotaService

        svc = QuotaService()
        eps = [
            {"endpoint_id": "ep1", "consecutive_failures": 0, "cooldown_until": 0.0},
        ]
        res = svc.self_heal_promote(eps, "ep1", reason="recovered")
        action = res.get("action") if isinstance(res, dict) else getattr(res, "action", None)
        # _build_heal_state registers endpoints at tier=primary, so a real
        # promote returns 'promote' or no_op('already primary'). The broken
        # wiring executed demote instead, which yields action='demote'.
        assert action in ("promote", "no_op"), f"promote returned action={action!r}"
        assert action != "demote", "promote is miswired to demote"

    def test_demote_calls_demote_not_auto_balance(self):
        from moa_gateway.services.quota_service import QuotaService

        svc = QuotaService()
        eps = [
            {"endpoint_id": "ep1", "consecutive_failures": 5, "cooldown_until": 0.0},
        ]
        res = svc.self_heal_demote(eps, "ep1", reason="failing")
        action = res.get("action") if isinstance(res, dict) else getattr(res, "action", None)
        assert action == "demote", f"demote returned action={action!r}"


# ---------------------------------------------------------------------------
# P2-E: GDPR salted anonymization + scrub
# ---------------------------------------------------------------------------

class TestGDPRSalted:
    def test_anon_id_salted_irreversible(self):
        from moa_gateway.compliance.gdpr import GDPRManager

        a = GDPRManager._make_anon_id("alice")
        b = GDPRManager._make_anon_id("alice")
        assert a.startswith("anon_")
        assert a != b, "same input must yield different tokens (random salt)"
        assert len(a) >= 20

    def test_process_deletion_scrubs_user_id(self):
        import sqlite3

        from moa_gateway.compliance.gdpr import GDPRManager

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE admin_users (username TEXT)")
        conn.execute("CREATE TABLE request_logs (api_key_id TEXT)")
        conn.execute("CREATE TABLE api_keys (key_id TEXT, name TEXT)")
        conn.execute("INSERT INTO admin_users VALUES ('bob')")
        conn.execute("INSERT INTO api_keys VALUES ('k1', 'bob')")
        conn.execute("INSERT INTO request_logs VALUES ('k1')")

        mgr = GDPRManager()

        async def _go():
            req = await mgr.create_deletion_request("bob")
            result = await mgr.process_deletion(req.request_id, db_conn=conn)
            return req, result

        req, result = asyncio.run(_go())
        assert result["status"] == "completed"
        assert req.user_id == "[forgotten]", "user_id must be scrubbed after deletion"
        # list_requests must not re-identify the subject
        listed = mgr.list_requests()
        assert all(r["user_id"] == "[forgotten]" for r in listed)
        # log row anonymized to a salted token, not the username
        row = conn.execute("SELECT api_key_id FROM request_logs").fetchone()
        assert row[0].startswith("anon_")
        assert row[0] != "bob"
        conn.close()


# ---------------------------------------------------------------------------
# P2-F: DELETE /v1/moa/prompts no NameError
# ---------------------------------------------------------------------------

class TestPromptDeleteClean:
    def test_delete_returns_cleanly_for_admin(self, monkeypatch, tmp_path):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        import moa_gateway.config as cfg_mod
        import moa_gateway.storage as storage_mod
        from moa_gateway.config import Settings

        settings = Settings(
            auth={
                "admin_username": "admin",
                "admin_password": "SuperStr0ng!Pass#2024",
                "jwt_secret": "prompt-delete-secret-long-enough-for-hs256-x",
                "jwt_expire_minutes": 60,
                "gateway_api_keys": [],
            }
        )
        with patch.object(cfg_mod, "get_settings", return_value=settings):
            with patch.object(cfg_mod, "_settings", settings):
                with patch.object(cfg_mod, "DATA_DIR", tmp_path):
                    with patch.object(storage_mod, "DATA_DIR", tmp_path):
                        with patch.object(storage_mod, "ROOT_DIR", tmp_path):
                            with patch.object(
                                storage_mod, "_FERNET_PATH", tmp_path / ".fernet_key"
                            ):
                                storage_mod.Storage._instance = None
                                from moa_gateway.auth import create_jwt_token
                                from moa_gateway.server import create_app

                                app = create_app()
                                token = create_jwt_token("admin", role="admin")
                                client = TestClient(app)
                                # delete a template that does not exist -> clean 404,
                                # NOT a 500 NameError
                                resp = client.delete(
                                    "/v1/moa/prompts/nonexistent-tpl",
                                    headers={"Authorization": f"Bearer {token}"},
                                )
                                storage_mod.Storage._instance = None
                                assert resp.status_code == 404, resp.text
                                assert "NameError" not in resp.text
