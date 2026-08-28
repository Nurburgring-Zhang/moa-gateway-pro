# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.2.1] — 2026-08-29 — 独立加固审计版（Production Hardening）

三路独立深审（安全+诚实性 / 架构 / 部署工程）→ 用户决策边界（生产加固线、仅文档警示不改默认行为、装 Go 不装 Docker）→ 分步加固。全量测试 **1170 passed, 0 failed**（Windows/Py3.11 实测，含 81 项新加固回归）；Go 代理 0 错误编译 + 16 个测试函数全绿 + 真实 uvicorn 活体 smoke；CI 新增 Go 构建/测试 job。详见 RELEASE_NOTES_v3.2.1.md 与 docs/SECURITY-HARDENING-GUIDE.md。

### 安全
- SSRF: 编码 IP 字面量（十进制/十六进制/八进制/1-4段）在 DNS 之前平台无关归一化判定，畸形 fail-closed；追加 6to4/Teredo 过渡段与 trailing-dot FQDN 封堵（红队复审）
- orchestrator: 非特权调用者不可执行任何沙箱技能（planner 过滤+filtered_privileged_skills 披露 + executor 纵深防御 + MCP 按真实角色 check_access）；DANGEROUS_TOOLS/BUILTIN_TOOL_NAMES 单源化
- skill_factory: load_persisted 重放语法+安全静态校验（功能校验由每次调用的运行时沙箱承担，不在事件循环跑子进程）；builtin 名保护 + 参数名标识符校验（红队 P1: 参数名注入绕过 sanitize_code）+ 重名去重；测试不再污染 data/
- Go 代理: 修复 extractClientIP 未定义（无法编译）；`Bearer mgw-` 网关 key 转发后端鉴权（此前被当 JWT 误拒）；边缘丢弃伪造 X-Forwarded-For；启动日志脱敏 userinfo
- login_attempts upsert 方言感知（PG 上原 `INSERT OR REPLACE` 语法错误）
- 依赖: sqlalchemy≥2.0.36（公告修复）、移除零导入 aiohttp、pyproject 补齐 5 个未声明运行时依赖、pip-audit 真拦截、CI 改用 requirements.txt

### 诚实性
- channels（Subagent/CLI/API）合成输出全链路 `mock` 标注（结果级/链级/orchestrator 透传）
- chat 失败路径记录 5xx 指标（此前仅成功记账，错误率告警永不触发）
- web_search docstring 与实现对齐（诚实失败，不伪造）
- license 矛盾统一（pyproject Apache-2.0 → MIT，与 LICENSE 一致）

### 部署
- Dockerfile.backend: 移除 COPY data/（新克隆必失败+密钥烘焙）；workers 4→1
- HA compose: 删伪 postgres-replica/Swarm replicas；Redis 直连 master；Prometheus 挂载与 rule_files 修复；Grafana 真实 provisioning；删除重复告警文件
- Helm: 补 Secret 模板；readOnlyRootFilesystem + emptyDir；MOA_ADMIN_PASSWORD 注入；版本 1.8.1→3.2.1
- 新增 docs/SECURITY-HARDENING-GUIDE.md（W1-W6 生产警示与缓解配置）

## [3.1.1] — 2026-08-16 — 十轮全量审计修复（P0/P1 清零）

对 v3.1.0 执行十轮全量审计（243 端点扫描、6 路并行深审、本地真实 LLM 链路注入、
浏览器 E2E、chaos 故障注入、对抗性复审），修复全部 P0 与 P1，以及对抗复审二轮新发现项。
测试基线由 593 提升至 **1071 passed, 0 failed**。

### P0 — Agent 沙箱逃逸（RCE）
- 重写 `agent_loop/skills/code_execute.py` + 新增 `agent_loop/sandbox_exec.py` 子进程隔离执行
- AST 层：全 dunder 属性封禁、subscript 字符串键封禁、format 属性遍历封禁、**任意含 dunder 的字符串字面量封禁**
- 导入白名单移除 `operator`（attrgetter/methodcaller）与 `string`（Formatter）——二者可在运行时走属性遍历绕过 AST 封禁
- 运行时兜底：注入模块统一包 `_ModuleProxy`，封禁一切 dunder 动态访问（拦截 chr() 拼接的 format 攻击）
- 危险工具（code_execute/file_read/file_write/file_list/api_verify）仅 admin/operator 可用，API-key 用户 403

