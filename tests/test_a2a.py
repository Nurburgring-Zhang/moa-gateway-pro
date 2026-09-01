"""tests/test_a2a.py — M5 A2A layer: Agent Card + JSON-RPC 2.0 + five real skills.

Covers moa_gateway/a2a/* and moa_gateway/routes/a2a.py (ported from OmniRoute,
https://github.com/diegosouzapw/OmniRoute, MIT license).

The app is self-built per the frozen M5 architecture contract:
    app = FastAPI(); app.include_router(moa_gateway.routes.a2a.router)
so these tests never depend on moa_gateway/server.py or routes/__init__.py.

Zero test doubles on the real path: the chat pipeline runs through the
gateway's own D6 explicit MockProvider (a keyless endpoint, labeled
provider="mock" by the gateway itself) — that is the gateway's explicit-mock
policy, not a test fake. The ONLY controlled doubles in this file are the two
routing-advice boundary fixtures, needed because moa_gateway.routing_strategies
(M1) is delivered in parallel by another agent; both are marked
[CONTROLLED TEST DOUBLE — BOUNDARY] at their point of use.
"""
from __future__ import annotations

import hashlib
import importlib
import sys
import types

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.a2a.skills import sanitize_outbound, scrub_url_credentials
from moa_gateway.a2a.task_manager import (
    A2ATaskManager,
    InvalidTransitionError,
    TaskTransitionError,
    reset_task_manager,
)
from moa_gateway.a2a.protocol import normalize_messages

API_KEY = "a2a-test-key-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
SECRET_VALUE = "sk-live-SUPERSECRET42"

EXPECTED_SKILL_IDS = {
    "chat-completion",
    "model-list",
    "health",
    "routing-advice",
    "cache-insight",
}


# ============ Fixtures ============


@pytest.fixture
def gateway_settings(monkeypatch):
    """Isolated Settings: one keyless endpoint (real D6 MockProvider path)
    plus one keyed endpoint carrying a credential that must NEVER leak."""
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        models=[
            {
                "id": "test-lite",
                "provider": "openai",
                "model": "gpt-4o-mini",
                "tier": "lite",
                "enabled": True,
            },
            {
                "id": "secret-ep",
                "provider": "openai",
                "model": "gpt-4o",
                "tier": "standard",
                "enabled": True,
                "api_key": SECRET_VALUE,
            },
        ],
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    return settings


@pytest.fixture(autouse=True)
def _isolate_a2a_singletons():
    """Reset A2A-specific process singletons per test (conftest handles the
    shared ones: config/storage/pool/router/cache/toggles)."""
    from moa_gateway.ha import health_checker as ha_checker

    reset_task_manager()
    ha_checker._checks.clear()
    ha_checker._results.clear()
    ha_checker.mark_not_ready()
    yield
    reset_task_manager()
    ha_checker._checks.clear()
    ha_checker._results.clear()
    ha_checker.mark_not_ready()


@pytest.fixture
def app(gateway_settings):
    """Self-built app per the M5 contract (no server.py involvement)."""
    from moa_gateway.routes.a2a import router

    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://a2a.test") as ac:
        yield ac


def _owner_hash() -> str:
    """Owner the route derives for a yaml-config gateway key."""
    return hashlib.sha256(b"yaml:yaml").hexdigest()[:32]


async def rpc(client, payload):
    return await client.post("/v1/a2a", json=payload, headers=AUTH)


def invoke(client, skill, **params):
    body = {"jsonrpc": "2.0", "id": 1, "method": "skills/invoke",
            "params": {"skill": skill, **params}}
    return body


# ============ Agent Card (GET /.well-known/agent.json) ============


