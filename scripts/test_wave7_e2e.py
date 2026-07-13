"""Wave 7 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 8991

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
key = s.create_api_key(name="wave7_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("config (set+get)", "POST", "/v1/capability/config", {
        "action": "set", "key": "model", "value": "gpt-4o", "layer": "user",
    }),
    ("config (get after set)", "POST", "/v1/capability/config", {
        "action": "get", "key": "model",
    }),
    ("bubble (escalate)", "POST", "/v1/capability/bubble", {
        "action": "escalate", "parent_id": "p1", "agent_id": "a1", "action_desc": "rm -rf", "reason": "dangerous",
    }),
    ("bubble (should_continue)", "POST", "/v1/capability/bubble", {
        "action": "schedule", "agent_id": "ag1", "event_type": "trigger",
    }),
    ("worktree (snapshot)", "POST", "/v1/capability/worktree", {
        "action": "snapshot", "repo_path": "D:\\MoA Gateway Pro",
    }),
    ("route (auto_detect)", "POST", "/v1/capability/route", {
        "action": "auto_detect", "task": "fix typo in header",
    }),
    ("route (priority)", "POST", "/v1/capability/route", {
        "action": "priority", "severity": "critical",
    }),
    ("session-lock (try_acquire)", "POST", "/v1/capability/session-lock", {
        "action": "try_acquire", "lock_id": "l1", "session_id": "s1", "ttl": 60.0,
    }),
    ("session-lock (register_mcp)", "POST", "/v1/capability/session-lock", {
        "action": "register_mcp", "name": "echo", "description": "echo tool", "parameters": {"msg": "string"}, "returns": "ok",
    }),
    ("session-lock (invoke_mcp)", "POST", "/v1/capability/session-lock", {
        "action": "invoke_mcp", "name": "echo", "kwargs": {"msg": "hi"},
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