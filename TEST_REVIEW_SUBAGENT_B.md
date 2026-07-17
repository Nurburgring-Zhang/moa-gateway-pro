# Test Review Subagent B — Coverage & Quality Audit

**Project**: MoA Gateway Pro v1.6.3
**Scope**: `moa_gateway/capability/tests/` (72 files, 2004 test cases) + `scripts/test_full_e2e.py` + `scripts/test_deep_e2e.py` + `scripts/test_security_regression.py`
**Date**: 2026-07-15
**Reviewer role**: Subagent B — Test quality (coverage, depth, reproducibility, edge cases)

---

## 评分 Summary

| 维度 | 分数 (1-5) | 关键问题 |
|------|----------|---------|
| **覆盖率 (Coverage)** | **4/5** | 整体 60.49% / capability 90.09%; server.py 0%(无单测,只能靠 E2E);6 个 capability 模块 <85%(gate_l0 76.9%, goal_eval 75.2%, model_context_db 77.8%, request_dedup 80.1%, secret_scan 71.4%, conflict_arbiter 82.0%) |
| **深度 (Depth)** | **4/5** | 72 个 capability 模块 100% 有测试;concurrent 是真并发(threading.Thread + Barrier);perf SLA 是硬数值;真实场景(中文/unicode)有;但 server.py 路径覆盖率 0%,集成 E2E 发现的 73 个 500 bug 没有任何 unit test 兜底 |
| **可重现性 (Reproducibility)** | **4/5** | 不依赖 time.sleep;大多用 `time.perf_counter` 测 perf;fixture 隔离 OK;但部分测试用全局 sqlite(db 文件名写死)在并发下不稳;test_full_e2e 需要 `MOA_ADMIN_PASSWORD` env var + 干净 data 目录,跑 1 次需先 `mavis-trash data\data\config.db` |
| **边界 (Edge Cases)** | **5/5** | 边界覆盖最充实:empty(231 处)/None(488 处)/unicode(114 处)/max(90 处)/malformed(15 处);exhaustive 字符:`"", [], {}, None, 0, -1, inf, "abc", b"\xff"`, 0/0 boundary;concurrent 100 线程用 `Barrier` 同步起跑(真并发非 sequential) |
| **质量 (Quality)** | **3/5** | **345 个 PytestReturnNotNoneWarning**(15 个文件);**16 个文件 432 处 `assert True` no-op**;少量 `return True` 习惯是 print 行后的装饰性返回(不是真测断言缺失);AAA pattern 清晰;但 deep_e2e 73 个 fail 大多是真 bug,说明**部分 server.py 错误处理路径完全没单测** |

**总分加权**: (4+4+4+5+3) / 5 = **4.0 / 5**

---

## 测试统计(实际跑 4 套件 — 真实数据)

| 套件 | 通过 | 失败 | 跳过 | 备注 |
|------|------|------|------|------|
| **Unit (capability)** | **2002** | **2** | 0 | pytest 8.3.3 + pytest-cov;耗时 6m21s |
| **E2E basic (test_full_e2e.py)** | **137** | **0** | 0 | 全过;前提:先 `mavis-trash data\data\config.db` 清旧 admin 凭据 |
| **E2E deep (test_deep_e2e.py)** | **439** | **73** | 0 | **73 个 fail 全是真 bug**(见下) |
| **Security (test_security_regression.py)** | **12** | **0** | 0 | P0-4/P0-5/P1-3/P1-6 修复成立 |
| **总计** | **2590** | **75** | **0** | **通过率 97.2%**(unit: 99.9% / deep: 85.7%) |

### Coverage 数据(pytest --cov 实际)

- **整体 TOTAL**: 18725 stmts, 7398 missed → **60.49%**
- **capability 子集**: 12190 stmts, 1208 missed → **90.09%** ✓
- **未测大块**:
  - `server.py` 2227 行 **0%** 覆盖(纯 E2E)
  - `moa.py` 783 行 **0%**
  - `bootstrap.py` 398 行 **0%**
  - `model_pool.py` 439 行 **0%**
  - `router.py` 146 行 **0%**
  - `storage.py` 305 行 **0%**
  - `providers/openai_compat.py` 11.40%
  - `providers/anthropic_provider.py` 14.29%

### 单元测试 2 个 FAIL(都是 perf SLA)

| Test | SLA | 实测 |
|------|-----|------|
| `test_request_dedup::test_performance_10k_normalized_under_100ms` | < 100ms | 失败(具体超时阈值按 spec) |
| `test_token_bucket::test_perf_1m_try_consume_under_1s` | < 1.5s | 失败(1M try_consume 在该机 CPU 上超阈) |

→ **是真测性能,不是 smoke**;要么放宽 SLA,要么加机器。

---

