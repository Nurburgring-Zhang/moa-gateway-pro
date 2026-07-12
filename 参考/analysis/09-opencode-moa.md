# 09-opencode-moa 深度分析

> 源项目: `D:\MoA Gateway Pro\参考\extracted\09-opencode-moa\opencode-moa-main`
> 分析时间: 2026-07-13
> 文件总数: 33 个 (文档 17 + 源码 7 个 agent + 2 个 command + 1 个 JSON 配置 + 1 个 install.sh + 1 个 check 脚本 + 4 个 docs 文档 + 1 个 paper + 1 个 bibliography + 3 个 experiment + 3 个 example)
> 代码规模: 核心 agent 7 个, 共 ~1100 行 system prompt; 设计文档 2184 行
> 协议: Apache 2.0

---

## 1. 项目概述

**opencode-moa** 是一个**完全构建在 OpenCode 内部**的多模型编排器 (multi-agent orchestrator), 用 **100% 声明式 markdown + JSON** 实现, **零 bash、零 Python、零外部运行时**。它的核心思路是:

1. 一个 primary agent (`orquestador`) 作为编排主体, 通过 OpenCode 原生的 `task` tool 并行调用多个 subagent
2. 让 N 个 AI 模型**同时**对同一个 prompt 各自生成提案 (`propuesta-glm`, `propuesta-kimi`, `propuesta-mimo`, 等)
3. 一个 `validador` 实际**执行**提案里的命令做经验性验证
4. 一个 `evaluador` 用 5 维客观标准打分
5. 一个 `sintetizador` 排名、整合、选赢
6. 可选 iterate 模式, 跨多轮迭代直到**收敛** (score 提升幅度低于阈值) 或触达 `max_iteraciones`

整个系统**没有**任何 shell 脚本是项目必需的 (除了 `install.sh` 是辅助)。所有协调逻辑、决策、错误恢复都在 agent 的 system prompt 文字里。

**灵感来源**: 论文 *Mixture-of-Agents* (Together AI 2024, arXiv:2406.04692) — 表明异质 LLM 组合优于任何单一模型。opencode-moa 借鉴其精神但用水平迭代代替垂直层叠。

**作者**: Israel Roldan (单一作者项目, 6+ 个月从 bash 版本迭代而来)

**当前版本**: v0.3 beta (2026-07-12)

**关键设计哲学**:
- **Native-first**: 全部基于 OpenCode 原语 (`task`, `bash`, `webfetch`, `read`, `write`, `glob`, `grep`, `todowrite`)
- **Declarative parallelism**: 在同一个 response 里放 N 个 `task` 调用即并行
- **Per-section viability**: 复杂提案有多个技术节, 单节失败不淘汰整篇
- **Opt-in disqualification**: 默认保留 ⚠️ 警告, 不强行淘汰
- **4-layer config merge**: 运行时参数 > project JSON > user JSON > 默认值
- **Convergence with anti-loop**: `umbral_convergencia` + `max_iteraciones` + 回归即停, 无穷循环不可能

---

## 2. 核心模块清单

### 2.1 仓库结构

```
opencode-moa-main/
├── README.md                              260 行, 项目总览与徽章
├── CHANGELOG.md                           233 行, 三个版本 (0.1/0.2/v0.3)
├── ROADMAP.md                             91 行, v0.3 → v2.0 路线
├── LICENSE                                201 行, Apache 2.0 (作者 Israel Alberto Roldan Vega)
├── install.sh                             70 行, 用户级 / 项目级安装脚本
│
├── docs/                                  设计与研究文档
│   ├── installation.md                    493 行, 4 种安装方法 (user/project/VPS/Docker)
│   ├── proposals/
│   │   └── 001-orquestador-nativo-opencode.md   2184 行, 23 节完整设计提案
│   ├── research/
│   │   ├── iterations-analysis.md         226 行, 5 个真实项目的多模型证据
│   │   └── experiments/
│   │       ├── README.md                  42 行, 实验日志索引
│   │       ├── 2026-07-11-rust-gui-app.md 335 行, 12 模型 v0.2.0-beta 第 1 次实验
│   │       └── 2026-07-12-rust-gui-app-v3.md 379 行, v0.3 sintesis_central 验证
│   └── papers/
│       ├── BIBLIOGRAPHY.md                114 行, 论文引用 (MoA/MetaGPT/AutoGen/DSPy/...)
│       └── DRAFT-multi-model-orchestration.md 491 行, 论文草稿 §6.2/§6.3 验证
│
├── examples/                              使用示例
│   ├── auth-jwt-rest-api.md               215 行, REST API 完整 iterate 流程
│   ├── smoke-test-colores.md              109 行, 烟雾测试示例
│   └── opencode.jsonc.test-template       11 行, headless 测试权限模板
│
├── opencode-moa/                          可安装包 (核心)
│   ├── README.md                          109 行, bundle 安装说明
│   ├── AGENTS.md                          318 行, 运营/post-mortem (含模型绑定冲突复盘)
│   ├── orquestador.json                   39 行, 主配置 (schema v1.1, 16 字段)
│   ├── agents/                            7 个 agent 文件
│   │   ├── orquestador.md                 491 行, primary 编排器
│   │   ├── propuesta-glm.md               99 行, GLM-5.1 提案器
│   │   ├── propuesta-glm-52.md            99 行, GLM-5.2 提案器
│   │   ├── propuesta-kimi.md              99 行, Kimi K2.6 提案器
│   │   ├── propuesta-kimi-k27-code.md     99 行, Kimi K2.7 Code 提案器
│   │   ├── propuesta-mimo.md              99 行, MiMo v2.5 Pro 提案器
│   │   ├── propuesta-mimo-v25.md          99 行, MiMo v2.5 base 提案器
│   │   ├── propuesta-deepseek.md          99 行, DeepSeek V4 Pro 提案器
│   │   ├── propuesta-deepseek-flash.md    99 行, DeepSeek V4 Flash 提案器
│   │   ├── propuesta-qwen37-plus.md       99 行, Qwen3.7 Plus 提案器
│   │   ├── propuesta-minimax.md           99 行, MiniMax-M3 提案器 (用户 plan)
│   │   ├── evaluador.md                   108 行, 评分器 (temp=0.0)
│   │   ├── sintetizador.md                218 行, 综合器 (4 种模式, temp=0.1)
│   │   └── validador.md                   182 行, 验证器 (有 bash 白名单)
│   └── commands/                          2 个 slash command
│       ├── orquestar.md                   40 行, /orquestar
│       └── orquestar-iterate.md           31 行, /orquestar-iterate
│
└── scripts/
    └── check-no-forbidden-model.sh        68 行, CI 检查禁用模型
```

### 2.2 可安装包 (bundle) 详解

**13 个 agent 文件** 按职责分类:

| 类别 | 数量 | 文件 | 模式 |
|---|---|---|---|
| 编排器 (primary) | 1 | `orquestador.md` | `mode: primary` |
| 提案器 (proposer) | 10 | `propuesta-*.md` | `mode: subagent` |
| 评估器 (evaluator) | 1 | `evaluador.md` | `mode: subagent` |
| 综合器 (synthesizer) | 1 | `sintetizador.md` | `mode: subagent` |
| 验证器 (validator) | 1 | `validador.md` | `mode: subagent` (受 bash 白名单限制) |

**2 个 command** (用户从 TUI 直接调用的 slash):
- `/orquestar` — 单次 9 步流程
- `/orquestar-iterate` — 多轮迭代直到收敛

---

## 3. 详细能力列表

### 3.1 编排能力 (Orchestrator Capabilities)

#### 3.1.1 多阶段流水线编排 (10 步 + 1 选配 + 迭代)
- **Step 0 — Initialization**:
  - 解析 `$ARGUMENTS` (user prompt + id + 7 个 flag)
  - 验证 id 匹配 `^[a-z0-9][a-z0-9-]{2,29}$`
  - 4 层配置 merge: 运行时参数 > `./orquestador.json` > `~/.config/opencode/orquestador.json` > 硬编码默认
  - 对 `modelos_a_competir` 中每个 model 派生 `id_corto` 并验证 `propuesta-{id_corto}.md` 存在
  - 计算迭代号 N = `max(existing iters) + 1`
  - `bash: mkdir -p out/{id}/iter-{N}`
  - `todowrite` 跟踪 9 个步骤
  - `--force` 标志会先 `rm -rf` 目标目录
  - `max_wall_clock_minutes > 0` 强制停止并写 "STOPPED" sumario
