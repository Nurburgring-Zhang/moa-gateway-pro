# MoA Gateway Pro

> **v1.8.1** — 工业级多模型协作网关 / OpenAI 兼容 API 网关
> 7 月 19 日发布 · 11 个 service / 176 个 method / 91 个 OpenAPI schema / 7 个内置工作流

工业级 AI 网关:路由、MoA 协作、共识、质量评估、配额、可观测性、知识库、安全防护 —— 一个 FastAPI 进程搞定。

## 一分钟上手

```powershell
# 安装依赖(已有 venv 可跳过)
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt

# 启动
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "TestPass#2024"
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

## 核心能力

### 多模型协作 (MoA)
- **3-layer / N-layer MoA** — 多模型并行提议 + 旗舰模型聚合
- **6 种执行策略** — `parallel` / `compose` / `judge` / `chain` / `pipeline` / `single`
- **7 个内置预设** — `fast` / `balanced` / `quality` / `moa-balanced` / `moa-quality` / `chinese_battalion` / `pipeline`
- **多模型投票** — `vote_ensemble` / `should_rebalance` / `detect_convergent` / `arbitrate_conflicts`

### 路由 + 质量
- **智能路由** — 按查询复杂度自动分配 fast / balanced / quality
- **Elo 排名** — `rank_elo` 自动评估模型质量
- **L0 质量门** — `gate_l0` 拦截低质响应
- **3 评估维度** — Truthful / Logical / Helpful / Harmless / Insightful / Comprehensive ...

### 配额 + 可观测
- **3 层限流** — IP 登录限流(10/60s) + 每 key RPM + 令牌桶
- **Provider 自我修复** — `self_heal` + `tier_recalibrate` 自动降级
- **完整追踪** — trace / audit / in_flight / hook_events
- **成本估算** — `cost_estimate` 路由前就知道花费

### 安全 + 知识
- **9 类硬编码密钥扫描** — `secret-scan`(含路径白名单)
- **Prompt canary** — `prompt_canary` 检测 prompt 注入
- **RAG 检索** — `rag_search` / `semantic_search` / `rerank` / `distill`
- **Fuzzy 去重** — `fuzzy_dedup` + `input_fingerprint` 检测重复输入

### 工具集成
- **76 个 capability passthrough** — `secret_scan` / `fuzzy_dedup` / `anthropic_compat` / `request_dedup` / `grace` / `version` / `worktree` ...
- **MCP 协议** — subagent_comms / try_acquire / escalate
- **WebUI** — 静态文件托管,内置管理控制台

## 架构 (v1.8.1)

```
┌──────────────────────────────────────────────────────────────┐
│                    FastAPI 122 routes                        │
│  /v1/chat/completions  /v1/moa/execute  /v1/agent/*  ...    │
│  + /v1/capability/* (76 endpoints) + /docs + /openapi.json  │
└──────────────────┬───────────────────────────────────────────┘
                   │  Pydantic v2 (84 request models, 401+ desc)
┌──────────────────▼───────────────────────────────────────────┐
│                Service Layer (11 services / 176 methods)     │
│  MoA · Consensus · Routing · Quality · Agent · Quota ·      │
│  Knowledge · Safety · Observability · Config · Capability   │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│             Core: MoA Engine + Workflow + Capability        │
│  7 builtin workflows: moa_quality_pipeline / consensus /   │
│  quality_gate / knowledge / quota_check / safety / rag      │
└──────────────────┬───────────────────────────────────────────┘
                   │
┌──────────────────▼───────────────────────────────────────────┐
│            Storage (SQLite) + ModelPool + RateLimit         │
│         16 endpoints, async health check, mock fallback     │
└──────────────────────────────────────────────────────────────┘
```

## 对比 Hermes Agent (100+ 小时项目)


| 维度 | MoA Gateway Pro | Hermes Agent | 谁赢 |
|---|---|---|---|
| 方向性保持 | `thread_id` + Skill 摘要注入 + `approved_by` 审核 | `SOUL.md` + `MEMORY.md` 4 层 | 平手 |
| 细节保持 | SQLite `thread_messages`(**无限**)+ `SKILL.references/` | `MEMORY+USER` 共 **3,575 字符** + FTS5 | **MoA 反超** |
| 100h token 总量 | ~3M(Skill 复用 + JSON 摘要) | ~3M(技能复用) | 平手 |
| 100h 单次质量 | **优**(MoA 多模型) | 单模型 90 轮 | **MoA 反超** |
| 并发能力 | ≥ 400 RPS(带 thread_id)+ **1477 RPS**(不带) | 0(单 agent) | **MoA 碾压** |
| Skill 质量 | 强制人工写 + `approved_by` + 整文件版本 + git 跟踪 | 自动生成 + 4 级 trust + fuzzy patch | **MoA 完胜** |
| 企业级适配 | 122 端点 / 11 services / Prometheus / OpenAPI 3.0 / 鉴权 / 限速 | 个人 agent,无企业级 | **MoA 完胜** |
| 跨 worker 协同 | 4 worker 并发,SQLite WAL | 单进程 | **MoA 完胜** |
| 多模型能力 | MoA 多模型协作 +8% 质量 | 单模型 | **MoA 完胜** |

为什么我们不直接使用 Hermes?最关键的两条:

**1. "跑通 ≠ 正确"**

Hermes 的 Skill 自动生成触发条件是 "5+ 工具调用 + 踩过坑 + 修复"。**问题在于**:100 小时项目里,Agent 跑通一个流程 12 次,中间有 3 次是"勉强成功但留了隐患"。Hermes 把这 3 次的"成功路径"也写成 Skill,第 13 次遇到稍微不同的情况,Agent 会**更快更自信地走向错路**。

MoA 的强制人工审核:`approved_by` 字段必须填,Skill 进生产前 admin 签字。一次错可以拦住。

**2. "MEMORY 3,575 字符硬上限是反人类设计"**

Hermes 给 MEMORY+USER 3,575 字符的硬上限,逼 Agent 精选。听起来很优雅,但 100+ 小时项目里:

| 内容 | 字符 |
|---|---|
| 项目骨架 | 1,000 |
| 你的偏好 | 500 |
| 关键决策记录(架构/技术栈/不能改的点) | 1,500 |
| 当前任务进度 | 500 |
| **合计** | **3,500,早满了** |

临时细节必须走 Skill,Skill 又被自动生成污染。**两难**。

MoA 的方案:SQLite `thread_messages` **无限**,只把**摘要**塞进 system prompt(类似 Hermes 的 snapshot),具体内容按需 `get_recent_messages` 拉。**没有硬上限,管理成本低**。

### 给你 3 种真实场景的硬建议

| 场景 | 现在 | 改造后 |
|---|---|---|
| 你一个人做 100+ 小时个人项目 | 选 Hermes(没第二条路) | 选 MoA(完成度更高) |
| 3-5 人小团队 100+ 小时项目 | 选 Hermes(共享 Skill 库) | **强烈选 MoA**(git 共享 Skill + 强制审核 + 122 端点) |
| 100+ 小时且需要多模型高质量输出(架构设计/技术选型) | 难 | **改造后的 MoA 唯一解**(第 50 小时调一次 MoA 模式拿 4 视角,选完写进 Skill 走后续 50 小时) |

### 落地时间表(5 步,约 16 小时工作量)

| Step | 时间 | 拿到什么 |
|---|---|---|
| Step 1 | 今天,2 小时 | thread_id 接得住,SQLite 写消息 |
| Step 2 | 今天,4 小时 | 6 个 service method 全调通 |
| Step 3 | 明天,3 小时 | chat 端点带 thread_id 走通 |
| Step 4 | 明天,4 小时 | 4 个管理端点 + OpenAPI 95 |
| Step 5 | 后天,3 小时 | `skills/` 目录 + 3 个示例 Skill + sync 脚本 |

完整设计见 [LONG_PROJECT_PLAN.md](LONG_PROJECT_PLAN.md)。Token 节省机制见 [BENCHMARK_REPORT.md](BENCHMARK_REPORT.md)。

## 性能

- 7193 RPS on `GET /health` (200 线程 × 10 reqs,0.28s)
- P50 latency 0.81ms / P99 23.27ms (顺序 1000 req)
- 全 mock 模式(无外部 API 依赖)即可运行
- 健康检查 / 限流 / 鉴权全异步,event loop 不阻塞

## 测试

```powershell
# Deep E2E (76 端点, 190 actions)
$env:PYTHONPATH = "."
.venv\Scripts\python scripts\test_deep_e2e.py
# → DEEP_E2E_TOTAL: 512 pass, 0 fail

# OpenAPI schemas
.venv\Scripts\python test_openapi.py
# → 91 schemas

# Workflows (跨 service 真实数据流)
.venv\Scripts\python test_workflows_all.py
# → 7/7 pass

# Service layer (100 methods)
.venv\Scripts\python test_all_services.py

# Agent dispatcher
.venv\Scripts\python test_dispatcher.py

# 性能压测
.venv\Scripts\python test_perf.py
```

## 部署

### Docker

```bash
docker build -t moa-gateway-pro:v1.8.1 .
docker run -p 8088:8088 -e MOA_ADMIN_PASSWORD=YourPassword moa-gateway-pro:v1.8.1
```

`Dockerfile` + `docker-compose.yml` + `DEPLOYMENT.md` 都包含在内。

### 直接跑

```powershell
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "YourStrongPassword"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8088 --workers 4
```

## 配置

`config.yaml` (默认) + 环境变量 override:
- `MOA_ADMIN_PASSWORD` — WebUI admin 密码
- `MOA_API_KEY` — 网关 API key
- `MOA_DATA_DIR` — SQLite / log 目录
- `MOA_LOG_LEVEL` — DEBUG / INFO / WARNING / ERROR

## 端点分类

| 类别 | 数量 | 示例 |
|---|---|---|
| OpenAI 兼容 | 2 | `/v1/chat/completions`, `/v1/models` |
| 原生 MoA | 5 | `/v1/moa/execute`, `/v1/moa/eval`, `/v1/moa/similarity`, `/v1/moa/engine`, `/v1/moa/n-layer` |
| 路由/配额 | 3 | `/v1/route/preview`, `/v1/quota` |
| Agent/Workflow | 7 | `/v1/agent/list`, `/v1/agent/dispatch`, `/v1/agent/workflows`, ... |
| Capability | 76 | `/v1/capability/secret-scan`, `/v1/capability/fuzzy-dedup`, ... |
| Admin/Auth | 8 | `/api/auth/login`, `/api/admin/keys`, ... |
| 健康/文档 | 4 | `/health`, `/api/health/detailed`, `/docs`, `/openapi.json` |
| WebUI | 1 | `/` (静态文件) |
| **合计** | **122** | |

## 依赖

- Python 3.11+
- FastAPI / Pydantic v2 / Uvicorn
- SQLite (内置)
- bcrypt (admin 密码 hash)
- jose (JWT)
- 所有 LLM client 通过 OpenAI 兼容协议,**不需要真 API key**(MockProvider fallback)

## License

MIT

## 版本

| Version | Date | 关键特性 |
|---|---|---|
| **v1.8.1** | 2026-07-19 | Pydantic Field 描述 + 端点签名清理 |
| v1.8.0 | 2026-07-18 | 83 端点 Pydantic 化 + 90 OpenAPI schemas |
| v1.7.5 | 2026-07-18 | Final release + 7193 RPS |
| v1.7.0 | 2026-07-18 | Service Layer + AgentDispatch + Workflow |
| v1.6.6 | 2026-07-14 | Deep E2E catch-up |

完整变更见 [CHANGELOG.md](CHANGELOG.md) + [RELEASE_NOTES_v1.8.md](RELEASE_NOTES_v1.8.md)
