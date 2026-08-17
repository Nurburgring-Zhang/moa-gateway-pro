"""Wave B1 verification tests.

Covers:
- T1.1 (D1): audit HMAC signing chain activated via settings.auth.jwt_secret
- T1.2 (D11): config.yaml encoding integrity (UTF-8, no PUA, no CR corruption)
- T1.3 (D9): /v1/moa/tri-review accounts tokens via limiter.incr_tokens
- T1.4 (D8): agent file skills sandboxed to data/agent_sandbox
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- T1.1 (D1)
def test_audit_signing_initialized_from_jwt_secret(tmp_path, make_settings):
    """setup_audit_logging must pick up settings.auth.jwt_secret (was broken: settings.jwt_secret)."""
    import moa_gateway.audit as audit_mod

    settings = make_settings(jwt_secret="b1-hmac-secret-for-signing-chain-test")

    # Reset signing state + handlers
    orig_secret = audit_mod._audit_secret
    orig_prev = audit_mod._prev_signature
    orig_handlers = list(audit_mod.audit_logger.handlers)
    audit_mod.audit_logger.handlers.clear()
    audit_mod._audit_secret = None
    audit_mod._prev_signature = ""
    try:
        with patch("moa_gateway.config.get_settings", return_value=settings):
            audit_mod.setup_audit_logging(str(tmp_path / "audit.jsonl"))
        assert audit_mod._audit_secret == "b1-hmac-secret-for-signing-chain-test"

        # Emit two events -> signature chain must verify
        ev1 = audit_mod.AuditEvent(action="t1", actor_id="u", actor_role="admin", resource="x")
        ev2 = audit_mod.AuditEvent(action="t2", actor_id="u", actor_role="admin", resource="x")
        audit_mod.log_audit(ev1)
        audit_mod.log_audit(ev2)

        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
        records = [json.loads(ln) for ln in lines if '"action": "t' in ln]
        assert len(records) == 2
        r1, r2 = records
        assert "sig" in r1 and "sig" in r2
        assert r1["prev_sig"] == ""
        assert r2["prev_sig"] == r1["sig"]
        assert audit_mod.verify_signature(r1, audit_mod._audit_secret, r1["sig"], "")
        assert audit_mod.verify_signature(r2, audit_mod._audit_secret, r2["sig"], r1["sig"])
        # Tamper detection
        r1_tampered = dict(r1)
        r1_tampered["result"] = "forged"
        assert not audit_mod.verify_signature(
            r1_tampered, audit_mod._audit_secret, r1["sig"], ""
        )
    finally:
        audit_mod._audit_secret = orig_secret
        audit_mod._prev_signature = orig_prev
        for h in list(audit_mod.audit_logger.handlers):
            h.close()
        audit_mod.audit_logger.handlers.clear()
        for h in orig_handlers:
            audit_mod.audit_logger.addHandler(h)


# ---------------------------------------------------------------- T1.2 (D11)
def test_config_yaml_encoding_integrity():
    """config.yaml must be clean UTF-8 without PUA chars / CR corruption / mojibake."""
    import re

    cfg_path = PROJECT_ROOT / "config.yaml"
    raw = cfg_path.read_bytes()
    text = raw.decode("utf-8")  # must decode cleanly
    assert "\r" not in text, "corrupted line endings remain"
    pua = re.findall(r"[\ue000-\uf8ff]", text)
    assert not pua, f"PUA chars remain: {pua[:5]}"
    for marker in ["\u94b9\u4f72\u6523", "\u535a\u58df", "\u9ee8\u6a3f", "\u63ba\u5d87"]:
        assert marker not in text, f"mojibake marker remains: {marker!r}"
    data = yaml.safe_load(text)
    assert data["server"]["port"] == 8910
    assert len(data["models"]) == 22
    assert data["auth"]["admin_username"] == "admin"


# ---------------------------------------------------------------- T1.3 (D9)
@pytest.mark.asyncio
async def test_tri_review_accounts_tokens():
    """tri-review must call limiter.incr_tokens with real usage after execution."""
    import moa_gateway.routes.moa as moa_routes
    from moa_gateway.moa import MoAResult, ReferenceResult
    from moa_gateway.routes.chat import ChatCompletionRequest, ChatMessage

    result = MoAResult(
        request_id="r1",
        query="q",
        preset="tri_model_review",
        strategy="parallel",
        final_content="final answer " * 20,
    )
    result.references = [
        ReferenceResult(
            model_id=f"m{i}", content="x" * 100, success=True,
            prompt_tokens=100, completion_tokens=200,
        )
        for i in range(3)
    ]
    result.critics = []

    limiter = MagicMock()
    limiter.check_and_incr.return_value = (1, 60, 0, 5_000_000)
    moa_mock = MagicMock()
    moa_mock.execute = AsyncMock(return_value=result)

    req = ChatCompletionRequest(
        model="auto", messages=[ChatMessage(role="user", content="hello tri review")]
    )

    with (
        patch.object(moa_routes, "get_limiter", return_value=limiter),
        patch.object(moa_routes, "get_moa", return_value=moa_mock),
    ):
        resp = await moa_routes.moa_tri_review(req, key_info={"key_id": "k1"})

    assert resp["request_id"] == "r1"
    limiter.incr_tokens.assert_called_once()
    tokens = limiter.incr_tokens.call_args.args[1]
    # 3 refs x (100+200) = 900 + input approx + output approx
    assert tokens >= 900


@pytest.mark.asyncio
async def test_tri_review_accounting_rejection_does_not_drop_result():
    """If quota accounting raises 429 AFTER execution, the result must still be returned."""
    from fastapi import HTTPException

    import moa_gateway.routes.moa as moa_routes
    from moa_gateway.moa import MoAResult
    from moa_gateway.routes.chat import ChatCompletionRequest, ChatMessage

    result = MoAResult(
        request_id="r2", query="q", preset="tri_model_review", strategy="parallel",
        final_content="done",
    )
    limiter = MagicMock()
    limiter.check_and_incr.return_value = (1, 60, 0, 5_000_000)
    limiter.incr_tokens.side_effect = HTTPException(429, "daily token limit")
    moa_mock = MagicMock()
    moa_mock.execute = AsyncMock(return_value=result)

    req = ChatCompletionRequest(
        model="auto", messages=[ChatMessage(role="user", content="q")]
    )
    with (
        patch.object(moa_routes, "get_limiter", return_value=limiter),
        patch.object(moa_routes, "get_moa", return_value=moa_mock),
    ):
        resp = await moa_routes.moa_tri_review(req, key_info={"key_id": "k1"})
    assert resp["request_id"] == "r2"  # result not dropped despite 429 accounting


# ---------------------------------------------------------------- T1.4 (D8)
def test_agent_sandbox_default_is_data_dir(tmp_path, monkeypatch):
    """file_ops sandbox default must be DATA_DIR/agent_sandbox, not cwd."""
    monkeypatch.delenv("AGENT_SANDBOX_ROOT", raising=False)
    # conftest patches moa_gateway.config.DATA_DIR -> tmp_path; reload to rebind
    import moa_gateway.agent_loop.skills.file_ops as fo

    orig_root = fo._SANDBOX_ROOT
    importlib.reload(fo)
    try:
        assert fo._SANDBOX_ROOT == str(tmp_path / "agent_sandbox")
        assert Path(fo._SANDBOX_ROOT).is_dir(), "sandbox dir must be auto-created"

        # Inside sandbox -> OK
        inner = tmp_path / "agent_sandbox" / "note.txt"
        inner.write_text("hi", encoding="utf-8")
        assert fo._validate_path(str(inner)) == inner.resolve()

        # Escape attempts -> PermissionError
        with pytest.raises(PermissionError):
            fo._validate_path(str(tmp_path / "outside.txt"))
        with pytest.raises(PermissionError):
            fo._validate_path(str(inner) + "/../../escape.txt")
    finally:
        fo._SANDBOX_ROOT = orig_root


@pytest.mark.asyncio
async def test_agent_file_ops_sandboxed_e2e(tmp_path, monkeypatch):
    """file_read/file_write must reject paths outside sandbox (runtime behavior)."""
    monkeypatch.delenv("AGENT_SANDBOX_ROOT", raising=False)
    import moa_gateway.agent_loop.skills.file_ops as fo

    orig_root = fo._SANDBOX_ROOT
    importlib.reload(fo)
    try:
        out = await fo.file_write(str(tmp_path / "evil.txt"), "payload")
        assert out.startswith("Error:")
        assert not (tmp_path / "evil.txt").exists()

        ok = await fo.file_write("inside.txt", "payload")
        assert "Wrote" in ok
        assert (tmp_path / "agent_sandbox" / "inside.txt").read_text(encoding="utf-8") == "payload"

        content = await fo.file_read("inside.txt")
        assert content == "payload"
    finally:
        fo._SANDBOX_ROOT = orig_root
