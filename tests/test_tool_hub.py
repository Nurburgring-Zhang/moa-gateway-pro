"""Tests for the unified ToolHub (capability/tool_hub.py) and its wiring.

Covers:
- three-source aggregation without overwrite/loss + namespacing
- unified metadata + role gating (RBAC pattern)
- guardrail blocking of dangerous arguments
- real execution (local code_execute subprocess, MCP builtin handlers,
  external registry call path)
- AgentHarness tools_source="tool_hub"
- /v1/agent/run-loop include_mcp_tools
- MoA aggregator tool loop (real code_execute arithmetic + result feed-back,
  max_tool_rounds convergence, legacy no-tools path unchanged)
- /v1/moa/execute E2E with tools=["local__code_execute"]
- yaml workflow agent_loop step + builtin moa-tool-pipeline DAG
"""
from __future__ import annotations

import json
import re

import pytest

ADMIN_KEY = "tool-hub-admin-key-01"
MOA_KEY = "tool-hub-moa-key-01"

# Settings built inside tests don't load config.yaml, so MoA presets must be
# provided explicitly (mirrors the production "balanced" preset).
_MOA_CFG = {
    "enabled": True,
    "default_preset": "balanced",
    "reference_models": 2,
    "critic_rounds": 0,
    "presets": {
        "balanced": {
            "enabled": True,
            "strategy": "parallel",
            "reference_count": 2,
            "aggregator": "",
            "aggregator_tier": "standard",
            "tier": "standard",
            "critic_rounds": 0,
            "reference_temperature": 0.7,
            "aggregator_temperature": 0.3,
            "max_tokens": 2048,
        }
    },
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_tool_hub():
    """Reset the ToolHub singleton and external registry state per test."""
    import moa_gateway.capability.tool_hub as th
    from moa_gateway.mcp.external_registry import get_external_mcp_registry

    orig_hub = th._hub
    th._hub = None
    reg = get_external_mcp_registry()
    saved = (dict(reg._servers), dict(reg._discovered_tools), dict(reg._clients))
    yield
    th._hub = orig_hub
    reg._servers, reg._discovered_tools, reg._clients = saved


@pytest.fixture
def hub():
    """A fresh (non-singleton) ToolHub for unit tests."""
    from moa_gateway.capability.tool_hub import ToolHub

    return ToolHub()


@pytest.fixture
def mock_pool(monkeypatch):
    """Settings + model pool with one explicit mock-backed endpoint."""
    import moa_gateway.config as cfg
    import moa_gateway.model_pool as mp
    from moa_gateway.config import ModelEndpointConfig, Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "ToolHubP@ss!2024",
            "jwt_secret": "tool-hub-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [ADMIN_KEY],
        },
        ratelimit={"enabled": False},
        moa=_MOA_CFG,
        models=[
            ModelEndpointConfig(
                id="deepseek-v3",
                provider="deepseek",
                model="deepseek-v3",
                tier="standard",
                enabled=True,
            )
        ],
    )
    monkeypatch.setattr(cfg, "_settings", settings)
    monkeypatch.setattr(mp, "_pool", None)
    pool = mp.get_model_pool()
    yield pool
    monkeypatch.setattr(mp, "_pool", None)


@pytest.fixture
def hub_client(monkeypatch):
    """TestClient with one yaml admin key + one DB api key, mock model endpoint."""
    import moa_gateway.config as cfg
    import moa_gateway.storage as storage_mod
    from moa_gateway.config import ModelEndpointConfig, Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "ToolHubP@ss!2024",
            "jwt_secret": "tool-hub-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [ADMIN_KEY],
        },
        ratelimit={"enabled": False},
        models=[
            ModelEndpointConfig(
                id="deepseek-v3",
                provider="deepseek",
                model="deepseek-v3",
                tier="standard",
                enabled=True,
            )
        ],
    )
    monkeypatch.setattr(cfg, "_settings", settings)

    from fastapi.testclient import TestClient
    from moa_gateway.ha.graceful import graceful as _graceful
    from moa_gateway.server import create_app

    app = create_app()
    storage = storage_mod.get_storage()
    db_key = storage.create_api_key("hub-plain-key", quota_rpm=100, quota_daily_tokens=10_000)
    _graceful._shutting_down = False  # previous app teardown must not 503 this app
    with TestClient(app) as c:
        yield c, db_key["key"]


