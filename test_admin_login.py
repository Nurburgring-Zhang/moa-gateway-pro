"""Test admin login and the 500-error endpoints."""
import os
import json
import time
import httpx
from typing import Any

BASE = "http://127.0.0.1:8910"
ADMIN_USER = "admin"
ADMIN_PW = "TestPassword123!"

def login():
    with httpx.Client(timeout=10.0) as c:
        r = c.post(f"{BASE}/api/auth/login",
                   json={"username": ADMIN_USER, "password": ADMIN_PW})
        print("LOGIN STATUS:", r.status_code)
        print("LOGIN BODY:", r.text[:500])
        if r.status_code == 200:
            return r.json().get("token", "")
    return None

# After login, hit /api/* endpoints
def main():
    token = login()
    if not token:
        print("Login failed - test admin endpoints without token")
        return

    # Test 500-error endpoints to see what they fail with
    failing = [
        ("should-rebalance", "POST", {
            "stats": {"m1": {"tier": "standard", "endpoint_count": 1,
                             "success_count": 10, "fail_count": 0, "weight_sum": 100}},
        }),
        ("cost-estimate", "POST", {
            "input_tokens": 1000, "output_tokens": 500,
            "channels": [{"name": "m1", "cost_per_1k_input": 0.0005,
                          "cost_per_1k_output": 0.001, "tier": "standard"}],
        }),
        ("action-policy", "POST", {
            "command": "ls",
            "rules": [{"pattern": "rm -rf /", "action": "deny", "priority": 100}],
        }),
    ]
    for name, method, body in failing:
        url = f"{BASE}/v1/capability/{name}"
        with httpx.Client(timeout=10.0) as c:
            r = c.post(url, headers={"Authorization": "Bearer demo-key-please-change"},
                       json=body)
            print(f"\n{method} {url}: {r.status_code}")
            print("Body:", r.text[:1000])

    # Test admin endpoints
    admin_eps = [
        ("GET", "/api/auth/me", None),
        ("GET", "/api/endpoints", None),
        ("GET", "/api/api-keys", None),
        ("GET", "/api/logs", None),
        ("GET", "/api/stats", None),
        ("GET", "/api/metrics", None),
        ("GET", "/api/adapters", None),
    ]
    for method, path, body in admin_eps:
        url = BASE + path
        with httpx.Client(timeout=10.0) as c:
            r = c.request(method, url, headers={"Authorization": f"Bearer {token}"},
                          json=body)
            print(f"\n{method} {path}: {r.status_code}")
            print("Body:", r.text[:300])

if __name__ == "__main__":
    main()
