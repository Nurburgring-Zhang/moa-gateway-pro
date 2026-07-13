"""Security regression tests for v1.6.3 (P0 fixes).

These run against a live server (uvicorn) and verify the 4 critical
P0 security fixes hold:
  - P0-4: checkpoint path traversal + arbitrary file write (now require_admin)
  - P0-5: worktree arbitrary git cwd (now require_admin)
  - P1-3: JWT detection (no more count(".")==2)
  - P1-6: token length limit
"""
import sys
sys.path.insert(0, '.')
import os
import threading
import time
import urllib.request
import urllib.error
import json

# Will run against the dev server (same setup as test_full_e2e.py)
# Expects server already running on 127.0.0.1:8088
BASE = "http://127.0.0.1:8088"


def call(method, path, body=None, headers=None, timeout=30):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req_headers = {"Content-Type": "application/json"}
    if headers:
        req_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        b = r.read().decode("utf-8", errors="replace")
        try:
            b = json.loads(b) if b and b[0] in "{[" else b
        except Exception:
            pass
        return r.status, b
    except urllib.error.HTTPError as e:
        b = e.read().decode("utf-8", errors="replace")[:500]
        try:
            b = json.loads(b) if b and b[0] in "{[" else b
        except Exception:
            pass
        return e.code, b
    except urllib.error.URLError as e:
        return 0, {"detail": f"URLError: {e.reason}"}


def main():
    # login as admin
    s, b = call("POST", "/api/auth/login",
                body={"username": "admin", "password": "TestPass#2024"})
    assert s == 200, f"login failed: {s} {b}"
    admin_token = b["token"]
    admin_h = {"Authorization": f"Bearer {admin_token}"}

    # create non-admin API key
    s, b = call("POST", "/api/api-keys", headers=admin_h,
                body={"name": "sec_test", "quota_rpm": 100, "quota_daily_tokens": 1000000})
    assert s == 200, f"create key failed: {s} {b}"
    api_key = b["key"]
    api_h = {"Authorization": f"Bearer {api_key}"}

    passed = 0
    failed = 0

    def check(name, expected, actual, body=None):
        nonlocal passed, failed
        if actual == expected:
            passed += 1
            print(f"  ✓ {name}: {actual}")
        else:
            failed += 1
            short = str(body)[:200] if body else ""
            print(f"  ✗ {name}: expected {expected}, got {actual} body={short}")

    # ===================== P0-4: checkpoint path traversal + arbitrary write =====================
    print("\n=== P0-4: checkpoint security ===")

    # 1. atomic_write action 已被删除(不存在了)
    s, b = call("POST", "/v1/capability/checkpoint",
                body={"action": "atomic_write", "path": "C:/Windows/test.txt", "data": "pwned"},
                headers=api_h)
    check("P0-4.a atomic_write 401 (not exposed)", 401, s, b)

    # 2. 即便 admin, root_dir 不在白名单也应被拒
    s, b = call("POST", "/v1/capability/checkpoint",
                body={"action": "save", "name": "test", "payload": {"a": 1},
                      "root_dir": "C:/Windows/System32"},
                headers=admin_h)
    check("P0-4.b root_dir 400 (allowlist)", 400, s, b)

    # 3. name 不在 [a-zA-Z0-9_-]{1,64} 应被拒
    s, b = call("POST", "/v1/capability/checkpoint",
                body={"action": "save", "name": "../etc/passwd", "payload": {"a": 1}},
                headers=admin_h)
    check("P0-4.c name traversal 400", 400, s, b)

    # 4. 普通 save (admin) 应该成功
    s, b = call("POST", "/v1/capability/checkpoint",
                body={"action": "save", "name": "sec_test_1", "payload": {"x": 1}},
                headers=admin_h)
    check("P0-4.d admin save 200", 200, s, b)

    # 5. api_key 访问 checkpoint 应被 401
    s, b = call("POST", "/v1/capability/checkpoint",
                body={"action": "save", "name": "sec_test_2", "payload": {"x": 1}},
                headers=api_h)
    check("P0-4.e api_key → 401", 401, s, b)

    # ===================== P0-5: worktree arbitrary git cwd =====================
    print("\n=== P0-5: worktree security ===")

    # 1. api_key 访问应 401
    s, b = call("POST", "/v1/capability/worktree",
                body={"action": "snapshot", "repo_path": "."},
                headers=api_h)
    check("P0-5.a api_key → 401", 401, s, b)

    # 2. 即便 admin, repo_path 不在白名单应 400
    s, b = call("POST", "/v1/capability/worktree",
                body={"action": "snapshot", "repo_path": "C:/Windows/System32"},
                headers=admin_h)
    check("P0-5.b cwd allowlist 400", 400, s, b)

    # 3. admin + 白名单 cwd 应成功
    s, b = call("POST", "/v1/capability/worktree",
                body={"action": "snapshot", "repo_path": "."},
                headers=admin_h)
    check("P0-5.c admin + cwd OK 200", 200, s, b)

    # ===================== P1-3: JWT detection =====================
    print("\n=== P1-3: JWT strict regex ===")

    # 1. "a.b.c" 非 JWT 格式不应被当 JWT 尝试解码
    # 用 /v1/quota (要求 auth) 而非 /v1/models (public)
    s, b = call("GET", "/v1/quota", headers={"Authorization": "Bearer a.b.c"})
    check("P1-3.a fake JWT 401", 401, s, b)

    # 2. 真实格式但无效签名也应 401 (不算 bug,但走 JWT 路径)
    s, b = call("GET", "/v1/quota",
                headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.bogus"})
    check("P1-3.b invalid sig 401", 401, s, b)

    # ===================== P1-6: token length limit =====================
    print("\n=== P1-6: token length limit ===")

    # 1. 超长 token 应被 _bearer_or_raw 截断 → 401 (因为空 token 或截断后无效)
    long_token = "A" * 1024
    s, b = call("GET", "/v1/quota", headers={"Authorization": f"Bearer {long_token}"})
    check("P1-6.a 1KB token 401", 401, s, b)

    # 2. 多值 header (逗号分隔) 取第一个
    s, b = call("GET", "/v1/quota",
                headers={"Authorization": f"Bearer invalid, Bearer {api_key}"})
    # 应取第一个 "invalid" → 401
    check("P1-6.b multi-value first", 401, s, b)

    # ===================== Summary =====================
    print(f"\n========================================")
    print(f"  Security tests: {passed} pass, {failed} fail")
    print(f"========================================")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
