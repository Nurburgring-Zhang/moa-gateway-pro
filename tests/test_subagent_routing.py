"""M9 subagent routing tests (OpenClacky port).

Covers: fork-prefix detection, task classification, lite-model registry
(default tables + runtime registration + OpenClacky resolution semantics),
forbidden-tools filtering, the pure route_subagent_request decision,
[SUBAGENT SUMMARY] folding, bounded transcripts, cost merging, tool
registration on a real ToolExecutor, and the /v1/subagent HTTP surface.

Route tests build their own FastAPI app with only the subagent router
included (no dependency_overrides; auth via settings gateway keys).
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.subagent_routing import (
    INVOKE_LITE_SUBAGENT_TOOL,
    CostLedger,
    LiteModelRegistry,
    SubagentContext,
    classify_task_category,
    detect_fork_prefix,
    extract_subagent_transcript,
    filter_forbidden_tools,
    fold_subagent_result,
    forbidden_notice,
    generate_subagent_summary,
    get_lite_registry,
    invoke_lite_subagent,
    is_tool_allowed,
    register_subagent_tools,
    route_subagent_request,
    set_subagent_runner,
)

API_KEY = "subagent-test-key-0001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def api_key(monkeypatch):
    """Real auth path: register the test key in isolated settings. The
    Storage bootstrap additionally requires a strong admin password."""
    monkeypatch.setenv("MOA_ADMIN_PASSWORD", "SubAgentTestP@ss99!")
    from moa_gateway.config import get_settings

    get_settings().auth.gateway_api_keys.append(API_KEY)


@pytest.fixture
async def client():
    from moa_gateway.routes.subagent import router

    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _clear_runner():
    """Runner is a process-wide seam; tests install their own and clear it."""
    yield
    set_subagent_runner(None)


# ─────────────────────────── fork prefix ───────────────────────────


class TestForkPrefix:
    def test_slash_fork_prefix(self):
        is_fork, cleaned = detect_fork_prefix("/fork analyze the logs")
        assert is_fork is True
        assert cleaned == "analyze the logs"

    def test_colon_fork_prefix(self):
        is_fork, cleaned = detect_fork_prefix("fork:summarize repo")
        assert is_fork is True
        assert cleaned == "summarize repo"

    def test_no_prefix_stays_inline(self):
        is_fork, cleaned = detect_fork_prefix("fix the fork handling")
        assert is_fork is False
        assert cleaned == "fix the fork handling"

    def test_prefix_must_lead_and_word_boundary(self):
        assert detect_fork_prefix("/forkable thing")[0] is False
        assert detect_fork_prefix("please /fork now")[0] is False

    def test_bare_prefix_yields_empty_task(self):
        is_fork, cleaned = detect_fork_prefix("/fork")
        assert is_fork is True
        assert cleaned == ""

    def test_custom_prefixes_override(self):
        # A plain word prefix followed by whitespace is a valid fork
        # (the whitespace is the separator); the defaults are simply not used.
        is_fork, cleaned = detect_fork_prefix("@sub run it", prefixes=("@sub",))
        assert (is_fork, cleaned) == (True, "run it")
        is_fork, cleaned = detect_fork_prefix("@sub:run it", prefixes=("@sub:",))
        assert (is_fork, cleaned) == (True, "run it")
        # And a default prefix no longer matches under a custom list.
        assert detect_fork_prefix("/fork x", prefixes=("@sub",))[0] is False


class TestClassification:
    def test_read_only(self):
        assert classify_task_category("read the config and list errors") == "read_only"

    def test_write_wins_over_read(self):
        assert classify_task_category("read the file, then fix and edit the bug") == "write"

    def test_general_fallback(self):
        assert classify_task_category("do the thing") == "general"


# ─────────────────────────── lite registry ───────────────────────────


class TestLiteRegistry:
    def test_default_pairing_hit(self):
        reg = LiteModelRegistry()
        assert reg.resolve("openclacky", "abs-claude-opus-5") == "abs-claude-haiku-4-5"
        assert reg.resolve("qwen", "qwen3.7-max") == "qwen3.6-flash"

    def test_unlisted_primary_with_table_is_already_lite(self):
        reg = LiteModelRegistry()
        # haiku is not a KEY in the table -> already lite-class -> None,
        # never falling through to a global field (OpenClacky semantics).
        assert reg.resolve("openclacky", "abs-claude-haiku-4-5") is None

    def test_global_lite_fallback(self):
        reg = LiteModelRegistry()
        # deepseek preset ships a provider-wide lite_model only.
        assert reg.resolve("deepseek", "deepseek-v4-pro") == "deepseek-v4-flash"

    def test_unknown_provider_none(self):
        assert LiteModelRegistry().resolve("nope", "x") is None

    def test_runtime_registration_api(self):
        reg = LiteModelRegistry(lite_tables={}, global_lite={})
        reg.register_pair("mypool", "endpoint-a-max", "endpoint-a-flash")
        assert reg.resolve("mypool", "endpoint-a-max") == "endpoint-a-flash"
        assert reg.unregister_pair("mypool", "endpoint-a-max") is True
        assert reg.resolve("mypool", "endpoint-a-max") is None
        reg.register_provider("mypool", lite_model="pool-flash")
        assert reg.resolve("mypool", "anything") == "pool-flash"

    def test_process_registry_has_openclacky_defaults(self):
        assert get_lite_registry().resolve("openrouter", "openai/gpt-5.5") == "openai/gpt-5.4-mini"


# ─────────────────────────── forbidden tools ───────────────────────────


class TestForbiddenTools:
    def test_filter_names_and_definitions(self):
        tools = [
            "read_file",
            {"type": "function", "function": {"name": "write_file"}},
            {"name": "execute_shell"},
        ]
        kept = filter_forbidden_tools(tools, ["write_file", "execute_shell"])
        assert kept == ["read_file"]

    def test_empty_forbidden_returns_copy(self):
        tools = ["a", "b"]
        kept = filter_forbidden_tools(tools, [])
        assert kept == tools and kept is not tools

    def test_notice_text(self):
        assert forbidden_notice([]) == ""
        notice = forbidden_notice(["write_file"])
        assert "`write_file`" in notice and "disabled" in notice

    def test_runtime_guard(self):
        assert is_tool_allowed("read_file", ["write_file"]) is True
        assert is_tool_allowed("write_file", ["write_file"]) is False


# ─────────────────────────── routing decision ───────────────────────────


class TestRouteDecision:
    def test_inline_default_behavior_unchanged(self):
        ctx = SubagentContext(primary_model="m1", available_tools=["t1"])
        d = route_subagent_request("regular task", ctx)
        assert d.route == "inline" and d.forked is False
        assert d.model == "m1" and d.tools == ["t1"]
        assert d.instructions is None

    def test_fork_with_lite_mapping(self):
        ctx = SubagentContext(
            primary_model="abs-claude-opus-5",
            provider_id="openclacky",
            available_tools=["read_file", "write_file"],
            forbidden_tools=["write_file"],
            requested_model="lite",
        )
        d = route_subagent_request("/fork summarize docs", ctx)
        assert d.forked is True and d.route == "fork"
        assert d.task == "summarize docs"
        assert d.model == "abs-claude-haiku-4-5"
        assert d.model_source == "lite_mapping"
        assert d.tools == ["read_file"]  # forbidden filtered
        assert "1 forbidden tool(s) filtered" in d.reason
        assert "FORKED SUBAGENT MODE" in d.instructions
        assert "`write_file`" in d.instructions

    def test_lite_without_pairing_falls_back_to_primary(self):
        ctx = SubagentContext(
            primary_model="abs-claude-haiku-4-5",
            provider_id="openclacky",
            requested_model="lite",
        )
        d = route_subagent_request("/fork quick check", ctx)
        assert d.model == "abs-claude-haiku-4-5"
        assert d.model_source == "primary"
        assert "no lite pairing" in d.reason

    def test_explicit_model_override(self):
        ctx = SubagentContext(primary_model="m1", requested_model="custom-model")
        d = route_subagent_request("/fork task", ctx)
        assert d.model == "custom-model" and d.model_source == "override"

    def test_requested_model_alone_forks_without_prefix(self):
        ctx = SubagentContext(primary_model="m1", requested_model="lite")
        d = route_subagent_request("task without prefix", ctx)
        assert d.forked is True

    def test_budget_overrides(self):
        ctx = SubagentContext(max_iterations=5, max_output_tokens=512, requested_model="lite")
        d = route_subagent_request("/fork task", ctx)
        assert d.budget == {"max_iterations": 5, "max_output_tokens": 512}

    def test_to_dict_serialisable(self):
        d = route_subagent_request("/fork x", SubagentContext(requested_model="lite"))
        payload = d.to_dict()
        assert json.loads(json.dumps(payload)) == payload


# ─────────────────────────── summary fold & costs ───────────────────────────


def _subagent_trail():
    """Realistic post-fork trail: scaffolding + tool round + final answer."""
    return [
        {"role": "user", "content": "fork notice", "system_injected": True,
         "subagent_instructions": True},
        {"role": "assistant", "content": "ack", "system_injected": True},
        {"role": "user", "content": "actual task"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": {"p": "x"}}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "file body"},
        {"role": "assistant", "content": "Found 3 errors in module X."},
    ]


class TestSummaryFold:
    def test_summary_format(self):
        trail = _subagent_trail()
        text = generate_subagent_summary(trail[2:], iterations=4, total_cost=0.01234)
        assert text.startswith("[SUBAGENT SUMMARY]")
        assert "Completed in 4 iterations, cost: $0.0123" in text
        assert "Tools used: read_file" in text
        assert "Found 3 errors in module X." in text

    def test_fold_replaces_instructions_message(self):
        parent = [
            {"role": "user", "content": "please delegate"},
            {"role": "user", "content": "fork notice", "system_injected": True,
             "subagent_instructions": True},
        ]
        folded = fold_subagent_result(parent, _subagent_trail()[2:], iterations=2, total_cost=0.5)
        assert len(folded) == 2
        replaced = folded[1]
        assert replaced["subagent_summary"] is True
        assert "subagent_instructions" not in replaced
        assert "[SUBAGENT SUMMARY]" in replaced["content"]
        # Inputs never mutated.
        assert parent[1]["subagent_instructions"] is True

    def test_fold_appends_when_no_instructions_message(self):
        folded = fold_subagent_result([{"role": "user", "content": "hi"}], _subagent_trail()[2:])
        assert len(folded) == 2
        assert folded[-1]["subagent_summary"] is True

    def test_transcript_bounded_and_scaffolding_dropped(self):
        trail = _subagent_trail()
        events = extract_subagent_transcript(trail, parent_message_count=2)
        assert all(not e.get("system_injected") for e in events)
        roles = [e["role"] for e in events]
        assert roles == ["user", "assistant", "tool", "assistant"]
        assert events[1]["tool_calls"][0]["name"] == "read_file"

    def test_transcript_evicts_oldest_with_marker(self):
        msgs = [{"role": "user", "content": f"msg {i}"} for i in range(10)]
        events = extract_subagent_transcript(msgs, max_events=3, max_bytes=10_000)
        assert events[0]["role"] == "system"
        assert "7 earlier event(s) omitted" in events[0]["content"]
        assert [e["content"] for e in events[1:]] == ["msg 7", "msg 8", "msg 9"]

    def test_cost_ledger_merges_subagent_spend(self):
        ledger = CostLedger(cost_usd=1.0)
        ledger.absorb_subagent_cost(0.25, iterations=3, model="haiku")
        ledger.absorb_subagent_cost(-5)  # negative spend clamped to 0
        assert ledger.total_cost_usd == 1.25
        d = ledger.to_dict()
        assert d["subagent_runs"] == 2
        assert d["per_model_cost"] == {"haiku": 0.25}


# ─────────────────────────── tool registration ───────────────────────────


class TestToolRegistration:
    def test_registers_on_real_tool_executor(self):
        from moa_gateway.agent_loop.base import ToolExecutor

        executor = ToolExecutor()
        names = register_subagent_tools(executor)
        assert names == [INVOKE_LITE_SUBAGENT_TOOL]
        assert INVOKE_LITE_SUBAGENT_TOOL in executor.list_tools()
        specs = executor.tool_specs()
        assert any(s["name"] == INVOKE_LITE_SUBAGENT_TOOL and s["description"] for s in specs)

    def test_registers_via_agent_loop_property(self):
        from moa_gateway.agent_loop.base import AgentLoop, ToolExecutor

        class _Loop(AgentLoop):  # test boundary: minimal concrete loop
            async def run(self, messages, context=None):
                raise NotImplementedError

        loop = _Loop(ToolExecutor())
        register_subagent_tools(loop)
        assert INVOKE_LITE_SUBAGENT_TOOL in loop.tool_executor.list_tools()

    def test_rejects_non_executor_harness(self):
        with pytest.raises(TypeError):
            register_subagent_tools(object())

    async def test_tool_dry_run_without_runner(self):
        set_subagent_runner(None)
        out = await invoke_lite_subagent(
            task="check the logs",
            provider_id="openclacky",
            primary_model="abs-claude-opus-5",
            available_tools=["read_file"],
        )
        payload = json.loads(out)
        assert payload["executed"] is False
        assert payload["route"] == "fork"
        assert payload["model"] == "abs-claude-haiku-4-5"
        assert "no subagent runner" in payload["detail"]

    async def test_tool_executes_through_registered_runner(self):
        seen: dict = {}

        async def runner(task, decision):
            seen["task"] = task
            seen["model"] = decision.model
            seen["tools"] = decision.tools
            return "subagent final answer"

        set_subagent_runner(runner)
        out = await invoke_lite_subagent(
            task="/fork count files",
            primary_model="openai/gpt-5.5",
            provider_id="openrouter",
            forbidden_tools=["write_file"],
            available_tools=["read_file", "write_file"],
        )
        payload = json.loads(out)
        assert payload["executed"] is True
        assert payload["output"] == "subagent final answer"
        assert payload["model"] == "openai/gpt-5.4-mini"  # default -> lite pairing
        assert seen["task"] == "count files"
        assert seen["tools"] == ["read_file"]

    async def test_tool_rejects_empty_task(self):
        with pytest.raises(ValueError):
            await invoke_lite_subagent(task="   ")

    async def test_tool_runs_via_executor_dispatch(self):
        """End-to-end through ToolExecutor.execute (handler(**arguments))."""
        from moa_gateway.agent_loop.base import ToolCall, ToolExecutor

        async def runner(task, decision):
            return "done"

        set_subagent_runner(runner)
        executor = ToolExecutor()
        register_subagent_tools(executor)
        call = ToolCall(
            name=INVOKE_LITE_SUBAGENT_TOOL,
            arguments={"task": "/fork ping", "primary_model": "m", "provider_id": "p"},
        )
        result = await executor.execute(call)
        assert result.success is True
        payload = json.loads(result.output)
        assert payload["executed"] is True


# ─────────────────────────── HTTP routes ───────────────────────────


class TestSubagentRoutes:
    async def test_config_requires_auth(self, client):
        r = await client.get("/v1/subagent/config")
        assert r.status_code == 401

    async def test_config_shape(self, client, api_key):
        r = await client.get("/v1/subagent/config", headers=AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["fork_prefixes"] == ["/fork", "fork:"]
        assert body["default_budget"]["max_iterations"] > 0
        assert "openclacky" in body["providers"]
        assert body["lite_tables"]["openclacky"]["abs-claude-opus-5"] == "abs-claude-haiku-4-5"
        assert "runner_registered" in body

    async def test_route_dry_run_fork(self, client, api_key):
        r = await client.post(
            "/v1/subagent/route",
            json={
                "task": "/fork summarize the repo",
                "primary_model": "abs-claude-sonnet-5",
                "provider_id": "openclacky",
                "requested_model": "lite",
                "available_tools": ["read_file", "write_file"],
                "forbidden_tools": ["write_file"],
            },
            headers=AUTH,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["dry_run"] is True
        assert body["route"] == "fork"
        assert body["model"] == "abs-claude-haiku-4-5"
        assert body["tools"] == ["read_file"]
        assert body["instructions"]

    async def test_route_inline_default(self, client, api_key):
        r = await client.post(
            "/v1/subagent/route",
            json={"task": "ordinary request", "primary_model": "m"},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["route"] == "inline"

    async def test_route_capability_gated(self, client, api_key, monkeypatch):
        import moa_gateway.capability_toggles as toggles

        # Test boundary: disable the gating capability in the in-memory cache.
        cache = dict(toggles.DEFAULT_CAPABILITIES)
        cache["function_call"] = False
        monkeypatch.setattr(toggles, "_cache", cache)
        r = await client.get("/v1/subagent/config", headers=AUTH)
        assert r.status_code == 503
