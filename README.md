# MoA Gateway Pro

> **v3.2.1** — 独立加固审计版（三路深审 + 生产加固 + 红蓝对抗复审）
> 243个API端点 · **1170个测试实测全绿** · Go高性能代理（16个Go测试函数全绿 + CI Go job + 真实后端活体smoke） · PostgreSQL双后端 · MCP网关 · 自主编排引擎

工业级 AI 网关:路由、MoA 协作、共识、质量评估、配额、可观测性、知识库、安全防护、MCP协议、语义缓存、高可用 —— 一个 FastAPI 进程 + Go代理层搞定。

## 一分钟上手

```powershell
# 安装依赖(已有 venv 可跳过)
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 启动
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "YourStrongPassword#2024"
$env:MOA_JWT_SECRET = "your-secret-key-minimum-32-characters-long!"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 127.0.0.1 --port 8088

# 打开 Swagger UI
# http://127.0.0.1:8088/docs
```

任何 OpenAI 客户端都能直接接:

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:8088/v1", api_key="mgw-...")
resp = client.chat.completions.create(model="auto", messages=[{"role":"user","content":"hi"}])
```

## v3.2.1: 独立加固审计（真实漏洞修复, 非合规润色）

在三路独立深审（安全+诚实性 / 架构 / 部署工程）基础上, 按"生产加固线 / 行为变更仅文档警示 / 装 Go 不装 Docker"的决策边界完成加固。合并 v3.2.0 自主编排引擎（orchestrator 8 模块, 能力注册表经执行验证）。关键修复:

- **SSRF 编码 IP 平台无关修复** — Windows 解析器不归一化十进制/十六进制 IP, 污染 DNS 下 `http://2130706433/` 可被放行（项目自带测试在本机实测失败复现）。现于 DNS 之前以 inet_aton 语义归一化判定, 畸形 fail-closed; 6to4/Teredo 过渡段与 trailing-dot FQDN 一并封堵; 47 项确定性回归测试
- **orchestrator 授权一致性** — readonly key 此前可经"技能名提及"路径触达 code_execute 等危险技能; 现 planner 过滤（计划内诚实披露 filtered_privileged_skills, 含自定义技能）+ executor 纵深防御 + MCP 按真实角色检查, 与 agent 信任模型对齐; 18 项回归
- **skill 热部署重校验** — load_persisted 重放语法+安全静态校验（功能校验由每次调用的运行时沙箱承担）, builtin 名保护 + 参数名标识符校验 + 重名去重
- **Go 代理实锤缺陷修复** — extractClientIP 未定义（仓库此前无法编译）; OpenAI 风格 `Bearer mgw-` 网关 key 被当 JWT 误拒（活体 smoke 发现, 数据面曾全断）; 伪造 X-Forwarded-For 边缘丢弃; BACKEND_URLS 多后端轮询（env 未设行为不变）; 新增 16 个 Go 测试函数 + CI Go job（此前 0）
- **诚实性** — channels 三通道（Subagent/CLI/API）合成输出全链路 mock 标注; chat 失败路径 5xx 真实记账（错误率告警现可触发）; web_search docstring 与实现对齐
- **部署产物可部署化** — Dockerfile.backend 移除 COPY data/ + workers=1; HA compose 删伪副本/修 Prometheus 挂载/Grafana 真实 provisioning; Helm 补 Secret 模板 + emptyDir（此前必然部署失败）; sqlalchemy>=2.0.36; pip-audit 真拦截
- **生产警示（W1-W6）** — /metrics/docs 暴露、配额覆盖现状、RBAC 现状、admin JWT 兼 key、SSRF TOCTOU 残余窗口、错误信封并存 → [docs/SECURITY-HARDENING-GUIDE.md](docs/SECURITY-HARDENING-GUIDE.md)

完整清单见 [RELEASE_NOTES_v3.2.1.md](RELEASE_NOTES_v3.2.1.md) 与 [CHANGELOG.md](CHANGELOG.md)。

