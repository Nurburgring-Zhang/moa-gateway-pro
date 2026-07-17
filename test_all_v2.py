"""Test all endpoints with proper auth - get token first."""
import os
import json
import time
import httpx
from typing import Any

BASE = "http://127.0.0.1:8910"
API_KEY = "demo-key-please-change"

# Login first
def login_admin():
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{BASE}/api/auth/login",
                   json={"username": "admin", "password": "TestPassword123!"})
        if r.status_code == 200:
            return r.json()["token"]
    return None

ADMIN_TOKEN = login_admin()
print(f"Admin token: {ADMIN_TOKEN[:50]}..." if ADMIN_TOKEN else "LOGIN FAILED")

# Each entry: (method, path, body, params, auth_type)
# auth_type: "api" = API key, "admin" = JWT, "none" = no auth
ENDPOINTS: list[tuple[str, str, Any, Any, str]] = [
    ("GET", "/health", None, None, "none"),
    ("GET", "/api/health/detailed", None, None, "api"),
    ("GET", "/v1/models", None, None, "api"),
    ("POST", "/v1/chat/completions",
     {"model": "auto", "messages": [{"role": "user", "content": "say hi"}]},
     None, "api"),
    ("POST", "/v1/chat/completions",
     {"model": "deepseek-v3", "messages": [{"role": "user", "content": "hi"}]},
     None, "api"),
    ("POST", "/v1/moa/execute",
     {"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
     None, "api"),
    ("POST", "/v1/moa/eval",
     {"query": "hi", "candidates": ["deepseek-v3", "glm-4-flash"]},
     None, "api"),
    ("POST", "/v1/moa/similarity",
     {"query": "hi", "candidate_a": "hi", "candidate_b": "hello"},
     None, "api"),
    ("POST", "/v1/moa/flask",
     {"query": "hi", "response": "hello"},
     None, "api"),
    ("POST", "/v1/moa/benchmark",
     {"presets": ["fast"], "category": "reasoning", "limit": 1},
     None, "api"),
    ("POST", "/v1/moa/cost-pareto",
     {"prompts": ["a", "b", "c"], "presets": ["fast"]},
     None, "api"),
    ("GET", "/v1/moa/presets", None, None, "api"),
    ("GET", "/v1/moa/prompts", None, None, "api"),
    ("GET", "/v1/moa/prompts/aggregator_system", None, None, "api"),
    ("PUT", "/v1/moa/prompts/test_save_001", {"content": "test content"}, None, "api"),
    ("DELETE", "/v1/moa/prompts/test_save_001", None, None, "api"),
    ("GET", "/v1/route/preview", None, {"q": "hi"}, "api"),
    ("GET", "/v1/quota", None, None, "api"),
]

CAP_ENDPOINTS = [
    ("secret-scan", "POST", {"path": ".", "fail_on": 3, "no_block": True}, "api"),
    ("group-think-check", "POST", {
        "session_id": "s1",
        "members": [{"member_id": "a", "content": "x", "round": 0}],
    }, "api"),
    ("ensemble-vote", "POST", {
        "votes": [{"voter_id": "a", "candidate": "x", "confidence": 0.9, "reason": "ok"}],
        "method": "weighted",
    }, "api"),
    ("should-rebalance", "POST", {
        "stats": {"m1": {"tier": "standard", "endpoint_count": 1,
                         "success_count": 10, "fail_count": 0, "weight_sum": 100}},
    }, "api"),
    ("cost-estimate", "POST", {
        "input_tokens": 1000, "output_tokens": 500,
        "channels": [{"name": "m1", "cost_per_1k_input": 0.0005,
                      "cost_per_1k_output": 0.001, "tier": "standard"}],
        "include_fallback": True,
    }, "api"),
    ("gate-l0", "POST", {"query": "2+3"}, "api"),
    ("score-panel", "POST", {"query": "test", "answer": "test answer"}, "api"),
    ("models", "GET", None, "api"),  # special: GET
    ("calculate-max-tokens", "POST", {"model_id": "gpt-4o", "input_tokens": 1000,
                                      "requested_output": 2000, "safety_margin": 0.1}, "api"),
    ("estimate-cost", "POST", {"model_id": "gpt-4o", "input_tokens": 1000,
                               "output_tokens": 500}, "api"),
    ("quota-check", "POST", {
        "windows": [{"name": "5h", "limit_tokens": 100000, "used_history": []}],
        "requested": 1000,
    }, "api"),
    ("quota-record", "POST", {
        "windows": [{"name": "5h", "limit_tokens": 100000, "used_history": []}],
        "tokens": 100,
    }, "api"),
    ("moa-n-layer", "POST", {
        "query": "test",
        "proposers": [{"name": "p1", "model_id": "deepseek-v3"}],
        "aggregators": [
            {"name": "a1", "model_id": "glm-4-flash", "role": "layer1"},
            {"name": "a2", "model_id": "glm-4-flash", "role": "layer2"},
            {"name": "a3", "model_id": "glm-4-flash", "role": "layer3"},
        ],
    }, "api"),
    ("convergent-detect", "POST", {
        "proposals": [{"proposal_idx": 0, "author": "a", "text": "test"}],
    }, "api"),
    ("action-policy", "POST", {
        "command": "ls",
        "rules": [{"pattern": "rm -rf /", "action": "deny", "priority": 100}],
    }, "api"),
    ("embeddings", "POST", {"input": ["hello"], "dim": 64}, "api"),
    ("semantic-search", "POST", {
        "query": "hello", "documents": ["hello world", "goodbye"], "top_k": 2, "dim": 64,
    }, "api"),
    ("prompt-features", "POST", {"text": "test prompt"}, "api"),
    ("provider-health", "POST", {
        "providers": [{"provider": "p1", "total_calls": 100, "success_calls": 95,
                       "fail_calls": 5, "avg_latency_ms": 200, "p99_latency_ms": 1000,
                       "consecutive_failures": 0, "circuit_open": False}],
    }, "api"),
    ("context-clean", "POST", {"messages": [{"role": "user", "content": "hi"}],
                               "max_tokens": 100}, "api"),
    ("self-heal", "POST", {"errors": [{"error": "timeout", "count": 3}]}, "api"),
    ("multi-mode-synth", "POST", {
        "query": "test", "modes": [{"name": "a", "strategy": "single"}],
    }, "api"),
    ("conflict-arbitrate", "POST", {
        "conflicts": [{"option_a": "x", "option_b": "y"}],
    }, "api"),
    ("section-viability", "POST", {
        "sections": [{"text": "test", "position": 0}],
    }, "api"),
    ("feedback-iter", "POST", {
        "query": "test", "previous_output": "old", "feedback": "improve",
    }, "admin"),
    ("stream-aggregate", "POST", {
        "chunks": [{"text": "hello "}, {"text": "world"}],
    }, "api"),
    ("per-provider-rl", "POST", {
        "providers": [{"provider": "p1", "score": 80, "weight": 1.0}],
    }, "api"),
    ("tier-recalibrate", "POST", {
        "endpoints": [{"id": "e1", "tier": "standard", "score": 80}],
    }, "api"),
    ("consumption-intel", "POST", {"usage": [{"date": "2026-01-01", "tokens": 1000}]}, "api"),
    ("importance-score", "POST", {"text": "important text here"}, "api"),
    ("quorum-check", "POST", {"votes": [{"voter_id": "a", "approve": True}]}, "api"),
    ("model-entry", "POST", {"model_id": "test", "api_key": "test-key",
                             "api_base": "https://api.test.com", "provider": "openai"}, "api"),
    ("tool-replay", "POST", {"tool_call": {"name": "test", "args": {}}}, "api"),
    ("hook-events", "POST", {"event": "test", "data": {}}, "api"),
    ("meta-prompt", "POST", {"text": "test meta prompt"}, "api"),
    ("task-tree", "POST", {"task": "test task"}, "api"),
    ("distill", "POST", {"text": "long text to distill", "max_length": 50}, "api"),
    ("rerank", "POST", {"query": "test", "documents": ["a", "b"]}, "api"),
    ("goal-eval", "POST", {"goal": "test goal", "output": "test output"}, "api"),
    ("auto-converge", "POST", {"output": "test output"}, "api"),
    ("subagent-comms", "POST", {"sender": "a", "receiver": "b", "message": "hi"}, "api"),
    ("version", "POST", {"data": {"v": 1}}, "api"),
    ("config", "POST", {"key": "test", "value": "test"}, "api"),
    ("bubble", "POST", {"input": "test"}, "api"),
    ("worktree", "POST", {"action": "list"}, "admin"),
    ("route", "POST", {"query": "test query"}, "api"),
    ("session-lock", "POST", {"session_id": "s1", "action": "acquire"}, "api"),
    ("flask", "POST", {"query": "test", "response": "test response"}, "api"),
    ("elo", "POST", {"players": [{"id": "a"}, {"id": "b"}], "results": []}, "api"),
    ("brainstorm", "POST", {"topic": "test", "count": 3}, "api"),
    ("cross-iter", "POST", {"iterations": [{"text": "iter1"}]}, "api"),
    ("audit", "POST", {"action": "read", "resource": "/tmp/test"}, "api"),
    ("in-flight", "POST", {"request_id": "r1", "action": "check"}, "api"),
    ("mx", "POST", {"input": "test input"}, "api"),
    ("tier-promo", "POST", {"endpoint": "e1", "score": 90}, "api"),
    ("artifact", "POST", {"type": "test", "data": {}}, "api"),
    ("frozen", "POST", {"action": "check", "key": "test"}, "api"),
    ("turboquant", "POST", {"text": "test text"}, "api"),
    ("moa-engine", "POST", {"query": "test"}, "api"),
    ("acceptance", "POST", {"spec": "test", "output": "ok"}, "api"),
    ("llm-merge", "POST", {"outputs": ["a", "b"]}, "api"),
    ("grace", "POST", {"action": "check", "key": "test"}, "api"),
    ("rag-search", "POST", {"query": "test", "documents": ["doc1", "doc2"]}, "api"),
    ("plan-act", "POST", {"goal": "test", "context": {}}, "api"),
    ("channels", "POST", {"action": "list"}, "api"),
    ("reference-router", "POST", {"query": "test"}, "api"),
    ("checkpoint", "POST", {"action": "create", "name": "test"}, "admin"),
    ("audit2", "POST", {"action": "scan", "path": "."}, "api"),  # second /v1/capability/audit
    ("canary", "POST", {"prompt": "test"}, "api"),
    ("wrap-output", "POST", {"content": "test"}, "api"),
    ("fuzzy-dedup", "POST", {"items": ["a", "a", "b"]}, "api"),
    ("input-fingerprint", "POST", {"input": "test"}, "api"),
    ("tool-screening", "POST", {"tool": "test"}, "api"),
    ("anthropic-compat", "POST", {"prompt": "test"}, "api"),
    ("token-bucket", "POST", {"action": "consume", "tokens": 1}, "api"),
    ("request-dedup", "POST", {"request_id": "r1", "fingerprint": "fp1"}, "api"),
    ("trace", "POST", {"trace_id": "t1", "data": {}}, "api"),
]

FINAL: list[tuple[str, str, Any, Any, str]] = list(ENDPOINTS)
for ep in CAP_ENDPOINTS:
    name, method, body, auth = ep
    if method == "GET":
        FINAL.append(("GET", f"/v1/capability/{name}", None, None, auth))
    else:
        path = f"/v1/capability/{name}"
        if name == "audit2":
            path = f"/v1/capability/audit"
        FINAL.append((method, path, body, None, auth))

# Admin endpoints
FINAL.extend([
    ("POST", "/api/auth/login", {"username": "admin", "password": "TestPassword123!"}, None, "none"),
    ("POST", "/api/auth/change-password",
     {"old_password": "TestPassword123!", "new_password": "TestPassword123!"}, None, "admin"),
    ("GET", "/api/auth/me", None, None, "admin"),
    ("GET", "/api/endpoints", None, None, "admin"),
    ("POST", "/api/endpoints",
     {"endpoint_id": "test_ep_001", "provider": "openai", "model": "gpt-4o",
      "tier": "standard", "api_key_plain": "sk-test"}, None, "admin"),
    ("DELETE", "/api/endpoints/test_ep_001", None, None, "admin"),
    ("POST", "/api/endpoints/test_ep_001/toggle", None, None, "admin"),
    ("POST", "/api/endpoints/test_ep_001/reset-breaker", None, None, "admin"),
    ("GET", "/api/api-keys", None, None, "admin"),
    ("POST", "/api/api-keys", {"name": "test_key"}, None, "admin"),
    ("DELETE", "/api/api-keys/key_xxx", None, None, "admin"),
    ("GET", "/api/logs", None, None, "admin"),
    ("GET", "/api/stats", None, None, "admin"),
    ("GET", "/api/metrics", None, None, "admin"),
    ("GET", "/api/adapters", None, None, "admin"),
    ("GET", "/api/adapters/curl", None, None, "admin"),
    ("GET", "/", None, None, "none"),
    ("GET", "/webui/index.html", None, None, "none"),
])


def get_token(method, path, body):
    """Get auth token for endpoint."""
    if not ADMIN_TOKEN:
        return ""
    if method == "POST" and path == "/api/auth/login":
        return ""
    if path.startswith("/api/"):
        return f"Bearer {ADMIN_TOKEN}"
    return f"Bearer {API_KEY}"


def main():
    results = []
    start_t = time.time()
    n = len(FINAL)
    for i, (method, path, body, params, auth_type) in enumerate(FINAL, 1):
        url = BASE + path
        token = ADMIN_TOKEN if auth_type == "admin" else API_KEY
        if auth_type == "none":
            token = ""
        try:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            with httpx.Client(timeout=30.0) as client:
                if method == "GET":
                    r = client.get(url, headers=headers, params=params)
                elif method == "PUT":
                    r = client.put(url, headers=headers, json=body)
                elif method == "DELETE":
                    r = client.delete(url, headers=headers)
                else:
                    r = client.post(url, headers=headers, json=body)
            content = r.text[:300] if r.status_code >= 400 else r.text[:100]
            results.append({
                "i": i, "method": method, "path": path,
                "auth": auth_type,
                "status": r.status_code,
                "size": len(r.content),
                "ok_2xx": r.status_code < 300,
                "is_5xx": r.status_code >= 500,
                "snippet": content,
            })
        except Exception as e:
            results.append({
                "i": i, "method": method, "path": path,
                "auth": auth_type, "status": "ERR",
                "error": str(e)[:200], "ok_2xx": False, "is_5xx": False,
            })
    elapsed = time.time() - start_t
    summary = {
        "total": n,
        "elapsed_seconds": round(elapsed, 2),
        "by_status": {},
        "ok_2xx_count": sum(1 for r in results if r.get("ok_2xx")),
        "5xx_count": sum(1 for r in results if r.get("is_5xx")),
        "results": results,
    }
    for r in results:
        s = str(r.get("status"))
        summary["by_status"][s] = summary["by_status"].get(s, 0) + 1
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