@pytest.fixture
def moa_e2e_client(monkeypatch):
    """TestClient for /v1/moa/execute E2E with a fresh MoA singleton."""
    import moa_gateway.config as cfg
    import moa_gateway.moa as moa_mod
    from moa_gateway.config import ModelEndpointConfig, Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "ToolHubP@ss!2024",
            "jwt_secret": "tool-hub-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [MOA_KEY],
        },
        ratelimit={"enabled": False},
        moa=_MOA_CFG,
        models=[
            ModelEndpointConfig(
                id="deepseek-v3",
                provider="deepseek",
                model="deepseek-v3",
                tier="standard",
                enabled=True,
            )
        ],
    )
    monkeypatch.setattr(cfg, "_settings", settings)
    orig_moa = moa_mod._moa
    moa_mod._moa = None

    from fastapi.testclient import TestClient
    from moa_gateway.ha.graceful import graceful as _graceful
    from moa_gateway.server import create_app

    _graceful._shutting_down = False  # previous app teardown must not 503 this app
    with TestClient(create_app()) as c:
        yield c
    moa_mod._moa = orig_moa


def _register_external_tool(server_name: str = "srv1", tool_name: str = "web_search"):
    """Register an external MCP server + one discovered tool (real registry)."""
    from moa_gateway.mcp.external_registry import (
        ExternalMCPServer,
        get_external_mcp_registry,
    )

    reg = get_external_mcp_registry()
    reg.register(
        ExternalMCPServer(
            name=server_name,
            transport="http",
            url="http://mcp.example.invalid/rpc",
        )
    )
    reg.add_discovered_tool(
        tool_name,
        server_name,
        {
            "name": tool_name,
            "description": "external search tool",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    )
    return reg


# ---------------------------------------------------------------------------
# Part 1: three-source aggregation, namespacing, metadata
# ---------------------------------------------------------------------------


def test_singleton_returns_same_instance():
    from moa_gateway.capability.tool_hub import get_tool_hub

    assert get_tool_hub() is get_tool_hub()


def test_local_tools_aggregated(hub):
    names = {s.name for s in hub.list_tools()}
    for raw in (
        "web_search",
        "code_execute",
        "file_read",
        "file_write",
        "file_list",
        "analyze_data",
        "api_verify",
    ):
        assert f"local__{raw}" in names


def test_mcp_builtin_tools_aggregated(hub):
    names = {s.name for s in hub.list_tools()}
    for raw in (
        "moa_list_models",
        "moa_check_quota",
        "moa_route_preview",
        "discover_free_models",
        "list_free_models",
        "apply_prompt_template",
        "apply_param_template",
        "run_agent_loop",
        "search_web",
    ):
        assert f"mcp__{raw}" in names


def test_three_sources_no_overwrite_no_loss(hub):
    """Same raw name across sources must coexist under distinct namespaces."""
    _register_external_tool("srv1", "web_search")  # collides with local web_search
    names = [s.name for s in hub.list_tools()]
    assert "local__web_search" in names
    assert "mcp__search_web" in names
    assert "external__srv1__web_search" in names
    # nothing lost, nothing overwritten
    assert len(names) == len(set(names))
    assert len(names) == 7 + 9 + 1


def test_all_names_namespaced(hub):
    for spec in hub.list_tools():
        assert spec.name.startswith(("local__", "mcp__", "external__"))


def test_unified_metadata_fields(hub):
    for spec in hub.list_tools():
        assert spec.name and spec.raw_name
        assert isinstance(spec.description, str) and spec.description
        assert isinstance(spec.parameters, dict)
        assert spec.allowed_roles and isinstance(spec.allowed_roles, frozenset)
        assert spec.source in ("local", "mcp", "external")
        d = spec.to_dict()
        for key in ("name", "description", "parameters", "allowed_roles", "source"):
            assert key in d
        schema = spec.to_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == spec.name


def test_local_dangerous_tools_privileged_only(hub):
    for raw in ("code_execute", "file_read", "file_write", "file_list", "api_verify"):
        spec = hub.get_spec(f"local__{raw}")
        assert spec is not None
        assert spec.allowed_roles == frozenset({"admin", "operator"})
    assert hub.get_spec("local__web_search").allowed_roles == frozenset(
        {"admin", "operator", "user"}
    )


def test_role_filter_user(hub):
    names = {s.name for s in hub.list_tools("user")}
    assert "local__web_search" in names
    assert "local__analyze_data" in names
    assert "mcp__search_web" in names
    assert "local__code_execute" not in names
    assert "mcp__moa_route_preview" not in names
    assert "mcp__run_agent_loop" not in names


def test_role_filter_readonly_sees_nothing(hub):
    assert hub.list_tools("readonly") == []


def test_admin_sees_everything(hub):
    assert len(hub.list_tools("admin")) == 7 + 9
    assert hub.tool_count("admin") == 16


# ---------------------------------------------------------------------------
# Part 2: execute — role gate, guardrails, real execution
# ---------------------------------------------------------------------------


async def test_execute_unknown_tool(hub):
    res = await hub.execute("local__does_not_exist", {}, "admin")
    assert res.success is False
    assert "Unknown tool" in res.error


async def test_execute_role_denied(hub):
    res = await hub.execute("local__code_execute", {"code": "print(1)"}, "user")
    assert res.success is False
    assert "permission denied" in res.error


async def test_execute_guardrails_blocks_rm_rf(hub):
    res = await hub.execute(
        "local__code_execute",
        {"code": "import os\nos.system('rm -rf /')"},
        "admin",
    )
    assert res.success is False
    assert "guardrails" in res.error


async def test_execute_guardrails_blocks_eval(hub):
    res = await hub.execute(
        "local__code_execute",
        {"code": "print(eval('1+1'))"},
        "admin",
    )
    assert res.success is False
    assert "guardrails" in res.error


async def test_execute_local_code_execute_real(hub):
    """Real sandboxed subprocess execution: 6*7 must come back as 42."""
    res = await hub.execute("local__code_execute", {"code": "print(6*7)"}, "admin")
    assert res.success is True
    assert "42" in res.output
    assert res.source == "local"
    assert res.latency_ms >= 0.0
    assert isinstance(res.usage, dict)


async def test_execute_mcp_tool_real_handler(hub):
    """Real MCP builtin handler (param template) through the hub."""
    res = await hub.execute("mcp__apply_param_template", {"task_type": "code_generation"}, "user")
    assert res.success is True
    assert res.source == "mcp"
    assert isinstance(res.data, dict)
    assert res.data.get("task_type") == "code_generation"
    assert res.data.get("params")


async def test_execute_mcp_role_denied(hub):
    res = await hub.execute("mcp__moa_route_preview", {"prompt": "hi"}, "user")
    assert res.success is False
    assert "permission denied" in res.error


async def test_execute_mcp_list_models_real_pool(hub):
    """moa_list_models touches the real model pool (empty here) without error."""
    res = await hub.execute("mcp__moa_list_models", {}, "user")
    assert res.success is True
    assert isinstance(res.data, dict)
    assert "models" in res.data


async def test_external_tool_synced_into_listing(hub):
    _register_external_tool("srv1", "web_search")
    names = {s.name for s in hub.list_tools()}
    assert "external__srv1__web_search" in names
    spec = hub.get_spec("external__srv1__web_search")
    assert spec.source == "external"
    assert spec.server == "srv1"
    assert spec.raw_name == "web_search"
    assert spec.parameters.get("required") == ["query"]


async def test_external_tool_execute_real_registry_path(hub):
    """Execution routes through ExternalMCPRegistry.call_tool (JSON-RPC client)."""
    reg = _register_external_tool("srv1", "web_search")

    class _FakeClient:
        connected = True

        def __init__(self):
            self.calls = []

        async def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            return {"content": [{"type": "text", "text": "ext-ok"}]}

        async def disconnect(self):
            pass

    fake = _FakeClient()
    reg._clients["srv1"] = fake

    res = await hub.execute("external__srv1__web_search", {"query": "hello"}, "admin")
    assert res.success is True
    assert fake.calls == [("web_search", {"query": "hello"})]
    assert res.source == "external"
    assert "ext-ok" in res.output


async def test_external_tool_denied_for_user_role(hub):
    _register_external_tool("srv1", "web_search")
    res = await hub.execute("external__srv1__web_search", {"query": "q"}, "user")
    assert res.success is False
    assert "permission denied" in res.error
    assert hub.list_tools("user") and not any(
        s.source == "external" for s in hub.list_tools("user")
    )


async def test_external_tool_removed_on_unregister(hub):
    reg = _register_external_tool("srv1", "web_search")
    assert hub.get_spec("external__srv1__web_search") is not None
    reg.unregister("srv1")
    assert hub.get_spec("external__srv1__web_search") is None


async def test_execute_not_connected_external_reports_error(hub):
    """No live client -> honest ConnectionError surfaced as failed ToolResult."""
    _register_external_tool("srv1", "web_search")
    res = await hub.execute("external__srv1__web_search", {"query": "q"}, "admin")
    assert res.success is False
    assert "not connected" in res.error


# ---------------------------------------------------------------------------
# Part 3: AgentHarness tools_source="tool_hub"
# ---------------------------------------------------------------------------


def test_harness_invalid_tools_source_raises():
    from moa_gateway.agent_loop import AgentHarness

    with pytest.raises(ValueError):
        AgentHarness(llm_call=None, tools_source="bogus")


async def test_harness_default_builtin_surface_unchanged():
    from moa_gateway.agent_loop import AgentHarness

    async def llm(messages, **params):
        return "Final Answer: ok"

    h = AgentHarness(llm_call=llm)
    assert h.tools_source == "builtin"
    await h.run(messages=[{"role": "user", "content": "hi"}], loop_name="react")
    assert h.list_tools() == []  # no hub tools leak into the default surface


async def test_harness_tool_hub_registers_role_filtered_admin():
    from moa_gateway.agent_loop import AgentHarness

    async def llm(messages, **params):
        return "Final Answer: ok"

    h = AgentHarness(llm_call=llm, tools_source="tool_hub", caller_role="admin")
    result = await h.run(
        messages=[{"role": "user", "content": "hi"}], loop_name="react", max_iterations=1
    )
    assert result.success is True
    tools = h.list_tools()
    assert "local__code_execute" in tools
    assert "mcp__search_web" in tools
    assert all(t.startswith(("local__", "mcp__", "external__")) for t in tools)


async def test_harness_tool_hub_user_role_excludes_dangerous():
    from moa_gateway.agent_loop import AgentHarness

    async def llm(messages, **params):
        return "Final Answer: ok"

    h = AgentHarness(llm_call=llm, tools_source="tool_hub", caller_role="user")
    await h.run(messages=[{"role": "user", "content": "hi"}], loop_name="react", max_iterations=1)
    tools = h.list_tools()
    assert "local__web_search" in tools
    assert "local__code_execute" not in tools
    assert "mcp__moa_route_preview" not in tools


async def test_harness_hub_tool_real_execution_in_react_loop():
    """ReAct loop drives a REAL code_execute through the hub (subprocess)."""
    from moa_gateway.agent_loop import AgentHarness

    responses = [
        'Thought: I should compute.\nAction: local__code_execute\nAction Input: {"code": "print(391)"}',
        "Thought: done\nFinal Answer: 391",
    ]

    async def llm(messages, **params):
        return responses.pop(0)

    h = AgentHarness(llm_call=llm, tools_source="tool_hub", caller_role="admin")
    result = await h.run(
        messages=[{"role": "user", "content": "compute"}],
        loop_name="react",
        max_iterations=5,
        hub_tools=["local__code_execute"],
    )
    assert result.success is True
    assert result.final_response == "391"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "local__code_execute"
    assert result.tool_results[0].success is True
    assert "391" in result.tool_results[0].output


async def test_harness_hub_tool_guardrail_failure_visible_in_loop():
    """A guardrail-blocked tool call surfaces as a failed observation, not a crash."""
    from moa_gateway.agent_loop import AgentHarness

    responses = [
        'Thought: run it\nAction: local__code_execute\nAction Input: {"code": "eval(\'1\')"}',
        "Thought: blocked\nFinal Answer: blocked",
    ]

    async def llm(messages, **params):
        return responses.pop(0)

    h = AgentHarness(llm_call=llm, tools_source="tool_hub", caller_role="admin")
    result = await h.run(
        messages=[{"role": "user", "content": "run"}],
        loop_name="react",
        max_iterations=5,
        hub_tools=["local__code_execute"],
    )
    assert result.success is True
    assert result.tool_results[0].success is False
    assert "guardrails" in result.tool_results[0].error


# ---------------------------------------------------------------------------
# Part 4: /v1/agent/run-loop include_mcp_tools
# ---------------------------------------------------------------------------


def test_run_loop_default_surface_unchanged(hub_client):
    client, _db_key = hub_client
    resp = client.post(
        "/v1/agent/run-loop",
        json={"messages": [{"role": "user", "content": "hi"}], "max_iterations": 1},
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text
    tools = resp.json()["tools_available"]
    # legacy bare local skill names, nothing namespaced
    assert "code_execute" in tools
    assert all(not t.startswith(("local__", "mcp__", "external__")) for t in tools)


def test_run_loop_include_mcp_tools_admin_surface(hub_client):
    client, _db_key = hub_client
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "max_iterations": 1,
            "include_mcp_tools": True,
        },
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text
    tools = resp.json()["tools_available"]
    assert "local__code_execute" in tools
    assert any(t.startswith("mcp__") for t in tools)
    assert "mcp__moa_list_models" in tools
    # legacy bare names are not part of the hub surface
    assert "code_execute" not in tools


def test_run_loop_include_mcp_tools_readonly_key_gets_no_tools(hub_client):
    client, db_key = hub_client
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "max_iterations": 1,
            "include_mcp_tools": True,
        },
        headers={"Authorization": f"Bearer {db_key}"},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tools_available"] == []


