"""Test MoA dispatch and create a workflow."""
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
               body={"name": "moa_test", "quota_rpm": 1000, "quota_daily_tokens": 10000000},
               headers=H)
API_KEY = body["key"]
AH = {"Authorization": f"Bearer {API_KEY}"}

# 2. Dispatch: validate MoA config
s, body = call("POST", "/v1/agent/dispatch",
               body={"service": "moa", "method": "validate_config",
                     "payload": {
                         "proposers": [{"name": "p1", "model_id": "gpt-4o", "system_prompt": "x"}],
                         "aggregator": {"name": "a1", "model_id": "gpt-4o", "synthesis_prompt": "synth"},
                     }},
               headers=AH)
print(f"validate_config: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2))

# 3. Run MoA 3-layer (this is a real MoA call)
s, body = call("POST", "/v1/agent/dispatch",
               body={"service": "moa", "method": "run_three_layer",
                     "payload": {
                         "query": "What is Python?",
                         "proposers": [{"name": "p1", "model_id": "gpt-4o-mock", "system_prompt": "be concise"},
                                       {"name": "p2", "model_id": "gpt-4o-mock", "system_prompt": "be deep"}],
                         "aggregators": [{"name": "a1", "model_id": "gpt-4o-mock", "synthesis_prompt": "synth1"},
                                         {"name": "a2", "model_id": "gpt-4o-mock", "synthesis_prompt": "synth2"},
                                         {"name": "a3", "model_id": "gpt-4o-mock", "synthesis_prompt": "synth3"}],
                     }},
               headers=AH, timeout=60)
print(f"\nrun_three_layer: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2)[:1000])

# 4. Build a workflow template dynamically (use file:// as example)
# Actually, we need a way to register workflow. Let me use a script-level workflow.
# For now, let's test by sending the workflow steps directly to a hypothetical endpoint
# The /v1/agent/workflow/run endpoint looks up a registered workflow.
# We need to register one. Let me check if there's an admin endpoint.
# For now, just verify the endpoint exists.
s, body = call("POST", "/v1/agent/workflow/run", body={"name": "non_existent"}, headers=AH)
print(f"\nworkflow non-existent: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2)[:200])

print("\nMOA DISPATCH TESTS DONE")