class TestAgentCard:
    async def test_card_public_and_core_fields(self, client, gateway_settings):
        """Agent Card is public (A2A discovery semantics) and carries the real
        A2AConfig values + A2A v0.3 shape."""
        resp = await client.get("/.well-known/agent.json")  # no auth header
        assert resp.status_code == 200
        card = resp.json()
        assert card["name"] == "moa-gateway-pro"
        assert card["version"] == "4.2.0"
        assert card["protocolVersion"] == "0.3.0"
        assert card["url"].endswith("/v1/a2a")
        assert isinstance(card["description"], str) and card["description"]
        assert card["capabilities"]["streaming"] is False
        assert card["capabilities"]["pushNotifications"] is False
        assert card["capabilities"]["stateTransitionHistory"] is True
        assert card["defaultInputModes"] == ["text"]
        assert card["defaultOutputModes"] == ["text"]
        assert card["authentication"]["schemes"] == ["bearer"]

    async def test_card_skills_are_the_five_real_skills(self, client):
        resp = await client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        skills = resp.json()["skills"]
        assert len(skills) == 5
        assert {s["id"] for s in skills} == EXPECTED_SKILL_IDS
        for s in skills:
            assert s["name"] and s["description"]
            assert isinstance(s["tags"], list) and s["tags"]
            assert isinstance(s["examples"], list) and s["examples"]

    async def test_card_runtime_extension_reflects_live_state(self, client):
        """x-gateway must carry REAL runtime state: toggles + pool snapshot."""
        resp = await client.get("/.well-known/agent.json")
        assert resp.status_code == 200
        ext = resp.json()["x-gateway"]
        assert isinstance(ext["gateway_version"], str) and ext["gateway_version"]
        assert ext["agent_enabled"] is True
        assert ext["capability_toggles"]["a2a"] is True
        assert ext["model_pool"]["total"] == 2  # test-lite + secret-ep
        assert ext["model_pool"]["enabled"] == 2
        assert "OmniRoute" in ext["attribution"]
        # credentials never leave the gateway, even in the card extension
        assert SECRET_VALUE not in resp.text

    async def test_card_cache_control_header(self, client):
        resp = await client.get("/.well-known/agent.json")
        assert resp.headers.get("cache-control") == "public, max-age=3600"

    async def test_card_503_when_capability_disabled(self, client):
        from moa_gateway.capability_toggles import set_enabled

        set_enabled("a2a", False)
        resp = await client.get("/.well-known/agent.json")
        assert resp.status_code == 503
        assert "a2a" in resp.json()["detail"]

    async def test_rpc_503_when_capability_disabled(self, client):
        from moa_gateway.capability_toggles import set_enabled

        set_enabled("a2a", False)
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "skills/list"})
        assert resp.status_code == 503


# ============ Auth on POST /v1/a2a ============


class TestAuth:
    async def test_rpc_requires_api_key(self, client):
        resp = await client.post(
            "/v1/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "skills/list"}
        )
        assert resp.status_code == 401

    async def test_rpc_rejects_wrong_key(self, client):
        resp = await client.post(
            "/v1/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "skills/list"},
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


# ============ JSON-RPC 2.0 protocol envelope ============


class TestProtocolEnvelope:
    async def test_parse_error_32700_on_invalid_json(self, client):
        resp = await client.post(
            "/v1/a2a", content=b"{definitely-not-json", headers=AUTH
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["id"] is None
        assert body["error"]["code"] == -32700
        assert "Parse error" in body["error"]["message"]

    async def test_invalid_request_32600_bad_jsonrpc_version(self, client):
        resp = await rpc(client, {"jsonrpc": "1.0", "id": 9, "method": "skills/list"})
        assert resp.status_code == 400
        err = resp.json()["error"]
        assert err["code"] == -32600
        assert resp.json()["id"] == 9

    async def test_invalid_request_32600_missing_method(self, client):
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 10})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32600

    async def test_method_not_found_32601(self, client):
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 7, "method": "bogus/method"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["id"] == 7
        assert body["error"]["code"] == -32601
        assert "bogus/method" in body["error"]["message"]

    async def test_notification_gets_204_no_body(self, client):
        resp = await rpc(client, {"jsonrpc": "2.0", "method": "skills/list"})  # no id
        assert resp.status_code == 204
        assert resp.content == b""

    async def test_batch_mixed_requests(self, client):
        batch = [
            {"jsonrpc": "2.0", "id": 1, "method": "skills/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "no/such"},
            {"jsonrpc": "2.0", "method": "skills/list"},  # notification: no reply
        ]
        resp = await rpc(client, batch)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list) and len(body) == 2
        by_id = {item["id"]: item for item in body}
        assert "result" in by_id[1]
        assert by_id[2]["error"]["code"] == -32601

    async def test_batch_empty_32600(self, client):
        resp = await rpc(client, [])
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32600

    async def test_batch_non_object_element(self, client):
        resp = await rpc(client, ["just-a-string"])
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["error"]["code"] == -32600
        assert body[0]["id"] is None


