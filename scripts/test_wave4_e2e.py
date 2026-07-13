"""Wave 4 server E2E — 5 新 capability 端点真实跑通"""
import json
import sys
import time
import urllib.request
import urllib.error
import os
import subprocess
from pathlib import Path

ROOT = Path.cwd()
PYTHON = sys.executable
PORT = 8961

env = os.environ.copy()
env["MOA_ADMIN_PASSWORD"] = "TestPass#2024"
env["PYTHONPATH"] = str(ROOT)
p = subprocess.Popen(
    [PYTHON, "-u", "-m", "uvicorn", "moa_gateway.server:app",
     "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    env=env, cwd=str(ROOT),
)
print(f"server pid={p.pid}")
time.sleep(5)

from moa_gateway.storage import get_storage
s = get_storage()
key = s.create_api_key(name="wave4_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        body = r.read().decode("utf-8", errors="replace")
        return r.status, json.loads(body) if body and body[0] in "{[" else body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:400]


tests = [
    ("tier-recalibrate (5 tiers)", "POST", "/v1/capability/tier-recalibrate", {
        "tiers": [
            {"tier": "free", "p50_latency_ms": 200, "p95_latency_ms": 400, "success_rate": 0.99, "cost_per_1k_input": 0.0001, "cost_per_1k_output": 0.0002, "daily_call_volume": 50000},
            {"tier": "lite", "p50_latency_ms": 500, "p95_latency_ms": 800, "success_rate": 0.98, "cost_per_1k_input": 0.0005, "cost_per_1k_output": 0.001, "daily_call_volume": 30000},
            {"tier": "standard", "p50_latency_ms": 800, "p95_latency_ms": 1500, "success_rate": 0.97, "cost_per_1k_input": 0.002, "cost_per_1k_output": 0.004, "daily_call_volume": 20000},
            {"tier": "premium", "p50_latency_ms": 2000, "p95_latency_ms": 4000, "success_rate": 0.85, "cost_per_1k_input": 0.01, "cost_per_1k_output": 0.02, "daily_call_volume": 5000},
            {"tier": "flagship", "p50_latency_ms": 5000, "p95_latency_ms": 10000, "success_rate": 0.6, "cost_per_1k_input": 0.05, "cost_per_1k_output": 0.1, "daily_call_volume": 1000},
        ]
    }),
    ("consumption-intel (vision)", "POST", "/v1/capability/consumption-intel", {
        "context": {"request_id": "r1", "query": "describe this image", "required_capabilities": ["vision"], "max_cost_per_1k": 0.01, "max_latency_ms": 5000, "priority": "high"},
        "endpoints": [
            {"endpoint_id": "e1", "model_id": "gpt-4o", "cost_per_1k_input": 0.005, "cost_per_1k_output": 0.015, "avg_latency_ms": 1000, "capabilities": ["vision", "tools"], "tier": "premium"},
            {"endpoint_id": "e2", "model_id": "deepseek-v3", "cost_per_1k_input": 0.0005, "cost_per_1k_output": 0.001, "avg_latency_ms": 800, "capabilities": ["text"], "tier": "standard"},
            {"endpoint_id": "e3", "model_id": "claude-opus", "cost_per_1k_input": 0.015, "cost_per_1k_output": 0.075, "avg_latency_ms": 2000, "capabilities": ["vision", "tools"], "tier": "flagship"},
        ]
    }),
    ("importance-score (5 msg)", "POST", "/v1/capability/importance-score", {
        "messages": [
            {"role": "system", "content": "You are a Python expert.", "timestamp": 1.0},
            {"role": "user", "content": "How do I use FastAPI?", "timestamp": 2.0},
            {"role": "assistant", "content": "Use FastAPI like this: ...", "timestamp": 3.0, "has_tool_calls": False},
            {"role": "tool", "content": "{'result': 'success'}", "timestamp": 4.0, "is_tool_result": True},
            {"role": "assistant", "content": "The result is...", "timestamp": 5.0, "is_decision": True},
        ],
        "top_k": 3,
    }),
    ("quorum-check (3/3)", "POST", "/v1/capability/quorum-check", {
        "participants": [
            {"participant_id": "p1", "responded": True, "response": "answer1", "responded_at": 100.0},
            {"participant_id": "p2", "responded": True, "response": "answer2", "responded_at": 110.0},
            {"participant_id": "p3", "responded": True, "response": "answer3", "responded_at": 120.0},
        ],
        "required": 3,
        "grace_seconds": 30.0,
        "at": 130.0,
    }),
    ("quorum-check (judge)", "POST", "/v1/capability/quorum-check", {
        "participants": [],
        "judge_response": "[[rating_a]] 8",
    }),
    ("model-entry (filter+sort)", "POST", "/v1/capability/model-entry", {
        "models": [
            {"model_id": "gpt-4o", "provider": "openai", "family": "gpt-4o", "context_window": 128000, "max_output": 4096, "modalities": ["TEXT", "IMAGE"], "supports_tools": True, "supports_vision": True, "supports_reasoning": False, "supports_streaming": True, "input_cost_per_1k": 0.005, "output_cost_per_1k": 0.015},
            {"model_id": "deepseek-v3", "provider": "deepseek", "family": "deepseek", "context_window": 64000, "max_output": 8000, "modalities": ["TEXT"], "supports_tools": True, "supports_vision": False, "supports_reasoning": False, "supports_streaming": True, "input_cost_per_1k": 0.00027, "output_cost_per_1k": 0.0011},
            {"model_id": "claude-opus", "provider": "anthropic", "family": "claude-3", "context_window": 200000, "max_output": 4096, "modalities": ["TEXT", "IMAGE"], "supports_tools": True, "supports_vision": True, "supports_reasoning": True, "supports_streaming": True, "input_cost_per_1k": 0.015, "output_cost_per_1k": 0.075},
        ],
        "filter": {"capability": "supports_vision", "value": True},
        "sort": "cost_asc",
    }),
]

passed = 0
failed = []
for name, method, path, body in tests:
    code, resp = call(method, path, body)
    short = json.dumps(resp)[:200] if isinstance(resp, dict) else str(resp)[:200]
    if code == 200:
        print(f"  ✓ {name:40s} {code}: {short}")
        passed += 1
    else:
        print(f"  ✗ {name:40s} {code}: {short}")
        failed.append((name, code, short))

print(f"\n=== {passed}/{len(tests)} passed ===")
if failed:
    sys.exit(1)
p.terminate()
p.wait(timeout=5)