# MOA (Multi-tenant Operations Agent) — Enterprise Edition Deep Analysis

> **项目**: `moa-main` (hwuiwon/moa) — 基于 Rust 的云优先、多租户 AI Agent 运营平台
> **许可证**: Apache-2.0
> **代码规模**: 1216 个 Rust 源文件,45 个 Cargo crate,32 篇架构文档
> **定位**: 企业级 Agent Operations 平台,Postgres-first + Restate 持久化编排

---

## 1. 项目概述 (Project Overview)

**MOA (Multi-tenant Operations Agent)** 是面向企业的 AI Agent 运营平台,运行在 Restate 之上(用于持久化编排),数据落在 Postgres/Neon + pgvector(用于产品记录、审计、向量与图谱记忆),通过 OCSF v1.3 实现安全事件审计,通过 OpenFGA 实现细粒度授权,通过 Auth0/OIDC + SCIM v2 实现 SSO/企业身份供给,通过 Object Lock 兼容 S3 实现不可篡改审计归档。

核心能力包括:
- **多租户隔离**: tenant 是运行时/RLS/数据隔离硬边界,workspace 是部署级管理边界
- **任务分段 (Task Segmentation)**: 每个会话拆为离散任务段,带工具/技能使用、成本与解决率评分
- **技能学习 (Skills-first Learning)**: tenant 本地、resolution-weighted、append-only 且可回滚
- **治理执行 (Governed Execution)**: 风险动作需审批、密钥不进入模型代码、MCP 凭证代理
- **可插拔 Hands (Pluggable Execution)**: local / Docker / Daytona / E2B / MCP 走统一 ToolRouter
- **Provider 无关**: Anthropic、OpenAI、Google Gemini 一等公民
- **多模态消息**: Slack、Postmark Email、Twilio SMS、聊天、Web 联系
- **可观测性**: Prometheus metrics、OTel/OpenInference lineage、Merkle 链审计

架构层次: **Edge (`moa-edge`) → Restate Ingress → Orchestrator (`moa-orchestrator`) → Brain (`moa-brain`) → Hands (`moa-hands`) + Postgres/Neon**。

---

## 2. 核心模块清单 (Workspace Crates - 45 个)

| Crate | 行/规模 | 职责 |
|---|---|---|
| `moa-core` | 94 .rs | 共享类型/traits/config/events/analytics DTO,稳定接口 |
| `moa-brain` | 75 .rs | 上下文管线、查询改写、检索、回合执行、解决率评分、流式回合、lineage |
| `moa-db` | - | 共享 SQLx pool、scoped connection、RLS scope |
| `moa-session` | 24 .rs | Postgres 会话存储、事件日志、任务段、学习日志、analytics 视图、Neon 分支 |
| `moa-analytics` | - | 通用分析目录、查询校验、ClickHouse 执行、provider-agnostic SQL 编译 |
| `moa-runtime-store` | - | 内存与 Redis/Valkey 运行时缓存 |
| `moa-migrations` | - | 中心化 Postgres 迁移与 schema 运行器 |
| `moa-memory/graph` | 10 .rs | 关系型图谱节点/边表、sidecar、RLS、双时态、变更日志 |
| `moa-memory/ingest` | 14 .rs | 慢速路径图谱摄入 + 快速 memory 写 API、矛盾检测、实体解析 |
| `moa-memory/lifecycle` | 5 .rs | 记忆合并、质量评分、digest 生成 |
| `moa-memory/pii` | 4 .rs | PII 分类、openai/privacy-filter 集成、记忆擦除 |
| `moa-memory/types` | - | 共享 memory 域类型 |
| `moa-memory/vector` | 7 .rs | pgvector 存储、Turbopuffer 提升路径、同步 |
| `moa-knowledge` | 13 目录 | 租户知识库域、provider(Nango/Merge)、parsers(LlamaParse/Unstructured/Reducto/liteparse)、解析/分块/图谱 delta/检索 |
| `moa-lineage/core` | 5 .rs | lineage 记录与分数类型 |
| `moa-lineage/citation` | - | provider 引用规范化、BM25/NLI 验证 |
| `moa-lineage/sink` | - | 异步 lineage sink 写 (ClickHouse 等) |
| `moa-lineage/otel` | - | OTel/OpenInference bridge |
| `moa-lineage/audit` | 7 .rs | 合规审计哈希、Merkle 根、签名、DSAR、admin/export |
| `moa-observability` | 7 .rs | 运行时 metrics、tracing bootstrap、Restate 观测、TTFT、turn latency |
| `moa-authz-schema` | 3 .rs | 类型化 OpenFGA object/relation/tuple-key 常量 |
| `moa-authz` | 7 .rs | OpenFGA 客户端、`require_authz`、事务性 outbox、poller、awakeable |
| `moa-auth-providers` | 10 .rs | 本地 API 密钥、disabled auth、builtin 审批、null token vault |
| `moa-auth-providers-auth0` | 6 .rs | Auth0 / 通用 OIDC、Token Vault、CIBA、JWKS cache、group sync |
| `moa-fga-bootstrap` | - | OpenFGA store 与授权模型引导二进制 |
| `moa-ocsf` | 7 .rs | OCSF v1.3 安全事件、签名、持久化、批写、emit/spawn API |
| `moa-edge` | 42 .rs | 公共 HTTP edge、authn、identity 头注入、Auth0 webhooks、MCP HTTP transport |
| `moa-hands` | 20+ 目录 | ToolRouter、本地/Docker/Daytona/E2B hands、MCP 客户端、15 个内置工具 |
| `moa-providers` | 55 .rs | LLM/embedding/rerank provider、模型目录、限流/重试/router、OpenAI/Anthropic/Gemini 适配 |
| `moa-orchestrator` | 151 .rs | Restate 服务/虚拟对象/工作流、`moa-orchestrator-bin`、SCIM v2、authz admin、learning review、experiments、eval、agent definitions |
| `moa-agents` | 4 .rs | tenant 可配置 agent 解析、运行时策略锁定、安装/部署 |
| `moa-contacts` | - | 联系人身份域、持久化辅助 |
| `moa-artifacts` | - | agents/skills/connectors/actions/experiment-plans 规范定义 |
| `moa-experiments` | - | 实验运行与评分卡配置 |
| `moa-scoring` | - | 共享评分运行存储与汇总查询 |
| `moa-messaging` | 15 .rs | Slack 适配、Postmark Email、Twilio SMS、平台渲染、动作审批渲染 |
| `moa-security` | 4 .rs | 凭证 vault、MCP 代理、policies、prompt-injection 防护 |
| `moa-skills` | 20 .rs | Agent Skills 解析、蒸馏、改进、回归、提议、程序图 |
| `moa-eval-core` | - | 共享 eval 引擎类型与评分原语 |
| `moa-eval` | 63 .rs | 评估 harness(长对话、memory、kernel、golden、pentest、external_memory) |
| `moa-loadtest` | - | 直接 HTTP 负载测试 harness |
| `moa-test-support` | - | 共享集成测试夹具、Postgres helper、合同检查 |
| `workspace-hack` | - | `cargo-hakari` 生成的 feature 统一 crate |
| `xtask` | - | repo-local 审计/维护命令 |

辅助服务 (`services/`):
- `audit-shipper/` — Python 服务,将 PostgreSQL pgaudit 日志 + OCSF `security_events` 压缩签名后送到 S3 Object Lock
- `pii-service/` — Python PII 过滤 HTTP 服务,使用 OpenAI

---

## 3. 详细能力列表 (Capabilities)

### 3.1 API 能力 (HTTP / API Endpoints)

#### 3.1.1 Edge 公共 HTTP API (`moa-edge`,Axum 路由)

- **健康**: `GET /healthz` — 进程探活
- **认证与账户**:
  - `POST /v1/auth/login` — 邮箱 + 密码登录,可选 `tenant_id` / `tenant_slug` 消歧义,`remember_me` 长期会话
  - `POST /v1/auth/logout`
  - `POST /v1/auth/password/reset-request` — 邮件重置令牌(30 分钟 TTL)
  - `POST /v1/auth/password/reset` — 消耗一次性令牌
  - `POST /v1/auth/password` — 已登录用户改密,可撤销其他会话
  - `PATCH /v1/users/me` / `GET /v1/users/me` — 资料/settings 修改
  - `POST /v1/tenant/users/{user_id}/password` — tenant-admin 重置
  - `POST /v1/tenants/signup` — 自助租户注册
  - `GET/PATCH/DELETE /v1/tenant` — 租户元数据 CRUD
  - `POST /v1/tenant/purge/{operation_id}` / `GET .../{op_id}` — 异步租户擦除
  - `GET/POST /v1/tenant/users` — 列出/创建用户
  - `POST /v1/tenant/invitations` / `POST /v1/tenant/invitations/accept` — 邀请与接受
