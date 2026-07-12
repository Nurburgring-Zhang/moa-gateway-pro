"""端到端测 8 个新 server 端点"""
import json
import sys
import time
import urllib.request
import urllib.error
sys.path.insert(0, ".")
import subprocess
import os
import signal
from pathlib import Path

ROOT = Path.cwd()
PYTHON = sys.executable
PORT = 8926

# 启动 server
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

# 拿 key
from moa_gateway.storage import get_storage
s = get_storage()
key = s.create_api_key(name="cap2_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
print(f"key: {key[:24]}...")

h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
BASE = f"http://127.0.0.1:{PORT}"


def call(method, path, body=None, qs=None):
    url = f"{BASE}{path}"
    if qs:
        url += "?" + qs
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=15)
        body = r.read().decode("utf-8", errors="replace")
        return r.status, json.loads(body) if body and body[0] in "{[" else body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")[:300]


tests = [
    ("gate-l0 (arithmetic)", "POST", "/v1/capability/gate-l0", {"query": "2+3"}),
    ("gate-l0 (complex)", "POST", "/v1/capability/gate-l0", {"query": "design a distributed cache system"}),
    ("models (all)", "GET", "/v1/capability/models", None),
    ("models (provider=deepseek)", "GET", "/v1/capability/models", None, "provider=deepseek"),
    ("calculate-max-tokens", "POST", "/v1/capability/calculate-max-tokens",
     {"model_id": "gpt-4o", "input_tokens": 1000, "requested_output": 2000}),
    ("estimate-cost", "POST", "/v1/capability/estimate-cost",
     {"model_id": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}),
    ("score-panel", "POST", "/v1/capability/score-panel",
     {"query": "What is Python?",
      "answer": "Python is a high-level language. Step 1: Install from python.org. See https://python.org. However, beginners may find it tricky."}),
    ("ensemble-vote", "POST", "/v1/capability/ensemble-vote",
     {"votes": [
         {"voter_id": "a", "candidate": "A", "confidence": 0.9, "reason": "x"},
         {"voter_id": "b", "candidate": "A", "confidence": 0.8, "reason": "y"},
         {"voter_id": "c", "candidate": "B", "confidence": 0.5, "reason": "z"},
     ], "method": "weighted"}),
    ("should-rebalance", "POST", "/v1/capability/should-rebalance",
     {"stats": {"deepseek-v3": {"tier": "standard", "endpoint_count": 1,
                                "success_count": 50, "total_calls": 100,
                                "avg_latency_ms": 800, "avg_cost": 0.001,
                                "last_24h_calls": 100, "cooldown_count": 0}},
      "config": {"high_threshold": 0.8, "low_threshold": 0.2}}),
    ("cost-estimate", "POST", "/v1/capability/cost-estimate",
     {"input_tokens": 1000, "output_tokens": 500,
      "channels": [{"name": "deepseek-v3", "cost_per_1k_input": 0.0005,
                    "cost_per_1k_output": 0.001, "avg_latency_ms": 800,
                    "reliability": 0.95}],
      "include_fallback": True}),
    ("secret-scan", "POST", "/v1/capability/secret-scan", {"path": ".", "fail_on": 3}),
    ("group-think", "POST", "/v1/capability/group-think-check",
     {"session_id": "test-1", "members": [
         {"member_id": "a", "content": "Great point, we all agree this is brilliant."},
         {"member_id": "b", "content": "Could not agree more, indeed, settled then."},
     ]}),
]

passed = 0
failed = []
for t in tests:
    name = t[0]
    method = t[1]
    path = t[2]
    body = t[3]
    qs = t[4] if len(t) > 4 else None
    code, resp = call(method, path, body, qs)
    short = json.dumps(resp)[:200] if isinstance(resp, dict) else str(resp)[:200]
    if code == 200:
        print(f"  ✓ {name:40s} {code}: {short}")
        passed += 1
    else:
        print(f"  ✗ {name:40s} {code}: {short}")
        failed.append((name, code, short))

print(f"\n=== {passed}/{len(tests)} passed ===")
if failed:
    print(f"Failed: {failed}")
    sys.exit(1)

# 关闭 server
p.terminate()
p.wait(timeout=5)