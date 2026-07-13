"""Wave 10 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 9021

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
key = s.create_api_key(name="wave10_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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


# 60 msg for turboquant hard cap test
msgs = []
for i in range(70):
    role = "system" if i == 0 else ("user" if i % 2 == 1 else "assistant")
    msgs.append({"role": role, "content": f"msg {i}", "timestamp": float(i)})

tests = [
    ("turboquant (should_compress 70)", "POST", "/v1/capability/turboquant", {
        "action": "should_compress",
        "messages": msgs,
        "hard_cap": 60,
    }),
    ("turboquant (apply Q4)", "POST", "/v1/capability/turboquant", {
        "action": "apply",
        "messages": msgs,
        "level": "Q4",
        "hard_cap": 60,
        "preserve": 30,
    }),
    ("moa-engine (validate only)", "POST", "/v1/capability/moa-engine", {
        "proposers": [
            {"model_id": "gpt-4o", "system_prompt": "You are expert A."},
            {"model_id": "deepseek-v3", "system_prompt": "You are expert B."},
            {"model_id": "claude-opus", "system_prompt": "You are expert C."},
        ],
        "aggregator": {"model_id": "gpt-4o", "synthesis_prompt": "Synthesize"},
        "validate_only": True,
    }),
    ("moa-engine (run mock)", "POST", "/v1/capability/moa-engine", {
        "proposers": [
            {"model_id": "gpt-4o", "system_prompt": "Expert A"},
            {"model_id": "deepseek-v3", "system_prompt": "Expert B"},
        ],
        "aggregator": {"model_id": "gpt-4o", "synthesis_prompt": "Combine"},
        "query": "What is Python?",
    }),
    ("acceptance (parse_ears)", "POST", "/v1/capability/acceptance", {
        "action": "parse_ears",
        "text": "When user clicks submit, the form should validate. While in error state, show banner. Within 5 seconds, the system should respond.",
    }),
    ("acceptance (validate_pattern)", "POST", "/v1/capability/acceptance", {
        "action": "validate_pattern",
        "criterion": {"id": "ac1", "given": "valid user", "when": "clicks", "then": "logged in"},
    }),
    ("llm-merge (CONCAT)", "POST", "/v1/capability/llm-merge", {
        "action": "merge",
        "strategy": "concat",
        "responses": [
            {"source": "gpt-4o", "text": "answer A", "tokens": 100, "latency_ms": 800, "cost_usd": 0.005, "confidence": 0.9},
            {"source": "deepseek-v3", "text": "answer B", "tokens": 80, "latency_ms": 600, "cost_usd": 0.0005, "confidence": 0.8},
        ],
    }),
    ("llm-merge (WEIGHTED)", "POST", "/v1/capability/llm-merge", {
        "action": "merge",
        "strategy": "weighted",
        "responses": [
            {"source": "gpt-4o", "text": "high conf", "tokens": 100, "latency_ms": 800, "cost_usd": 0.005, "confidence": 0.95},
            {"source": "deepseek-v3", "text": "low conf", "tokens": 80, "latency_ms": 600, "cost_usd": 0.0005, "confidence": 0.3},
        ],
    }),
    ("llm-merge (fallback success)", "POST", "/v1/capability/llm-merge", {
        "action": "fallback",
        "providers": ["p1", "p2", "p3"],
    }),
    ("llm-merge (fallback p1 fail)", "POST", "/v1/capability/llm-merge", {
        "action": "fallback",
        "providers": ["p1", "p2", "p3"],
        "fail_at": ["p1"],
    }),
    ("grace (register)", "POST", "/v1/capability/grace", {
        "action": "register", "name": "lint",
    }),
    ("grace (record_fail+status)", "POST", "/v1/capability/grace", {
        "action": "record_fail", "check_id": "PLACEHOLDER", "at": 1000.0,
    }),
]

passed = 0
failed = []
last_check_id = None
for name, method, path, body in tests:
    # 动态 check_id 替换
    if last_check_id and body.get("check_id") == "PLACEHOLDER":
        body = {**body, "check_id": last_check_id}
    code, resp = call(method, path, body)
    short = json.dumps(resp)[:200] if isinstance(resp, dict) else str(resp)[:200]
    if code == 200:
        print(f"  ✓ {name:40s} {code}: {short}")
        passed += 1
        if isinstance(resp, dict) and "check_id" in resp:
            last_check_id = resp["check_id"]
    else:
        print(f"  ✗ {name:40s} {code}: {short}")
        failed.append((name, code, short))

print(f"\n=== {passed}/{len(tests)} passed ===")
if failed:
    sys.exit(1)
p.terminate()
p.wait(timeout=5)