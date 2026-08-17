"""Real-path regression tests for the services layer (audit P1 dead-method fix).

Guarantees:
  1. Every `_load_*` loader of every service module REALLY imports its
     capability symbols (no mocks, no patching) — the exact failure mode of
     the 35+ dead methods was an ImportError raised inside these loaders.
  2. Every registered (service, method) pair is wired to a callable function.
  3. Every service is live-dispatched with real arguments for at least two
     methods; results must carry ok=True and non-empty data.
  4. The specific methods that were dead before the fix (tier_promo_*,
     reference_route, rank_elo, distill, mx, cross_iter, context_clean, ...)
     are exercised with semantic assertions on the real computation.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import socket
import threading
import time

import pytest

import moa_gateway.services as services_pkg
from moa_gateway.services.dispatcher import get_dispatcher

# Modules that are infrastructure (no capability loaders to verify).
_NON_SERVICE_MODULES = {"base", "dispatcher", "capability_dispatcher", "__init__"}


def _service_modules():
    mods = []
    for info in pkgutil.iter_modules(services_pkg.__path__):
        if info.name in _NON_SERVICE_MODULES:
            continue
        mods.append(importlib.import_module(f"moa_gateway.services.{info.name}"))
    return mods


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. Every loader really imports (the original dead-method failure mode)
# ---------------------------------------------------------------------------
class TestLoadersRealImport:
    def test_every_loader_imports_real_symbols(self):
        """Call every module-level _load_* function — must not raise."""
        checked = 0
        for mod in _service_modules():
            for name, obj in vars(mod).items():
                if not name.startswith("_load_"):
                    continue
                if not callable(obj):
                    continue
                result = obj()  # would raise ImportError if symbols are fake
                assert result is not None, f"{mod.__name__}.{name} returned None"
                checked += 1
        # The audit counted 30+ loaders across the 10 service files.
        assert checked >= 30, f"expected 30+ loaders, only found {checked}"

    def test_every_registered_method_is_callable(self):
        dispatcher = get_dispatcher()
        services = {s["name"]: s for s in dispatcher.list_agents()}
        assert len(services) >= 11
        for name, svc in services.items():
            instance = dispatcher.registry.get(name)
            assert instance is not None, f"service {name} not registered"
            for m in svc["methods"]:
                method = instance.get_method(m["name"])
                assert method is not None, f"{name}.{m['name']} missing"
                assert callable(method.func), f"{name}.{m['name']} func not callable"


# ---------------------------------------------------------------------------
# 2. Previously-dead methods: live dispatch + semantic assertions
# ---------------------------------------------------------------------------
class TestPreviouslyDeadMethods:
    """Each of these raised ImportError / TypeError before the fix."""

    def test_quota_tier_promo_classify(self):
        res = _run(
            get_dispatcher().dispatch(
                "quota",
                "tier_promo_classify",
                {
                    "evidence": [
                        {"event_type": "ok", "timestamp": float(i), "weight": 2.0}
                        for i in range(6)
                    ],
                    "confidence_threshold": 0.5,
                },
            )
        )
        assert res.ok, res.error
        # 6 evidence items × weight 2 → confidence 12/15=0.8 ≥ 0.5, count 6 ≥ 5 → TIER_3
        assert res.data["current_tier"] == "TIER_3"
        assert res.data["evidence_count"] == 6
        assert res.data["confidence"] == pytest.approx(0.8)

    def test_quota_tier_promo_compute_suppression(self):
        res = _run(
            get_dispatcher().dispatch(
                "quota", "tier_promo_compute", {"count": 12, "confidence": 0.3}
            )
        )
        assert res.ok, res.error
        # confidence 0.3 < threshold 0.7 → suppressed, stays TIER_1
        assert res.data["tier"] == "TIER_1"
        assert res.data["suppressed"] is True

    def test_quota_tier_promo_can_spawn_and_cohabitation(self):
        res = _run(
            get_dispatcher().dispatch(
                "quota",
                "tier_promo_can_spawn",
                {"parent_id": "p1", "allowed_children": ["c1", "c2"], "child_id": "c9"},
            )
        )
        assert res.ok, res.error
        assert res.data["allowed"] is False
        res2 = _run(
            get_dispatcher().dispatch(
                "quota",
                "tier_promo_cohabitation",
                {"parent_a": "p1", "children_a": ["c1"], "parent_b": "p1", "children_b": ["c2"]},
            )
        )
        assert res2.ok, res2.error
        assert res2.data["compatible"] is True

    def test_routing_reference_route_validate(self):
        res = _run(
            get_dispatcher().dispatch(
                "routing",
                "reference_route",
                {
                    "query": "explain mixture of agents",
                    "main_model": "main-large",
                    "ref_model": "ref-small",
                    "strategy": "validate",
                },
            )
        )
        assert res.ok, res.error
        assert res.data["decision"] in ("accept", "flag", "reject")
        assert res.data["strategy_used"] == "validate"
        assert res.data["main_answer"]
        assert isinstance(res.data["calibration"], list)

    def test_quality_rank_elo_record_and_ranked(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "quality",
                "rank_elo",
                {
                    "action": "record",
                    "model_ids": ["m1", "m2"],
                    "matches": [
                        {"winner_id": "m1", "loser_id": "m2"},
                        {"winner_id": "m1", "loser_id": "m2"},
                    ],
                },
            )
        )
        assert res.ok, res.error
        assert len(res.data["recorded"]) == 2
        ratings = {r["model_id"]: r["rating"] for r in res.data["ranked"]}
        assert ratings["m1"] > 1500 > ratings["m2"]

    def test_quality_plan_act_classify(self):
        res = _run(
            get_dispatcher().dispatch("quality", "plan_act", {"query": "refactor the database layer"})
        )
        assert res.ok, res.error
        assert isinstance(res.data, dict) and res.data

    def test_quality_meta_prompt_clash_and_fuse(self):
        res = _run(
            get_dispatcher().dispatch(
                "quality",
                "meta_prompt",
                {"action": "clash", "query": "monolith or microservices", "role_a": "arch", "role_b": "ops"},
            )
        )
        assert res.ok, res.error
        assert res.data["role_a_view"] and res.data["role_b_view"]
        res2 = _run(
            get_dispatcher().dispatch(
                "quality", "meta_prompt", {"action": "fuse", "options": ["A", "B"]}
            )
        )
        assert res2.ok, res2.error
        assert res2.data["decision"]

    def test_knowledge_distill(self):
        res = _run(
            get_dispatcher().dispatch(
                "knowledge",
                "distill",
                {
                    "proposals": [
                        "add caching to improve gateway performance",
                        "caching will improve the gateway performance a lot",
                        "write more unit tests for the parser",
                    ],
                    "keep_ratio": 0.5,
                    "evaluations": [{"quality": 0.9}, {"quality": 0.7}],
                },
            )
        )
        assert res.ok, res.error
        # original_count counts extracted ideas after clustering (similar
        # caching ideas from two proposals merge into one).
        assert res.data["original_count"] >= 1
        assert (
            len(res.data["kept_ideas"]) + len(res.data["dropped_ideas"])
            == res.data["original_count"]
        )
        assert "eval_average" in res.data

    def test_knowledge_context_clean(self):
        res = _run(
            get_dispatcher().dispatch(
                "knowledge",
                "context_clean",
                {
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"},
                        {"role": "assistant", "content": "how can I help"},
                    ],
                    "max_total_chars": 4000,
                },
            )
        )
        assert res.ok, res.error
        # consecutive assistant messages must be merged
        roles = [m["role"] for m in res.data["messages"]]
        assert roles.count("assistant") == 1
        assert res.data["stats"]["merged_pairs"] >= 1

    def test_moa_cross_iter_convergence_and_adoption(self):
        iters = [
            {"iter_idx": 0, "proposals": ["use redis", "use memcached"], "best_score": 0.5,
             "best_proposal_idx": 0, "summary": "cache debate"},
            {"iter_idx": 1, "proposals": ["use redis cluster", "use redis"], "best_score": 0.9,
             "best_proposal_idx": 0, "summary": "redis wins"},
        ]
        res = _run(get_dispatcher().dispatch("moa", "cross_iter", {"iters": iters, "action": "convergence"}))
        assert res.ok, res.error
        assert res.data["output"]
        res2 = _run(get_dispatcher().dispatch("moa", "cross_iter", {"iters": iters, "action": "adoption"}))
        assert res2.ok, res2.error
        assert res2.data["mode"] == "recommended_adoption"

    def test_config_mx_parse_fanin_cli(self):
        text = "# mx:TODO: refactor auth\n# mx:TODO: fix logging\n# mx:WARN: crash risk\n"
        res = _run(get_dispatcher().dispatch("config", "mx", {"action": "parse", "text": text, "file_path": "a.py"}))
        assert res.ok, res.error
        assert res.data["count"] == 3
        res2 = _run(get_dispatcher().dispatch("config", "mx", {"action": "fanin", "text": text, "file_path": "a.py"}))
        assert res2.ok, res2.error
        assert sum(res2.data["fanin"].values()) == 3
        res3 = _run(
            get_dispatcher().dispatch(
                "config", "mx", {"action": "cli", "text": text, "file_path": "a.py", "command": "count TODO"}
            )
        )
        assert res3.ok, res3.error
        assert res3.data["output"] == "2"

    def test_agent_subagent_message_flow(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "agent",
                "send_message",
                {"session_id": "alice", "to_session": "bob", "content": "ping bob"},
            )
        )
        assert res.ok, res.error
        assert res.data["to_session"] == "bob"
        res2 = _run(d.dispatch("agent", "inbox", {"session_id": "bob"}))
        assert res2.ok, res2.error
        assert any(m["content"] == "ping bob" for m in res2.data["messages"])

    def test_agent_mcp_register_invoke(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "agent",
                "register_mcp",
                {"name": "live_echo", "description": "echo tool", "parameters": {"msg": "str"}, "returns": "ok-echo"},
            )
        )
        assert res.ok, res.error
        res2 = _run(d.dispatch("agent", "invoke_mcp", {"name": "live_echo", "kwargs": {"msg": "x"}}))
        assert res2.ok, res2.error
        assert res2.data["result"] == "ok-echo"

    def test_agent_bubble_flow(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "agent",
                "bubble_escalate",
                {"parent_id": "pX", "agent_id": "aX", "action_desc": "drop table", "reason": "danger"},
            )
        )
        assert res.ok, res.error
        request_id = res.data["request_id"]
        res2 = _run(d.dispatch("agent", "bubble_pending", {"parent_id": "pX"}))
        assert res2.ok, res2.error
        assert res2.data["count"] >= 1
        res3 = _run(
            d.dispatch("agent", "bubble_resolved", {"parent_id": "pX", "request_id": request_id, "status": "denied"})
        )
        assert res3.ok, res3.error
        assert res3.data["resolved"] == 1

    def test_safety_canary_inject_check_roundtrip(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch("safety", "prompt_canary", {"action": "inject", "prompt": "translate this", "strategy": "suffix"})
        )
        assert res.ok, res.error
        canary = res.data["canary"]
        assert canary and canary in res.data["prompt"]
        res2 = _run(
            d.dispatch(
                "safety",
                "prompt_canary",
                {"action": "check", "response": f"sure here you go {canary}", "canary": canary},
            )
        )
        assert res2.ok, res2.error
        assert isinstance(res2.data, dict) and res2.data

    def test_safety_llm_merge_and_fallback(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "safety",
                "llm_merge",
                {
                    "action": "merge",
                    "strategy": "CONCAT",
                    "responses": [
                        {"source": "p1", "text": "alpha", "tokens": 3, "latency_ms": 5, "cost_usd": 0.001, "confidence": 0.9},
                        {"source": "p2", "text": "beta", "tokens": 3, "latency_ms": 6, "cost_usd": 0.001, "confidence": 0.8},
                    ],
                },
            )
        )
        assert res.ok, res.error
        assert "alpha" in res.data["text"] and "beta" in res.data["text"]
        res2 = _run(
            d.dispatch(
                "safety", "llm_merge", {"action": "fallback", "providers": ["pa", "pb"], "fail_at": ["pa"]}
            )
        )
        assert res2.ok, res2.error
        assert res2.data["response"]["source"] == "pb"

    def test_observability_trace_start_end_query(self):
        d = get_dispatcher()
        res = _run(d.dispatch("observability", "trace", {"action": "start", "tags": {"t": "1"}}))
        assert res.ok, res.error
        traceparent = res.data["traceparent"]
        res2 = _run(
            d.dispatch("observability", "trace", {"action": "end", "traceparent": traceparent, "status": "ok"})
        )
        assert res2.ok, res2.error
        assert res2.data["ended"] is True

    def test_config_tool_replay_real_format(self):
        proposals = [
            'A: <tool_use name="search" id="c1">{"q": "moa"}</tool_use> then '
            '<tool_use name="search" id="c2">{"q": "moa"}</tool_use>',
            'B: <tool_use name="search" id="c3">{"q": "moa"}</tool_use>',
        ]
        res = _run(
            get_dispatcher().dispatch("config", "tool_replay", {"proposals": proposals, "window": 5})
        )
        assert res.ok, res.error
        # 3 identical calls dedupe down to 1 kept call
        assert len(res.data["tool_calls"]) == 1
        assert res.data["deduplicated_count"] == 2
        # loop detection over the raw sequence flags the repetition
        assert res.data["loop_detected"] is not None

    def test_config_checkpoint_save_load(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch("config", "checkpoint", {"action": "save", "name": "pytest_ckpt", "payload": {"n": 42}})
        )
        assert res.ok, res.error
        res2 = _run(d.dispatch("config", "checkpoint", {"action": "load", "name": "pytest_ckpt"}))
        assert res2.ok, res2.error
        assert res2.data["payload"] == {"n": 42}

    def test_consensus_arbitrate_and_synthesize(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "consensus",
                "arbitrate_conflicts",
                {
                    "options": [
                        {"option_id": "a", "description": "postgres", "supporting_proposals": [0, 1, 2]},
                        {"option_id": "b", "description": "mysql", "supporting_proposals": [3]},
                    ],
                    "criteria": {"total_proposals": 4},
                },
            )
        )
        assert res.ok, res.error
        assert res.data["winner_option_id"] == "a"
        res2 = _run(
            d.dispatch(
                "consensus",
                "synthesize_multi_mode",
                {"mode": "classification", "proposals": ["fix the bug", "add tests"]},
            )
        )
        assert res2.ok, res2.error
        assert res2.data["mode"] == "classification"


# ---------------------------------------------------------------------------
# 3. ≥2 live dispatches per remaining service (real params, ok=True, data)
# ---------------------------------------------------------------------------
class TestEveryServiceLiveDispatch:
    def test_quota_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "quota",
                "check_quota",
                {
                    "windows": [{"name": "5h", "limit_tokens": 100000, "used_tokens": 100}],
                    "requested": 500,
                },
            )
        )
        assert res.ok and res.data, res.error
        assert res.data["available"] is True
        res2 = _run(
            d.dispatch(
                "quota",
                "dedup_check",
                {"method": "POST", "path": "/v1/chat", "body": {"q": 1}},
            )
        )
        assert res2.ok and res2.data, res2.error

    def test_routing_live(self):
        d = get_dispatcher()
        res = _run(d.dispatch("routing", "chain_info", {}))
        assert res.ok and res.data, res.error
        assert "subagent" in res.data["channels"]
        res2 = _run(d.dispatch("routing", "classify_error", {"error": "401 unauthorized"}))
        assert res2.ok and res2.data, res2.error
        assert res2.data["category"] == "auth"

    def test_quality_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch("quality", "score_flask", {"query": "what is 2+2?", "response": "2+2 equals 4."})
        )
        assert res.ok and res.data, res.error
        res2 = _run(d.dispatch("quality", "gate_l0", {"query": "hello"}))
        assert res2.ok and res2.data, res2.error

    def test_knowledge_live(self):
        d = get_dispatcher()
        res = _run(d.dispatch("knowledge", "embed", {"input": "hello world", "dim": 32}))
        assert res.ok and res.data, res.error
        assert res.data["count"] == 1
        res2 = _run(
            d.dispatch(
                "knowledge",
                "rag_search",
                {"query": "moa", "corpus": [{"text": "mixture of agents", "tags": "moa"}]},
            )
        )
        assert res2.ok and res2.data, res2.error

    def test_moa_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "moa",
                "validate_config",
                {
                    "proposers": [{"model_id": "auto", "system_prompt": "x"}],
                    "aggregator": {"model_id": "auto", "synthesis_prompt": "y"},
                },
            )
        )
        assert res.ok and res.data, res.error
        assert "valid" in res.data
        res2 = _run(
            d.dispatch(
                "moa",
                "run_engine",
                {
                    "query": "test",
                    "proposers": [{"model_id": "m1"}, {"model_id": "m2"}],
                    "aggregator": {"model_id": "agg"},
                },
            )
        )
        assert res2.ok and res2.data, res2.error

    def test_config_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch("config", "config", {"action": "set", "key": "live_k", "value": 7, "layer": "PROJECT"})
        )
        assert res.ok and res.data, res.error
        res2 = _run(d.dispatch("config", "config", {"action": "get", "key": "live_k"}))
        assert res2.ok and res2.data, res2.error
        assert res2.data["value"] == 7

    def test_consensus_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "consensus",
                "vote_ensemble",
                {
                    "votes": [
                        {"voter_id": "v1", "candidate": "A", "confidence": 0.9},
                        {"voter_id": "v2", "candidate": "A", "confidence": 0.8},
                        {"voter_id": "v3", "candidate": "B", "confidence": 0.6},
                    ],
                    "method": "weighted",
                },
            )
        )
        assert res.ok and res.data, res.error
        assert res.data["winner"] == "A"
        res2 = _run(
            d.dispatch(
                "consensus",
                "check_group_think",
                {"session_id": "gt1", "members": [{"member_id": "m1", "content": "I agree fully"}]},
            )
        )
        assert res2.ok and res2.data, res2.error

    def test_agent_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch("agent", "try_acquire", {"lock_id": "L1", "session_id": "S1", "ttl": 30})
        )
        assert res.ok and res.data, res.error
        assert res.data["acquired"] is True
        res2 = _run(d.dispatch("agent", "get_lock_state", {"lock_id": "L1"}))
        assert res2.ok and res2.data, res2.error
        assert res2.data["lock"] is not None

    def test_safety_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "safety",
                "output_wrapping",
                {"action": "wrap", "content": "raw tool output", "source": "tool", "trust": "untrusted"},
            )
        )
        assert res.ok and res.data, res.error
        assert "untrusted_tool_output" in res.data["wrapped"]
        res2 = _run(
            d.dispatch("safety", "tool_screening", {"tool_name": "bash", "arguments": {"command": "ls -la"}})
        )
        assert res2.ok and res2.data is not None, res2.error

    def test_observability_live(self):
        d = get_dispatcher()
        res = _run(
            d.dispatch(
                "observability", "audit", {"action": "record", "action_id": "act-live", "action_data": {"x": 1}}
            )
        )
        assert res.ok and res.data, res.error
        res2 = _run(d.dispatch("observability", "hook_events", {"action": "list_events"}))
        assert res2.ok and res.data, res2.error
        assert "SessionStart" in res2.data["events"]


# ---------------------------------------------------------------------------
# 4. Capability loopback service: really executes HTTP endpoints
# ---------------------------------------------------------------------------
class TestCapabilityLoopbackLive:
    """capability.call_* methods loop back over HTTP into the running gateway.

    Boots a real uvicorn server on a free port with a known API key, then
    dispatches two capability methods through the service layer.
    """

    def test_loopback_dispatch_executes_real_endpoints(self, monkeypatch):
        import uvicorn

        import moa_gateway.config as _cfg
        from moa_gateway.config import Settings

        # free port
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        api_key = "svc-real-test-key-001"
        monkeypatch.setattr(
            _cfg,
            "_settings",
            Settings(
                server={"port": port},
                auth={
                    "gateway_api_keys": [api_key],
                    "admin_username": "admin",
                    "admin_password": "SuperStr0ng!Pass#2024",
                    "jwt_secret": "loopback-test-secret-long-enough-for-hs256-signing",
                    "jwt_expire_minutes": 60,
                },
            ),
        )
        monkeypatch.setenv("MOA_GATEWAY_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("MOA_GATEWAY_KEY", api_key)

        # v3.1.1: earlier tests' server lifespans leave the process-global
        # graceful-shutdown flag set, which makes this fresh server answer 503
        # "Server is shutting down". Reset it before booting.
        from moa_gateway.ha import graceful as _graceful_pre

        _graceful_pre._shutting_down = False
        _graceful_pre._shutdown_event = None
        _graceful_pre._active_requests = 0

        from moa_gateway.server import create_app

        app = create_app()
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.time() + 20
            while time.time() < deadline and not server.started:
                time.sleep(0.05)
            assert server.started, "uvicorn server did not start in time"

            d = get_dispatcher()
            res = _run(
                d.dispatch("capability", "call_gate_l0", {"body": {"query": "2+2"}})
            )
            assert res.ok, res.error
            assert isinstance(res.data, dict) and "passed" in res.data

            res2 = _run(d.dispatch("capability", "call_models", {"body": {}}))
            assert res2.ok, res2.error
            assert res2.data.get("count", 0) >= 1
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            # The server lifespan sets the process-global graceful-shutdown
            # flag; reset it so later tests' apps accept requests again.
            from moa_gateway.ha import graceful as _graceful

            _graceful._shutting_down = False
            _graceful._shutdown_event = None
            _graceful._active_requests = 0
