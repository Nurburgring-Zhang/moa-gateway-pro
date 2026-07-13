"""Wave 6 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 8981

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
key = s.create_api_key(name="wave6_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("rerank (3 docs)", "POST", "/v1/capability/rerank", {
        "query": "python web framework",
        "documents": [
            "Python is a high-level programming language",
            "FastAPI is a modern Python web framework for building APIs",
            "JavaScript is used for frontend development",
            "Python with FastAPI is great for web servers and APIs",
        ],
        "top_n": 3,
        "latency_budget_ms": 1000,
    }),
    ("goal-eval (2 goals)", "POST", "/v1/capability/goal-eval", {
        "goals": [
            {"id": "g1", "description": "Output mentions Python", "tier": 1, "criteria": "contains: Python"},
            {"id": "g2", "description": "Output is about web dev", "tier": 2, "criteria": "web framework API server"},
        ],
        "output": "Python FastAPI is a great web framework for building APIs",
        "generate_ceiling": True,
        "claim": "Python FastAPI is best for APIs",
        "evidence": ["50 RPS benchmark", "Type hints native"],
        "baseline": "Express.js at 30 RPS",
        "gaps": ["No GraphQL support yet"],
        "residual_risk": "Single-node failure",
    }),
    ("auto-converge (3 rounds)", "POST", "/v1/capability/auto-converge", {
        "state": {"iteration": 2, "best_score_history": [0.6, 0.7], "stagnation_count": 0, "converged": False},
        "new_score": 0.71,
        "classify_events": 5,
        "calibrate_score": 0.8,
        "calibrate_samples": 15,
    }),
    ("subagent-comms (send+broadcast)", "POST", "/v1/capability/subagent-comms", {
        "action": "send",
        "session_id": "s1",
        "to_session": "s2",
        "content": "Hello from s1",
    }),
    ("subagent-comms (create_task)", "POST", "/v1/capability/subagent-comms", {
        "action": "create_task",
        "session_id": "s1",
        "title": "Build API",
        "assignee": "s2",
    }),
    ("subagent-comms (acquire lock)", "POST", "/v1/capability/subagent-comms", {
        "action": "acquire",
        "lock_id": "l1",
        "holder": "s1",
    }),
    ("version (add+latest)", "POST", "/v1/capability/version", {
        "action": "add",
        "proposal_id": "p1",
        "content": "v1 content: use Python",
    }),
    ("version (parse_rating)", "POST", "/v1/capability/version", {
        "action": "parse_rating",
        "judge_response": "[[rating_a]] 8",
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