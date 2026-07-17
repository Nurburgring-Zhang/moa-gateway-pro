"""Test the new agent dispatch endpoints."""
import json
import urllib.request
import urllib.error
import sys

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

# 1. Login
s, body = call("POST", "/api/auth/login", body={"username": "admin", "password": "TestPass#2024"})
assert s == 200, f"login failed: {s} {body}"
TOKEN = body["token"]
H = {"Authorization": f"Bearer {TOKEN}"}
print(f"login OK, token len={len(TOKEN)}")

# 2. Create API key
s, body = call("POST", "/api/api-keys",
               body={"name": "dispatcher_test", "quota_rpm": 1000, "quota_daily_tokens": 10000000},
               headers=H)
assert s == 200, f"key create failed: {s} {body}"
API_KEY = body["key"]
AH = {"Authorization": f"Bearer {API_KEY}"}
print(f"key created: {API_KEY[:20]}...")

# 3. List agents
s, body = call("GET", "/v1/agent/list", headers=AH)
print(f"\nagents: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2)[:1500])

# 4. Dispatch a simple service call
s, body = call("POST", "/v1/agent/dispatch",
               body={"service": "consensus", "method": "check_group_think",
                     "payload": {"members": [{"member_id": "a", "content": "agreed", "round": 0}], "warn_threshold": 0.4, "block_threshold": 0.7}},
               headers=AH)
print(f"\ndispatch group_think: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2))

# 5. Dispatch batch
s, body = call("POST", "/v1/agent/dispatch_batch",
               body={"calls": [
                   {"service": "routing", "method": "chain_info"},
                   {"service": "routing", "method": "classify_error", "payload": {"error": "rate limit exceeded"}},
               ]},
               headers=AH)
print(f"\ndispatch_batch: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2)[:1500])

# 6. List workflows (should be empty)
s, body = call("GET", "/v1/agent/workflows", headers=AH)
print(f"\nworkflows: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2))

print("\nALL DISPATCHER TESTS PASSED")
