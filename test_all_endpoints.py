"""Test all endpoints of MoA Gateway Pro. Outputs a JSON report."""
import os
import json
import time
import sys
import httpx
from typing import Any

BASE = "http://127.0.0.1:8910"
API_KEY = "demo-key-please-change"

# Each entry: (method, path, body_or_None, params_or_None)
ENDPOINTS: list[tuple[str, str, Any, Any]] = [
    # 1. /health
    ("GET", "/health", None, None),
    # 2. /api/health/detailed
    ("GET", "/api/health/detailed", None, None),
    # 3. /v1/models
    ("GET", "/v1/models", None, None),
    # 4. /v1/chat/completions (auto route, single model)
    ("POST", "/v1/chat/completions",
     {"model": "auto", "messages": [{"role": "user", "content": "say hi"}]},
     None),
    # 5. /v1/chat/completions (specific endpoint id)
    ("POST", "/v1/chat/completions",
     {"model": "deepseek-v3", "messages": [{"role": "user", "content": "hi"}]},
     None),
    # 6. /v1/moa/execute
    ("POST", "/v1/moa/execute",
     {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
     None),
    # 7. /v1/moa/eval
    ("POST", "/v1/moa/eval",
     {"query": "hi", "candidates": ["deepseek-v3", "glm-4-flash"]},
     None),
    # 8. /v1/moa/similarity
    ("POST", "/v1/moa/similarity",
     {"query": "hi", "candidate_a": "hi", "candidate_b": "hello"},
     None),
    # 9. /v1/moa/flask
    ("POST", "/v1/moa/flask",
     {"query": "hi", "response": "hello"},
     None),
    # 10. /v1/moa/benchmark
    ("POST", "/v1/moa/benchmark",
     {"presets": ["fast"], "category": "reasoning", "limit": 1},
     None),
    # 11. /v1/moa/cost-pareto
    ("POST", "/v1/moa/cost-pareto",
     {"prompts": ["a", "b", "c"], "presets": ["fast"]},
     None),
    # 12. /v1/moa/presets
    ("GET", "/v1/moa/presets", None, None),
    # 13. /v1/moa/prompts
    ("GET", "/v1/moa/prompts", None, None),
    # 14. /v1/moa/prompts/{name}
    ("GET", "/v1/moa/prompts/aggregator_system", None, None),
    # 15. /v1/moa/prompts/{name} (PUT)
    ("PUT", "/v1/moa/prompts/test_save_001", {"content": "test content"}, None),
    # 16. /v1/moa/prompts/{name} (DELETE)
    ("DELETE", "/v1/moa/prompts/test_save_001", None, None),
    # 17. /v1/route/preview
    ("GET", "/v1/route/preview", None, {"q": "hi"}),
    # 18. /v1/quota
    ("GET", "/v1/quota", None, None),
]

# Capability endpoints — derived from the server source
CAP_ENDPOINTS = [
    ("secret-scan", "POST", {"path": ".", "fail_on": 3, "no_block": True}),
    ("group-think-check", "POST", {
        "session_id": "s1",
        "members": [{"member_id": "a", "content": "x", "round": 0}],
    }),
    ("ensemble-vote", "POST", {
        "votes": [{"voter_id": "a", "candidate": "x", "confidence": 0.9, "reason": "ok"}],
        "method": "weighted",
    }),
    ("should-rebalance", "POST", {
        "stats": {"m1": {"tier": "standard", "endpoint_count": 1,
                         "success_count": 10, "fail_count": 0, "weight_sum": 100}},
    }),
    ("cost-estimate", "POST", {
        "input_tokens": 1000, "output_tokens": 500,
        "channels": [{"name": "m1", "cost_per_1k_input": 0.0005,
                      "cost_per_1k_output": 0.001, "tier": "standard"}],
        "include_fallback": True,
    }),
    ("gate-l0", "POST", {"query": "2+3"}),
    ("score-panel", "POST", {"query": "test", "answer": "test answer"}),
    # GET /v1/capability/models
    None,  # marker for GET
    ("calculate-max-tokens", "POST", {"model_id": "gpt-4o", "input_tokens": 1000,
                                      "requested_output": 2000, "safety_margin": 0.1}),
    ("estimate-cost", "POST", {"model_id": "gpt-4o", "input_tokens": 1000,
                               "output_tokens": 500}),
    ("quota-check", "POST", {
        "windows": [{"name": "5h", "limit_tokens": 100000, "used_history": []}],
        "requested": 1000,
    }),
    ("quota-record", "POST", {
        "windows": [{"name": "5h", "limit_tokens": 100000, "used_history": []}],
        "tokens": 100,
    }),
    ("moa-n-layer", "POST", {
        "query": "test",
        "proposers": [{"name": "p1", "model_id": "deepseek-v3"}],
        "aggregators": [
            {"name": "a1", "model_id": "glm-4-flash", "role": "layer1"},
            {"name": "a2", "model_id": "glm-4-flash", "role": "layer2"},
            {"name": "a3", "model_id": "glm-4-flash", "role": "layer3"},
        ],
    }),
    ("convergent-detect", "POST", {
        "proposals": [{"proposal_idx": 0, "author": "a", "text": "test"}],
    }),
    ("action-policy", "POST", {
        "command": "ls",
        "rules": [{"pattern": "rm -rf /", "action": "deny", "priority": 100}],
    }),
    ("embeddings", "POST", {"input": ["hello"], "dim": 64}),
    ("semantic-search", "POST", {
        "query": "hello", "documents": ["hello world", "goodbye"], "top_k": 2, "dim": 64,
    }),
    ("prompt-features", "POST", {"text": "test prompt"}),
    ("provider-health", "POST", {
        "providers": [{"provider": "p1", "total_calls": 100, "success_calls": 95,
                       "fail_calls": 5, "avg_latency_ms": 200, "p99_latency_ms": 1000,
                       "consecutive_failures": 0, "circuit_open": False}],
    }),
    ("context-clean", "POST", {"messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 100}),
    ("self-heal", "POST", {"errors": [{"error": "timeout", "count": 3}]}),
    ("multi-mode-synth", "POST", {
        "query": "test", "modes": [{"name": "a", "strategy": "single"}],
    }),
    ("conflict-arbitrate", "POST", {
        "conflicts": [{"option_a": "x", "option_b": "y"}],
    }),
    ("section-viability", "POST", {
        "sections": [{"text": "test", "position": 0}],
    }),
    ("feedback-iter", "POST", {
        "query": "test", "previous_output": "old", "feedback": "improve",
    }),
    ("stream-aggregate", "POST", {
        "chunks": [{"text": "hello "}, {"text": "world"}],
    }),
    ("per-provider-rl", "POST", {
        "providers": [{"provider": "p1", "score": 80, "weight": 1.0}],
    }),
    ("tier-recalibrate", "POST", {
        "endpoints": [{"id": "e1", "tier": "standard", "score": 80}],
    }),
    ("consumption-intel", "POST", {"usage": [{"date": "2026-01-01", "tokens": 1000}]}),
    ("importance-score", "POST", {"text": "important text here"}),
    ("quorum-check", "POST", {"votes": [{"voter_id": "a", "approve": True}]}),
    ("model-entry", "POST", {"model_id": "test", "api_key": "test-key",
                             "api_base": "https://api.test.com", "provider": "openai"}),
    ("tool-replay", "POST", {"tool_call": {"name": "test", "args": {}}}),
    ("hook-events", "POST", {"event": "test", "data": {}}),
    ("meta-prompt", "POST", {"text": "test meta prompt"}),
    ("task-tree", "POST", {"task": "test task"}),
    ("distill", "POST", {"text": "long text to distill", "max_length": 50}),
    ("rerank", "POST", {"query": "test", "documents": ["a", "b"]}),
    ("goal-eval", "POST", {"goal": "test goal", "output": "test output"}),
    ("auto-converge", "POST", {"output": "test output"}),
    ("subagent-comms", "POST", {"sender": "a", "receiver": "b", "message": "hi"}),
    ("version", "POST", {"data": {"v": 1}}),
    ("config", "POST", {"key": "test", "value": "test"}),
    ("bubble", "POST", {"input": "test"}),
    ("worktree", "POST", {"action": "list"}),
    ("route", "POST", {"query": "test query"}),
    ("session-lock", "POST", {"session_id": "s1", "action": "acquire"}),
    ("flask", "POST", {"query": "test", "response": "test response"}),
    ("elo", "POST", {"players": [{"id": "a"}, {"id": "b"}], "results": []}),
    ("brainstorm", "POST", {"topic": "test", "count": 3}),
    ("cross-iter", "POST", {"iterations": [{"text": "iter1"}]}),
    ("audit", "POST", {"action": "read", "resource": "/tmp/test"}),
    ("in-flight", "POST", {"request_id": "r1", "action": "check"}),
    ("mx", "POST", {"input": "test input"}),
    ("tier-promo", "POST", {"endpoint": "e1", "score": 90}),
    ("artifact", "POST", {"type": "test", "data": {}}),
    ("frozen", "POST", {"action": "check", "key": "test"}),
    ("turboquant", "POST", {"text": "test text"}),
    ("moa-engine", "POST", {"query": "test"}),
    ("acceptance", "POST", {"spec": "test", "output": "ok"}),
    ("llm-merge", "POST", {"outputs": ["a", "b"]}),
    ("grace", "POST", {"action": "check", "key": "test"}),
    ("rag-search", "POST", {"query": "test", "documents": ["doc1", "doc2"]}),
    ("plan-act", "POST", {"goal": "test", "context": {}}),
    ("channels", "POST", {"action": "list"}),
    ("reference-router", "POST", {"query": "test"}),
    ("checkpoint", "POST", {"action": "create", "name": "test"}),
    ("audit", "POST", {"action": "scan", "path": "."}),  # second /v1/capability/audit
    ("canary", "POST", {"prompt": "test"}),
    ("wrap-output", "POST", {"content": "test"}),
    ("fuzzy-dedup", "POST", {"items": ["a", "a", "b"]}),
    ("input-fingerprint", "POST", {"input": "test"}),
    ("tool-screening", "POST", {"tool": "test"}),
    ("anthropic-compat", "POST", {"prompt": "test"}),
    ("token-bucket", "POST", {"action": "consume", "tokens": 1}),
    ("request-dedup", "POST", {"request_id": "r1", "fingerprint": "fp1"}),
    ("trace", "POST", {"trace_id": "t1", "data": {}}),
]

# Build final test list
FINAL: list[tuple[str, str, Any, Any]] = list(ENDPOINTS)
GET_MODELS = ("models", "GET", None, None)

for ep in CAP_ENDPOINTS:
    if ep is None:
        # GET /v1/capability/models
        FINAL.append(("GET", "/v1/capability/models", None, None))
        continue
    name, method, body = ep
    if method == "GET":
        FINAL.append(("GET", f"/v1/capability/{name}", None, None))
    else:
        FINAL.append(("POST", f"/v1/capability/{name}", body, None))

# Admin / API key / log endpoints (require auth token)
FINAL.extend([
    ("POST", "/api/auth/login", {"username": "admin", "password": "TestPassword123!"}, None),
    ("POST", "/api/auth/change-password",
     {"old_password": "TestPassword123!", "new_password": "TestPassword123!"}, None),
    ("GET", "/api/auth/me", None, None),
    ("GET", "/api/endpoints", None, None),
    ("POST", "/api/endpoints",
     {"endpoint_id": "test_ep", "provider": "openai", "model": "gpt-4o",
      "tier": "standard", "api_key_plain": "sk-test"}, None),
    ("DELETE", "/api/endpoints/test_ep", None, None),
    ("POST", "/api/endpoints/test_ep/toggle", None, None),
    ("POST", "/api/endpoints/test_ep/reset-breaker", None, None),
    ("GET", "/api/api-keys", None, None),
    ("POST", "/api/api-keys", {"name": "test_key"}, None),
    ("DELETE", "/api/api-keys/key_xxx", None, None),
    ("GET", "/api/logs", None, None),
    ("GET", "/api/stats", None, None),
    ("GET", "/api/metrics", None, None),
    ("GET", "/api/adapters", None, None),
    ("GET", "/api/adapters/curl", None, None),
    ("GET", "/", None, None),
    ("GET", "/webui/index.html", None, None),
])


def main():
    results = []
    start_t = time.time()
    n = len(FINAL)
    for i, (method, path, body, params) in enumerate(FINAL, 1):
        url = BASE + path
        # Use admin login for /api/auth/* and /api/* but others with API key
        is_admin_path = path.startswith("/api/")
        if is_admin_path and path != "/api/auth/login":
            # Need JWT — get one
            pass  # we will reuse first login token
        try:
            headers = {"Authorization": f"Bearer {API_KEY}"}
            with httpx.Client(timeout=20.0) as client:
                if method == "GET":
                    r = client.get(url, headers=headers, params=params)
                else:
                    r = client.post(url, headers=headers, json=body)
            # 401 is "expected" if auth path. Record.
            content = r.text[:200] if r.status_code != 200 else r.text[:80]
            results.append({
                "i": i, "method": method, "path": path,
                "status": r.status_code,
                "size": len(r.content),
                "ok": r.status_code < 500 and r.status_code != 401,
                "snippet": content,
            })
        except Exception as e:
            results.append({
                "i": i, "method": method, "path": path,
                "status": "ERR", "error": str(e)[:200], "ok": False,
            })
    elapsed = time.time() - start_t
    summary = {
        "total": n,
        "elapsed_seconds": round(elapsed, 2),
        "by_status": {},
        "ok_count": sum(1 for r in results if r.get("ok")),
        "results": results,
    }
    for r in results:
        s = str(r.get("status"))
        summary["by_status"][s] = summary["by_status"].get(s, 0) + 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