### 安全 P1
- **secret-scan**：提权 require_admin + commonpath 限定项目/数据目录 + Finding 源头脱敏（不再回显密钥原文）
- **in-flight**：忽略调用方 state_dir，固定 `DATA_DIR/in_flight_state`（封堵任意目录写原语）
- **health restore/purge**：提权 require_admin，restore 用 EndpointUpsert 严格校验
- **moa prompts PUT/DELETE**：提权 require_admin（封堵跨租户提示词注入）
- **SSRF 统一强化**（`utils/url_validator.py`）：DNS 解析全部落地地址、编码 IP（十/八/十六进制）、
  内部域名、IPv4-mapped IPv6，fail-closed；**显式拉黑 IANA 特殊段含 RFC 6598 CGNAT 100.64.0.0/10
  （阿里云元数据 100.100.100.200 所在段）**；api_verify 与 MCP 外部注册统一委托

### 诚实性 P1（D6 显式 mock 政策闭环）
- MoA 全链路 provider 追踪（ReferenceResult/CriticResult.provider + MoAResult.mock_used），
  `/v1/moa/execute` 返回 `X-MOA-Mock` 头 + `mock` 字段
- MoA 渐进流式补 mock 头（`predict_stream_mock`）
- channels / reference-router 端点显式 mock 标注（头 + `mock:true` + mock_note）
- MoA 参考模型全失败 → 显式 `ProviderError(502)` 并保留逐模型失败证据，**不再静默降级**
- **缓存命中重放 mock 标注**：缓存条目携带 mock 信封，HIT 时重放 `X-MOA-Mock`（修复 mock 输出经缓存后丢标注）

### 功能 P1
- **服务层死方法清零**：修复 quota/routing/quality/knowledge/config/consensus/agent/safety/moa/observability
  共 60+ 处 ImportError/签名错配（含 self_heal promote/demote 错接线），全部改走真实 capability 实现
- **GDPR 被遗忘权真实生效**：路由传真实 db_conn、改删 `admin_users`、按 key_id 解析后匿名化日志；
  匿名化改**加盐 HMAC-SHA256（盐即弃，不可彩虹表还原）**；删除后清理内存 user_id 残留、审计日志不再留存明文 user_id
- **流式配额计费**：`stream=true` 计入每日 token 配额（封堵 stream 绕过计费）
- **MoA 高耗端点限流**：similarity/flask/benchmark/cost-pareto 补 RPM 检查 + token 计费
- **请求模型真实类型化**：85 个 req_models 改真实类型 + `extra=forbid`（未知字段 422）

### 打包与版本
- wheel 补数据文件：prompts/、workflows/builtin/、webui/、param_templates/、migrations（v3.1.0 的 wheel 为零数据文件）
- 版本号四处统一 3.1.1：`__init__.py` / `pyproject.toml` / server.py openapi / routes/health.py
- 前端 admin-ui 会话修复：硬刷新/深链不再丢登录态（请求时 localStorage 兜底读 token + 模块加载即恢复）

### 测试
- 新增 `test_sandbox_escape.py`、`test_v311_fixes.py`、`test_v311_round2.py`、`test_service_methods_real.py` 等
- 全量 **1071 passed, 0 failed**；活体 41/41 + 二轮 4/4；前端 E2E 硬刷新 6/6

## [Unreleased] — v2.1.x

### v2.1.0 (2026-08-06) — 全链路真实化（Wave B1–B5）

#### Wave B1 — 基础修复
- **D1 HMAC 签名链修复** — `audit.py` 改用 `settings.auth.jwt_secret`，移除 type-ignore hack，补签名/验签往返测试
- **D11 config.yaml 乱码清理** — GBK/UTF-8 混写行统一 UTF-8（无 BOM/LF）
- **D9 tri-review 配额对齐** — 三模型互审执行后按真实消耗 `incr_tokens` 记账，记账失败不吞结果仅告警
- **D8 Agent 沙箱收紧** — 沙箱根由 cwd 改为 `data/agent_sandbox`（自动创建 + 路径逃逸校验）