- **会话 (公开 Bearer 通道)**:
  - `POST /v1/sessions` — 初始化 agent 会话(public contact)
  - `POST /v1/sessions/{id}/promote` — 将会话绑定到已验证 contact
  - `PATCH /v1/sessions/{id}/channel` — 切换通讯通道
  - `POST /v1/sessions/{id}/messages` — 发送消息(默认 12MB body)
  - `POST /v1/sessions/{id}/progress` — 获取活跃 turn 进度 + 事件流(范围/游标)
  - `POST /v1/sessions/{id}/cancel` — 取消(默认 `task_tree`,支持 `coordinator_only`)
  - `GET /v1/sessions/{id}/attachments/{aid}` — 附件下载
  - `POST /v1/contacts/verification/start` / `.../complete` — OTP 验证 contact
  - `POST /v1/sessions/{id}/contacts/verification/start` / `.../complete`
- **审批/Action Review**:
  - `GET /v1/authz-challenges` — 列出待处理的 CIBA/builtin 审批
  - `POST /v1/authz-challenges/{id}/decision` — 提交审批决策
  - `GET /v1/action-reviews` — 列出待处理的 tenant action review
  - `POST /v1/action-reviews/{id}/decision` — 决策
  - `POST /v1/contacts/tokens` — 颁发 contact JWT
  - `POST /v1/authz/api-key-tenant-roles` — 受限的 API key 角色授予(grant/revoke `admin`/`operator` tenant 关系)
- **分析/审计/血缘(直读)**:
  - `GET /v1/analytics/catalog` — 分析目录(数据集/字段)
  - `POST /v1/analytics/query` — catalog 校验的 SQL 编译查询
  - `POST /v1/audit/verify` — OCSF event 签名验证(JCS+HMAC)
  - `POST /v1/lineage/explain` — 解释某条决策的 lineage
  - `POST /v1/lineage/query` — 按条件检索 lineage
  - `POST /v1/lineage/verify` — 验证 Merkle proof / DSAR
- **Webhook**:
  - `POST /v1/security/secret-scanning/github` — GitHub secret scanning alert
  - `POST /v1/webhooks/auth0/connection-linked` — HMAC-SHA256 验证的 Auth0 链接回调
  - `POST /v1/knowledge/webhooks/{nango,merge,llamaparse,reducto}` — 4 个 provider 知识回调
- **MCP 传输**: `MoaMcpServer` (rmcp) — Streamable HTTP `/mcp` 端点,严格 Host/Origin allowlist,contact/agent 身份拒绝
- **catch-all**: `GET/PATCH/POST/DELETE /v1/{*rest}` → 转发到 Restate ingress `/restate/call/...`,X-Moa-* 身份头注入

#### 3.1.2 MCP 工具(tenant-operations,`moa-edge/src/mcp`)

| 类别 | 工具 |
|---|---|
| 观测/分析 | `analytics_catalog`, `analytics_query`, `sessions_list`, `session_get`, `session_events_list`, `lineage_explain`, `learning_candidates_list` |
| 工件/学习 | `artifacts_list`, `artifact_export`, `artifact_validate`, `artifact_import`, `artifact_publish`, `learning_candidate_get`, `learning_candidate_accept_skill`, `learning_candidate_reject` |
| 流程 | `capabilities_list`, `procedure_runs_list`, `procedure_run_status`, `procedure_run_start`, `procedure_run_cancel`, `procedure_review_decide`, `procedure_signal` |
| 内部 Eval | `eval_suites_summarize`, `eval_plan`, `eval_datasets_list`, `eval_dataset_register`, `eval_run`, `eval_run_status`, `eval_scores`, `eval_compare` |
| 实验 | `experiment_plan_generate`, `experiments_list`, `experiment_run`, `experiment_status`, `experiment_trials_list`, `experiment_trial_status`, `experiment_cancel`, `experiment_scores`, `experiment_compare`, `experiment_propose_improvements` |
| 配置型 Agent | `agent_definitions_list`, `agent_installations_list`, `agent_definitions_deploy`, `agent_revision_compare`, `agent_revision_simulate`, `agent_revision_simulation_compare` 等 |
| Agent 主体生命周期 | `agent_principal_register`, `agent_principals_list`, `agent_principal_get`, `agent_principal_deactivate`, `agent_principal_grant_act_as`, `agent_principal_revoke_act_as` |

所有工具返回 `{summary, data}` 结构,失败时 `{error}` + `isError: true`。schema 含闭枚举、JSON Schema bounds、annotations(readOnly/Destructive/Idempotent/OpenWorld)。

#### 3.1.3 SCIM v2 端点 (`/scim/v2`,port 10022)

- `GET/POST /scim/v2/Users`
- `GET/PUT/PATCH/DELETE /scim/v2/Users/{id}`
- `GET/POST /scim/v2/Groups`
- `GET/PUT/PATCH/DELETE /scim/v2/Groups/{id}`
- `GET /scim/v2/ServiceProviderConfig`, `/ResourceTypes`, `/Schemas`
- 过滤器支持 `userName eq "x"`, `emails.value eq "x"`, `externalId eq "x"`, `displayName eq "x"`
- `PATCH active=false` 触发去激活级联:取消活动会话、撤销 API 密钥(`deactivation_cascade`)、enqueue OpenFGA 元组删除、移除 SCIM 组成员
- Group 映射 `tenant:<T>:admin` ↔ `operator:<U> admin tenant:<T>`

#### 3.1.4 Restate 服务表面(`moa-orchestrator-bin`)

- **Virtual Object**: `Session`, `Worker`, `Tenant`, `CronJob`, `IngestionVO`
- **Workflow**: `TurnExecution`, `WorkerTurnExecution`, `ProcedureExecution`, `KnowledgeSyncIngestion`, `Consolidate`, `ExperimentRun`, `ExperimentTrialRun`, `SkillLearning`(feature-gated)
- **Service**: `AgentDefinitions`, `Agents`, `AdminMaintenance`, `Artifacts`, `ActionReviews`, `ApiKeys`, `Authz`, `AuthzChallenges`, `Contacts`, `Eval`, `Experiments`, `GraphMemoryMaint`, `Knowledge`, `LearningReview`, `LLMGateway`, `Memory`, `NeonMaint`, `Privacy`, `SessionStore`, `Skills`, `Tenants`, `ToolExecutor`, `ActionPolicy`
- 部署注册由 `restate-register` sidecar 自动完成,`MOA_REQUIRE_RESTATE_REGISTRATION_FOR_READINESS=true` 时 readiness 探针等待注册完成

#### 3.1.5 Ingress 路径(Restate 1.7+ 协议)

- 未键控: `POST /restate/call/{Service}/{handler}`
- 键控 VO: `POST /restate/call/{Service}/{key}/{handler}`
- 流控(per-tenant 准入): `POST /restate/scope/tenant-{tenant_id}/call/{Service}/{key}/{handler}`
- fire-and-forget: `POST /restate/send/{...}` (orchestrator 自用,edge 不用)
- cluster rule book:`* → concurrency 1000` 默认,可通过 `restate rules set` 增加 exact-scope 限额

### 3.2 数据模型 (Data Model)

#### 3.2.1 标识符 (`moa-core::types::identifiers`)
- UUID newtype: `SessionId`, `SegmentId`, `TenantId`, `BrainId`, `ToolCallId`, `SessionAttachmentId`, `AgentSignalId`
- String newtype: `UserId`, `StoragePartitionId`(用 tenant UUID 文本), `ModelId`(例 `claude-sonnet-4-6`)

#### 3.2.2 事件枚举 (`Event`, 40+ 变体,strum EnumDiscriminants)
生命周期:`SessionCreated`, `SessionStatusChanged`, `SessionChannelChanged`, `SessionCompleted`
任务段:`SegmentStarted`, `SegmentCompleted`
消息流:`UserMessage`, `QueuedMessage`, `BrainThinking`, `BrainResponse`, `ProgressUpdate`, `ProgressNarrated`
工具:`ToolCall`, `ToolResult`, `ToolError`
Action Review:`ActionReviewRequested`, `ActionReviewDecided`
Workers:`WorkerSpawned`, `WorkerMessageSent`, `WorkerStatusChanged`, `WorkerNotificationDelivered`, `WorkerResultBundle`, `WorkerResultSynthesisRequested`, `WorkerSignalReceived`, `WorkerParentResumeRequested`, `WorkerHeartbeatStale`
内存:`MemoryRead`, `MemoryWrite`, `MemoryIngest`
Hands:`HandProvisioned`, `HandDestroyed`, `HandError`
系统:`Checkpoint`, `CacheReport`, `TurnMetrics`, `Error`, `Warning`, `GuardrailCheck`