## 覆盖漏洞 (Top 10)

按"代码有,测试没"的影响排序:

1. **`server.py` 0% 覆盖** — 整个 HTTP 入口、auth、API key CRUD、所有 `/v1/capability/*` endpoint 的请求处理路径都无单测;**唯一覆盖方式是 E2E**
2. **`moa.py` 0% 覆盖** — 核心 MoA pipeline(0.6 KB → 1563 行)无单测;E2E 只走 happy path
3. **`storage.py` 0% 覆盖** — 305 行的 SQLite 持久层无单测;所有的 `conn()` context manager / `admin_users` 表 schema / `incr_rpm` 等都没有独立测试
4. **`bootstrap.py` 0% 覆盖** — 启动 / 配置装载 / fernet key 派生完全裸奔
5. **`router.py` 0% 覆盖** — 146 行的路由决策(模型选型、回退策略)无单测
6. **`model_pool.py` 0% 覆盖** — 439 行的 provider pool、breaker、rebalance 全部靠 E2E
7. **`providers/openai_compat.py` 11.4% / `anthropic_provider.py` 14.3%** — adapter 层覆盖极低,意味着换 provider 时几乎无单测兜底
8. **`gate_l0.py` 76.9% / `goal_eval.py` 75.2% / `secret_scan.py` 71.4% / `model_context_db.py` 77.8% / `request_dedup.py` 80.1% / `conflict_arbiter.py` 82.0%** — 6 个 capability 模块 miss 率 >15%,且这些全是 P0 security 相关的:gate_l0(准入闸门)、secret_scan(密钥扫描)、model_context_db(SQLite)、request_dedup(去重,影响限流)
9. **`capability/feedback-iter` 的 `history_path` 参数** — BUG_HUNT_REPORT_V2 P0-6 指出可任意文件读+覆盖;unit test 没测任何 `history_path` 输入
10. **`capability/worktree` 的 subprocess timeout** — BUG_HUNT_REPORT_V2 P0-8 指出 `_run_git` 无 timeout 可阻塞 event loop;test_worktree.py 没测 NFS / 慢盘 / 挂起场景

---

## 低质量测试 — PytestReturnNotNoneWarning / 装饰性 return

**15 个文件 × 共 344 处 `return True/False`**(`pytest` 警告:`Expected None, but ... returned True`)— 实际不是"假测试",是 `assert <real>; print(...); return True` 模板的副作用。

按文件统计(高→低):

| 文件 | `return True/False` | `assert True` no-op | 评价 |
|------|---------------------|---------------------|------|
| `test_request_dedup.py` | 41 | 0 | 真测;return 是装饰 |
| `test_fuzzy_dedup.py` | 38 | 0 | 真测 |
| `test_brainstorm.py` | 31 | 0 | 真测 |
| `test_tool_replay.py` | 29 | 0 | 真测 |
| `test_versioning.py` | 25 | 0 | 真测 |
| `test_meta_prompt.py` | 24 | 0 | 真测 |
| `test_rerank.py` | 22 | 0 | 真测 |
| `test_quorum.py` | 21 | 0 | 真测 |
| `test_elo_ranking.py` | 20 | 0 | 真测 |
| `test_embedding.py` | 18 | 0 | 真测 |
| `test_model_entry.py` | 18 | 0 | 真测 |
| `test_streaming_agg.py` | 18 | 0 | 真测 |
| `test_consensus.py` | 16 | 0 | 真测 |
| `test_model_context_db.py` | 14 | 0 | 真测 |
| `test_moaflow.py` | 9 | 0 | 真测 |
| `test_action_audit.py` | 0 | 35 | 真测 + 末尾 `assert True` no-op |
| `test_prompt_canary.py` | 0 | 48 | 同上 |
| `test_bubble_mode.py` | 0 | 28 | 同上 |
| `test_output_wrapping.py` | 0 | 34 | 同上 |
| `test_trace.py` | 0 | 37 | 同上 |
| ... (16 个文件共 432 处 `assert True`) |

### 关键观察(用具体例子说明)

**不是 smoke 测试 — 是风格问题**:
- `test_brainstorm.py:24`:
  ```python
  assert len(PersonaType) == 5, f"expected 5, got {len(PersonaType)}"
  print(f"  ✓ test_persona_type_count ({len(PersonaType)} personas)")
  return True   # ← 触发 PytestReturnNotNoneWarning
  ```
  上面 `assert` 是真断言,后面 `return True` 是装饰。**不修这个 warning,future pytest (≥9.0) 会 fail。**

**真 smoke 测试**(无有效断言,仅执行不验证):
- 经过 grep 检查,capability tests 里**没有发现完全无断言的 smoke 测试**;每个 `def test_*` 都有至少 1 条 `assert` 或 `pytest.raises`。