#### Wave B2 — Mock 显式化 + Purge 自毁拆除
- **D6 Mock 显式化** — `mock: {mode: explicit|disabled}` 配置；所有 mock 响应注入 `X-MOA-Mock: true` 头 + usage `mock=true`；mode=disabled 时无 Key 调用返回明确 503，严禁静默模拟
- **T2.4 /health 展示 mock 规模** — 返回 `mock_endpoints_count` / `real_endpoints_count` / `mock_mode`
- **D3 Purge 自毁拆除** — 首轮 purge 延迟 `purge_initial_delay_seconds`（默认 86400s）；探测/淘汰跳过 mock 端点；`purge_records` 快照恢复机制（快照永不含 API Key）

#### Wave B3 — 数据流转接线
- **D2/D12 内部回调鉴权** — YAML 工作流 `_http_post` 与 Assistant executor 回调统一注入网关 admin Key，修复 401 断链
- **D5 Discovery 接线** — `FreeModelDiscoveryEngine` 注入 `settings.discovery.api_keys`
- **D4 空壳指标接线** — `record_llm_request` / `record_cache_access` 等接入真实调用点，/metrics 非零

#### Wave B4 — Agent/任务系统激活
- **D7 Agent 计量真实化** — `LlmUsage`/`LlmOutcome` + `normalize_llm_outcome()`；ReAct 局部累加；PlanExecute 改用每次 run 新建的 `_UsageAcc` 显式传参，杜绝并发串数
- **D12 Runs 健壮化** — `asyncio.wait_for` 超时（先重读落盘终态防覆写）、`_active_run_ids` 409 并发防重、lifespan 僵尸 run 清扫、submit_tool_outputs 状态翻转先持久化再入队
- **D13 TaskBoard 持久化** — SQLite `agent_tasks` 表 + `/v1/agent/tasks` CRUD；分页 `has_more/total`、显式 null 取消指派（`clear_assignee` 哨兵）

#### Wave B5 — Tracer 接线与总回归
- **T5.1 Tracer 接线** — HTTP 中间件 root span 之下注入 `model_pool.call` / `workflow.step` / `moa.execute` / `moa.tri_review` / `assistant.run` / `assistant.submit` / `assistant.llm_call` / `agent.run_loop` 子 span；`create_span` 区分显式 None（无父 root）与省略（继承上下文）；root span 的 span_id 与 `X-Span-ID` 响应头一致；`observability.otlp_endpoint` 配置项 + lifespan 按 `trace_enabled` 启用
- **T5.2 总回归** — 509+ 测试全过、ruff/mypy 零告警、约 30 端点真实 uvicorn 冒烟矩阵

## [Unreleased-old] — v1.8.x

### v1.8.1 (2026-07-19) — OpenAPI field descriptions + endpoint signature cleanup
- **Pydantic Field descriptions (401 fields)** — `_gen_descriptions.py` 给 84 个 Pydantic model 的字段加中文 description,Swagger UI 直接展示字段含义
- **5 dead `request: Request` removed** — `chat_completions` / `moa_execute` / `route_preview` / `quota` 端点签名不再注入无用 Request
- **`get_client_ip` dependency** — `login` 改用 `Depends(get_client_ip)` 抽离 IP 提取,支持 X-Forwarded-For
- **`list_models` 保留 `request: Request`** — 因 `authenticate_api_key(request)` 真用 Authorization header
- **`_raw_payload` → `raw_payload`** — Pydantic 不允许前导下划线字段名
- **Deep E2E 客户端改长连接池** — `urllib.request.urlopen` 换成 `http.client.HTTPConnection` 复用,解决 Windows ephemeral port 1000 上限的 TIME_WAIT 撞池

### v1.8.0 (2026-07-18) — Pydantic BaseModel for 83 endpoints + 90 OpenAPI schemas

## [v1.7.0] — 2026-07-18 — Production Architecture (5 rounds of fixes)

### Round 1: P0/P1/P2 + 80 deep e2e fails → all fixed
- **Global exception handlers** — TypeError/ValueError/KeyError/AttributeError/JSONDecodeError → 4xx
- **43× `HTTPException(500)` → `_err_500()` smart mapper** — input errors → 4xx, real server errors → 500
- **Aggregator.from_dict added** + **BOM stripped** + **duplicate `except HTTPException: raise` removed** (32 dup clauses)
- **P0-11 per-endpoint async lock** for `_saved_api_key` race condition
- **P0-12 chat_completions fallback recheck** on endpoint removal race
- **P1-2 worktree `__import__("os")` cleanup** → direct `os.environ`
- **P1-9 login rate limit** — IP-based, 10 attempts per 60s, new `login_attempts` table
- **moa-n-layer query type validation** — int query → 422
- **moa-3-layer 422 validation** for invalid proposer/aggregator
- **DEEP E2E RESULT**: 432/512 → 512/512 pass (0 fail)