每个事件分类为 `ProcessingEffect::{Trigger, Neutral, Terminal}`,反向 tail 扫描据此判断是否需要新 turn。

#### 3.2.3 任务段 (`TaskSegment` + `SegmentCompletion`)
含 tools_used, skills_activated, token_cost, duration_ms, segment_index, task_summary, outcome assessment (resolution score 0-1)

#### 3.2.4 体验与归因 (`ExperienceRecord` + `ExperienceAttribution`)
- `task_fingerprint` 任务指纹
- `success_rate` 策略成功率
- attribution 链:skills/tools/memory/policy/verification

#### 3.2.5 学习候选 (`LearningCandidate`)
状态机: `proposed → evaluating → promoted | rejected`
含 `source_experience_ids`, `payload`, `evaluation_payload`, `risk_class`, `promotion_requirements`

#### 3.2.6 学习日志 (`LearningEntry`)
- `valid_from` / `valid_to` 双时态
- `batch_id` 支持 batch 回滚
- types: `skill_created`, `skill_improved`, `memory_updated`, `segment_assessed`
- scopes: `global | tenant | contact`

#### 3.2.7 联系与 Channel (`Contact`, `Channel`, `Attachment`)
- `Channel` 枚举: Chat, Slack, Email, SMS
- `SessionChannelBinding` 关联会话↔渠道,支持 `binding_id` 路由
- `ContactPoint` 邮箱/电话哈希(`MOA_CONTACT_POINT_HASH_KEY_HEX` 32字节)

#### 3.2.8 Actions & Policy (`ActionEnvelope`, `ActionPolicyRule`)
- `ActionClass`: `LocalWrite | NetworkWrite | ExternalService | SystemAction`
- `RiskLevel`: `Low | Medium | High | Critical`
- 规则 `effect`: `Allow | Deny | AdminReview`
- `stricter_effect()` 函数决定规则叠加

#### 3.2.9 Artifacts (`moa-artifacts`)
统一结构: agents, skills, connectors, actions, experiment plans 共享 `moa-artifact-v1.schema.json`

#### 3.2.10 OCSF 事件类
- `AuthenticationEvent` (class_uid 3002)
- `AuthorizationEvent` (3003) — 包含 privileges, resource
- `AccountChangeEvent` (3001) — create/enable/disable/delete
- `EntityManagementEvent` (3004) — agents, api_keys, scim, contacts
- `Metadata { version: "1.3.0", product: { name, vendor_name, version } }`
- Activity IDs: 1=Logon, 5=CredentialValidation, 1=GrantPrivileges, 2=Revoke, 99=Other
- Status IDs: 1=Success, 2=Failure, 1=Allowed, 2=Denied

### 3.3 算法 (Algorithms)

#### 3.3.1 上下文管线 (`moa-brain::pipeline`)
- 阶段(顺序): `identity → agent_instructions → instructions → tools → query_rewrite → skills → digest → memory → history → delegation_planning → runtime_context → compactor`
- **fetch/apply 分离**: 标记 `parallelizable()` 的阶段并发读取不可变 `WorkingContext`,再按顺序 apply,保证 prompt 缓存字节稳定
- **byte-stable**: 同一份输入下,编译后的请求字节一致,以最大化 provider 提示缓存命中

#### 3.3.2 查询改写 (`moa-brain::query_rewrite`)
- 重写为紧凑表述,标准化大小写、归一化 Unicode

#### 3.3.3 记忆检索 (`moa-memory::vector`, `moa-memory-graph::lexical`)
- pgvector 主路径,embedding: OpenAI、Cohere v4、Gemini、ZeroEntropy、mock
- hybrid: BM25 (rust-stemmers) + dense 融合
- Rerank 候选:Cohere v4 (latency-bounded)
- moka future::Cache 缓存 frequent query

#### 3.3.4 解决率评分 (`moa-brain::segment_assessment`, `moa-core::types::segment_assessment`)
- `SegmentBaseline`: tenant 结构基线
- `SkillResolutionRate`: aggregated `resolved | partial | failed` by tenant & skill
- `TaskStrategySuccessRate`: 任务指纹+策略成功率
- `ResolutionConfig` (configurable weights): 决定 partial vs resolved

#### 3.3.5 技能蒸馏与改进 (`moa-skills`)
- `distiller`: 从体验记录抽取出可重用的 Agent Skills(OpenAI / Anthropic 风格 SKILL.md)
- `improver`: 对照 baseline + regression suite 修改现有 skill
- `regression`: 评分回归测试集
- `mining`: weakness mining → candidate proposals
- `candidates`: 候选状态机持久化

#### 3.3.6 Shell 命令解析 (`moa-core::shell`, `moa-security::policies`)
- `split_shell_chain`: 按 `&&` `||` `;` `|` 拆解,避免单条规则覆盖整条管线
- `parse_and_match_command`: glob 模式匹配(globset)
- `has_action_policy_unsafe_shell_syntax`: 检测重定向、子 shell 等

#### 3.3.7 OCSF JCS 规范化 + HMAC-SHA256 签名 (`moa-ocsf`)
- 规范化: `jcs::canonicalize()` 按 RFC 8785
- 签名: HMAC-SHA256(`tenant_signing_keys.key_b64`, jcs_bytes) → hex
- 缓存: moka TTL 300s
- 验证: `constant_time_eq` 恒时比较

#### 3.3.8 Merkle 链审计 (`moa-lineage-audit`)
- 顺序哈希链:每个 entry 包含前一个 hash
- Merkle root 周期性发布
- DSAR 导出包含验证路径

#### 3.3.9 Provider 重试 + 限流 (`moa-providers::core::retry`, `rate_guard`)
- 指数退避: 默认 1s → 60s, 因子 2.0
- 终端 429 记录 cooldown,可被后续调用 short-circuit 或 failover
- `CapabilityTier` 5 档: Frontier / Flagship / Balanced / Fast / Light,failover 限制在同一档
- 并发控制: per-provider `MAX_REQUESTS_PER_MIN`, `MAX_INPUTS_PER_MIN`, `MAX_CONCURRENT_REQUESTS`

#### 3.3.10 Failover (`moa-providers::failover`)
- 同档内自动切换 model(provider 内)
- 跨档需要显式 `MOA_PROVIDERS_OVERRIDE=scripted:/path.json`

#### 3.3.11 记忆合并与质量 (`moa-memory-lifecycle`)
- `consolidate`: 标记 superseded / expired / merged / contradicted
- `quality`: 评分节点质量,触发 promotion
- `digest`: 为 context 准备 summary digest
- `curate`: 选择性保留高频事实

### 3.4 UI 能力 (UI Surfaces)

#### 3.4.1 Operator Dashboard (内部,SSR via `crates/moa-edge/src/routes/dashboard/`)
- 静态 HTML + JS 前端(代码在 dashboard/ 子目录)
- 通过 `moa-edge` 直接读取数据,使用受 OpenFGA 保护的 read-only endpoint

#### 3.4.2 Tenant-Operations MCP (`/mcp`,rmcp Streamable HTTP)
- 6 类 ~50 工具,如 3.1.2 所列
- 输入 schema 包含 `Use when:`, `Returns:`, `Next:` 字段
- 强类型 JSON Schema enums

#### 3.4.3 Grafana Dashboards (`dashboards/`)
- `long-conversation-eval.json` — 长对话评估面板
- `k8s/observability/` — K8s 监控模板

#### 3.4.4 第三方系统集成
- Slack: 渲染 + 富消息 (SlackRenderer, max 40000 chars)
- Email (Postmark): 验证 token + 模板
- SMS (Twilio): 状态回调
- 联系人公开链接/QR: contact verification start/complete

### 3.5 集成能力 (Integrations)

#### 3.5.1 LLM Provider
- **Anthropic** (`moa-providers/src/adapters/anthropic/`): Claude 全系,support tools + vision + prompt cache
- **OpenAI** (`adapters/openai_responses/`): Responses API (rustls, default-features off)
- **Google Gemini** (`adapters/gemini/`): Gemini 全系
- **Scripted** (`adapters/scripted/`): 重放脚本响应(用于 loadtest, `MOA_PROVIDERS_OVERRIDE=scripted:/loadtest-scripts/perf-gate.json`)

#### 3.5.2 Embedding / Rerank
- OpenAI embedding, Cohere v4, Gemini, ZeroEntropy, mock
- Cohere v4 rerank(可选 latency-bounded)

#### 3.5.3 Hands / Sandboxes
- `local` (LocalHandProvider) — 开发用
- `docker` — `docker exec`, `--network none`
- `daytona` (DaytonaHandProvider) — 商业 microVM
- `e2b` (E2BHandProvider) — 云 sandbox
- MCP clients (rmcp streamable HTTP) — 外部 MCP server

