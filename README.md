# MoA Gateway Pro

> **v2.0** — 商业级/工业级多模型协作API网关
> 141个API端点 · 236个测试用例 · Go高性能代理 · PostgreSQL双后端 · MCP网关 · SOC2合规

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
- **6 种执行策略** — `parallel` / `compose` / `judge` / `chain` / `pipeline` / `single`
- **7 个内置预设** — `fast` / `balanced` / `quality` / `moa-balanced` / `moa-quality` / `chinese_battalion` / `pipeline`
- **多模型投票** — `vote_ensemble` / `should_rebalance` / `detect_convergent` / `arbitrate_conflicts`

### MCP网关 (v2.0新增)
- **MCP Server** — JSON-RPC 2.0协议,工具注册/发现/调用
- **MCP Client** — 连接外部MCP Server发现工具
- **工具级RBAC** — admin/operator/user/readonly按角色过滤工具
- **Tool Guardrails** — Pre/Post调用防护(危险模式检测)
- **3个内置工具** — `moa_list_models` / `moa_check_quota` / `moa_route_preview`

### 语义缓存 (v2.0新增)
- **L1 精确匹配** — MD5 hash,LRU淘汰,10K条目
- **L2 语义缓存** — N-gram向量 + 余弦相似度 ≥0.95
- **L3 Redis分布式** — 多实例共享,优雅降级
- **防护** — 空值缓存(防穿透) + TTL随机偏移(防雪崩)

### RBAC权限体系 (v2.0新增)
- **4级角色** — admin / operator / user / readonly
- **15项权限** — call/chat, call/moa, read/models, write/keys, admin/rbac ...
- **审计日志** — 结构化JSON,PII自动脱敏,HMAC签名链
- **用户管理API** — CRUD + 角色分配

### SOC2合规 (v2.0新增)
- **AES-256-GCM加密** — 字段级静态数据加密
- **PII检测** — 9种模式(email/手机/信用卡/SSN/身份证/IP/API Key/JWT)
- **GDPR** — 数据删除(被遗忘权) + 数据导出
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
- **分布式追踪** — 每请求trace_id + span链
- **14+ Prometheus指标** — LLM延迟/Token用量/成本/缓存命中/熔断器/限流
- **结构化日志** — JSON格式,trace_id关联
- **Grafana Dashboard** — 12面板JSON模板
- **告警规则** — 10条Prometheus告警(高延迟/高错误率/Provider不可用)

### 路由 + 质量
- **智能路由** — 按查询复杂度自动分配 fast / balanced / quality
- **Elo 排名** — `rank_elo` 自动评估模型质量
- **L0 质量门** — `gate_l0` 拦截低质响应

### 工具集成
- **76 个 capability passthrough** — `secret_scan` / `fuzzy_dedup` / `anthropic_compat` ...
- **MCP 协议** — JSON-RPC 2.0 Server/Client
- **WebUI** — 静态文件托管,内置管理控制台

## 架构 (v2.0)

```
┌──────────────────────────────────────────────────────────────┐
│              Go Proxy Layer (proxy/)                         │
│  JWT快速验证 · SSE流转发 · 令牌桶限流 · Prometheus指标       │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│              FastAPI 141 routes (server.py 287行)            │
│  /v1/chat/completions  /v1/moa/*  /v1/mcp/*  /v1/agent/*    │
│  + /v1/capability/* (76) + /api/admin/* + /api/auth/*       │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  routes/ (12模块) · rbac.py · audit.py · _helpers.py        │
│  health · metrics · mcp · chat · moa · auth · admin ·       │
│  capability · models · agent · webui · compliance           │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  mcp/ · cache/ · observability/ · compliance/ · ha/         │
│  MCP Server/Client · 三层缓存 · OTel三支柱 · SOC2 · 熔断器  │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│  database.py (SQLite/PostgreSQL双后端) · storage.py         │
│  连接池 · Alembic迁移 · 16模型端点 · async health check     │
└──────────────────────────────────────────────────────────────┘
```

## 测试