def test_run_loop_include_mcp_tools_denies_dangerous_for_plain_key(hub_client):
    client, db_key = hub_client
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "tools": ["code_execute"],
            "include_mcp_tools": True,
        },
        headers={"Authorization": f"Bearer {db_key}"},
        timeout=60,
    )
    assert resp.status_code == 403, resp.text
    assert "admin/operator" in resp.json()["detail"]


def test_run_loop_include_mcp_tools_admin_subset(hub_client):
    client, _db_key = hub_client
    resp = client.post(
        "/v1/agent/run-loop",
        json={
            "messages": [{"role": "user", "content": "hi"}],
            "tools": ["code_execute", "mcp__search_web"],
            "max_iterations": 1,
            "include_mcp_tools": True,
        },
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        timeout=60,
    )
    assert resp.status_code == 200, resp.text
    tools = resp.json()["tools_available"]
    assert tools == ["local__code_execute", "mcp__search_web"] or set(tools) == {
        "local__code_execute",
        "mcp__search_web",
    }


# ---------------------------------------------------------------------------
# Part 5: MoA aggregator tool loop
# ---------------------------------------------------------------------------


def _scripted_aggregator(monkeypatch, always_call_tool: bool = False):
    """Patch MoAOrchestrator._call_with_fallback with a scripted aggregator.

    First contact emits a real tool_calls JSON for local__code_execute
    (print(17*23)); once real tool results are fed back, the final answer is
    derived FROM the observed tool output (genuine result propagation).
    """
    from moa_gateway.moa import MoAOrchestrator

    state = {"calls": 0}

    async def fake_call_with_fallback(self, ep, messages, tools, temperature, max_tokens):
        state["calls"] += 1
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = str(m.get("content", ""))
                break
        if always_call_tool:
            content = json.dumps(
                {
                    "tool_calls": [
                        {
                            "name": "local__code_execute",
                            "arguments": {"code": "print(17*23)"},
                        }
                    ]
                },
                ensure_ascii=False,
            )
        elif "工具真实执行结果" in last_user:
            match = re.search(r'"output":\s*"([^"]*)"', last_user)
            observed = (match.group(1) if match else "").replace("\\n", "").strip()
            content = json.dumps(
                {"final_answer": f"计算结果: {observed}"}, ensure_ascii=False
            )
        else:
            content = json.dumps(
                {
                    "tool_calls": [
                        {
                            "name": "local__code_execute",
                            "arguments": {"code": "print(17*23)"},
                        }
                    ]
                },
                ensure_ascii=False,
            )
        return {
            "content": content,
            "cost": 0.0001,
            "latency_ms": 1.0,
            "used_model_id": ep.id,
            "fallback_used": False,
            "prompt_tokens": 5,
            "completion_tokens": 5,
            "provider": "mock",
        }

    monkeypatch.setattr(MoAOrchestrator, "_call_with_fallback", fake_call_with_fallback)
    return state