### Round 2: Service Layer + AgentDispatch + Workflow Engine
- **`services/base.py`** — `ServiceBase`, `ServiceMethod`, `ServiceRegistry`, `service_method` decorator
- **`services/dispatcher.py`** — `AgentDispatcher` + `Workflow` + `WorkflowStep` (DAG executor with real inter-module data flow)
- **7 endpoints under `/v1/agent/*`** — list, dispatch, dispatch_batch, workflows, workflow/run, workflow/register

### Round 3: 10 services + 100 methods
- **MoAService** (4): `run_three_layer`, `run_engine`, `cross_iter`, `validate_config`
- **ConsensusService** (7): `vote_ensemble`, `should_rebalance`, `detect_convergent`, `arbitrate_conflicts`, `synthesize_multi_mode`, `check_group_think`, `evaluate_section_viability`
- **RoutingService** (6): `route`, `chain_info`, `execute_chain`, `classify_error`, `cost_estimate`, `reference_route`
- **QualityService** (7): `score_flask`, `rank_elo`, `gate_l0`, `score_panel`, `brainstorm`, `plan_act`, `meta_prompt`
- **AgentService** (18): comms, session_lock, bubble, MCP (subagent_comms, try_acquire, escalate, etc.)
- **QuotaService** (24): rate_quota, per_provider_rl, token_bucket, request_dedup, self_heal, tier_recalibrate, tier_promo, provider_health, consumption_intel, should_rebalance, cost_estimate
- **KnowledgeService** (12): embed, semantic_search, rag_search, fuzzy_dedup, input_fingerprint, rerank, distill, importance, context_clean, turboquant, prompt_features, goal_eval
- **SafetyService** (10): secret_scan, prompt_canary, tool_screening, output_wrapping, frozen, grace, anthropic_compat, llm_merge, version, worktree
- **ObservabilityService** (4): trace, audit, hook_events, in_flight
- **ConfigService** (8): config, mx, checkpoint, artifact, acceptance, action_policy, tool_replay, brainstorm_decide

### Round 4: CapabilityDispatcher (76 capability passthroughs)
- **`services/capability_dispatcher.py`** — single service with 76 `call_<endpoint>` methods
- **All capability endpoints** accessible via `service=capability, method=call_<endpoint>`

### Round 5: 7 builtin workflow templates (all pass with real data flow)
- `moa_quality_pipeline` — validate → run_moa → score_flask (3 steps, 8.9ms)
- `consensus_pipeline` — detect_convergent → vote_ensemble (2 steps, 7.6ms)
- `quality_gate` — gate_l0 → brainstorm (2 steps, 9.1ms)
- `knowledge_pipeline` — embed → semantic_search → rerank (3 steps, 2.4ms)
- `quota_check` — cost_estimate → provider_health → should_rebalance (3 steps, 3.7ms)
- `safety_pipeline` — gate_l0 → tool_screening → output_wrapping (3 steps, 11.4ms)
- `rag_pipeline` — rag_search → rerank (2 steps, 0.9ms)

### Round 6: Production deployment
- **`Dockerfile`** — multi-stage Python 3.11-slim
- **`docker-compose.yml`** — production compose with healthcheck, resource limits, log rotation
- **`.dockerignore`** — exclude test files, caches, secrets
- **`DEPLOYMENT.md`** — comprehensive deployment guide (Linux/macOS/Windows/Docker/K8s)
- **Performance test** (`test_perf.py`):
  - Sequential `/health`: 1000 reqs, p50=0.81ms, p99=23.27ms
  - Concurrent `/health`: 200 threads × 10 = 2000 reqs in 0.28s → **7193 RPS**
  - Concurrent `/v1/agent/dispatch`: 50 threads × 5 = 250 dispatches

## [v1.6.6] — 2026-07-15 — Deep E2E catch-up (4 critical bugs)

## [1.6.6] — 2026-07-15 — Deep E2E catch-up

