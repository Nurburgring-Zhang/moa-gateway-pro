"""Test all service methods via /v1/agent/list and a few invocations."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8088"

def call(method, path, body=None, headers=None, timeout=30):
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

# 1. Login + key
s, body = call("POST", "/api/auth/login", body={"username": "admin", "password": "TestPass#2024"})
TOKEN = body["token"]
H = {"Authorization": f"Bearer {TOKEN}"}
s, body = call("POST", "/api/api-keys",
               body={"name": "all_services_test", "quota_rpm": 10000, "quota_daily_tokens": 100000000},
               headers=H)
API_KEY = body["key"]
AH = {"Authorization": f"Bearer {API_KEY}"}

# 2. List all services
s, body = call("GET", "/v1/agent/list", headers=AH)
print(f"services: {s}, count: {len(body.get('agents', []))}")
total_methods = 0
for agent in body.get("agents", []):
    print(f"  {agent['name']}: {len(agent['methods'])} methods")
    total_methods += len(agent['methods'])
print(f"total methods: {total_methods}")

# 3. Test a method from each service
test_calls = [
    ("moa", "validate_config", {
        "proposers": [{"model_id": "gpt-4o", "system_prompt": "x"}],
        "aggregator": {"model_id": "gpt-4o", "synthesis_prompt": "y"},
    }),
    ("consensus", "check_group_think", {
        "members": [{"member_id": "a", "content": "agreed", "round": 0}],
    }),
    ("routing", "chain_info", {}),
    ("quality", "gate_l0", {"query": "What is Python?"}),
    ("agent", "list_mcp", {}),
    ("quota", "dedup_stats", {}),
    ("knowledge", "embed", {"input": ["hello", "world"], "dim": 32}),
    ("safety", "tool_screening", {"tool_name": "exec", "arguments": {"cmd": "ls"}}),
    ("observability", "audit", {"action": "stats"}),
    ("config", "config", {"action": "get", "key": "test"}),
]

ok_count = 0
for svc, method, payload in test_calls:
    s, body = call("POST", "/v1/agent/dispatch",
                   body={"service": svc, "method": method, "payload": payload},
                   headers=AH, timeout=10)
    if s == 200 and isinstance(body, dict) and body.get("ok"):
        ok_count += 1
        print(f"  {svc}.{method}: ok")
    else:
        err = ""
        if isinstance(body, dict):
            err = body.get("error", "") or body.get("detail", "")
        print(f"  {svc}.{method}: FAIL {s}: {err[:200]}")

print(f"\n{ok_count}/{len(test_calls)} service methods passed")