async def test_moa_tool_loop_real_code_execute_and_backfill(mock_pool, monkeypatch):
    """Core: aggregator requests code_execute, REAL subprocess computes 391,
    result is fed back, final answer contains 391, tool_trace recorded."""
    from moa_gateway.moa import MoAOrchestrator

    _scripted_aggregator(monkeypatch)
    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="用代码计算 17*23 等于多少",
        tools=["local__code_execute"],
        caller_role="admin",
        strategy="parallel",
        reference_count=2,
        critic_rounds=0,
    )
    assert result.tool_trace, "tool_trace must not be empty"
    entry = result.tool_trace[0]
    assert entry["tool"] == "local__code_execute"
    assert entry["success"] is True
    assert entry["args"] == {"code": "print(17*23)"}
    assert "391" in entry["result_summary"]
    assert entry["latency_ms"] >= 0.0
    assert "391" in result.final_content
    assert result.metadata.get("tool_loop") == "executed"
    assert result.metadata.get("tool_rounds", 0) >= 1


async def test_moa_tool_trace_fields_complete(mock_pool, monkeypatch):
    from moa_gateway.moa import MoAOrchestrator

    _scripted_aggregator(monkeypatch)
    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="用代码计算 17*23",
        tools=["local__code_execute"],
        caller_role="admin",
        strategy="parallel",
        reference_count=1,
        critic_rounds=0,
    )
    assert result.tool_trace
    for key in ("round", "tool", "args", "success", "result_summary", "latency_ms", "source"):
        assert key in result.tool_trace[0]
    assert result.tool_trace[0]["source"] == "local"
    d = result.to_dict()
    assert "tool_trace" in d and d["tool_trace"] == result.tool_trace