### Fixed
- **goal-eval** (server.py:1841) — schema mismatch: server sent wrong fields to `Goal()`
  - Goal's actual fields are `(id, description, tier, criteria, evaluator_fn)`
  - Server now maps input to right fields with sensible defaults
- **goal-eval** (server.py:1855) — `generate_ceiling=True` crashed when baseline/residual_risk empty
  - Now defaults to placeholder text when fields missing
- **per-provider-rl** (server.py:1373, 1380) — used `mpl.limiters[provider]` (does not exist)
  - `MultiProviderLimiter` only has private `_limiters`; now uses `mpl._get(provider)`
- **task-tree** (server.py:1714) — `else: tree = TaskTree(root_id='root')` wiped out the built tree
  - Removed the bogus override that was discarding the actual constructed tree
- **moa-n-layer** (server.py:980) — validation failures wrapped as 500
  - Added explicit 400 checks for `proposers` non-empty and `aggregators` count = 3

### Added
- **43 4xx pass-through** (server.py) — auto-patched via `scripts/_patch_4xx.py`
  - Added `except HTTPException: raise` before every `except Exception` block that wraps 500
  - Inner code's 4xx now propagates correctly (no longer wrapped as 500)
- `scripts/test_deep_e2e.py` — 509 cases / 76 endpoints / 11 phases deep E2E
  - Data-driven test that catches production bugs basic E2E misses
- `scripts/_patch_4xx.py` — auto-applies 4xx pass-through fix
- `scripts/_fix_patch_order.py` — reverts bad patch order

### Test results
| Suite | v1.6.5 | v1.6.6 |
|-------|--------|--------|
| Unit tests | 1980 | 1980 |
| E2E (basic) | 137 | 137 |
| Deep E2E | 75 fail | **65 fail** (-10) |
| Server routes | 80 | 80 |

## [1.6.5] — 2026-07-14 — Wave 13 (5 new HIGH)

### Added
- **Wave 13 — 5 new HIGH capabilities** (229/229 tests pass)
  - `tool_screening.py` — 9-segment tool input risk detection (SQL/shell/path/code/prompt/URL/file/network/privesc), 50+ patterns, 5 risk levels (59 tests)
  - `anthropic_compat.py` — Anthropic Messages API compatibility (parse/format_response/SSE/tool_use/tool_result) (45 tests)
  - `token_bucket.py` — Token bucket rate limit (lazy refill, multi-key LRU 10000) (47 tests)
  - `request_dedup.py` — Request dedup with EXACT/NORMALIZED/SEMANTIC strategies + response cache (41 tests)
  - `trace.py` — W3C `traceparent` format, span tree, TraceCollector with LRU (37 tests)
- **5 new endpoints**:
  - `POST /v1/capability/tool-screening`
  - `POST /v1/capability/anthropic-compat`
  - `POST /v1/capability/token-bucket`
  - `POST /v1/capability/request-dedup`
  - `POST /v1/capability/trace`

### Fixed
- `scripts/pack_zip.py` — GBK encoding crash on Windows console (replaced `✓` with `[OK]`, `reconfigure stdout to utf-8`)

### Test results
| Suite | v1.6.4 | v1.6.5 |
|-------|--------|--------|
| Unit | 1751 | **1980** (+229) |
| E2E | 126 | **137** (+11) |
| Security regression | 12/12 | 12/12 |
| Server routes | 75 | 80 (+5) |

## [1.6.4] — 2026-07-14 — Wave 12 + 5 P0 + 2 P1

### Added
- **Wave 12 — 5 new HIGH capabilities** (210/210 tests pass)
  - `audit_cache.py` — LRU + TTL 24h audit event cache (36 tests)
  - `prompt_canary.py` — 4 strategies (SUFFIX/PREFIX/INVISIBLE/MULTI) + 18 injection patterns (48 tests)
  - `output_wrapping.py` — `<untrusted_tool_output>` tags + XML escape (34 tests)
  - `fuzzy_dedup.py` — simhash 64-bit local-sensitive hash (38 tests)
  - `input_fingerprint.py` — 4-layer hash fingerprint + collision detect (54 tests)
- **5 new endpoints**

