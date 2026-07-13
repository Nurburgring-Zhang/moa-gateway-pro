"""Wave 9 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 9011

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
key = s.create_api_key(name="wave9_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("in-flight (start+complete)", "POST", "/v1/capability/in-flight", {
        "action": "start", "session_id": "s1", "phase": "analyze",
    }),
    ("in-flight (merge)", "POST", "/v1/capability/in-flight", {
        "action": "merge",
        "checkpoints": [
            {"session_id": "s1", "phase": "analyze", "data": {"run_count": 3, "plan_status": "draft"}},
            {"session_id": "s2", "phase": "implement", "data": {"run_count": 5, "plan_status": "final"}},
        ],
    }),
    ("mx (parse 6 tags)", "POST", "/v1/capability/mx", {
        "action": "parse",
        "text": "# mx:NOTE: a\n// mx:WARN: b\n/* mx:ANCHOR: c */\n<!-- mx:REASON: d -->\n# mx:TODO: e\n# mx:SPEC: f",
        "file_path": "test.py",
    }),
    ("mx (cli list)", "POST", "/v1/capability/mx", {
        "action": "cli",
        "text": "# mx:NOTE: important\n# mx:WARN: danger",
        "command": "list",
    }),
    ("tier-promo (classify 5 events)", "POST", "/v1/capability/tier-promo", {
        "action": "classify",
        "evidence": [
            {"event_type": "test", "timestamp": i, "weight": 1.0} for i in range(5)
        ],
    }),
    ("tier-promo (can_spawn)", "POST", "/v1/capability/tier-promo", {
        "action": "can_spawn",
        "parent_id": "p1",
        "allowed_children": ["a1", "a2"],
        "child_id": "a1",
    }),
    ("artifact (register+list)", "POST", "/v1/capability/artifact", {
        "action": "register",
        "id": "agent-1",
        "name": "Backend Agent",
        "type": "agent",
        "description": "Handles backend API calls",
    }),
    ("artifact (validate)", "POST", "/v1/capability/artifact", {
        "action": "validate",
        "id": "x",
        "name": "x",
        "type": "skill",
        "description": "",
    }),
    ("frozen (add+is_frozen)", "POST", "/v1/capability/frozen", {
        "action": "add",
        "path": "/core/auth.py",
        "zone": "frozen-canonical",
        "reason": "critical security module",
    }),
    ("frozen (is_frozen)", "POST", "/v1/capability/frozen", {
        "action": "is_frozen",
        "path": "/core/auth.py",
    }),
    ("frozen (list sentinels)", "POST", "/v1/capability/frozen", {
        "action": "list_sentinels",
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