# ============ skills/list ============


class TestSkillsList:
    async def test_skills_list_returns_five_real_skills(self, client):
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 1, "method": "skills/list"})
        assert resp.status_code == 200
        skills = resp.json()["result"]["skills"]
        assert {s["id"] for s in skills} == EXPECTED_SKILL_IDS
        for s in skills:
            assert s["description"] and s["tags"] and s["examples"]


# ============ Skill: chat-completion (real chat pipeline) ============


class TestChatCompletionSkill:
    async def test_auto_routing_real_pipeline(self, client):
        """auto -> IntelligentRouter -> ModelPool.call -> D6 MockProvider."""
        resp = await rpc(
            client,
            invoke(
                client,
                "chat-completion",
                messages=[{"role": "user", "content": "hi"}],
            ),
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["task"]["state"] == "completed"
        content = result["artifacts"][0]["content"]
        assert isinstance(content, str) and content.strip()
        meta = result["metadata"]
        assert meta["provider"] == "mock"  # D6 explicit-mock label, never silent
        assert meta["model"] == "test-lite"
        assert meta["finish_reason"] == "stop"
        assert meta["prompt_tokens"] >= 1
        assert meta["completion_tokens"] >= 1
        assert meta["cached"] is False
        assert meta["routing"]["primary"] == "test-lite"
        # real side effect: the pool endpoint actually served one call
        from moa_gateway.model_pool import get_model_pool

        assert get_model_pool().endpoints["test-lite"].total_calls == 1

    async def test_explicit_model(self, client):
        resp = await rpc(
            client,
            invoke(
                client,
                "chat-completion",
                messages=[{"role": "user", "content": "hello"}],
                metadata={"model": "test-lite"},
            ),
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["metadata"]["model"] == "test-lite"
        assert result["artifacts"][0]["content"].strip()

    async def test_cache_hit_on_second_identical_call(self, client):
        """Second identical call must be served by the real response cache."""
        payload = invoke(
            client,
            "chat-completion",
            messages=[{"role": "user", "content": "cache me please"}],
        )
        first = await rpc(client, payload)
        assert first.json()["result"]["metadata"]["cached"] is False
        second = await rpc(client, payload)
        meta = second.json()["result"]["metadata"]
        assert meta["cached"] is True
        assert meta["cache_layer"] == "l1_exact"

    async def test_unknown_model_fails_task_persisted(self, client):
        resp = await rpc(
            client,
            invoke(
                client,
                "chat-completion",
                messages=[{"role": "user", "content": "hi"}],
                metadata={"model": "ghost-model"},
            ),
        )
        assert resp.status_code == 500
        err = resp.json()["error"]
        assert err["code"] == -32603
        assert "ghost-model" in err["message"]
        task_id = err["data"]["task_id"]
        # the failed task is persisted with an error artifact
        got = await rpc(client, {"jsonrpc": "2.0", "id": 2, "method": "tasks/get",
                                 "params": {"id": task_id}})
        task = got.json()["result"]["task"]
        assert task["state"] == "failed"
        assert task["artifacts"][0]["type"] == "error"


# ============ Skill: model-list (real runtime pool) ============


class TestModelListSkill:
    async def test_model_list_real_pool(self, client):
        resp = await rpc(client, invoke(client, "model-list",
                                        messages=[{"role": "user", "content": "list"}]))
        assert resp.status_code == 200
        meta = resp.json()["result"]["metadata"]
        ids = {e["id"] for e in meta["endpoints"]}
        assert ids == {"test-lite", "secret-ep"}
        lite = next(e for e in meta["endpoints"] if e["id"] == "test-lite")
        assert lite["provider"] == "openai"
        assert lite["model"] == "gpt-4o-mini"
        assert lite["tier"] == "lite"
        assert lite["enabled"] is True
        assert meta["snapshot"]["total"] == 2
        assert meta["presets"] == ["auto", "fast", "balanced", "quality", "pipeline"]
        assert "Model pool: 2 endpoint(s)" in resp.json()["result"]["artifacts"][0]["content"]

    async def test_model_list_never_leaks_credentials(self, client):
        """PII hard rule: gateway credentials never ride along in A2A output."""
        resp = await rpc(client, invoke(client, "model-list",
                                        messages=[{"role": "user", "content": "list"}]))
        assert resp.status_code == 200
        assert SECRET_VALUE not in resp.text
        for e in resp.json()["result"]["metadata"]["endpoints"]:
            assert "api_key" not in e


# ============ Skill: health (real health modules) ============


class TestHealthSkill:
    async def test_health_reports_genuine_not_ready_state(self, client):
        """Fresh checker (startup incomplete) -> status must be not_ready,
        never a prettied-up constant."""
        resp = await rpc(client, invoke(client, "health",
                                        messages=[{"role": "user", "content": "health?"}]))
        assert resp.status_code == 200
        meta = resp.json()["result"]["metadata"]
        assert meta["status"] == "not_ready"
        assert meta["readiness"]["status"] == "not_ready"
        assert meta["liveness"]["status"] == "alive"
        assert meta["pool_snapshot"]["total"] == 2
        assert meta["degraded_endpoints"] == []

    async def test_health_ready_then_degraded_transitions(self, client):
        from moa_gateway.ha import health_checker as ha_checker
        from moa_gateway.model_pool import get_model_pool

        ha_checker.mark_ready()
        resp = await rpc(client, invoke(client, "health",
                                        messages=[{"role": "user", "content": "health?"}]))
        assert resp.json()["result"]["metadata"]["status"] == "healthy"

        # real degraded transition: one endpoint goes unhealthy
        get_model_pool().endpoints["secret-ep"].health_status = "unhealthy"
        resp = await rpc(client, invoke(client, "health",
                                        messages=[{"role": "user", "content": "health?"}]))
        meta = resp.json()["result"]["metadata"]
        assert meta["status"] == "degraded"
        assert meta["degraded_endpoints"] == ["secret-ep"]
        assert "secret-ep" in resp.json()["result"]["artifacts"][0]["content"]


# ============ Skill: routing-advice (real router + lazy strategy engine) ============


class TestRoutingAdviceSkill:
    async def test_module_not_ready_structured_error(self, client, monkeypatch):
        """When moa_gateway.routing_strategies (M1, parallel delivery) is not
        importable, the skill must return a structured module_not_ready status
        plus the REAL IntelligentRouter decision — never fabricated strategies.

        [CONTROLLED TEST DOUBLE — BOUNDARY] importlib.import_module is wrapped
        to raise ImportError for exactly one module name, simulating "M1 not
        merged yet" deterministically regardless of parallel-agent progress.
        All other imports delegate to the real function.
        """
        real_import_module = importlib.import_module

        def fake_import_module(name, *args, **kwargs):
            if name == "moa_gateway.routing_strategies":
                raise ImportError("No module named 'moa_gateway.routing_strategies'")
            return real_import_module(name, *args, **kwargs)

        monkeypatch.setattr(importlib, "import_module", fake_import_module)

        resp = await rpc(
            client,
            invoke(client, "routing-advice",
                   messages=[{"role": "user", "content": "hi"}]),
        )
        assert resp.status_code == 200
        meta = resp.json()["result"]["metadata"]
        engine = meta["strategy_engine"]
        assert engine["status"] == "module_not_ready"
        assert engine["module"] == "moa_gateway.routing_strategies"
        assert "No module named" in engine["detail"]
        assert engine["requested_strategy"] == "auto"
        # the router decision is still REAL
        assert meta["router_decision"]["primary"] == "test-lite"
        assert meta["complexity"] in {"trivial", "simple", "medium", "complex", "expert"}
        assert resp.json()["result"]["task"]["state"] == "completed"

    async def test_engine_ready_introspected_and_ranker_run(self, client, monkeypatch):
        """With the strategy engine importable, the skill introspects its real
        registry and runs its ranker for real against the live pool.

        [CONTROLLED TEST DOUBLE — BOUNDARY] a stub module stands in for
        moa_gateway.routing_strategies until M1 lands (or overrides it
        deterministically while M1 is in flight). It mimics the registry +
        ranker entry points the skill probes for; the skill code under test is
        fully real.
        """
        stub = types.ModuleType("moa_gateway.routing_strategies")
        stub.get_registry = lambda: {
            "latency-first": object(),
            "cost-first": object(),
            "balanced": object(),
        }
        stub.rank_endpoints = lambda strategy, endpoints: {
            "strategy": strategy,
            "ranking": sorted(endpoints.keys()),
        }
        monkeypatch.setitem(sys.modules, "moa_gateway.routing_strategies", stub)

        resp = await rpc(
            client,
            invoke(client, "routing-advice",
                   messages=[{"role": "user", "content": "design a microservice"}],
                   metadata={"strategy": "cost-first"}),
        )
        assert resp.status_code == 200
        engine = resp.json()["result"]["metadata"]["strategy_engine"]
        assert engine["status"] == "ready"
        assert engine["strategies"] == ["balanced", "cost-first", "latency-first"]
        assert engine["count"] == 3
        assert engine["requested_strategy"] == "cost-first"
        assert engine["ranker"] == "rank_endpoints"
        assert engine["ranked"]["strategy"] == "cost-first"
        assert engine["ranked"]["ranking"] == ["secret-ep", "test-lite"]


# ============ Skill: cache-insight (real cache telemetry) ============


class TestCacheInsightSkill:
    async def test_cache_insight_reflects_real_traffic(self, client):
        """Generate genuine cache traffic through the real CacheManager, then
        verify the skill reports exactly those numbers."""
        from moa_gateway.cache.manager import get_cache_manager

        mgr = get_cache_manager()
        msgs = [{"role": "user", "content": "a2a cache probe"}]
        assert await mgr.get(msgs, "test-lite", temperature=0.6, max_tokens=4096) is None
        await mgr.set(msgs, "test-lite", {"ok": True},
                      temperature=0.6, max_tokens=4096)
        hit = await mgr.get(msgs, "test-lite", temperature=0.6, max_tokens=4096)
        assert hit is not None and hit["layer"] == "l1_exact"

        resp = await rpc(client, invoke(client, "cache-insight",
                                        messages=[{"role": "user", "content": "stats"}]))
        assert resp.status_code == 200
        meta = resp.json()["result"]["metadata"]
        assert meta["enabled"] is True
        stats = meta["stats"]
        assert stats["total_requests"] >= 2
        assert stats["total_hits"] >= 1
        assert stats["hit_rate_pct"] > 0
        assert stats["hits_by_layer"].get("l1_exact", 0) >= 1
        assert meta["layers"]["l1_exact"]["entries"] >= 1
        assert meta["layers"]["l3_redis"]["available"] is False  # no Redis in tests
        assert "hit_rate=" in resp.json()["result"]["artifacts"][0]["content"]


# ============ tasks/get, tasks/cancel ============


class TestTaskMethods:
    async def test_tasks_get_roundtrip(self, client):
        done = await rpc(client, invoke(client, "model-list",
                                        messages=[{"role": "user", "content": "list"}]))
        task_id = done.json()["result"]["task"]["id"]
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 3, "method": "tasks/get",
                                  "params": {"id": task_id}})
        assert resp.status_code == 200
        task = resp.json()["result"]["task"]
        assert task["id"] == task_id
        assert task["skill"] == "model-list"
        assert task["state"] == "completed"
        states = [e["state"] for e in task["events"]]
        assert states == ["submitted", "working", "completed"]
        assert task["createdAt"] and task["expiresAt"]

    async def test_tasks_get_unknown_32601(self, client):
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 4, "method": "tasks/get",
                                  "params": {"id": "no-such-task"}})
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == -32601

    async def test_tasks_get_missing_id_32602(self, client):
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 5, "method": "tasks/get",
                                  "params": {}})
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32602

    async def test_tasks_cancel_terminal_task_32603(self, client):
        done = await rpc(client, invoke(client, "model-list",
                                        messages=[{"role": "user", "content": "list"}]))
        task_id = done.json()["result"]["task"]["id"]
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 6, "method": "tasks/cancel",
                                  "params": {"id": task_id}})
        assert resp.status_code == 500
        err = resp.json()["error"]
        assert err["code"] == -32603
        assert "Invalid transition" in err["message"]

    async def test_tasks_cancel_active_task_success(self, client):
        """Cancel a live (submitted) task owned by this key's owner hash."""
        from moa_gateway.a2a.task_manager import get_task_manager

        task = get_task_manager().create_task(
            "model-list", [{"role": "user", "content": "pending"}], owner=_owner_hash()
        )
        resp = await rpc(client, {"jsonrpc": "2.0", "id": 7, "method": "tasks/cancel",
                                  "params": {"id": task.id}})
        assert resp.status_code == 200
        assert resp.json()["result"]["task"] == {"id": task.id, "state": "cancelled"}
        got = await rpc(client, {"jsonrpc": "2.0", "id": 8, "method": "tasks/get",
                                 "params": {"id": task.id}})
        assert got.json()["result"]["task"]["state"] == "cancelled"


