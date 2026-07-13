"""Wave 5 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 8971

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
key = s.create_api_key(name="wave5_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("tool-replay (3 calls)", "POST", "/v1/capability/tool-replay", {
        "proposals": [
            'I will use <tool_use name="search" id="t1">{"query": "python"}</tool_use> to find info.',
            'Let me also use <tool_use name="search" id="t2">{"query": "python"}</tool_use> and <tool_use name="calc" id="t3">{"expr": "2+2"}</tool_use>',
        ],
    }),
    ("hook-events (list)", "POST", "/v1/capability/hook-events", {
        "action": "list_events",
    }),
    ("hook-events (ralph)", "POST", "/v1/capability/hook-events", {
        "action": "ralph_advance",
        "stage": "analyze",
        "data": {"passed": True},
    }),
    ("meta-prompt (get_stages)", "POST", "/v1/capability/meta-prompt", {
        "action": "get_stages",
        "query": "How to design a scalable system?",
    }),
    ("meta-prompt (fuse)", "POST", "/v1/capability/meta-prompt", {
        "action": "fuse",
        "options": [
            "Use Python with FastAPI for the backend. This is a great choice.",
            "Use Node.js for the backend. It's also good.",
            "Use Python and FastAPI because of performance and ecosystem maturity.",
        ],
        "context": "design a scalable backend",
    }),
    ("task-tree (ready)", "POST", "/v1/capability/task-tree", {
        "tasks": [
            {"id": "root", "title": "Build system", "description": "main", "parent_id": None, "children_ids": ["a", "b"], "depends_on": []},
            {"id": "a", "title": "Backend", "description": "API", "parent_id": "root", "children_ids": [], "depends_on": []},
            {"id": "b", "title": "Frontend", "description": "UI", "parent_id": "root", "children_ids": [], "depends_on": ["a"]},
        ],
        "action": "ready",
    }),
    ("task-tree (cycles)", "POST", "/v1/capability/task-tree", {
        "tasks": [
            {"id": "x", "title": "X", "description": "x", "parent_id": None, "depends_on": ["y"]},
            {"id": "y", "title": "Y", "description": "y", "parent_id": None, "depends_on": ["x"]},
        ],
        "action": "cycles",
    }),
    ("distill (3 props)", "POST", "/v1/capability/distill", {
        "proposals": [
            "Python is great for web development. Use FastAPI for backend APIs. FastAPI is fast and easy to learn.",
            "Python is excellent for web servers. Use FastAPI for the API. The framework is mature and reliable.",
            "I recommend Node.js with Express for the backend. It's fast and widely used in industry.",
        ],
        "keep_ratio": 0.5,
        "evaluations": [
            {"TQ": 40, "CO": 35, "AP": 38, "SE": 30, "IN": 25},
            {"TQ": 45, "CO": 40, "AP": 42, "SE": 35, "IN": 30},
        ],
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