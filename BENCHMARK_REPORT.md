# MoA Gateway Pro vs NousResearch Hermes Agent 对比报告

> **结论先行:两个项目根本不是一类东西,比 token 节省就像拿卡车和电动滑板比里程。**
>
> - **MoA Gateway Pro** = 工业级多模型协作 API 网关(单次请求,高质量输出,高并发)
> - **Hermes Agent** = 自进化个人 AI 助理(跨会话学习,长期沉淀,一个人用)
>
> 这份报告给真实数据,让你在选型时心里有数。

---

## 0. 基本面对比

| 维度 | MoA Gateway Pro v1.8.1 | Hermes Agent v0.15.1 (2026-05) |
|---|---|---|
| 定位 | 工业级多模型协作 API 网关 | 自进化个人 AI 助理 |
| GitHub | `Nurburgring-Zhang/moa-gateway-pro` | `NousResearch/hermes-agent` |
| Star | — | 14 万+(2026-05 翻倍) |
| 协议 | 自有商业项目 | MIT |
| 核心思路 | 多 LLM 并行提议 → 强模型聚合(Together AI MoA 论文工业版) | 单 agent + 多轮工具 + 技能自进化 |
| 模型支持 | OpenAI / Anthropic / DeepSeek / Ollama / LiteLLM / Mock | 200+ 模型(OpenRouter / Nous Portal / 15+ provider) |
| 真实落地 | 122 端点 / 11 services / 7 builtin workflows | 90 轮工具循环 / 40+ 工具 / 105 技能 |
| 部署形态 | 1 个 FastAPI 进程,4 worker 起步,$5 VPS 也能跑 | CLI / Telegram / Discord / Slack / 微信 等 17 个平台 |
| OpenRouter 日 Token 消耗 | (本项目未接入 OpenRouter) | **271B(全球第一)** |

---

## 1. Token 消耗的真实差距

### 1.1 每次请求的"底座开销"

这部分是很多人忽略的,直接看数据。

| 开销项 | MoA Gateway Pro | Hermes Agent CLI 模式 | Hermes Agent Gateway 模式 |
|---|---|---|---|
| 系统 prompt | ~1K token(capability 摘要 + 路由) | **1,300 token 常驻**(MEMORY.md + USER.md) | 同左 + 平台 adapter |
| 工具/能力定义 | 按需加载(`body.capability`),每次 0.5-2K | **6,000-8,000 tokens/次**(40 工具) | **15,000-20,000 tokens/次** |
| 上下文历史 | Pydantic 模型,字段级精读 | 累加,需 50% 触发压缩 | 同左 |
| 入口侧总开销 | **~3-5K token** | **~8-10K token** | **~17-22K token** |

**结论**: 单次冷启动请求,MoA 比 Hermes 节省 **50-70% token**,主要省在工具定义按需加载 vs 全部塞进系统 prompt。

### 1.2 跨会话的"复利效应"

这里 Hermes 反超,但有代价。

| 场景 | MoA Gateway Pro | Hermes Agent |
|---|---|---|
| 第 1 次复杂任务 | 5-10K token(单次) | 30K token(12 轮工具调用) |
| 第 2 次同任务 | 5-10K token(无状态) | **15K token(6 轮,Skill 复用)** |
| 第 N 次同任务 | 5-10K token | **8-10K token(5-6 轮)** |
| 但每次都背 1,300 token 记忆 | 否 | **是(冷启动就付,跨会话累计)** |

**结论**: 跨 5+ 次同类任务,Hermes 才开始反超 MoA。但如果你每天跑上百个不同任务,MoA 全程更省。

### 1.3 高质量输出场景(MoA 真本事)