## v3.2: 自主编排引擎 + 43 能力注册表（合并自 v3.2.0, 含 v3.2.1 授权加固）

## v3.1: 十轮全量审计（P0/P1 清零）

v3.1.0/v3.1.1 连续执行十轮全量审计:243 端点扫描、6 路并行代码深审、本地真实 LLM 链路注入、浏览器 E2E、chaos 故障注入、双AI对抗复审。全部确认缺陷已修复并活体复验。

| 项目 | 结果 |
|------|------|
| **单元测试** | 1071 passed, 0 failed（v3.1 历史基线；当前版本实测 1170，见上文 v3.2.1） |
| **活体探针** | 一轮 41/41 + 二轮 4/4（沙箱/SSRF/越权/mock标注/配额/GDPR/真实链路） |
| **前端** | `next build` 通过，tsc 0 错；E2E 硬刷新会话保持 6/6 |
| **对抗复审** | 一轮 19 项全部证实；二轮新发现 2 P1 + 4 P2 全部修复 |

关键修复:

- **P0 Agent 沙箱逃逸 RCE** — 三层加固:AST 封禁全 dunder 访问与含 dunder 字符串字面量、导入白名单移除 `operator`/`string`、运行时 `_ModuleProxy` 拦截动态 dunder；危险工具仅 admin/operator 可用
- **SSRF 统一强化** — DNS 全解析、编码 IP、IPv4-mapped IPv6、fail-closed；显式拉黑 IANA 全部特殊段含 RFC 6598 CGNAT 100.64.0.0/10（云元数据地址所在段）
- **GDPR 被遗忘权真实生效** — 真删用户、按 key_id 匿名化日志、加盐 HMAC-SHA256 不可彩虹表还原
- **D6 显式 mock 政策闭环** — 无真实 key 时合成结果必带 `X-MOA-Mock` 头 + `mock:true` 字段；缓存命中重放仍带标注；MoA 全失败显式 502 不冒充成功
- **服务层死方法清零** — 60+ 处 ImportError/签名错配全部改走真实实现
- **请求模型类型化** — 85 个请求模型 `extra=forbid`（未知字段 422）

完整清单见 [RELEASE_NOTES_v3.1.1.md](RELEASE_NOTES_v3.1.1.md)、[RELEASE_NOTES_v3.1.0.md](RELEASE_NOTES_v3.1.0.md) 与 [CHANGELOG.md](CHANGELOG.md)。

## v2.0 核心升级

| 维度 | v1.8.1 | v2.0 | 提升 |
|------|--------|------|------|
| **架构** | 单体server.py 5000行 | 11个路由模块 + Go代理层 | 模块化 + 微秒级延迟 |
| **数据库** | SQLite only | SQLite + PostgreSQL双后端 | 高并发写入支持 |
| **权限** | 2级(admin/user) | 4级RBAC + 15权限 + 审计日志 | 企业级权限控制 |
| **缓存** | 无 | 三层语义缓存(精确+语义+Redis) | 降本20-40% |
| **可观测** | 基础日志 | OpenTelemetry Trace/Metrics/Logs | Grafana + 告警 |
| **合规** | 无 | SOC2: AES-256加密 + PII脱敏 + GDPR | 企业合规就绪 |
| **高可用** | 单实例 | 熔断器 + 故障转移 + K8s Helm | 99.99% SLA |
| **MCP** | 基础 | 完整JSON-RPC Server/Client + 工具RBAC | 对标TrueFoundry |
| **测试** | 0 | 236个(100%通过) | 商业级覆盖 |
| **性能** | 7193 RPS(health) | 636 RPS(health,含全中间件) | 安全+可观测开销内 |

## 核心能力

### 多模型协作 (MoA)
- **3-layer / N-layer MoA** — 多模型并行提议 + 旗舰模型聚合
- **10+ 执行策略** — `parallel` / `compose` / `judge` / `chain` / `pipeline` / `layered` / `single_proposer` / `ranker` / `single`
- **13 个内置预设** — `fast` / `balanced` / `quality` / `chinese_battalion` / `tri_model_review` / `pipeline` ...
- **多模型投票** — `vote_ensemble` / `should_rebalance` / `detect_convergent` / `arbitrate_conflicts`
- **全链路 provider 追踪** — 每个参考/聚合结果带真实 provider 标识与 mock 标注

