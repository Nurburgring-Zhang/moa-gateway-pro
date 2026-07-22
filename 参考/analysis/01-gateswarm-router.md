# GateSwarm MoMA Router v0.5.6 — 深度分析报告

> 分析时间: 2026-07-13
> 项目位置: `D:\MoA Gateway Pro\参考\extracted\01-gateswarm-router\gateswarm-router-main`
> 当前版本: **v0.5.6 (Routing Transparency + Quota-Aware Routing + OSS Hygiene)**
> 许可证: MIT

---

## 1. 项目概述 (Project Overview)

**GateSwarm MoMA Router** 是一个**自优化 LLM 路由网关 (Self-Optimizing LLM Routing Gateway)**。它作为 OpenAI 兼容客户端与多种 LLM 提供方（HTTP 云 API、本地 Ollama、CLI 子进程代理）之间的中间层，拦截每个 chat-completion 请求，通过 **25 维特征 + 集成投票器 (ensemble voter)** 对 prompt 复杂度打分，挑选**最便宜且能胜任的模型**，转发请求并持续记录反馈以自我改进。

**MoMA = Mixture of Multimodal Agents** — 即不只用一个模型服务所有请求，而是**按需混搭本地、HTTP 云、CLI 代理等多种提供方**，并支持 vision/audio 多模态路由。

**核心架构**:
```
Client (OpenAI-compatible) → GateSwarm :8900
    ↓
[9 阶段请求管线] 1.解析 → 2.复杂度打分(ensemble) → 3.Plan/Act 模式解析
                  → 4.effort_override 旁路 → 5.greeting 快路径
                  → 6.ConsumptionIntelligence 选模型 → 7.TurboQuant 压缩
                  → 8.RAG 上下文注入 → 9.7-phase 消息清洗 → 10.分派/回退
                  → 11.反馈记录 + 自评 + 训练投票 + 续接注入
    ↓
HTTP/CLI Provider → 返回 → 客户端
```

**核心价值**: 对混合工作负载节省 60-90% 的 token 成本；provider 配额耗尽/限流时自动回退；多模态请求自动路由到 vision-capable 模型；通过 RAG/反馈回路持续校准 tier 边界。

---

## 2. 核心模块清单 (Core Module Inventory)

### 2.1 顶层入口 (Top-Level Entry)

| 路径 | 作用 |
|---|---|
| `router.py` | Python v0.3.5 单文件路由器 (15 维特征 + 5 二分类级联) — 旧版，保留作生产基线与 HTTP API |
| `train.py` | Python v0.3 级联分类器训练器 (sklearn LogisticRegression × 5) — 离线训练 cascade_weights.json |
| `package.json` | Node.js 包元数据、npm scripts (start/test/eval:*) |
| `requirements.txt` | Python 依赖 (scipy, numpy, scikit-learn, datasets, requests) |
| `tsconfig.json` | TypeScript 配置 (ESM, target ES2022) |
| `vite.config.ts` | Vite 配置 (用于 cli/dist 构建) |
| `Dockerfile` | Python 容器构建 (router.py) |
| `Dockerfile.inference` | 推理服务容器 (legacy) |
| `.env.example` | 环境变量模板 (BAILIAN_KEY, ZAI_KEY, OLLAMA_CLOUD_KEY, OLLAMA_BASE, PORT) |
| `v04_config.json` | **热重载的活配置** (tier_models / tier_boundaries / feedback_loop / rag) |
| `v32_cascade_weights.json` | v0.3.2 级联权重 (历史参考) |
| `v33_heuristic_weights.json` | v0.3.3 启发式权重 (历史参考) |
| `LICENSE` | MIT |
| `CHANGELOG.md` | 发布历史 (v0.4.4 → v0.5.6) |
| `PRD.md` | 产品需求 |
| `QUICKSTART.md` | 5 分钟快速开始 |
| `SECURITY.md` | 漏洞报告 |
| `CONTRIBUTING.md` | 贡献指南 |
| `CODE_OF_CONDUCT.md` | 社区准则 |

### 2.2 主 src 目录 (Main TypeScript Source)

| 路径 | 作用 |
|---|---|
| `src/moma-gateway.ts` | **核心 HTTP 服务器** (2868 行)：9 阶段请求管线、路由、CORS、流转发、端点注册 (44 个端点) |
| `src/router.ts` | ModelRouter 类 (122 行)：基于 Effort×Device 矩阵的路由决策 (legacy, 仍被 CLI/TUI 引用) |
| `src/intent-engine.ts` | v0.4 浏览器兼容意图引擎 (108 行) — 25 维启发式打分 |
| `src/intent-engine-v04.ts` | v0.4 服务端集成打分器 (109 行) — 协调 heuristic + RAG + history |
| `src/feature-extractor-v04.ts` | **25 维特征提取器** (391 行)：v3.3 9 个 + v3.2 6 个 + v0.4 10 个 |
| `src/ensemble-voter.ts` | **集成投票器** (390 行)：heuristic + cascade + RAG + historyBias 加权 |
| `src/consumption-intelligence.ts` | **消费智能引擎** (890 行)：主动探活 + 静态优先 + 自愈 tier 再平衡 |
| `src/provider-quota.ts` | **配额管理** (611 行)：RPM/RPD/token 追踪 + 健康评分 + 3 窗口配额 |
| `src/consumption-tracker.ts` | **消费追踪** (576 行)：按小时分桶的 5h/weekly/monthly/all-time 用量统计 |
| `src/model-matrix.ts` | **模型矩阵** (737 行)：所有已知模型元数据 + 容量自动分级 |
| `src/model-discovery.ts` | **模型发现** (342 行)：每 15 分钟轮询 /models 端点 |
| `src/routing-matrix.ts` | **路由矩阵** (311 行)：Effort × Device 5×6 = 30 决策单元 |
| `src/agent-registry.ts` | **代理注册表** (721 行)：多代理认证 + CLI provider 调度 + 防抖写入 |
| `src/feedback-store.ts` | **反馈存储** (258 行)：JSON 文件持久化的 prompt/route/actualTier 记录 |
| `src/rag-index.ts` | **RAG 索引** (248 行)：JSON 持久化的关键词重叠检索 + 24h TTL |
| `src/turboquant-compressor.ts` | **TurboQuant 压缩器 v3.6** (818 行)：5 级 Q8/Q4/Q2/Q1/Q0 + 结构不变量 |
| `src/token-estimator.ts` | **tiktoken 包装** (63 行)：懒加载 cl100k_base 编码器 |
| `src/tier-boundaries.ts` | **Tier 边界** (97 行)：5 个分数阈值，热更新 |
| `src/retraining.ts` | **再训练** (144 行)：基于已评分反馈的边界网格搜索 |
| `src/self-eval.ts` | **自评** (193 行)：快速启发式 + LLM judge (qwen3.6-plus) |
| `src/training-mode.ts` | **训练模式** (482 行)：3 标签源 (gold/silver/bronze) + aleatory 采样 |
| `src/vote-persistence.ts` | **投票持久化** (264 行)：JSON 文件存储训练投票 |
| `src/label-combiner.ts` | **标签组合器** (216 行)：3 源权重校准 + RAG bootstrap 阶段 |
| `src/benchmark-logger.ts` | **基准日志** (253 行)：每请求成本/节省/延迟记录 |
| `src/quota-sync.ts` | **配额同步** (198 行)：从 dashboard 抓取真实配额数据 |
| `src/gateswarm-cli.ts` | **CLI 主入口** (935 行)：30+ 子命令 |

### 2.3 子目录 (Subdirectories)

| 路径 | 作用 |
|---|---|
| `src/adapters/` | 模型适配器层 (6 文件) |
| `src/classifiers/` | 分类器层 (3 文件) |
| `src/secrets/` | Sovereign Vault 集成 (4 文件) |
| `src/types/` | TypeScript 类型定义 (1 文件) |
| `cli/src/` | GateSwarm-Bar TUI (8 文件, React + Ink) |
| `tests/` | Vitest 测试套件 (22 文件) |
| `eval/` | 评估工具 (12 文件 + 3 split 数据) |
| `llmfit/` | Python 训练工具 (4 文件) |
| `data/` | 运行时状态 (gitignored) |
| `public/` | ONNX 模型 + tokenizer + sw.js |
| `scripts/` | 运维脚本 (10 文件) |
| `docs/` | 文档 (15 .md 文件) |

### 2.4 adapters/ (6 文件)

| 路径 | 作用 |
|---|---|
| `src/adapters/cli-provider.ts` | **CLI Provider 适配器** (322 行)：subprocess 调度 Claude Code / Codex / Pi / Hermes / OpenClaw |
| `src/adapters/cli-adapter.ts` | 旧版 CLI 适配器 (134 行, spawn 通用 CLI) |
| `src/adapters/cloud-api-adapter.ts` | 旧版云 API 适配器 (105 行, SSE 流解析) |
| `src/adapters/local-adapter.ts` | **本地适配器** (100 行)：@huggingface/transformers WebGPU/WebNN/WASM |
| `src/adapters/ollama-adapter.ts` | Ollama 适配器 (112 行)：直接 HTTP 调用 /api/tags + /api/generate |
| `src/adapters/registry.ts` | 适配器注册表 (150 行)：懒加载 + 用量统计 |
| `src/adapters/provider-health.ts` | **提供者健康追踪** (234 行)：5 分钟 cooldown + 不可用响应识别 |
| `src/adapters/index.ts` | 桶导出 (23 行) |
| `src/adapters/types.ts` | 适配器类型 (76 行) |

### 2.5 classifiers/ (3 文件)

| 路径 | 作用 |
|---|---|
| `src/classifiers/ordinal-logistic.ts` | **序数 Logistic 回归** (495 行)：5 阈值累积 logit，可加载 v05_ordinal_weights.json |
| `src/classifiers/heuristic-linear.ts` | **启发式基线分类器** (179 行)：实现了 TierClassifier 契约 + 单调切点网格搜索 |
| `src/classifiers/types.ts` | TierClassifier 契约 (52 行) |

### 2.6 secrets/ (4 文件)

| 路径 | 作用 |
|---|---|
| `src/secrets/vault-env.ts` | Vault → process.env 引导 (48 行) |
| `src/secrets/sv-secrets.mjs` | Sovereign Vault 加载器 (Node 子进程) |
| `src/secrets/sv-secrets.d.mts` | TypeScript 声明 |
| `src/secrets/README.md` | 集成文档 |

### 2.7 types/ (1 文件)

| 路径 | 作用 |
|---|---|
| `src/types/huggingface-transformers.d.ts` | @huggingface/transformers 第三方类型声明 |

### 2.8 cli/ (TUI 客户端, 8 文件)

| 路径 | 作用 |
|---|---|
| `cli/src/cli.tsx` | TUI 入口 (94 行)：4 标签 + mock 模式 + once JSON dump |
| `cli/src/api.ts` | HTTP API 客户端 (67 行)：5 个端点封装 |
| `cli/src/types.ts` | API 类型 |
| `cli/src/format.ts` | 数字/字节格式化 |
| `cli/src/mock.ts` | 内置 mock 数据 (脱机运行) |
| `cli/src/components/App.tsx` | TUI 主应用 (197 行) |
| `cli/src/components/Header.tsx` | 顶部状态栏 (76 行) |
| `cli/src/components/ProvidersPanel.tsx` | 提供方面板 |
| `cli/src/components/ModelsPanel.tsx` | 模型面板 |
| `cli/src/components/TiersMatrix.tsx` | Tier 矩阵 |
| `cli/src/components/RouterConfig.tsx` | 路由配置面板 |
| `cli/src/components/ActivityPanel.tsx` | 活动面板 (隐藏 health-check 噪声) |
| `cli/package.json` | TUI 包元数据 |
| `cli/tsconfig.json` | TUI TS 配置 |

### 2.9 tests/ (22 文件)

| 路径 | 作用 |
|---|---|
| `tests/agent-registry-debounce.test.ts` | 防抖写入测试 |
| `tests/plan-act-routing.test.ts` | Plan/Act 路由 |
| `tests/intent-engine.test.ts` | 意图引擎 |
| `tests/router.test.ts` | ModelRouter |
| `tests/routing-matrix.test.ts` | 路由矩阵 |
| `tests/ensemble-voter.test.ts` | 集成投票 |
| `tests/feature-extractor-v04.test.ts` | 25 维特征 |
| `tests/heuristic-boundaries.test.ts` | 启发式边界 |
| `tests/ordinal-logistic.test.ts` | 序数回归 |
| `tests/cli-providers.test.ts` | CLI 提供方 |
| `tests/provider-health.test.ts` | 提供方健康 |
| `tests/benchmark.test.ts` | 基准 |
| `tests/assertiveness.test.ts` | 路由决断性 |
| `tests/score-drift-guard.test.ts` | 分数漂移守卫 |
| `tests/subsystem-smoke.test.ts` | 子系统冒烟 |
| `tests/training-mode.test.ts` | 训练模式 |
| `tests/plan-act.test.ts` | Plan/Act 解析 |
| `tests/hybrid-eval-helpers.test.ts` | Hybrid 评估辅助 |
| `tests/hybrid-rubric.test.ts` | Hybrid 评分 |
| `tests/hybrid-sample.test.ts` | Hybrid 采样 |
| `tests/hybrid-warm-fixtures.test.ts` | Hybrid 预热夹具 |
| `tests/test_router.py` | Python router 测试 |

### 2.10 eval/ (12 文件 + splits)

