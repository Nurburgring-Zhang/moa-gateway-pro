"""Test workflow register + run with real inter-module data flow."""
import json
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8088"

def call(method, path, body=None, headers=None, timeout=60):
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
               body={"name": "wf_test", "quota_rpm": 1000, "quota_daily_tokens": 10000000},
               headers=H)
API_KEY = body["key"]
AH = {"Authorization": f"Bearer {API_KEY}"}

# Register a workflow: MoA validate → MoA run → FLASK score
workflow = {
    "name": "moa_with_quality",
    "description": "Validate config, run MoA, then score with FLASK",
    "steps": [
        {
            "name": "validate",
            "service": "moa",
            "method": "validate_config",
            "payload": {
                "proposers": [
                    {"model_id": "gpt-4o-mock", "system_prompt": "be concise"}
                ],
                "aggregator": {"model_id": "gpt-4o-mock", "synthesis_prompt": "synth"},
            },
            "depends_on": [],
            "input_map": {},
            "optional": True,  # validation failure shouldn't stop workflow
            "description": "Validate MoA config (best-effort)",
        },
        {
            "name": "run_moa",
            "service": "moa",
            "method": "run_engine",
            "payload": {
                "query": "$input.query",
            },
            "depends_on": ["validate"],
            "input_map": {
                "proposers": "$input.proposers",
                "aggregator": "$input.aggregator",
            },
            "description": "Run MoA engine with user input",
        },
        {
            "name": "score",
            "service": "quality",
            "method": "score_flask",
            "payload": {},
            "depends_on": ["run_moa"],
            "input_map": {
                "query": "$input.query",
                "response": "run_moa.result",
            },
            "description": "FLASK score the MoA output",
        },
    ],
}

s, body = call("POST", "/v1/agent/workflow/register", body=workflow, headers=AH)
print(f"register: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2))

# List workflows
s, body = call("GET", "/v1/agent/workflows", headers=AH)
print(f"\nlist workflows: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2)[:500])

# Run workflow
s, body = call("POST", "/v1/agent/workflow/run",
               body={"name": "moa_with_quality",
                     "input": {
                         "query": "Write a Python function to reverse a linked list",
                         "proposers": [{"model_id": "gpt-4o-mock", "system_prompt": "be concise"}],
                         "aggregator": {"model_id": "gpt-4o-mock", "synthesis_prompt": "synth"},
                     }},
               headers=AH, timeout=120)
print(f"\nrun workflow: {s}")
print(json.dumps(body, ensure_ascii=False, indent=2)[:2000])

print("\nWORKFLOW TEST DONE")
