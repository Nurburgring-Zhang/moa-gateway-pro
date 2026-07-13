"""Wave 8 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 9001

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
key = s.create_api_key(name="wave8_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("flask (good answer)", "POST", "/v1/capability/flask", {
        "answer": "We should use Python with FastAPI. Step 1: Install via pip. See https://fastapi.tiangolo.com for docs. The framework is fast and reliable.",
        "query": "Python web framework recommendation",
    }),
    ("elo (record matches)", "POST", "/v1/capability/elo", {
        "action": "record",
        "model_ids": ["gpt-4o", "deepseek-v3", "claude-opus"],
        "matches": [
            {"winner_id": "gpt-4o", "loser_id": "deepseek-v3", "timestamp": 1.0},
            {"winner_id": "gpt-4o", "loser_id": "claude-opus", "timestamp": 2.0},
            {"winner_id": "deepseek-v3", "loser_id": "claude-opus", "timestamp": 3.0},
        ],
    }),
    ("brainstorm (ideas)", "POST", "/v1/capability/brainstorm", {
        "action": "ideas",
        "topic": "scalable backend",
    }),
    ("brainstorm (decide)", "POST", "/v1/capability/brainstorm", {
        "action": "decide",
        "topic": "Choose backend",
        "options": ["Python + FastAPI", "Node.js + Express", "Go + Gin"],
    }),
    ("cross-iter (convergence)", "POST", "/v1/capability/cross-iter", {
        "action": "convergence",
        "iters": [
            {"iter_idx": 1, "proposals": ["Use Python FastAPI for backend"], "best_score": 75, "best_proposal_idx": 0, "summary": "Python FastAPI recommended"},
            {"iter_idx": 2, "proposals": ["Use Python FastAPI which is great"], "best_score": 80, "best_proposal_idx": 0, "summary": "Confirmed Python FastAPI choice"},
        ],
    }),
    ("cross-iter (step5 SKIP)", "POST", "/v1/capability/cross-iter", {
        "action": "step5",
        "step5_mode": "skip",
        "iters": [
            {"iter_idx": 1, "proposals": ["x"], "best_score": 90, "best_proposal_idx": 0, "summary": "x"},
        ],
    }),
    ("audit (read)", "POST", "/v1/capability/audit", {
        "action_id": "a1",
        "action_data": {"action": "read", "resource": "config.yaml"},
    }),
    ("audit (delete)", "POST", "/v1/capability/audit", {
        "action_id": "a2",
        "action_data": {"action": "delete", "resource": "old_logs"},
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