```powershell
# 236个测试用例 (100%通过)
.venv\Scripts\python -m pytest tests/ -v --tb=short

# 测试覆盖:
# test_core_endpoints.py  27个 — 核心API端点集成
# test_security_fixes.py  11个 — 安全修复验证
# test_rbac.py            22个 — RBAC权限矩阵
# test_mcp.py             31个 — MCP协议
# test_cache.py           25个 — 三层缓存
# test_observability.py   27个 — OTel可观测性
# test_compliance.py      33个 — SOC2合规
# test_ha.py              35个 — 高可用架构
# test_boundary.py        14个 — 边界条件
# test_quality_fixes.py   11个 — 代码质量

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
docker build -t moa-gateway-pro:v2.0 .
docker run -p 8088:8088 \
  -e MOA_ADMIN_PASSWORD=YourPassword \
  -e MOA_JWT_SECRET=your-secret-key-minimum-32-characters-long! \
  moa-gateway-pro:v2.0
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

`config.yaml` (默认) + 环境变量 override:

### 核心配置
- `MOA_ADMIN_PASSWORD` — WebUI admin 密码
- `MOA_JWT_SECRET` — JWT签名密钥(≥32字符)
- `MOA_DATA_DIR` — SQLite / log 目录
- `MOA_LOG_LEVEL` — DEBUG / INFO / WARNING / ERROR

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
| OpenAI 兼容 | 2 | `/v1/chat/completions`, `/v1/models` |
| 原生 MoA | 13 | `/v1/moa/execute`, `/v1/moa/eval`, `/v1/moa/presets` ... |
| MCP网关 | 6 | `/v1/mcp`, `/v1/mcp/tools`, `/v1/mcp/servers` |
| 路由/配额 | 2 | `/v1/route/preview`, `/v1/quota` |
| Agent/Workflow | 6 | `/v1/agent/list`, `/v1/agent/dispatch` ... |
| Capability | 15 | `/v1/capability/secret-scan`, `/v1/capability/ensemble-vote` ... |
| Admin/Auth | 19 | `/api/auth/login`, `/api/admin/users`, `/api/admin/roles` ... |
| 合规 | 10 | `/api/admin/compliance/baseline`, `/api/admin/compliance/gdpr/*` |
| 健康/指标 | 7 | `/health`, `/health/live`, `/health/ready`, `/metrics` |
| WebUI | 1 | `/` (静态文件) |
| **合计** | **141** | |

## 项目结构

```
moa-gateway-pro/
├── proxy/              # Go高性能代理层(13个文件)
├── moa_gateway/
│   ├── server.py       # FastAPI入口(287行)
│   ├── routes/         # 12个路由模块
│   ├── mcp/            # MCP协议(7个模块)
│   ├── cache/          # 三层语义缓存(7个模块)
│   ├── observability/  # OpenTelemetry(8个模块)
│   ├── compliance/     # SOC2合规(8个模块)
│   ├── ha/             # 高可用(5个模块)
│   ├── rbac.py         # RBAC权限(4角色/15权限)
│   ├── audit.py        # 审计日志(PII脱敏)
│   ├── database.py     # SQLite/PostgreSQL双引擎
│   └── ...             # 其他核心模块
├── tests/              # 236个测试用例
├── benchmarks/         # 压测框架
├── deploy/
│   ├── ha/             # Docker HA + K8s Helm
│   ├── monitoring/     # Grafana + Prometheus告警
│   └── database/       # PostgreSQL部署
└── 参考/analysis/      # 11个架构分析文档
```

## 依赖

- Python 3.11+
- FastAPI / Pydantic v2 / Uvicorn
- SQLite (开发) / PostgreSQL (生产)
- Redis (可选,分布式缓存)
- Go 1.22+ (可选,高性能代理)
- bcrypt / jose (JWT) / cryptography (AES-256)
- opentelemetry-sdk / prometheus-client

## License

MIT

## 版本

| Version | Date | 关键特性 |
|---|---|---|
| **v2.0** | 2026-07-22 | 商业级升级: Go代理 + PostgreSQL + RBAC + MCP + 语义缓存 + OTel + SOC2 + HA |
| v1.8.1 | 2026-07-19 | Pydantic Field 描述 + 端点签名清理 |
| v1.8.0 | 2026-07-18 | 83 端点 Pydantic 化 + 90 OpenAPI schemas |
| v1.7.5 | 2026-07-18 | Final release + 7193 RPS |
| v1.7.0 | 2026-07-18 | Service Layer + AgentDispatch + Workflow |

完整变更见 [CHANGELOG.md](CHANGELOG.md)