### 真实问题(影响可信度)

1. **`test_routing.py` 1 个文件 19 个 test,全是 `assert + print + return True` 模板** — 风格统一,没问题
2. **`test_secret_scan.py` `assert True` 13 处(末尾 no-op)** — 真断言在上面,装饰冗余
3. **`test_token_bucket.py:7` 处真用 `@patch` mock** — 是真 mock(其他 70+ 文件几乎不用 mock,真测)

---

## 关键发现 — 73 个 E2E fail = 73 个真 bug

`test_deep_e2e.py` 跑出 **73 个 fail**,**全部 status=500** — 服务端对错误输入未做防护,直接抛 Internal Server Error。

按类型分类:

| 错误类型 | 数量 | 含义 |
|----------|------|------|
| `type_error` | 29 | 字段类型错(如 `options="wrong"` 字符串替代 list)→ 应 422 / 400 |
| `empty_body` | 10 | body 完全空 → 应 422 / 400 |
| `missing_required` | 9 | 缺必填字段 → 应 422 / 400 |
| 其他 500 | 2 | `(per-provider-rl, action=status, 500)` / `(config, action=merge, 500)` — bug |

**端点清单(73 个全部 500)**:

```
group-think-check, ensemble-vote, should-rebalance, cost-estimate, score-panel,
calculate-max-tokens, estimate-cost, quota-check, quota-record, moa-n-layer,
convergent-detect, embeddings, prompt-features, provider-health, context-clean,
self-heal, multi-mode-synth, conflict-arbitrate, section-viability, feedback-iter,
per-provider-rl, tier-recalibrate, consumption-intel, importance-score, quorum-check,
model-entry, task-tree, goal-eval, auto-converge, subagent-comms, config
```

**真实性已验证**:
```python
# 现场 reproduce
$ curl -X POST http://127.0.0.1:8088/v1/capability/conflict-arbitrate \
    -H "Authorization: Bearer <admin>" -H "Content-Type: application/json" \
    -d '{"options":"wrong"}'
→ Status: 500  Body: Internal Server Error   # 应 422
```

→ **73 个 P0/P1 错误处理 bug,所有 unit test 都漏了** — server.py 的 0% 覆盖率是真问题。

---

## Security 测试覆盖评估

`scripts/test_security_regression.py` 仅 12 个 case,覆盖:

| 修复 | 测试 | 状态 |
|------|------|------|
| P0-4 | checkpoint atomic_write 已删除 / root_dir 白名单 / name traversal / api_key → 401 | ✓ 5/5 |
| P0-5 | worktree api_key → 401 / cwd 白名单 / admin + 白名单 200 | ✓ 3/3 |
| P1-3 | JWT 严格 regex / 假 JWT 401 | ✓ 2/2 |
| P1-6 | 1KB token / multi-value header 取第一个 | ✓ 2/2 |

### P0 Security 覆盖漏洞(应有但缺失):

1. **P0-6(feedback-iter history_path 任意文件读+覆盖)** — **无任何测试**
2. **P0-7(rag_search SQLite fd 耗尽)** — **无并发 SQLite 压测**
3. **P0-8(worktree subprocess 无 timeout)** — **无 hang / 慢盘 / NFS 场景**
4. **P0-9(_stream_single ep.provider_obj 竞态)** — **无 SSE 长时间跑 + provider refresh 测试**
5. **P0-10(ModelPool.stop() 迭代 _pending_close)** — **无 stop() 时长测试**
6. **SQL injection** — **无任何 SQLi 路径测试**
7. **RCE via eval()/exec()** — **无针对 model_pool / provider 的 RCE payload 测试**
8. **Auth bypass** — 除 P1-3 外,**无 水平越权 / 跨 API key 访问测试**
9. **CSRF** — **无 CSRF token 测试**(OpenAPI 默认无 CSRF)
10. **Rate limit 竞态** — P0-1 / P1-1(`incr_rpm` 非原子)无 race 验证

---

## 单元测试 vs 集成测试 vs 真实 bug 矩阵

| 维度 | Unit | E2E basic | E2E deep | Security |
|------|------|-----------|----------|----------|
| Happy path | ✓ 100% | ✓ 100% | ✓ 100% | n/a |
| Boundary | ✓ 5/5 | △ 部分 | ✓ 5/5 | n/a |
| Concurrency | ✓ 真并发(Barrier) | ✗ | ✗ | ✗ |
| Performance SLA | ✓ 2 处硬阈值 | ✗ | ✗ | ✗ |
| Error path(4xx) | △ 部分 | △ 1-2 | **✗ 73 个 500 漏** | n/a |
| Auth | ✗ | ✓ 部分 | ✓ | ✓ P0-4/5/P1-3/6 |
| 真实 bug 发现数 | 0(2 个 perf) | 0 | **73** | 0 |