### Agent 与 Workflow (v3.x)
- **Agent Loop** — ReAct / Plan-Execute 双循环，真实 LLM 调用与工具执行
- **沙箱隔离** — AST 静态净化 + 独立子进程执行 + 模块代理，防逃逸 RCE
- **Runs/TaskBoard** — 异步 run 持久化、超时与并发防重、任务 CRUD 与指派
- **Workflow 引擎** — YAML DAG 工作流，步骤间真实数据流转，内部回调自动鉴权
- **9 个 MCP 工具** — `moa_list_models` / `moa_check_quota` / `moa_route_preview` / `discover_free_models` / `list_free_models` / `apply_prompt_template` / `apply_param_template` / `run_agent_loop` / `search_web`

### 多模态与模型生态 (v3.x)
- **22 个开箱模型端点** — DeepSeek / GLM / Kimi / Qwen / 豆包 / GPT / Claude / Mistral 等
- **多模态生成** — 图像 / 视频 / 音频 / 3D 任务提交与轮询
- **免费模型发现** — 30+ 平台 Discovery 引擎，每日刷新、自动注册
- **显式 mock 标注** — 无 key 时合成结果处处标注，配置真实 key 后自动切换

### MCP网关 (v2.0新增)
- **MCP Server** — JSON-RPC 2.0协议,工具注册/发现/调用
- **MCP Client** — 连接外部MCP Server真实发现工具（stdio 诚实标注 unsupported）
- **工具级RBAC** — admin/operator/user/readonly按角色过滤工具
- **Tool Guardrails** — Pre/Post调用防护(危险模式检测)

### 语义缓存 (v2.0新增)
- **L1 精确匹配** — MD5 hash,LRU淘汰,10K条目
- **L2 语义缓存** — N-gram向量 + 余弦相似度 ≥0.95，按 model/strategy/preset scope 隔离
- **L3 Redis分布式** — 多实例共享,优雅降级
- **防护** — 空值缓存(防穿透) + TTL随机偏移(防雪崩) + mock 信封重放

### RBAC权限体系 (v2.0新增)
- **4级角色** — admin / operator / user / readonly
- **15项权限** — call/chat, call/moa, read/models, write/keys, admin/rbac ...
- **审计日志** — 结构化JSON,PII自动脱敏,HMAC签名链
- **用户管理API** — CRUD + 角色分配

### SOC2合规 (v2.0新增)
- **AES-256-GCM加密** — 字段级静态数据加密
- **PII检测** — 9种模式(email/手机/信用卡/SSN/身份证/IP/API Key/JWT)
- **GDPR** — 被遗忘权真删 + 加盐 HMAC 匿名化 + 数据导出
- **密钥轮换** — 双密钥过渡期,90天自动提醒
- **安全基线检查** — 10项配置检查(jwt_secret/encryption/debug/cors/tls...)
- **数据保留策略** — 自动清理过期数据

### 高可用架构 (v2.0新增)
- **熔断器** — CLOSED/OPEN/HALF_OPEN状态机
- **智能重试** — 指数退避 + 抖动
- **Provider故障转移** — 优先级排序 + 自动切换
- **优雅关停** — 请求排空 + 超时强制退出
- **深度健康检查** — liveness / readiness / startup 三探针
- **Docker Compose HA** — 多实例 + PostgreSQL + Redis + Prometheus + Grafana
- **K8s Helm Chart** — Deployment / Service / HPA / PDB

### Go高性能代理层 (v2.0新增)
- **微秒级延迟** — httputil.ReverseProxy零拷贝转发
- **JWT快速验证** — Go层完成签名验证,不转发到Python
- **SSE流转发** — 零缓冲实时流
- **令牌桶限流** — 每IP独立桶,过期自动清理
- **Prometheus指标** — 请求数/延迟/状态码

