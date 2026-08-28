# -*- coding: utf-8 -*-
"""S17 live probe — verify every hardening claim against a REAL running server.

Run with the gateway started at 127.0.0.1:8088 (MOA_GATEWAY_KEY=mgw-smoke-key-12345).
Prints PASS/FAIL per probe with evidence; exits non-zero on any failure.
"""
import json
import sys

import httpx

BASE = "http://127.0.0.1:8088"
KEY = "mgw-smoke-key-12345"
AUTH = {"Authorization": f"Bearer {KEY}"}

results: list[tuple[str, bool, str]] = []


def probe(name: str, ok: bool, evidence: str) -> None:
    results.append((name, bool(ok), evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {evidence}")


def main() -> int:
    c = httpx.Client(base_url=BASE, timeout=30)

    # 1. Health probes
    for path in ("/health", "/health/live", "/health/ready", "/health/startup"):
        r = c.get(path)
        probe(f"health {path}", r.status_code == 200, f"status={r.status_code}")

    # 2. Auth matrix on data plane
    r = c.get("/v1/models")
    probe("/v1/models without key -> 401", r.status_code == 401, f"status={r.status_code}")
    r = c.get("/v1/models", headers=AUTH)
    probe("/v1/models with key -> 200", r.status_code == 200,
          f"status={r.status_code}, models={len(r.json().get('data', []))}")

    # 3. W1 warning evidence: /docs and /metrics are open (documented residual)
    r = c.get("/docs")
    probe("/docs open (W1 documented residual)", r.status_code == 200, f"status={r.status_code}")
    r = c.get("/metrics")
    probe("/metrics open (W1 documented residual)", r.status_code == 200, f"status={r.status_code}")

    # 4. Chat through real MockProvider with honest labeling
    r = c.post("/v1/chat/completions", headers=AUTH, json={
        "model": "glm-4-flash",
        "messages": [{"role": "user", "content": "ping"}],
    })
    body = r.json()
    content = body.get("choices", [{}])[0].get("message", {}).get("content", "")
    mock_hdr = "x-moa-mock" in {k.lower() for k in r.headers.keys()}
    probe("chat mock-labeled header", r.status_code == 200 and mock_hdr,
          f"status={r.status_code}, X-Moa-Mock={r.headers.get('x-moa-mock')}")
    probe("chat mock-labeled content prefix", content.startswith("[Mock:"), content[:40])

    # 5. SSRF live probe: encoded-IP literal must be rejected (v3.2.1 F1)
    r = c.post("/v1/video/generate", headers=AUTH, json={
        "model": "kling-v1", "prompt": "x",
        "image_url": "http://2130706433/img.png",
    })
    probe("SSRF decimal-IP literal rejected", r.status_code == 400 and "URL rejected" in r.text,
          f"status={r.status_code}, detail={r.text[:90]}")
    r = c.post("/v1/video/generate", headers=AUTH, json={
        "model": "kling-v1", "prompt": "x",
        "image_url": "http://0x7f000001/img.png",
    })
    probe("SSRF hex-IP literal rejected", r.status_code == 400 and "URL rejected" in r.text,
          f"status={r.status_code}, detail={r.text[:90]}")
    r = c.post("/v1/video/generate", headers=AUTH, json={
        "model": "kling-v1", "prompt": "x",
        "image_url": "http://169.254.169.254/latest/meta-data/",
    })
    probe("SSRF metadata IP rejected", r.status_code == 400, f"status={r.status_code}")

    # 6. Orchestrator readonly path — must use a DB-created key (no role →
    # readonly). The YAML MOA_GATEWAY_KEY is admin-level by design (auth.py
    # "yaml trusted gateway keys are admin-level"), so it cannot probe this.
    lr = c.post("/api/auth/login", json={"username": "admin", "password": "Test#Pass1"})
    jwt_ok = lr.status_code == 200
    token = (lr.json() or {}).get("access_token") if jwt_ok else None
    readonly_key = None
    if token:
        kr = c.post("/api/admin/api-keys",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"name": "live-probe-readonly", "quota_rpm": 60, "quota_daily_tokens": 100000})
        readonly_key = (kr.json() or {}).get("key")
    probe("probe setup: readonly DB key created", bool(readonly_key),
          f"login={lr.status_code}, key={'yes' if readonly_key else 'missing'}")

    if readonly_key:
        ro = {"Authorization": f"Bearer {readonly_key}"}
        r = c.post("/v1/orchestrator/run", headers=ro, json={
            "task": "please use code_execute to compute 6*7",
            "input": {"code": "print(6*7)"},
        })
        ok = r.status_code == 200
        plan = r.json().get("plan", {}) if ok else {}
        caps = [s.get("capability_id") for s in plan.get("steps", [])]
        exec_res = r.json().get("execution", {}).get("step_results", {}) if ok else {}
        no_skill_exec = all(not (isinstance(v, dict) and v.get("ok") and v.get("skill")) for v in exec_res.values())
        probe("orchestrator readonly: no skill execution", ok and "skill.code_execute" not in caps and no_skill_exec,
              f"status={r.status_code}, plan_caps={caps}, filtered={plan.get('filtered_privileged_skills')}")
        probe("orchestrator readonly: honest disclosure", ok and "code_execute" in (plan.get("filtered_privileged_skills") or []),
              f"filtered_privileged_skills={plan.get('filtered_privileged_skills')}")

    # 7. Admin path: dangerous skill reachable (trust model holds both ways)
    if token:
        r2 = c.post("/v1/orchestrator/run",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"task": "please use code_execute to compute 6*7",
                          "input": {"code": "print(6*7)"}})
        ok2 = r2.status_code == 200
        exec2 = r2.json().get("execution", {}).get("step_results", {}) if ok2 else {}
        ran = any(isinstance(v, dict) and v.get("ok") and v.get("skill") == "code_execute" and "42" in str(v.get("value", "")) for v in exec2.values())
        probe("orchestrator admin: sandbox genuinely computes", ok2 and ran, f"status={r2.status_code}, ran={ran}")
    else:
        probe("orchestrator admin path", False, f"login failed: {lr.status_code}")

    c.close()
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} probes passed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