| 路径 | 作用 |
|---|---|
| `eval/cv.ts` | 交叉验证 |
| `eval/assess.ts` | 评估 |
| `eval/calibrate.ts` | 校准 |
| `eval/leaderboard.ts` | 模型排行榜 |
| `eval/feature-report.ts` | 特征报告 |
| `eval/refit-boundaries.ts` | 边界重拟合 |
| `eval/consistency-check.ts` | 配置一致性 |
| `eval/split.ts` | 数据切分 |
| `eval/hybrid-routing-eval.ts` | Hybrid 路由评估 |
| `eval/train-ordinal.ts` | 序数模型训练 |
| `eval/dataset.json` | 评估数据集 |
| `eval/ASSESSMENT.md` | 评估文档 |
| `eval/lib/` | 9 个 lib 工具 (dataset, metrics, runner, split, hybrid-*) |
| `eval/splits/` | 5 折交叉验证 + 留出集 (folds.v1.json, holdout.v1.json, MANIFEST.json) |
| `eval/ssl/` | 半监督学习 (3 文件: build-corpus, extract-features, label_propagation) |

### 2.11 llmfit/ (Python 训练)

| 路径 | 作用 |
|---|---|
| `llmfit/llmfit.py` | LLM 微调 |
| `llmfit/self_eval.py` | 自评 |
| `llmfit/anonymizer.py` | 路径/标识符脱敏 |
| `llmfit/__init__.py` / `__main__.py` | 包入口 |
| `llmfit/datasets/gpd_generator.py` | 合成数据集生成器 |

### 2.12 public/ (静态资源)

| 路径 | 作用 |
|---|---|
| `public/index.html` | 入口 HTML |
| `public/dashboard.html` | 仪表盘 |
| `public/sw.js` | Service Worker |
| `public/models/complexity-regressor-q4.onnx` | 复杂度回归 ONNX 模型 |
| `public/models/complexity-regressor-v2.onnx` | v2 ONNX |
| `public/models/tokenizer/` | 分词器 (5 文件: tokenizer.json, tokenizer_config.json, special_tokens_map.json, added_tokens.json, spm.model) |

### 2.13 scripts/ (运维脚本, 10 文件)

| 路径 | 作用 |
|---|---|
| `scripts/cascade-retrain.py` | 级联再训练 |
| `scripts/cli-health-probe.sh` | 认证感知健康探针 |
| `scripts/quota-sync.py` | 配额 scraper |
| `scripts/run-gateway-local.ps1` | 本地启动 (PowerShell) |
| `scripts/start-gateway.sh` | 启动脚本 (Bash) |
| `scripts/demo-gateway-probe.mjs` | 演示探针 |
| `scripts/demo-pi-gateswarm.ps1` / `interactive.ps1` | Pi 代理演示 |
| `scripts/sv-import-env.mjs` | Vault 导入 |
| `scripts/QUOTA_SYNC_README.md` | 配额同步文档 |

### 2.14 docs/ (15 文件)

| 路径 | 作用 |
|---|---|
| `docs/ARCHITECTURE.md` | 9 阶段管线 + 7 阶段清洗 + 4 象限回退 |
| `docs/ROUTING_STRATEGY.md` | 路由策略 |
| `docs/ROUTING_IMPROVEMENT_PLAN.md` | 改进路线 |
| `docs/INTEGRATION.md` | 集成指南 |
| `docs/REQUIREMENTS.md` | 需求 |
| `docs/OPS_GUIDE.md` | 运维 |
| `docs/SECURITY_AUDIT.md` | 安全审计 |
| `docs/SAFETY.md` | 安全 |
| `docs/PERSISTENCE_GUIDE.md` | 持久化 |
| `docs/CONTEXT_COMPRESSION_GUIDE.md` | 压缩指南 |
| `docs/ACCURACY_ROADMAP.md` | 准确度路线 |
| `docs/TRAINING_MODE_GUIDE.md` | 训练模式 |
| `docs/GATEWAY_QUICKSTART.md` | 网关快速开始 |
| `docs/research/V3_2_CASCADE_REPORT.md` | v3.2 研究 |
| `docs/research/V3_3_MODEL_ROUTING_STRATEG...` | v3.3 研究 |

### 2.15 .github/

| 路径 | 作用 |
|---|---|
| `.github/workflows/ci.yml` | CI: typecheck + test |
| `.github/ISSUE_TEMPLATE/bug_report.yml` | Bug 报告模板 |
| `.github/ISSUE_TEMPLATE/feature_request.yml` | 功能请求模板 |
| `.github/ISSUE_TEMPLATE/config.yml` | Issue 配置 |
| `.github/pull_request_template.md` | PR 模板 |

---

## 3. 详细能力列表 (Detailed Capability List)

### 3.1 API 端点 (HTTP API Endpoints)

#### 3.1.1 OpenAI 兼容入口

**`POST /v1/chat/completions`** (src/moma-gateway.ts:2663)
- **描述**: 主完成端点。OpenAI 兼容，接受任意 agent 的请求。
- **认证**: `Authorization: Bearer moma-<agent-key>` 或 `x-api-key` 头
- **请求体**: 标准 OpenAI 格式 + 扩展字段 `mode`, `effort_override`, `direct_route`, `session_id`
- **流程**: 9 阶段管线 (认证 → 消息归一化 → 模式/effort 解析 → 直接路由旁路 → 问候快路径 → 复杂度打分 → 消费智能选模型 → Plan/Act 模式覆写 → Trivial 快路径 → TurboQuant 压缩 → RAG 注入 → 续接注入 → 7-phase 清洗 → CLI/HTTP 分派 → 反馈/自评/RAG/续接记录)
- **响应头**: `X-Tier`, `X-Score`, `X-Routed-Model`, `X-Routed-Tier`, `X-Routing-Method`, `X-Routing-Reason`, `X-Mode`, `X-Mode-Confidence`, `X-Modality`, `X-Training-Vote`
- **错误码**: 400 (参数错误), 401 (未授权), 502 (provider 链耗尽), 503 (provider 不可用), 504 (超时)

**`GET /v1/models`** (src/moma-gateway.ts:2513)
- **描述**: 列出所有可用模型，附带 provider 元数据
- **响应**: `{ object: "list", data: [{ id, object, owned_by, providerType }] }`
- **CLI 模型**: 保留 `cc/`, `cx/`, `pi/`, `hm/`, `oc/` 前缀

**`GET /v1/providers`** (src/moma-gateway.ts:2534)
- **描述**: 列出所有注册 provider (HTTP + CLI)，含配置状态、CLI 健康检查
- **响应字段**: `id, name, type, models, configured, available, healthCheck, quota`

**`POST /v1/direct/chat`** (src/moma-gateway.ts:2555)
- **描述**: 直接路由端点（旁路所有评分）
- **必填**: `direct_route: { provider, model }`
- **使用场景**: Pi 代理需要固定某 provider 的请求

**`POST /v1/score`** (src/moma-gateway.ts:2335)
- **描述**: 仅分类、不分派。返回 tier + 推荐模型
- **用途**: Pi statusline 扩展 (pi-v33-statusline) 在 footer 显示即将路由的决策
- **请求**: `{ prompt: string, mode?: 'plan' | 'act' }`
- **响应**: `{ prompt, score, tier, method, confidence, lowConfidence, classifierAccuracy, latencyMs, selected, mode }`

#### 3.1.2 代理管理

**`GET /v1/agents`** (src/moma-gateway.ts:2588)
- 返回所有注册代理的元数据 (id, name, provider, tierProfile, benchmarkEnabled, requestCount, createdAt)

**`POST /v1/agents/register`** (src/moma-gateway.ts:2604)
- **请求**: `{ name, provider?, tierProfile?, benchmarkEnabled?, maxTokensPerRequest? }`
- **响应 201**: 返回生成的 `apiKey = "moma-" + 32 字符 hex` 和连接模板 `{ base_url, api_key }`
- **tierProfile**: 'cost-optimized' | 'balanced' | 'quality' | 'benchmark' | 'claude-quality' | 'codex-heavy'

**`GET /v1/agents/:id`** (src/moma-gateway.ts:2635)
- 返回单个代理完整配置

**`PATCH /v1/agents/:id`** (src/moma-gateway.ts:2645)
- 更新 `tierProfile` / `benchmarkEnabled` / `provider`

#### 3.1.3 健康/指标

**`GET /health`** (src/moma-gateway.ts:2235)
- **响应**: `{ status, router, turboquant, ensemble, feedback, llmJudge, capabilities, timestamp, providers, agents }`
- providers 列表包含每 provider 的 quota 状态 (CLI)
- agents 列表含每 agent 的 requestCount

**`GET /metrics`** (src/moma-gateway.ts:2483)
- 返回 `benchmarkLogger.getTodaySummary()`: 当日请求总数、token、节省金额、tier/model 分布

**`GET /metrics/:agentId`** (src/moma-gateway.ts:2489)
- 单个 agent 的 usage (requestCount, totalTokensIn/Out, lastUsed) + 配置

#### 3.1.4 v0.4 集成/反馈管理

**`GET /v04/status`** (src/moma-gateway.ts:2693)
- 集成权重 (live) + 路径 (ensemble-v0.4 | heuristic-fallback) + tier 模型 + 推理状态 + 反馈 buffer + LLM judge 模型
- 重要: 返回的是 **活 voter 权重** (非 write-only config copy)

**`GET /v04/feedback`** (src/moma-gateway.ts:2726)
- 总交互数 + 最近 20 条 + 每 tier 准确度 + 是否应该再训练

**`POST /v04/retrain`** (src/moma-gateway.ts:2736)
- 手动触发 tier 边界重校准
- 响应: `{ retrained, accuracyBefore, accuracyAfter, boundaries, reason }`
- **算法**: 网格搜索 5 阈值在 labeled 数据上 (score, actualTier) 的最佳精确度
- 触发条件: ≥ min(30, minSamplesPerTier × 3) 评分反馈, 且新准确度提升 ≥ 2 个百分点

**`GET /v04/training?agentId=X`** (src/moma-gateway.ts:2752)
- 单 agent 训练统计: enabled, totalVotes, gold/silver/bronze 标签数, 待定投票, 疲劳衰减, RAG 阶段

**`POST /v04/training/enable`** (src/moma-gateway.ts:2766)
- 启用/禁用 agent 的训练模式: `{ agentId, enabled }`

**`POST /v04/training/vote`** (src/moma-gateway.ts:2781)
- 记录投票回复: `{ voteId, agentId, reply }`

**`POST /v04/training/vote/reply`** (src/moma-gateway.ts:2795)
- 检查消息是否是投票回复: `{ agentId, message }` → `{ isVote, voteId }`

#### 3.1.5 v0.5 消费智能/配额

**`GET /v05/intel`** (src/moma-gateway.ts:2262)
- 完整 intel: `version`, `stats` (模型/请求/错误/token/成本), `recommendations` (每 tier 推荐), `recentDecisions`

**`GET /v05/intel/last-decision`** (src/moma-gateway.ts:2271)
- 最近 1 条 request 决策（用于 ActivityPanel）

**`GET /v05/intel/ops-guide`** (src/moma-gateway.ts:2279)
- 返回 `docs/OPS_GUIDE.md` 全文（`text/markdown`）

**`GET /v05/intel/models`** (src/moma-gateway.ts:2363)
- 所有模型列表 (按 totalTokensIn 降序)

**`GET /v05/intel/providers`** (src/moma-gateway.ts:2372)
- 提供方汇总 (model 数、token 总数、错误率、平均延迟)

**`POST /v05/intel/rediscover`** (src/moma-gateway.ts:2378)
- 强制立即重新发现所有 provider 模型

**`GET /v05/intel/consumption`** (src/moma-gateway.ts:2387)
- 5h/weekly/monthly/all-time 消费报告 + quota 状态 + ETA 到耗尽

**`GET /v05/intel/usage`** (src/moma-gateway.ts:2402)
- providerQuota.getUsageSummary() - 总 token/请求/成本

**`GET /v05/intel/balance`** (src/moma-gateway.ts:2409)
- 每 tier 当前模型 + 置信度 + 当前 swap 状态

**`GET /v05/intel/swaps`** (src/moma-gateway.ts:2431)
- 自愈 tier swap 状态: `{ swaps: [...], currentTiers }`

**`GET /v05/intel/sync`** (src/moma-gateway.ts:2441)
- 从 data/quota-sync.json 读取真实 dashboard 快照

**`GET /v05/intel/quota`** (src/moma-gateway.ts:2455)
- 每 provider 详细: health, RPM/RPD/tokens remaining, throttled, throttledUntil, realQuota (5h/weekly/monthly 用量%)

**`GET /v05/cli`** (src/moma-gateway.ts:2809)
- CLI provider 状态: enabled, 每个 provider 的 available, reason, command, maxConcurrent, quota, models, contextWindow

#### 3.1.6 v0.6 Plan/Act

**`POST /v06/mode/detect`** (src/moma-gateway.ts:2295)
- 提示词模式检测: `{ prompt }` → `{ mode: 'plan'|'act'|'auto', confidence, planScore, actScore }`

**`POST /v06/resolve`** (src/moma-gateway.ts:2306)
- 解析给定 (tier, mode) 将用哪个模型: `{ tier, mode }` → `{ tier, mode, resolved: { model, provider, max_tokens, enable_thinking } }`

### 3.2 数据模型 (Data Models)

#### 3.2.1 复杂度和路由类型 (src/types.ts)