# ============ Owner scoping / IDOR hardening (GHSA-jcm5-6wpp-wjj8 port) ============


class TestOwnerScoping:
    async def test_http_task_owner_hash(self, client):
        from moa_gateway.a2a.task_manager import get_task_manager

        done = await rpc(client, invoke(client, "model-list",
                                        messages=[{"role": "user", "content": "list"}]))
        task_id = done.json()["result"]["task"]["id"]
        task = get_task_manager().get_task(task_id, _owner_hash())
        assert task is not None
        assert task.owner == _owner_hash()
        assert len(task.owner) == 32
        assert API_KEY not in task.owner
        assert SECRET_VALUE not in task.owner

    def test_owner_isolation_unit(self, gateway_settings):
        """A task owned by alice is invisible/uncancellable to bob; not-found
        errors are indistinguishable from missing tasks (no IDOR enumeration)."""
        manager = A2ATaskManager()
        task = manager.create_task(
            "health", [{"role": "user", "content": "x"}], owner="alice"
        )
        assert manager.get_task(task.id, "alice") is not None
        assert manager.get_task(task.id, "bob") is None  # invisible
        with pytest.raises(TaskTransitionError):
            manager.cancel_task(task.id, "bob")  # uncancellable by bob
        assert manager.get_task(task.id, "bob") is None
        cancelled = manager.cancel_task(task.id, "alice")
        assert cancelled.state == "cancelled"

    def test_ownerless_task_visible_to_everyone(self, gateway_settings):
        manager = A2ATaskManager()
        task = manager.create_task("health", [{"role": "user", "content": "x"}])
        assert manager.get_task(task.id, "anyone").id == task.id
        assert manager.get_task(task.id, None).id == task.id