#### 3.5.4 Knowledge Provider (`moa-knowledge/src/providers/`)
- `nango` — OAuth/code-owned sync
- `merge` — normalized common models
- `unstructured` — 文档解析
- `llamaparse` — 文档布局解析
- `reducto` — 文档压缩解析
- `liteparse` (Rust crate) — 本地 PDF 解析(默认)

#### 3.5.5 通讯 Channel
- Slack: 通过 OAuth 安装,supports DM/channel/thread
- Email: Postmark (token from CredentialVault `platform.postmark.server_token`)
- SMS: Twilio (account_sid, auth_token, api_key_sid/secret, from_number, messaging_service_sid)

#### 3.5.6 身份/SSO
- Auth0 (RS256 JWT, JWKS cache TTL 1h or on kid miss)
- OIDC 通用
- SCIM v2 (Okta-compatible)
- 本地 API 密钥(默认)
- Disabled auth(仅测试)

#### 3.5.7 存储
- Postgres 17 + pgvector 0.8.2 + pgaudit
- Neon 分支(checkpoint)
- Object Store: AWS S3, GCP, RustFS(开发)
- Redis/Valkey (运行时缓存)
- ClickHouse (analytics + lineage export)
- RustFS(本地对象存储,开发)

#### 3.5.8 观测
- OTLP (gRPC tonic + HTTP/proto)
- Prometheus (metrics-exporter-prometheus)
- Grafana
- OTel/OpenInference
- Debezium(`ops/debezium/`) — CDC 订阅

### 3.6 工具能力 (Tooling & Built-in Tools)

`moa-hands/src/tools/` 内置工具(15 个):
- `bash` — shell 执行(支持 policy glob)
- `file_read`, `file_write`, `file_outline`, `file_search` — 文件操作
- `str_replace` — 字符串替换
- `edit_output` — 编辑流式输出
- `docker_file` — Docker 文件生成
- `grep` — 文本搜索
- `fs_util` — 文件系统工具
- `memory` — 图谱 memory 工具
- `sandbox_descriptor` — sandbox 描述
- `session_search` — 跨 session 搜索
- `tool_result` — 工具结果包装

每个工具带: `name`, `description`, `input_schema (JSON Schema)`, `policy_spec`, `idempotency_class`, `max_output_tokens`, `definition`。

### 3.7 安全能力 (Security — 重点)

#### 3.7.1 身份验证 (Authn)
- **本地 API 密钥**: hashed, dual-prefix (`moa_`), 4 段随机 base32, scoped by env + tenant + agent
- **Auth0/OIDC**: RS256, JWKS cache (1h TTL, refresh on kid miss), `https://moa/tenant_id` & `https://moa/identity_type` 自定义 claim
- **Password (本地)**: argon2 哈希, min 12 chars / max 1024 chars, reset token 30 分钟 TTL
- **Contact JWT**: 显式 `requested_scopes` + `agent_ids` 必填,不能成为 admin/operator, bounded route/data
- **SCIM API key**: 需显式 grant `tenant#admin` 关系

#### 3.7.2 授权 (Authz)
- **OpenFGA**: 默认引擎, self-hosted 1.8.16
- 关系图: `workspace#admin` → `tenant#admin` → `tenant#operator` → `api_key:<id>` / `agent:<id>` / `operator:<id>` / `contact:<id>` / `service:<id>`
- **require_authz** / **require_authz_with_delegation**: 类型化关系检查 + OCSF 审计发射
- **决策缓存**: 允许结果 2s TTL(可配),拒绝永缓存以保证快速重检
- **事务性 outbox**: tuple 写入与产品状态在同一 PG 事务提交,poller 异步同步到 OpenFGA
- **Tuple 一致性**: 创建对称删除路径(`SAFETY` 注释要求,outbox 反向 tuple)
- **API key 授权**: key 的 subject(非 owner)运行 check,scoped 至 key

#### 3.7.3 OCSF v1.3 审计
- **类**: Authentication (3002), Authorization (3003), AccountChange (3001), EntityManagement (3004)
- **同步发射** (fail-closed): authn_failure, api_key_created/revoked_tx, agent_registered/deactivated_tx, delegation_granted/revoked_tx, scim_*
- **异步发射** (background batch, spawn_): authn_success, authz_decision (deny 同步)
- **签名**: HMAC-SHA256(tenant key) over JCS 规范化 JSON
- **存储**: Postgres `security_events` 表 + `tenant_signing_keys` 表
- **导出**: Python audit-shipper 服务批量压缩签名,送 S3 Object Lock COMPLIANCE mode
- **验证**: `POST /v1/audit/verify {event_id, tenant_id}` → `moa-ocsf::signing::verify` 用 JCS bytes + signature_hex + signing_key_id

#### 3.7.4 行级安全 (RLS)
- `storage_partition_id` 是 RLS scope key(等于 tenant UUID)
- 所有 storage crate 使用 scoped connection, 强制 partition

#### 3.7.5 沙箱分层
- **Tier 0**: 进程内(MOA 内部代码、memory 工具)
- **Tier 1**: 容器 / managed workspace(普通 hand 工具)— read-only root, non-root, dropped caps, seccomp, no network
- **Tier 2**: MicroVM(高风险未信任代码)— Daytona
- **Worker 隔离**: 每个 worker 拥有独立 sandbox,lease key `(session_id, worker_id, provider)`

#### 3.7.6 Prompt Injection 防护
- **Canary token**: 每 turn UUIDv7, `moa_canary_` 前缀,system message 指示模型不要复制
- **Tool input screening**: `screen_tool_input_for_canary` — active canary 泄漏或 generic marker 命中 → Blocked
- **Output wrapping**: `wrap_untrusted_tool_output` 用 `<untrusted_tool_output>` 边界包,边界 tag 内部出现则 HTML escape
- **Inspection**: `inspect_input` 9 类启发式评分(`ignore_previous_instructions`, `you are now`, `system:`, `delimiter_token` 等)→ Normal/MediumRisk/HighRisk
- **工具权限收紧**: 输出不直接追加到 system message,加 wrapper 标记
- **Canary prompt cache 友好**: standing rule 放在 identity prefix,不重复

#### 3.7.7 Action Policy
- 规则层: `permissions.admin_review`, `permissions.always_deny` 配置 glob + DB 持久化 rules
- 决策效果: `Allow | Deny | AdminReview`
- Shell 链解析: `split_shell_chain` 不让一条规则覆盖整条 `cmd && other_cmd`
- `admin_review` 写持久化 `tenant_action_reviews` + event + 返回 pending-review 给模型,worker 不阻塞
- 显式 admin 决策通过 `ActionReviews/decide` Restate 服务

#### 3.7.8 MCP 凭证代理
- `MCPCredentialProxy`: process-local, single-use, opaque grants
- 不持久化,同 K8s pod 内有效
- `EnvironmentCredentialVault`: 从环境变量或 runtime config 注入,不入沙箱

#### 3.7.9 联系人验证
- 邮箱/电话哈希(32 字节 HMAC key,`MOA_CONTACT_POINT_HASH_KEY_HEX`)
- 显式 `verification/start` + `.../complete` 升级 token
- Contact JWT 不能成为 admin/operator

#### 3.7.10 Tenancy 隔离
- 同一 `tenant_id` 的资源 RLS 严格隔离
- 联系人 JWT 限定 `agent_ids` + `scopes`
- SCIM 限定为 tenant admin 关系

#### 3.7.11 治理 (Governance)
- **DSAR**: `moa-lineage-audit::export` 导出受签名的隐私包
- **Erasure**: `moa-memory-pii::erasure` 实现 subject erasure
- **Compliance tier**: opt-in,显式 attestation caveat,等待外部密码学审查

### 3.8 性能能力 (Performance)

#### 3.8.1 Prompt Caching
- byte-stable context pipeline
- `moa-brain::harness` 跟踪 cache hit/miss
- `CacheReport` 事件持久化
- `gen_ai.client.token.usage` OTel metric (cached/uncached/write)

#### 3.8.2 Latency Metrics (Prometheus)
- `moa_turn_step_duration_seconds` — histogram, [1ms, 30s]
- `moa_session_event_append_phase_seconds`
- 6 步追踪: `snapshot_load`, `snapshot_write`, `pipeline_compile`, `llm_call`, `tool_dispatch`, `event_persist`
- `gen_ai.client.operation.duration`, `time_to_first_chunk`
- `moa_authz_decision_cache_*` (hit/miss)

