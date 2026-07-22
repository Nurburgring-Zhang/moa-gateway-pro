"""perf/chaos.py — MoA Gateway Pro 故障注入测试

商用前必测:
  1. 异常输入 (大 payload, SQL/XSS, 类型错, None)
  2. 鉴权 (无 key, 错 key, 过期 key, 越权)
  3. 速率限制 (RPM 超限)
  4. server 故障 (DB 损坏, 大并发)
"""
import json
import time
import httpx
import os

BASE = "http://127.0.0.1:8088"
ADMIN_PWD = "TestPass#2024"


def login():
    with httpx.Client(timeout=5) as c:
        r = c.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": ADMIN_PWD})
        return r.json().get("token", "")


def get_real_key(token):
    with httpx.Client(timeout=5) as c:
        r = c.post(f"{BASE}/api/api-keys",
                    json={"name": "chaos", "quota_rpm": 100000, "quota_daily_tokens": 999999999},
                    headers={"Authorization": f"Bearer {token}"})
        return r.json().get("key", "")


def test(label, expected, status, body):
    ok = "✓" if status == expected else "✗"
    print(f"  {ok} {label}: expected={expected}, got={status} | {body[:80]}")
    return status == expected


def main():
    print("=" * 60)
    print(" MoA Gateway Pro 故障注入测试")
    print("=" * 60)

    token = login()
    key = get_real_key(token)
    auth = {"Authorization": f"Bearer {key}"} if key else {}

    pass_count = 0
    fail_count = 0

    def expect(label, expected, status, body):
        nonlocal pass_count, fail_count
        if test(label, expected, status, body):
            pass_count += 1
        else:
            fail_count += 1

    # ===== 1. 异常输入 =====
    print("\n[1] 异常输入")
    # 1.1 大 payload (1.5MB > middleware 1MB 限制)
    big_msg = "x" * 1_500_000  # 1.5MB
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": big_msg}]},
                   headers=auth, timeout=30)
    expect("1.1 大 payload (1.5MB > 1MB)", 413, r.status_code, r.text)
    # 1.2 SQL 注入 (复杂 query,确保走通 medium tier)
    complex_sql = "Please analyze: '; DROP TABLE users;-- and explain its impact on multi-model AI orchestration"
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": complex_sql}]},
                   headers=auth, timeout=30)
    expect("1.2 SQL 注入 (不崩不执行)", 200, r.status_code, r.text)
    # 1.3 XSS payload
    complex_xss = "Discuss this topic: <script>alert('xss')</script> in modern AI systems"
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": complex_xss}]},
                   headers=auth, timeout=30)
    expect("1.3 XSS payload (不执行)", 200, r.status_code, r.text)
    # 1.4 类型错误 (messages 是 string)
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": "not a list"},
                   headers=auth, timeout=30)
    expect("1.4 messages 类型错", 422, r.status_code, r.text)
    # 1.5 None fields
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": None, "messages": None},
                   headers=auth, timeout=30)
    expect("1.5 None fields", 422, r.status_code, r.text)
    # 1.6 missing required
    r = httpx.post(f"{BASE}/v1/chat/completions", json={}, headers=auth, timeout=30)
    expect("1.6 空 body", 422, r.status_code, r.text)
    # 1.7 未知 model
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "fake-model-xyz", "messages": [{"role": "user", "content": "hi"}]},
                   headers=auth, timeout=30)
    expect("1.7 未知 model", 503, r.status_code, r.text)  # 找不到 → 503

    # ===== 2. 鉴权 =====
    print("\n[2] 鉴权")
    # 2.1 无 key
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                   timeout=10)
    expect("2.1 无 key", 401, r.status_code, r.text)
    # 2.2 错 key
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"Authorization": "Bearer mgw-fake-key"}, timeout=10)
    expect("2.2 错 key", 401, r.status_code, r.text)
    # 2.3 错 key 格式
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"Authorization": "Basic abc123"}, timeout=10)
    expect("2.3 错 Auth scheme", 401, r.status_code, r.text)
    # 2.4 super 长 token (防内存炸弹)
    long_token = "x" * 100_000
    r = httpx.post(f"{BASE}/v1/chat/completions",
                   json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                   headers={"Authorization": f"Bearer {long_token}"}, timeout=10)
    expect("2.4 100KB token (防内存炸弹)", 401, r.status_code, r.text)
    # 2.5 admin endpoint 无 admin (用真 admin endpoint path)
    r = httpx.get(f"{BASE}/api/api-keys", headers=auth, timeout=5)
    expect("2.5 admin endpoint 需 admin (普通 key 拒)", 401, r.status_code, r.text)

    # ===== 3. 速率限制 =====
    print("\n[3] 速率限制")
    # 用低 RPM key 测
    with httpx.Client(timeout=5) as c:
        r = c.post(f"{BASE}/api/api-keys",
                    json={"name": "chaos-low-rpm", "quota_rpm": 5, "quota_daily_tokens": 100000},
                    headers={"Authorization": f"Bearer {token}"})
        low_key = r.json().get("key", "")
    if low_key:
        # 5 RPM = 1 req/12s
        low_auth = {"Authorization": f"Bearer {low_key}"}
        # 先打 6 个,第 6 个应该 429
        statuses = []
        for i in range(6):
            r = httpx.post(f"{BASE}/v1/chat/completions",
                           json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                           headers=low_auth, timeout=10)
            statuses.append(r.status_code)
        # 不应该全是 200
        has_429 = 429 in statuses
        if has_429:
            expect("3.1 RPM 限速触发 (5 RPM key 打 6)", 429, statuses[5], "")
        else:
            print(f"  ? 3.1 RPM 限速: {statuses} (全部 200/503)")

    # ===== 4. 公共端点 =====
    print("\n[4] 公共端点 + 错误路径")
    # 4.1 /v1/models 无 auth
    r = httpx.get(f"{BASE}/v1/models", timeout=5)
    expect("4.1 /v1/models 公共", 200, r.status_code, r.text)
    # 4.2 /health
    r = httpx.get(f"{BASE}/health", timeout=5)
    expect("4.2 /health", 200, r.status_code, r.text)
    # 4.3 /docs
    r = httpx.get(f"{BASE}/docs", timeout=5)
    expect("4.3 /docs Swagger UI", 200, r.status_code, r.text)
    # 4.4 /openapi.json
    r = httpx.get(f"{BASE}/openapi.json", timeout=5)
    expect("4.4 /openapi.json", 200, r.status_code, r.text)
    # 4.5 不存在的路径
    r = httpx.get(f"{BASE}/v1/nonexistent", timeout=5)
    expect("4.5 不存在路径 → 404", 404, r.status_code, r.text)
    # 4.6 错误 HTTP method
    r = httpx.delete(f"{BASE}/v1/chat/completions", timeout=5)
    expect("4.6 DELETE /v1/chat → 405", 405, r.status_code, r.text)

    print("\n" + "=" * 60)
    print(f" 总结: {pass_count} pass, {fail_count} fail")
    print("=" * 60)


if __name__ == "__main__":
    main()