```typescript
type EffortLevel = 'trivial' | 'light' | 'moderate' | 'heavy' | 'intensive' | 'extreme';
type IntentMode = 'plan' | 'act' | 'auto';
type Tier = 'local' | 'gatekeeper' | 'cloud';
type ModelTier = 'nano' | 'small' | 'medium' | 'large' | 'cloud-light' | 'cloud-heavy';
type DeviceProfileName = 'desktop-high' | 'desktop-mid' | 'mobile-high' | 'mobile-low' | 'lowend';
type BackendType = 'webgpu' | 'webnn' | 'wasm';

interface ComplexityScore {
  value: number; // 0-1
  rawValue?: number; // 无 history_bias 的值
  method: 'ml' | 'heuristic' | 'v3.3-heuristic' | 'heuristic-fallback' | 'ensemble-v0.4';
  latencyMs: number;
  tier?: EffortLevel;
  confidence?: number;
  lowConfidence?: boolean;
  classifierAccuracy?: number;
}

interface RoutingDecision {
  tier, model, score, effort, deviceClass, estimatedLatencyMs,
  estimatedCostCents, qualityScore, reason, profile, mode
}
```

#### 3.2.2 配置模型 (src/v04-config.ts)

```typescript
interface FallbackModel { model, provider }
interface TierModelConfig {
  model, provider, max_tokens, enable_thinking,
  fallback_models?: FallbackModel[],
  plan_model?, plan_provider?, plan_max_tokens?, plan_enable_thinking?
}
interface EnsembleWeightsConfig { heuristic, cascade, ragSignal, historyBias }  // 和=1
interface FeedbackLoopConfig {
  retrainAfterInteractions: 500, minSamplesPerTier: 50,
  maxWeightChangePct: 0.20, llmJudgeModel: 'zai/glm-4.7',
  llmJudgeSamplingRate: 0.10, cascadeRetraining: false,
  cascadeRetrainingSource: 'real_feedback_labels' | 'formula_labels',
  abTestHoldoutPct: 0.10
}
interface RagConfig { inMemory, sqlite, maxEntries: 10000, ttlMs: 86400000, queryMaxResults: 3 }
interface V04Config {
  version, trained, method,
  ensemble: { weights, confidenceThresholds: { high: 0.8, low: 0.5 }, lowConfidenceAction, ordinalAbstainMargin: 0.08 },
  scoring: { formula, signal_types: 9, feature_count: 25, signals[] },
  tier_boundaries: Record<EffortLevel, [number, number]>,
  tier_models: Record<EffortLevel, TierModelConfig>,
  feedback_loop, rag
}
```

#### 3.2.3 代理类型 (src/agent-registry.ts)

```typescript
type ProviderType = 'http-api' | 'cli-agent';
interface HttpProviderConfig { id, name, type: 'http-api', baseUrl, apiKey, models[] }
interface CliProviderEntry { id, name, type: 'cli-agent', models[], cliConfig: CliProviderConfig }
interface AgentTierConfig { trivial, light, moderate, heavy, intensive, extreme: string }
interface AgentConfig {
  id, name, apiKey, provider, tierConfig, benchmarkEnabled, maxTokensPerRequest,
  createdAt, lastUsed, requestCount, totalTokensIn, totalTokensOut
}
interface RegistryState { providers, agents, defaultAgentId }

const DEFAULT_TIER_CONFIGS = {
  'cost-optimized': { trivial: 'zai/glm-4.5-air', light: 'opencodego/deepseek-v4-flash', ... },
  'quality': { ..., heavy: 'cc/claude-sonnet-4-6', ... },
  'balanced': { ... },
  'benchmark': { trivial: 'openrouter/owl-alpha', ... },
  'claude-quality': { ... },
  'codex-heavy': { ... }
}

const HTTP_PROVIDER_MODELS: Record<string, string[]> = {
  bailian: ['qwen3.6-plus', 'qwen3.5-plus', 'qwen3-coder-plus', 'qwen3.6-max-preview', 'qwen4.6'],
  zai: ['glm-4.5-air', 'glm-4.7', 'glm-4.7-flash', 'glm-5', 'glm-5-turbo', 'glm-5.1'],
  openrouter: ['owl-alpha', 'glm-4.7-flash', 'qwen-plus', 'gemini-2.5-flash', 'claude-sonnet-4.6', 'claude-opus-4.6'],
  opencodego: ['deepseek-v4-flash', 'deepseek-v4-pro', 'qwen3.7-plus', 'qwen3.7-max', 'qwen3.6-plus', 'kimi-k2.5', 'kimi-k2.6', 'glm-5', 'glm-5.1', 'minimax-m3', 'minimax-m2.7', 'mimo-v2.5', 'mimo-v2.5-pro'],
  ollama: ['qwen2.5:0.5b', 'qwen2.5:1.5b'],
  'ollama-cloud': ['kimi-k2.5', 'kimi-k2.6', 'kimi-k2.7-code', 'glm-5.1', 'gemma3:12b', 'qwen3-vl:235b', 'minimax-m2.7', 'minimax-m3', 'deepseek-v4-pro']
}

const DEFAULT_CLI_PROVIDERS: Record<string, CliProviderEntry> = {
  'claude-cli': { command: 'claude', argsTemplate: ['--print', '--model', '{model}', '-p', '{prompt}'], models: ['cc/claude-sonnet-4-6', 'cc/claude-opus-4-7', 'cc/claude-opus-4-8', 'cc/claude-haiku-4-5'], quota: { type: 'subscription', windows: [{'5-hour': 5h, weekly: 7d}] } },
  'codex-cli': { command: 'codex', argsTemplate: ['exec', '-'], modelFlag: '--config', models: ['cx/gpt-5.5-codex', 'cx/gpt-5.4-codex', 'cx/gpt-5.3-codex', 'cx/gpt-4.1'] },
  'pi-agent': { command: 'node', argsTemplate: [process.env.HOME + '/.pi/agent/src/index.js', ...], models: ['pi/qwen3.5-plus', 'pi/glm-4.7-flash'] },
  'hermes-agent': { command: 'node', argsTemplate: ['/usr/local/lib/hermes-agent/src/agent.js', ...], models: ['hm/glm-4.7', 'hm/glm-4.7-flash'] },
  'openclaw-agent': { command: 'openclaw', argsTemplate: ['agent', '--agent', 'main', '--model', '{model}', '--message', '{prompt}', '--timeout', '120', '--json'], models: ['oc/bailian/qwen3.5-plus', 'oc/zai/glm-4.7-flash'] }
}
```

#### 3.2.4 模型矩阵 (src/model-matrix.ts)

```typescript
interface ModelEntry {
  id, provider, name,
  contextWindow, maxTokens, inputModalities[],
  supportsReasoning, supportsVision, supportsTools,
  available, lastChecked, latencyMs, avgLatencyMs,
  costPer1kInput, costPer1kOutput,
  totalTokensIn, totalTokensOut, totalRequests, errorCount, consecutiveFailures, lastError,
  recommendedTier, updatedAt
}
interface ProviderSummary { provider, name, totalModels, availableModels, totalTokensIn/Out, totalRequests, totalErrors, avgLatencyMs, rateLimitHits }
```

#### 3.2.5 配额类型 (src/provider-quota.ts)

```typescript
interface ProviderQuota {
  provider, name,
  rpm, rpd, rpmRemaining, rpdRemaining, rpmResetAt,
  tokensDailyLimit, tokensRemaining,
  requestsThisMinute, requestsToday, tokensToday, lastRequestAt, minuteWindowStart,
  totalRequests, totalTokens,
  rateLimitHits, consecutive429s, throttled, throttledUntil, healthScore,  // 0-100
  estimatedCost
}
interface WindowQuotaConfig { requests: number|null, tokens: number|null, resetAt: string, resetType: 'fixed' | 'rolling' }
interface MultiWindowQuotaConfig { fiveHour, weekly, monthly: WindowQuotaConfig }

// 预定义配额
const MULTI_WINDOW_QUOTAS: {
  ollama: { 5h: ∞, weekly: ∞, monthly: ∞ },
  'ollama-cloud': { 5h: { requests: 200, tokens: 50000 }, weekly: { requests: 2000, tokens: 500000 } },
  opencodego: { 5h: { tokens: 50000 }, weekly: { tokens: 300000 }, monthly: { tokens: 500000 } },
  zai: { 5h: { tokens: 30000 }, weekly: { tokens: 200000 } },
  bailian: { 5h: { requests: 300, tokens: 250000 }, weekly: { requests: 30000 }, monthly: { requests: 100000 } }
}
```

#### 3.2.6 消费追踪 (src/consumption-tracker.ts)

```typescript
interface HourlyBucket { hourStart, requests, tokensIn, tokensOut, cost, latencySum, latencyCount, errors }
interface ProviderConsumption { provider, buckets: Record<number, HourlyBucket>, totalRequests/In/Out/Cost/Errors, firstRequestAt, lastRequestAt }
interface WindowConsumption { requests, tokensIn, tokensOut, totalTokens, cost, errors, avgLatencyMs, windowMs, windowStart, windowEnd, hoursCovered }
interface QuotaWindowStatus { usedRequests, limitRequests, remainingRequests, usedPctRequests, usedTokens, limitTokens, remainingTokens, usedPctTokens, resetAt, resetType, etaExhaustion? }
interface ProviderConsumptionReport { provider, fiveHour, weekly, monthly, allTime, ageInDays, dailyAverage, trend: { fiveHourVsPrevious, weeklyVsPrevious }, quota: ProviderQuotaStatus }
```

#### 3.2.7 反馈 (src/feedback-store.ts)

```typescript
interface FeedbackEntry {
  id, timestamp, promptHash, predictedTier, actualTier|null,
  modelUsed, responseTokens, adequacyScore|null, escalated, userSatisfaction|null,
  score?, promptSnippet?, source?, agentId?
}
const MAX_ENTRIES = 10000;
// 文件: data/feedback/entries.json, TTL 由 v04_config.rag.ttlMs 控制
```

#### 3.2.8 RAG (src/rag-index.ts)

```typescript
interface RagEntry {
  id, timestamp, keywords[], tags[], tier: string,  // effort tier OR 'Q0'|'Q1'|'Q2' for compressor
  modelUsed, originalRole, adequacyScore, summary, originalTokens, compressedTokens
}
const MAX_ENTRIES = 10000, TTL_MS = 86400000;
// 文件: data/rag/index.json
```

#### 3.2.9 训练投票 (src/vote-persistence.ts)

```typescript
interface VoteRecord {
  id, agentId, promptHash, promptSnippet, predictedTier, actualTier|null,
  source: 'gold' | 'silver' | 'bronze', weight, timestamp, expiresAt,
  voted, userAgreed|null, userCorrectTier|null, score?
}
interface AgentTrainingConfig {
  agentId, enabled: false, aleatoryRate: 0.10, alwaysAskBelowConfidence: 0.5,
  neverAskTiers: ['trivial', 'extreme'], weightedTiers: ['moderate', 'heavy', 'intensive'],
  weightedRateMultiplier: 2.0, retrainAfterVotes: 10, voteExpiryMs: 24h
}
const MAX_VOTES = 5000;
```

#### 3.2.10 标签组合 (src/label-combiner.ts)

```typescript
interface LabelSource { tier, source: 'gold'|'silver'|'bronze', weight, confidence }
interface CombinedLabel { tier, confidence, totalWeight, sources[] }
const DEFAULT_GOLD_WEIGHT = 1.0, DEFAULT_SILVER_WEIGHT = 0.3, DEFAULT_BRONZE_WEIGHT = 0.5;
// 阶段: 'disabled' (0-50) → 'low' (50-200) → 'full' (200+, 校准后)
```

#### 3.2.11 TurboQuant 压缩 (src/turboquant-compressor.ts)

```typescript
type QuantLevel = 'Q8' | 'Q4' | 'Q2' | 'Q1' | 'Q0';
interface MessageImportance { radius: 0-1, angle: 'system'|'user'|'assistant'|'tool'|'decision', level: QuantLevel }
interface CompressResult {
  messages, originalTokens, compressedTokens, compressionRatio,
  tierCounts: Record<QuantLevel, number>,
  model, contextWindow,
  kvCacheEstimateBytes, ragStored, ragAvailable
}
const MAX_MESSAGES_HARD_CAP = 60, PRESERVE_LAST_N = 30, MAX_INPUT_TOKENS_ABSOLUTE = 32000;
const PRESERVE_RECENT_COUNT = 3, SHORT_CONVERSATION_MAX_MESSAGES = 5, SHORT_CONVERSATION_MAX_TOKENS = 8000;
```

#### 3.2.12 适配器 (src/adapters/cli-provider.ts)

```typescript
type CliInputFormat = 'stdin' | 'arg';
type CliOutputFormat = 'stdout-text' | 'stdout-json';
type CliQuotaType = 'subscription' | 'unlimited' | 'token-bucket';
interface CliProviderConfig {
  command, argsTemplate, modelFlag, inputFormat, outputFormat, timeoutMs, maxTokens,
  env?, workingDir?, maxConcurrent, modelAlias?, healthCheck?, quota?, contextWindow?
}
interface CliProviderResult { content, model, finishReason, usage?: { promptTokens, completionTokens, totalTokens }, latencyMs }
```

#### 3.2.13 Tier 边界 (src/tier-boundaries.ts)