#### 3.8.3 限流与并发
- Per-provider `MAX_REQUESTS_PER_MIN`, `MAX_INPUTS_PER_MIN`, `MAX_CONCURRENT_REQUESTS`
- `moa-providers::core::concurrency_factory` 提供 per-model 池
- GlobalConcurrency 与 tenant-scope 协同

#### 3.8.4 缓存层
- `moa-runtime-store`: 进程内 moka + Redis/Valkey
- `signing_key_cache` (10000 tenant, 5min TTL)
- `decision_cache` (100k, 2s TTL)

#### 3.8.5 准入控制 (Restate vqueues)
- `* → concurrency 1000` 默认
- per-tenant scope `tenant-{uuid}`,ingress `/restate/scope/.../call/...`
- 实验特性 `RESTATE_EXPERIMENTAL_ENABLE_VQUEUES=true`

#### 3.8.6 分布式执行
- Restate 工作流跨 K8s 副本,无 sticky session
- Process-local map 仅作 reconnect / transport demux / 性能 cache,正确性由 PG/Restate/Redis 保证

#### 3.8.7 编译优化
- `[profile.release]`: `strip = "symbols"`, `lto = "thin"`, `debug = 0`
- `[profile.dev]`: workspace opt-level 0, dependencies opt-level 2
- `split-debuginfo = "unpacked"`
- Hakari 统一 feature 减少编译时间

#### 3.8.8 内存 + 队列
- `tokio::sync::mpsc` 事件流
- `tokio_util::sync::CancellationToken` 协作取消
- `moka` future cache
- `dashmap` 等

#### 3.8.9 ClickHouse 分析导出
- 高频 analytics 行分离到 ClickHouse
- 配置: `MOA_CLICKHOUSE_URL`, `MOA_CLICKHOUSE_USER`, `MOA_CLICKHOUSE_PASSWORD`
- 查询预算: `clickhouse_max_execution_time_secs`, `clickhouse_max_rows_to_read`, `clickhouse_max_bytes_to_read`

### 3.9 部署能力 (Deployment)

#### 3.9.1 Docker (单仓库多服务)
- 根 `Dockerfile` — orchestrator
- `crates/moa-edge/Dockerfile` — edge
- `docker/postgres/Dockerfile` — Postgres 17 + pgvector 0.8.2 + pgaudit
- `services/audit-shipper/Dockerfile` (Python)
- `services/pii-service/Dockerfile` (Python)

#### 3.9.2 docker-compose (本地开发)
- 8 服务: postgres, valkey, openfga-db-init, openfga-migrate, openfga, rustfs, rustfs-init, restate, moa-orchestrator, moa-edge
- 端口: 10010 (restate ingress), 10011 (restate admin), 10012 (restate cluster), 10030 (openfga), 10032 (openfga playground), 10040 (postgres), 10050 (pii), 10051 (valkey), 10021 (orchestrator health), 10022 (scim), 10023 (metrics), 10000 (edge)
- `docker-compose.edge.yml`, `docker-compose.chaos.yml` (network partition)
- `make dev` 引导,`make dev-wipe` 重置,`make dev-status` 检查

#### 3.9.3 Kubernetes (生产)
- `k8s/base/`:
  - `00-namespace.yaml`
  - `10-restate-cluster.yaml` (RestateDeployment CRD)
  - `20-orchestrator-deployment.yaml` (6 replicas, 500m-2CPU, 1-4Gi, RestateDeployment CRD 6 replicas)
  - `25-orchestrator-service.yaml`
  - `26-orchestrator-network-policy.yaml` (NetworkPolicy 锁定)
  - `30-orchestrator-hpa.yaml` (HPA)
  - `40-orchestrator-pdb.yaml` (PodDisruptionBudget)
  - `50-edge-deployment.yaml` (3 replicas, 250m-1CPU, 512M-1Gi)
  - `55-edge-service.yaml`
  - `kustomization.yaml`
- `k8s/overlays/production/`, `k8s/overlays/local/`
- `k8s/observability/` — Prometheus + Grafana
- `k8s/scripts/` — `restate-rules-bootstrap.sh` 注入 scope rules
- HPA 配置 / PDB / NetworkPolicy 完整

#### 3.9.4 Restate cluster
- 1.7.0 + vqueues 实验特性
- Bifrost record cache 128Mi
- 默认 `* → concurrency 1000`

#### 3.9.5 CI/CD (`.githooks/`, `.github/`, `.cargo/`)
- `git diff --check` 钩子
- Workspace Hakari
- 镜像推送到 `ghcr.io/hwuiwon/moa-{orchestrator,edge}`

#### 3.9.6 Makefile (`Makefile`)
- `make dev`, `make dev-down`, `make dev-wipe`, `make dev-status`
- `make dev-restate-ui`, `make loadtest-mock`, `make loadtest-live`
- `make check`, `make lint`, `make test`

#### 3.9.7 数据库迁移 (`moa-migrations`)
- 中心化,启动时自动 apply
- 支持 schema migration 与 seed
- `cargo run -p moa-orchestrator -- migrate` 单独运行

#### 3.9.8 Postgres 启动调优
- `shared_preload_libraries=pgaudit`
- `wal_level=logical` (支持 Debezium CDC)
- `max_replication_slots=10`, `max_wal_senders=10`
- pgaudit `log=write,ddl,role`, `log_relation=on`, `log_parameter=off`

### 3.10 测试能力 (Testing)

#### 3.10.1 测试 lanes (`docs/20-testing.md`)
- 后缀约定: `_offline`, `_db`, `_db_memory`, `_service_e2e`, `_provider_e2e`, `_eval`, `_live`, `_docker`
- `cargo nextest` 并行,每测试独占 ID/tempdir/schema/port

#### 3.10.2 Live 测试
- `#[ignore = "..."]` + env flag 如 `MOA_RUN_LIVE_COHERE_TESTS=1`
- 无 credentials 时显式失败
- 永不写 secrets 到 fixtures/git

#### 3.10.3 测试组织
- 单元: `#[cfg(test)]` 内联
- 集成: `tests/<name>.rs`
- 离线/`_db`/`_db_memory` per-lane harness binary,例如 `tests/orchestrator_db.rs` + `mod foo_db;` 拼装
- e2e/live/eval: standalone 二进制

#### 3.10.4 Eval Harness (`moa-eval`)
- 离线测试 (long_conversation, memory_eval, golden, pentest, external_memory)
- `EvalEngine::run_suite(suite, configs)` 并行运行
- 5 类 metrics: kernel, cost, counting, stats, compare
- `EvalLineageHandle` — 抓取 eval 期 lineage
- `TrajectoryCollector` — 收集执行轨迹

#### 3.10.5 Scorecards (`docs/eval/scorecards/`)
- 定义评估标准与基线

#### 3.10.6 外部记忆基准
- `external-memory-benchmarks.md` — 跨产品对比
- `wixqa-rag-experiments.md` — WixQA RAG 实验
- `memory-eval-pipeline.md` — 记忆评估管道

#### 3.10.7 Load Testing (`moa-loadtest`, `make loadtest-{mock,live}`)
- 直接 HTTP,step-latency 报告使用 6 步 Prometheus metric
- `loadtest-scripts/perf-gate.json` scripted provider

#### 3.10.8 Chaos Testing
- `docker-compose.chaos.yml` — 注入网络分区

#### 3.10.9 CI Tooling
- `cargo fmt --all`
- `cargo clippy --workspace --all-targets -- -D warnings`
- `cargo test --workspace --no-run`
- `cargo build --workspace`
- `git diff --check`

#### 3.10.10 Snapshots
- `insta` crate (json, redactions)
- byte-stable request body snapshot
- 模式在 `docs/20-testing.md`

#### 3.10.11 Property Testing
- `proptest` for 解析、shell 拆分等

#### 3.10.12 Wiremock
- Provider HTTP mock (Anthropic, OpenAI, Gemini)
- 用于 offline 端到端测试

#### 3.10.13 SCIM Compliance
- `MOA_RUN_LIVE_SCIM_TESTS=1` 跑 live Okta 兼容测试

---

## 4. 技术栈 (Tech Stack)

### 4.1 语言与构建
- **Rust**: edition 2024, resolver = 2
- **Cargo workspace**: 45 crates
- **cargo-hakari**: feature 统一
- **cargo-machete**: 死依赖检测
- **RustFS**: S3 兼容对象存储(开发)
- **Python 3**: audit-shipper, pii-service