- **Step 1 — 并行提案生成**:
  - 对 `modelos_a_competir` 中**每个**模型启动一个 `task(propuesta-{id_corto})`
  - 全部 `task` 在**同一 response** 里发出 (声明式并行)
  - prompt 包含: `{user_prompt}`, `{id}`, `{N}`, `{model}`
  - 目标文件: `out/{id}/iter-{N}/01-propuesta-{id_corto}.md`
  - 包含 `=== EMPIRICAL TESTING (encouraged but bounded) ===` 块: 鼓励但限制 `cargo check --quiet` (5-10x 快于 build), 允许完整 `cargo build` 仅当 < 5 min, 禁止 `cargo tauri build` 与 GUI 工具
  - 包含 `=== FEEDBACK-AWARE ITERATION ===` 块: 当 N > 1 时, 要求子 agent **先读** `iter-1/03-calificacion-evaluador.md`, `04-clasificacion.md`, `05-propuesta-integrada.md` 后再写
  - 12-18 次内部工具调用, 单提案 60-180s, 50-500 行
  - **6 分钟硬超时**: 单 subagent 6 min 内不收敛即 abort 该 subagent, 不阻塞整体
  - 可选: `if_mejoras_tecnicamente_similares_a_otras=true` 时, top-5 提案栈重叠 > 80% 触发"非传统角度"创新指令
- **Step 2 — 经验性验证 (并行, 可选)**: 当 `validacion_empirica=true`, 每个提案由一个 `validador` 子 agent 处理, 输出 `02-validacion-{id_corto}.md`
- **Step 3 — 评估 (单模型, 默认)**: 一次 `task(evaluador)`, 写 `03-calificacion-evaluador.md`; 当 `multi_eval=true` 时, fan-out 到 `multi_eval_modelos` 然后**取平均**
- **Step 4 — 分类 (含可配置淘汰)**: `task(sintetizador)` mode=classification, 写 `04-clasificacion.md`; 按 `descalificar_fallida` 决定 ⚠️ vs DESCALIFICADA
- **Step 5 — 改进 (3 种模式)**: 配置 `step_5_modo`:
  - `sintesis_central` (默认): 一次 `task(sintetizador)` mode=integrated_synthesis, 产 `05-propuesta-integrada.md`
  - `self_improve` (legacy): N 个 `task(propuesta-{id_corto})` mode=improvement, 产 `05-mejorada-{id_corto}.md`
  - `skip`: 跳过
- **Step 6 — 改进后验证 (可选)**: 对 integrated/改进过的候选项再跑 validador
- **Step 7 — 再评估**: 对改进后的候选项再跑 evaluador, 写 `07-calificacion-final.md`
- **Step 8 — 选赢**: `task(sintetizador)` mode=final_selection, 写 `08-ganador.md`
- **Step 9 — Sumario**: **编排器自己用 `write` 写**, 不调子 agent
  - 最终分数、赢的模型、淘汰列表、迭代指标、收敛状态、cost 归属表
- **Step 10 — 跨迭代综合 (选配)**: 当 `sintesis_final=true`, 写 `out/{id}/10-sintesis-cross-iter.md`, 含 Convergence / Best of each iter / Recommended adoption / Convergence trajectory

#### 3.1.2 迭代模式 (Convergence Loop)
- 仅在 `/orquestar-iterate` 触发
- 决策伪码:
  ```
  if N >= max_iteraciones: STOP
  if N == 1: prev_score = 0, ALWAYS continue
  mejora = score_actual - prev_score
  if mejora >= umbral_convergencia: CONTINUE(N+1)
  else: STOP (含 regression)
  ```
- 三个保险: `max_iteraciones` 上限 / 阈值收敛 / 回归即停
- 无穷循环不可能 (硬上限 + 阈值)
- `filter_low_performers` 机制: 从 iter-2 开始, iter-N-1 分数低于 `descalificar_debajo_de` 的模型被丢弃, 但至少保留 `keep_minimo` (默认 3)
- **反馈感知迭代**: iter-N 提案器在写之前必须读 iter-1 的 03/04/05 文件, 让跨迭代知识传递

#### 3.1.3 4 层 Smoke Test 控制
| 优先级 | 来源 | 行为 |
|---|---|---|
| 1 (高) | 命令行 `--smoke-test={true\|false\|auto}` | 强制覆盖 |
| 2 | `./orquestador.json` (project) | 覆盖 user-level |
| 3 | `~/.config/opencode/orquestador.json` (user) | 覆盖 default |
| 4 (低) | 硬编码 `false` | 默认 |
- `"auto"` 启发式: 提示 < 50 字符且不包含 `design`/`implement`/`build`/`create` → 启用
- 启用时强制 prompt = "List the 7 colors of the rainbow in order"

#### 3.1.4 配置 16 字段 schema (orquestador.json v1.1)
| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `version` | string | "1.1" | semver |
| `modelos_a_competir` | array | 8 个模型 | 提案模型列表 |
| `modelo_objetivo` | string | `minimax-coding-plan/MiniMax-M3` | 评估/验证/综合器模型 |
| `max_iteraciones` | int 1-10 | 3 | 最大迭代数 |
| `umbral_convergencia` | number | 0.5 | 收敛阈值 |
| `validacion_empirica` | bool | true | 是否跑 step 2/6 |
| `descalificar_fallida` | bool | false | 严格淘汰 |
| `smoke_test` | bool\|"auto" | false | 烟雾测试模式 |
| `step_5_modo` | string | "sintesis_central" | step 5 模式 |
| `sintesis_final` | bool | false | 是否启用 step 10 |
| `sintesis_final_modelo` | string | = modelo_objetivo | step 10 使用的模型 |
| `multi_eval` | bool | false | 多评估器开关 |
| `multi_eval_modelos` | array | [] | 多评估器模型列表 |
| `max_wall_clock_minutes` | int | 0 (unlimited) | 硬时间上限 |
| `filter_low_performers` | object | {30, iter_>=2, 3} | 弱模型过滤 |
| `if_mejoras_tecnicamente_similares_a_otras` | bool | false | 创新激发开关 |

#### 3.1.5 通信与错误处理
- 编排器在每个 step 前后输出 `[STEP N]` / `[STEP N ✓]` 日志
- 子 agent 失败重试 1 次, 仍失败 abort 并给清晰消息
- 配置文件 malformed 立即 abort
- 单 subagent 文件缺失立即 abort
- 配置解析错误回显给用户: "ERROR: no `propuesta-{id_corto}.md` found for model `{model}`..."

### 3.2 提案生成能力 (Proposer Capabilities)

10 个 `propuesta-*.md` agent 共享相同结构 (每个 99 行, 仅 `model:` 不同):

#### 3.2.1 两种工作模式
- **Mode "generation" (Step 1)**: 收到 "Generate a proposal for: {prompt}. ID: {id}. Iteration: {N}. Model: {model}. Write to out/{id}/iter-{N}/01-propuesta-{modelo_id}.md"
  1. 读 user prompt
  2. 分析技术领域
  3. 产出完整提案, 含: Executive summary / Proposed architecture / Tech stack / Installation commands / Considerations (Security, Scalability, Maintainability) / Effort estimation / References
  4. 用 `write` 落盘
  5. 返回 1 段摘要
- **Mode "improvement" (Step 5 self_improve)**: 读 03/04/02 反馈, 改进原提案写 05-mejorada

#### 3.2.2 提案结构规范
- 最小 50 行, 最大 500 行
- 必含可执行 shell 命令 (供 validador 验证)
- 必含 7 节: Executive summary, Architecture, Tech stack, Installation commands (代码块), Considerations, Effort, References
- 跨模型实例命令:
  - `propuesta-glm.md`, `propuesta-kimi.md`, `propuesta-mimo.md` 用 `npm install ...` (Node)
  - `propuesta-deepseek-flash.md`, `propuesta-glm-52.md`, `propuesta-kimi-k27-code.md`, `propuesta-mimo-v25.md`, `propuesta-qwen37-plus.md` 用 `cargo build ...` (Rust)

#### 3.2.3 抗幻觉 (Anti-Hallucination) 原则
- 必须有具体可执行命令
- 不确定时建议替代方案而非断言
- 可用 `webfetch` 验证 URL/API 存在
- 每个技术决定必须有 justification