### OpenTelemetry可观测性 (v2.0新增)
- **分布式追踪** — 每请求trace_id + span链，model_pool/workflow/moa/assistant 子 span 全接线
- **14+ Prometheus指标** — LLM延迟/Token用量/成本/缓存命中/熔断器/限流，真实调用点记账
- **结构化日志** — JSON格式,trace_id关联
- **Grafana Dashboard** — 12面板JSON模板
- **告警规则** — 10条Prometheus告警(高延迟/高错误率/Provider不可用)

### 路由 + 质量
- **智能路由** — 按查询复杂度自动分配 fast / balanced / quality
- **Elo 排名** — `rank_elo` 自动评估模型质量
- **L0 质量门** — `gate_l0` 拦截低质响应
- **自愈调度** — `self_heal` 按健康状态 promote/demote 端点

### 工具集成
- **77 个 capability 端点** — `secret_scan` / `fuzzy_dedup` / `anthropic_compat` / `rerank` / `embedding` ...
- **admin-ui** — Next.js 14 管理控制台（登录/仪表盘/模型/能力/配额管理）
- **WebUI** — 静态文件托管,内置管理控制台

## 架构 (v3.1)

```
┌──────────────────────────────────────────────────────────────┐
│              Go Proxy Layer (proxy/, 10个Go文件)             │
│  JWT快速验证 · SSE流转发 · 令牌桶限流 · Prometheus指标       │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│          FastAPI 243 API routes (server.py 738行)            │
│  /v1/chat/completions  /v1/moa/*  /v1/mcp/*  /v1/agent/*    │
│  + /v1/capability/* (77) + /api/admin/* + /api/auth/*       │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  routes/ (26模块) · rbac.py · audit.py · _helpers.py        │
│  health · metrics · mcp · chat · moa · auth · admin ·       │
│  capability · models · agent · workflow · webui · compliance│
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  mcp/ · cache/ · observability/ · compliance/ · ha/         │
│  agent_loop/ (沙箱) · capability/ (72模块) · services/      │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  database.py (SQLite/PostgreSQL双后端) · storage.py         │
│  连接池 · Alembic迁移 · 22模型端点 · async health check     │
└──────────────────────────────────────────────────────────────┘
```

## 测试

```powershell
# 1170个测试用例 (v3.2.1 实测全绿)
.venv\Scripts\python -m pytest tests/ -v --tb=short

# 覆盖分组(节选):
# test_core_endpoints.py      — 核心API端点集成
# test_security_fixes.py      — 安全修复验证
# test_sandbox_escape.py      — Agent沙箱逃逸对抗(v3.1.1新增)
# test_v311_fixes.py          — v3.1.1 P0/P1修复回归(新增)
# test_v311_round2.py         — 对抗复审二轮修复回归(新增)
# test_service_methods_real.py— 服务层真实接线验证(新增)
# test_rbac.py / test_mcp.py / test_cache.py / test_compliance.py / test_ha.py ...

# 性能基准
.venv\Scripts\python -m benchmarks.run_benchmark --concurrency 10 --duration 10
```

## 性能基准 (v2.0实测)

| 场景 | RPS | P50 | P95 | P99 | 成功率 |
|------|-----|-----|-----|-----|--------|
| /health | 636 | 12.7ms | 30.7ms | 57.3ms | 100% |
| /v1/models | 210 | 44.5ms | 62.4ms | 81.0ms | 100% |
| /api/auth/login | 190 | 46.8ms | 68.3ms | 102ms | 100% |
| /api/admin/stats | 605 | 14.9ms | 26.9ms | 38.8ms | 100% |

> 13,835次基准请求,0失败。bcrypt登录P50=47ms符合预期(bcrypt rounds=12)。

## 部署

### Docker (单实例)

```bash
docker build -t moa-gateway-pro:v3.1.1 .
docker run -p 8088:8088 \
  -e MOA_ADMIN_PASSWORD=YourPassword \
  -e MOA_JWT_SECRET=your-secret-key-minimum-32-characters-long! \
  moa-gateway-pro:v3.1.1
```