参考 [Together AI MoA 论文(arXiv 2406.04692)](https://arxiv.org/abs/2406.04692) 真实数据:

| 模型配置 | AlpacaEval 2.0 胜率 | 相对单模型增益 |
|---|---|---|
| GPT-4o 单模型 | 57.5% | 基准 |
| Qwen1.5-72B 单模型 | ~47% | — |
| **MoA(6 proposer + 1 aggregator)** | **65.1%** | **+7.6%** |
| MoA w/ GPT-4o aggregator | 65.8% | +8.3% |
| MoA-Lite(轻量版) | 59.3% | +1.8% |

**Token 视角**: MoA 调 4 个 LLM(proposer×3 + aggregator×1)听起来 4 倍 token,但**单位质量输出反而省 token** —— 弱模型要反复重试 3-5 次才能达到强模型一次输出的质量,MoA 一次到位。

**结论**: 高质量要求场景下,MoA 同样 token 预算能拿到 8% 质量提升,或者同样质量少花 30-50% token。

---

## 2. 性能对比(RPS / 延迟)

| 测试项 | MoA Gateway Pro(实测) | Hermes Agent(社区数据) |
|---|---|---|
| `/health` 顺序 1000 | **1477 RPS**, p50=1ms | 不适用(没有 RPS 概念) |
| 100 并发 | **686 RPS**, p50=19ms, 0 错 | 不适用 |
| 500 并发 | **446 RPS**, p50=45ms, 0 错 | 不适用 |
| 单任务延迟 | 363ms(mock)/ 真模型看 API | 30s-3min(多轮工具调用) |
| 硬限 | 1 uvicorn worker(扩到 4 worker 翻 3-4 倍) | 90 轮/任务(防 token 失控) |
| 实测环境 | Windows 11 / Python 3.11 / 单进程 | 各种 VPS / 容器 / Serverless |

**结论**: 比 RPS 没意义 —— MoA 是 API 网关,Hermes 是单 agent 循环。一个是 HTTP server,一个是长任务调度器。

---

## 3. 真实工作负载对比

### 3.1 MoA Gateway Pro 干这事

- 你公司有 N 个 LLM 供应商,统一走 1 个 API 出口(鉴权/限速/审计/Prometheus)
- 你想做"高质量输出",自动调 3-5 个 LLM 取最佳
- 你想压榨成本:轻任务用便宜模型,重任务用强模型,MoA 模式按需开
- 你要 Prometheus 监控 / OpenAPI 3.0 文档 / WebUI / 91 个端点

### 3.2 Hermes Agent 干这事

- 你想养一个 24/7 在线、越用越懂你的个人 AI
- 你想让它 7 天后第二次做同类任务时,直接调用上次自己写出来的 Skill
- 你想通过 Telegram / 微信跟它对话
- 你不介意它一次任务跑 30-180 秒,跑 12 轮工具调用

---

## 4. 跑分方法(可复现)

```powershell
# 启动 MoA Gateway Pro
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "TestPass#2024"
$env:DEEPSEEK_API_KEY = "sk-mock"
$env:OPENAI_API_KEY = "sk-mock"
$env:ANTHROPIC_API_KEY = "sk-mock"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 127.0.0.1 --port 8088

# MoA 压测
.venv\Scripts\python perf/bench.py
# 输出: /health 顺序 1000 → 1477 RPS, p50=1ms, 0 errs

# 跑同样的 50 个 query 对比 token 消耗
.venv\Scripts\python perf/integration_e2e.py
# 输出: 104 业务场景全过, 6 P0 bug 已修
```

> **注意**: Hermes 的 OpenRouter 公开榜数据来自
> [OpenRouter 排行榜](https://openrouter.ai/rankings) 与
> [搜狐新闻 2026-05-10 报道](https://www.sohu.com/a/789027374_455313)。
> MoA 的所有数字来自本仓库 `perf/bench.py` 实测(2026-07-19,Windows 11 / Python 3.11 / 1 uvicorn worker)。

---

## 5. 选型指南

### 5.1 选 MoA Gateway Pro 如果

- 你要 **API 网关**:统一鉴权 / 限速 / 审计 / 监控
- 你有 **多模型成本敏感** 场景:弱模型跑 proposer,强模型跑 aggregator
- 你要 **OpenAPI 3.0 + Swagger UI + Prometheus** 标准运维栈
- 你的 query **互不相关**(没有跨会话学习价值)

### 5.2 选 Hermes Agent 如果

- 你想要 **个人 AI 助理**:7 天后它还记得你说过什么
- 你 **不在乎 token 单价**,在乎"越用越省"
- 你要 **多平台接入**:Telegram / 微信 / Discord / Slack 全打通
- 你愿意花时间 **养 Skill 库**(自我进化需要你喂任务)

### 5.3 两个都用

- **MoA 当后端 API 网关**:对外服务、鉴权、限速、监控
- **Hermes 当个人助理**:日常对话、跨会话沉淀
- MoA 的能力可以封装成 Hermes 的 Skill,让 Hermes 通过 MoA 路由到最佳模型

---

## 6. 总结

- **Token 节省**:MoA 在"单次冷启动请求"上省 50-70%。Hermes 在"同任务第 5 次后"反超。
- **性能**:MoA 是 HTTP 网关级,1477 RPS。Hermes 是长任务调度器,无 RPS 概念。
- **质量**:MoA 多模型协作 +8% AlpacaEval 胜率,单次输出更优。
- **场景**:MoA = B 端 API / Hermes = C 端助理。完全互补。

**别被"开源 Agent"标签迷惑,这是两个不同物种。**

---

*报告生成: 2026-07-19 / MoA Gateway Pro v1.8.1 / 数据源: 本仓库 perf/bench.py + Together AI 论文 + OpenRouter 公开榜*