### 4.2 核心依赖(workspace 级)
| 依赖 | 版本 | 用途 |
|---|---|---|
| `axum` | 0.8 | HTTP 服务(edge, orchestrator handlers, SCIM) |
| `restate-sdk` | 0.10 | 持久化编排 |
| `tokio` | 1 (full) | 异步运行时 |
| `sqlx` | 0.8 (rustls, postgres, uuid, chrono, json) | Postgres 驱动 |
| `clickhouse` | 0.13 (rustls-tls) | ClickHouse |
| `rmcp` | =2.2.0 (server, streamable-http) | MCP 协议 |
| `async-openai` | 0.34 (rustls, responses) | OpenAI 客户端 |
| `serde`, `serde_json`, `serde_yaml`, `serde_canonical_json` | 1 | 序列化 |
| `chrono` | 0.4 | 时间 |
| `uuid` | 1 (v7) | UUID |
| `tracing`, `tracing-subscriber`, `tracing-opentelemetry`, `tracing-appender` | - | 可观测性 |
| `opentelemetry`, `opentelemetry-otlp`, `opentelemetry_sdk` | 0.31 | OTLP 导出 |
| `metrics`, `metrics-exporter-prometheus` | 0.24 / 0.18 | Prometheus |
| `moka` | 0.12 | future cache |
| `redis` | 0.32 (tokio-comp) | Redis/Valkey 客户端 |
| `object_store` | 0.11 (aws, gcp) | 对象存储 |
| `arrow`, `parquet` | 53 | 列存 / 数据导出 |
| `schemars` | 1.2 | JSON Schema |
| `hmac`, `sha2`, `blake3`, `argon2`, `ed25519-dalek` | - | 加密 |
| `jsonwebtoken` | 9.3 | JWT 验签 |
| `reqwest` | 0.13 (http2, json, multipart, rustls, stream) | HTTP 客户端 |
| `insta`, `wiremock`, `httpmock`, `proptest` | - | 测试 |
| `tempfile`, `dirs`, `regex`, `globset`, `shell-words`, `rust-stemmers`, `similar` | - | 工具 |
| `liteparse` | 2.2.1 | PDF 解析 |
| `secrecy` | 0.10 | 密钥封装 |
| `subtle`, `constant_time_eq` | - | 恒时比较 |
| `eventsource-stream` | 0.2 | SSE 客户端 |
| `clickhouse` | 0.13 | OLAP |
| `croner` | 2.2 | Cron 解析 |
| `chrono-tz` | 0.10 | 时区 |
| `flate2`, `tar` | - | 压缩 / 归档 |
| `testcontainers` | 0.23 | 集成测试容器 |

### 4.3 基础设施
- **Postgres 17**: 主数据库 + pgvector 0.8.2 + pgaudit
- **Neon**: 可选 serverless Postgres(分支)
- **Restate 1.7.0**: 持久化编排
- **OpenFGA 1.8.16**: 细粒度授权
- **Redis/Valkey 8**: 运行时缓存
- **ClickHouse**: 分析/血缘导出
- **AWS S3 / GCP / RustFS**: 对象存储
- **Slack / Postmark / Twilio**: 通讯
- **Auth0**: 身份/SSO/Token Vault/CIBA
- **GitHub**: secret scanning
- **Nango / Merge / LlamaParse / Reducto / Unstructured**: 知识库
- **Daytona / E2B**: 沙箱
- **OpenTelemetry / Prometheus / Grafana**: 可观测
- **Debezium**: CDC

### 4.4 LLM 提供方
- **Anthropic** (Claude)
- **OpenAI** (Responses API + Embeddings)
- **Google Gemini**
- **Cohere** (v4 Embeddings + Rerank)
- **ZeroEntropy** (Embeddings)
- **Scripted** provider(loadtest 复用)

---

## 5. 关键代码片段 (Key Code Snippets)

### 5.1 Edge → Restate 转发(身份头注入 + 路径校验)
```rust
// crates/moa-edge/src/proxy.rs:30-68
pub async fn forward(&self, identity: &Identity, method, path, body, request_headers) {
    validate_upstream_path(path)?;  // 防 SSRF: 必须 /restate/ 前缀, 无 //, 无 ..
    let url = format!("{}{}", self.upstream_base, path);
    let mut request = self.http.request(method, url);
    for (name, value) in request_headers {
        if !should_forward_header(name, has_body) { continue; }  // 去掉 X-Moa-*, hop-by-hop
        request = request.header(name, value);
    }
    // 注入受信任的 X-Moa-* 头
    request = request
        .header(H_IDENTITY_TYPE, identity_type_str(identity.identity_type))
        .header(H_IDENTITY_ID, identity.id.to_string())
        .header(H_TENANT_ID, identity.tenant_id.to_string());
    if let Some(api_key_id) = identity.api_key_id {
        request = request.header(H_API_KEY_ID, api_key_id.to_string());
    }
    ...
}
```

### 5.2 OpenFGA 鉴权 + 决策缓存 + 审计发射
```rust
// crates/moa-auth/authz/src/require.rs:119-144
pub async fn require_authz(fga, identity, object_type, object_id, relation) -> Result<()> {
    let object_id = object_id.to_string();
    let subject = fga_subject(identity);  // e.g. "operator:<uuid>" or "api_key:<uuid>"
    let object = format!("{object_type}:{object_id}");
    let allowed = cached_decision(&subject, &relation_str, &object, || {
        fga.check(&subject, &relation_str, &object)
    }).await?;  // 允许缓存(2s TTL), 拒绝不缓存
    emit_authz_audit(identity, &object, object_type, &relation, allowed).await?;
    if !allowed { return Err(AuthzCheckError::Forbidden{...}); }
    Ok(())
}
```

### 5.3 OCSF v1.3 HMAC 签名 + 验证
```rust
// crates/moa-ocsf/src/signing.rs:145-175
pub async fn sign(pool, tenant_id, event_json) -> Result<(Uuid, String, Vec<u8>)> {
    let mut tx = pool.begin().await?;
    sign_tx(&mut tx, tenant_id, event_json).await
}

pub async fn sign_cached(pool, tenant_id, event_json) -> Result<...> {
    let active = active_key_cached(pool, tenant_id).await?;  // moka TTL 300s
    let key_bytes = B64.decode(active.key.expose_secret())?;
    let event_jcs = jcs::canonicalize(event_json)?;  // RFC 8785
    let signature_hex = hmac_hex(&key_bytes, &event_jcs)?;
    Ok((active.key_id, signature_hex, event_jcs))
}

pub async fn verify(pool, signing_key_id, event_jcs, signature_hex) -> Result<bool> {
    let row = sqlx::query_as("SELECT key_b64 FROM tenant_signing_keys WHERE id = $1")
        .bind(signing_key_id).fetch_optional(pool).await?;
    let expected = hmac_hex(&key_bytes, event_jcs)?;
    Ok(constant_time_eq::constant_time_eq(expected.as_bytes(), signature_hex.as_bytes()))
}
```

### 5.4 ToolRouter 执行 + Retry/Recovery
```rust
// crates/moa-hands/src/core/dispatch.rs:23-71
impl ToolRouter {
    pub async fn execute_authorized_with_cancel(
        &self, session, invocation, cancel_token, hard_cancel_token,
    ) -> Result<(Option<String>, ToolOutput)> {
        let tool_span = tool_execution_span(session, invocation);
        async move {
            let started_at = Instant::now();
            let prepared = self.prepare_invocation(session, invocation).await?;
            let registered_tool = self.registry.tools.get(&invocation.name)...;
            record_tool_invocation_metadata(&tool_span, session, &registered_tool.execution, &prepared.policy().effect);
            let result = self.execute_authorized_inner(session, invocation, cancel_token, hard_cancel_token).await;
            record_tool_execution_result(&tool_span, &invocation.name, started_at.elapsed(), &result);
            result
        }.instrument(tool_span).await
    }
}
```

### 5.5 Shell 命令链解析 + Action Policy
```rust
// crates/moa-security/src/policies.rs:96-160 (ActionPolicies::check)
pub fn check(&self, input, ctx, rules) -> Result<ActionPolicyCheck> {
    for rule in rules {
        if !rule_visible_to_context(rule, ctx) { continue; }
        if rule.tool != input.tool_name { continue; }
        if rule_matches(rule, &input.tool_name, &input.normalized_input)? {
            matched_rule = Some(stricter(rule, current));  // Deny > AdminReview > Allow
        }
    }
    if let Some(rule) = matched_rule { return Ok(...); }
    if let Some((effect, reason)) = self.configured_tool_effect(&input.tool_name) { return Ok(...); }
    Ok(ActionPolicyCheck { effect: stricter(input.default_effect, self.default_effect), ... })
}
```