# ============ Persistence + task lifecycle ============


class TestPersistenceAndLifecycle:
    async def test_tasks_persist_across_manager_instances(self, client):
        """tasks/get works beyond the in-process singleton: a brand-new
        manager instance (fresh memory) reads the task from the DB."""
        done = await rpc(client, invoke(client, "model-list",
                                        messages=[{"role": "user", "content": "list"}]))
        task_id = done.json()["result"]["task"]["id"]
        fresh = A2ATaskManager()
        task = fresh.get_task(task_id, _owner_hash())
        assert task is not None
        assert task.state == "completed"
        assert task.skill == "model-list"

    def test_lazy_ttl_expiry_marks_task_failed(self, gateway_settings):
        """Expired non-terminal tasks fail lazily on next read (OmniRoute TTL)."""
        manager = A2ATaskManager(ttl_minutes=-0.5)  # expires in the past
        task = manager.create_task("health", [{"role": "user", "content": "x"}])
        current = manager.get_task(task.id)
        assert current is not None
        assert current.state == "failed"
        assert current.events[-1]["message"] == "Task expired"

    def test_state_machine_enforced(self, gateway_settings):
        manager = A2ATaskManager()
        task = manager.create_task("health", [{"role": "user", "content": "x"}])
        with pytest.raises(InvalidTransitionError):
            manager.update_task(task.id, "completed")  # submitted -> completed invalid
        manager.update_task(task.id, "working")
        manager.update_task(task.id, "completed")
        with pytest.raises(InvalidTransitionError):
            manager.update_task(task.id, "working")  # terminal state
        with pytest.raises(TaskTransitionError):
            manager.update_task("missing-id", "working")

    async def test_task_stats_counts(self, client):
        from moa_gateway.a2a.task_manager import get_task_manager

        await rpc(client, invoke(client, "model-list",
                                 messages=[{"role": "user", "content": "list"}]))
        stats = get_task_manager().get_stats()
        assert stats["total"] >= 1
        assert stats["counts"]["completed"] >= 1
        assert stats["lastTaskAt"]