async def test_moa_without_tools_zero_change(mock_pool):
    """Legacy path (no tools) must behave exactly as before: no loop, no trace."""
    from moa_gateway.moa import MoAOrchestrator

    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="什么是人工智能?",
        strategy="parallel",
        reference_count=2,
        critic_rounds=0,
    )
    assert result.tool_trace == []
    assert "tool_loop" not in result.metadata
    assert result.final_content  # mock provider aggregation still happens
    assert result.references


async def test_moa_dict_tools_only_no_loop(mock_pool):
    """OpenAI-style schema dicts remain pure pass-through (no tool loop)."""
    from moa_gateway.moa import MoAOrchestrator

    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="什么是人工智能?",
        tools=[{"type": "function", "function": {"name": "x", "parameters": {}}}],
        strategy="parallel",
        reference_count=1,
        critic_rounds=0,
    )
    assert result.tool_trace == []
    assert "tool_loop" not in result.metadata


async def test_moa_max_tool_rounds_convergence(mock_pool, monkeypatch):
    """An aggregator that never stops calling tools must be cut off at the cap."""
    from moa_gateway.moa import MoAOrchestrator

    _scripted_aggregator(monkeypatch, always_call_tool=True)
    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="用代码计算 17*23",
        tools=["local__code_execute"],
        caller_role="admin",
        strategy="parallel",
        reference_count=1,
        critic_rounds=0,
        max_tool_rounds=2,
    )
    assert len(result.tool_trace) == 2  # one real execution per round, then cut off
    assert result.metadata.get("tool_loop_exhausted") is True
    assert result.metadata.get("tool_rounds") == 2