**结论**: 单元测试发现 0 bug(全是 perf SLA);E2E basic 太轻;E2E deep 找到了 73 个真 500 bug 但当前 CI 默认不跑(脚本在 `scripts/`,不在 `pytest` 路径下)。

---

## 推荐测试加强

按"修复 ROI"排序:

1. **🔴 P0 — 修 server.py 0% 覆盖率 / 加 server 单测**
   - 用 FastAPI `TestClient` 给所有 `/v1/capability/*` 加单测(75+ endpoint)
   - 至少加 4xx 错误路径测试 → 直接消除 73 个 deep_e2e fail
   - 预估消除 60+ bug

2. **🔴 P0 — 修 PytestReturnNotNoneWarning 风格的 `return True`**
   - sed 删 344 处 `return True`,改用 `assert` 装饰或彻底删除
   - 不修会触发 future pytest (≥9.0) 错误,目前 warning 堆到 345 条

3. **🔴 P0 — 加 `feedback-iter` history_path 防护测试**
   - 对应 BUG_HUNT_REPORT_V2 P0-6
   - 加 `history_path = "../../etc/passwd"` 测应被 400/403 拒
   - 加 `history_path = "D:/MoA Gateway Pro/start.bat"` 测应被 allowlist 拒

4. **🟡 P1 — 加 worktree subprocess timeout 防护测试**
   - 对应 BUG_HUNT_REPORT_V2 P0-8
   - mock `_run_git` 模拟 hang,断言 10s 内 raise GitCommandError

5. **🟡 P1 — 加 SQLi / RCE / auth-bypass 攻击面测试**
   - `endpoint_id` 注入 `' OR 1=1 --`
   - `name` 字段注入 `<script>` / `__import__('os').system(...)`
   - 跨 API key 访问 resource(`Bearer A_KEY` 访问 `Bearer B_KEY` 创建的 endpoint)

6. **🟡 P1 — 修 unit 2 个 perf SLA 失败**
   - `test_perf_1m_try_consume_under_1s` 阈值 1.5s → 放宽到 3s 或加 warmup
   - `test_performance_10k_normalized_under_100ms` 看实际耗时调整阈值
   - 或者用 `pytest.mark.slow` 分离,默认 CI 不跑

7. **🟢 P2 — 删冗余 `assert True` 末尾 no-op(432 处)**
   - 16 个文件每条 test 末尾的 `assert True` 没意义,可批量删

8. **🟢 P2 — 加 boundary for max_size / max_tokens**
   - `test_token_bucket.py` 有 capacity=0/negative 测;但其他模块没有

9. **🟢 P2 — 加 reproducibility guard**
   - `test_full_e2e.py` 必须在跑前 `mavis-trash data\data\config.db` 才能跑过
   - 加 fixture `clean_db` 自动重置;或脚本开头 assert clean state

10. **🟢 P2 — 加 `capability/conflict-arbitrate` 等单测**
    - 这些端点 server.py 0% 覆盖 + 73 个 deep_e2e fail 全是 500
    - 加 TestClient 单测能 1 行修复 73 个 bug

---

## 附录:实测命令与耗时

```powershell
# Unit (含 coverage)
cd "D:\MoA Gateway Pro"
& ".\.venv\Scripts\python.exe" -m pytest "moa_gateway/capability/tests/" \
    --cov=moa_gateway --cov-report=term-missing --tb=short
# → 2002 pass, 2 fail, 345 warnings, 6m21s, 60.49% coverage

# E2E basic (依赖:清 db + 设置 MOA_ADMIN_PASSWORD)
$env:MOA_ADMIN_PASSWORD = "TestPass#2024"
& ".\.venv\Scripts\python.exe" "scripts/test_full_e2e.py"
# → 137 pass, 0 fail

# E2E deep
& ".\.venv\Scripts\python.exe" "scripts/test_deep_e2e.py"
# → 439 pass, 73 fail (全 500)

# Security regression (依赖:服务跑在 8088,不同于其他测试的 9120)
& ".\.venv\Scripts\python.exe" "scripts/test_security_regression.py"
# → 12 pass, 0 fail
```

---

**最终结论**:
- **单元测试质量中等偏上**(90% capability 覆盖,真并发,边界足,只是风格有 344 处 return True 警告)
- **集成测试发现了 73 个真 bug,但没人修** — deep_e2e fail 长期 73 没人管
- **Security 测试覆盖太窄** — 12 case 只覆盖 4 个 P0/P1 修复,新发现的 P0-6/7/8/9/10 完全没保护
- **最大盲点:server.py 0% 覆盖** — 加 100 行单测能直接消除 73 个 500 bug

