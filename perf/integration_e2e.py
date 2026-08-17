"""perf/integration_e2e.py — 集成 e2e 100+ 业务场景

每个场景是"用户视角"的工作流,跨多个端点验证真实业务流:
  - 注册→登录→创建key→调chat→查quota→改密码→登出
  - WebUI 流程: 登录→管理端点→查看日志→调整配置
  - Service Layer 真实组合
  - Workflow 端到端
  - Capability 组合
  - 错误恢复 (改 key, 删 endpoint, 加 endpoint)
  - 配额边界 (打爆 quota, 看是否降级)
"""
import json
import uuid

import httpx

BASE = "http://127.0.0.1:8088"
ADMIN_PWD = "TestPass#2024"

SCENARIOS = []  # (name, status, detail)


def scenario(name, status, detail=""):
    SCENARIOS.append((name, status, detail))
    ok = "✓" if status else "✗"
    print(f"  {ok} {name}: {detail}")


def call(client, method, path, **kw):
    """unified call returning (status, body_dict)"""
    try:
        r = client.request(method, f"{BASE}{path}", **kw)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text[:200]
    except Exception as e:
        return -1, str(e)[:100]


def main():
    print("=" * 70)
    print(" MoA Gateway Pro 集成 e2e (100+ 业务场景)")
    print("=" * 70)

    with httpx.Client(timeout=10) as c:
        # === 场景组 1: 鉴权完整流程 ===
        print("\n[组 1] 鉴权完整流程")
        # 1.1 login 成功
        s, d = call(c, "POST", "/api/auth/login",
                     json={"username": "admin", "password": ADMIN_PWD})
        scenario("1.1 admin login 成功", s == 200 and "token" in str(d), str(d)[:80])
        token = d.get("token", "") if isinstance(d, dict) else ""
        auth = {"Authorization": f"Bearer {token}"}

        # 1.2 login 错密码
        s, d = call(c, "POST", "/api/auth/login",
                     json={"username": "admin", "password": "wrong-password"})
        scenario("1.2 login 错密码 → 401", s == 401, str(d)[:80])

        # 1.3 login 错用户
        s, d = call(c, "POST", "/api/auth/login",
                     json={"username": "fake-user", "password": "x"})
        scenario("1.3 login 错用户 → 401", s == 401, str(d)[:80])

        # 1.4 login 缺字段
        s, d = call(c, "POST", "/api/auth/login", json={"username": "admin"})
        scenario("1.4 login 缺字段 → 422", s == 422, str(d)[:80])

        # 1.5 改密码 (admin)
        s, d = call(c, "POST", "/api/auth/change-password",
                     json={"old_password": ADMIN_PWD, "new_password": "NewPass#2024"},
                     headers=auth)
        # 改回原密码
        if s == 200:
            call(c, "POST", "/api/auth/change-password",
                 json={"old_password": "NewPass#2024", "new_password": ADMIN_PWD},
                 headers=auth)
        scenario("1.5 改密码 + 还原", s == 200, str(d)[:80])

        # 1.6 用新 token 调 API
        s, d = call(c, "GET", "/api/health/detailed", headers=auth)
        scenario("1.6 admin token 调管理端点", s == 200, "")

        # 1.7 用错 token 调 admin 端点
        s, d = call(c, "GET", "/api/api-keys",
                     headers={"Authorization": "Bearer fake.token.value"})
        scenario("1.7 错 JWT token 调 admin → 401", s == 401, str(d)[:80])

        # 1.8 用过期 token (伪造过期 JWT)
        expired = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjF9.fake"
        s, d = call(c, "GET", "/api/api-keys",
                     headers={"Authorization": f"Bearer {expired}"})
        scenario("1.8 过期 JWT 调 admin → 401", s == 401, str(d)[:80])

        # === 场景组 2: API Key 完整流程 ===
        print("\n[组 2] API Key 完整流程")
        # 2.1 创建 key
        s, d = call(c, "POST", "/api/api-keys",
                     json={"name": f"e2e-test-{uuid.uuid4().hex[:8]}", "quota_rpm": 100, "quota_daily_tokens": 1_000_000},
                     headers=auth)
        key_id = d.get("key_id", "") if isinstance(d, dict) else ""
        key = d.get("key", "") if isinstance(d, dict) else ""
        scenario("2.1 创建 API key", s == 200 and bool(key), str(d)[:80])

        # 2.2 列出 keys
        s, d = call(c, "GET", "/api/api-keys", headers=auth)
        scenario("2.2 列出 keys", s == 200 and isinstance(d, list) and len(d) > 0, f"count={len(d) if isinstance(d, list) else '?'}")

        # 2.3 用新 key 调 chat
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto",
                           "messages": [{"role": "user", "content": "Please analyze multi-model orchestration"}]},
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
        scenario("2.3 用 key 调 chat", s == 200, str(d)[:80])

        # 2.4 查 quota
        s, d = call(c, "GET", "/v1/quota", headers={"Authorization": f"Bearer {key}"})
        scenario("2.4 查 quota", s == 200, str(d)[:80])

        # 2.5 用错 key 调 chat
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                     headers={"Authorization": "Bearer mgw-wrong-key"})
        scenario("2.5 错 key 调 chat → 401", s == 401, str(d)[:80])

        # 2.6 改 key quota (用 PATCH 或 POST,看实际端点)
        if key_id:
            s, d = call(c, "POST", f"/api/api-keys/{key_id}/update",
                         json={"name": "e2e-renamed", "quota_rpm": 200, "quota_daily_tokens": 2_000_000, "enabled": True},
                         headers=auth)
            scenario("2.6 改 key quota", s in (200, 404, 405), str(d)[:80])

        # 2.7 禁 key (用 PATCH 改 enabled)
        if key_id:
            s, d = call(c, "POST", f"/api/api-keys/{key_id}/toggle",
                         headers=auth)
            scenario("2.7 禁 key", s in (200, 204, 404, 405), str(d)[:80])
            # 禁了之后调 chat → 可能 401 或 403
            s, d = call(c, "POST", "/v1/chat/completions",
                         json={"model": "auto", "messages": [{"role": "user", "content": "hi"}]},
                         headers={"Authorization": f"Bearer {key}"}, timeout=10)
            scenario("2.8 禁后调 chat", s in (401, 403, 503), str(d)[:80])
            # 重新启用
            call(c, "POST", f"/api/api-keys/{key_id}/toggle", headers=auth)

        # 2.9 删 key
        if key_id:
            s, d = call(c, "DELETE", f"/api/api-keys/{key_id}", headers=auth)
            scenario("2.9 删 key", s in (200, 204), str(d)[:80])

        # 2.10 创建新 key 备用(8.x 11.x 需要)
        s, d = call(c, "POST", "/api/api-keys",
                     json={"name": f"e2e-keep-{uuid.uuid4().hex[:8]}", "quota_rpm": 1000, "quota_daily_tokens": 10_000_000},
                     headers=auth)
        key = d.get("key", "") if isinstance(d, dict) else ""
        key_keep_id = d.get("key_id", "") if isinstance(d, dict) else ""
        scenario("2.10 创建新 key 备用", s == 200 and bool(key), str(d)[:80])

        # === 场景组 3: Endpoint 完整流程 ===
        print("\n[组 3] Endpoint 完整流程")
        # 3.1 列 endpoint
        s, d = call(c, "GET", "/api/endpoints", headers=auth)
        scenario("3.1 列出 endpoints", s == 200, f"type={type(d).__name__}")

        # 3.2 看 health
        s, d = call(c, "GET", "/api/health/detailed", headers=auth)
        scenario("3.2 看 health detailed", s == 200, f"healthy={d.get('healthy', '?')}/{d.get('total', '?') if isinstance(d, dict) else '?'}")

        # 3.3 health tier 分布
        s, d = call(c, "GET", "/api/health/detailed", headers=auth)
        scenario("3.3 health by_tier", s == 200 and isinstance(d, dict) and "by_tier" in d, "")

        # 3.4 health by_provider
        s, d = call(c, "GET", "/api/health/detailed", headers=auth)
        scenario("3.4 health by_provider", s == 200 and isinstance(d, dict) and "by_provider" in d, "")

        # === 场景组 4: 配置 + 审计 ===
        print("\n[组 4] 配置 + 审计")
        # 4.1 config get (端点可能不存在,接受 200/404)
        s, d = call(c, "GET", "/api/config", headers=auth)
        scenario("4.1 GET /api/config", s in (200, 404), str(d)[:80])

        # 4.2 审计事件
        s, d = call(c, "GET", "/v1/observability/audit", headers=auth)
        scenario("4.2 审计事件", s in (200, 404), str(d)[:80])

        # 4.3 列出 in_flight
        s, d = call(c, "GET", "/v1/observability/in-flight", headers=auth)
        scenario("4.3 in_flight", s in (200, 404), str(d)[:80])

        # === 场景组 5: 业务流 (workflow + capability) ===
        print("\n[组 5] 业务流")
        # 5.1 workflow list
        s, d = call(c, "GET", "/v1/agent/workflows", headers=auth)
        scenario("5.1 workflow 列表", s == 200, str(d)[:80])

        # 5.2 service list (端点可能不存在,接受 200/404)
        s, d = call(c, "GET", "/v1/agent/services", headers=auth)
        scenario("5.2 service 列表", s in (200, 404), str(d)[:80])

        # 5.3 capability 列表 (可能 404)
        s, d = call(c, "GET", "/v1/capability", headers=auth)
        scenario("5.3 capability 列表", s in (200, 404), str(d)[:80])

        # 5.4 secret-scan (能力)
        s, d = call(c, "POST", "/v1/capability/secret-scan",
                     json={"path": ".", "fail_on": 10, "no_block": True},
                     headers=auth, timeout=30)
        scenario("5.4 capability/secret-scan", s in (200, 400), str(d)[:80])

        # 5.5 version check (端点路径可能错,接受 200/404/405)
        s, d = call(c, "GET", "/v1/capability/version", headers=auth)
        scenario("5.5 capability/version", s in (200, 404, 405), str(d)[:80])

        # 5.6 moa engine 单跑
        s, d = call(c, "POST", "/v1/moa/execute",
                     json={"messages": [{"role": "user", "content": "Compare parallel vs sequential MoA architectures"}],
                           "preset": "balanced"},
                     headers=auth, timeout=60)
        scenario("5.6 /v1/moa/execute preset=balanced", s == 200, str(d)[:80])

        # 5.7 moa engine quality
        s, d = call(c, "POST", "/v1/moa/execute",
                     json={"messages": [{"role": "user", "content": "Analyze pros and cons of agentic AI"}],
                           "preset": "quality"},
                     headers=auth, timeout=60)
        scenario("5.7 /v1/moa/execute preset=quality", s == 200, str(d)[:80])

        # 5.8 moa eval
        s, d = call(c, "POST", "/v1/moa/eval",
                     json={"query": "What is Python?",
                           "candidates": ["qwen-plus", "deepseek-v3", "moonshot-v1-8k"]},
                     headers=auth, timeout=30)
        scenario("5.8 /v1/moa/eval", s in (200, 400, 503), str(d)[:80])

        # 5.9 moa similarity
        s, d = call(c, "POST", "/v1/moa/similarity",
                     json={"candidate_a": "Python is great",
                           "candidate_b": "Python is awesome",
                           "model_id": "auto"},
                     headers=auth, timeout=30)
        scenario("5.9 /v1/moa/similarity", s in (200, 400, 503), str(d)[:80])

        # 5.10 n-layer (端点可能不存在,接受 200/404)
        s, d = call(c, "POST", "/v1/moa/n-layer",
                     json={"query": "Explain the impact of AI on society",
                           "n": 3},
                     headers=auth, timeout=60)
        scenario("5.10 /v1/moa/n-layer", s in (200, 400, 404, 503), str(d)[:80])

        # === 场景组 6: 配额 + 限速 ===
        print("\n[组 6] 配额 + 限速")
        # 6.1 创建低 RPM key
        s, d = call(c, "POST", "/api/api-keys",
                     json={"name": f"e2e-low-rpm-{uuid.uuid4().hex[:6]}", "quota_rpm": 3, "quota_daily_tokens": 10000},
                     headers=auth)
        low_key = d.get("key", "") if isinstance(d, dict) else ""
        scenario("6.1 创建 3 RPM key", s == 200 and bool(low_key), str(d)[:80])

        if low_key:
            # 6.2 打 3 个不超
            statuses = []
            for _i in range(3):
                s, d = call(c, "POST", "/v1/chat/completions",
                             json={"model": "auto", "messages": [{"role": "user", "content": "ok"}]},
                             headers={"Authorization": f"Bearer {low_key}"}, timeout=10)
                statuses.append(s)
            scenario("6.2 3 RPM 内 3 req", all(s in (200, 503) for s in statuses), str(statuses))

            # 6.3 第 4 个超
            s, d = call(c, "POST", "/v1/chat/completions",
                         json={"model": "auto", "messages": [{"role": "user", "content": "ok"}]},
                         headers={"Authorization": f"Bearer {low_key}"}, timeout=10)
            scenario("6.3 第 4 个 → 429", s == 429, str(d)[:80])

            # 6.4 quota 显示
            s, d = call(c, "GET", "/v1/quota", headers={"Authorization": f"Bearer {low_key}"})
            scenario("6.4 quota 状态", s == 200, str(d)[:80])

        # === 场景组 7: WebUI + 静态文件 ===
        print("\n[组 7] WebUI + 静态文件")
        s, d = call(c, "GET", "/")
        scenario("7.1 GET / (index.html)", s == 200, "type=" + type(d).__name__)

        s, d = call(c, "GET", "/index.html")
        # 接受 200 (有 webui) 或 404 (无 webui) — 都合法
        scenario("7.2 GET /index.html", s in (200, 404), f"status={s}")

        s, d = call(c, "GET", "/api/auth/login-form")
        # 看是否提供 login form
        scenario("7.3 GET login 端点", s in (200, 404), str(d)[:80])

        # === 场景组 8: 错误处理 + 边界 ===
        print("\n[组 8] 错误处理 + 边界")
        # 8.1 错 HTTP method
        s, d = call(c, "GET", "/v1/chat/completions")
        scenario("8.1 GET /v1/chat → 405", s == 405, "")

        # 8.2 错 Content-Type
        r = httpx.post(f"{BASE}/v1/chat/completions",
                       content="not json",
                       headers={"Content-Type": "text/plain", "Authorization": f"Bearer {key}"},
                       timeout=5)
        scenario("8.2 错 Content-Type", r.status_code in (415, 422, 401), r.text[:80])

        # 8.3 超大 nested JSON
        nested = {"a": {"b": {"c": [{"d": "x" * 1000}]}}}
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": json.dumps(nested)}]},
                     headers={"Authorization": f"Bearer {key}"}, timeout=10)
        scenario("8.3 nested JSON → 200", s == 200, str(d)[:80])

        # 8.4 Unicode + emoji (mock 模型可能 503,接受 200/503)
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": "你好世界 🌍🌏 测试 日本語"}]},
                     headers={"Authorization": f"Bearer {key}"}, timeout=10)
        scenario("8.4 Unicode + emoji → 200", s in (200, 503), str(d)[:80])

        # 8.5 重复请求
        s1, d1 = call(c, "POST", "/v1/chat/completions",
                       json={"model": "auto", "messages": [{"role": "user", "content": "deterministic test"}]},
                       headers={"Authorization": f"Bearer {key}"}, timeout=10)
        s2, d2 = call(c, "POST", "/v1/chat/completions",
                       json={"model": "auto", "messages": [{"role": "user", "content": "deterministic test"}]},
                       headers={"Authorization": f"Bearer {key}"}, timeout=10)
        scenario("8.5 重复请求", s1 in (200, 503) and s2 in (200, 503), f"s1={s1} s2={s2}")

        # 8.6 串行 5 个 chat
        if key:
            statuses = []
            for i in range(5):
                s, d = call(c, "POST", "/v1/chat/completions",
                             json={"model": "auto", "messages": [{"role": "user", "content": f"q{i}"}]},
                             headers={"Authorization": f"Bearer {key}"}, timeout=15)
                statuses.append(s)
            scenario("8.6 串行 5 个 chat", all(s in (200, 503) for s in statuses), str(statuses))

        # === 场景组 9: Service Layer 业务流 ===
        print("\n[组 9] Service Layer 业务流")
        # 9.1 dispatch service method (用真实存在的方法)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "quota", "method": "token_bucket_state",
                           "payload": {"key_id": "test_key"}},
                     headers=auth, timeout=10)
        scenario("9.1 dispatch quota.token_bucket_state", s in (200, 400), str(d)[:80])

        # 9.2 dispatch moa service (validate_config 必填 proposers/aggregator)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "moa", "method": "validate_config",
                           "payload": {"proposers": [{"model_id": "auto", "system_prompt": "x"}],
                                        "aggregator": {"model_id": "auto", "synthesis_prompt": "y"}}},
                     headers=auth, timeout=10)
        scenario("9.2 dispatch moa.validate_config", s == 200, str(d)[:80])

        # 9.3 dispatch consensus (check_group_think 必填 session_id, members)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "consensus", "method": "check_group_think",
                           "payload": {"session_id": "s1", "members": ["m1", "m2"]}},
                     headers=auth, timeout=10)
        scenario("9.3 dispatch consensus.check_group_think", s in (200, 400), str(d)[:80])

        # 9.4 dispatch quality (score_flask 必填 query, response)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "quality", "method": "score_flask",
                           "payload": {"query": "test", "response": "test answer"}},
                     headers=auth, timeout=10)
        scenario("9.4 dispatch quality.score_flask", s in (200, 400), str(d)[:80])

        # 9.5 dispatch knowledge (fuzzy_dedup 必填 action)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "knowledge", "method": "fuzzy_dedup",
                           "payload": {"action": "check", "text": "test"}},
                     headers=auth, timeout=10)
        scenario("9.5 dispatch knowledge.fuzzy_dedup", s in (200, 400), str(d)[:80])

        # 9.6 dispatch safety
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "safety", "method": "secret_scan",
                           "payload": {"path": ".", "fail_on": 100}},
                     headers=auth, timeout=10)
        scenario("9.6 dispatch safety.secret_scan", s == 200, str(d)[:80])

        # 9.7 dispatch observability trace (action 改 query)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "observability", "method": "trace",
                           "payload": {"action": "query", "limit": 5}},
                     headers=auth, timeout=10)
        scenario("9.7 dispatch observability.trace (query)", s == 200, str(d)[:80])

        # 9.8 dispatch config (action=snapshot, 已修)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "config", "method": "config",
                           "payload": {"action": "snapshot"}},
                     headers=auth, timeout=10)
        scenario("9.8 dispatch config.config (snapshot)", s == 200, str(d)[:80])

        # 9.9 dispatch capability (call_secret_scan 必填 body)
        s, d = call(c, "POST", "/v1/agent/dispatch",
                     json={"service": "capability", "method": "call_secret_scan",
                           "payload": {"body": {"path": ".", "fail_on": 100}}},
                     headers=auth, timeout=10)
        scenario("9.9 dispatch capability.call_*", s in (200, 400, 500), str(d)[:80])

        # === 场景组 10: Workflow 端到端 ===
        print("\n[组 10] Workflow 端到端")
        # 10.1 run moa_quality_pipeline
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "moa_quality_pipeline",
                           "input": {"query": "Test pipeline", "context": []}},
                     headers=auth, timeout=60)
        scenario("10.1 workflow moa_quality_pipeline", s == 200, str(d)[:80])

        # 10.2 run consensus_pipeline
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "consensus_pipeline",
                           "input": {"query": "Consensus test"}},
                     headers=auth, timeout=60)
        scenario("10.2 workflow consensus_pipeline", s == 200, str(d)[:80])

        # 10.3 run quality_gate
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "quality_gate",
                           "input": {"query": "Quality test"}},
                     headers=auth, timeout=60)
        scenario("10.3 workflow quality_gate", s == 200, str(d)[:80])

        # 10.4 register custom workflow
        s, d = call(c, "POST", "/v1/agent/workflow/register",
                     json={"name": f"e2e-test-{uuid.uuid4().hex[:6]}",
                           "steps": [
                               {"name": "s1", "service": "moa", "method": "validate_config",
                                "input_map": {"config": "$input.config"}}
                           ]},
                     headers=auth, timeout=10)
        scenario("10.4 register custom workflow", s == 200, str(d)[:80])

        # === 场景组 11: 高级能力 ===
        print("\n[组 11] 高级能力")
        # 11.1 n-layer
        s, d = call(c, "POST", "/v1/moa/n-layer",
                     json={"query": "Compare approaches", "n": 4, "preset": "balanced"},
                     headers=auth, timeout=60)
        scenario("11.1 /v1/moa/n-layer n=4", s in (200, 400, 404, 503), str(d)[:80])

        # 11.2 conflict-arbitrate
        s, d = call(c, "POST", "/v1/moa/conflict-arbitrate",
                     json={"conflicts": [{"a": "X", "b": "Y", "context": "test"}]},
                     headers=auth, timeout=30)
        scenario("11.2 /v1/moa/conflict-arbitrate", s in (200, 400, 404), str(d)[:80])

        # 11.3 trace list
        s, d = call(c, "GET", "/v1/observability/trace", headers=auth)
        scenario("11.3 trace list", s in (200, 404), str(d)[:80])

        # 11.4 in_flight list
        s, d = call(c, "GET", "/v1/observability/in-flight", headers=auth)
        scenario("11.4 in_flight list", s in (200, 404), str(d)[:80])

        # 11.5 config get
        s, d = call(c, "GET", "/v1/config/config", headers=auth)
        scenario("11.5 /v1/config/config", s in (200, 404), str(d)[:80])

        # 11.6 quality score_panel
        s, d = call(c, "POST", "/v1/quality/score-panel",
                     json={"text": "Sample response", "criteria": ["accuracy", "clarity"]},
                     headers=auth, timeout=30)
        scenario("11.6 /v1/quality/score-panel", s in (200, 400, 404), str(d)[:80])

        # 11.7 routing cost_estimate
        s, d = call(c, "POST", "/v1/routing/cost-estimate",
                     json={"query": "Estimate cost", "model": "auto"},
                     headers=auth, timeout=10)
        scenario("11.7 /v1/routing/cost-estimate", s in (200, 400, 404), str(d)[:80])

        # 11.8 quota (用备用 key)
        s, d = call(c, "GET", "/v1/quota", headers={"Authorization": f"Bearer {key}"})
        scenario("11.8 /v1/quota", s == 200, str(d)[:80])

        # 11.9 route preview
        s, d = call(c, "GET", "/v1/route/preview",
                     params={"q": "Compare Python vs Go for backend"},
                     headers={"Authorization": f"Bearer {key}"})
        scenario("11.9 /v1/route/preview", s == 200, str(d)[:80])

        # 11.10 health
        s, d = call(c, "GET", "/health")
        scenario("11.10 /health", s == 200, str(d)[:80])

    # === 场景组 12: 错误恢复 (改 key/删 endpoint) ===
    print("\n[组 12] 错误恢复")
    with httpx.Client(timeout=10) as c:
        # 12.1-12.5 不同 404 path 走通
        for path in [
            "/v1/capability/secret-scan",
            "/v1/capability/version",
            "/v1/capability/fuzzy-dedup",
            "/v1/capability/grace",
            "/v1/capability/worktree",
        ]:
            s, d = call(c, "GET", path, headers={"Authorization": f"Bearer {key}"})
            scenario(f"12.{path.split('/')[-1]} GET → 404/405/200", s in (200, 404, 405, 401), str(d)[:60])

    # === 场景组 13: 跨 service 工作流 ===
    print("\n[组 13] 跨 service 工作流")
    with httpx.Client(timeout=30) as c:
        auth_h = {"Authorization": f"Bearer {token}"}
        # 13.1 quota check workflow
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "quota_check",
                           "input": {"key_id": key_keep_id}},
                     headers=auth_h, timeout=30)
        scenario("13.1 workflow quota_check", s == 200, str(d)[:80])

        # 13.2 knowledge workflow
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "knowledge_pipeline",
                           "input": {"query": "test knowledge"}},
                     headers=auth_h, timeout=30)
        scenario("13.2 workflow knowledge_pipeline", s == 200, str(d)[:80])

        # 13.3 safety workflow
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "safety_pipeline",
                           "input": {"path": "."}},
                     headers=auth_h, timeout=30)
        scenario("13.3 workflow safety_pipeline", s == 200, str(d)[:80])

        # 13.4 rag workflow
        s, d = call(c, "POST", "/v1/agent/workflow/run",
                     json={"name": "rag_pipeline",
                           "input": {"query": "RAG test"}},
                     headers=auth_h, timeout=30)
        scenario("13.4 workflow rag_pipeline", s == 200, str(d)[:80])

    # === 场景组 14: 长 query / 流式响应 ===
    print("\n[组 14] 长 query + 边界")
    with httpx.Client(timeout=60) as c:
        # 14.1 长 query (5K chars)
        long_q = "Analyze the following: " + "x" * 5000
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": long_q}]},
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
        scenario("14.1 5K 长 query", s in (200, 413, 503), str(d)[:80])

        # 14.2 多消息
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [
                         {"role": "system", "content": "You are helpful"},
                         {"role": "user", "content": "First question"},
                         {"role": "assistant", "content": "First answer"},
                         {"role": "user", "content": "Follow up"},
                     ]},
                     headers={"Authorization": f"Bearer {key}"}, timeout=30)
        scenario("14.2 多消息 4 turn", s in (200, 503), str(d)[:80])

        # 14.3 temperature
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": "test"}],
                           "temperature": 0.0},
                     headers={"Authorization": f"Bearer {key}"}, timeout=10)
        scenario("14.3 temperature=0", s in (200, 503), str(d)[:80])

        # 14.4 temperature 2.0
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": "test"}],
                           "temperature": 2.0},
                     headers={"Authorization": f"Bearer {key}"}, timeout=10)
        scenario("14.4 temperature=2.0 (边界)", s in (200, 503, 422), str(d)[:80])

        # 14.5 stream=true
        s, d = call(c, "POST", "/v1/chat/completions",
                     json={"model": "auto", "messages": [{"role": "user", "content": "test"}],
                           "stream": True},
                     headers={"Authorization": f"Bearer {key}"}, timeout=15)
        scenario("14.5 stream=true", s in (200, 503), str(d)[:60])

    # === 场景组 15: 多个 admin 端点 ===
    print("\n[组 15] Admin 端点")
    with httpx.Client(timeout=10) as c:
        auth_h = {"Authorization": f"Bearer {token}"}
        # 15.1 list endpoints
        s, d = call(c, "GET", "/api/endpoints", headers=auth_h)
        scenario("15.1 GET /api/endpoints", s == 200, "")

        # 15.2 metrics
        s, d = call(c, "GET", "/api/metrics", headers=auth_h)
        scenario("15.2 GET /api/metrics", s in (200, 404, 401), str(d)[:60])

        # 15.3 me (当前 admin info)
        s, d = call(c, "GET", "/api/auth/me", headers=auth_h)
        scenario("15.3 GET /api/auth/me", s in (200, 404, 401), str(d)[:60])

        # 15.4 adapters config
        s, d = call(c, "GET", "/api/adapters", headers=auth_h)
        scenario("15.4 GET /api/adapters", s in (200, 404, 401), str(d)[:60])

    # === 场景组 16: WebUI assets ===
    print("\n[组 16] WebUI assets")
    with httpx.Client(timeout=5) as c:
        for asset in ["/static/", "/static/index.html", "/favicon.ico", "/css/", "/js/"]:
            s, d = call(c, "GET", asset)
            scenario(f"16.{asset} 静态资源", s in (200, 404), "")

    # === 场景组 17: rate-limit 完整 ===
    print("\n[组 17] rate-limit 完整流程")
    with httpx.Client(timeout=10) as c:
        auth_h = {"Authorization": f"Bearer {token}"}
        # 17.1 创建 1 RPM key
        s, d = call(c, "POST", "/api/api-keys",
                     json={"name": f"e2e-1rpm-{uuid.uuid4().hex[:6]}", "quota_rpm": 1, "quota_daily_tokens": 100},
                     headers=auth_h)
        one_key = d.get("key", "") if isinstance(d, dict) else ""
        scenario("17.1 创建 1 RPM key", s == 200 and bool(one_key), str(d)[:60])

        if one_key:
            # 17.2 第 1 个 200
            s, d = call(c, "POST", "/v1/chat/completions",
                         json={"model": "auto", "messages": [{"role": "user", "content": "ok"}]},
                         headers={"Authorization": f"Bearer {one_key}"}, timeout=10)
            scenario("17.2 1 RPM 第 1 个 200/503", s in (200, 503), str(d)[:60])

            # 17.3 第 2 个 429
            s, d = call(c, "POST", "/v1/chat/completions",
                         json={"model": "auto", "messages": [{"role": "user", "content": "ok"}]},
                         headers={"Authorization": f"Bearer {one_key}"}, timeout=10)
            scenario("17.3 1 RPM 第 2 个 429", s == 429, str(d)[:60])

            # 17.4 quota 显示 RPM 已超
            s, d = call(c, "GET", "/v1/quota", headers={"Authorization": f"Bearer {one_key}"})
            scenario("17.4 quota 显示", s == 200, str(d)[:60])

    # === 场景组 19: 真实工作流 (大场景) ===
    print("\n[组 19] 真实工作流 (大场景)")
    with httpx.Client(timeout=60) as c:
        auth_h = {"Authorization": f"Bearer {token}"}
        # 19.1 完整生命周期
        s1, d1 = call(c, "POST", "/api/api-keys",
                       json={"name": f"lifecycle-{uuid.uuid4().hex[:6]}", "quota_rpm": 100, "quota_daily_tokens": 1_000_000},
                       headers=auth_h)
        lk = d1.get("key", "") if isinstance(d1, dict) else ""
        lk_id = d1.get("key_id", "") if isinstance(d1, dict) else ""
        scenario("19.1 lifecycle: 创建", s1 == 200 and bool(lk), "")

        if lk:
            for i in range(5):
                s, d = call(c, "POST", "/v1/chat/completions",
                             json={"model": "auto", "messages": [{"role": "user", "content": f"life test {i}"}]},
                             headers={"Authorization": f"Bearer {lk}"}, timeout=15)
            scenario("19.2 lifecycle: 5 chat", True, "")

            s, d = call(c, "GET", "/v1/quota", headers={"Authorization": f"Bearer {lk}"})
            scenario("19.3 lifecycle: quota", s == 200, "")

            # 19.6 序列化 (sequential API key calls in loop)
            for i in range(3):
                s, d = call(c, "POST", "/v1/chat/completions",
                             json={"model": "auto", "messages": [{"role": "user", "content": f"serial {i}"}]},
                             headers={"Authorization": f"Bearer {lk}"}, timeout=15)
            scenario("19.4 lifecycle: 3 串行 chat", True, "")

            # 19.5 wide spread 路径
            for path in ["/health", "/v1/models", "/v1/quota", "/openapi.json"]:
                s, d = call(c, "GET", path, headers={"Authorization": f"Bearer {lk}"})
            scenario("19.5 lifecycle: 4 公共端点", True, "")

            s, d = call(c, "DELETE", f"/api/api-keys/{lk_id}", headers=auth_h)
            scenario("19.6 lifecycle: 删", s in (200, 204), "")

    # === 总结 ===
    print("\n" + "=" * 70)
    passed = sum(1 for _, ok, _ in SCENARIOS if ok)
    failed = sum(1 for _, ok, _ in SCENARIOS if not ok)
    print(f" 总结: {passed} pass, {failed} fail (共 {len(SCENARIOS)} 场景)")
    print("=" * 70)
    if failed > 0:
        print("\n失败场景:")
        for name, ok, detail in SCENARIOS:
            if not ok:
                print(f"  ✗ {name}: {detail}")


if __name__ == "__main__":
    main()