async def test_moa_unknown_tool_recorded_not_executed(mock_pool):
    from moa_gateway.moa import MoAOrchestrator

    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="hi",
        tools=["local__definitely_not_a_tool"],
        caller_role="admin",
        strategy="parallel",
        reference_count=1,
        critic_rounds=0,
    )
    assert result.tool_trace == []
    assert result.metadata.get("tool_loop") == "no_tools_resolved"
    assert "local__definitely_not_a_tool" in result.metadata.get("tool_loop_unknown", [])


async def test_moa_role_denied_tool_not_executed(mock_pool):
    """caller_role=user cannot drive local__code_execute — recorded, not run."""
    from moa_gateway.moa import MoAOrchestrator

    orch = MoAOrchestrator(model_pool=mock_pool)
    result = await orch.execute(
        query="用代码计算 17*23",
        tools=["local__code_execute"],
        caller_role="user",
        strategy="parallel",
        reference_count=1,
        critic_rounds=0,
    )
    assert result.tool_trace == []
    assert result.metadata.get("tool_loop") == "no_tools_resolved"
    assert "local__code_execute" in result.metadata.get("tool_loop_denied", [])


def test_parse_tool_loop_output_variants():
    from moa_gateway.moa import MoAOrchestrator

    parse = MoAOrchestrator._parse_tool_loop_output
    kind, payload = parse(
        '{"tool_calls": [{"name": "local__code_execute", "arguments": {"code": "print(1)"}}]}'
    )
    assert kind == "tool_calls" and payload[0]["name"] == "local__code_execute"

    kind, payload = parse('```json\n{"final_answer": "42"}\n```')
    assert kind == "final" and payload == "42"

    assert parse("plain prose")[0] == "final"
    assert parse('{"tool_calls": []}')[0] == "final"  # empty list converges
    assert parse("") == ("final", "")


