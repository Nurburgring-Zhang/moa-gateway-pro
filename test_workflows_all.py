"""Test all 7 builtin workflows with real data flow."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8088"

def call(method, path, body=None, headers=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        b = r.read().decode()
        try:
            return r.status, json.loads(b) if b else None
        except Exception:
            return r.status, b
    except urllib.error.HTTPError as e:
        b = e.read().decode()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b

# Login + key
s, body = call("POST", "/api/auth/login", body={"username": "admin", "password": "TestPass#2024"})
TOKEN = body["token"]
H = {"Authorization": f"Bearer {TOKEN}"}
s, body = call("POST", "/api/api-keys",
               body={"name": "wf_all_test", "quota_rpm": 10000, "quota_daily_tokens": 100000000},
               headers=H)
API_KEY = body["key"]
AH = {"Authorization": f"Bearer {API_KEY}"}

# List workflows
s, body = call("GET", "/v1/agent/workflows", headers=AH)
workflows = body.get("workflows", [])
print(f"workflows: {len(workflows)}")

# Test each workflow
test_inputs = {
    "moa_quality_pipeline": {
        "query": "Write a Python function to compute factorial",
        "proposers": [{"model_id": "gpt-4o-mock", "system_prompt": "be concise"}],
        "aggregator": {"model_id": "gpt-4o-mock", "synthesis_prompt": "synth"},
    },
    "consensus_pipeline": {
        "proposals": [
            {"proposal_idx": 0, "author": "a", "text": "use Python"},
            {"proposal_idx": 1, "author": "b", "text": "use Python with async"},
            {"proposal_idx": 2, "author": "c", "text": "use Java"},
        ],
        "viability_scores": {0: 0.8, 1: 0.7, 2: 0.3},
        "votes": [
            {"voter_id": "v1", "candidate": "Python", "confidence": 0.9, "reason": "best"},
            {"voter_id": "v2", "candidate": "Python", "confidence": 0.7, "reason": "good"},
            {"voter_id": "v3", "candidate": "Java", "confidence": 0.5, "reason": "alternative"},
        ],
    },
    "quality_gate": {
        "query": "What are the benefits of exercise?",
    },
    "knowledge_pipeline": {
        "query": "Python performance",
        "documents": ["Python performance tips: list comprehensions",
                      "JavaScript async await",
                      "Python generators yield items lazily"],
    },
    "quota_check": {
        "input_tokens": 1000,
        "output_tokens": 500,
        "channels": [{"name": "ch1", "cost_per_1k_input": 0.001, "cost_per_1k_output": 0.002,
                      "avg_latency_ms": 500, "reliability": 0.95}],
        "providers": [{"provider": "p1", "total_calls": 100, "success_count": 95,
                        "failure_count": 5, "rate_limit_hits": 0,
                        "consecutive_429s": 0, "consecutive_failures": 0,
                        "avg_latency_ms": 500, "p95_latency_ms": 800, "breaker_open": False}],
        "stats": {"p1": {"tier": "standard", "endpoint_count": 1, "success_count": 95,
                        "total_calls": 100, "avg_latency_ms": 500, "avg_cost": 0.001,
                        "last_24h_calls": 100, "cooldown_count": 0}},
    },
    "safety_pipeline": {
        "query": "Run command ls -la",
        "tool_name": "exec",
        "arguments": {"cmd": "ls -la"},
        "output": "file1.txt file2.txt",
        "source": "exec",
    },
    "rag_pipeline": {
        "query": "Python performance",
        "corpus": [{"id": "a", "text": "Python performance tips", "tags": ["python"]}],
        "documents": ["Python performance", "JavaScript performance"],
    },
}

results = []
for wf in workflows:
    name = wf["name"]
    if name not in test_inputs:
        results.append((name, "skip", "no test input"))
        continue
    s, body = call("POST", "/v1/agent/workflow/run",
                   body={"name": name, "input": test_inputs[name]},
                   headers=AH, timeout=60)
    if s == 200 and body.get("ok"):
        n_steps = len(body.get("steps", {}))
        total_latency = body.get("latency_ms", 0)
        results.append((name, "OK", f"{n_steps} steps, {total_latency:.1f}ms"))
    else:
        err = body.get("error", body.get("detail", ""))
        results.append((name, "FAIL", f"{s} {str(err)[:200]}"))

print()
for name, status, info in results:
    print(f"  [{status}] {name}: {info}")
print(f"\n{sum(1 for _, s, _ in results if s == 'OK')}/{len(results)} workflows passed")
