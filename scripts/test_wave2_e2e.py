"""Wave 2 server E2E — 5 新 capability 端点真实跑通"""
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
PORT = 8941

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
key = s.create_api_key(name="wave2_e2e", quota_rpm=1000, quota_daily_tokens=999999999)["key"]
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
    ("prompt-features (code)", "POST", "/v1/capability/prompt-features", {
        "text": "Please write a Python function that uses FastAPI to create a REST API. ```python\nfrom fastapi import FastAPI\napp = FastAPI()\n```"
    }),
    ("prompt-features (zh)", "POST", "/v1/capability/prompt-features", {
        "text": "请帮我写一个Python脚本,使用FastAPI框架。这是一个代码任务。"
    }),
    ("provider-health (good)", "POST", "/v1/capability/provider-health", {
        "providers": [
            {"provider": "deepseek-v3", "total_calls": 100, "success_count": 99, "failure_count": 1, "rate_limit_hits": 0, "consecutive_429s": 0, "consecutive_failures": 0, "avg_latency_ms": 800, "p95_latency_ms": 1500, "breaker_open": False},
            {"provider": "gpt-4o", "total_calls": 100, "success_count": 80, "failure_count": 20, "rate_limit_hits": 5, "consecutive_429s": 2, "consecutive_failures": 1, "avg_latency_ms": 1200, "p95_latency_ms": 3000, "breaker_open": False},
            {"provider": "broken", "total_calls": 50, "success_count": 0, "failure_count": 50, "rate_limit_hits": 10, "consecutive_429s": 0, "consecutive_failures": 5, "avg_latency_ms": 500, "p95_latency_ms": 500, "breaker_open": True},
        ]
    }),
    ("context-clean (mixed)", "POST", "/v1/capability/context-clean", {
        "messages": [
            {"role": "assistant", "content": "Hi! How can I help?"},
            {"role": "user", "content": "Tell me about Python"},
            {"role": "user", "content": "And FastAPI?"},
            {"role": "system", "content": "You are a Python expert"},
            {"role": "tool", "content": "orphan tool"},
        ]
    }),
    ("self-heal (record_failure)", "POST", "/v1/capability/self-heal", {
        "endpoints": [
            {"endpoint_id": "ep1", "tier": "primary"},
            {"endpoint_id": "ep2", "tier": "secondary"},
        ],
        "action": "record_failure",
        "endpoint_id": "ep1",
        "at": 100.0,
    }),
    ("self-heal (auto_balance)", "POST", "/v1/capability/self-heal", {
        "endpoints": [
            {"endpoint_id": "ep1", "tier": "fallback", "original_tier": "primary", "in_cooldown": True, "cooldown_until": 50.0},
        ],
        "action": "auto_balance",
        "at": 200.0,
    }),
    ("multi-mode-synth (classification)", "POST", "/v1/capability/multi-mode-synth", {
        "mode": "classification",
        "proposals": [
            {"proposal_idx": 0, "author": "a", "text": "Use Python with FastAPI. Here's the code: ```python\nprint('hello')\n```"},
            {"proposal_idx": 1, "author": "b", "text": "What is the meaning of life? It's a philosophical question."},
        ]
    }),
    ("multi-mode-synth (final_selection)", "POST", "/v1/capability/multi-mode-synth", {
        "mode": "final_selection",
        "proposals": [
            {"proposal_idx": 0, "author": "a", "text": "First proposal"},
            {"proposal_idx": 1, "author": "b", "text": "Second proposal"},
            {"proposal_idx": 2, "author": "c", "text": "Third proposal"},
        ],
        "scores": {0: 0.7, 1: 0.9, 2: 0.5},
    }),
    ("multi-mode-synth (integrated)", "POST", "/v1/capability/multi-mode-synth", {
        "mode": "integrated_synthesis",
        "proposals": [
            {"proposal_idx": 0, "author": "a", "text": "Python is great for web. Python is simple. Use FastAPI for performance."},
            {"proposal_idx": 1, "author": "b", "text": "Python is a great language. Use FastAPI for the API. It's fast and easy."},
        ],
        "target_chars": 200,
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
    print(f"Failed: {failed}")
    sys.exit(1)
p.terminate()
p.wait(timeout=5)