# ---------------------------------------------------------------------------
# Part 6: /v1/moa/execute E2E with tools=["local__code_execute"]
# ---------------------------------------------------------------------------


def test_moa_execute_endpoint_tool_trace_e2e(moa_e2e_client, monkeypatch):
    """E2E: POST /v1/moa/execute with tools=["local__code_execute"],
    task "用代码计算 17*23" -> tool_trace non-empty, answer contains 391."""
    _scripted_aggregator(monkeypatch)
    resp = moa_e2e_client.post(
        "/v1/moa/execute",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "用代码计算 17*23 等于多少"}],
            "tools": ["local__code_execute"],
            "strategy": "parallel",
            "reference_count": 2,
            "critic_rounds": 0,
        },
        headers={"Authorization": f"Bearer {MOA_KEY}"},
        timeout=120,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tool_trace"], "tool_trace must be non-empty"
    assert data["tool_trace"][0]["tool"] == "local__code_execute"
    assert data["tool_trace"][0]["success"] is True
    assert "391" in data["tool_trace"][0]["result_summary"]
    assert "391" in data["final_content"]


def test_moa_execute_endpoint_no_tools_unchanged(moa_e2e_client):
    resp = moa_e2e_client.post(
        "/v1/moa/execute",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "什么是人工智能?"}],
            "strategy": "parallel",
            "reference_count": 1,
            "critic_rounds": 0,
        },
        headers={"Authorization": f"Bearer {MOA_KEY}"},
        timeout=120,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["tool_trace"] == []
    assert data["final_content"]


# ---------------------------------------------------------------------------
# Part 7: yaml workflow agent_loop step + builtin DAG
# ---------------------------------------------------------------------------


def test_yaml_agent_loop_step_type_accepted():
    from moa_gateway.workflows.yaml_workflow import VALID_STEP_TYPES, WorkflowYAML

    assert "agent_loop" in VALID_STEP_TYPES
    wf = WorkflowYAML(
        """
name: agent-step-parse
steps:
  - id: s1
    type: agent_loop
    inputs:
      loop_name: react
      prompt: "hi"
"""
    )
    assert wf.steps[0].type == "agent_loop"