```typescript
type TierBoundaries = [number, number, number, number, number];
const DEFAULT_BOUNDARIES: TierBoundaries = [0.208938, 0.264209, 0.32502, 0.36585, 0.485382];
// midpoints: trivial=0.104, light=0.236, moderate=0.295, heavy=0.345, intensive=0.426, extreme=0.74
```

#### 3.2.14 路由矩阵 (src/routing-matrix.ts)

```typescript
interface ModelDefinition { id, tier: ModelTier, displayName, sizeMB, backendReq[], minMemoryGB, tpsEstimate: { webgpu, webnn, wasm }, maxContextTokens, strengths[], weaknesses[] }
interface MatrixCell { effort, deviceProfile, primaryModel, fallbackModel, cloudOverride, estimatedLatencyMs, estimatedCostCents, qualityScore }
// 6 effort × 5 device = 30 cells
```

#### 3.2.15 Benchmark (src/benchmark-logger.ts)

```typescript
interface BenchmarkLogEntry { timestamp, request_id, prompt?, prompt_hash, prompt_length, tier, routed_model, tokens_in, tokens_out, latency_ms, cost_usd, baseline_cost_usd, savings_usd, savings_pct, provider, status, error_message? }
interface DailyBenchmarkSummary { date, total_requests, total_tokens_in, total_tokens_out, total_cost_usd, baseline_cost_usd, total_savings_usd, savings_pct, tier_distribution, model_distribution }
const BASELINE_MODEL = 'anthropic/claude-opus-4.6';
```

### 3.3 算法 (Algorithms)

#### 3.3.1 25 维特征提取 (src/feature-extractor-v04.ts:185)
**输入**: prompt string  
**输出**: `FeatureVector` (25 维: has_question, has_code, has_imperative, has_arithmetic, has_sequential, has_constraint, has_context, has_architecture, has_design, sentence_count, avg_word_length, question_technical, technical_design, technical_terms, multi_step, has_negation, entity_count, code_block_size, domain_finance, domain_legal, domain_medical, domain_engineering, temporal_references, output_format_spec, prior_context_needed, novelty_score, multi_domain, user_expertise_level, compound_tech, requirement_count, distinct_imperative_verbs, question_count, conjunction_enumeration, scale_quantity_mentions, diagnostic_causal_markers)  
**逻辑**:
1. 9 个 v3.3 启发式信号（binary）
2. 6 个 v3.2 级联结构特征
3. 10 个 v0.4 新增（否定、实体、代码块大小、4 领域、时序、输出格式、前置上下文、新颖度、多领域、用户专业度）
4. Phase 2 分解特征（requirement_count, distinct_imperative_verbs, question_count, conjunction_enumeration, scale_quantity_mentions, diagnostic_causal_markers）

#### 3.3.2 启发式评分 (src/feature-extractor-v04.ts:330)
```
score = lengthScore + structScore + archScore + techScore + codeScore + reasonScore 
      + decompositionScore + scaleScore + diagnosticScore + domainScore + compoundScore + systemBonus
```
其中:
- `lengthScore = min(log1p(wordCount) / log1p(45), 1) * 0.30`
- `archScore = min((has_architecture + has_design) * 0.09, 0.18)`
- `decompositionScore = min(req * 0.012 + verb * 0.014 + q * 0.014 + conj * 0.005, 0.12)`
- `systemBonus = wordCount >= 12 && sysCount >= 4 ? 0.10 : wordCount >= 10 && sysCount >= 3 ? 0.05 : 0`

#### 3.3.3 集成投票 (src/ensemble-voter.ts:278)
**输入**: `{ prompt, heuristicScore, ragSignal?, enableCascade? }`  
**输出**: `{ finalScore, rawScore, tier, confidence, components, cascadeMargin, abstained, method, escalated }`  
**逻辑**:
- **优先级 1**: 序数级联可用且 margin ≥ 0.08 → 用 ordinal cascade
- **优先级 2**: 旧级联权重加载 → sigmoid 评分
- **默认路径 (no cascade)**: `rawScore = ragPresent ? heuristic*0.8 + rag*0.2 : heuristic`, `finalScore = rawScore + bias (historyBias, -0.1 to 0.1)`
- **置信度**: 边界距离派生 (0.5 + margin/0.06 × 0.45, 范围 0.5-0.95)
- **v0.5.2 修复**: 移除 "confidence < 0.55 escalate up one tier" 规则（实测伤害准确度）

#### 3.3.4 消费智能选择 (src/consumption-intelligence.ts:320)
**输入**: tier + options (`{preferProvider?, excludeProviders?, estimatedPromptTokens?, source?, requireVision?}`)  
**输出**: `ConsumptionDecision`  
**逻辑**:
1. **静态优先**: 探活 `staticCfg.provider`，健康则返回（reason: 'provider_preferred'）
2. **动态 fallback**: 探活候选 provider → 过滤（tier 匹配 ±1, contextWindow, maxTokens, supportsReasoning, supportsVision, supportsTools, latency, excludeProviders, isProviderHealthy）→ 评分（size 0-25, capability 0-20, cost 0-20, health 0-25, latency 0-10, prefer 0-5, reputation 0-10, track record 0-5）
3. **vision 请求降级**: 无 in-tier vision → 拓宽到任何 vision-capable（reason: 'vision_widened'）
4. **终极 fallback**: 即使不健康也用静态配置（confidence 0.2, reason: 'static_fallback'）

#### 3.3.5 主动 provider 探活 (src/consumption-intelligence.ts:120)
**逻辑**:
- 缓存 60s (TTL: `PROBE_TTL_MS`)
- **CLI provider**: execSync healthCheck.command
- **HTTP provider**: 步骤 1 GET /models, 步骤 2 发送 max_tokens=5 的 mini chat completion
- 探测 quota 错误: `UsageLimitError`, `quota_exhausted`, `insufficient_quota`, status 429/402, ZAI 200 OK with error body containing limit/quota/rate
- 探测失败 → record429 + mark unhealthy

#### 3.3.6 自愈 Tier 再平衡 (src/consumption-intelligence.ts:742)
- `recordFallbackOutcome(tier, provider, model, success, errorType)`: 失败时如果错误是默认 tier 的 quota 错误 → `rebalanceTier`
- `rebalanceTier`: 选排除 failed provider 的最佳候选 → 写回 v04_config.json
- `checkRecovery` (每 5 min): 如果原 provider 健康度 > 80 且未限流 → 恢复

#### 3.3.7 TurboQuant 压缩 (src/turboquant-compressor.ts:492)
**输入**: `{ messages, targetModel, reservedTokens? }`  
**输出**: `CompressResult`  
**步骤**:
1. **HARD CAP** (msg > 60): 递归删除旧的 assistant+tool_call 链
2. **SHORT CONVERSATION SKIP** (≤5 msg 且 ≤8K tok): 直接返回
3. **Pre-merge**: 合并相邻同角色消息 (排除 tool)
4. **Importance Scoring** (radius): recency(0.25) + tool_result(0.15) + tool_calls(0.20) + decision(0.15) + error(0.10) + system(0.15) + user(0.10) + semantic(0.25)
5. **Structural Invariants**: user ≥ Q4, tool/tool_calls ≥ Q8, system ≥ Q8, last 3 ≥ Q8
6. **Quantize**: budgetRatio 决定 Q8/Q4/Q2/Q1
7. **Q2/Q1/Q0**: 写摘要到 RAG index (Q0 完全丢弃)
8. **Budget verify**: 二阶段按句子截断 → 三阶段丢弃最早 assistant

#### 3.3.8 边界重校准 (src/retraining.ts:62)
**算法**: 网格搜索 5 严格递增 cut point 在 [0.08, 0.7] 范围，步长 0.01，最大化 labeled (score, tier) 对的精确度  
**触发**: `data.length >= max(30, minSamplesPerTier*3)` AND `best.accuracy > accuracyBefore + 0.02`  
**应用**: 写入 tier-boundaries (hot), 持久化到 v04_config.json, reset history cache

#### 3.3.9 序数 Logistic 回归 (src/classifiers/ordinal-logistic.ts)
**模型**: 比例优势累积 logit  
`P(Y <= k) = sigmoid(theta[k] - w·x), k=0..4`  
`P(Y = k) = P(Y <= k) - P(Y <= k-1)`  
**训练**: 默认 900 epochs, lr=0.045, l2=0.001, 5-折 holdout (20%), Platt 标定  
**特性**: 自动加载 v05_ordinal_weights.json, 有序阈值强制, 训练特征名匹配校验

