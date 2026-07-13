"""Wave 3 server E2E — 5 新 capability 端点真实跑通"""
import json
import sys
import time
import urllib.request
import urllib.error
import os
import subprocess
import asyncio
from pathlib import Path

ROOT = Path.cwd()
PYTHON = sys.executable
PORT = 8951

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
key = s.create_api_key(name="wave3_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("conflict-arbitrate (2 opts)", "POST", "/v1/capability/conflict-arbitrate", {
        "options": [
            {"option_id": "use_python", "description": "Use Python with FastAPI", "supporting_proposals": [0, 2], "viability_scores": {0: 0.9, 2: 0.85}, "command_compilable": True, "empirical_evidence_count": 3},
            {"option_id": "use_node", "description": "Use Node.js with Express", "supporting_proposals": [1], "viability_scores": {1: 0.7}, "command_compilable": True, "empirical_evidence_count": 1},
        ],
    }),
    ("section-viability (good)", "POST", "/v1/capability/section-viability", {
        "text": "## Architecture\nWe should use microservices with FastAPI. The system must handle 1000 RPS. We will deploy on Kubernetes.\n\n## Database\nPostgreSQL should be the primary database. It must support JSON columns.",
    }),
    ("section-viability (short)", "POST", "/v1/capability/section-viability", {
        "text": "## Brief\nToo short.",
    }),
    ("stream-aggregate (success)", "POST", "/v1/capability/stream-aggregate", {
        "prompt": "Hello world this is a test prompt for streaming aggregate " * 10,
        "model": "mock-stream-v1",
        "fail_prob": 0.0,
    }),
    ("stream-aggregate (fallback)", "POST", "/v1/capability/stream-aggregate", {
        "prompt": "Force fallback test " * 5,
        "model": "mock-stream-v1",
        "fail_prob": 1.0,
    }),
    ("per-provider-rl (check)", "POST", "/v1/capability/per-provider-rl", {
        "limits": {"deepseek-v3": {"provider": "deepseek-v3", "max_requests_per_minute": 60, "max_inputs_per_minute": 100000, "max_concurrent": 5}},
        "action": "check",
        "provider": "deepseek-v3",
    }),
    ("per-provider-rl (record + exceed)", "POST", "/v1/capability/per-provider-rl", {
        "limits": {"deepseek-v3": {"provider": "deepseek-v3", "max_requests_per_minute": 2, "max_inputs_per_minute": 100000, "max_concurrent": 5}},
        "action": "record",
        "provider": "deepseek-v3",
        "request_count": 3,
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