### Docker Compose HA (生产级)

```bash
cd deploy/ha
# 配置 .env (DB_PASSWORD, MOA_JWT_SECRET, MOA_ADMIN_PASSWORD)
docker-compose -f docker-compose.ha.yml up -d
# 启动: 3个后端 + 2个Go代理 + PostgreSQL + Redis + Prometheus + Grafana
```

### Go代理层 (高性能前端)

```bash
cd proxy
go build -o moa-proxy .
./moa-proxy --listen :8080 --backend http://127.0.0.1:8088
```

### K8s Helm

```bash
cd deploy/ha/helm
helm install moa-gateway . -f values.yaml
```

### 直接跑

```powershell
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "YourStrongPassword"
$env:MOA_JWT_SECRET = "your-secret-key-minimum-32-characters-long!"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8088 --workers 4
```

### PostgreSQL (生产数据库)

```bash
export DATABASE_URL="postgresql+psycopg2://moa:password@localhost:5432/moa_gateway"
export DB_POOL_SIZE=20
export DB_MAX_OVERFLOW=10
alembic upgrade head  # 首次迁移
```

## 配置

`config.yaml` (默认) + 环境变量 override。config.yaml 不携带任何密钥,全部走环境变量:

### 核心配置
- `MOA_ADMIN_PASSWORD` — WebUI admin 密码
- `MOA_JWT_SECRET` — JWT签名密钥(≥32字符)
- `MOA_GATEWAY_KEY` — 网关 API Key
- `MOA_DATA_DIR` — SQLite / log 目录
- `MOA_LOG_LEVEL` — DEBUG / INFO / WARNING / ERROR

### 模型 Provider Keys
- `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `MOONSHOT_API_KEY` / `QWEN_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `MISTRAL_API_KEY` ... — 按 config.yaml 中各模型 `api_key_env` 配置;未配置的 provider 自动降级为显式标注的 MockProvider

### 数据库
- `DATABASE_URL` — PostgreSQL连接URL(不设则用SQLite)
- `DB_POOL_SIZE` — 连接池大小(默认20)
- `DB_MAX_OVERFLOW` — 连接池溢出(默认10)

### 缓存
- `REDIS_URL` — Redis连接URL(不设则仅用本地缓存)

### 合规
- `MOA_ENCRYPTION_KEY` — AES-256加密密钥
- `MOA_AUDIT_SIGNING_KEY` — 审计日志签名密钥
- `MOA_KEY_ROTATION_DAYS` — 密钥轮换周期(默认90天)

## 端点分类

| 类别 | 数量 | 示例 |
|---|---|---|
| Capability | 77 | `/v1/capability/secret-scan`, `/v1/capability/rerank` ... |
| v1 其他(对话/模型/路由/配额/多模态/3D/发现) | 65 | `/v1/chat/completions`, `/v1/models`, `/v1/route/preview` ... |
| Admin | 34 | `/api/admin/users`, `/api/admin/stats`, `/api/admin/compliance/*` ... |
| API 其他(审计/基准/优化/可观测) | 15 | `/api/audit/*`, `/api/benchmark/*` ... |
| 原生 MoA | 12 | `/v1/moa/execute`, `/v1/moa/eval`, `/v1/moa/presets` ... |
| MCP网关 | 12 | `/v1/mcp`, `/v1/mcp/tools`, `/v1/mcp/servers` ... |
| Agent | 12 | `/v1/agent/list`, `/v1/agent/dispatch`, `/v1/agent/tasks` ... |
| Workflow | 5 | `/v1/workflow/run`, `/v1/workflow/register` ... |
| 健康/指标 | 4 | `/health`, `/health/live`, `/health/ready`, `/metrics` |
| Auth | 3 | `/api/auth/login`, `/api/auth/logout` ... |
| WebUI/静态 | 4 | `/` (静态文件) |
| **API 合计** | **243** | (另有 /docs /redoc /openapi.json) |

## 项目结构