### Fixed (P0 + P1 from v2 bug hunt)
- **P0-6** `feedback-iter` — RCE via `history_path`; now `require_admin` + path allowlist
- **P0-8** `worktree` — `subprocess.run` no timeout; added `timeout=10s` + `GIT_OPTIONAL_LOCKS=0`
- **P0-9** `_stream_single` — provider race; copies `provider = ep.provider_obj` before stream
- **P0-10** `_pending_close` unbounded — `deque(maxlen=100)` + background `_close_pending_loop`
- **P1-13** `change_password` — bcrypt 300ms blocking event loop; now `asyncio.to_thread`

### Test results
| Suite | v1.6.3 | v1.6.4 |
|-------|--------|--------|
| Unit | 1541 | **1751** |
| E2E | 115 | **126** |
| Server routes | 70 | 75 |

## [1.6.3] — 2026-07-14 — 9 security patches (P0 RCE prevention)

### Fixed (5 P0 + 4 P1)
- **P0-4** `checkpoint` — RCE via `atomic_write` action; now `require_admin` + removed
- **P0-5** `worktree` — RCE via `.gitconfig`; now `require_admin` + cwd allowlist
- **P0-1** `incr_rpm` race condition — now `BEGIN IMMEDIATE` atomic
- **P0-2** `_rebuild_provider` resource leak — uses `_pending_close` queue
- **P0-3** Fernet key TOCTOU — `O_CREAT|O_EXCL` + singleflight
- **P1-1** `incr_tokens` permanent lockout — check before increment
- **P1-2** health check dead code — `is not None` not `isinstance`
- **P1-3** JWT detection — strict regex not `count('.')==2`
- **P1-5** webui path traversal — `os.path.commonpath` not `startswith`
- **P1-6** token length limit — max 256 + multi-value header handling
- **P1-7** chat_completions recheck — endpoint exists after router returns

### Added
- `scripts/test_security_regression.py` — 12 security regression tests
- `BUG_HUNT_REPORT.md` — full 5 P0 + 7 P1 audit

## [1.6.2] — 2026-07-14 — Wave 11 (5 HIGH) + 2 patches

### Added
- **Wave 11 — 5 new HIGH capabilities** (173/173 tests pass)
  - `rag_search.py`, `plan_act.py`, `channels.py`, `reference_router.py`, `checkpoint.py`

### Fixed
- `ratelimit.py` — per-key `quota_rpm` now respected (was using global)
- `ratelimit.py` — admin JWT no longer KeyError on `/v1/quota`
- `server.py` — 4xx errors no longer wrapped as 500 (16 routes fixed)
- `feedback_loop.py` — panel_scores dict keys coerced to int
- `model_pool.py` — first-iter health check runs immediately (no 30s wait)

## [1.6.1] — 2026-07-13 — 4 production bug fixes

### Fixed
- `secret_scan.py` — relative path bug
- `model_pool.py` — JWT routing
- `server.py` — bcrypt async
- `pack_zip.py` — exclude `zip/` to prevent recursion (19 GB → 5.8 MB)

## [1.6.0] — 2026-07-13 — First production release

### Added
- 7 P0 capabilities
- 50 HIGH capabilities (Wave 1-10)
- 80+ server routes
- 1300+ unit tests
- 115+ E2E tests
- GitHub release with zip asset

---

## Cumulative metrics

| Version | Capability modules | Unit tests | E2E tests | Server routes |
|---------|-------------------|------------|-----------|---------------|
| v1.6.0  | 57 (7 P0 + 50 HIGH) | 1300+ | 115 | 70+ |
| v1.6.1  | 57 | 1300+ | 115 | 70+ |
| v1.6.2  | 62 (7 P0 + 55 HIGH) | 1541 | 115 | 70+ |
| v1.6.3  | 62 | 1541 | 115 | 70+ |
| v1.6.4  | 67 (7 P0 + 60 HIGH) | 1751 | 126 | 75+ |
| v1.6.5  | 72 (7 P0 + 65 HIGH) | 1980 | 137 | 80+ |
| v1.6.6  | 72 | 1980 | 137 (deep: 65 fail) | 80+ |

## Upcoming (v1.6.7+)

- **Pydantic validation** for 30 endpoints (replace `body: Dict[str, Any]`)
- **Wave 14** — 5 more HIGH capabilities
- **P1 🔸 medium 109** — long-term investment
- **v1.6.5 deferred P0/P1** — rag_search aiosqlite + storage conn pool + LRU caps