async def test_yaml_agent_loop_step_real_execution(mock_pool):
    """agent_loop step runs a REAL harness+loop against the model pool."""
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    wf = WorkflowYAML(
        """
name: agent-step-run
steps:
  - id: s1
    type: agent_loop
    inputs:
      loop_name: react
      prompt: "Reply with a short answer about AI"
      max_iterations: 2
    outputs:
      - answer
"""
    )
    res = await wf.execute({})
    assert res["success"] is True, res
    out = res["outputs"]["steps.s1.output"]
    assert isinstance(out, str) and out.strip()


async def test_yaml_agent_loop_result_propagation(mock_pool):
    """Output of an agent_loop step feeds the next step via {{ }} template."""
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    wf = WorkflowYAML(
        """
name: agent-step-propagate
steps:
  - id: s1
    type: agent_loop
    inputs:
      loop_name: react
      prompt: "Say something"
      max_iterations: 1
    outputs:
      - answer
  - id: s2
    type: transform
    depends_on: [s1]
    inputs:
      template: "VERDICT::{{steps.s1.output}}"
    outputs:
      - final
"""
    )
    res = await wf.execute({})
    assert res["success"] is True, res
    s1_out = res["outputs"]["steps.s1.output"]
    assert res["outputs"]["steps.s2.output"] == f"VERDICT::{s1_out}"


async def test_yaml_agent_loop_invalid_loop_name_fails():
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    wf = WorkflowYAML(
        """
name: agent-step-bad-loop
steps:
  - id: s1
    type: agent_loop
    inputs:
      loop_name: bogus
      prompt: "hi"
"""
    )
    res = await wf.execute({})
    assert res["success"] is False
    assert "s1" in res["error"]


async def test_yaml_agent_loop_hub_namespaced_tools(mock_pool):
    """Namespaced tool names route the step through the ToolHub."""
    from moa_gateway.workflows.yaml_workflow import WorkflowYAML

    wf = WorkflowYAML(
        """
name: agent-step-hub-tools
steps:
  - id: s1
    type: agent_loop
    inputs:
      loop_name: react
      tools: ["local__code_execute"]
      prompt: "Reply directly"
      max_iterations: 1
    outputs:
      - answer
"""
    )
    res = await wf.execute({})
    assert res["success"] is True, res


async def test_yaml_moa_step_forwards_tools(monkeypatch):
    """moa step must forward tools/max_tool_rounds to /v1/moa/execute."""
    import moa_gateway.workflows.yaml_workflow as yw

    captured = {}

    async def fake_post(url, body):
        captured["url"] = url
        captured["body"] = body
        return {"final_content": "ok", "aggregated_content": "ok"}

    monkeypatch.setattr(yw, "_http_post", fake_post)
    wf = yw.WorkflowYAML(
        """
name: moa-tools-forward
steps:
  - id: m1
    type: moa
    inputs:
      prompt: "compute"
      tools: ["local__code_execute"]
      max_tool_rounds: 3
    outputs:
      - analysis
"""
    )
    res = await wf.execute({})
    assert res["success"] is True, res
    assert captured["url"].endswith("/v1/moa/execute")
    assert captured["body"]["tools"] == ["local__code_execute"]
    assert captured["body"]["max_tool_rounds"] == 3


def test_builtin_moa_tool_pipeline_dag():
    """workflows/builtin/moa-tool-pipeline.yaml: moa(tools) -> agent_loop."""
    from moa_gateway.workflows.workflow_loader import WorkflowLoader

    wf = WorkflowLoader().get_workflow("moa-tool-pipeline")
    assert wf is not None
    step_map = {s.id: s for s in wf.steps}
    assert step_map["analyze"].type == "moa"
    assert step_map["analyze"].inputs["tools"] == ["local__code_execute"]
    assert step_map["verify"].type == "agent_loop"
    assert step_map["verify"].depends_on == ["analyze"]
    assert step_map["verify"].inputs["loop_name"] == "react"
    # DAG order: analyze before verify
    order = wf._topological_sort()
    assert order.index("analyze") < order.index("verify")