# ============ message/send ============


class TestMessageSend:
    async def test_message_send_parts_form_defaults_to_chat(self, client):
        resp = await rpc(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "message/send",
             "params": {"message": {"parts": ["Hello ", "gateway"]}}},
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["task"]["skill"] == "chat-completion"
        assert result["task"]["state"] == "completed"
        assert result["artifacts"][0]["content"].strip()
        assert result["metadata"]["provider"] == "mock"

    async def test_message_send_invalid_message_32602(self, client):
        resp = await rpc(
            client,
            {"jsonrpc": "2.0", "id": 2, "method": "message/send",
             "params": {"message": {}}},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32602

    async def test_message_send_skill_override(self, client):
        resp = await rpc(
            client,
            {"jsonrpc": "2.0", "id": 3, "method": "message/send",
             "params": {"message": {"content": "which models?"},
                        "metadata": {"skill": "model-list"}}},
        )
        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["task"]["skill"] == "model-list"
        assert result["metadata"]["snapshot"]["total"] == 2

    async def test_message_send_unknown_skill_32601(self, client):
        resp = await rpc(
            client,
            {"jsonrpc": "2.0", "id": 4, "method": "message/send",
             "params": {"message": {"content": "hi"},
                        "metadata": {"skill": "nope"}}},
        )
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == -32601
        assert sorted(err["data"]["available"]) == sorted(EXPECTED_SKILL_IDS)


# ============ skills/invoke param validation ============


class TestSkillsInvokeValidation:
    async def test_unknown_skill_32601_with_available_list(self, client):
        resp = await rpc(client, invoke(client, "no-such-skill",
                                        messages=[{"role": "user", "content": "x"}]))
        assert resp.status_code == 404
        err = resp.json()["error"]
        assert err["code"] == -32601
        assert sorted(err["data"]["available"]) == sorted(EXPECTED_SKILL_IDS)

    async def test_missing_input_32602(self, client):
        resp = await rpc(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "skills/invoke",
             "params": {"skill": "health"}},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == -32602

    async def test_arguments_query_fallback(self, client):
        """OmniRoute-compatible input shapes: arguments.query also works."""
        resp = await rpc(
            client,
            {"jsonrpc": "2.0", "id": 1, "method": "skills/invoke",
             "params": {"skill": "routing-advice", "arguments": {"query": "hi"}}},
        )
        assert resp.status_code == 200
        assert resp.json()["result"]["task"]["state"] == "completed"


# ============ PII sanitization units ============


class TestSanitization:
    def test_sanitize_outbound_redacts_secret_like_keys(self):
        payload = {
            "model": "gpt-4o",
            "api_key": SECRET_VALUE,
            "access_token": "t0k3n",
            "prompt_tokens": 10,
            "completion_tokens": 20,
            "nested": {"db_password": "p@ss", "Authorization": "Bearer x",
                       "ok_field": 1},
            "list": [{"token": "t0k3n", "plain": "fine"}],
        }
        clean = sanitize_outbound(payload)
        assert clean["api_key"] == "[REDACTED]"
        assert clean["access_token"] == "[REDACTED]"
        assert clean["nested"]["db_password"] == "[REDACTED]"
        assert clean["nested"]["Authorization"] == "[REDACTED]"
        assert clean["nested"]["ok_field"] == 1
        assert clean["list"][0]["token"] == "[REDACTED]"
        assert clean["list"][0]["plain"] == "fine"
        assert clean["model"] == "gpt-4o"
        # telemetry counters are NOT secrets — no false positives
        assert clean["prompt_tokens"] == 10
        assert clean["completion_tokens"] == 20
        assert SECRET_VALUE not in str(clean)

    def test_scrub_url_credentials(self):
        assert (
            scrub_url_credentials("redis://user:pass@localhost:6379/0")
            == "redis://[REDACTED]@localhost:6379/0"
        )
        assert scrub_url_credentials("redis://localhost:6379") == "redis://localhost:6379"
        assert scrub_url_credentials("") == ""


# ============ normalize_messages units ============


class TestNormalizeMessages:
    def test_variants(self):
        assert normalize_messages("hello") == [{"role": "user", "content": "hello"}]
        assert normalize_messages([{"role": "user", "content": "a"},
                                   {"role": "assistant", "content": "b"}]) == [
            {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}
        ]
        assert normalize_messages({"content": "c"}) == [{"role": "user", "content": "c"}]
        parts = normalize_messages({"parts": ["Hello ", {"text": "world"}]})
        assert parts is not None and "Hello" in parts[0]["content"] and "world" in parts[0]["content"]
        assert normalize_messages("") is None
        assert normalize_messages([]) is None
        assert normalize_messages({}) is None
        assert normalize_messages(42) is None
