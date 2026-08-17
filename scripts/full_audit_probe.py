#!/usr/bin/env python3
"""Full-capability live audit probe — Round N.

Starts a fresh server (rm -rf data), waits for ready, then probes every
capability group including concurrent load. Prints PASS/FAIL summary.
Exit 0 if no unexpected 5xx, 1 otherwise.

Usage: AUDIT_KEY=... AUDIT_PW=... python3 scripts/full_audit_probe.py [ROUND]
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8910"
KEY = os.environ.get("AUDIT_KEY", "r6audit-key-001")
PW = os.environ.get("AUDIT_PW", "R6Audit#Pass2024")
SECRET = os.environ.get("AUDIT_SECRET", "r6-audit-secret-minimum-32-characters-long-xxxxx")


def _req(method, path, headers=None, body=None, timeout=25):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    h = headers or {}
    if data and "Content-Type" not in h:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def main():
    round_n = sys.argv[1] if len(sys.argv) > 1 else "?"
    # Fresh data
    subprocess.run("rm -rf data && mkdir -p data", shell=True, cwd=os.getcwd())
    env = {
        **os.environ,
        "MOA_ADMIN_PASSWORD": PW, "MOA_GATEWAY_KEY": KEY, "MOA_JWT_SECRET": SECRET,
    }
    proc = subprocess.Popen(
        ["python3", "-m", "uvicorn", "moa_gateway.server:app",
         "--host", "127.0.0.1", "--port", "8910", "--log-level", "warning"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        # wait ready
        ready = False
        for _ in range(40):
            if _req("GET", "/health/ready") == 200:
                ready = True
                break
            time.sleep(1)
        if not ready:
            print(f"ROUND {round_n}: SERVER NOT READY")
            return 1

        AUTH = {"Authorization": f"Bearer {KEY}"}
        # admin token
        code = _req("POST", "/api/auth/login", {"Content-Type": "application/json"},
                    {"username": "admin", "password": PW})
        # get token via reading response
        req = urllib.request.Request(f"{BASE}/api/auth/login",
            data=json.dumps({"username": "admin", "password": PW}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            TOKEN = json.loads(r.read())["token"]
        ADMIN = {"Authorization": f"Bearer {TOKEN}"}

        passed = failed = 0
        fails = []

        def check(label, method, path, headers, body=None):
            nonlocal passed, failed
            c = _req(method, path, headers, body)
            if c >= 500 or c == 0:
                failed += 1
                fails.append(f"{c} {label} ({path})")
            else:
                passed += 1

        # === chat/streaming/MoA ===
        check("chat", "POST", "/v1/chat/completions", AUTH,
              {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
        check("chat-stream", "POST", "/v1/chat/completions", AUTH,
              {"model": "auto", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5, "stream": True})
        for s in ("parallel", "compose", "judge", "chain", "pipeline", "layered", "single_proposer", "ranker", "single"):
            check(f"moa-{s}", "POST", "/v1/chat/completions", AUTH,
                  {"model": "balanced", "strategy": s, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5})
        # === multimodal ===
        check("embeddings", "POST", "/v1/embeddings", AUTH, {"input": "hi", "model": "mock"})
        check("audio-tts", "POST", "/v1/audio/speech", AUTH, {"model": "tts-1", "input": "hi", "voice": "alloy"})
        check("world", "POST", "/v1/world/simulate", AUTH, {"scenario": "ball", "steps": 2})
        check("embodied", "POST", "/v1/embodied/plan", AUTH, {"observation": {"description": "x"}, "goal": "y"})
        check("3d", "POST", "/v1/3d/generate", AUTH, {"model": "auto", "prompt": "cube"})
        check("video", "POST", "/v1/video/generate", AUTH, {"model": "auto", "prompt": "x"})
        check("image", "POST", "/v1/images/generations", AUTH, {"model": "dall-e-3", "prompt": "cat", "n": 1, "size": "256x256"})
        # === MCP ===
        check("mcp-ping", "POST", "/v1/mcp", AUTH, {"jsonrpc": "2.0", "id": 1, "method": "ping"})
        check("mcp-tools", "POST", "/v1/mcp", AUTH, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        # === agent/assistant/workflow ===
        check("assistant-c", "POST", "/v1/assistants", AUTH, {"name": "t", "model": "auto", "instructions": "h"})
        check("wf-reg", "POST", "/v1/agent/workflow/register", AUTH, {"name": "t", "steps": []})
        # === admin/compliance/RBAC GET ===
        for label, path, hdr in [
            ("health", "/health", AUTH), ("health-ready", "/health/ready", AUTH),
            ("models", "/v1/models", AUTH), ("admin-eps", "/api/endpoints", ADMIN),
            ("admin-keys", "/api/api-keys", ADMIN), ("admin-users", "/api/admin/users", ADMIN),
            ("admin-roles", "/api/admin/roles", ADMIN), ("admin-audit", "/api/admin/audit-log", ADMIN),
            ("failover", "/api/failover", ADMIN), ("benchmark", "/v1/benchmark", AUTH),
            ("capabilities", "/v1/capabilities", AUTH), ("strategies", "/v1/strategies", AUTH),
            ("moa-presets", "/v1/moa/presets", AUTH), ("optimizer-stats", "/v1/optimizer/stats", AUTH),
            ("observability", "/v1/observability/reports", AUTH),
            ("gdpr-retention", "/api/admin/compliance/retention", ADMIN),
            ("key-rotation", "/api/admin/compliance/key-rotation/status", ADMIN),
        ]:
            check(label, "GET", path, hdr)

        # === concurrent load (stability) ===
        import concurrent.futures
        def one():
            return _req("POST", "/v1/chat/completions", AUTH,
                        {"model": "auto", "messages": [{"role": "user", "content": "load"}], "max_tokens": 3})
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(lambda _: one(), range(40)))
        load_5xx = sum(1 for c in results if c >= 500 or c == 0)
        if load_5xx:
            failed += 1
            fails.append(f"concurrent-load: {load_5xx}/40 returned 5xx/0")
        else:
            passed += 1

        print(f"=== ROUND {round_n}: PASS={passed} FAIL={failed} ===")
        for f in fails:
            print(f"  FAIL {f}")
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