### 5.6 Canary Token 注入 + 工具输入筛选
```rust
// crates/moa-security/src/injection.rs
pub fn inspect_input(content: &str, expected_canaries: &[String]) -> InputInspection {
    let normalized = content.to_ascii_lowercase();
    let mut score = 0.0f32;
    for (needle, signal, weight) in [
        ("ignore previous instructions", "ignore_previous_instructions", 0.8),
        ("you are now", "identity_reassignment", 0.7),
        ("system:", "spoofed_system_role", 0.3),
        ("<|", "delimiter_token", 0.5),
        ("reveal the hidden prompt", "prompt_exfiltration", 0.7),
        ("print the canary", "canary_exfiltration", 0.9),
    ] {
        if normalized.contains(needle) { score += weight; signals.push(signal); }
    }
    let leaked_canary = contains_canary_tokens(content) || expected_canaries.iter().any(|c| content.contains(c));
    if leaked_canary { score += 1.0; }
    let classification = if score >= 0.8 { HighRisk } else if score >= 0.4 { MediumRisk } else { Normal };
    InputInspection { classification, score, signals, leaked_canary }
}

pub fn wrap_untrusted_tool_output(content: &str) -> String {
    let body = content.trim_end()
        .replace("</untrusted_tool_output>", "&lt;/untrusted_tool_output&gt;")
        .replace("<untrusted_tool_output>", "&lt;untrusted_tool_output&gt;");
    format!("<untrusted_tool_output>\n{body}\n</untrusted_tool_output>")
}
```

### 5.7 Multi-tenant Routing: Ingress Scope + Tenant Concurrency
```rust
// crates/moa-edge/src/ingress.rs:55-65
pub(crate) fn call_path(scope: &IngressScope, service_path: &str) -> String {
    match scope.scope_key() {
        Some(scope_key) => format!("/restate/scope/{scope_key}/call{service_path}"),
        None => format!("/restate/call{service_path}"),
    }
}
impl IngressScope {
    fn scope_key(&self) -> Option<String> {
        match self {
            IngressScope::Unscoped => None,
            IngressScope::Tenant(tenant_id) => Some(format!("tenant-{tenant_id}")),
        }
    }
}
```

### 5.8 Event Scheduling: Trigger/Neutral/Terminal
```rust
// crates/moa-core/src/events.rs:574-633
pub fn processing_effect(&self) -> ProcessingEffect {
    match self {
        // Triggers
        Self::UserMessage{..} | Self::ToolCall{..} | Self::ToolResult{..} | Self::ToolError{..}
        | Self::WorkerParentResumeRequested{..} | Self::WorkerResultSynthesisRequested{..}
            => ProcessingEffect::Trigger,
        // Terminals
        Self::BrainResponse{..} | Self::SessionCompleted{..} | Self::Error{..}
        | Self::ActionReviewRequested{..} | Self::ActionReviewDecided{..}
            => ProcessingEffect::Terminal,
        // Neutrals (late async events must not mask triggers)
        Self::SessionCreated{..} | Self::SegmentStarted{..} | Self::SegmentCompleted{..}
        | Self::QueuedMessage{..} | Self::BrainThinking{..} | Self::ProgressUpdate{..}
        | Self::GuardrailCheck{..} | Self::WorkerSpawned{..} | Self::WorkerStatusChanged{..}
        | Self::WorkerResultBundle{..} | Self::TurnMetrics{..} | Self::WorkerSignalReceived{..}
        | Self::WorkerHeartbeatStale{..} | Self::ProgressNarrated{..}
        | Self::MemoryRead{..} | Self::MemoryIngest{..}
        | Self::HandProvisioned{..} | Self::HandDestroyed{..} | Self::HandError{..}
        | Self::Checkpoint{..} | Self::CacheReport{..} | Self::Warning{..}
        | Self::WorkerNotificationDelivered{..} | Self::WorkerMessageSent{..}
        | Self::SessionStatusChanged{..} | Self::SessionChannelChanged{..}
            => ProcessingEffect::Neutral,
    }
}
```

### 5.9 Capability Tier Failover
```rust
// crates/moa-providers/src/core/models.rs
pub enum CapabilityTier {
    Frontier,   // rank 0
    Flagship,   // rank 1
    Balanced,   // rank 2
    Fast,       // rank 3
    Light,      // rank 4
}
impl CapabilityTier {
    pub fn distance(self, other) -> u8 { self.rank().abs_diff(other.rank()) }
}
// Failover 限制: 同档内 0..=1,不允许 Frontier → Light
```

### 5.10 Provider Retry + Rate Guard
```rust
// crates/moa-providers/src/core/retry.rs:50-100
pub async fn send_gated<F>(&self, build_request: F, guard: &RateGuard) -> Result<Response> {
    let mut attempt = 0usize;
    loop {
        let response = match build_request().send().await {
            Ok(r) => r,
            Err(error) => {
                let retry_eligible = self.is_retryable_transport_error(&error) && attempt < self.max_retries;
                if retry_eligible && guard.allow_retry() {
                    let delay = self.delay_for_attempt(attempt);
                    tokio::time::sleep(delay).await;
                    attempt += 1;
                    continue;
                }
                return Err(MoaError::ProviderError(format!("provider request failed: {error}")));
            }
        };
        let status = response.status();
        if status.is_success() { return Ok(response); }
        // 429 cooldown + retry
        ...
    }
}
```

---

## 6. 集成点 (Integration Points)

### 6.1 外部服务集成
| 集成 | 路径 | 鉴权/协议 |
|---|---|---|
| LLM | `crates/moa-providers/src/adapters/{anthropic,openai_responses,gemini,scripted}/` | API key, RS256, OAuth |
| Embedding/Rerank | `crates/moa-providers/src/embedding/`, `crates/moa-providers/src/rerank/` | API key |
| 沙箱 | `crates/moa-hands/src/adapters/{local,daytona,e2b,mcp}/` | API key / socket |
| Slack | `crates/moa-messaging/src/slack/` | OAuth bot token |
| Postmark | `crates/moa-messaging/src/postmark.rs` | Server token (CredentialVault) |
| Twilio | `crates/moa-messaging/src/twilio.rs` | Account SID + auth token |
| Auth0 | `crates/moa-auth/auth0/src/{auth0_provider,oidc_provider,ciba,vault,jwks_cache}` | OIDC JWT, JWKS, CIBA push |
| OpenFGA | `crates/moa-auth/authz/src/client.rs` | Preshared key |
| Knowledge | `crates/moa-knowledge/src/providers/{nango,merge}/` + parser | OAuth / API key |
| PII Filter | `services/pii-service/main.py` (HTTP) | API key |
| Object Storage | `crates/moa-session/src/attachment_storage.rs` (rustfs / S3 / GCP) | AWS sigv4 / HMAC |
| ClickHouse | `crates/moa-lineage-sink`, `crates/moa-analytics/src/clickhouse_exec.rs` | HTTP basic auth |
| GitHub | webhook `POST /v1/security/secret-scanning/github` | HMAC |
| Restate | `crates/moa-orchestrator` (restate-sdk 0.10) | 1.7.0 cluster |
| Postgres | `crates/moa-db/`, 所有 storage crate | sqlx 0.8 + pgvector + pgaudit |
| Redis/Valkey | `crates/moa-runtime-store/` | redis 0.32 |
| OTLP | `crates/moa-observability/` | gRPC tonic / HTTP proto |
| Prometheus | `crates/moa-observability/src/runtime_metrics.rs` | HTTP scrape |
| OpenTelemetry | 全栈 | OTel SDK 0.31 |

### 6.2 内部接口
- `moa-core::traits::{SessionStore, BlobStore, BranchManager, LLMProvider, EmbeddingProvider, HandProvider, BuiltInTool, ChannelAdapter, ContextProcessor, CredentialVault, AuthProvider, TokenVaultProvider, AsyncAuthzProvider, LineageHandle}` — 12 个稳定 trait
- `moa-authz::require_authz` — 所有 handler 鉴权必经
- `moa-ocsf::emit_*_tx` / `spawn_*` — 审计事件
- `moa-memory-graph::bitemporal_write` — 图谱写
- `moa-lineage-core::LineageSink` — lineage 接收
- `moa-observability::runtime_metrics` — 6 步 turn 追踪

### 6.3 部署集成
- **K8s 资源**: 1 namespace, 1 RestateDeployment CRD, 1 HPA, 1 PDB, 1 NetworkPolicy, 2 Deployments, 2 Services, 1 storage class
- **Postgres**: pgaudit 输出 csvlog 供 audit-shipper 读
- **OCSF 安全事件**: audit-shipper 读 + 送 S3 Object Lock COMPLIANCE

### 6.4 凭据来源(`CredentialVault` service/scope)
- `platform.postmark.server_token`
- `platform.twilio.account_sid` / `auth_token` / `api_key_sid` / `api_key_secret` / `from_number` / `messaging_service_sid`
- Auth0 linked-connection tokens
- MCP server 凭据(proxy, ephemeral)