#### 3.3.10 Plan/Act 模式检测 (src/v04-config.ts:313)
**输入**: prompt text  
**输出**: `{ mode: 'plan'|'act'|'auto', confidence, planScore, actScore }`  
**逻辑**:
- **关键词命中** (stem-friendly `\b{kw}\w*`): plan 24 个词 + act 14 个词
- **正则模式**: plan 11 个模式 (`how should/would`, `not sure`, `walk me through`, `before I write/code/...` 等), act 8 个模式 (imperative verbs, throws/error/exception, broken/crashing/failing, doesn't work, silently, shows $0)
- **胜出**: planScore vs actScore 高者胜; max=0 → 'auto'
- **置信度**: min(maxScore/3, 1)

#### 3.3.11 7 阶段消息清洗 (src/moma-gateway.ts:1370)
**Phase 1**: system 消息移到最前  
**Phase 2**: 合并相邻同角色 (排除 tool, 排除 media)  
**Phase 3**: 第一个非 system 消息必须 user；否则提到最前  
**Phase 5**: 跳过空内容；跳过孤立 tool (无 parent assistant); 跳过 null content assistant  
**Phase 6**: 丢弃前导非 system/user  
**Phase 7**: 无 user 消息 → 注入 synthetic `[Continuing conversation — please respond]`  
**Phase 8**: 剥离 orphan tool_calls 数组

#### 3.3.12 训练投票 aleatory 采样 (src/training-mode.ts:89)
**逻辑**:
- 不在 enabled 列表 → false
- 不在 neverAskTiers → false (默认: trivial, extreme)
- 置信度 < alwaysAskBelowConfidence (0.5) → true
- `effectiveRate = max(0.02, baseRate × e^(-votes/50))` (疲劳衰减)
- weightedTiers (moderate, heavy, intensive) × weightedRateMultiplier (2.0)
- 上限 50%
- `Math.random() < rate`

#### 3.3.13 提供方健康评分 (src/provider-quota.ts:333)
```
score = 100
     - rateLimitHits * 15
     - consecutive429s * 25
     - if rpmUsage > 0.8: -30 else if > 0.5: -15 else if > 0.3: -5
     - if throttled: -50
     - if rpdUsage > 0.8: -25 else if > 0.5: -10
     - if tokenUsage > 0.8: -20 else if > 0.5: -8
score = max(0, score)
```

#### 3.3.14 RAG 检索 (src/rag-index.ts:160)
**输入**: `keywords[]`, `maxResults=3`  
**输出**: `RagEntry[]`  
**逻辑**:
- 过滤 TTL (24h) 过期
- 对每条目, 计算 `keywords ∩ (entry.keywords ∪ entry.tags)` 重叠数
- `sort by score desc, slice maxResults`

#### 3.3.15 适配器健康检查 (src/adapters/provider-health.ts)
- **不可用响应检测**: 空 body / auth 错误模式 / API error NNN / 错误式 rate-limit 文本 → 标记 unusable
- **Cooldown**: 连续 2 次 hard failure (auth/transport/timeout) → 5 分钟 cooldown
- **HTTP 状态码分类**: 401/403 → auth, 429/1305/1308 → rate_limit, 5xx → server_error, 其他 → provider_error

#### 3.3.16 复杂度回归 ONNX (public/models/)
- `complexity-regressor-v2.onnx` 和 `q4.onnx` (客户端备用)
- tokenizer: cl100k_base 兼容 (spm.model, tokenizer.json, added_tokens.json)

#### 3.3.17 GPD 合成数据生成 (train.py:233)
- `_gen_gpd_trivial`: 18 模板 + 12 概念 + 5 acronym + 多替换变量
- `_gen_gpd_light`: 10 模板 + 5 path + 5 error + 3 function + 5 command
- 默认生成 25000 trivial + 10000 light 样本

#### 3.3.18 LLM Judge 自评 (src/self-eval.ts:81)
- 快速启发式: token 范围 (0.4) + 长度 (0.2) + 延迟 (0.2) + 重复率 (0.2)
- LLM 异步评估: 10% 采样, prompt 注入 judge 模型 (qwen3.6-plus/extreme tier), 期望 JSON `{adequacy, correct_tier}`
- Anti-circularity: judge 总是比被评 tier 更强

#### 3.3.19 CLI Provider 健康 + quota 调度 (src/adapters/cli-provider.ts:116)
- `isAvailable()`: quota 检查 (subscription window) + 30s 缓存的 execSync healthCheck
- `checkQuota()`: rolling 窗口滚动 + limit 检查
- 串行并发: ConcurrencyLimiter (max=1 for codex-claude, 2 for pi/hermes, 3 for openclaw)
- 子进程: `spawn(command, args, { timeout, env, stdio: [stdinMode, 'pipe', 'pipe'] })`
- ANSI/CR 清洗 + JSON 解析

### 3.4 UI 元素 (TUI Components)

#### 3.4.1 GateSwarm-Bar TUI (cli/src/)
- **Header** (Header.tsx): 边框状态栏, 显示模型数/请求数/错误率/总 token/刷新间隔
- **ProvidersPanel**: 每 provider 5h/weekly/monthly 容量条 + 真实 dashboard 同步指示
- **ModelsPanel**: 按 token 排序的模型列表 + availability
- **TiersMatrix**: 6 tier × 路由矩阵网格
- **RouterConfig**: 实时 ensemble 权重 + tier boundary + 配置
- **ActivityPanel**: 最近 20 决策日志，过滤 health-check 噪声
- **Tab 切换**: `[1]overview [2]providers [3]tiers [4]activity`, `[r]` 刷新, `[q]` 退出
- **Snapshot 模式**: `--once` 输出 JSON dump 到 stdout
- **Mock 模式**: `--mock` 用内置数据（无需服务器）
- **响应式**: TTY 检测 + 非 TTY 自动 snapshot 模式

#### 3.4.2 公共仪表盘 (public/)
- `index.html` + `dashboard.html` + `sw.js` (PWA-ready)

#### 3.4.3 CLI 输出 (gateswarm-cli.ts)
- `bar()`: 进度条字符 (█/▓/▰ + ░)
- `pctBadge()`: 颜色标签 (🟢/🟡/🔴)
- 表格: padEnd 列对齐
- 状态图标: ✅/🚫/⚡/🧠/📦/🔍/🎯/📊/🏥/🔄

### 3.5 集成 (Integrations)

#### 3.5.1 HTTP API 提供方集成

**Z.AI (Zhipu AI / GLM)**
- Base: `https://api.z.ai/api/coding/paas/v4`
- 模型: glm-4.5-air, glm-4.7, glm-4.7-flash, glm-5, glm-5-turbo, glm-5.1
- 5h: 30K tok, weekly: 200K tok, free tier
- 状态: **Healthy (primary)**

**Alibaba Bailian (Coding Plan)**
- Base: `https://coding-intl.dashscope.aliyuncs.com/v1`
- 模型: qwen3.6-plus, qwen3.5-plus, qwen3-coder-plus, qwen3.6-max-preview, qwen4.6
- 5h: 250K tok, weekly: 30K req
- 状态: **Key expired (2026-06-20)** — disabled as primary

**OpenCode Go (opencode.ai/zen)**
- Base: `https://opencode.ai/zen/go/v1`
- 模型: deepseek-v4-flash/pro, qwen3.7-plus/max, kimi-k2.5/2.6, glm-5/5.1, MiniMax-m3/m2.7, mimo-v2.5
- 5h: 50K tok, weekly: 300K tok, monthly: 500K tok
- 状态: **Quota exhausted (resets in 14d)**

**Ollama Cloud (free tier)**
- Base: `https://ollama.com/v1`
- 模型: kimi-k2.5/2.6/2.7-code, glm-5.1, gemma3:12b, qwen3-vl:235b, MiniMax-m2.7/m3, deepseek-v4-pro
- 5h: 200 req / 50K tok, weekly: 2000 req / 500K tok
- 状态: **Healthy**

**Ollama (local CPU)**
- Base: `http://127.0.0.1:11434/v1`
- 模型: qwen2.5:0.5b, qwen2.5:1.5b
- 配额: 无限
- 状态: **Healthy (when running)**

**OpenRouter** (legacy, removed per user request)
- 代码保留但注册被注释

#### 3.5.2 CLI 代理集成 (subprocess)

**Claude Code (`claude-cli`)** - prefix `cc/`
- Command: `claude --print --model {model} -p {prompt}`
- 模型: cc/claude-sonnet-4-6, cc/claude-opus-4-7, cc/claude-opus-4-8, cc/claude-haiku-4-5
- Auth: OAuth (`~/.claude/.credentials.json`) 认证
- Quota: subscription 5h/weekly
- Health check: `bin/cli-health-probe.sh claude-cli` (验证 OAuth + binary)

**OpenAI Codex CLI (`codex-cli`)** - prefix `cx/`
- Command: `codex exec -` (stdin)
- Model flag: `--config`
- 模型: cx/gpt-5.5-codex, cx/gpt-5.4-codex, cx/gpt-5.3-codex, cx/gpt-4.1
- Auth: ChatGPT OAuth (`~/.codex/auth.json`)

**Pi Agent (`pi-agent`)** - prefix `pi/`
- Command: `node ~/.pi/agent/src/index.js -p {prompt} --model {model} --json`
- 模型: pi/qwen3.5-plus, pi/glm-4.7-flash
- No auth required (binary only)

**Hermes Agent (`hermes-agent`)** - prefix `hm/`
- Command: `node /usr/local/lib/hermes-agent/src/agent.js -p {prompt} --model {model} --json`
- 模型: hm/glm-4.7, hm/glm-4.7-flash

**OpenClaw Agent (`openclaw-agent`)** - prefix `oc/`
- Command: `openclaw agent --agent main --model {model} --message {prompt} --timeout 120 --json`
- 模型: oc/bailian/qwen3.5-plus, oc/zai/glm-4.7-flash
- 用途: sessions_spawn

#### 3.5.3 秘密管理集成 (src/secrets/)
- **Sovereign Vault** (推荐): `loadVaultEnv()` 先尝试 vault (container: 'env-gateswarm'), 失败回退 .env
- **SECRETS_SOURCE** = `auto|vault|env`
- **SV_BIN** = path to sovereign-vault binary
- **SV_TIMEOUT_MS** = 30000 默认
- vault 成功 → `Object.assign(process.env, result.vars)` (覆盖)
- .env 失败 → 仅设置 undefined 变量 (不覆盖)

#### 3.5.4 Python 集成 (router.py, train.py)
- 可作为 Python 库使用: `from router import score_prompt`
- HTTP API 模式: `python router.py --serve --port 8080`
- CLI 模式: `python router.py "Write a REST API in Python"`
- 批量模式: `python router.py --file prompts.jsonl --output scored.jsonl`
- 级联权重从 `v32_cascade_weights.json` 自动加载

#### 3.5.5 模型发现集成
- 启动 + 每 15 分钟轮询所有 HTTP provider 的 /models
- Ollama Cloud, ZAI, OpenCode Go: `${baseUrl}/models`
- Ollama local: `${baseUrl}/api/tags` (替换 /v1/models)
- Bailian: 无 list 端点 → 用静态 catalog

### 3.6 工具 (Tools)

#### 3.6.1 CLI 工具 (gateswarm-cli.ts)
**核心命令**:
- `status` / `models` / `model <tier> <model> <provider>` / `reasoning [tier] [on|off]`
- `retrain-freq [N]` (min 50) / `weights [method] [value]` (heuristic/cascade/ragSignal/historyBias)
- `feedback` / `rag` / `retrain` (手动触发)
- `training [agentId] [on|off|labels]` 
- `providers` / `direct <provider> <model> "prompt"`

**Plan/Act 命令**:
- `mode-status` / `mode-set <tier> <field> <value>` (字段: plan_model/plan_provider/plan_max_tokens/plan_enable_thinking)
- `mode-detect "prompt"` / `effort-override <tier> "prompt"` / `resolve <tier> [mode]`

**消费智能命令**:
- `intel` / `consumption [5h|weekly|monthly]` / `quota` / `rediscover`

**运维命令**:
- `ops-guide` / `health` / `version` (检查 7 version stamp 一致性)
- `tui` / `tui --once` / `tui --refresh 5` / `tui --mock` / `tui --tab providers`

#### 3.6.2 运维脚本 (scripts/)
- `start-gateway.sh` / `run-gateway-local.ps1` - 启动网关
- `cli-health-probe.sh` - 认证感知健康探针（验证 binary + auth 凭证）
- `quota-sync.py` - 从 provider dashboard 抓配额
- `cascade-retrain.py` - 级联再训练脚本
- `sv-import-env.mjs` - Vault 导入
- 4 个 demo 脚本: `demo-cli-probe.ps1`, `demo-gateway-probe.mjs`, `demo-pi-gateswarm.ps1`, `demo-pi-interactive.ps1`

#### 3.6.3 评估工具 (eval/)
- `cv.ts` - 交叉验证
- `calibrate.ts` - 校准
- `consistency-check.ts` - 验证 tier 模型存在于 provider catalog
- `feature-report.ts` - 特征重要性报告
- `refit-boundaries.ts` - 手动重拟合 tier 边界
- `train-ordinal.ts` - 训练序数 cascade 模型
- `leaderboard.ts` - 模型排行榜
- `hybrid-routing-eval.ts` - Hybrid 路由评估
- `assess.ts` - 全量评估

#### 3.6.4 Python 训练工具 (llmfit/)
- `llmfit.py` - LLM 微调
- `self_eval.py` - 自评
- `anonymizer.py` - 脱敏路径和标识符
- `datasets/gpd_generator.py` - 合成 prompt 数据集

### 3.7 安全 (Security)

#### 3.7.1 认证 (src/moma-gateway.ts)
- **API Key 格式**: `moma-<32-char hex>` (randomBytes(16).toString('hex'))
- **GATESWARM_REQUIRE_AUTH=true**: 拒绝无效/缺失 key (返回 401)
- **GATESWARM_HOST=0.0.0.0** 警告: 必须同时 REQUIRE_AUTH=true
- **默认**: loopback (127.0.0.1) 绑定, REQUIRE_AUTH=false

#### 3.7.2 鉴权感知 CLI 健康探针 (bin/cli-health-probe.sh)
- 同时验证 binary 存在 AND OAuth 凭证存在
- 防止 `codex --version` 通过但实际无 auth 的假阳性

#### 3.7.3 不可用响应检测 (src/adapters/provider-health.ts)
- 防止以 "rate limit" 主题为内容的合法响应被误判为 quota 错误
- 短文本 (≤500 字符) + 错误式开头 + rate limit 词 → 标记 unusable
- HTTP-level 429 独立处理

#### 3.7.4 媒体内容保护 (moma-gateway.ts:114-194)
- 不可逆的 base64 → `[image]`/`[audio]`/`[video]` 降级
- Vision-capable 模型接收原始 content 数组
- Text-only 模型（包括所有 CLI 代理）永远见不到 base64
- 防止 raw base64 进入 prompt 或 scoring

#### 3.7.5 输入验证
- 严格 tier 校验: `effort_override` 必须是 6 个之一 (400 if invalid)
- `mode` 仅接受 'plan' | 'act'
- Tool message 严格验证: tool_call_id 必须有 parent assistant

### 3.8 性能 (Performance)

#### 3.8.1 TurboQuant 压缩 v3.6
- **HARD CAP 60 msg**: 防止无限 session 增长 (Pi 980+ msg case)
- **SHORT SKIP** (≤5 msg + ≤8K tok): 完全跳过压缩
- **Q* 结构不变量**: 防止 strict APIs (DeepSeek, ZAI) 拒绝
- **多阶段 budget verify**: 二次截断 + 三次丢弃

#### 3.8.2 防抖写入 (v0.5.3)
- agent-registry: 1s SAVE_DEBOUNCE_MS 合并并行请求
- provider-quota: 10s 定时器
- model-matrix: 5s 定时器
- consumption-tracker: 30s 定时器
- feedback/rag: 60s 定时器
- flushPending() 在 shutdown 强制同步写入

#### 3.8.3 问候快路径 (moma-gateway.ts:1054)
- 长度 < 30 + 匹配 GREETING_RE → 跳过 RAG/scoring/compression
- 节省 50-200ms 分类 + 1-3s 上下文处理
- 10s 超时 (AbortSignal.timeout)

#### 3.8.4 Trivial Fast-Path (moma-gateway.ts:1282)
- 当 effort=trivial + model ≤ 2B + user prompt < 60 chars + provider=ollama
- 剥离 system 和 history, 只发最后 user 消息
- 0.5B 模型在 CPU 处理 4K system prompt 需 20s; 剥离后 5-10× 加速

#### 3.8.5 主动探活缓存 (consumption-intelligence.ts)
- 60s TTL 避免每次请求都探活
- 健康 provider 不重复探活

#### 3.8.6 流转发 (forwardToProvider)
- 90s 空闲超时 (v0.5.3 调整)
- 120s 单请求超时
- SSE 事件分块读取 + 解码器
- Heartbeat (CLI 10s): 防止 SSE 客户端超时

#### 3.8.7 全局重试预算 (moma-gateway.ts:1629)
- 60s 总额度防止长时间级联失败
- 优于 5 分钟连续 429 浪费

### 3.9 部署 (Deployment)

#### 3.9.1 npm scripts
- `npm start` → `tsx src/moma-gateway.ts --port 8900`
- `npm run dev` → tsx watch
- `npm test` → vitest run
- `npm run eval:*` → 各种评估
- `npm run ssl:*` → 半监督学习管线
- `npm run check:types` / `lint` → TypeScript 编译检查
- `npm run check:consistency` → 配置一致性

#### 3.9.2 npm bin
- `gateswarm` → `./src/gateswarm-cli.ts`
- `gateswarm-gateway` → `./src/moma-gateway.ts`

#### 3.9.3 Python 部署
- `Dockerfile` (Python 3.12-slim): `pip install -r requirements.txt`, `CMD ["python", "-u", "router.py"]`
- `Dockerfile.inference` (legacy)

#### 3.9.4 systemd (推荐)
- canonical unit: `moma-gateway.service`
- 部署文档: `docs/GATEWAY_QUICKSTART.md`
- 需要: `GATESWARM_ROOT` env var (健康探针定位)

#### 3.9.5 TUI 部署
- `cli/dist/cli.js` 通过 `tsc` 编译
- `gateswarm tui` 启动

#### 3.9.6 端口配置
- 默认 8900, 通过 `--port` 参数覆盖
- 默认 host: 127.0.0.1 (loopback)
- GATESWARM_HOST=0.0.0.0 启用网络监听（需 REQUIRE_AUTH）

#### 3.9.7 数据持久化目录
- `data/feedback/entries.json` (10000 上限)
- `data/rag/index.json` (10000 上限, 24h TTL)
- `data/training/votes.json` (5000 上限)
- `data/training/agent-configs.json`
- `data/training/tier-accuracy.json`
- `data/agent-registry.json` (debounced)
- `data/provider-quota.json` (10s 刷新)
- `data/model-matrix.json` (5s 刷新)
- `data/consumption-history.json` (30s 刷新, 35 天保留)
- `data/benchmark-logs/<YYYY-MM-DD>.jsonl` (按日)
- `data/organic/labeled.jsonl` (训练 organic 标签)
- `data/quota-sync.json` (从 dashboard 抓取)
- `v04_config.json` (CLI 修改时立即保存)

### 3.10 测试 (Testing)

#### 3.10.1 Vitest 配置
- `vitest` v3.1+
- 测试文件: `tests/*.test.ts`
- Python 测试: `tests/test_router.py`

#### 3.10.2 测试覆盖
- agent-registry 防抖
- plan-act 路由
- intent engine
- router
- routing-matrix
- ensemble-voter
- feature-extractor-v04
- heuristic-boundaries
- ordinal-logistic
- cli-providers
- provider-health
- benchmark
- assertiveness (路由决断性)
- score-drift-guard
- subsystem-smoke
- training-mode
- plan-act
- hybrid eval helpers/rubric/sample/warm-fixtures

#### 3.10.3 CI (.github/workflows/ci.yml)
- typecheck + test on push/PR

---

## 4. 技术栈 (Tech Stack)

### 4.1 Languages
- **TypeScript 5.7+** (ESM modules, target ES2022)
- **Python 3.12** (legacy router.py, training tools)
- **JSX/TSX** (Ink TUI)

### 4.2 Frameworks & Runtimes
- **Node.js ≥18** (engines field)
- **tsx** (development runner)
- **Vitest 3.1** (testing)
- **Vite** (TUI build)
- **Ink** (React for CLI)
- **HTTP** (node:http built-in)

### 4.3 Core Dependencies
- `dotenv ^17.4.2` - .env 加载
- `idb-keyval ^6.2.1` - IndexedDB 包装（optional）
- `tiktoken ^1.0.22` - OpenAI 分词器
- `@huggingface/transformers ^3.4.0` (optional) - 客户端本地推理
- `onnxruntime-web ^1.21.0` (optional) - 客户端 ONNX 推理

### 4.4 Dev Dependencies
- `@types/node ^22`
- `tsx ^4.19`
- `typescript ^5.7`
- `vitest ^3.1`

### 4.5 Python Dependencies
- `scipy >=1.11`
- `numpy >=1.24`
- `scikit-learn >=1.3`
- `datasets >=2.14`
- `requests >=2.31`

### 4.6 Data Formats
- JSON (配置, 持久化, API)
- JSONL (benchmark 日志, organic 标签)
- OpenAI Chat Completions API (compatible)
- Server-Sent Events (SSE) for streaming

### 4.7 部署方式
- **Standalone**: `npm start` 或 `tsx src/moma-gateway.ts`
- **systemd**: `moma-gateway.service` (推荐用于 VPS)
- **Docker**: `Dockerfile` (Python), `Dockerfile.inference`
- **CLI/TUI**: `gateswarm tui` 启动 Ink 终端 UI
- **PWA**: `public/index.html` + `sw.js` (dashboard)

---

## 5. 关键代码片段 (Key Code Snippets)

### 5.1 9 阶段请求管线核心 (moma-gateway.ts:1194-1266)

```typescript
// 1. Score complexity via ensemble
const v04Score = await scoreIntentV04(promptText);
let score = v04Score.value;
let rawScore = v04Score.rawValue ?? v04Score.value;
let effort: EffortLevel = v04Score.tier ?? 'moderate';

// 2. Effort override bypass (v0.5.7)
if (effortOverride) {
  score = tierMidpoints()[effortOverride as EffortLevel];
  rawScore = score;
  effort = effortOverride as EffortLevel;
}

// 3. Session continuity tracking
const sessionId = body.session_id || body.session 
  || `${agent.id}:${promptText.slice(0, 100)}`;
const modeDetection = detectIntentMode(promptText);
const activeMode: IntentMode = modeOverride ?? modeDetection.mode;

// 4. Consumption Intelligence selects model (with vision filter)
const estimatedPromptTokens = estimateTokens(
  messages.map(m => messageContentToText(m?.content)).join('\n')
);
let decision: ConsumptionDecision;
try {
  decision = await consumptionIntelligence.selectModel(effort, {
    estimatedPromptTokens,
    source: 'request',
    requireVision: requestModalities.vision,
  });
} catch {
  // Static fallback
  decision = { /* ... */ };
}

let providerId = decision.provider;
let model = decision.model;

// 5. Plan mode override
const rawTier = getTierModel(effort);
if (activeMode === 'plan' && rawTier?.plan_model) {
  providerId = rawTier.plan_provider || decision.provider;
  model = rawTier.plan_model;
  console.log(`📋 [${agent.name}] Plan mode override: ${providerId}/${model}`);
}
```

### 5.2 问候快路径与 Trivial 快路径 (moma-gateway.ts:1054-1187, 1282-1293)

```typescript
// 问候快路径：超短问候直接路由到 trivial 模型
const GREETING_RE = /^\s*(hi|hello|hey|yo|sup|good\s+(morning|afternoon|evening)|thanks?|thank\s+you|ok(?:ay)?|bye|cya|gm|gn)\s*[.!]?\s*$/i;
if (!effortOverride && promptText && GREETING_RE.test(promptText) && promptText.length < 30) {
  const trivialCfg = getConfig().tier_models.trivial;
  const trivialHealth = providerQuota.shouldSwitch(trivialCfg.provider);
  if (!trivialHealth.shouldSwitch && trivialCfg && agentRegistry.isHttpProvider(trivialCfg.provider)) {
    // 跳过所有评分/RAG/压缩，直接 POST
    const resp = await fetch(`${baseUrl}/chat/completions`, {
      method: 'POST',
      body: JSON.stringify({
        messages: greetingMessages,  // 只有最后 user 消息
        model: cleanModel,
        stream: clientWantsStream,
      }),
      signal: AbortSignal.timeout(10000),
    });
    // ... 转发响应, 返回所有 routing transparency headers
  }
}

// Trivial 快路径：超短 prompt + 极小模型 → 剥离 system 和 history
const modelSizeB = (() => {
  const m = String(model).match(/(\d+\.?\d*)b(?:[-_]|\b|$)/i);
  return m ? parseFloat(m[1]) : 999;
})();
const userPromptLen = messageContentToText(lastUserMsg?.content).length;
const isTrivialFastPath = 
  effort === 'trivial' && modelSizeB <= 2.0 && 
  userPromptLen > 0 && userPromptLen < 60 && providerId === 'ollama';
if (isTrivialFastPath) {
  workingMessages = [lastUserMsg]; // 仅最后 user 消息
  // 0.5B 模型在 CPU 处理 4K system prompt 需 20s; 剥离后 5-10× 加速
}
```

### 5.3 集成投票与历史偏差 (ensemble-voter.ts:278-378)

```typescript
export function ensembleVote(input: EnsembleInput): EnsembleVote {
  const heuristic = input.heuristicScore;
  
  // 1. 尝试序数级联（如果可用）
  const ordinal = input.enableCascade !== false ? ordinalCascadeScore(input.prompt) : null;
  const legacyCasc = (!ordinal && input.enableCascade !== false && cascadeWeights.length > 0)
    ? cascadeScore(input.prompt) : -1;
  const casc = ordinal ? ordinal.score : legacyCasc;
  const cascadeMargin = ordinal?.margin;
  const cascadeAbstainMargin = input.cascadeAbstainMargin ?? 0.08;
  const abstained = ordinal ? ordinal.margin < cascadeAbstainMargin : false;
  
  // 2. Phase 4: history bias 默认 0 (warm-store ablation)
  const bias = weights.historyBias > 0 ? calcHistoryBias() : 0;
  
  // 3. RAG 可选信号（不存在时不注入中性 0.5）
  const ragPresent = typeof input.ragSignal === 'number';
  const rag = ragPresent ? (input.ragSignal as number) : null;
  
  let rawScore, finalScore, confidence, method;
  if (casc < 0 || casc === undefined || abstained) {
    // 默认路径：无级联
    method = abstained ? 'ensemble-v0.4' : 'heuristic-fallback';
    if (ragPresent && weights.ragSignal > 0 && !abstained) {
      rawScore = heuristic * 0.8 + (rag as number) * 0.2;
    } else {
      rawScore = heuristic; // 完整动态范围
    }
    rawScore = Math.max(0, Math.min(1, rawScore));
    finalScore = Math.max(0, Math.min(1, rawScore + bias));
    confidence = abstained && ordinal
      ? Math.min(confidenceFromMargin(heuristic), Math.max(0.5, ordinal.confidence * 0.8))
      : confidenceFromMargin(finalScore);
  } else {
    // 完整 ensemble（训练级联可用）
    method = 'ensemble-v0.4';
    const ragVal = ragPresent ? (rag as number) : heuristic;
    const wSum = weights.heuristic + weights.cascade + weights.ragSignal;
    const wH = wSum > 0 ? weights.heuristic / wSum : 1;
    const wC = wSum > 0 ? weights.cascade / wSum : 0;
    const wR = wSum > 0 ? weights.ragSignal / wSum : 0;
    rawScore = Math.max(0, Math.min(1, heuristic * wH + casc * wC + ragVal * wR));
    finalScore = Math.max(0, Math.min(1, rawScore + bias));
  }
  
  // 4. v0.5.2 修复：移除 confidence<0.55 escalate 规则
  const tier = abstained ? scoreToEffort(heuristic) : scoreToEffort(finalScore);
  return { finalScore, rawScore, tier, confidence, components, cascadeMargin, abstained, method, escalated: false };
}
```

### 5.4 7 阶段消息清洗 (moma-gateway.ts:1370-1485)

```typescript
const sanitizeMessages = (msgs: any[]): any[] => {
  if (msgs.length <= 1) return [...msgs];

  // Phase 1: System 消息移到最前
  const systemMsgs = msgs.filter(m => m.role === 'system');
  const nonSystemMsgs = msgs.filter(m => m.role !== 'system');

  // Phase 2: 合并相邻同角色（排除 tool 和 media）
  const merged: any[] = [];
  for (const msg of nonSystemMsgs) {
    const prevMsg = merged.length > 0 ? merged[merged.length - 1] : null;
    if (prevMsg && prevMsg.role === msg.role && msg.role !== 'tool' 
        && !messageHasMediaParts(prevMsg) && !messageHasMediaParts(msg)) {
      const prevContent = typeof prevMsg.content === 'string' ? prevMsg.content : JSON.stringify(prevMsg.content);
      const currContent = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
      prevMsg.content = prevContent + '\n---\n' + currContent;
    } else {
      merged.push({ ...msg });
    }
  }

  // Phase 3: User-first
  const result = [...systemMsgs];
  if (merged.length > 0 && merged[0].role !== 'user') {
    const firstUserIdx = merged.findIndex(m => m.role === 'user');
    if (firstUserIdx > 0) {
      result.push(merged[firstUserIdx], ...merged.slice(firstUserIdx + 1));
    } else {
      result.push(...merged);
    }
  } else {
    result.push(...merged);
  }

  // Phase 5: 跳过空内容 + 孤立 tool
  const valid: any[] = [];
  const hasToolCallParent = new Set<string>();
  for (const msg of result) {
    if (msg.role === 'assistant' && msg.tool_calls) {
      for (const tc of msg.tool_calls) if (tc.id) hasToolCallParent.add(tc.id);
    }
  }
  for (const msg of result) {
    if (!msg.content && !(msg.role === 'assistant' && msg.tool_calls)) continue;
    if (typeof msg.content === 'string' && msg.content.trim() === '' && !(msg.role === 'assistant' && msg.tool_calls)) continue;
    if (msg.role === 'assistant' && msg.content === null && !msg.tool_calls) continue;
    if (msg.role === 'tool') {
      if (msg.tool_call_id && !hasToolCallParent.has(msg.tool_call_id)) continue;
      const prevRole = valid.length > 0 ? valid[valid.length - 1].role : null;
      if (prevRole !== 'assistant' && prevRole !== 'tool') continue;
    }
    valid.push(msg);
  }

  // Phase 6: 前导清理
  while (valid.length > 0 && valid[0].role !== 'system' && valid[0].role !== 'user') {
    valid.shift();
  }

  // Phase 7: 无 user 则注入
  if (!valid.some(m => m.role === 'user')) {
    const sysEnd = valid.findIndex(m => m.role !== 'system');
    const insertIdx = sysEnd < 0 ? valid.length : sysEnd;
    valid.splice(insertIdx, 0, { role: 'user', content: '[Continuing conversation — please respond]' });
  }

  // Phase 8: 剥离 orphan tool_calls
  const validToolIds = new Set<string>();
  for (const msg of valid) {
    if (msg.role === 'tool' && msg.tool_call_id) validToolIds.add(msg.tool_call_id);
  }
  for (const msg of valid) {
    if (msg.role === 'assistant' && msg.tool_calls?.length > 0) {
      const covered = msg.tool_calls.every((tc: any) => validToolIds.has(tc.id));
      if (!covered) {
        delete msg.tool_calls;
        if (msg.content === null || msg.content === undefined || (typeof msg.content === 'string' && msg.content.trim() === '')) {
          msg.content = '[tool use]';
        }
      }
    }
  }
  return valid;
};
```

### 5.5 TurboQuant 量化与结构不变量 (turboquant-compressor.ts:268-317)

```typescript
function quantize(importance: MessageImportance, budgetRatio: number, position: number, total: number): QuantLevel {
  const { radius, angle } = importance;
  
  // Q8: System + 最近 3 条
  const isRecent = position >= total - PRESERVE_RECENT_COUNT;
  if (angle === 'system' || isRecent) return 'Q8';
  
  // v3.6 结构不变量
  if (angle === 'tool') return 'Q8'; // tool 结果是结构锚
  if (angle === 'user') {
    // user 消息最低 Q4 (不能丢)
    if (radius > 0.5) return 'Q8';
    return 'Q4';
  }
  
  // 预算驱动量化
  if (budgetRatio > 0.7) {
    if (radius > 0.5) return 'Q8';
    if (radius > 0.3) return 'Q4';
    if (radius > 0.15) return 'Q2';
    return 'Q1';
  }
  if (budgetRatio > 0.4) {
    if (radius > 0.6) return 'Q8';
    if (radius > 0.4) return 'Q4';
    if (radius > 0.2) return 'Q2';
    return 'Q1';
  }
  if (budgetRatio > 0.2) {
    if (radius > 0.7) return 'Q8';
    if (radius > 0.5) return 'Q4';
    if (radius > 0.3) return 'Q2';
    return 'Q1';
  }
  // 严格：仅最高重要性保留 Q4
  if (radius > 0.8) return 'Q4';
  if (radius > 0.5) return 'Q2';
  return 'Q1';
}
```

### 5.6 消费智能选择 + Vision 拓宽 (consumption-intelligence.ts:430-462)

```typescript
// 动态候选 + tier 匹配 + vision fallback
const candidates = allModels.filter(m => {
  const providerConfig = agentRegistry.getProvider(m.provider);
  if (!providerConfig) return false;
  const baseUrl = agentRegistry.getProviderBaseUrl(m.provider);
  if (!baseUrl && !['ollama', 'ollama-cloud'].includes(m.provider)) return false;
  if (agentRegistry.isHttpProvider(m.provider) && !agentRegistry.getProviderApiKey(m.provider)) return false;
  if (excludeSet.has(m.provider)) return false;
  if (!this.isProviderHealthy(m.provider)) return false;
  const tierIdx = this.tierRank(tier);
  const modelTierIdx = this.tierRank(m.recommendedTier);
  if (modelTierIdx !== tierIdx && modelTierIdx !== tierIdx + 1 && modelTierIdx !== tierIdx - 1) return false;
  if (tierIdx <= 1 && modelTierIdx >= 3) return false;
  if (m.contextWindow < reqs.minContextWindow) return false;
  if (m.maxTokens < reqs.minMaxTokens) return false;
  if (reqs.needsReasoning && !m.supportsReasoning) return false;
  if (reqs.needsVision && !m.supportsVision) return false;
  if (reqs.needsTools && !m.supportsTools) return false;
  if (m.avgLatencyMs > reqs.maxLatencyMs && m.avgLatencyMs > 0) return false;
  return true;
});

if (candidates.length === 0) {
  // MoMA: 视觉请求降级前先拓宽到任何视觉模型
  if (reqs.needsVision) {
    const visionAny = allModels.filter(m =>
      m.supportsVision && !excludeSet.has(m.provider) &&
      this.isProviderHealthy(m.provider) && 
      agentRegistry.getProviderBaseUrl(m.provider) &&
      (!agentRegistry.isHttpProvider(m.provider) || agentRegistry.getProviderApiKey(m.provider))
    );
    if (visionAny.length > 0) {
      visionAny.sort((a, b) => this.scoreModel(b, tier, reqs, options?.preferProvider) - this.scoreModel(a, tier, reqs, options?.preferProvider));
      const pick = visionAny[0];
      const decision: ConsumptionDecision = {
        provider: pick.provider, model: pick.id, tier,
        reason: 'vision_widened',  // 任何视觉模型胜过完美 tier 匹配
        confidence: 0.6,
        // ...
      };
      return decision;
    }
  }
  // 终极 fallback: 即使不健康也用静态配置
  return { /* static_fallback, confidence: 0.2 */ };
}
```

### 5.7 主动 Provider 探活 (consumption-intelligence.ts:164-202)

```typescript
// HTTP provider: 步骤 1 GET /models
const modelsUrl = baseUrl.replace(/\/+$/, '') + '/models';
const resp = await fetch(modelsUrl, {
  method: 'GET', headers,
  signal: controller.signal,
});
if (!resp.ok) {
  const body = await resp.text().catch(() => '');
  const isQuotaError = body.includes('UsageLimitError') || body.includes('usage limit') ||
    body.includes('quota_exhausted') || body.includes('insufficient_quota') ||
    resp.status === 429 || resp.status === 402;
  if (isQuotaError) {
    providerQuota.record429(providerId);
    console.log(`🔴 [Intel Probe] ${providerId}: QUOTA EXHAUSTED — marking throttled`);
  }
  return { healthy: false, error };
}

// 步骤 2: Deep probe 发送 max_tokens=5 的 mini chat
const deepResp = await fetch(chatUrl, {
  method: 'POST', headers,
  body: JSON.stringify({
    model: probeModel,
    messages: [{ role: 'user', content: 'hi' }],
    max_tokens: 5,  // 5 tokens, 空响应 = 真实错误
  }),
  signal: deepController.signal,
});
const deepBody = await deepResp.text().catch(() '');
// ZAI 返回 200 OK 但 body 包含错误
const isQuotaError = deepBody.includes('UsageLimitError') || deepBody.includes('usage limit') ||
  deepBody.includes('GoUsageLimitError') || deepBody.includes('Limit Exhausted') ||
  deepBody.includes('Rate limit reached') || deepBody.includes('Weekly/Monthly Limit') ||
  !deepResp.ok && (deepResp.status === 429 || deepResp.status === 402);

if (isQuotaError) {
  providerQuota.record429(providerId);
  console.log(`🔴 [Intel Probe] ${providerId}: DEEP PROBE FAILED — quota exhausted`);
  return { healthy: false, error: `quota_exhausted: ${deepBody.slice(0, 120)}` };
}
```

### 5.8 Plan/Act 模式检测 (v04-config.ts:313-332)

```typescript
export function detectIntentMode(promptText: string): { mode: IntentMode; confidence: number; planScore: number; actScore: number } {
  const lower = promptText.toLowerCase().trim();
  if (!lower) return { mode: 'auto', confidence: 0, planScore: 0, actScore: 0 };

  let planScore = keywordHits(lower, PLAN_KEYWORDS);  // 24 词
  let actScore = keywordHits(lower, ACT_KEYWORDS);    // 14 词
  for (const re of PLAN_PATTERNS) { if (re.test(lower)) planScore++; }  // 11 pattern
  for (const re of ACT_PATTERNS) { if (re.test(lower)) actScore++; }    // 8 pattern

  const maxScore = Math.max(planScore, actScore);
  if (maxScore === 0) return { mode: 'auto', confidence: 0, planScore: 0, actScore: 0 };
  const confidence = Math.min(maxScore / 3, 1);
  const mode: IntentMode = planScore > actScore ? 'plan' : actScore > planScore ? 'act' : 'auto';
  return { mode, confidence, planScore, actScore };
}

// PLAN_PATTERNS 示例
const PLAN_PATTERNS = [
  /\bhow (should|would|do|can) (i|we|you)\b/,
  /\bshould (i|we)\b/,
  /\bwhat'?s the best way\b/,
  /\bnot sure (how|whether|if|which|what)\b/,
  /\bhelp me (decide|choose|think|figure)\b/,
  /\bwalk me through (the )?(options|possibilities|tradeoffs|approaches)\b/,
  /\bbefore (i|we) (write|code|build|implement|start|begin)\b/,
];

// ACT_PATTERNS 示例
const ACT_PATTERNS = [
  /^(write|create|build|implement|fix|add|remove|update|change|make|generate|refactor|...)\b/,
  /\b(throws?|throwing|returns?|returning|raises?) (an? )?(error|500|404|exception|null|undefined|nan|wrong|empty)\b/,
  /\b(is|are|was|were|keeps?|looks?|shows?|appears?|stays?|goes?) (broken|crashing|failing|not working|blank|empty|null|undefined|grey|gray|frozen|stuck)\b/,
  /\b(doesn'?t|does not|won'?t|wont|can'?t|cannot|unable to|fails? to|stopped|stops) (work|compile|build|run|load|render|upload|...)\b/,
];
```

### 5.9 边界再训练网格搜索 (retraining.ts:62-75)

```typescript
function optimizeBoundaries(data: LabeledScore[]): { bounds: number[]; accuracy: number } {
  const grid: number[] = [];
  for (let v = 0.08; v <= 0.7; v += 0.01) grid.push(Number(v.toFixed(3)));
  let best = { bounds: getTierBoundaries(), accuracy: accuracyFor(getTierBoundaries(), data) };
  
  // 5 嵌套循环搜索 5 严格递增 cut points
  for (const b0 of grid.filter(v => v < 0.3))
    for (const b1 of grid.filter(v => v > b0 && v < 0.4))
      for (const b2 of grid.filter(v => v > b1 && v < 0.5))
        for (const b3 of grid.filter(v => v > b2 && v < 0.6))
          for (const b4 of grid.filter(v => v > b3 && v < 0.7)) {
            const acc = accuracyFor([b0, b1, b2, b3, b4], data);
            if (acc > best.accuracy) best = { bounds: [b0, b1, b2, b3, b4], accuracy: acc };
          }
  return best;
}

// 应用条件: best.accuracy > accuracyBefore + 0.02
// 同时: 数据量 >= max(30, minSamplesPerTier * 3)
```

### 5.10 CLI Provider subprocess dispatch (cli-provider.ts:226-267)

```typescript
private execSubprocess(args: string[], stdinPrompt?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    // arg-based CLI: 继承 stdin (避免 "no stdin data" 警告)
    // stdin-based CLI: pipe 模式以便 feed prompt
    const stdinMode = this.cfg.inputFormat === 'stdin' ? 'pipe' : 'ignore';
    
    const child = spawn(this.cfg.command, args, {
      timeout: this.cfg.timeoutMs,  // 5min for codex-claude
      env: { ...process.env, ...(this.cfg.env ?? {}) },
      cwd: this.cfg.workingDir,
      stdio: [stdinMode, 'pipe', 'pipe'],
    });
    
    let stdout = '', stderr = '';
    if (stdinPrompt && this.cfg.inputFormat === 'stdin') {
      child.stdin?.write(stdinPrompt, 'utf-8', () => child.stdin?.end());
    } else if (this.cfg.inputFormat === 'stdin') {
      child.stdin?.end();
    }
    
    child.stdout?.on('data', (d: Buffer) => { stdout += d.toString(); });
    child.stderr?.on('data', (d: Buffer) => { stderr += d.toString(); });
    
    child.on('close', (code) => {
      if (code !== 0 && stderr.trim()) {
        reject(new Error(`CLI exited with code ${code}: ${stderr.trim().slice(0, 500)}`));
      } else {
        resolve(stdout);
      }
    });
    child.on('error', (err) => reject(new Error(`CLI process error: ${err.message}`)));
  });
}

// 串行并发限制
class ConcurrencyLimiter {
  async acquire(): Promise<void> {
    if (this.max <= 0 || this.running < this.max) {
      this.running++;
      return;
    }
    return new Promise((resolve) => { this.queue.push(resolve); });
  }
  release(): void {
    if (this.queue.length > 0) {
      this.queue.shift()!();
    } else {
      this.running--;
    }
  }
}
```

---

## 6. 集成点 (Integration Points)

### 6.1 客户端集成

#### 6.1.1 OpenAI 兼容客户端（标准方式）

```yaml
# Pi agent ~/.pi/agent/models.json
{
  "providers": {
    "moma": {
      "baseUrl": "http://localhost:8900/v1",
      "apiKey": "moma-<jack-key>",  # 32 字符 hex
      "api": "openai-completions",
      "models": [{
        "id": "gateswarm",
        "name": "GateSwarm MoMA v0.5.6"
      }]
    }
  }
}
```

```bash
# 标准 curl
curl http://localhost:8900/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer moma-<key>" \
  -d '{"model":"gateswarm","messages":[{"role":"user","content":"..."}]}'
```

#### 6.1.2 高级用法：直接路由

```bash
# 旁路评分直接路由
curl http://localhost:8900/v1/chat/completions -d '{
  "model": "cc/claude-sonnet-4-6",  # 通过前缀解析
  "messages": [...]
}'

# 或 body 字段
{
  "model": "gateswarm",
  "direct_route": { "provider": "claude-cli", "model": "cc/claude-sonnet-4-6" },
  "messages": [...]
}

# 或 headers
X-Direct-Provider: claude-cli
X-Direct-Model: cc/claude-sonnet-4-6
```

#### 6.1.3 Plan/Act 模式

```bash
# 强制 plan 模式
{
  "model": "gateswarm",
  "messages": [...],
  "mode": "plan"  # 或 "act"
}

# 或 header
X-Mode: plan
```

#### 6.1.4 Effort 强制覆盖

```bash
{
  "model": "gateswarm",
  "messages": [...],
  "effort_override": "heavy"  # trivial|light|moderate|heavy|intensive|extreme
}

# 或 header
X-Effort-Override: heavy
```

#### 6.1.5 多模态（视觉）

```bash
{
  "model": "gateswarm",
  "messages": [{
    "role": "user",
    "content": [
      { "type": "text", "text": "What color is this?" },
      { "type": "image_url", "image_url": { "url": "data:image/png;base64,..." } }
    ]
  }]
}
# 响应头: X-Modality: text+vision
# 自动路由到 ollama-cloud/gemini-3-flash-preview (vision-capable)
```

### 6.2 自定义 agent 注册

```bash
# 通过 CLI
gateswarm status
# 返回 agent ID + API key

# 或通过 HTTP API
curl -X POST http://localhost:8900/v1/agents/register \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-coding-agent",
    "provider": "moma",
    "tierProfile": "quality",  // cost-optimized | balanced | quality | benchmark | claude-quality | codex-heavy
    "benchmarkEnabled": true
  }'
# 响应: { apiKey: "moma-<hex>", connection: { base_url, api_key } }
```

### 6.3 TUI 监控

```bash
# 交互式 TUI（需要 TTY）
gateswarm tui
# 4 标签: overview | providers | tiers | activity
# 快捷键: [1-4] 切换, [r] 刷新, [q] 退出

# Snapshot 模式（脚本）
gateswarm tui --once | jq '.consumption.totalFiveHour'
# Mock 模式（脱机）
gateswarm tui --mock

# 5s 刷新
gateswarm tui --refresh 5

# 启动指定标签
gateswarm tui --tab providers
```

### 6.4 运维查询

```bash
# 健康检查
curl http://localhost:8900/health
# 11-point check
gateswarm health

# 配置状态
gateswarm status
curl http://localhost:8900/v04/status

# 消费报告
gateswarm consumption
gateswarm consumption weekly
curl http://localhost:8900/v05/intel/consumption

# 配额
gateswarm quota
curl http://localhost:8900/v05/intel/quota

# 智能推荐
gateswarm intel
curl http://localhost:8900/v05/intel

# Tier swap
curl http://localhost:8900/v05/intel/swaps

# 直接重路由
curl -X POST http://localhost:8900/v1/chat/completions -d '{
  "direct_route": {"provider": "claude-cli", "model": "cc/claude-sonnet-4-6"},
  "messages": [...]
}'

# 边界再训练
curl -X POST http://localhost:8900/v04/retrain

# 模型再发现
curl -X POST http://localhost:8900/v05/intel/rediscover
```

### 6.5 数据流（典型 chat completion）

```
1. Client → POST /v1/chat/completions
   ↓
2. authenticate(apiKey) → agentRegistry → agent
   ↓
3. handleChatCompletion:
   a. parseBody, detectRequestModalities
   b. Check training vote reply (recordDetectedVoteReply)
   c. Mode override (body.mode | X-Mode)
   d. Effort override (body.effort_override | X-Effort-Override)
   e. Direct route (body.direct_route | body.model prefix | X-Direct-*)
   f. Greeting fast-path (if pattern + < 30 chars)
   g. scoreIntentV04(promptText) → tier, score, confidence
   h. consumptionIntelligence.selectModel(tier, requireVision) → provider, model
   i. Plan mode override (if activeMode=plan)
   j. Trivial fast-path (if effort=trivial, model≤2B, prompt<60)
   k. turboQuantCompress(messages, model)
   l. RAG injection (queryRag with keywords)
   m. Continuity injection (if model switch)
   n. 7-phase message sanitization
   o. CLI dispatch OR HTTP retry chain
   ↓
4. Response:
   - Token usage, latency
   - X-Mode, X-Mode-Confidence
   - X-Tier, X-Score, X-Routed-Model, X-Routed-Tier
   - X-Routing-Method, X-Routing-Reason
   - X-Modality
   - X-Training-Vote (if applicable)
   ↓
5. Post-response:
   - recordFeedback
   - selfEvaluate (async, 10% LLM judge)
   - calibrateBronze / calibrateSilver
   - appendVotePromptToCompletion (if training mode + sampled)
   - addRagEntry
   - updateContinuity
   - benchmarkLogger.log (if enabled)
   - consumptionTracker.recordUsage
   - providerQuota.recordRequest / recordSuccess
   - modelMatrix.recordUsage / recordLatency
```

### 6.6 集成范式

**HTTP API Gateway 范式**: 标准 OpenAI 兼容端点 + 扩展头/字段。  
**Subprocess Dispatch 范式**: CLI 代理用 OAuth 凭证（claude-cli, codex-cli），无凭证（pi, hermes, openclaw）。  
**Vault 范式**: 密钥在 Sovereign Vault 容器，.env 作为自动回退。  
**本地优先回退链范式**: tier 静态配置 + fallback_models 链 + consumption intelligence 动态候选。  
**自学习范式**: 反馈 → LLM judge → boundary retraining → live hot-reload。  
**自愈范式**: 配额耗尽 → 主动探活 → tier rebalance → 5min 恢复检查。  
**多模态范式**: 视觉请求 → vision-capable 过滤 → tier-band widening → 不可用则 [image] placeholder 降级。

---

## 7. 总能力数量统计 (Total Capability Count)

### 7.1 API 端点统计

| 类别 | 数量 | 端点 |
|---|---|---|
| OpenAI 兼容 | 5 | POST /v1/chat/completions, GET /v1/models, GET /v1/providers, POST /v1/direct/chat, POST /v1/score |
| 代理管理 | 4 | GET/POST /v1/agents[/register], GET/PATCH /v1/agents/:id |
| 健康/指标 | 3 | GET /health, GET /metrics, GET /metrics/:agentId |
| v0.4 集成/反馈 | 7 | GET /v04/status, GET /v04/feedback, POST /v04/retrain, GET /v04/training, POST /v04/training/enable, POST /v04/training/vote, POST /v04/training/vote/reply |
| v0.5 消费智能 | 10 | GET /v05/intel, /v05/intel/last-decision, /v05/intel/ops-guide, /v05/intel/models, /v05/intel/providers, POST /v05/intel/rediscover, GET /v05/intel/consumption, /v05/intel/usage, /v05/intel/balance, /v05/intel/swaps, /v05/intel/sync, /v05/intel/quota |
| v0.5 CLI | 1 | GET /v05/cli |
| v0.6 Plan/Act | 2 | POST /v06/mode/detect, POST /v06/resolve |
| **HTTP API 端点总数** | **~32** | (含 REST 路径组合) |

### 7.2 模块/类/函数统计

| 类别 | 数量 |
|---|---|
| TypeScript 源文件 (.ts/.tsx) | 41 |
| Python 源文件 (.py) | 7 |
| 配置文件 (.json) | 5 |
| Markdown 文档 | 21 |
| 测试文件 | 22 |
| 评估脚本 | 12 |
| 运维脚本 | 10 |
| Docker 文件 | 2 |
| GitHub Actions | 1 |
| 注释行（"API endpoint" - 完整描述） | 32+ |
| 数据模型（interface/type） | 30+ |
| 算法（详细分析） | 19 |
| UI 组件 (TUI) | 6 |
| 集成 provider | 11 (5 HTTP + 5 CLI + 1 Vault) |
| CLI 工具命令 | 30+ |
| 安全功能 | 5 |
| 性能优化 | 7 |
| 部署方式 | 6 |
| 持久化数据文件 | 11 |

### 7.3 能力分类统计

| 能力类别 | 数量 |
|---|---|
| **API** | 32 个 HTTP 端点 |
| **数据模型** | 30+ interface/type 定义 |
| **算法** | 19 个详细算法（特征提取、评分、投票、选择、压缩、训练、检测等） |
| **UI** | 6 个 TUI 组件 + 仪表盘 + CLI 输出格式 |
| **集成** | 11 个 provider（5 HTTP + 5 CLI + 1 Vault） |
| **工具** | 30+ CLI 命令 + 10 脚本 + 12 评估 |
| **安全** | 5 个安全机制（认证、鉴权探针、不可用检测、媒体保护、输入验证） |
| **性能** | 7 个性能优化（压缩、防抖、快路径、缓存、流转发、重试预算、KV 估算） |
| **部署** | 6 种方式（standalone, systemd, Docker, TUI, PWA, npm bin） |
| **测试** | 22 个测试文件 + CI |

### 7.4 关键统计数字

- **版本号**: v0.5.6 (Routing Transparency)
- **项目代码量**: 41 TS 文件 + 7 Python 文件 = 约 8,000+ 行业务代码
- **25 维特征**: 9 启发式 + 6 级联 + 10 新增
- **6 路由 tiers**: trivial / light / moderate / heavy / intensive / extreme
- **5 量化级别**: Q8 / Q4 / Q2 / Q1 / Q0
- **7 阶段消息清洗**: System-first → Merge → User-first → Orphan filter → Leading cleanup → User injection → Tool cleanup
- **9 阶段请求管线**: 解析 → 评分 → 模式 → 旁路 → 问候快路径 → 智能选模型 → 压缩 → RAG → 续接
- **3 标签源**: gold (1.0) / silver (0.3) / bronze (0.5)
- **3 配额窗口**: 5h / weekly / monthly
- **5 CLI provider**: claude / codex / pi / hermes / openclaw
- **6 HTTP provider**: zai / bailian / opencodego / ollama / ollama-cloud / (openrouter removed)
- **11 持久化数据文件**: feedback, rag, votes, agent-configs, tier-accuracy, agent-registry, provider-quota, model-matrix, consumption-history, benchmark-logs, quota-sync
- **7 version stamp**: 服务报告 / package.json / cli/package.json / v04_config.json / moma-gateway.ts / systemd unit / ~/.pi/agent/models.json

---

## 8. 关键架构亮点 (Key Architectural Highlights)

1. **MoMA (Mixture of Multimodal Agents)**: 不是单一模型服务所有请求，而是按需混搭本地、HTTP 云、CLI 代理 + 视觉/文本多模态。

2. **25 维特征 + 集成投票**: 三个 v3.3/v3.2/v0.4 阶段演进，Phase 2 加入分解/规模/诊断特征解决 mid-band 准确度问题。

3. **静态优先 + 主动探活 + 自愈**: tier 静态配置为主，动态发现为补，主动 60s 缓存探活防止 cascade 失败，5min 恢复检查自动还原。

4. **TurboQuant v3.6 结构不变量**: user ≥ Q4, tool/tool_calls ≥ Q8, system ≥ Q8, 解决 strict API 拒绝问题；HARD CAP 60 防 runaway session。

5. **5 标签源 3 权重校准**: gold (100% 人) / silver (RAG consensus) / bronze (LLM judge)，按 agreement rate 校准权重，Phase 1→2→3 渐进启用。

6. **多模态 + 视觉拓宽**: vision 请求自动路由到 vision-capable 模型，tier-band 无 vision 时拓宽到任何 vision 模型，`X-Modality` 响应头暴露。

7. **Plan/Act 模式 (v0.5.2)**: 同一 tier 不同模式可路由到不同模型（plan 用轻量推理模型，act 用快/便宜的 HTTP 模型），关键词+pattern 检测 + 显式 override。

8. **7 阶段消息清洗 + 工具调用保护**: DeepSeek 等 strict API 拒绝 orphan tool_calls，Phase 8 剥离 + [tool use] 占位。

9. **防抖写入 (v0.5.3)**: 多文件 5-60s 防抖减少磁盘 IO，shutdown 强制同步。

10. **7 version stamp 一致性检查**: `gateswarm version` 检查 package.json / cli/package.json / v04_config.json / 服务报告 / systemd / Pi 配置全对齐。

11. **鉴权感知 CLI 健康探针**: 验证 binary + OAuth 凭证存在，避免假阳性。

12. **Sovereign Vault 集成**: vault-first 加载密钥，.env 自动回退，SECRETS_SOURCE 控制回退行为。

13. **Routing Transparency (v0.5.6)**: 5 个响应头暴露 X-Tier/X-Score/X-Routed-Model/X-Routed-Tier/X-Routing-Method/X-Routing-Reason，ActivityPanel 过滤 health-check 噪声。

14. **greeting + trivial 双重快路径**: 极短问候直接 trivial 模型，跳过评分；trivial+小模型剥离 system/RAG 节省 5-10× 时间。

15. **CLI provider loop guard**: 防止 CLI agent 路由回自身造成无限循环。

---

## 9. 总结 (Summary)

GateSwarm MoMA Router 是一个**生产级的自优化 LLM 路由网关**，核心价值在于：
- **零代码改动**: 标准 OpenAI 兼容，base_url 替换即可接入。
- **自动优化**: 25 维特征 + 集成投票 + 反馈回路 + 边界再训练持续提升准确度。
- **多模态 + 多模态提供方**: 视觉/文本/工具调用需求自动路由到合适模型。
- **高韧性**: 健康检查、quota tracking、自动回退、provider 替换、5min 自愈。
- **完整可观测性**: 32 个端点、6 TUI 面板、benchmark 日志、消费报告、配额 dashboard、训练统计。
- **可扩展架构**: 加新 provider 加 catalog 条目即可；加新 model tier 修改 v04_config.json 即可。

项目设计成熟，代码质量高（防抖、热重载、原子写、结构化日志、详细注释），是一个**完整的、企业级的、可立即投产的 LLM 路由解决方案**。