#### 3.2.4 10 个模型绑定 (硬编码 frontmatter)
| 文件 | model | temperature |
|---|---|---|
| `propuesta-glm.md` | `opencode-go/glm-5.1` | 0.7 |
| `propuesta-glm-52.md` | `opencode-go/glm-5.2` | 0.7 |
| `propuesta-kimi.md` | `opencode-go/kimi-k2.6` | 0.7 |
| `propuesta-kimi-k27-code.md` | `opencode-go/kimi-k2.7-code` | 0.7 |
| `propuesta-mimo.md` | `opencode-go/mimo-v2.5-pro` (关键: 不是 minimax-m3, 见 §3.9 事故) | 0.7 |
| `propuesta-mimo-v25.md` | `opencode-go/mimo-v2.5` | 0.7 |
| `propuesta-deepseek.md` | `opencode-go/deepseek-v4-pro` | 0.7 |
| `propuesta-deepseek-flash.md` | `opencode-go/deepseek-v4-flash` | 0.7 |
| `propuesta-qwen37-plus.md` | `opencode-go/qwen3.7-plus` | 0.7 |
| `propuesta-minimax.md` | `minimax-coding-plan/MiniMax-M3` | 0.7 |

### 3.3 评估能力 (Evaluator Capabilities)

`evaluador.md` (108 行):
- **单模型评估器** (默认, 决策有实证依据见 iterations-analysis.md §3.1)
- 温度 **0.0** (绝对客观)
- 模型 `minimax-coding-plan/MiniMax-M3` (用户 plan)
- **5 维评估** (每维 0-10, 总 50):
  - **TQ** Technical Quality: 架构/栈/技术决策是否合理
  - **CO** Completeness: 是否覆盖全部 prompt; 章节有无缺; 命令是否可执行
  - **AP** Applicability: 可否按现状实现; 依赖是否合理; 实施计划是否清晰
  - **SE** Security: 鉴权/敏感数据/输入验证
  - **IN** Innovation: 创意/差异化/是否使用现代能力
- **经验性可调**: 当 validador 给了"per-section viability"时按节数降分:
  - 0 节 ❌ → AP 满 (10)
  - 1 节 ❌ (共 3-4 节) → AP = 5-7
  - 2 节 ❌ → AP = 2-4
  - 3+ 节 ❌ → AP = 1
  - 全局 viability < 2/10 → AP = 1
- **可选淘汰**: `descalificar_fallida=true` + 全局 viability < 3/10 → 标 DESCALIFICADA
- **抗偏**: 显式禁膨胀分数; 即使自己生成的提案也评; 每个分数必须引用 1-2 句提案原文
- **多评估器模式** (`multi_eval=true`): fan-out 到 `multi_eval_modelos` 列表中的模型, **取平均**; 跳过 03, 直接写 04
- **输出格式**: 80+ 行 markdown, 含 consolidated table (Pos/TQ/CO/AP/SE/IN/Total/Viability/Notes), 每个提案独立 5 维分析 + 一般观察

### 3.4 综合能力 (Synthesizer Capabilities)

`sintetizador.md` (218 行) — **4 种模式**:
- 温度 0.1 (创造与稳定的折中)
- 模型 `minimax-coding-plan/MiniMax-M3`

#### 3.4.1 Mode "classification" (Step 4)
- 读 03/01/02-*
- 按 `descalificar_fallida` 标记 ❌ vs ⚠️
- 按总分排序, 平局按 id_corto 字典序
- 输出 04-clasificacion.md: Ranking 表 (🥇🥈🥉 + ~~DESCALIFICADA~~) + Analysis 段 + Disqualifications 段 + Warnings 段

#### 3.4.2 Mode "integrated synthesis" (Step 5, sintesis_central)
- 读 12 个原始提案 + 03 + 04 + 02-*
- 识别 TOP 3 by total score + TOP 3 by viability
- 提取各提案的**独特技术贡献** (例: "multi-viewport API for the popup", "cargo-free binary static artifact")
- **检测 CONVERGENT 想法** (3+ 提案独立提及 → 验证为强信号, 逐字保留)
- **检测 CONFLICTING 选择** (例如不同 crate), 按 evaluator 信号 + 命令可编译性 + viability 选强证据
- 写出 200-400 行的统一自包含提案, 格式与原始提案一致
- 加 `## Source attribution` 节: 每个引用标 `propuesta-{modelo}.md` + 行号
- 加 `## Why this beats the field` 节: 跨参 03 文件, 列出: 赢的提案的哪些弱点被设计选择解决, 输家的哪些弱点被规避
- **明确约束**: 不引入"任何原始提案都没提过"的想法 (curation, not invention)

#### 3.4.3 Mode "final selection" (Step 8)
- 读 07 + 05-mejorada-*/05-integrada + 04 + 06-*
- 整合候选 (integrated) 与 12 原稿一同排名
- 高分但 viability < 5/10 不能赢
- 选赢并给 justification
- 输出 08-ganador.md: Winner/Total score/Viability + Decision analysis + Winning proposal 1 段摘要

#### 3.4.4 Mode "cross-iteration synthesis" (Step 10)
- 读所有 iter 的 08/09/05/07
- 4 节: Convergence (idea N → M 演进) / Best of each iter (表) / Recommended adoption (单一文件推荐) / Convergence trajectory (markdown 表, 标 avg/top score/std dev)
- 输出 10-sintesis-cross-iter.md

#### 3.4.5 综合器原则
- 温度 0.1
- 显式 criterion 而非直觉
- 每个决定必须有可见 justification
- Curation 而非 invention

### 3.5 经验验证能力 (Validator Capabilities)

`validador.md` (182 行) — **唯一带 bash 白名单的子 agent**:
- 温度 0.0 (绝对客观)
- 模型 `minimax-coding-plan/MiniMax-M3`
- **重要权限白名单** (覆盖 ~40 个 glob 规则):

**Allow 命令**:
- 工具检测: `command -v *`, `* --version`, `*-version`, `which *`
- 语法检查: `shellcheck *`, `node --check *`, `python -c *`, `python3 -c *`
- 包管理: `pip show *`, `npm ls`, `npm list *`, `cargo --list`, `npm install *`, `pip install *`
- 构建: `cargo build *`, `cargo check *`, `go build *`, `go vet *`, `make *`
- 输出: `echo *`, `printf *`, `cat *`, `ls *`, `head *`, `tail *`, `wc *`, `file *`, `stat *`
- 目录: `mkdir *`, `mkdir -p *`, `cp *`, `mv *`, `touch *`
- 文本: `grep *`, `awk *`, `sed *`
- 网络: `curl *`, `wget *`
- 时间: `sleep *`, `date *`

**Ask (需用户批准)**: `*` 默认, `rm *`

**Deny**: `edit`

**Allow 其他**: `webfetch`, `read`, `write`

- **per-section viability 报告流程**:
  1. 读完整提案
  2. 识别 SECTIONS (架构/安装命令/API 端点/代码片段/...)
  3. 提取每节可验证元素 (完整 shell 命令/依赖与版本/外部 API URL/环境假设/代码片段)
  4. 用 bash 执行 (每命令 30s 超时, 超时即 SKIP)
  5. 用 webfetch 查官方文档
  6. 报告**每节**的 viability, 不只是全局
- **输出格式**: Executive summary 表 (Sections/Viable/Warnings/Not viable/Commands OK/FAILED/SKIP/全局 viability) + Verdict (✅/⚠️/❌) + Viability per section 表 + Detail per section (每节一个 subsection) + Investigation with webfetch + Suggested changes + Conclusion
- **沙箱规则**: 禁止 `rm -rf /`, `mkfs`, `dd of=/dev/...` 等破坏性命令
- 最小 30 行, 6 个必含节

### 3.6 命令入口能力 (Slash Commands)

#### 3.6.1 `/orquestar` (40 行)
- frontmatter: `agent: orquestador`, `model: minimax-coding-plan/MiniMax-M3`, `subtask: true`
- 参数: `$1` = prompt, `$2` = id (可选, 缺省 slugify)
- 7 个可选 flag:
  - `--smoke-test={true|false|auto}` 覆盖 smoke_test
  - `--max-iter=N` 覆盖 max_iteraciones
  - `--convergence=X` 覆盖 umbral_convergencia
  - `--force` 删除 out/{id}/iter-{N}/
  - `--no-validation` 关闭 step 2/6
  - (隐含的) `--step-5-modo={sintesis_central|self_improve|skip}`
  - (隐含的) `--multi-eval={true|false}`
- 行为: 读+merge 配置 → 验证 → step 1→2→3→4→5→6→7→8→9 → 显示 sumario
- 例子: `/orquestar "Design REST API" auth-jwt`

#### 3.6.2 `/orquestar-iterate` (31 行)
- 同 frontmatter 结构
- 行为: 同 /orquestar, 但 step 9 后做收敛判断, 满足条件即启 iter N+1
- 例子: `/orquestar-iterate --max-iter=5 --convergence=0.3 "..." complex`

### 3.7 安装与部署能力 (Installation)