### 6.5 信任边界
- `moa-edge` 是公开 trust boundary,内部 Restate handler port 必须在 prod 保持 internal-only
- `x-moa-*` 头不被外部信任,edge 主动 strip 后注入
- `/mcp` endpoint 强制 `tenant:#operator`、拒绝 contact/agent、严格 Host/Origin allowlist
- Dashboard OAuth(规划中)将用 PKCE + RFC 8707, RFC 8414/9728 metadata,short-lived audience-bound token

---

## 7. 商业级特性亮点 (Commercial-Grade Highlights)

| 维度 | 实现 |
|---|---|
| **多租户** | workspace→tenant→contact→session 层级;tenant 硬隔离 (RLS + OpenFGA);workspace 软管理 |
| **权限** | OpenFGA 1.8.16 关系模型;workspace#admin→tenant#admin→tenant#operator 继承;4 类主体(operator/api_key/agent/contact);事务性 outbox 保 tuple 一致性 |
| **审计** | OCSF v1.3 四类(Authentication/Authorization/AccountChange/EntityManagement);HMAC-SHA256 + JCS 规范化 + 恒时验证;Postgres + S3 Object Lock 双写;6 个 SCIM 事件类 |
| **SLA** | Restate 持久化工作流;Replicate/Retry/Awakeable 模式;turn progress 心跳;6 步 Prometheus 延迟追踪; `MOA_REQUIRE_RESTATE_REGISTRATION_FOR_READINESS` |
| **计费/预算** | `BudgetConfig` + `tenant_cost_since` 聚合;tool budget 限制;`BudgetExhausted` 错误;`MOA_PERSIST_TURN_METRICS` |
| **SSO** | Auth0 OIDC + SCIM v2 + OIDC 通用;Auth0 Token Vault 第三方 OAuth;CIBA 异步审批;JWKS cache + kid miss refresh;webhook 验证(secret scanning / connection-linked / 4 知识库回调) |
| **合规** | DSAR export 签名包;Erasure (`moa-memory-pii`);Merkle 链 lineage audit;显式 attestation caveat; opt-in compliance tier |
| **数据隔离** | storage_partition_id RLS;Neon 分支(checkpoint);pgaudit DDL/role/write;session attachment 走对象存储 + 显式 tenant key |
| **PII** | `moa-memory-pii` openai/privacy-filter HTTP;contact point 哈希(`MOA_CONTACT_POINT_HASH_KEY_HEX` 32字节);`erasure` 实现 right to forget |
| **Action 治理** | 风险等级 + ActionClass + 审批流;`admin_review` 持久化 + 不阻塞 turn;`ActionReviews/decide` Restate 服务 |
| **注入防护** | per-turn canary + 9 类启发式 + tool input screening + output wrapping + `<untrusted_tool_output>` 边界 + active/turn canary cache |
| **多 region/HA** | 6 replica orchestrator + 3 replica edge;HPA + PDB;NetworkPolicy;非 sticky 路由;StatefulSet 不可用, Deployment 即可 |
| **可观测** | Prometheus + OTLP + Grafana + OTel/OpenInference lineage bridge;tokio-metrics (unstable);turn latency 6 步追踪;ocsf-audit 验证 endpoint |
| **API 治理** | axum 路由 + clap 配置;统一 error taxonomy (`MoaError`);tool failure classification (Retryable/ReProvision/Fatal);scoped caching;MCP server (rmcp) |
| **扩展性** | 30+ 内部 Restate services/workflows/objects;6 业务边界 (Runtime/Brain/Execution/Data + Auth/Audit/Lineage);federation via 共享 session storage |

---

## 8. 工程规范与质量 (Engineering Discipline)

- **Rust 规范**: 100% doc comments、`thiserror` (lib) + `anyhow` (bin)、`tracing` (no println/eprintln)、`tokio` 异步、禁用 `unwrap()` in lib code、可选依赖走 feature flag
- **ID**: UUIDv7 newtype
- **Time**: `chrono::DateTime<Utc>`
- **Config**: TOML via `config` crate + `envy` env overlay
- **Error**: 每 lib crate 一个 `Error` enum,`MoaError` 跨 crate
- **测试组织**: lane suffix (`_offline`/`_db`/`_db_memory`/`_service_e2e`/`_provider_e2e`/`_eval`/`_live`/`_docker`)
- **SAFETY 注释**: handler 缺鉴权时必须 `// SAFETY: ...` 注释
- **Lints**: clippy `-D warnings`,`unexpected_cfgs` 警告
- **Audit 受保护**: docs `14-architecture-policy.md`,`docs/08-security.md`, `docs/auth/README.md` 作为权威 ADR

---

## 9. 关键文档清单 (Documentation Map)

| 文档 | 主题 |
|---|---|
| `README.md`, `ARCHITECTURE.md`, `AGENTS.md` | 总览 + Agent 工作流 |
| `docs/00-direction.md` | 产品方向 |
| `docs/01-architecture-overview.md` | 31KB 系统模型 + trait map |
| `docs/02-brain-orchestration.md` | Restate 编排 + brain 循环 |
| `docs/03-communication-layer.md` | 消息/通讯/审批 |
| `docs/04-memory-architecture.md` | 图谱 memory + 隐私 + 检索 |
| `docs/05-session-event-log.md` | 事件模式 + 压缩 |
| `docs/06-hands-and-mcp.md` | Hand provider + MCP |
| `docs/07-context-pipeline.md` | 上下文编译 + 缓存 |
| `docs/08-security.md` | 凭证 + 沙箱 + 注入防护 |
| `docs/09-skills-and-learning.md` | Agent Skills |
| `docs/10-technology-stack.md` | crate + 构建 + 部署 |
| `docs/11-event-replay-runbook.md` | 事件回放 |
| `docs/12-restate-architecture.md` | 持久化编排 |
| `docs/13-task-segmentation.md` | 任务段 |
| `docs/14-multi-tenancy-and-learning.md` | 多租户 + 学习 |
| `docs/15-architecture-policy.md` | 架构策略 |
| `docs/16-evaluation.md` | 评估 |
| `docs/17-observability.md` | 观测 |
| `docs/18-performance.md` | 性能 |
| `docs/19-data-operations.md` | 数据操作 |
| `docs/20-testing.md` | 测试 |
| `docs/21-tenant-knowledge-base.md` | 租户知识库 |
| `docs/22-load-and-chaos-testing.md` | 负载 + chaos |
| `docs/23-environment-variables.md` | 49KB 环境变量 |
| `docs/auth/README.md` + `scim.md` | 认证架构 + SCIM |
| `docs/operations/*.md` | 14 个运维 runbook |
| `docs/eval/*.md` | 评估基准 |
| `SEQUENCE-DIAGRAMS.md` | 序列图(23KB) |
| `CHANGELOG.md` | 变更日志 |
| `docs/schemas/{moa-artifact-v1,moa-procedure-v1,moa-skill-v1}.schema.json` | JSON Schemas |
| `docs/schemas/clickhouse-analytics.md` | OLAP schema |
| `docs/product/behavior-lab.md` | Behavior Lab 产品 |
| `docs/implementation-caveats.md` | 实现注意 |
| `docs/prompt-caching-architecture.md` | 提示缓存架构 |
| `docs/analytics.md` | 分析摘要 |

---

## 10. 总结 (Summary)

MOA 是一个 **成熟、企业级、多租户** 的 AI Agent Operations 平台:

- **架构清晰**: 4 边界(Runtime/Brain/Execution/Data) + 6 业务领域(Auth/Audit/Lineage/Memory/Skills/Communication)
- **代码规模大**: 1216 Rust 文件,45 crates,完整覆盖 LLM 推理/记忆/技能/工具/审批/审计/可观测
- **商业级完备**: 多租户隔离(RLS + OpenFGA),审计(OCSF + S3 Object Lock),SSO(Auth0 + SCIM v2),合规(DSAR/erasure/Merkle),SLA(Restate 持久化 + 6 步延迟追踪),计费(token 成本聚合 + tool budget),可观测(Prometheus/OTel/OpenInference)
- **provider 无关**: 5 个 LLM/embedding 提供方 + 4 个 sandbox 提供方 + 4 个知识库 provider
- **可扩展**: 30+ Restate services/workflows/objects,handler 鉴权契约统一,事务性 outbox 保 tuple 一致
- **生产就绪**: K8s manifests (HPA/PDB/NetworkPolicy),docker-compose dev stack,Python audit-shipper/PII service 配套
- **工程纪律**: 完整 lane 测试组织(offline/db/db_memory/live/docker),byte-stable prompt cache,SCIM Okta 合规,eval harness 5 类 metrics

适合作为 **企业 AI Agent 平台基线** 或 **多租户 SaaS Agent 后端** 来参考实现。
