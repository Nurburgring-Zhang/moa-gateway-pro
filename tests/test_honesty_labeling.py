"""Honesty-labeling regression (v3.2.1 S4).

Policy: every synthetic/simulated result must carry an explicit mock flag —
not just a text prefix. The CLI channel chain (subagent/CLI/API channels)
is fully simulated today; its results must be labeled ``mock=True`` at the
result, chain, and orchestrator-consumer levels.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("MOA_ADMIN_PASSWORD", "Audit#2026StrongPwd!")
os.environ.setdefault("MOA_GATEWAY_KEY", "gw-test-key-audit-20260814")
os.environ.setdefault("MOA_JWT_SECRET", "audit-jwt-secret-0123456789abcdef0123456789abcdef")


class TestChannelMockLabeling:
    def test_subagent_channel_labeled_mock(self):
        from moa_gateway.capability.channels import SubagentChannel

        res = asyncio.run(SubagentChannel().execute("some question?"))
        assert res.success is True
        assert res.mock is True, "synthetic subagent answer must be mock-labeled"

    def test_cli_channel_labeled_mock(self):
        from moa_gateway.capability.channels import CLIChannel

        res = asyncio.run(CLIChannel().execute("do a thing"))
        assert res.success is True
        assert res.mock is True, "simulated CLI output must be mock-labeled"

    def test_api_channel_labeled_mock(self):
        from moa_gateway.capability.channels import APIChannel

        res = asyncio.run(APIChannel().execute("final fallback"))
        assert res.success is True
        assert res.mock is True, "simulated API answer must be mock-labeled"

    def test_chain_aggregates_mock_flag(self):
        from moa_gateway.capability.channels import ChannelChain

        out = asyncio.run(ChannelChain().execute("chain query"))
        assert out["mock"] is True
        assert out["result"].mock is True

    def test_failed_attempt_keeps_mock_false_ok(self):
        """Failures are honest already (no fabricated content); mock flag
        must simply be present and boolean in serialized attempts."""
        from moa_gateway.capability.channels import (
            APIChannel,
            ChannelChain,
            ChannelError,
            CLIChannel,
            ChannelType,
            SubagentChannel,
        )

        chain = ChannelChain(
            [
                SubagentChannel(fail_rate=1.0),
                CLIChannel(fail_kind="timeout"),
                APIChannel(fail_kind="cli"),
            ]
        )
        with pytest.raises(ChannelError) as ei:
            asyncio.run(chain.execute("always fails"))
        serialized = ei.value.to_dict()
        for attempt in serialized["attempts"]:
            assert isinstance(attempt["mock"], bool)

    def test_orchestrator_cli_step_propagates_mock(self):
        from moa_gateway.orchestrator.executor import Executor

        cap = type("Cap", (), {"invoke": {"channel": "ch1"}, "name": "cli.ch1"})()
        result = asyncio.run(Executor()._exec_cli(cap, {"query": "hello chain"}))
        assert result["ok"] is True
        assert result["mock"] is True, "orchestrator must surface the mock flag"


class TestWebSearchHonestyDocstring:
    def test_no_mock_in_degradation_chain_doc(self):
        import inspect

        from moa_gateway.agent_loop.skills.web_search import web_search

        doc = inspect.getdoc(web_search)
        assert "-> Mock" not in doc, "docstring must not promise a mock fallback"
        assert "honest failure" in doc


class TestChatFailureMetricHonesty:
    def test_failed_chat_records_5xx(self, monkeypatch):
        """v3.2.1 S5: a failing provider call must increment the chat
        counter with a 5xx label (previously success-only accounting meant
        the chat error-rate alert could never fire)."""
        import pytest as _pytest
        from prometheus_client import REGISTRY

        import moa_gateway.routes.chat as chat_mod
        from fastapi import HTTPException
        from moa_gateway.providers.base import ProviderError
        from moa_gateway.routes.chat import ChatCompletionRequest, chat_completions

        model = "metric-test-model"

        class _FakePool:
            endpoints = {model: object()}  # endpoints keyed by model id

            async def call(self, *a, **k):
                raise ProviderError("simulated provider outage", status=502)

        monkeypatch.setattr(chat_mod, "get_model_pool", lambda: _FakePool())

        before = REGISTRY.get_sample_value(
            "moa_chat_requests_total", {"model": model, "status": "5xx"}
        ) or 0.0

        req = ChatCompletionRequest(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            stream=False,
        )
        key_info = {
            "key_id": "metric-test-key",
            "role": "readonly",
            "quota_rpm": 60,
            "quota_daily_tokens": 100000,
        }
        with _pytest.raises(HTTPException) as ei:
            asyncio.run(chat_completions(req, key_info))
        assert ei.value.status_code == 502

        after = REGISTRY.get_sample_value(
            "moa_chat_requests_total", {"model": model, "status": "5xx"}
        ) or 0.0
        assert after == before + 1, "5xx chat failure must be recorded"


class TestPgDialectUpsertRouting:
    def test_login_attempts_upsert_is_dialect_aware(self, monkeypatch, tmp_path):
        """v3.2.1 S10: the login brute-force counter must emit PG-compatible
        SQL when the dual backend is PostgreSQL (INSERT OR REPLACE is
        SQLite-only and previously raised a syntax error there)."""
        import re
        from contextlib import contextmanager

        import moa_gateway.routes.auth as auth_mod

        executed: list[tuple[str, tuple]] = []

        class _FakeCursor:
            def execute(self, sql, params=()):
                executed.append((sql, params))
                return self

            def fetchone(self):
                return None

        class _FakeEngine:
            is_sqlite = False
            is_postgres = True

        class _FakeStorage:
            _engine = _FakeEngine()

            @contextmanager
            def conn(self):
                yield _FakeCursor()

        monkeypatch.setattr(auth_mod, "get_storage", lambda: _FakeStorage())

        import asyncio

        # get_client_ip is a dependency — call the login coroutine with a stub
        async def _run():
            return await auth_mod.login(
                auth_mod.LoginRequest(username="nobody", password="whatever"), "1.2.3.4"
            )

        from fastapi import HTTPException

        try:
            asyncio.run(_run())
        except HTTPException:
            pass  # 401 from unknown user is fine — upsert happens before auth

        upserts = [sql for sql, _ in executed if "INSERT" in sql and "login_attempts" in sql]
        assert upserts, "login attempt upsert must have run"
        sql = upserts[0]
        assert "INSERT OR REPLACE" not in sql, "PG path must not receive SQLite syntax"
        assert "ON CONFLICT(ip) DO UPDATE" in sql
        assert re.search(r"VALUES \(\?, 1, \?\)", sql)

    def test_sqlite_branch_unchanged(self, monkeypatch):
        """SQLite path keeps INSERT OR REPLACE (regression guard)."""
        from contextlib import contextmanager

        import moa_gateway.routes.auth as auth_mod
        import asyncio
        from fastapi import HTTPException

        executed: list[tuple[str, tuple]] = []

        class _FakeCursor:
            def execute(self, sql, params=()):
                executed.append((sql, params))
                return self

            def fetchone(self):
                return None

        class _FakeEngine:
            is_sqlite = True
            is_postgres = False

        class _FakeStorage:
            _engine = _FakeEngine()

            @contextmanager
            def conn(self):
                yield _FakeCursor()

        monkeypatch.setattr(auth_mod, "get_storage", lambda: _FakeStorage())

        async def _run():
            return await auth_mod.login(
                auth_mod.LoginRequest(username="nobody", password="whatever"), "1.2.3.4"
            )

        try:
            asyncio.run(_run())
        except HTTPException:
            pass
        upserts = [sql for sql, _ in executed if "INSERT OR REPLACE" in sql]
        assert upserts, "SQLite path must keep INSERT OR REPLACE"