#### 3.7.1 `install.sh` (70 行 bash)
- 自动检测 `XDG_CONFIG_HOME` 或默认 `~/.config/opencode`
- 验证 `opencode-moa/bundle/` 存在
- 创建 `~/.config/opencode/agents/` 和 `~/.config/opencode/commands/`
- 复制 `agents/*.md` (10 文件) 和 `commands/*.md` (2 文件)
- 复制 `orquestador.json` (**冲突时询问** y/N, 默认保留现有)
- 输出 Next steps: `ls ~/.config/opencode/agents/` + 烟雾测试命令
- 幂等: 可重复运行

#### 3.7.2 4 种安装方法 (`docs/installation.md`)
| 方法 | 适用 | 难度 |
|---|---|---|
| A. User-level | 个人跨项目 | 易 |
| B. Project-level | 单项目隔离 | 易 |
| C. VPS / SSH | 远程 headless 服务器 | 中 |
| D. Docker | 可复现 / CI-CD | 中 |

#### 3.7.3 4 层配置 merge
- 运行时 arg > `./orquestador.json` > `~/.config/opencode/orquestador.json` > 默认
- 合并规则: 仅出现键覆盖, 缺省保留
- 允许用户级一次性安装, 项目级选择性覆盖

#### 3.7.4 故障排除 (Troubleshooting)
- 缺 agent 文件 → 复制模板 + 改 `model:` 字段
- Permission denied → 修 validador.md 的 `permission.bash` glob
- Config 不加载 → `python3 -m json.tool` 验证
- Step 2/3 权限挂起 → 文档化 OpenCode 上游 bug #35073, 给 2 个 workaround:
  - user-level `bash: allow` (有安全警告, 仅研究机用)
  - 直接绕过 validador/evaluator 用 build agent 跑 step 5 (`setsid opencode run --auto --pure --print-logs --log-level=INFO`)
- `--command` 返回 `UnknownError` → 用 `--agent` + positional 替代
- Headless 模式 external_directory 挂起 → `examples/opencode.jsonc.test-template` 模板

### 3.8 文档与研究能力

#### 3.8.1 设计提案 (`001-orquestador-nativo-opencode.md`, 2184 行, 23 节)
完整设计文档, 包含:
- 0. Changelog (V1-V12, N1-N15)
- 1. Executive summary
- 2. Why native, not bash (Primitives 替代表)
- 3. Multi vs single-model per role (决策矩阵 + 实证)
- 4. General architecture (文件树 + 输出树 + 流程图)
- 5. Installation (user/project/SSH)
- 6. Configuration merge (3-layer)
- 7. orquestador.json schema v1.0 (8 字段)
- 8. Agent 完整定义 (5 个)
- 9. Commands 定义
- 10. 10 步流程伪码
- 11. Iterate 模式 (决策伪码 + 5 个 case)
- 12. Empirical validation per section
- 13. Opt-in disqualification
- 14. Output structure `out/{id}/iter-{N}/...`
- 15. Resumability
- 16. Native metrics
- 17. 4-layer smoke test
- 18. i18n (English-only 设计)
- 19. Repo name 35 candidates (5 groups)
- 20. Minimal smoke test
- 21. Risks & tradeoffs
- 22. Out of scope
- 23. Next steps

#### 3.8.2 实证证据 (`iterations-analysis.md`, 226 行)
5 个真实项目分析:
- cardiorrenal R1/R2 (medical supplements, 9+3 模型)
- oc-rust-02 (Rust on OpenCode, 8×8)
- eval-7-ia-001 (meta-evaluation, 量化自动评估偏差 -0.51 ~ +1.89)
- oc-sda/001 (2 models, even small N benefits from multi-eval)
- oc-sda/002 (3 models, 7 文件迭代)
- 关键发现:
  - 多模型生成: 6/6 项目有价值
  - 多模型评估: 33% 有价值 (close ties), 67% 冗余 (consensus)
  - 多模型验证: 无价值 (bash 输出是 binary)
  - **自动评估偏差范围 -0.51 ~ +1.89 点**, qwen3.7-max 是 outlier (+1.89)
  - **67% cases 共识 > 85%** (单 evaluator 足矣)
  - 评分分歧最高 2.93 点 (cardiorrenal)
  - **之前的项目从未真正跑过 empirical validation** (opencode-moa 的最大贡献)
  - umbral_convergencia=0.5 的实证基础: cardiorrenal R2 gap=0.03, oc-rust-02 gap=2.0

#### 3.8.3 实验日志
**2026-07-11 v0.2.0-beta** (`2026-07-11-rust-gui-app.md`, 335 行):
- 12 模型完整跑 Rust GUI 设计, Spanish prompt
- iter-1 完整 (30 文件, winner=minimax 42/50), iter-2 被 5h quota 在 step 5 切断
- Cost: $12.34 总 (644 请求, 3M tokens in, 533K out, 76K reasoning)
- 实证**跨迭代 cross-pollination**: `request_repaint()` 1/12 → 12/12, edge-detect 1/12 → 12/12, `rust-toolchain.toml` 4/12 → 12/12 — 验证 §6.3 论文命题
- Model personality vs floor 分类
- 关键发现: mimo-v2.5 (最便宜 $0.046) iter-2 lift +14 最高, qwen3.7-max 最贵 ($3.85, 31% 总) 但产出可接受
- Step-1 约束补丁 ad-hoc: max 12 tool calls, no `cargo tauri build`, 6 min hard-stop

**2026-07-12 v0.3** (`2026-07-12-rust-gui-app-v3.md`, 379 行):
- 同一 prompt, v0.3 bundle + `step_5_modo: sintesis_central`
- iter-1 partial (validador step 2 被 bash:ask 阻塞, 步骤 3/4/7/8 合成), iter-2 11/12 提案子进程被 orphan 干扰杀死
- 关键产出 `05-propuesta-integrada.md` 25.6KB / 422 行, score 46/50 (vs v0.2.0-beta 42/50)
- 选赢栈: GTK4 0.10 + `v4_12` + 独立 popup + `ToplevelState::MINIMIZED` + `#1B5E20` 深绿
- iter-2 唯一完成的 `01-propuesta-minimax.md` 包含 "## Iter-2 changes vs iter-1" 节, 列 10 项针对 iter-1 的修正, 收敛到与 integrator 相同栈 — **实证 feedback-aware iteration 机制**
- Cost ~$8.50-11.20 (estimated, minimax-coding-plan provider 无 cost metadata)
- 验证 §6.2 命题 PARTIALLY: cost 4-18× cheaper ✅, wall-clock 3× faster ✅, quality 是 systematically different (convergent vs prominent), not identical

#### 3.8.4 论文草稿 (`DRAFT-multi-model-orchestration.md`, 491 行)
学术格式论文:
- Abstract: 介绍 + 两轮实验 (12 模型 Rust GUI) + 关键发现
- §3 System architecture (3 类文件 / pipeline / iterate / selective participation)
- §4 Method (Run A v0.2.0-beta self_improve × 12, Run B v0.3 sintesis_central)
- §5 Empirical results (cost/ROI, cross-pollination, winner content, sintesis_central validation)
- §6 Discussion:
  - §6.1 model floor > model lift (preliminary, N=1)
  - §6.2 **synthesis centralized > self-improvement × 12** (partial validation)
  - §6.3 **cross-pollination observable and significant** (validated + extended)
  - §6.4 Limitations (N=2, headless 阻塞, orphan, cost telemetry absent)
- §7 Future work (8 项)
- §8 Conclusion
- 引用 7 个文献 [wang2024moa, together2024moa, du2023, hong2023metagpt, wu2023autogen, khattub2023dspy, anthropic2024claude, opencode]

#### 3.8.5 路线图 (`ROADMAP.md`, 91 行)
- **v0.3** (current Q3 2026): 稳定 v0.2 beta API; auto-detect propuesta files; multi-eval opt-in; idioma_output; cost estimation; git integration; better error messages
- **v0.4** (Q4 2026): Web dashboard, interactive re-run, diff view
- **v0.5** (Q4 2026): Multi-machine resume, cloud execution, cached evaluations
- **v1.0** (2027+): Sandbox enforcement, audit trail, replay mode, plugin system
- **v2.0** (long-term): Full MoA paper implementation (layered model evaluation, cross-model attention, configurable depth)
- **Research directions**: auto-tuning convergence, prompt injection detection, cross-language prompts, federated evaluation
- **Out of scope**: replace OpenCode, support non-OpenCode orchestrators, cloud-only SaaS

### 3.9 运营 & 事故复盘 (`opencode-moa/AGENTS.md`, 318 行)