```
moa-gateway-pro/
├── proxy/              # Go高性能代理层(10个Go文件)
├── admin-ui/           # Next.js 14 管理控制台
├── moa_gateway/
│   ├── server.py       # FastAPI入口(738行)
│   ├── routes/         # 26个路由模块
│   ├── capability/     # 72个能力模块
│   ├── agent_loop/     # Agent循环 + 沙箱隔离执行
│   ├── services/       # 10个服务层(routing/quota/quality/moa/...)
│   ├── mcp/            # MCP协议(8个模块)
│   ├── cache/          # 三层语义缓存(7个模块)
│   ├── observability/  # OpenTelemetry(9个模块)
│   ├── compliance/     # SOC2合规(8个模块)
│   ├── ha/             # 高可用(5个模块)
│   ├── workflows/      # YAML工作流 + 内置工作流
│   ├── providers/      # Provider适配(OpenAI兼容/Anthropic/Mock)
│   ├── rbac.py         # RBAC权限(4角色/15权限)
│   ├── audit.py        # 审计日志(PII脱敏)
│   ├── database.py     # SQLite/PostgreSQL双引擎
│   └── ...
├── tests/              # 1170个测试用例
├── benchmarks/         # 压测框架
├── perf/               # E2E/chaos/压测脚本
├── scripts/            # 打包/审计探针/冒烟脚本
└── deploy/
    ├── ha/             # Docker HA + K8s Helm
    ├── monitoring/     # Grafana + Prometheus告警
    └── database/       # PostgreSQL部署
```

## 依赖

- Python 3.10+（实测 3.11）
- FastAPI / Pydantic v2 / Uvicorn
- SQLite (开发) / PostgreSQL (生产)
- Redis (可选,分布式缓存)
- Go 1.22+ (可选,高性能代理)
- bcrypt / jose (JWT) / cryptography (AES-256)
- opentelemetry-sdk / prometheus-client

## 诚实性政策（零虚假）

- 无真实 provider key 时，多模态/搜索/重排等能力返回**显式标注**的合成结果（`X-MOA-Mock: true` 头 / `mock:true` 字段 / `[Mock]` 前缀），绝不冒充真实模型输出;配置真实 key 后优先真实 provider。
- 缓存命中重放保留 mock 标注;MoA 参考模型全失败返回显式 502 + 逐模型失败证据，不静默降级。
- 外部 MCP stdio 传输在本部署形态诚实返回 unsupported。
- 发布包与仓库经密钥扫描:零真实密钥,config.yaml 全部留空走环境变量。

## License

MIT

## 版本

| Version | Date | 关键特性 |
|---|---|---|
| **v3.2.1** | 2026-08-29 | 独立加固审计+红蓝对抗复审: SSRF平台无关修复 + orchestrator授权一致性 + skill参数名注入封堵 + Go代理修复/测试/CI + 部署产物可部署化; 1170测试全绿 |
| v3.1.1 | 2026-08-16 | 十轮全量审计修复: P0沙箱RCE封堵 + SSRF/GDPR/mock标注闭环; 1071测试全绿（历史基线） |
| v3.1.0 | 2026-08-14 | 十轮全量测试 + 双AI对抗评审: 29+21项缺陷修复, 全链路真实化 |
| v2.1.0 | 2026-08-06 | Wave B1–B5 全链路真实化: HMAC签名链/Mock显式化/Agent计量/Tracer接线 |
| v2.0 | 2026-08-03 | 商业级升级: Go代理 + PostgreSQL + RBAC + MCP + 语义缓存 + OTel + SOC2 + HA |
| v1.8.1 | 2026-07-19 | Pydantic Field 描述 + 端点签名清理 |
| v1.8.0 | 2026-07-18 | 83 端点 Pydantic 化 + 90 OpenAPI schemas |
| v1.7.5 | 2026-07-18 | Final release + 7193 RPS |
| v1.7.0 | 2026-07-18 | Service Layer + AgentDispatch + Workflow |

完整变更见 [CHANGELOG.md](CHANGELOG.md)