**8 节关键运营文档**:
- **§1. 默认 modelos_a_competir 8 选** — 表格列出 8 模型 + 入选理由 + 4 个被剔除的 (qwen3.7-max $0.056/req 最差 ROI, deepseek-v4-pro 24→24 退化, qwen3.6-plus 27→27 退化, mimo-v2.5-pro 持续低分)
- **§2. `propuesta-mimo.md` 模型绑定冲突 (HIGH 严重度事故)**:
  - v0.2.0-beta → v0.3 PR#1 (commit 75307fd) 误改 `propuesta-mimo.md` frontmatter 从 `opencode-go/mimo-v2.5-pro` 改为 `opencode-go/minimax-m3`
  - 用户指令 "nunca se va a ejecutar MiniMax de OpenCode" (永不执行 OpenCode 平台的 MiniMax)
  - 2026-07-12 实测结果: 42 个请求到 `opencode-go/minimax-m3` (耗 $0.10), 11 个合法 iter-2 子进程被连带杀
  - **修复 (PR#4)**: 恢复 `propuesta-mimo.md` 为 `opencode-go/mimo-v2.5-pro`, 同时修 4 个 meta-agent + 2 个 command 的 frontmatter, 修 orquestador.json 默认 8 模型 + 改 `modelo_objetivo` 为 `minimax-coding-plan/MiniMax-M3`
  - **验证脚本**: `head -7 ~/.config/opencode/agents/propuesta-mimo.md` 应包含 `model: opencode-go/mimo-v2.5-pro`
- **§3. Headless 模式权限挂起 (OpenCode 上游 bug #35073)**:
  - 现象: `opencode run` (headless) 时, subagent (validador step 2, evaluador step 3) **永久挂起** 在 `bash: ask` 权限询问
  - 原因: `--auto` 不自动批准, subagent 继承 interactive-actor 语义
  - **Workaround #1**: 在 **user-level** `~/.config/opencode/opencode.jsonc` 加 `bash: allow` (有安全警告, 仅研究机)
  - **Workaround #2**: 直接绕过 validador/evaluator, 单独用 build agent 跑 step 5:
    ```bash
    setsid opencode run \
      --model minimax-coding-plan/MiniMax-M3 \
      --auto --pure --print-logs --log-level=INFO \
      --title "step 5 — sintesis_central" \
      --dir /tmp/your/test/dir \
      "Read all 12 proposals in /tmp/your/test/dir/out/{id}/iter-1/01-propuesta-*.md and produce /tmp/your/test/dir/out/{id}/iter-1/05-propuesta-integrada.md following the sintesis_central rules in opencode-moa/agents/sintetizador.md" \
      < /dev/null > /tmp/your/test/dir/logs/step5.log 2>&1 &
    disown
    ```
- **§4. 孤儿进程处理**: 父进程死后, child propuesta subagent 成孤儿, 继续写带错误模型的提案; 用 `pkill -9 -f 'opencode run'` 清 (但会杀合法子进程, 谨慎)
- **§5. Quota telemetry**: `minimax-coding-plan` provider **不返回 cost/tokens**; 要从 opencode-web UI (`agent.rovisoft.net`) 手动复制; `opencode-go` provider **返回** cost/tokens
- **§6. 加新模型流程**: 1. 创建 `propuesta-{id_corto}.md`, 2. 加到 `modelos_a_competir`, 3. `./install.sh`, 4. `grep '^model:'` 验证, 5. 更新 §1 表
- **§7. 删模型流程**: 1. 从 `modelos_a_competir` 删, 2. (可选) 删 agent 文件, 3. 更新表, 4. CHANGELOG, 5. 重新 install
- **§8. Cross-references**: 链接到 2 个 bitácora, paper draft §6.2, OpenCode 上游 issue/PR

### 3.10 CI 防御 (`scripts/check-no-forbidden-model.sh`, 68 行)
- 搜索 `opencode-go/minimax-m3` 在 active code 里的引用
- 白名单 (允许): `opencode-moa/AGENTS.md`, `opencode-moa/CHANGELOG.md`, 2 个 bitácora, design proposal, paper draft
- 非白名单命中 → exit 1, 列出违规位置
- 配套建议 CI workflow (`.github/workflows/ci.yml` 骨架)

### 3.11 输出能力 (Out/ Directory Schema)

```
out/{id}/iter-{N}/
├── 01-propuesta-{id_corto}.md            12 个原始提案
├── 02-validacion-{id_corto}.md           12 个验证报告 (if validacion_empirica)
├── 03-calificacion-evaluador.md          1 个评分 (or 1 个 multi-eval consensus)
├── 04-clasificacion.md                   1 个排名
├── 05-propuesta-integrada.md             1 个整合提案 (if sintesis_central)
│   OR
├── 05-mejorada-{id_corto}.md             12 个改进提案 (if self_improve)
├── 06-validacion-{integrada|mejorada}.md  1/N 个验证 (if validacion_empirica)
├── 07-calificacion-final.md              1 个再评分
├── 08-ganador.md                         1 个赢家
└── 09-sumario.md                         1 个总览

out/{id}/10-sintesis-cross-iter.md        1 个跨迭代综合 (if sintesis_final)
```

- **ID 验证**: `^[a-z0-9][a-z0-9-]{2,29}$` (3-30 字符, 小写, 数字, 连字符, 不以连字符开头)
- **resumability**: 重跑时 orchestrator 用 `glob` 看哪些文件已存在, 已存在的 step 跳过
- **完整性判定**: 各文件最小行数 (propuesta 50, validacion 30, calificacion 80, etc.)
- **强制重跑**: `--force` flag 删除目标 iter dir 后重开

### 3.12 安全与审计能力

#### 3.12.1 沙箱
- validador 的 `permission.bash` glob 名单 (~40 allow rules)
- validador 主动拒绝 `rm -rf /`, `mkfs`, `dd of=/dev/...` 等破坏性命令
- 任何 `*` 不在白名单的命令 → `ask` (需人工)
- `edit: deny` (validador 不能改文件)
- 30s timeout per command
- "纯客观"原则: 不可验证标 ⏭️ SKIP 而非编造

#### 3.12.2 可审计性
- 每个验证结果含**确切命令 + 输出 + 时间**
- 每个 evaluator 分数必须引用 1-2 句提案原文
- 每个 sintetizador 决定必须有 visible justification
- session log 由 OpenCode 自身记录 (含 task/bash/read/write 等所有工具调用)

#### 3.12.3 4 层 merge 防误覆盖
- user-level 配置 + project-level 配置自动 merge, 不冲突键保留
- 单 JSON 文件, 9 → 16 字段, 版本化 (semver)

---

## 4. 技术栈

### 4.1 运行环境
- **OpenCode CLI** v1.0.0+ (必须, 整个系统基于其原语)
- 至少 2 个 AI model provider:
  - `minimax-coding-plan` (用户 plan, 必含, 用于所有 meta-agent + propuesta-minimax)
  - `opencode-go` (7 个 opencode-go 提案模型)
- POSIX shell (bash/zsh/fish) — 仅用于 `install.sh`
- 可选 Docker — 提供 Dockerfile 模板

### 4.2 Agent Framework 原语 (OpenCode)
- `mode: primary` vs `mode: subagent` — 区分主协作者和子 agent
- `task(subagent_type='X')` — 并行调用子 agent
- `bash`, `webfetch`, `read`, `write`, `glob`, `grep`, `todowrite`, `question` — 内置工具
- frontmatter 控制: `model:`, `temperature:`, `permission:`, `description:`
- `permission.bash` glob 规则系统 (~40 rules)
- slash command 引擎 (`commands/orquestar.md` + `subtask: true`)
- 父子 session log 体系

### 4.3 配置格式
- **JSON** v1.1 schema (orquestador.json, 16 字段)
- **JSONC** 注释可选 (opencode.jsonc, 用户级权限)
- **Markdown + YAML frontmatter** agent / command 文件
- **Markdown** 4 级标题 (Output files: `# 01 — Proposal`, `## Tech stack`, etc.)

### 4.4 文件总数与规模
- 文档: 17 个 (.md, ~6500 行)
- 源码: 7 个 agent + 2 个 command + 1 个 JSON = 10 个核心文件 (~1200 行)
- 脚本: 2 个 (install.sh + check-no-forbidden-model.sh)
- 配置文件: 1 个 orquestador.json + 1 个 opencode.jsonc 模板
- **总**: 33 个文件, **约 8000 行**

### 4.5 AI 模型清单
- **10 个提案器模型**:
  - `opencode-go/glm-5.1`
  - `opencode-go/glm-5.2`
  - `opencode-go/kimi-k2.6`
  - `opencode-go/kimi-k2.7-code`
  - `opencode-go/mimo-v2.5-pro`
  - `opencode-go/mimo-v2.5`
  - `opencode-go/deepseek-v4-pro`
  - `opencode-go/deepseek-v4-flash`
  - `opencode-go/qwen3.7-plus`
  - `minimax-coding-plan/MiniMax-M3`
- **4 个 meta-agent 模型** (orquestador, evaluador, sintetizador, validador): 全部 `minimax-coding-plan/MiniMax-M3` (用户 plan)
- **2 个 command 模型**: `minimax-coding-plan/MiniMax-M3`

### 4.6 引用文献
- [wang2024moa] Mixture-of-Agents (Together AI 2024) — 灵感源
- [du2023improvingfactualityreasoning] Multi-agent Debate
- [hong2023metagpt] MetaGPT — 角色化对比
- [wu2023autogen] AutoGen — Director+Worker 拓扑
- [khattub2023dspy] DSPy — declarative 编译
- [anthropic2024claude] Constitutional AI — 文本规则

---

## 5. 关键代码片段

### 5.1 id_corto 派生规则 (`agents/orquestador.md` lines 60-77)
```markdown
4. For each model in modelos_a_competir, derive its `id_corto` (the
   short slug that names the corresponding agent file) and verify
   that `propuesta-{id_corto}.md` exists in `~/.config/opencode/agents/`
   or `.opencode/agents/`. The derivation rule is:
   - Strip the provider prefix (everything before and including the first `/`)
     → e.g. `opencode-go/mimo-v2.5-pro` → `mimo-v2.5-pro`
   - Strip any version segment (substring starting with `-` followed by
     digits, or `.` followed by digits) → e.g. `mimo-v2.5-pro` → `mimo-pro`
     → `mimo`
   - Strip any `thinking` variant suffix (`-thinking`, `:thinking`)
   - Lowercase the result
   If multiple `propuesta-*.md` files exist whose frontmatter `model:`
   field matches the requested model string, prefer the one with the
   shortest name (e.g. `propuesta-mimo.md` wins over
   `propuesta-mimo-v2-5-pro.md`). If no match is found by any rule,
   ABORT with: "ERROR: no `propuesta-{id_corto}.md` found for model
   `{model}`..."
```

### 5.2 validador bash 白名单 (`agents/validador.md` lines 1-54)
```yaml
permission:
  edit: deny
  bash:
    "*": ask
    "command -v *": allow
    "* --version": allow
    "*-version": allow
    "which *": allow
    "shellcheck *": allow
    "node --check *": allow
    "python -c *": allow
    "python3 -c *": allow
    "pip show *": allow
    "npm ls": allow
    "npm list *": allow
    "cargo --list": allow
    "npm install *": allow
    "pip install *": allow
    "cargo build *": allow
    "cargo check *": allow
    "go build *": allow
    "go vet *": allow
    "make *": allow
    "echo *": allow
    "printf *": allow
    "cat *": allow
    "ls *": allow
    "head *": allow
    "tail *": allow
    "wc *": allow
    "file *": allow
    "stat *": allow
    "mkdir *": allow
    "mkdir -p *": allow
    "rm *": ask
    "cp *": allow
    "mv *": allow
    "touch *": allow
    "grep *": allow
    "awk *": allow
    "sed *": allow
    "curl *": allow
    "wget *": allow
    "sleep *": allow
    "date *": allow
  webfetch: allow
  read: allow
  write: allow
```

### 5.3 迭代收敛决策 (`agents/orquestador.md` lines 430-458)
```markdown
if N >= max_iteraciones:
  log("Maximum iterations reached ({N}/{max_iter}). STOP.")
  FINALIZE()

if mejora >= umbral_convergencia:
  log("Meaningful improvement: {mejora} >= {umbral}. CONTINUE to iter {N+1}.")
  CONTINUE to step 1 with N+1
else:
  log("Insufficient improvement: {mejora} < {umbral}. CONVERGED. STOP.")
  log("  (Regression: {mejora < 0})" if mejora < 0 else "")
  FINALIZE()

IMPORTANT: A regression (mejora < 0) ALWAYS results in STOP.
The check "mejora >= umbral" covers this:
  - mejora = -0.5, umbral = 0.5
  - -0.5 >= 0.5 → FALSE → STOP
```

### 5.4 step_5_modo = sintesis_central 集成 prompt (`agents/orquestador.md` lines 296-336)
```python
task(
  description="Synthesize integrated proposal",
  subagent_type="sintetizador",
  prompt="
    Produce ONE integrated proposal that consolidates the best ideas
    from all 12 originals in this iter.

    Read:
    - out/{id}/iter-{N}/01-propuesta-*.md  (12 originals)
    - out/{id}/iter-{N}/03-calificacion-evaluador.md (evaluator feedback)
    - out/{id}/iter-{N}/04-clasificacion.md (current ranking)
    - out/{id}/iter-{N}/02-validacion-*.md (if exist; per-section viability)

    Process:
    1. Identify the TOP 3 originals by total score and TOP 3 by empirical viability.
    2. For each, list the unique technical contribution...
    3. Detect CONVERGENT ideas — ideas that 3+ originals mentioned
       independently. These are validated by the diversity of the models
       and should be retained verbatim.
    4. Detect CONFLICTING choices — proposals that picked different
       crates. For each, evaluate against the evaluador's signal and
       pick the one with stronger evidence...
    5. Write ONE self-contained proposal in the same format as the
       originals (Tech stack, Architecture, Installation commands,
       Considerations, Effort, References). 200-400 lines. Mark with a
       '## Source attribution' section listing which original(s) each
       section draws from.
    6. End with a '## Why this beats the field' section that
       cross-references 03-calificacion-evaluador.md: which weakness
       in the WINNING original does each design choice address?

    Write to out/{id}/iter-{N}/05-propuesta-integrada.md
  "
)
```

### 5.5 反馈感知迭代 prompt (`agents/orquestador.md` lines 178-186)
```markdown
=== FEEDBACK-AWARE ITERATION ===
If N > 1, the following may already exist as iteration-1 artefacts:
  - out/{id}/iter-1/03-calificacion-evaluador.md
  - out/{id}/iter-1/04-clasificacion.md
  - out/{id}/iter-1/05-propuesta-integrada.md (if step_5_modo=sintesis_central)
Read these BEFORE writing, and incorporate the lessons (weaknesses
highlighted by the evaluador, design choices that converged,
ideas that propagated across iter-1). Your iter-N proposal should
be measurably better than iter-1's, not a copy with cosmetic changes.
```

### 5.6 evaluador 经验性可调规则 (`agents/evaluador.md` lines 46-55)
```markdown
If a proposal has a validation report with viability scores PER SECTION:
- If 0 sections are ❌ NO VIABLE: full AP (up to 10).
- If 1 section is ❌ NO VIABLE (out of 3-4): AP = 5-7 (reduced, not eliminated).
- If 2 sections are ❌ NO VIABLE: AP = 2-4 (severely reduced).
- If 3+ sections are ❌ NO VIABLE: AP = 1 (barely viable).
- If viability score GLOBAL < 2/10 OR all sections critical fail: AP = 1.

If `descalificar_fallida == true` AND global viability < 3/10: mark as DESCALIFICADA in your table.
```

### 5.7 orquestador.json v1.1 schema (`orquestador.json`, 39 行)
```json
{
  "$schema": "https://opencode-moa.dev/schemas/orquestador.v1.1.json",
  "version": "1.1",
  "modelos_a_competir": [
    "minimax-coding-plan/MiniMax-M3",
    "opencode-go/glm-5.1",
    "opencode-go/glm-5.2",
    "opencode-go/kimi-k2.6",
    "opencode-go/kimi-k2.7-code",
    "opencode-go/deepseek-v4-flash",
    "opencode-go/mimo-v2.5",
    "opencode-go/qwen3.7-plus"
  ],
  "modelo_objetivo": "minimax-coding-plan/MiniMax-M3",
  "max_iteraciones": 3,
  "umbral_convergencia": 0.5,
  "validacion_empirica": true,
  "descalificar_fallida": false,
  "smoke_test": false,
  "step_5_modo": "sintesis_central",
  "sintesis_final": false,
  "sintesis_final_modelo": "<same as modelo_objetivo>",
  "multi_eval": false,
  "multi_eval_modelos": [],
  "max_wall_clock_minutes": 0,
  "filter_low_performers": {
    "descalificar_debajo_de": 30,
    "aplicar_en": "iter_>=2",
    "keep_minimo": 3
  },
  "if_mejoras_tecnicamente_similares_a_otras": false
}
```

### 5.8 install.sh 核心 (`install.sh` lines 31-60)
```bash
# Create directories
mkdir -p "$CONFIG_DIR/agents"
mkdir -p "$CONFIG_DIR/commands"

# Copy agents
echo "Copying agents..."
cp "$BUNDLE_DIR/agents/"*.md "$CONFIG_DIR/agents/"
echo "  ✓ $(ls "$BUNDLE_DIR/agents/"*.md | wc -l) agent files"

# Copy commands
echo "Copying commands..."
cp "$BUNDLE_DIR/commands/"*.md "$CONFIG_DIR/commands/"
echo "  ✓ $(ls "$BUNDLE_DIR/commands/"*.md | wc -l) command files"

# Copy config (don't overwrite if exists)
if [[ -f "$CONFIG_DIR/orquestador.json" ]]; then
  echo
  echo "WARNING: $CONFIG_DIR/orquestador.json already exists."
  read -p "Overwrite? [y/N] " -n 1 -r
  echo
  if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "  Skipped orquestador.json (existing file preserved)"
  else
    cp "$BUNDLE_DIR/orquestador.json" "$CONFIG_DIR/"
    echo "  ✓ orquestador.json overwritten"
  fi
else
  cp "$BUNDLE_DIR/orquestador.json" "$CONFIG_DIR/"
  echo "  ✓ orquestador.json"
fi
```

### 5.9 check-no-forbidden-model.sh 防御脚本 (`scripts/check-no-forbidden-model.sh` lines 19-50)
```bash
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORBIDDEN_MODEL="opencode-go/minimax-m3"

# Files where the forbidden model is ALLOWED to appear (historical context).
WHITELIST_FILES=(
  "opencode-moa/AGENTS.md"
  "opencode-moa/CHANGELOG.md"
  "docs/research/experiments/2026-07-11-rust-gui-app.md"
  "docs/research/experiments/2026-07-12-rust-gui-app-v3.md"
  "docs/proposals/001-orquestador-nativo-opencode.md"
  "docs/papers/DRAFT-multi-model-orchestration.md"
)

# Build a grep --exclude list from the whitelist
EXCLUDE_ARGS=()
for FILE in "${WHITELIST_FILES[@]}"; do
  EXCLUDE_ARGS+=("--exclude=$(basename "$FILE")")
done

# Search active code
MATCHES=$(grep -rn "$FORBIDDEN_MODEL" \
  --include="*.md" \
  --include="*.json" \
  "$REPO_ROOT" \
  "${EXCLUDE_ARGS[@]}" 2>/dev/null || true)

# Filter out whitelist files by full path
FILTERED_MATCHES=$(echo "$MATCHES" | grep -v -F -f <(printf "%s\n" "${WHITELIST_FILES[@]/#/$REPO_ROOT/}") || true)

if [ -n "$FILTERED_MATCHES" ]; then
  echo "FAIL: forbidden model '$FORBIDDEN_MODEL' found in active code:"
  echo "$FILTERED_MATCHES"
  exit 1
fi

echo "PASS: no forbidden model '$FORBIDDEN_MODEL' in active code."
exit 0
```

### 5.10 sintetizador 4 模式结构 (`agents/sintetizador.md`)
```markdown
# Mode "classification" (step 4)
Inputs: 03/01/02-*, Output: 04-clasificacion.md
Process: 读 evaluations → descalificar (opt-in) → 排序 → ties 按 id_corto 字典序

# Mode "integrated synthesis" (step 5, when step_5_modo = "sintesis_central")
Inputs: 12 originals + 03 + 04 + 02-*, Output: 05-propuesta-integrada.md
Process: TOP 3 by score + TOP 3 by viability → 独特贡献列表 → CONVERGENT (3+) → CONFLICTING 选择 → 200-400 行统一提案 + Source attribution + Why this beats the field
约束: 不引入"任何原始提案都没提过"的想法 (curation, not invention)

# Mode "final selection" (step 8)
Inputs: 07 + 05-* + 04 + 06-*, Output: 08-ganador.md
Process: 整合候选与 12 原稿一同排名, 高分低 viability 不赢, 选赢+justification

# Mode "cross-iteration synthesis" (step 10, when sintesis_final = true)
Inputs: iter-*/08/09/05/07, Output: 10-sintesis-cross-iter.md
Process: Convergence / Best of each iter / Recommended adoption / Convergence trajectory
```

### 5.11 命令 frontmatter 模板
`commands/orquestar.md`:
```yaml
---
description: Orchestrate a complete multi-model competition (10 steps)
agent: orquestador
model: minimax-coding-plan/MiniMax-M3
subtask: true
---
```

`commands/orquestar-iterate.md`:
```yaml
---
description: Orchestrate with iterate mode (loop until convergence or max_iterations)
agent: orquestador
model: minimax-coding-plan/MiniMax-M3
subtask: true
---
```

### 5.12 提案器 agent frontmatter 模板
```yaml
---
description: Generates or improves technical proposals ({MODEL} variant)
mode: subagent
model: {model}
temperature: 0.7
---
```

---

## 6. 集成点

### 6.1 与 OpenCode 平台集成

#### 6.1.1 文件路径约定
| 类型 | 路径 | 来源 |
|---|---|---|
| Agents | `~/.config/opencode/agents/*.md` | install.sh 复制 |
| Commands | `~/.config/opencode/commands/*.md` | install.sh 复制 |
| User config | `~/.config/opencode/orquestador.json` | install.sh 复制 |
| User permissions | `~/.config/opencode/opencode.jsonc` | 用户手动 (workaround) |
| Project config | `./orquestador.json` | 用户手动 (override) |
| Project permissions | `./opencode.jsonc` | 用户手动 |
| Project agents | `./.opencode/agents/*.md` | 用户手动 (add model) |
| Project commands | `./.opencode/commands/*.md` | 用户手动 (add command) |
| Outputs | `./out/{id}/iter-{N}/*.md` | 编排器创建 |

#### 6.1.2 OpenCode 原语依赖
- `mode: primary` (orquestador) — 能调 `task` 工具
- `mode: subagent` (其他 12 个) — 能被 `task` 调
- `task(subagent_type='X', prompt='...')` — 编排器唯一调用方式
- `bash`, `webfetch`, `read`, `write`, `glob`, `grep` — 所有 agent 通用
- `todowrite` — 仅 orquestador 用 (跟踪 9 步)
- `permission.bash` glob — 仅 validador 用 (沙箱)
- `subtask: true` — command frontmatter
- `agent: orquestador` — command 路由

#### 6.1.3 配置层叠加
- `model:` frontmatter 决定 agent 模型 (subagent 不继承 modelo_objetivo, 见 AGENTS.md §2 教训)
- `command:` `model:` 字段**覆盖** agent frontmatter
- 4 层 JSON merge (arg > project > user > default)
- `modelo_objetivo` 字段控制 meta-agent 模型 (但实际有 frontmatter 优先级问题)

#### 6.1.4 OpenCode 上游 bug 依赖
- [#35073](https://github.com/anomalyco/opencode/issues/35073) "subagent permission asks hang indefinitely" — headless 模式 validador/evaluador 挂死
- [PR #35823](https://github.com/anomalyco/opencode/pull/35823) 修复中 (1.17.18 尚未发布)

### 6.2 与 AI 模型 Provider 集成

#### 6.2.1 Provider 1: `minimax-coding-plan`
- 模型: `MiniMax-M3`
- 用途: 4 个 meta-agent (orquestador, evaluador, sintetizador, validador) + 1 个 propuesta (propuesta-minimax) + 2 个 command
- Cost metadata: **不返回** (需 opencode-web UI 手工查)
- 限制: 这是用户 plan model, 用户指令 "nunca se va a ejecutar MiniMax de OpenCode" 限制的是 opencode-go 平台的同名模型

#### 6.2.2 Provider 2: `opencode-go`
- 模型: 7 个 (glm-5.1, glm-5.2, kimi-k2.6, kimi-k2.7-code, deepseek-v4-flash, mimo-v2.5, qwen3.7-plus) + 2 个可选 (deepseek-v4-pro [dropped], mimo-v2.5-pro)
- 用途: 提案器 subagent
- Cost metadata: **返回** ($/req, tokens)
- 价格分层 (from 2026-07-11 实验):
  - 最便宜: mimo-v2.5 ($0.046/cum)
  - 最贵: qwen3.7-max ($3.85/cum, **已被剔除默认**)
  - 性价比最高: deepseek-v4-flash + mimo-v2.5 + qwen3.7-plus

### 6.3 与 Git/GitHub 集成 (理论)
- README 提到未来 v0.3 有 `git_autocommit: true` 选项, 自动 commit `out/{id}/iter-{N}/` (planned)
- 暂未实现, 需手动 git 管理
- AGENTS.md 建议把 `propuesta-{modelo}.md` 文件名引用跨 bitácora 不要删, 否则历史 reference 失效
- CI 推荐 (待实现): `.github/workflows/ci.yml` 调用 `./scripts/check-no-forbidden-model.sh`

### 6.4 与 Docker 集成
- `docs/installation.md` 提供完整 Dockerfile 模板:
  ```dockerfile
  FROM opencode/opencode:latest
  RUN git clone https://github.com/YOUR-USERNAME/opencode-moa.git /tmp/opencode-moa \
      && cd /tmp/opencode-moa \
      && mkdir -p /root/.config/opencode/agents /root/.config/opencode/commands \
      && cp opencode-moa/agents/*.md /root/.config/opencode/agents/ \
      && cp opencode-moa/commands/*.md /root/.config/opencode/commands/ \
      && cp opencode-moa/orquestador.json /root/.config/opencode/ \
      && rm -rf /tmp/opencode-moa
  WORKDIR /workspace
  CMD ["opencode"]
  ```
- 用途: 可复现环境 / CI-CD pipeline

### 6.5 与 VPS 集成
- 通过 SSH 安装:
  ```bash
  ssh user@vps "mkdir -p ~/.config/opencode/{agents,commands}"
  scp opencode-moa/agents/*.md user@vps:~/.config/opencode/agents/
  scp opencode-moa/commands/*.md user@vps:~/.config/opencode/commands/
  scp opencode-moa/orquestador.json user@vps:~/.config/opencode/
  ```
- 配合 OpenCode web UI (`https://your-vps-domain/`, 默认端口 4096) 用浏览器跑 `/orquestar` 命令
- 用户报告的真实使用场景 (Israel Roldan 在 Hetzner VPS)

### 6.6 与 MoA 论文生态的集成
- 引用 [wang2024moa] Mixture-of-Agents (Together AI 2024, arXiv:2406.04692) — 灵感源
- 关系: opencode-moa 用**水平迭代**代替 MoA 的**垂直层叠**, 互补而非竞争
- 论文草稿 DRAFT-multi-model-orchestration.md 引用 7 个相关工作 (MoA, MetaGPT, AutoGen, DSPy, Multi-agent Debate, Constitutional AI, OpenCode)

### 6.7 与 bash 前身的迁移
- 从 `002/001-glm-5.1.md` 到 `007-glm-5.1.md` 的 7 个 bash 版本 (2058 行 + 12 helper scripts) 迁移到 markdown+JSON
- AGENTS.md §2 记录了迁移过程中 `propuesta-mimo.md` 模型绑定冲突事故
- 经验教训: model 字符串字面量要严格管理, 用 CI check 防御

### 6.8 与外部开发工具的集成
- `command -v`, `node --check`, `python -c`, `shellcheck` — 通过 validador 验证
- `npm install`, `cargo build`, `pip install`, `make` — 同上
- `curl -sI` — 检查 HTTP 端点
- `webfetch` — 验证官方文档

### 6.9 跨迭代集成点
- `out/{id}/iter-{N-1}/03-calificacion-evaluador.md` → iter-N 提案器读
- `out/{id}/iter-{N-1}/04-clasificacion.md` → iter-N 提案器读
- `out/{id}/iter-{N-1}/05-propuesta-integrada.md` → iter-N 提案器读
- iter-N 09-sumario.md → iterate-mode 决策算法读
- iter-{*} 全部 08/09/05/07 → step 10 cross-iteration synthesizer 读
- `out/{id}/iter-{N-1}/04-clasificacion.md` → filter_low_performers 读 (iter >= 2 时)

### 6.10 失败与恢复集成点
- `pgrep` + `pkill -9 -f 'opencode run'` — 清理孤儿进程
- 备份约定: `BACKUP-iter1-v3/`, `BACKUP-iter2-partial-v3/`, `FULL-BACKUP-2026-07-11/`, `*.v0.2.0-beta.bak` (install 升级前)
- `run-step5.sh` wrapper — 绕过 validador 直接跑 integrator
- `run-iter2-propuestas.sh` wrapper — 手工启 12 个子进程 (应对 orphan)

---

## 7. 关键经验与最佳实践 (来自项目自身)

### 7.1 设计决策的实证基础
- **多模型提案**: 6/6 项目有价值
- **多模型评估**: 仅 33% 有价值 (close ties); 67% 单 evaluator 足矣
- **多模型验证**: 无价值 (bash 输出是 binary)
- **多模型综合**: 单一标准产生稳定决策
- **自动评估偏差**: 范围 -0.51 ~ +1.89 点, 量化后用 temp=0.0 + 严格 prompt 缓解
- **共识阈值**: >85% 共识时单 evaluator 即可

### 7.2 论文级命题验证
- **§6.1** (preliminary): model floor > model lift (单 iter 时 floor 主导, 多 iter 时 lift 重要)
- **§6.2** (partially validated): sintesis_central ≈ self_improve × 12 in COST, but systematically DIFFERENT in output (4-18× cheaper, 3× faster, 但 winner 栈不同 — convergent vs prominent)
- **§6.3** (validated + extended): cross-pollination 可观察且显著; 6/12 提案独立选 GTK4, 8/12 独立选 separate popup, 6/12 独立选 #1B5E20 深绿 (iter-1 内即可见, 不只 iter-1→iter-2)

### 7.3 工程教训
- **model 字符串字面量**: 需 CI check (`check-no-forbidden-model.sh`)
- **frontmatter 优先级**: command `model:` 覆盖 agent `model:`; subagent 不继承 modelo_objetivo
- **id_corto 派生规则**: 严格匹配避免歧义; 短名优先 (`propuesta-mimo.md` > `propuesta-mimo-v2-5-pro.md`)
- **bash 白名单 glob**: 比脚本白名单更声明式
- **per-section viability**: 比全局 viability 更公平 (复杂提案多节, 单节失败不淘汰)
- **4 层 config merge**: 比"用哪个配置文件"清晰
- **convergence threshold + max_iter + regression = STOP**: 三重保险, 杜绝无穷循环

### 7.4 已知限制 (v0.3)
- N=2 in 1 domain (Rust GUI), 结论待跨域验证
- `opencode-go` provider 中途会降级模型
- iter-2 step 5 quota cut (v0.2.0-beta run)
- validador step 2 headless 模式权限挂起 (上游 bug #35073)
- iter-2 orphan-process 干扰 (subagent 与 orchestrator 不联动 kill)
- 12 `05-mejorada-*.md` v0.2.0-beta 文件已删, 文件级对比缺失
- `minimax-coding-plan` provider 不返回 cost/tokens metadata

### 7.5 关键成果 (量化)
- v0.2.0-beta iter-2 winner: minimax 42/50 (egui+eframe+wgpu)
- v0.3 sintesis_central winner: integradora 46/50 (GTK4+separate popup)
- v0.3 验证 12 模型独立收敛 6/12 → GTK4, 8/12 → separate popup
- v0.2.0-beta cost: $12.34 (644 req, 3M tokens in, 533K out)
- v0.3 step 5 cost ratio: 4-18× cheaper than self_improve
- v0.3 step 5 wall-clock: 3× faster than self_improve
- iter-2 minimax lift: +13 (37→50) for feedback-aware iteration

---

## 8. 总结

**opencode-moa** 是一个**完全在 OpenCode 内运行的零代码多模型编排器**, 用 ~8000 行 markdown + JSON + bash 辅助, 实现了:
1. **10 个提案模型**并行生成 + 经验性验证 + 5 维评估 + 4 模式综合 + 迭代收敛
2. **零外部运行时** (无 Python/bash 编排代码), 完全基于 OpenCode 原语
3. **4 层配置 merge** 灵活覆盖 user/project/runtime/default
4. **完整的学术闭环**: 5 个真实项目证据 → 23 节设计提案 → 2 个完整实验 → 论文草稿 + 参考文献
5. **实战事故复盘**: AGENTS.md 318 行记录 `propuesta-mimo.md` 模型绑定冲突 + 上游权限 bug + 孤儿进程处理
6. **CI 防御**: 自动检查禁用模型字符串

**核心价值主张**:
- 对 5-12 模型 ≤ 15 步的小型编排, declarative 优于 AutoGen/CrewAI/LangGraph 的代码方案
- "engineering tax of a Python framework dwarfs the algorithmic contribution" — 设计哲学
- 用 OpenCode 原生 `task` + frontmatter + permission glob 替代进程管理/重试/并发/调度
- 用 markdown prompt 替代所有控制流逻辑

**最大优势**: 完全可审计 (session log)、零依赖、声明式并行、用 8 模型默认 roster 跑一次 v0.3 iter-1 实测 ~$8-10

**最大风险**: 依赖 OpenCode 上游 (#35073 权限挂起 bug), 1 个 orphan 进程可污染多轮迭代, single-eval 设计有量化偏差 (-0.51 ~ +1.89)
