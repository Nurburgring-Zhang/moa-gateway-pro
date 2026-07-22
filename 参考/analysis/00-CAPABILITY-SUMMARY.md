# MoA Gateway Pro — 参考项目总能力表

> **生成时间**: 2026-07-13
> **覆盖项目**: 10 个 / 共 9500+ 行 / 10 份分析文件
> **目的**: 提取所有能力 → 去重 → 分类 → 评估对 MoA Gateway Pro 的可移植价值
> **来源项目编号**: 01=GateSwarm, 02=TogetherAI MoA, 03=MoA-Engine, 04=MOA-Commercial, 05=moa-skill, 06=MoAI-ADK, 07=Moat, 08=moa-server, 09=opencode-moa, 10=Verdex

---

## 0. 执行摘要 (Executive Summary)

10 个项目按"距离 MoA Gateway Pro 的远近"分为 3 类:

| 类别 | 项目 | 距离 | 核心价值 |
|------|------|------|---------|
| **直接参考** (高度可移植) | 01 GateSwarm, 08 moa-server, 10 Verdex, 05 moa-skill | 最近 | 路由 / OpenAI 兼容 / 桌面 / 委员会编排 |
| **算法核心** (可移植) | 02 TogetherAI MoA, 03 MoA-Engine, 09 opencode-moa | 中等 | MoA 算法 / 元 Prompt / 多模型协作 |
| **企业级参照** (选择性借鉴) | 04 MOA-Commercial, 06 MoAI-ADK, 07 Moat | 较远 | 多租户 / Agent 框架 / 安全审计 |

**总能力点**: 280+ (合并去重后)
**对 MoA Gateway Pro 的 HIGH 价值能力**: ~85 项
**优先级 ⭐ HIGH (必须做)**: 路由 / 复杂度打分 / MoA 聚合 / OpenAI 兼容 / 密钥扫描 / 9 类 OpenAI 协议 / SSE 流式
**优先级 🔸 MED (推荐做)**: RAG 注入 / 反馈回路 / quorum 宽限窗 / 反群体思维纪律栈 / 4 段裁决 UI / i18n
**优先级 ⚪ LOW (可选做)**: 多租户 / OpenFGA / OCSF 审计 / SCIM v2 / 8 层权限栈 / 9 阶段管线

---

## 1. 路由 / 调度 (Router / Scheduler / Load Balancing / Circuit Breaker / Degradation)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| R-01 | **9 阶段请求管线** | 01 | 解析→打分→Plan/Act→effort_override→greeting→CI 选模型→压缩→RAG→清洗→分派 | P2 | ⚪ |
| R-02 | **25 维 prompt 特征提取** | 01 | 启发式 + regex + 域检测,转 0-1 score | P1 | ⭐ |
| R-03 | **集成投票器 (ensemble voter)** | 01 | heuristic + cascade + RAG + historyBias 加权,4 路径 | P1 | ⭐ |
| R-04 | **Tier 边界动态重校准** | 01 | 网格搜索 5 阈值在 labeled 数据上,触发自动 retrain | P1 | ⭐ |
| R-05 | **序数 Logistic 回归分类器** | 01 | 累积 logit 5 阈值,可加载预训练权重 | P2 | 🔸 |
| R-06 | **消费智能引擎 (ConsumptionIntelligence)** | 01 | 静态优先 + 动态 fallback + vision 降级 + 自愈 tier 重新平衡 | P1 | ⭐ |
| R-07 | **提供者健康评分** | 01 | 100-(-rateLimitHits×15 - consecutive429s×25 ...),0-100 | P1 | ⭐ |
| R-08 | **多窗口配额 (5h/weekly/monthly)** | 01 | rolling window + ETA 到耗尽 | P1 | ⭐ |
| R-09 | **RAG 关键词检索** | 01 | 关键词重叠排序 + 24h TTL + maxResults=3 | P0 | ⭐ |
| R-10 | **Plan/Act 模式解析** | 01 | 24+14 关键词 + 11+8 正则 → 置信度 0-1 | P0 | ⭐ |
| R-11 | **7 阶段消息清洗** | 01 | system 前置 + 合并同角色 + 合成 user 注入 + orphan 剥离 | P1 | ⭐ |
| R-12 | **TurboQuant 压缩 (Q8/Q4/Q2/Q1/Q0)** | 01 | 5 级量化 + 结构不变量 + 60 msg HARD CAP + 30 msg PRESERVE | P2 | 🔸 |
| R-13 | **重要性评分 (radius 0-1)** | 01 | recency + tool_result + tool_calls + decision + system 加权 | P1 | 🔸 |
| R-14 | **多 provider 探活 (60s 缓存)** | 01 | CLI execSync + HTTP /models + mini chat | P0 | ⭐ |
| R-15 | **自愈 tier 重新平衡** | 01 | 失败时自动切换 provider,5 min check 恢复 | P1 | ⭐ |
| R-16 | **能力档 (Capability Tier 5 档)** | 04 | Frontier/Flagship/Balanced/Fast/Light,failover 限同档 | P1 | 🔸 |
| R-17 | **Per-provider 限流** | 04 | MAX_REQUESTS_PER_MIN + MAX_INPUTS_PER_MIN + 并发池 | P1 | ⭐ |
| R-18 | **Provider retry + 429 cooldown** | 04 | 指数退避 1s→60s,因子 2.0 | P0 | ⭐ |
| R-19 | **Provider failover 链** | 05,04 | fallback 列表,永久错误立即试下一条 | P0 | ⭐ |
| R-20 | **Quorum 宽限窗 (grace)** | 05 | 达 quorum 后 30s 内允许落伍者,shutdown(wait=False) 立即返回 | P1 | ⭐ |
| R-21 | **L0 闸门 (机械验证不启动 MoA)** | 05 | 关键词负信号 (计算/首都/翻译 等) → 跳过 MoA | P0 | ⭐ |
| R-22 | **自我 MoA (Self-MoA)** | 05 | 单模型 + N 席 = 复制,无显式 `--self-moa` 标志 | P0 | ⭐ |
| R-23 | **CH1/CH2/CH3 三通道** | 05 | 子代理 + codex CLI + API,fallback 链降级 | P1 | ⭐ |
| R-24 | **CH2 codex CLI 错误分类** | 05 | 4 类错误:auth/timeout/cli/empty | P1 | 🔸 |
| R-25 | **直接路由 (direct_route)** | 01 | 旁路所有评分,固定 provider+model | P0 | ⭐ |
| R-26 | **决策路由 (orchestador 4-layer merge)** | 09 | runtime > project JSON > user JSON > default | P0 | ⭐ |
| R-27 | **iter 收敛循环 + 3 重保险** | 09 | max_iter + umbral_convergencia + regression = STOP | P1 | ⭐ |
| R-28 | **RESTate 持久化 vqueues** | 04 | per-tenant scope + ingress `/restate/scope/.../call/...` | P2 | ⚪ |
| R-29 | **Worker 调度 (Lottery/Shortest-Queue)** | 02 | 随机 vs 队列最短 worker | P1 | ⚪ |
| R-30 | **Nginx 多 worker LB** | 02 | upstream + health check | P1 | 🔸 |
| R-31 | **Elo ranking + Bootstrap CI** | 02 | Bradley-Terry, K=4, 1000 重采样 | P1 | ⚪ |
| R-32 | **Switchest 失效重路由** | 08 | Promise.all 防失血,永不 reject | P0 | ⭐ |
| R-33 | **Dry-run 成本估算** | 05 | `--dry-run` 输出每席计费判定,subagent+api fallback ⚠ | P0 | ⭐ |
| R-34 | **Agent 注册表 (multi-agent)** | 01 | tierProfile × provider,默认 6 profile | P1 | 🔸 |
| R-35 | **Model discovery (15 min 轮询)** | 01 | 主动探活 + 静态 catalog 兜底 | P0 | ⭐ |
| R-36 | **CLI Provider 调度** | 01 | Claude Code / Codex / Pi / Hermes / OpenClaw subprocess | P1 | 🔸 |
| R-37 | **CLI 并发限制 (ConcurrencyLimiter)** | 01 | max=1/2/3 per provider | P1 | 🔸 |
| R-38 | **Bubbletea TUI (4 标签)** | 01 | Header/Providers/Models/Tiers/Activity | P2 | ⚪ |
| R-39 | **CliProvider 健康检查** | 01 | execSync + 30s 缓存 + OAuth 凭证感知 | P0 | ⭐ |
| R-40 | **Provider Quota 仪表化** | 01 | 5h/weekly/monthly/all-time + ETA 耗尽 | P1 | 🔸 |
| R-41 | **Trivial Fast-Path** | 01 | effort=trivial + model ≤ 2B + user < 60 chars + ollama | P0 | ⭐ |
| R-42 | **Greeting 快路径** | 01 | 长度 < 30 + 匹配 GREETING_RE → 跳过打分 | P0 | ⭐ |
| R-43 | **Provider 鉴权感知健康探针** | 01 | `bin/cli-health-probe.sh` 同时验证 binary + OAuth | P1 | 🔸 |
| R-44 | **媒体内容保护 (vision-only)** | 01 | base64 → [image] 降级,vision-capable 收原图 | P0 | ⭐ |

---

## 2. MoA 编排 (Proposer / Aggregator / Multi-Layer / Single-Proposer / Ranker / Judge)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| M-01 | **单层 MoA (2-layer 极简版)** | 02,08 | N proposer 并行 + 1 aggregator 合成,50 行 | P0 | ⭐ |
| M-02 | **多层 MoA (N-layer)** | 02 | 3-layer: 每一层把上轮结果作为 references 喂回 proposer | P1 | ⭐ |
| M-03 | **System Prompt 注入模式 (inline)** | 08,02 | 保留原 system 追加,无则前插,**不用 [agg_system, *originals] 旧模式** | P0 | ⭐ |
| M-04 | **MoA 核心聚合 prompt 模板** | 02 | "你已获得 N 个开源模型响应...综合为高质量答复..." | P0 | ⭐ |
| M-05 | **Aggregator 重试 (空响应)** | 08 | MAX_AGGREGATION_ATTEMPTS=2,空响应时加 system 指令再试 | P0 | ⭐ |
| M-06 | **Aggregator 流式 + 非流式** | 08 | 流式优先 + 失败回退非流式 (120 字符 chunk) | P1 | ⭐ |
| M-07 | **Tool call 重放 (aggregator)** | 08 | reference tool_calls 序列化为候选,aggregator 自主 emit | P1 | ⭐ |
| M-08 | **response_format 强化** | 08 | json_schema/json_object 时注入"必须严格 JSON"指令 | P0 | ⭐ |
| M-09 | **Tool choice 防循环** | 08 | tool_choice="none" 在 after_tool_execution 阶段 | P0 | ⭐ |
| M-10 | **Late role 头发送** | 08 | 只在有 content/tool_call delta 时补发 role 头 | P1 | ⭐ |
| M-11 | **Reference 模型分流** | 08 | 参考模型始终非流式,只有 aggregator 流式 | P0 | ⭐ |
| M-12 | **Provider 池 + lazy init** | 01 | 每个 model 名解析 base_url + api_key,懒构造 | P0 | ⭐ |
| M-13 | **多模型评估 (5 维 TQ/CO/AP/SE/IN)** | 09 | 每维 0-10,总 50,引用 1-2 句原文 | P1 | 🔸 |
| M-14 | **多模式综合器 (4 模式)** | 09 | classification / integrated_synthesis / final_selection / cross_iteration | P1 | ⭐ |
| M-15 | **Integrated synthesis (curation, not invention)** | 09 | 200-400 行整合,只取原始提案提到的想法,标注 Source attribution | P1 | ⭐ |
| M-16 | **CONVERGENT 想法检测 (3+ 提案)** | 09 | 独立提及 → 强信号,逐字保留 | P1 | ⭐ |
| M-17 | **CONFLICTING 选择仲裁** | 09 | 选 evaluator 信号 + 命令可编译性 + viability 强证据 | P1 | ⭐ |
| M-18 | **Empirical validation (per-section viability)** | 09 | 0/1/2/3+ sections ❌ → AP = 10/5-7/2-4/1 | P1 | ⭐ |
| M-19 | **Feedback-aware iteration** | 09 | iter-N 提案器读 iter-1 03/04/05,跨迭代知识传递 | P1 | ⭐ |
| M-20 | **filter_low_performers** | 09 | iter>=2 时,分数 < threshold 的模型被丢弃,keep_minimo | P1 | 🔸 |
| M-21 | **单评估器 vs 多评估器 (multi_eval)** | 09 | 默认单 evaluator (consensus > 85%),opt-in multi-eval 取平均 | P1 | 🔸 |
| M-22 | **3 阶段元 Prompt 协议 (单模型)** | 03 | 角色分化 → 结构化对抗 → 逻辑熔铸 | P1 | ⭐ |
| M-23 | **认知摩擦对抗 (批判者 ↔ 专家)** | 03 | 1-2 轮迭代,显式攻击 + 修正方案 v2 | P1 | ⭐ |
| M-24 | **3 次认知跃迁 (回答者→导演)** | 03 | 角色分化 / 显性对抗 / 过程熔铸 | P1 | ⭐ |
| M-25 | **动态角色指派 (4-6 个)** | 03 | 战略规划师根据任务特征动态选派 | P0 | ⭐ |
| M-26 | **冲突消解 (熔铸决策者)** | 03 | 不靠投票 → 主持虚拟辩论 → 基于逻辑裁决 | P1 | ⭐ |
| M-27 | **LLM-as-Judge 单答评分** | 02 | `[[rating_a]]` regex 解析 1-10 分 | P1 | 🔸 |
| M-28 | **LLM-as-Judge 双答对战** | 02 | 抗位置偏置 (A/B 位置交换 2 轮) | P1 | 🔸 |
| M-29 | **FLASK 12 维技能评分** | 02 | robustness/correctness/efficiency/.../harmlessness | P1 | ⚪ |
| M-30 | **4 段裁决 UI (consensus/divergence/blindspots/verdict)** | 10 | JSON 解析容忍 ```json``` 围栏 + prose 提取 | P0 | ⭐ |
| M-31 | **Judge Strategy (single/collision)** | 10 | 单裁判 vs 多裁判对同 panel 各自给裁决 | P0 | ⭐ |
| M-32 | **MoA Mode (simple/advanced)** | 10 | 简单单裁判 vs 高级 collision 多裁判 | P0 | ⭐ |
| M-33 | **per-section viability 报告** | 09 | 复杂提案分节验证,单节失败不淘汰整篇 | P1 | ⭐ |
| M-34 | **Task 分解树 (高内聚低耦合)** | 03 | 战略规划师拆任务,叶子必须有"专家+批判者" | P1 | ⭐ |
| M-35 | **方案版本化 (v1 → v2)** | 03 | 每次批判对应修正,形成"攻击-回应"对偶 | P1 | 🔸 |
| M-36 | **3 反群体思维对冲** | 05 | 发言序轮转 + changed_by_new_argument + 收尾盲投漂移 | P1 | ⭐ |
| M-37 | **谄媚计数器 (sycophancy_alert)** | 05 | flips_toward_majority / movers > 0.5 → 告警 | P0 | ⭐ |
| M-38 | **从众检测 (conformity_alerts)** | 05 | changed_position 但 changed_by_new_argument=false | P0 | ⭐ |
| M-39 | **假讨论检测 (pseudo_discussion_rounds)** | 05 | all turns have empty new_argument | P0 | ⭐ |
| M-40 | **立场漂移检测 (讨论 vs 盲投)** | 05 | drift_pair.disagree → 按盲投计其真实立场 | P0 | ⭐ |
| M-41 | **同源去重 (synthesis 硬规则 1)** | 05 | 多位委员基于同一材料 → 算 1 个证据源 | P0 | ⭐ |
| M-42 | **保留分歧 (禁止自行折中)** | 05 | 矛盾判断必须原样进入"待人工裁决" | P0 | ⭐ |
| M-43 | **ARBITER-UNVERIFIED 标** | 05 | 仲裁人自查门,自己新增强结论必须附工具证据 | P0 | ⭐ |
| M-44 | **早停信号 (consensus + no disputed)** | 05 | review 全一致 → early_stop;decide all same → early_stop | P0 | ⭐ |
| M-45 | **MoA 主题群 / 头脑风暴 5 发散人格** | 05 | radical_innovator / cross_industry_transplanter / ... | P1 | 🔸 |
| M-46 | **Opening-time 匿名化 (甲乙丙丁戊己庚辛)** | 05 | 8 标签循环,精炼轮去身份化 | P0 | ⭐ |
| M-47 | **Decide 模式动态角色注入 (advocate_<选项>)** | 05 | 按选项动态注入专家席 | P1 | ⭐ |
| M-48 | **Opening 协议 (BRIEFING → ROUTING → SYNTHESIS)** | 05 | 4 步:briefing 简报 → 路由 auto → 合成 → 报告 | P0 | ⭐ |
| M-49 | **L3 开会讨论 3 硬门** | 05 | L3 + 根本分歧未化解 + 用户显式要 → 才开 | P0 | ⭐ |
| M-50 | **Cross-iteration synthesis** | 09 | convergence / best of each iter / recommended adoption | P1 | ⚪ |
| M-51 | **Multi-eval consensus averaging** | 09 | fan-out + 取平均,自动评估偏差 -0.51~+1.89 | P1 | ⚪ |
| M-52 | **Step-5 三种模式** | 09 | sintesis_central (默认) / self_improve / skip | P1 | ⭐ |
| M-53 | **Per-judge temperature (0.0-0.3)** | 09,10 | 评估器 temp=0.0,综合器 temp=0.1,裁判 temp=0.3 | P0 | ⭐ |
| M-54 | **MoA 后端参数注入** | 01 | inline system 不破坏 native tool calling | P0 | ⭐ |

---

## 3. 多智能体 (Agent 抽象 / 通信 / 编排 / 规划 / 记忆)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| A-01 | **YAML Frontmatter Agent 定义** | 06,09 | description + mode + model + temperature + tools | P0 | ⭐ |
| A-02 | **27 个 Hook 事件 (Claude Code)** | 06 | SessionStart/PreToolUse/PostToolUse/Stop/... | P1 | ⭐ |
| A-03 | **8 层配置合并栈** | 06 | Policy > User > Project > Local > Plugin > Skill > Session > Builtin | P1 | ⭐ |
| A-04 | **8 层权限栈** | 06 | Hook > BypassMode > PlanMode > 8 tier 遍历 | P2 | ⚪ |
| A-05 | **5 个 Permission Mode** | 06 | default / acceptEdits / bypassPermissions / plan / bubble | P1 | 🔸 |
| A-06 | **Bubble Mode (parent escalate)** | 06 | fork agent 权限请求 escalate 到 parent AskUserQuestion | P1 | 🔸 |
| A-07 | **5 层宪法安全闸门** | 06 | FrozenGuard → Canary → Contradiction → RateLimiter → HumanOversight | P2 | ⚪ |
| A-08 | **9 个 Constitutional Validator Sentinel** | 06 | DRIFT / SOURCE_FILE_MISSING / FROZEN_WITHOUT_CANARY / ... | P2 | ⚪ |
| A-09 | **Frozen Zone (4-enum)** | 06 | frozen-canonical / frozen-safety / evolvable-tuning / evolvable-experimental | P1 | ⚪ |
| A-10 | **4 阶段 Ralph 反馈循环** | 06 | analyze → implement → test → review | P1 | ⭐ |
| A-11 | **5 优先级决策引擎** | 06 | MaxIter > QualityGate > Stagnation > HumanReview > Continue | P0 | ⭐ |
| A-12 | **2-tier 目标求值** | 06 | Tier 1 机械命令 + Tier 2 模型声明 | P1 | ⭐ |
| A-13 | **5-section Ceiling Report** | 06 | Claim/Evidence/Baseline/Gaps/Residual-risk | P1 | ⚪ |
| A-14 | **Tier Classification (1/3/5/10 阈值)** | 06 | observation/heuristic/rule/auto_update 4 档 | P1 | ⚪ |
| A-15 | **Auto-converge + stagnation detection** | 06 | 连续 N 次无提升 → converge | P1 | ⭐ |
| A-16 | **EARS/GEARS 模式匹配** | 06 | 5 GEARS + 6 EARS legacy 兼容 | P1 | ⚪ |
| A-17 | **Acceptance Tree (Given/When/Then)** | 06 | 嵌套继承 + 重复 ID 检测 + AC ID 正则 | P1 | ⚪ |
| A-18 | **Task Tree (TaskSegment)** | 04 | 离散任务段,带 token_cost / duration / resolution_score | P1 | 🔸 |
| A-19 | **Worker / Virtual Object (Session, Worker, Tenant)** | 04 | Restate 持久化工作流 | P2 | ⚪ |
| A-20 | **MCP 工具注册 (rmcp)** | 04,06 | Streamable HTTP + 50 个内置工具 + 6 类 | P1 | ⭐ |
| A-21 | **Artifact Schema 统一结构** | 04 | agents/skills/connectors/actions/experiment-plans 共享 schema | P1 | ⚪ |
| A-22 | **Multi-session 协调 (advisory lock)** | 06 | 3-retry/10ms-backoff,idempotent Register | P1 | 🔸 |
| A-23 | **Checkpoint (atomic write temp+rename)** | 06 | 文件锁防并发写开销 | P0 | ⭐ |
| A-24 | **In-Flight Transition 检测** | 06 | 扫描 .moai/state/ 检测 phase 转换中断 | P1 | ⚪ |
| A-25 | **Team Checkpoint Merge** | 06 | Run 累加 / Plan+Sync 取最后 | P1 | ⚪ |
| A-26 | **Event scheduling (Trigger/Neutral/Terminal)** | 04 | 反向 tail 扫描决定是否新 turn | P1 | 🔸 |
| A-27 | **Worker spawn/result/signal** | 04 | 独立 sandbox,lease key (session, worker, provider) | P1 | ⚪ |
| A-28 | **MCP Credential Proxy** | 04 | process-local, single-use, opaque grants | P1 | ⚪ |
| A-29 | **Worker Heartbeat Stale 检测** | 04 | Controller 定期清理 stale worker | P0 | ⭐ |
| A-30 | **Sandbox 多层级 (Tier 0/1/2)** | 04 | 进程内 / 容器 / MicroVM(Daytona) | P2 | ⚪ |
| A-31 | **Action Policy (Allow/Deny/AdminReview)** | 04 | shell 链解析 + globset + 规则叠加 | P1 | ⭐ |
| A-32 | **Shell 命令 Bypass Defense (CWE-214)** | 06 | `;`/`&&`/`$IFS` 检测,lex + parser 双层 | P1 | ⭐ |
| A-33 | **Plan Mode Hard Lock** | 06 | plan mode 下所有写操作强制 deny | P0 | ⭐ |
| A-34 | **HARNESS_FROZEN_* 8 sentinels** | 06 | PreToolUse 拦截 harness-learner 写 FROZEN zone | P1 | ⚪ |
| A-35 | **Audit Gate (5 步协议)** | 06 | hash → cache → invoke → route → persist | P1 | ⚪ |
| A-36 | **24h Audit Cache** | 06 | PASS 缓存 24h 复用 | P0 | ⭐ |
| A-37 | **7-day Grace Window** | 06 | FAIL 在合并后 7 天仅警告不阻塞 | P1 | ⚪ |
| A-38 | **Subagent 通信 (SendMessage/TaskCreate)** | 06 | 父子 + 同级互发 | P1 | ⭐ |
| A-39 | **MX 注解系统 (NOTE/WARN/ANCHOR/REASON/TODO/SPEC)** | 06 | 6 类代码上下文标签,16 语言 | P1 | ⚪ |
| A-40 | **MX fan-in (引用计数)** | 06 | AST 解析 + LSP query | P1 | ⚪ |
| A-41 | **Routing Observation Ledger** | 06 | append-only routing-ledger.jsonl | P0 | ⭐ |
| A-42 | **Worktree 隔离基元** | 06 | manager-develop/manager-design `isolation: worktree` | P1 | ⭐ |
| A-43 | **Worktree Snapshot/Diff** | 06 | git rev-parse + status --porcelain + ls-files | P1 | ⭐ |
| A-44 | **Moai-sidechain (mx CLI)** | 06 | 扫描/查询 MX 注解 | P1 | ⚪ |
| A-45 | **Harness Routing 3 档** | 06 | minimal / standard / thorough (5 优先级) | P1 | ⭐ |
| A-46 | **Auto-Detection Rules** | 06 | file≤3, single_domain, bugfix/docs → minimal | P1 | ⭐ |
| A-47 | **Force-Thorough Keywords** | 06 | 安全/支付/关键关键字触发 thorough 模式 | P0 | ⭐ |
| A-48 | **Tier Promotion (3 阈值 + confidence<0.70)** | 06 | 1/3/5/10 events 触发 tier 1/2/3/4 | P1 | ⚪ |
| A-49 | **Sub-agent Boundary Check** | 06 | subagent spawn 边界 + Cohabitation Guard | P1 | ⚪ |
| A-50 | **Tmux 面板编排 (CG mode)** | 06 | MaxVisible=3 pane + sensitive env argv-safe | P1 | ⚪ |

---

## 4. LLM Provider (OpenAI / Anthropic / 兼容 / 流式 / SSE / Tool calling)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| L-01 | **OpenAI Chat Completions 兼容** | 01,02,08,10 | POST /v1/chat/completions + stream + tools + response_format | P0 | ⭐ |
| L-02 | **OpenAI `/v1/models` 探测** | 10 | 自动检测 context_length,避免硬编码 | P0 | ⭐ |
| L-03 | **OpenAI 兼容 Async 客户端** | 08 | AsyncOpenAI + base_url 适配 Together/Local/Ollama | P0 | ⭐ |
| L-04 | **Anthropic Messages API (`/v1/messages`)** | 10 | `x-api-key` + `anthropic-version: 2023-06-01` | P0 | ⭐ |
| L-05 | **Anthropic system 提取** | 10 | 所有 system → 顶层 `system` 字段(\n\n 拼接) | P0 | ⭐ |
| L-06 | **Anthropic 首条 user 强制** | 10 | messages 空或首条非 user → 注入 user | P0 | ⭐ |
| L-07 | **Anthropic stream: true (强制)** | 10 | 仅设 Accept 头不够,body 必须含 stream=true | P0 | ⭐ |
| L-08 | **Anthropic content_block_delta** | 10 | 流式只取 type=content_block_delta 的 delta.text | P0 | ⭐ |
| L-09 | **双协议 SSE 解析** | 10 | OpenAI choices[0].delta.content + Anthropic content_block_delta | P0 | ⭐ |
| L-10 | **SSE 流式解析 (通用)** | 02,08,10 | `\n` 切行 + 跳过空行/注释 + 提取 `data:` + JSON.parse | P0 | ⭐ |
| L-11 | **SSE 流结束 [DONE] 处理** | 08 | 流尾追加 `data: [DONE]\n\n` | P0 | ⭐ |
| L-12 | **流式超时 + 非流式回退** | 10 | AbortController + 60s 超时,失败用 nonStreamBody | P0 | ⭐ |
| L-13 | **Tool calling (原生)** | 02,08 | OpenAI 风格 tool_calls + tool_call_id | P0 | ⭐ |
| L-14 | **Tool choice 规范化** | 08 | auto/none/required/函数名简写 → dict | P0 | ⭐ |
| L-15 | **Tool call name/args 抽取** | 08 | 嵌套 function.arguments(JSON 字符串) + 扁平 name/arguments | P0 | ⭐ |
| L-16 | **Old-style JSON tool_calls 解析** | 08 | ```json``` 围栏 + 顶层 tool_calls 字段 list | P0 | ⭐ |
| L-17 | **Response_format (json_object/json_schema)** | 08 | 注入"必须严格 JSON"指令,附 schema 全文 | P0 | ⭐ |
| L-18 | **Provider base_url 优先级** | 08 | `MOA_MODEL_<SLUG>_API_KEY > OPENAI_API_KEY > 默认 EMPTY` | P0 | ⭐ |
| L-19 | **Model slug 派生规则** | 09 | `opencode-go/mimo-v2.5-pro` → `mimo-v2.5-pro` → `mimo` | P0 | ⭐ |
| L-20 | **每模型独立 base_url 解析** | 08 | `MOA_MODEL_QWEN3_4_4B_BASE_URL` 自定义端点 | P0 | ⭐ |
| L-21 | **Azure OpenAI 适配** | 02 | openai.api_type=azure + env AZURE_OPENAI_* | P1 | 🔸 |
| L-22 | **Google PaLM-2 适配** | 02 | stateful chat_state 对象跨 turn | P2 | ⚪ |
| L-23 | **HuggingFace Inference API worker** | 02 | 部署模型到 HF Inference | P1 | ⚪ |
| L-24 | **Ollama (local + cloud)** | 01 | local HTTP /api/tags + cloud /v1/models | P0 | ⭐ |
| L-25 | **WebGPU/WebNN/WASM local** | 01 | @huggingface/transformers 本地推理 | P2 | ⚪ |
| L-26 | **量化推理 (AWQ/GPTQ/ExLlama v2)** | 02 | 4-bit 模型加载 | P2 | ⚪ |
| L-27 | **vLLM / SGLang / LightLLM / MLX workers** | 02 | 多推理后端 worker | P2 | ⚪ |
| L-28 | **Per-model 并发池** | 04 | per-provider MAX_REQUESTS_PER_MIN + MAX_CONCURRENT | P1 | ⭐ |
| L-29 | **Provider 状态 (12 字段 ModelEntry)** | 01 | contextWindow, maxTokens, modalities, supportsReasoning/Vision/Tools | P1 | ⭐ |
| L-30 | **AsyncOpenAI + asyncio.gather 并发** | 08 | 异步并行 N 个 reference | P0 | ⭐ |
| L-31 | **Stream delta 完整代理 (tool_calls index)** | 08 | 不在服务端粘合,直接代理给客户端 | P1 | ⭐ |
| L-32 | **Request ID 注入 (req_<ms_ts>)** | 08 | 日志关联客户端 trace | P0 | ⭐ |
| L-33 | **Parameter 净化 (allowlist)** | 08 | 硬编码 25 项 + 剥离 headers/api_key/extra_query | P0 | ⭐ |
| L-34 | **Pydantic v1 兼容 (try: from pydantic.v1 import)** | 02 | 兼容老版本 schema | P0 | ⭐ |
| L-35 | **EventSource SSE 客户端** | 04 | eventsource-stream crate | P0 | ⭐ |
| L-36 | **Embedding 端点 + 批处理** | 02,04 | /v1/embeddings + WORKER_API_EMBEDDING_BATCH_SIZE | P1 | ⭐ |
| L-37 | **Cohere v4 Embedding + Rerank** | 04 | latency-bounded rerank | P1 | 🔸 |
| L-38 | **Stream tool_call delta 保留 index** | 08 | 首个 delta 保留 id/type + 部分 arguments | P1 | ⭐ |
| L-39 | **finish_reason 透传 (tool_calls/stop)** | 08 | 流结束帧 finish_reason 处理 | P0 | ⭐ |
| L-40 | **Cleanup message 字段白名单** | 08 | 只保留 role/content/name/tool_call_id/tool_calls | P0 | ⭐ |

---

## 5. 评估 / 打分 (FLASK / BLEU / ELO / Quality Scoring / Pain Score / 神经衰弱)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| E-01 | **Pain Score (0-100)** | 07 | 7 类权重 (core_business=30, auth_payment=40, race=25...) | P1 | ⭐ |
| E-02 | **Pain Score 等级映射** | 07 | ≥75=CRITICAL, ≥50=HIGH, ≥25=MEDIUM, else LOW | P0 | ⭐ |
| E-03 | **Enhanced Pain Scorer** | 07 | 加载进化规则动态调整 multiplier (1.0 + confidence*0.5) | P1 | ⭐ |
| E-04 | **架构健康评分 (0-100)** | 07 | 基础 100 - high_entropy×20 - dependency_hub×5 | P1 | ⭐ |
| E-05 | **验收算子评分** | 07 | CRITICAL -20 / ERROR -10 / WARNING -5 / INFO -1 | P0 | ⭐ |
| E-06 | **神经衰弱检测 (5 维度)** | 07 | refactor 0.25 + perf 0.20 + bug_fix 0.20 + velocity 0.20 + fp -0.15 | P1 | 🔸 |
| E-07 | **FLASK 12 技能评分** | 02 | robustness/correctness/efficiency/factuality/.../harmlessness | P1 | ⚪ |
| E-08 | **5 难度 × 12 技能 = 60 维交叉表** | 02 | 细粒度评估矩阵 | P2 | ⚪ |
| E-09 | **LLM-as-Judge 单答评分** | 02 | `\[\[(\d+\.?\d*)\]\]` regex 解析 1-10 分 | P1 | ⭐ |
| E-10 | **LLM-as-Judge 双答对战** | 02 | 同题位置交换 2 次抗偏置 | P1 | ⭐ |
| E-11 | **TIE_DELTA=0.1 平局容差** | 02 | abs(score_diff) ≤ 0.1 → tie | P0 | ⭐ |
| E-12 | **Elo ranking (Bradley-Terry)** | 02 | K=4, INIT_RATING=1000, 平局各 0.5 | P1 | ⭐ |
| E-13 | **Elo Bootstrap CI (1000 重采样)** | 02 | 95% 置信区间 | P1 | ⚪ |
| E-14 | **Win rate adjusted (win+0.5*tie)/total** | 02 | 标准化胜率 | P0 | ⭐ |
| E-15 | **5 维 TQ/CO/AP/SE/IN 评估** | 09 | 每维 0-10,总 50,引用 1-2 句原文 | P1 | ⭐ |
| E-16 | **Per-section viability** | 09 | 0/1/2/3+ sections ❌ → AP = 10/5-7/2-4/1 | P1 | ⭐ |
| E-17 | **Multi-eval averaging** | 09 | fan-out + 取平均,自动评估偏差 -0.51~+1.89 | P1 | 🔸 |
| E-18 | **Adequacy score (heuristic)** | 01 | token 范围 (0.4) + 长度 (0.2) + 延迟 (0.2) + 重复率 (0.2) | P1 | ⭐ |
| E-19 | **LLM Judge (qwen3.6-plus)** | 01 | 10% 采样,JSON {adequacy, correct_tier} | P1 | ⭐ |
| E-20 | **Anti-circularity (judge 比被评强)** | 01 | judge 总是比被评 tier 更强 | P0 | ⭐ |
| E-21 | **Segment resolution rate (0-1)** | 04 | resolved / partial / failed by tenant & skill | P1 | ⭐ |
| E-22 | **Task Strategy Success Rate** | 04 | 任务指纹+策略成功率 | P1 | 🔸 |
| E-23 | **Quality Gate (feedback 5 条件)** | 06 | TestsFailed==0 ∧ LintErrors==0 ∧ BuildSuccess ∧ Coverage>=85% | P1 | ⭐ |
| E-24 | **Hierarchical Scoring (HRN-003)** | 06 | sub-criteria min/mean 聚合,二选一评分模型 | P1 | ⚪ |
| E-25 | **4 维质量审计 (F40/S25/C20/Cons15)** | 06 | Functionality / Security / Craft / Consistency 加权 | P1 | ⭐ |
| E-26 | **Pearson / Spearman 相关性** | 01 | 评估器间一致性 | P1 | ⚪ |
| E-27 | **EvalEngine 5 类 metrics** | 04 | kernel / cost / counting / stats / compare | P1 | ⚪ |
| E-28 | **EvalLineageHandle (eval 期 lineage)** | 04 | 抓取 eval 期间 lineage | P1 | ⚪ |
| E-29 | **TrajectoryCollector** | 04 | 收集执行轨迹 | P1 | ⚪ |
| E-30 | **Per-juror adjudicated scoring** | 09 | multi-eval fan-out + 平均 | P1 | 🔸 |
| E-31 | **L1 Subsystem Content Hash** | 07 | 子系统健康 + 内容哈希对比 | P0 | ⭐ |
| E-32 | **Dependency Hub (top 10 + threshold ≥ 5)** | 07 | AST 遍历 import,统计被引用 | P0 | ⭐ |
| E-33 | **代码熵增检测 (curr - base) / base * 100** | 07 | >100% 红,>50% 黄 | P0 | ⭐ |
| E-34 | **圈复杂度 (McCabe) / 认知复杂度 (SonarSource)** | 07 | if+1, 循环+2, 嵌套+1, 递归+3 | P1 | ⭐ |
| E-35 | **死代码 / 重复代码检测** | 07 | DeadCodeDetector AST Visitor + 5 行滑动窗口 | P1 | ⭐ |
| E-36 | **API-002 鉴权检测 (多框架)** | 07 | Flask/FastAPI/Django/Express/Gin/Fiber 装饰器 | P1 | ⭐ |
| E-37 | **Karpathy 原则 (Simplicity First)** | 07 | max_function_lines=50, max_class_methods=15, max_cyclomatic=10 | P0 | ⭐ |
| E-38 | **Surgical Changes** | 07 | max_diff_lines=100, max_files_changed=3 | P0 | ⭐ |
| E-39 | **Socratic Interview (4-quadrant)** | 06 | Known-Knowns/Unknowns/Unknown-Knowns/Unknown-Unknowns | P1 | ⚪ |
| E-40 | **Sycophancy / Conformity / Pseudo-discussion 计量** | 05 | 3 类群体思维检测 (movers, flips_toward_majority) | P1 | ⭐ |

---

## 6. 提示词工程 (Meta-Prompt / 角色 / 模板 / 协议)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| P-01 | **核心 MoA 聚合 Prompt 模板** | 02 | "你已获得 N 个开源模型响应...综合为高质量..." | P0 | ⭐ |
| P-02 | **Inline System 注入 (不破坏 tool calling)** | 08 | 保留原 system 追加,无则前插 | P0 | ⭐ |
| P-03 | **3 阶段元 Prompt 协议 (单模型)** | 03 | 战略规划师→专家群→批判者→熔铸决策者 | P0 | ⭐ |
| P-04 | **动态角色指派 API (4-6 个具体角色)** | 03 | 战略规划师根据任务特征动态选派 | P0 | ⭐ |
| P-05 | **阶段输出格式契约 (角色标识符)** | 03 | `[战略规划师]:`/`[专家A]:`/`[批判者]:`/`[熔铸决策者]:` 前缀 | P0 | ⭐ |
| P-06 | **认知摩擦回路 Prompt** | 03 | 批判者 → 攻击 → 专家 → 回应 → 修正 v2 | P0 | ⭐ |
| P-07 | **冲突消解 Prompt** | 03 | 熔铸决策者主持虚拟辩论,总结争论焦点 | P0 | ⭐ |
| P-08 | **3 次认知跃迁 (回答者→导演)** | 03 | 角色分化 / 显性对抗 / 过程熔铸 | P0 | ⭐ |
| P-09 | **任务分解算法 Prompt (高内聚低耦合)** | 03 | 写游戏 → 玩法设计 + 剧情 + 前端 + 后端 + 测试 | P0 | ⭐ |
| P-10 | **失败模式诊断表 (4 类)** | 03 | 角色泛化 / 批判空泛 / 修正敷衍 / 熔铸拼盘 | P0 | ⭐ |
| P-11 | **防截断机制 (Prompt 内置)** | 03 | 阶段 2-3 允许"仅输出核心逻辑",详细保留至阶段 4 | P0 | ⭐ |
| P-12 | **OpenAI 兼容 Error 输出哨兵 ($ERROR$)** | 02 | 失败返回 "$ERROR$" 字符串,便于解析过滤 | P0 | ⭐ |
| P-13 | **3 个内嵌 Judge 提示词模板** | 10 | default / strict-logic / multi-perspective | P0 | ⭐ |
| P-14 | **3 个中文版 Judge 提示词** | 10 | 中英双语 prompt template | P0 | ⭐ |
| P-15 | **Role Template (Panel 角色)** | 10 | 中性通用思维工具 (criticalScrutiny / domainXpert / ...) | P0 | ⭐ |
| P-16 | **YAML Frontmatter (Agent Skills 规范)** | 03,06 | name + description + tools + temperature | P0 | ⭐ |
| P-17 | **Briefing 简报 API** | 05 | 用户写 brief.md,include user prompt + context + constraints | P0 | ⭐ |
| P-18 | **Critical (审查) 模式 5 角色** | 05 | feasibility_skeptic / maintainability / security / user_advocate | P0 | ⭐ |
| P-19 | **Decide (决策) 模式动态角色** | 05 | advocate_<选项> 动态注入 | P0 | ⭐ |
| P-20 | **Brainstorm (头脑风暴) 5 角色** | 05 | radical_innovator / cross_industry_transplanter / ... | P0 | ⭐ |
| P-21 | **3 套中英 Judge 模板 × 3 = 9 默认** | 10 | 内置可用 | P0 | ⭐ |
| P-22 | **Template loading 3 级 fallback** | 05 | custom_roles > md 段落 > 兜底 | P0 | ⭐ |
| P-23 | **Temperature 任务类型映射** | 02 | writing/roleplay=0.7, extraction/math/coding=0.0, stem/humanities=0.1 | P0 | ⭐ |
| P-24 | **NEED_REF_CATS (4 类带 reference)** | 02 | math/reasoning/coding/arena-hard-200 | P0 | ⭐ |
| P-25 | **引用注入 API (message + references)** | 02 | `f"\n{i+1}. {response}"` 编号列表追加 system | P0 | ⭐ |
| P-26 | **feedback-aware iteration 块** | 09 | 提案器在 iter-N 必读 iter-1 03/04/05 | P0 | ⭐ |
| P-27 | **Empirical Testing 块 (encouraged but bounded)** | 09 | 鼓励 `cargo check`,禁 `cargo tauri build` | P0 | ⭐ |
| P-28 | **Per-model 角色绑定 (10 个 propuesta-*.md)** | 09 | frontmatter `model: opencode-go/X` | P0 | ⭐ |
| P-29 | **"Source attribution" 节** | 09 | 每个引用标 `propuesta-{modelo}.md` + 行号 | P0 | ⭐ |
| P-30 | **"Why this beats the field" 节** | 09 | 跨参 03 文件,赢提案弱点被设计选择解决 | P0 | ⭐ |
| P-31 | **Anti-hallucination 原则** | 09 | 必须有具体可执行命令,不确定建议替代方案 | P0 | ⭐ |
| P-32 | **Prohibitions (per-section 失败处理)** | 09 | bash 验证 30s 超时,不可验证标 ⏭️ SKIP 而非编造 | P0 | ⭐ |

---

## 7. 数据模型 (Schema / Event / Tenant / Workspace / Session / Artifact)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| D-01 | **V04Config (集成权重 + tier_boundaries)** | 01 | EnsembleWeightsConfig + 5 tier 模型 + FeedbackLoop | P1 | ⭐ |
| D-02 | **AgentConfig (id/apiKey/provider/tierConfig)** | 01 | 6 tierProfile 默认,requestCount + totalTokensIn/Out | P1 | ⭐ |
| D-03 | **ProviderQuota (rpm/rpd/token 3 窗口)** | 01 | healthScore 0-100,throttled + consecutive429s | P1 | ⭐ |
| D-04 | **FeedbackEntry (predictedTier vs actualTier)** | 01 | 10000 条上限,escalated, userSatisfaction | P1 | ⭐ |
| D-05 | **RagEntry (keywords + tags + tier)** | 01 | 10000 条 + 24h TTL,summary + compressedTokens | P1 | ⭐ |
| D-06 | **VoteRecord (3 源 gold/silver/bronze)** | 01 | weight + expiresAt + voted + userAgreed | P1 | ⭐ |
| D-07 | **CombinedLabel (RAG bootstrap 阶段)** | 01 | disabled (0-50) → low (50-200) → full (200+) | P1 | ⭐ |
| D-08 | **TurboQuant CompressResult (5 级)** | 01 | messages + originalTokens + kvCacheEstimateBytes | P1 | 🔸 |
| D-09 | **CliProviderConfig (argsTemplate)** | 01 | spawn(command, args, timeout, env, workingDir) | P1 | ⭐ |
| D-10 | **ModelEntry (12 字段, modality)** | 01 | supportsReasoning/Vision/Tools,totalTokensIn/Out | P1 | ⭐ |
| D-11 | **TaskSegment (token_cost + outcome)** | 04 | tools_used + skills_activated + resolution_score 0-1 | P1 | ⭐ |
| D-12 | **LearningCandidate 状态机** | 04 | proposed → evaluating → promoted/rejected | P1 | 🔸 |
| D-13 | **LearningEntry (双时态 valid_from/to)** | 04 | batch_id 支持 batch 回滚 | P1 | ⚪ |
| D-14 | **Contact / Channel / Attachment** | 04 | Chat/Slack/Email/SMS 4 channel + 32 字节 HMAC 哈希 | P1 | 🔸 |
| D-15 | **ActionEnvelope + ActionPolicyRule** | 04 | LocalWrite/NetworkWrite/ExternalService/SystemAction | P1 | ⭐ |
| D-16 | **OCSF Event (4 类)** | 04 | Authentication (3002) / Authorization (3003) / AccountChange (3001) / EntityManagement (3004) | P1 | ⚪ |
| D-17 | **Event 40+ 变体 (strum EnumDiscriminants)** | 04 | SessionCreated/UserMessage/ToolCall/WorkerSpawned/... | P1 | 🔸 |
| D-18 | **Tenant (storage_partition_id = UUID)** | 04 | RLS scope key | P1 | ⚪ |
| D-19 | **Session (UUID newtype)** | 04 | SessionId/SegmentId/TenantId/BrainId/ToolCallId | P1 | ⭐ |
| D-20 | **ProcessingEffect (Trigger/Neutral/Terminal)** | 04 | 反向 tail 扫描决定是否新 turn | P1 | ⭐ |
| D-21 | **ExperienceRecord + Attribution** | 04 | task_fingerprint + success_rate + skills/tools/memory chain | P1 | ⚪ |
| D-22 | **Artifact (moa-artifact-v1.schema.json)** | 04 | agents/skills/connectors/actions/experiment-plans 共享 | P1 | ⚪ |
| D-23 | **CliInputFormat (stdin/arg) + OutputFormat** | 01 | stdout-text / stdout-json | P0 | ⭐ |
| D-24 | **TierBoundaries 5 元组** | 01 | [0.208938, 0.264209, 0.32502, 0.36585, 0.485382] | P0 | ⭐ |
| D-25 | **ModelDefinition (TPS 3 backend)** | 01 | webgpu / webnn / wasm 三档 | P1 | ⚪ |
| D-26 | **BenchmarkLogEntry (cost_usd vs baseline)** | 01 | BASELINE_MODEL = claude-opus-4.6 | P1 | ⭐ |
| D-27 | **MoA Session (4 域 config)** | 10 | providers / roleTemplates / judgePrompts / sessions | P0 | ⭐ |
| D-28 | **AIProvider (id/name/modelString/baseUrl/apiKey/protocol)** | 10 | 5 必填 + capabilities?.maxContextChars | P0 | ⭐ |
| D-29 | **MoaMode (simple/advanced) + JudgeStrategy (single/collision)** | 10 | 2×2 模式组合 | P0 | ⭐ |
| D-30 | **MoASessionConfig (panelIds/panelRoles/judgeIds)** | 10 | 7 字段会话级调度配置 | P0 | ⭐ |
| D-31 | **SynthesisRequest/Response (4 段裁决契约)** | 10 | consensus/divergence/blindspots/verdict | P0 | ⭐ |
| D-32 | **PanelState (label/model snapshot 保护)** | 10 | provider 编辑不影响历史 | P0 | ⭐ |
| D-33 | **JudgeState (5 status: pending/judging/streaming/done/error)** | 10 | 状态机 | P0 | ⭐ |
| D-34 | **Turn + ChatSession** | 10 | prompt + panels + judges 一次完整流程 | P0 | ⭐ |
| D-35 | **MoaCallbacks (10 个事件回调)** | 10 | onPanelStart/Delta/Retry/Done/Error/Skipped/Complete + onJudge* | P0 | ⭐ |
| D-36 | **out/{id}/iter-{N}/ 文件结构** | 09 | 01-12 propuesta + 02-* validacion + 03 evaluador + 04 clasificacion + 05 integrada + 07 final + 08 ganador + 09 sumario | P0 | ⭐ |
| D-37 | **ID 验证 `^[a-z0-9][a-z0-9-]{2,29}$`** | 09 | 3-30 字符,小写,数字,连字符 | P0 | ⭐ |
| D-38 | **resumability (glob 检测已有文件)** | 09 | 重跑时跳过已存在的 step | P0 | ⭐ |
| D-39 | **ComposedRole (configurable MoA profile)** | 09 | modelos_a_competir + modelo_objetivo + step_5_modo + ... | P0 | ⭐ |
| D-40 | **10 modelo_a_competir 配置** | 09 | 8 默认 + 2 可选,frontmatter 绑定 | P0 | ⭐ |
| D-41 | **ModelEntry 上下文窗口自动检测** | 10 | /models API → 内置数据库 → undefined | P0 | ⭐ |
| D-42 | **Entry (Session) 9 字段** | 06 | SessionID/SpecID/Phase/StartedAt/LastHeartbeat/PID/Host/CWD | P1 | 🔸 |
| D-43 | **LoopState + Decision** | 06 | SpecID/Phase/Iteration/MaxIter/Feedback[] | P1 | 🔸 |
| D-44 | **Goal + Verdict + FailedCond + CeilingVerdict** | 06 | 2-tier 求值:机械条件 + 模型声明 | P1 | 🔸 |
| D-45 | **Event (JSONL) 12+ 字段** | 06 | moai_subcommand/agent_invocation/spec_reference/feedback/... | P1 | 🔸 |
| D-46 | **Pattern + Tier (1/3/5/10 阈值)** | 06 | observation/heuristic/rule/auto_update | P1 | ⚪ |
| D-47 | **HookInput (100+ 字段)** | 06 | Claude Code → MoAI JSON stdin | P1 | ⭐ |
| D-48 | **PermissionMode (5) + Source (8 tier)** | 06 | default/acceptEdits/bypassPermissions/plan/bubble | P1 | 🔸 |
| D-49 | **Verdict (Audit 5 状态)** | 06 | PASS / FAIL / FAIL_WARNED / BYPASSED / INCONCLUSIVE | P1 | ⭐ |
| D-50 | **LoopPhase (4)** | 06 | analyze / implement / test / review | P1 | ⭐ |
| D-51 | **Snapshot (Worktree 8 字段)** | 06 | SchemaVersion/CapturedAt/SnapshotID/HeadSHA/Branch/PorcelainLines/UntrackedSpecs | P1 | ⭐ |
| D-52 | **Finding (astgrep 11 字段)** | 06 | RuleID/Severity/Message/File/Line/Column/EndLine/Language/Metadata(CWE/OWASP) | P1 | ⭐ |
| D-53 | **MX Tag (6 类)** | 06 | NOTE / WARN / ANCHOR / REASON / TODO / SPEC | P1 | ⚪ |
| D-54 | **Zone (4-enum)** | 06 | frozen-canonical / frozen-safety / evolvable-tuning / evolvable-experimental | P1 | ⚪ |
| D-55 | **PermissionRule (8 层栈规则)** | 06 | Pattern + Action + Source + Origin | P1 | ⚪ |
| D-56 | **LearningEntry (LEARN-YYYYMMDD-NNN)** | 06 | path traversal 防御 | P1 | ⚪ |
| D-57 | **40+ 模型上下文窗口数据库** | 10 | OpenAI(10) + Anthropic(6) + DeepSeek(5) + Qwen(5) + Llama(6) + Mistral(3) + Gemini(4) + Kimi(2) + GLM(2) + Grok(3) + NVIDIA(2) | P0 | ⭐ |
| D-58 | **tokensToChars (× 4 保守近似)** | 10 | 简单乘 4 | P0 | ⭐ |
| D-59 | **ConfigFile 7 字段 (provider/roleTemplate/judgePrompt/sessions/...)** | 10 | 持久化 schema | P0 | ⭐ |
| D-60 | **Judges [] 配置 (per-judge prompt)** | 10 | collision mode per-judge prompt alignment | P0 | ⭐ |

---

## 8. 安全 / 审计 (密钥扫描 / SQL 注入 / 合规 / OpenFGA / OCSF / SCIM / Canary)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| S-01 | **13 类硬编码密钥检测** | 07 | aws_access_key / github_token / slack / google / private_key | P0 | ⭐ |
| S-02 | **9 类密钥正则 (5/05 moa-skill)** | 05 | private_key / openai_key / aws / google / github / slack / bearer / secret_assign / conn_string | P0 | ⭐ |
| S-03 | **占位符抑制 (12 类)** | 07 | YOUR_ / _HERE / <[^>]+> / {{}} / CHANGE_ME / xxx / yyy / zzz | P0 | ⭐ |
| S-04 | **环境变量读取识别 (12 类)** | 07 | os.getenv / os.environ.get / process.env. / ENV[ | P0 | ⭐ |
| S-05 | **脱敏预览 (前 3 字符 + ***(len N))** | 05,07 | 短于 6 字符则全遮 | P0 | ⭐ |
| S-06 | **0 文件扫描保护 (退出码 2)** | 05 | 区别于 clean=0 / 命中=1,不冒充安全 | P0 | ⭐ |
| S-07 | **SQL 注入 AST 检测 (Tree-sitter)** | 07 | 6 类执行点 + 6 类插值模式 | P1 | ⭐ |
| S-08 | **SQL 注入增强 (ORM 10 类)** | 07 | SQLAlchemy / Django / asyncpg | P1 | ⭐ |
| S-09 | **依赖项漏洞 (pip-audit / npm audit)** | 07 | 4 优先级检测策略 | P1 | ⭐ |
| S-10 | **内置漏洞数据库 (17 个)** | 07 | requests, django, flask, pillow, urllib3, jinja2, pyyaml, cryptography + node axios/lodash/express/minimist | P1 | ⭐ |
| S-11 | **未使用导出检测 (Python/TS/Go)** | 07 | `__all__` + 函数/类定义 + ast.walk(Name/Attribute) | P1 | ⭐ |
| S-12 | **敏感材料外发前告警** | 05 | `warn_sensitive_material()` 在 generate/refine/discuss-turn 派发前自动 | P0 | ⭐ |
| S-13 | **静态密钥自查 (leak-check 子命令)** | 05 | 递归扫文本文件,跳过二进制 | P0 | ⭐ |
| S-14 | **API Key 不落盘 (os.environ)** | 05 | endpoint_and_headers 从环境读,配置文件永远不含 key | P0 | ⭐ |
| S-15 | **文件名消毒 `_safe_name` (C2)** | 05 | 仅留 `[A-Za-z0-9._-]`,产物永远不出 collect-dir | P0 | ⭐ |
| S-16 | **CH1 subagent 标识 (arbiter-dispatched)** | 05 | 区分脚本派发 vs 仲裁人外派发 | P0 | ⭐ |
| S-17 | **同源去重 (synthesis 硬规则 1)** | 05 | 多位委员基于同一材料 → 算 1 个证据源 | P0 | ⭐ |
| S-18 | **声明级数据 (委员输出当数据处理)** | 05 | 即使含 "ignore previous instructions" 也当数据 | P0 | ⭐ |
| S-19 | **禁用 `--self-moa` 标志** | 05 | 由 `--members N --models "id"` 隐式触发,防误用 | P0 | ⭐ |
| S-20 | **API Key 格式 (moma-<32 hex>)** | 01 | randomBytes(16).toString('hex') | P0 | ⭐ |
| S-21 | **GATESWARM_REQUIRE_AUTH** | 01 | 拒绝无效/缺失 key (401) | P0 | ⭐ |
| S-22 | **鉴权感知 CLI 健康探针** | 01 | 同时验证 binary + OAuth 凭证 | P0 | ⭐ |
| S-23 | **不可用响应检测 (防误判 quota 错误)** | 01 | 短文本 + 错误式开头 + rate limit 词 → unusable | P1 | ⭐ |
| S-24 | **媒体内容保护 (vision-only)** | 01 | base64 → [image] 降级,text-only 永远不见 base64 | P0 | ⭐ |
| S-25 | **OpenFGA 细粒度授权** | 04 | workspace#admin → tenant#admin → tenant#operator 继承 | P2 | ⚪ |
| S-26 | **OCSF v1.3 4 类事件** | 04 | Authentication(3002)/Authorization(3003)/AccountChange(3001)/EntityManagement(3004) | P2 | ⚪ |
| S-27 | **JCS 规范化 + HMAC-SHA256 签名** | 04 | jcs::canonicalize() per RFC 8785,恒时比较 | P2 | ⚪ |
| S-28 | **Merkle 链审计 (顺序哈希链)** | 04 | 每个 entry 含前一个 hash,周期性 Merkle root | P2 | ⚪ |
| S-29 | **S3 Object Lock COMPLIANCE 模式** | 04 | 不可篡改审计归档 (audit-shipper Python 服务) | P2 | ⚪ |
| S-30 | **SCIM v2 (Okta 兼容)** | 04 | /scim/v2/Users/Groups + 过滤器 + PATCH active=false cascade | P2 | ⚪ |
| S-31 | **Argon2 密码哈希 (min 12 chars)** | 04 | 30 min reset token TTL | P2 | ⚪ |
| S-32 | **Contact JWT (bounded scope)** | 04 | 显式 requested_scopes + agent_ids,不能 admin | P1 | ⚪ |
| S-33 | **Local API Key (4 段 random base32)** | 04 | hashed, scoped by env + tenant + agent | P1 | ⚪ |
| S-34 | **RLS (storage_partition_id = tenant UUID)** | 04 | 所有 storage crate 使用 scoped connection | P1 | 🔸 |
| S-35 | **Sandbox 分层 Tier 0/1/2** | 04 | 进程内 / 容器 / MicroVM(Daytona) | P2 | ⚪ |
| S-36 | **Prompt Injection Canary (moa_canary_)** | 04 | 每 turn UUIDv7,指示模型不要复制 | P1 | ⭐ |
| S-37 | **Tool input screening 9 类启发式** | 04 | ignore_previous / you are now / system: / delimiter_token / ... | P1 | ⭐ |
| S-38 | **Output wrapping `<untrusted_tool_output>`** | 04 | 边界 tag 内部出现则 HTML escape | P1 | ⭐ |
| S-39 | **InputInspection (Normal/MediumRisk/HighRisk)** | 04 | 0.8 高 / 0.4 中分界 | P1 | ⭐ |
| S-40 | **ActionPolicy (Allow/Deny/AdminReview)** | 04 | shell 链解析 + globset + 规则叠加 | P1 | ⭐ |
| S-41 | **DSAR 导出 (签名隐私包)** | 04 | moa-lineage-audit::export 导出受签名 | P1 | ⚪ |
| S-42 | **Subject Erasure (moa-memory-pii)** | 04 | 记忆擦除实现 | P1 | ⚪ |
| S-43 | **GitHub Secret Scanning Webhook** | 04 | POST /v1/security/secret-scanning/github | P1 | ⭐ |
| S-44 | **Auth0 Webhook (HMAC-SHA256 验证)** | 04 | connection-linked 事件 | P1 | ⚪ |
| S-45 | **Constant-time 比较 (constant_time_eq)** | 04 | 防止时序攻击 | P1 | ⭐ |
| S-46 | **MCP 凭证代理 (single-use opaque grants)** | 04 | 不持久化,同 K8s pod 内有效 | P1 | ⚪ |
| S-47 | **tenant_signing_keys (HMAC key pool)** | 04 | 10000 tenant, 5 min TTL moka cache | P1 | ⚪ |
| S-48 | **5 层 Constitution Pipeline** | 06 | FrozenGuard → Canary → Contradiction → RateLimiter → HumanOversight | P1 | ⚪ |
| S-49 | **9 Sentinel Validation (constitution)** | 06 | DRIFT / SOURCE_FILE_MISSING / ZONE_UNREGISTERED / FROZEN_WITHOUT_CANARY / ANCHOR_NOT_FOUND / DUPLICATE_ID / STALE_ENTRY / DUPLICATE_ZONE_MARKER / INVALID_ZONE_CLASS | P1 | ⚪ |
| S-50 | **OWASP Checklist (moai-ref-owasp-checklist)** | 06 | LLM Security 引用 | P1 | ⚪ |
| S-51 | **OAuth Token Preservation (update)** | 06 | 更新时保护 OAuth 凭证 | P1 | ⚪ |
| S-52 | **Strict Mode (security.yaml.strict_mode=true)** | 06 | 拒绝 bypassPermissions | P0 | ⭐ |
| S-53 | **Fail-closed 鉴权 (authn_failure sync emit)** | 04 | 失败时同步发射,异步用 spawn | P1 | ⭐ |
| S-54 | **3 层豁免 (行内/文件/配置)** | 07 | `# moat-ignore: <id>` / 文件头 / `.moat/gatekeeper_config.json` | P0 | ⭐ |
| S-55 | **审计日志 (NDJSON)** | 07 | 逐行写入 audit_log_path | P0 | ⭐ |
| S-56 | **Skip audit (`--skip-audit` / env)** | 06 | MOAI_SKIP_PLAN_AUDIT=1 显式绕过 | P1 | ⚪ |
| S-57 | **Secrecy 0.10 密钥封装** | 04 | 不暴露明文 | P1 | ⭐ |
| S-58 | **3 协议错误码 (per/error/auth)** | 05 | PermanentError / TransientError / classify_http_error | P0 | ⭐ |
| S-59 | **决策缓存 (2s TTL allow / permanent deny)** | 04 | 允许结果缓存 2s,拒绝永缓存 | P1 | ⭐ |
| S-60 | **事务性 outbox (tuple 写入)** | 04 | 与产品状态在同一 PG 事务提交,poller 异步同步 | P1 | ⚪ |

---

## 9. 性能 / 缓存 (Prompt Cache / vqueues / Redis / ClickHouse / 量化)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| PF-01 | **TurboQuant 压缩 v3.6 (5 级量化)** | 01 | Q8/Q4/Q2/Q1/Q0 + 结构不变量 | P1 | ⭐ |
| PF-02 | **HARD CAP 60 msg** | 01 | 递归删除旧的 assistant+tool_call 链 | P1 | ⭐ |
| PF-03 | **SHORT CONVERSATION SKIP (≤5 msg, ≤8K tok)** | 01 | 直接返回,跳过压缩 | P0 | ⭐ |
| PF-04 | **Importance Scoring (radius 0-1)** | 01 | recency + tool_result + tool_calls + decision + system 加权 | P1 | ⭐ |
| PF-05 | **Q0 完整丢弃 (Q2/Q1 写 RAG)** | 01 | tier-aware 压缩策略 | P1 | ⭐ |
| PF-06 | **结构不变量 (user ≥ Q4, tool ≥ Q8, system ≥ Q8, last 3 ≥ Q8)** | 01 | 防止 strict API 拒绝 | P1 | ⭐ |
| PF-07 | **多阶段 budget verify (二阶段截断 + 三阶段丢弃)** | 01 | 三阶段防御 | P1 | ⭐ |
| PF-08 | **Prompt cache (byte-stable context pipeline)** | 04 | 同一份输入下编译字节一致 | P1 | ⭐ |
| PF-09 | **CacheReport 事件持久化** | 04 | gen_ai.client.token.usage OTel metric | P1 | ⭐ |
| PF-10 | **Latency Metrics (6 步追踪)** | 04 | snapshot_load / snapshot_write / pipeline_compile / llm_call / tool_dispatch / event_persist | P1 | ⭐ |
| PF-11 | **Per-provider 限流 + 并发池** | 04 | MAX_REQUESTS_PER_MIN + MAX_INPUTS_PER_MIN + MAX_CONCURRENT_REQUESTS | P1 | ⭐ |
| PF-12 | **GlobalConcurrency + tenant-scope 协同** | 04 | Restate 准入控制 | P1 | ⚪ |
| PF-13 | **moka future cache (signing_key_cache 10000)** | 04 | 5min TTL, 决策缓存 100k 2s TTL | P1 | ⭐ |
| PF-14 | **RESTate vqueues (per-tenant scope)** | 04 | `* → concurrency 1000` 默认 | P1 | ⚪ |
| PF-15 | **LRU 文件哈希缓存 (1.7x 加速)** | 07 | SHA256 + mtime 失效 + ThreadPoolExecutor 并行 | P0 | ⭐ |
| PF-16 | **Git diff 增量 (--cached --name-only)** | 07 | 只检查修改文件 | P0 | ⭐ |
| PF-17 | **Tree-sitter 优化 (优先 AST,失败降级正则)** | 07 | 精准+容错 | P0 | ⭐ |
| PF-18 | **Fail-open 装饰器** | 07 | 异常时返回默认值,不阻断流程 | P0 | ⭐ |
| PF-19 | **Quick mode (5 条常识规则, < 5s)** | 07 | L0 快速检查 | P0 | ⭐ |
| PF-20 | **Watchdog 防抖 (debounce_seconds=2.0)** | 07 | 避免频繁触发 | P0 | ⭐ |
| PF-21 | **SQLite WAL + busy_timeout=5000ms** | 07 | 多进程安全 | P0 | ⭐ |
| PF-22 | **Tree-sitter 进程内 Language 缓存** | 07 | 减少初始化 | P0 | ⭐ |
| PF-23 | **Cache 失效策略 (mtime + size)** | 07 | 精确性 | P0 | ⭐ |
| PF-24 | **SidecarWatcher 状态 (sidecar.json)** | 07 | 持久化 | P0 | ⭐ |
| PF-25 | **asyncio.gather 并发** | 02,08 | 同时跑 N 个 proposer | P0 | ⭐ |
| PF-26 | **ThreadPoolExecutor 32 路并发** | 02 | max_workers=32 | P0 | ⭐ |
| PF-27 | **Datasets num_proc (多进程)** | 02 | datasets.Dataset.map(num_proc=N) | P0 | ⭐ |
| PF-28 | **ProcessPoolExecutor** | 02 | FLASK/openai_concurrent.py | P1 | ⭐ |
| PF-29 | **Ray 分布式 (@ray.remote(num_gpus=1))** | 02 | GPU 分片 | P2 | ⚪ |
| PF-30 | **指数退避 (1,2,4,8,16,32 秒)** | 02,05 | 6 次重试 | P0 | ⭐ |
| PF-31 | **Async RateLimit 退避 (1,2,4)** | 02 | moa.py 异步版本 | P0 | ⭐ |
| PF-32 | **tenacity retry (wait_random_exponential)** | 02 | min=1, max=60, stop_after_attempt(6) | P0 | ⭐ |
| PF-33 | **Embedding 批处理 (WORKER_API_EMBEDDING_BATCH_SIZE)** | 02 | 批处理 | P1 | ⭐ |
| PF-34 | **DeepSpeed ZeRO-2/3** | 02 | playground/deepspeed_config_s2.json | P2 | ⚪ |
| PF-35 | **Flash Attention monkey-patch** | 02 | train/llama*_flash_attn_monkey_patch.py | P2 | ⚪ |
| PF-36 | **xFormers Attention** | 02 | train/llama_xformers_attn_monkey_patch.py | P2 | ⚪ |
| PF-37 | **60ms 节流刷新 (scheduleFlush)** | 10 | 用 flushTimer ref 防抖 | P0 | ⭐ |
| PF-38 | **600ms 防抖写盘** | 10 | config.json 持久化防抖 | P0 | ⭐ |
| PF-39 | **Slim 压缩 (panel rawText 截 4000 字符)** | 10 | 避免 config.json 膨胀 | P0 | ⭐ |
| PF-40 | **Sanitize sessions (非终态改 error)** | 10 | 应对上次崩溃 | P0 | ⭐ |
| PF-41 | **Atomic write temp+rename** | 06 | 防止半写 | P0 | ⭐ |
| PF-42 | **Bounded Channel (Drop Oldest 64)** | 06 | 反馈通道溢出时丢弃最旧事件 | P1 | ⭐ |
| PF-43 | **Async TraceWriter (REQ-OBS-003)** | 06 | 非阻塞 trace 写入 | P1 | ⭐ |
| PF-44 | **Diff-aware Config Reload (单 tier)** | 06 | 保留其他 tier,避免全量重载 | P1 | ⭐ |
| PF-45 | **Drift Index Caching** | 06 | SPEC drift 缓存 (perf) | P1 | ⭐ |
| PF-46 | **Drift Timebox 365 leaves** | 06 | perf benchmark | P1 | ⚪ |
| PF-47 | **Hook Timeout 30s + ReadAll 5 MiB** | 06 | 防卡死/OOM | P0 | ⭐ |
| PF-48 | **Active Sessions Cap (idempotent)** | 06 | session_id 注册去重 | P0 | ⭐ |
| PF-49 | **Atomic Lock 3-retry/10ms** | 06 | 文件锁防并发写开销 | P0 | ⭐ |
| PF-50 | **Frozen Guard 无 I/O (纯内存)** | 06 | L1 闸门纯内存 | P0 | ⭐ |
| PF-51 | **Stable Sort insertion (< 50)** | 06 | 进化学习小集合用 insertion sort | P0 | ⭐ |
| PF-52 | **Decoupled Phase pipeline (fetch/apply)** | 04 | 标记 parallelizable() 阶段并发读,顺序 apply | P1 | ⭐ |
| PF-53 | **ClickHouse 分析导出 (高频)** | 04 | analytics 行分离到 ClickHouse | P1 | ⚪ |
| PF-54 | **ClickHouse query budget** | 04 | clickhouse_max_execution_time_secs + max_rows/bytes_to_read | P1 | ⚪ |
| PF-55 | **Compilation optimization (release: strip/LTO/dev opt 2)** | 04 | cargo profile | P1 | ⚪ |
| PF-56 | **5ms 启动 (Go 单一二进制)** | 06 | 无 Python 解释器开销 | P1 | ⭐ |
| PF-57 | **goroutine 并发 (原生)** | 06 | 无 asyncio/threading | P0 | ⭐ |
| PF-58 | **Lazy Init (REQ-PERF-003-A)** | 06 | trivial 命令跳过全量依赖 | P0 | ⭐ |
| PF-59 | **Pandas DataFrame + Plotly 可视化** | 02 | 评估结果分析 | P1 | ⚪ |
| PF-60 | **Tqdm 进度条** | 02 | eval + 评估跟踪 | P0 | ⭐ |

---

## 10. 部署 / 运维 (Docker / K8s / Sidecar / Watchdog / SLA / 计费 / SSO / RLS)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| DP-01 | **Dockerfile (per service)** | 04,06,07 | 根 Dockerfile + 子项目 Dockerfile | P1 | ⭐ |
| DP-02 | **docker-compose (8 服务)** | 04 | postgres / valkey / openfga / rustfs / restate / moa-orchestrator / moa-edge | P1 | ⚪ |
| DP-03 | **docker-compose.chaos.yml (network partition)** | 04 | chaos testing | P2 | ⚪ |
| DP-04 | **Kubernetes (Kustomize + base/overlays)** | 04 | RestateDeployment CRD + HPA + PDB + NetworkPolicy | P2 | ⚪ |
| DP-05 | **Restate cluster 1.7.0 (Bifrost 128Mi)** | 04 | 持久化编排 | P2 | ⚪ |
| DP-06 | **Postgres 17 + pgvector 0.8.2 + pgaudit** | 04 | 主数据库 | P1 | ⭐ |
| DP-07 | **Neon 分支 (serverless Postgres)** | 04 | checkpoint | P2 | ⚪ |
| DP-08 | **Object Store (S3/GCP/RustFS)** | 04 | object_store 0.11 | P1 | ⭐ |
| DP-09 | **RustFS (本地 S3 兼容)** | 04 | 开发用 | P1 | ⚪ |
| DP-10 | **Redis/Valkey 8 运行时缓存** | 04 | redis 0.32 (tokio-comp) | P1 | ⭐ |
| DP-11 | **ClickHouse OLAP** | 04 | clickhouse 0.13 (rustls-tls) | P2 | ⚪ |
| DP-12 | **OTLP (gRPC tonic + HTTP/proto)** | 04 | observability | P1 | ⭐ |
| DP-13 | **Prometheus (metrics-exporter-prometheus)** | 04 | metrics-exporter-prometheus 0.18 | P0 | ⭐ |
| DP-14 | **Grafana Dashboards** | 04,06 | long-conversation-eval + k8s/observability | P1 | ⭐ |
| DP-15 | **OTel/OpenInference bridge** | 04 | tracing-opentelemetry | P1 | ⭐ |
| DP-16 | **Debezium CDC (pgaudit → S3)** | 04 | ops/debezium/ | P2 | ⚪ |
| DP-17 | **Audit-shipper (Python 压缩签名)** | 04 | services/audit-shipper/ | P2 | ⚪ |
| DP-18 | **PII-service (Python)** | 04 | services/pii-service/ | P2 | ⚪ |
| DP-19 | **Auth0 OIDC (RS256 + JWKS cache)** | 04 | 1h TTL, refresh on kid miss | P1 | ⚪ |
| DP-20 | **Token Vault / CIBA (async approval)** | 04 | moa-auth-providers-auth0 | P2 | ⚪ |
| DP-21 | **Group sync (Auth0 → tenant operator)** | 04 | tenant:<T>:admin ↔ operator:<U> admin tenant:<T> | P2 | ⚪ |
| DP-22 | **Multi-tenant RLS (storage_partition_id)** | 04 | 所有 storage crate 使用 scoped connection | P1 | ⚪ |
| DP-23 | **Tenant Concurrency / scope-based vqueue** | 04 | `tenant-{uuid}` scope | P1 | ⚪ |
| DP-24 | **Budget Exhaustion (`tenant_cost_since`)** | 04 | BudgetConfig + tool budget | P1 | ⚪ |
| DP-25 | **Cost aggregation (per tenant)** | 04 | `MOA_PERSIST_TURN_METRICS` | P1 | ⚪ |
| DP-26 | **K8s 6 replicas orchestrator + 3 replicas edge** | 04 | 500m-2CPU / 1-4Gi memory | P2 | ⚪ |
| DP-27 | **CI/CD (GitHub Actions + tauri-action)** | 10 | 4 平台矩阵 (macOS arm64/x64 + Ubuntu + Windows) | P1 | ⭐ |
| DP-28 | **单一二进制分发 (Go 5ms 启动)** | 06 | moai 跨平台 (macOS/Linux/WSL/Windows) | P1 | ⭐ |
| DP-29 | **go install / GoReleaser** | 06 | `go install ./cmd/moai` | P0 | ⭐ |
| DP-30 | **Auto Update (`moai update`)** | 06 | 121 KB update.go | P1 | ⭐ |
| DP-31 | **OAuth Token Preservation (update)** | 06 | 保护 OAuth 凭证 | P1 | ⚪ |
| DP-32 | **Migration (旧版 agency)** | 06 | moai migrate agency + rollback | P1 | ⚪ |
| DP-33 | **Windows 8.3 short path fix (MOAI_TEMP_DIR)** | 06 | Korean username 兼容 | P1 | ⚪ |
| DP-34 | **Coverage HTML (go tool cover)** | 06 | make coverage | P0 | ⭐ |
| DP-35 | **CI Mirror (make ci-local)** | 06 | 镜像 GitHub Actions | P1 | ⭐ |
| DP-36 | **Preflight Gate (lint-fast + test-race-short + build)** | 06 | make preflight | P0 | ⭐ |
| DP-37 | **CodeQL SAST** | 06 | .github/workflows/codeql.yml | P1 | ⭐ |
| DP-38 | **CodeRabbit PR review** | 06 | .coderabbit.yaml | P1 | ⭐ |
| DP-39 | **Required Checks 锁定** | 06 | .github/required-checks.yml | P1 | ⭐ |
| DP-40 | **Lefthook (git hooks)** | 06 | lefthook.yml | P1 | ⭐ |
| DP-41 | **PyPI 发布 (`moat-ai` v1.1.2)** | 07 | pip install moat-ai | P0 | ⭐ |
| DP-42 | **可选依赖组 (dashboard/sidecar/vscode/all)** | 07 | pyproject.toml extras_require | P0 | ⭐ |
| DP-43 | **MkDocs (Material 主题) 文档站** | 07 | 独立 site/ | P1 | ⭐ |
| DP-44 | **Sidecar Daemon (PID/日志/前后台)** | 07 | watchdog + FastAPI REST | P0 | ⭐ |
| DP-45 | **Web Dashboard (port 9876)** | 07 | 实时错误 + 5s 自动刷新 | P1 | ⭐ |
| DP-46 | **VS Code 扩展 (vscode-moat)** | 07 | TS 扩展 + tasks.json + keybindings | P1 | ⭐ |
| DP-47 | **Claude Code Hooks 生成** | 07 | PreToolUse/PostToolUse 注入 | P0 | ⭐ |
| DP-48 | **Cursor mdc 适配** | 07 | .cursor/rules.mdc | P1 | ⭐ |
| DP-49 | **Pre-commit hook 生成** | 07 | .git/hooks/pre-commit (chmod 0o755) | P0 | ⭐ |
| DP-50 | **GitHub Actions 集成** | 07 | pip install moat-ai && moat check --full | P0 | ⭐ |
| DP-51 | **Background 运行 (nohup/screen/tmux)** | 07 | 三种方式 | P0 | ⭐ |
| DP-52 | **PID 管理 (sidecar.pid)** | 07 | 进程状态 | P0 | ⭐ |
| DP-53 | **Tauri 4 平台构建 (macOS/Windows/Ubuntu)** | 10 | GitHub release | P1 | ⭐ |
| DP-54 | **Tauri capabilities (http:default + fs:allow-appdata-*)** | 10 | 严格权限 | P0 | ⭐ |
| DP-55 | **Nginx 网关 (multi worker)** | 02 | FastChat/.../serve/gateway/nginx.conf | P1 | 🔸 |
| DP-56 | **OpenCode native install (user/project/VPS/Docker)** | 09 | 4 种安装方法 | P1 | ⭐ |
| DP-57 | **install.sh 幂等 (冲突时询问 y/N)** | 09 | 重复运行安全 | P0 | ⭐ |
| DP-58 | **MoA Skill Marketplace 分发** | 05 | /plugin marketplace add sdsrss/moa-skill | P0 | ⭐ |
| DP-59 | **Bump-version 4 处同步 (CI 门禁)** | 05 | scripts/bump-version.sh --check | P0 | ⭐ |
| DP-60 | **Bearer Token 鉴权 (HTTPBearer)** | 02 | openai_api_server.py | P0 | ⭐ |

---

## 11. 工具 / 集成 (MCP / Tool calling / Sandbox / Knowledge base / Slack / Twilio / Auth0)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| TI-01 | **MCP Streamable HTTP (`/mcp`)** | 04 | rmcp 2.2.0 server,严格 Host/Origin allowlist | P1 | ⭐ |
| TI-02 | **MCP 50 个内置工具 (6 类)** | 04 | analytics/artifacts/procedures/eval/experiments/agent-principal | P1 | ⭐ |
| TI-03 | **MCP 闭枚举 + JSON Schema bounds** | 04 | annotations(readOnly/Destructive/Idempotent/OpenWorld) | P1 | ⭐ |
| TI-04 | **OpenAI 兼容 /v1/chat/completions + /v1/completions + /v1/embeddings** | 02 | OpenAI 协议完整 | P0 | ⭐ |
| TI-05 | **OpenAI 兼容 Bearer Token 鉴权** | 02 | HTTPBearer | P0 | ⭐ |
| TI-06 | **CORS + StreamingResponse + tiktoken 计数** | 02 | 完整 OpenAI 行为 | P0 | ⭐ |
| TI-07 | **Azure OpenAI (`api_type=azure`)** | 02 | env AZURE_OPENAI_ENDPOINT/KEY | P1 | ⭐ |
| TI-08 | **Anthropic Claude (anthropic SDK)** | 02 | `stop_sequences=[HUMAN_PROMPT]` | P0 | ⭐ |
| TI-09 | **Google PaLM-2 (stateful chat_state)** | 02 | chat-bison@001 | P2 | ⚪ |
| TI-10 | **HuggingFace Hub Upload (LoRA / delta)** | 02 | model/upload_hub.py | P1 | ⚪ |
| TI-11 | **HuggingFace Datasets (load_dataset + map)** | 02 | 加载 AlpacaEval 评估集 | P1 | ⚪ |
| TI-12 | **HuggingFace Transformers (本地推理)** | 02 | FLASK/model_output/inference.py | P2 | ⚪ |
| TI-13 | **vLLM / SGLang / LightLLM / MLX Workers** | 02 | 多种推理后端 | P2 | ⚪ |
| TI-14 | **Knowledge Provider (Nango/Merge)** | 04 | OAuth/code-owned sync | P1 | ⚪ |
| TI-15 | **Parsers (LlamaParse/Unstructured/Reducto/liteparse)** | 04 | 文档解析/分块/图谱 delta | P1 | ⚪ |
| TI-16 | **Slack 适配 (DM/channel/thread)** | 04 | moa-messaging/src/slack/ | P1 | ⚪ |
| TI-17 | **Slack Rich Message (max 40000 chars)** | 04 | SlackRenderer | P1 | ⚪ |
| TI-18 | **Postmark Email (Server token)** | 04 | moa-messaging/src/postmark.rs | P1 | ⚪ |
| TI-19 | **Twilio SMS (SID/Auth/API key)** | 04 | moa-messaging/src/twilio.rs | P1 | ⚪ |
| TI-20 | **Auth0 Token Vault (第三方 OAuth)** | 04 | moa-auth/auth0/ | P2 | ⚪ |
| TI-21 | **GitHub API (gh CLI wrapper)** | 06 | issue close / PR merge / review | P1 | ⭐ |
| TI-22 | **Git Worktree 隔离** | 06 | manager-develop `isolation: worktree` | P1 | ⭐ |
| TI-23 | **Conventional Commits `<type>(<scope>): <subject>`** | 06 | commit 规范 | P0 | ⭐ |
| TI-24 | **Git Tag checkpoint (`moai_cp/$(date)`)** | 06 | 注解标签 | P1 | ⭐ |
| TI-25 | **ast-grep (sg) 16 语言** | 06 | C#/Kotlin/PHP/Ruby/Elixir/Python/TS | P1 | ⭐ |
| TI-26 | **Multi-language AST (smacker/go-tree-sitter)** | 06 | 增量解析 | P1 | ⚪ |
| TI-27 | **LSP 3.17 (gopls/subprocess/transport)** | 06 | internal/lsp/ | P1 | ⚪ |
| TI-28 | **Mermaid 集成 (SPEC 文档自动图)** | 06 | skills/moai-workflow-spec/ | P1 | ⚪ |
| TI-29 | **Nextra 文档站 (.docs-site/)** | 06 | React 静态站点 | P1 | ⚪ |
| TI-30 | **tmux 集成 (CG Mode 多 pane)** | 06 | 启动 Claude Code 多 pane 会话 | P1 | ⚪ |
| TI-31 | **OS 信号处理 (posix/windows)** | 06 | * 平台分离 | P0 | ⭐ |
| TI-32 | **LocalHandProvider (开发用)** | 04 | 进程内 hand | P0 | ⭐ |
| TI-33 | **DockerHandProvider (--network none)** | 04 | 容器,只读 root,seccomp | P1 | ⚪ |
| TI-34 | **DaytonaHandProvider (microVM)** | 04 | 商业 sandbox | P2 | ⚪ |
| TI-35 | **E2BHandProvider (云 sandbox)** | 04 | e2b.dev | P2 | ⚪ |
| TI-36 | **MCP HTTP transport (rmcp)** | 04 | 外部 MCP server | P1 | ⚪ |
| TI-37 | **ToolRouter (统一入口)** | 04 | 15 内置工具 + MCP clients | P0 | ⭐ |
| TI-38 | **15 内置 tool (bash/file_read/.../memory/...)** | 04 | name + description + input_schema + policy_spec | P0 | ⭐ |
| TI-39 | **Contact Verification (OTP)** | 04 | start/complete upgrade token | P1 | ⚪ |
| TI-40 | **DSAR 导出 + Erasure** | 04 | moa-memory-pii::erasure | P1 | ⚪ |
| TI-41 | **Knowledge Webhook (4 provider)** | 04 | nango / merge / llamaparse / reducto | P1 | ⚪ |
| TI-42 | **Audit Verify (`POST /v1/audit/verify`)** | 04 | OCSF JCS 签名验证 | P1 | ⚪ |
| TI-43 | **Lineage Explain/Query/Verify** | 04 | 解释某条决策 + Merkle proof | P1 | ⚪ |
| TI-44 | **ActionReview (CIBA push)** | 04 | 异步审批 + Restate service | P1 | ⚪ |
| TI-45 | **STT/TTS (audio)** | 01 | provider 多模态 | P2 | ⚪ |
| TI-46 | **Pipeline 同步 (HF 跨平台)** | 01 | 各类 webfetch + grep 集成 | P0 | ⭐ |
| TI-47 | **Vercel AI SDK 集成 (LangChain)** | 02 | test_openai_langchain.py | P1 | ⭐ |
| TI-48 | **OpenAI Moderation API (内容审核)** | 02 | tag_openai_moderation.py | P1 | ⭐ |
| TI-49 | **FastAPI + Uvicorn (moa-server)** | 08 | 9 文件 / 2400 行 | P0 | ⭐ |
| TI-50 | **Tauri 2.x (Verdex 桌面)** | 10 | http plugin + fs plugin | P1 | ⭐ |
| TI-51 | **Vite 5 + React 18 + TS 5.6** | 10 | 前端栈 | P0 | ⭐ |
| TI-52 | **i18next 26 + react-i18next 17** | 10 | 12 namespace × 2 语言 | P0 | ⭐ |
| TI-53 | **Vitest 4.1 (26 个测试)** | 10 | extractAnthropicSystem / checkInputLimits / parseJudgeResponse | P0 | ⭐ |

---

## 12. UI / UX (桌面 / TUI / Web / Chat / 主题 / i18n / 可视化)

| # | 能力 | 来源 | 描述 | 复杂度 | 优先级 |
|---|------|------|------|--------|--------|
| U-01 | **Bubbletea TUI (4 标签)** | 01 | Header/Providers/Models/Tiers/Activity | P2 | ⭐ |
| U-02 | **Snapshot 模式 (`--once` JSON dump)** | 01 | 非交互输出 | P0 | ⭐ |
| U-03 | **Mock 模式 (`--mock` 内置数据)** | 01 | 无需服务器,脱机运行 | P0 | ⭐ |
| U-04 | **TUI 状态图标 (🟢/🟡/🔴)** | 01 | 可视化告警 | P0 | ⭐ |
| U-05 | **进度条字符 (█/▓/▰ + ░)** | 01 | bar() 函数 | P0 | ⭐ |
| U-06 | **TUI Snapshot golden 回归** | 06 | internal/tui/golden/ | P1 | ⭐ |
| U-07 | **Web Dashboard (port 9876)** | 07 | 实时错误 + 5s 自动刷新 | P1 | ⭐ |
| U-08 | **Web 资源 (templ)** | 06 | a-h/templ 纯 Go HTML 模板 | P1 | ⭐ |
| U-09 | **Operator Dashboard (静态 HTML+JS)** | 04 | OpenFGA protected read-only endpoint | P1 | ⚪ |
| U-10 | **Rich Console (彩色 Markdown 输出)** | 02 | `from rich.markdown import Markdown` | P0 | ⭐ |
| U-11 | **Typer 命令行参数** | 02 | bot.py `typer.run(main)` | P0 | ⭐ |
| U-12 | **Rich Prompt 交互式问答** | 02 | Prompt.ask with default | P0 | ⭐ |
| U-13 | **Console Status 旋转动画** | 02 | `console.status("[bold green]Querying all...")` | P0 | ⭐ |
| U-14 | **Loguru DEBUG 输出 (model/instruction/output[:20])** | 02 | 调试模式 | P0 | ⭐ |
| U-15 | **Gradio Arena UI (匿名/具名/视觉)** | 02 | FastChat/.../serve/gradio_block_arena_*.py × 6 | P2 | ⚪ |
| U-16 | **WebSocket 实时事件** | 02 | FastChat/.../serve/test_message.py | P2 | ⚪ |
| U-17 | **Plotly 可视化 (Elo)** | 02 | import plotly.express as px | P1 | ⚪ |
| U-18 | **NCurses-style Monitor** | 02 | FastChat/.../serve/monitor/monitor.py | P2 | ⚪ |
| U-19 | **huh 表单 (interactive)** | 06 | yes/no, multi-choice, input | P1 | ⭐ |
| U-20 | **Lipgloss 样式 (Catppuccin 主题)** | 06 | 终端彩色 | P0 | ⭐ |
| U-21 | **Fang 帮助系统 (样式化 help/errors)** | 06 | charm.land/fang/v2 | P0 | ⭐ |
| U-22 | **Statusline Hook (Claude Code 状态栏)** | 06 | moai statusline | P0 | ⭐ |
| U-23 | **Banner 渲染 (启动横幅 + 版本)** | 06 | printBanner | P0 | ⭐ |
| U-24 | **TUI 进度条 + 阶段指示** | 06 | runtime/audit_report.go | P0 | ⭐ |
| U-25 | **Markdown Renderer (termenv)** | 06 | 渲染 markdown 到终端 | P0 | ⭐ |
| U-26 | **Tauri 4 平台桌面应用 (Verdex)** | 10 | 1280×832 窗口 | P0 | ⭐ |
| U-27 | **3 套主题 (dark/light/soft) + 2 语言 (en/zh)** | 10 | CSS 变量 + Tailwind v4 @theme | P0 | ⭐ |
| U-28 | **28+ CSS 变量 / 主题** | 10 | canvas/surface/accent/error/warning/... | P0 | ⭐ |
| U-29 | **4 张裁决卡 tint (consensus/divergence/blindspots/verdict)** | 10 | blue/orange/purple/emerald | P0 | ⭐ |
| U-30 | **Tailwind v4 `@theme` 桥接** | 10 | CSS 变量 → utility class | P0 | ⭐ |
| U-31 | **verdex-pulse / verdex-blink 动画** | 10 | 1.2s/1s step-end | P0 | ⭐ |
| U-32 | **verdex-caret 流式光标 (`▍`)** | 10 | streaming 末 | P0 | ⭐ |
| U-33 | **220ms sidebar 过渡** | 10 | 侧栏展开/折叠 | P0 | ⭐ |
| U-34 | **8px 自定义滚动条** | 10 | 跨浏览器 | P0 | ⭐ |
| U-35 | **3 套 Judge 状态视图 (pending/streaming/error)** | 10 | 加载 + 错误 + 4 段卡 | P0 | ⭐ |
| U-36 | **4 段裁决卡 (🎯⚔️💡⚖️)** | 10 | 卡片式布局 + 降级视图 | P0 | ⭐ |
| U-37 | **Panel 折叠卡 (line-clamp-3)** | 10 | flex-wrap 横向并排 | P0 | ⭐ |
| U-38 | **ChatInput 自适应高度 (max 200px)** | 10 | textarea + Ctrl/⌘+Enter 发送 | P0 | ⭐ |
| U-39 | **SettingsModal (Provider/Templates 2 Tab)** | 10 | 14 个 Provider 字段 + 模板管理 | P0 | ⭐ |
| U-40 | **MoAConfigBar (3 段布局)** | 10 | 模式 + Panel 多选 + Judge 选择 | P0 | ⭐ |
| U-41 | **SessionRow (单击切换/双击重命名/悬停操作)** | 10 | 时间 + N rounds | P0 | ⭐ |
| U-42 | **i18n 12 namespace × 2 语言** | 10 | common/app/emptyState/chatInput/judge/panelStatus/... | P0 | ⭐ |
| U-43 | **Error i18n 12 错误码** | 10 | PROMPT_TOO_LONG / REQUEST_TIMEOUT / ... | P0 | ⭐ |
| U-44 | **Loading 渐变 V + 脉冲** | 10 | 启动动画 | P0 | ⭐ |
| U-45 | **MoA Skill 触发词 (中文 + 判断类信号 + L0 闸门负信号)** | 05 | moa模式/多人评审/委员会/第二意见/... | P0 | ⭐ |
| U-46 | **Dry-run 表格输出** | 05 | member / seat / channel / model / protocol | P0 | ⭐ |
| U-47 | **Progress 输出 (stderr)** | 05 | [generate] dispatching 3 members ... | P0 | ⭐ |
| U-48 | **Verdex Provider 端点规范化显示** | 10 | SettingsModal 实时显示 base URL | P0 | ⭐ |
| U-49 | **Verdex Provider 测试徽章 (✓234ms · 128K ctx)** | 10 | 连接测试结果 | P0 | ⭐ |
| U-50 | **Provider warning 黄色圆点 (无 API Key)** | 10 | visual hint | P0 | ⭐ |
| U-51 | **6 节 Help Modal** | 10 | 什么是 Verdex/快速开始/模式对比/模板/配置/快捷键/安全 | P0 | ⭐ |
| U-52 | **3 主题持久化 (config.json)** | 10 | data-theme= | P0 | ⭐ |
| U-53 | **Verdex Icons zero-dep gen (CRC32 + zlib)** | 10 | 157 行 Node.js 手写 PNG | P1 | ⭐ |
| U-54 | **Snapshot 模式 + Mock 模式 (TUI 兼容)** | 01 | 自动 TTY 检测 | P0 | ⭐ |
| U-55 | **displaywidth / UAX#29 (Unicode 宽度)** | 06 | 终端字符宽度 | P1 | ⭐ |
| U-56 | **CAT-1 (stream_text 推理模型输出)** | 10 | 处理流式推理文本 | P0 | ⭐ |

---

## 13. 总能力数量统计

| 大类 | 能力数 | HIGH ⭐ | MED 🔸 | LOW ⚪ |
|------|------:|--------:|-------:|-------:|
| 1. 路由/调度 | 44 | 27 | 8 | 9 |
| 2. MoA 编排 | 54 | 41 | 8 | 5 |
| 3. 多智能体 | 50 | 17 | 12 | 21 |
| 4. LLM Provider | 40 | 30 | 6 | 4 |
| 5. 评估/打分 | 40 | 22 | 8 | 10 |
| 6. 提示词工程 | 32 | 32 | 0 | 0 |
| 7. 数据模型 | 60 | 30 | 14 | 16 |
| 8. 安全/审计 | 60 | 30 | 12 | 18 |
| 9. 性能/缓存 | 60 | 36 | 14 | 10 |
| 10. 部署/运维 | 60 | 22 | 6 | 32 |
| 11. 工具/集成 | 53 | 18 | 12 | 23 |
| 12. UI/UX | 56 | 50 | 6 | 0 |
| **总计** | **609** | **355** | **106** | **148** |

> **注**: 总数 609 大于"去重后 280+",因为部分能力被列入 2-3 个相关大类(如 MoA 编排和提示词工程有大量交叉)。**核心独立能力去重后约 280-320 项**。

---

## 14. 可移植到 MoA Gateway Pro 的能力清单

### 14.1 ⭐ HIGH 优先级(必须做,100+ 项)

| 大类 | 能力编号 | 简短说明 |
|------|----------|----------|
| **路由/调度** | R-02,03,04,06,07,08,09,10,11,14,15,17,18,19,20,21,22,23,25,33,35,39,41,42,44 | 25 维特征 / 集成投票 / 边界重校准 / 健康评分 / 多窗口配额 / RAG / Plan-Act / 7 阶段清洗 / failover / quorum grace / L0 闸门 / Self-MoA / 3 通道 / direct route / dry-run / discovery / 探活 / 自愈 / 限制 / 问候快路径 / Trivial Fast-Path / 媒体保护 |
| **MoA 编排** | M-01..M-54 (核心) | 50+ 核心能力全部 HIGH |
| **LLM Provider** | L-01..L-20,30,32,33,34,35,38,39,40 | OpenAI 兼容 / Anthropic / SSE / 流式超时 / Tool calling / response_format / 协议分流 |
| **评估/打分** | E-02,05,11,14,15,16,18,19,20,23,25,31,32,33,37,38,40 | Pain Score / 5 维 / Elo / Sycophancy / LLM Judge / Karpathy 原则 |
| **提示词工程** | P-01..P-32 (全部) | 全部 32 项均 HIGH |
| **数据模型** | D-23,24,27,28,29,30,31,32,33,34,35,36,37,38,39,40,41,47,49,50,51,52,57,58,59,60 | 7-phase 清洗 / MoA Session 4 域 / 4 段裁决契约 / 40+ 模型 DB |
| **安全/审计** | S-01..S-19, 20,21,22,23,24, 36,37,38,39,40, 43, 45, 47, 52, 53, 54, 55, 57, 58, 59 | 13/9 类密钥 / 3 层豁免 / API Key 格式 / Anthropic system 提取 / 8 sentinels |
| **性能/缓存** | PF-01..PF-10, 15,16,17,18,19,20,21,22,23,24, 25,26,27,28,30,31,32,33, 37,38,39,40,41,42,43,44,45,47,48,49,50,51,52,57,58,60 | TurboQuant / 原子写 / bounded channel / 5ms 启动 |
| **部署/运维** | DP-20, 27, 29, 30, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 51, 52, 54, 57, 58, 59, 60 | PyPI / 单一二进制 / auto update / CI/CD / VS Code / Claude Code Hook |
| **工具/集成** | TI-01,02,04,05,06,07,08, 11,21,22,23,25, 32, 37, 38, 46, 47, 48, 49, 50, 51, 52, 53 | MCP / 15 内置 tool / ToolRouter / OpenAI Moderation |
| **UI/UX** | U-01..U-56 (几乎全部) | TUI / Web Dashboard / 桌面 / 主题 / i18n / 4 段裁决卡 |

### 14.2 🔸 MED 优先级(推荐做,~50 项)

| 大类 | 典型能力 |
|------|----------|
| **路由/调度** | R-05(序数回归)/ R-12(5 级量化)/ R-16(5 档 tier)/ R-34(multi-agent)/ R-36(CLI Provider)/ R-37(ConcurrencyLimiter)/ R-40(Quota 仪表化)/ R-43(鉴权感知探针) |
| **MoA 编排** | M-13,17,21,30,31,32,40,45,47,50,51,52(多评估器 / collision / 角色集) |
| **多智能体** | A-05,06,18,20,22,25,26(沙箱分层 / Event scheduling) |
| **LLM Provider** | L-21(Azure)/ L-29(per-model pool)/ L-31,36,37(stream tool_call / embedding / rerank) |
| **评估/打分** | E-06(神经衰弱)/ E-21(Segment resolution)/ E-23(Quality Gate)/ E-30(multi-eval) |
| **数据模型** | D-08(TurboQuant Result)/ D-11,12,13,14,15,17(Tenant/Session/Task) |
| **安全/审计** | S-23,36,37,38,39,40,43,53(Prompt Injection 防护 / 输出包装) |
| **性能/缓存** | PF-11,12,52,53(decoupled phase / ClickHouse) |
| **部署/运维** | DP-01,06,08,12,14,15,18,19(Docker / K8s 部分) |
| **工具/集成** | TI-09,14,15,16,17,18,19,20(知识库 / 通讯 / Auth0) |
| **UI/UX** | U-19,20,21,22,23,24,25,28,53,55(fang/lipgloss/TUI 部分) |

### 14.3 ⚪ LOW 优先级(可选做,或暂缓)

| 大类 | 典型能力 |
|------|----------|
| **路由/调度** | R-01(9 阶段管线)/ R-28(Restate vqueues)/ R-29(Lottery)/ R-31(Elo bootstrap)/ R-38(Bubbletea) |
| **MoA 编排** | M-29(FLASK 12 维)/ M-50(Cross-iteration)/ M-51(Multi-eval averaging) |
| **多智能体** | A-04(8 层权限栈)/ A-07(5 层宪法)/ A-08(9 sentinels)/ A-10(Ralph 循环)/ A-13(Ceiling)/ A-14,15(Pattern/Tier)/ A-16,17(EARS/Acceptance)/ A-19(VO)/ A-21,22(Artifact/Multi-session)/ A-27(Worker)/ A-29(Heartbeat)/ A-30,31(Sandbox)/ A-32(ActionPolicy)/ A-34-50(SPEC 流程 / Worktree) |
| **LLM Provider** | L-22(PaLM-2)/ L-23(HF Inference)/ L-25(WebGPU)/ L-26,27(量化 / 多 worker) |
| **评估/打分** | E-07,08(FLASK)/ E-13,24,26-29,39(细粒度评分) |
| **数据模型** | D-13(双时态)/ D-21,22(Experience/Artifact) |
| **安全/审计** | S-25..S-35(OpenFGA / OCSF / SCIM / Auth0 / Sandbox Tier 2) / S-41,42,44(DSAR) / S-48,49,50(Constitution) / S-56(Skip audit) / S-60(事务性 outbox) |
| **性能/缓存** | PF-29(Ray 分布式)/ PF-34(DeepSpeed)/ PF-35,36(Flash/xFormers)/ PF-53,54,55(编译优化) |
| **部署/运维** | DP-02,03,04,05,07,09,11,16,17(K8s/Neon/RustFS/ClickHouse/Debezium/PII/Python 服务)/ DP-31,32,33(Migration)/ DP-55(Nginx) |
| **工具/集成** | TI-09,12,13,14,15,16,17,18,19,20,26,27,28,29,30,33,34,35,36(通讯/推理后端/知识库) |
| **UI/UX** | U-09(Operator Dashboard)/ U-15(Gradio Arena)/ U-16(WebSocket)/ U-17(Plotly)/ U-18(NCurses) |

---

## 15. 数据/控制流图 (Data & Control Flow)

### 15.1 主请求流 (Main Request Flow)

```
[OpenAI 兼容客户端] / Anthropic Messages 客户端
                  ↓
        ┌─────────────────────────┐
        │ 1. AUTH (Bearer/API Key)│  ← S-20,21 / 鉴权
        └─────────────┬───────────┘
                      ↓
        ┌─────────────────────────┐
        │ 2. PARSE  + NORMALIZE   │  ← R-11 7-phase 清洗
        └─────────────┬───────────┘
                      ↓
        ┌─────────────────────────┐
        │ 3. L0 GATE (是否启 MoA) │  ← R-21 关键词负信号
        └─────┬───────────────┬───┘
              ↓               ↓
        [PASS]             [FAIL: 直接调 provider]
              ↓
        ┌─────────────────────────┐
        │ 4. SCORING (R-02,03)    │  ← 25 维特征 + 集成投票
        └─────────────┬───────────┘
                      ↓ score → tier
        ┌─────────────────────────┐
        │ 5. PLAN/ACT DETECT (R-10)│  ← 关键词 + 正则 + 置信度
        └─────────────┬───────────┘
                      ↓
        ┌─────────────────────────┐
        │ 6. PANEL/MoA 分流       │  ← R-25 direct_route vs 走 MoA
        └─────┬───────────────┬───┘
              ↓               ↓
        [direct]         [MoA Flow]
              ↓               ↓
        ┌─────────┐    ┌──────────────────────┐
        │Provider │    │ 7. PROPOSER GATHER   │  ← asyncio.gather
        │  call   │    │     N proposers      │
        └────┬────┘    └────────┬─────────────┘
             ↓                 ↓
             ↓         ┌──────────────────────┐
             ↓         │ 8. JUDGE/AGGREGATOR  │  ← M-03 inline system 注入
             ↓         │     parse verdict    │  ← M-30 4 段裁决
             ↓         └────────┬─────────────┘
             ↓                  ↓
             ↓         ┌──────────────────────┐
             ↓         │ 9. STATS + EXPORT    │  ← E-15,16,40 + 4 段 UI
             ↓         └────────┬─────────────┘
             ↓                  ↓
             ↓         ┌──────────────────────┐
             ↓         │ 10. FEEDBACK/LEARN   │  ← R-04 边界重校准 + D-04 反馈
             ↓         └────────┬─────────────┘
             ↓                  ↓
        ┌────┴──────────────────┴────────┐
        │ 11. RESPONSE (SSE / JSON)     │  ← L-10,11,32 + 9 错误码
        └───────────────────────────────┘
```

### 15.2 安全/审计旁路 (Security Side-flow)

```
[Request In]
   ↓
[SEC-01: Sensitive Material Warn]  ← S-12 自动告警(不阻断)
   ↓
[SEC-02: 13/9 类密钥检测]          ← S-01,02 静态扫描
   ↓
[SEC-03: Leak Check (CI gate)]    ← S-13 退出码 0/1/2
   ↓
[SEC-04: Input Inspection]        ← S-37,39 9 类启发式
   ↓
[SEC-05: API Key Auth]            ← S-20 Bearer
   ↓
[MoA / Provider Call]
   ↓
[SEC-06: Output Wrap]             ← S-38 <untrusted_tool_output>
   ↓
[SEC-07: Audit Sign (OCSF)]       ← S-26,27 (可选 enterprise)
   ↓
[Response Out]
```

### 15.3 反馈/学习回路 (Feedback Loop)

```
[User 反馈] → [D-04 FeedbackEntry] → [R-04 retrain()]
                                     ↓
                              [tier_boundaries.json]
                                     ↓
                              [R-03 ensemble voter 用新边界]
                                     ↓
                              [R-15 自愈 tier 重新平衡]
                                     ↓
                              [R-14 主动探活 + 配额更新]
                                     ↓
                              [R-08 ETA 耗尽 → 自动切 provider]
```

### 15.4 多模型评估流 (Multi-eval Flow)

```
[Turn prompt + N panel results]
   ↓
[Multi-eval fan-out]    ← E-21 单 evaluator OR
[Multi-eval averaging]  ← E-30 multi_eval=true
   ↓
[Per-section viability] ← E-16 0/1/2/3+ sections ❌
   ↓
[5 维 TQ/CO/AP/SE/IN]   ← E-15
   ↓
[3 反群体思维]          ← E-40 (Sycophancy / Conformity / Pseudo)
   ↓
[CONVERGENT 检测]       ← M-16 3+ 提案独立提及
   ↓
[CONFLICTING 仲裁]      ← M-17 evaluator 信号 + viability
   ↓
[Integrated synthesis OR Final selection]
   ↓
[Source attribution + Why this beats the field]  ← P-29,30
   ↓
[Verdict 输出]
```

---

## 16. 风险点与技术债 (Risk Analysis & Technical Debt)

### 16.1 复杂性风险 (Complexity Risks)

| 风险 | 来源项目 | 描述 | 缓解 |
|------|----------|------|------|
| **过度工程化** | 04 MOA-Commercial | 1216 源文件 / 45 crates / 多租户+SCIM+OCSF+OpenFGA+Auth0 全套,小型项目不需要 | **MVP 阶段跳过**;只借鉴关键能力 |
| **OpenCode 上游 bug 依赖** | 09 opencode-moa | 权限挂起 #35073 / frontmatter 优先级 | **不依赖上游**,自实现路由/权限 |
| **GateSwarm 单文件膨胀** | 01 | 2868 行单文件 + 44 端点 | 拆分到多模块,避免 God Object |
| **moa-server 9 文件/2400 行** | 08 | OK,可直接借鉴 | 0 |
| **8 层权限栈/5 层宪法** | 06 | 强大但学习成本高 | **MVP 跳过**;用简单 allow/deny |

### 16.2 兼容性与标准化风险

| 风险 | 描述 | 缓解 |
|------|------|------|
| **MoA 论文 "MoA" vs MoA Gateway Pro** | 02/04/05/08/10 都自称为 MoA 实现 | 明确定义:"MoA = Multi-model Orchestration & Aggregation" |
| **OpenAI 协议扩展 (extra_body/parallel_tool_calls)** | 08 通过 allowlist 净化 | 实施时**保守添加**新字段 |
| **Anthropic system 提取** | 10 用 `extractAnthropicSystem`,但 v3 API 升级风险 | 持续跟进 Anthropic SDK 升级 |
| **多协议分流 (OpenAI/Anthropic)** | 10 集中 `prepareOpenAI`/`prepareAnthropic` | 保持入口函数清晰 |
| **SSE 解析边界条件** | 10 用 `data:` 前缀 + 跳过 `[DONE]` | 完整测试覆盖 (8 case) |

### 16.3 运行时风险

| 风险 | 描述 | 缓解 |
|------|------|------|
| **Provider 配额耗尽/限流** | 01 R-08 多窗口 + R-15 自愈 | **必须做**自动切换 |
| **流式超时 60s 不够** | 10 默认 60s,推理模型可能 120s+ | 配置化 timeout,默认 120s |
| **reasoning 模型 max_tokens 陷阱** | 05 提到 gpt-5.6-sol 推理吃光额度 | 默认 max_tokens=8000,decision 模式 16000 |
| **chatcmpl id 冲突** | 08 `chatcmpl-{uuid.hex}` 极低 | 用 crypto.randomUUID() |
| **幻觉风险** | 09 P-31 必须有可执行命令 | 强约束 + self-eval |
| **Sycophancy 群体思维** | 05 E-40 3 类对冲 | **必须做**反群体思维纪律栈 |
| **模型绑定冲突** | 09 §2 事故复盘,`mimo.md` 误改 minimax | **CI 门禁** `check-no-forbidden-model.sh` |

### 16.4 性能与可扩展性

| 风险 | 描述 | 缓解 |
|------|------|------|
| **Asyncio.gather vs 大量并发** | 02/08 用 asyncio.gather,理论无上限 | per-provider 限流 (R-17) |
| **ClickHouse 不必要的依赖** | 04 用于分析导出 | **MVP 跳过**;先 JSON+SQLite |
| **OpenFGA self-hosted 1.8.16** | 04 强依赖 | **MVP 跳过**;用简单 RBAC |
| **50 个 MCP 工具膨胀** | 04 列 50 个,UI 复杂 | 收敛到 10-15 个核心 |
| **8 层权限栈调试** | 06 路径复杂 | MVP 4 层(allow/deny/admin/bubble) |
| **9 阶段管线不易调试** | 01 端点 44 个,各阶段无观测 | **加 OTel trace** 强制每个阶段有 span |

### 16.5 数据安全与隐私

| 风险 | 描述 | 缓解 |
|------|------|------|
| **API Key 明文存储** | 10 config.json 明文 | OS keychain 集成 (Tauri stronghold) |
| **多租户数据隔离** | 04 RLS 严格 | **MVP 单租户**;R-1 不上 |
| **S3 Object Lock 合规** | 04 不可篡改 | 暂不需要 |
| **PII 擦除** | 04 moa-memory-pii | 暂不需要 |
| **Debug 日志含 prompt/response 原文** | 08 已知限制 | **生产关 DEBUG**;脱敏 API key/email |

### 16.6 测试覆盖不足

| 风险 | 描述 | 缓解 |
|------|------|------|
| **moa-server 0 测试** | 08 未提供测试 | 移植 Verdex 26 测试方法 |
| **Verdex 26 测试覆盖关键纯函数** | 10 好实践 | 直接借鉴测试矩阵 |
| **moa-skill 126 测试** | 05 优秀 | 借鉴 dispatch_with_quorum / sycophancy 等边界测试 |
| **MoAI-ADK 85-100% 覆盖率** | 06 优秀 | 持续 CI 强制 |

---

## 17. 行动建议 (Action Recommendations)

### 17.1 必须先做(MVP 阶段,P0 = 1-2 周)

#### 阶段 1:核心网关骨架 (Week 1-2)

| 优先级 | 能力 | 复杂度 | 来源 |
|--------|------|--------|------|
| ⭐⭐⭐ | **OpenAI 兼容 + Anthropic Messages 协议** (L-01,04,05,06) | P0 | 08, 10 |
| ⭐⭐⭐ | **SSE 流式 + 60s 超时 + 非流式回退** (L-09,10,11,12) | P0 | 08, 10 |
| ⭐⭐⭐ | **Tool calling 原生** (L-13,14,15,16) | P0 | 08, 10 |
| ⭐⭐⭐ | **Provider 池 + lazy init** (L-12, R-35) | P0 | 08, 01 |
| ⭐⭐⭐ | **Bearer/API Key 鉴权** (S-20, 21) | P0 | 01, 02 |
| ⭐⭐⭐ | **9 类密钥检测 + 脱敏** (S-01..S-19) | P0 | 05, 07 |
| ⭐⭐⭐ | **3 协议错误码 (PermanentError/TransientError)** (S-58) | P0 | 05 |

#### 阶段 2:MoA 核心算法 (Week 2-3)

| 优先级 | 能力 | 复杂度 | 来源 |
|--------|------|--------|------|
| ⭐⭐⭐ | **单层 MoA (2-layer)** (M-01) | P0 | 02, 08 |
| ⭐⭐⭐ | **inline system 注入** (M-03) | P0 | 08 |
| ⭐⭐⭐ | **核心聚合 prompt 模板** (P-01) | P0 | 02 |
| ⭐⭐⭐ | **asyncio.gather 并发** (PF-25) | P0 | 02, 08 |
| ⭐⭐⭐ | **L0 闸门 (是否启 MoA)** (R-21) | P0 | 05 |
| ⭐⭐⭐ | **3 通道 (子代理/CLI/API)** (R-23) | P0 | 05 |
| ⭐⭐⭐ | **dry-run 成本估算** (R-33) | P0 | 05 |

#### 阶段 3:打分与路由 (Week 3-4)

| 优先级 | 能力 | 复杂度 | 来源 |
|--------|------|--------|------|
| ⭐⭐⭐ | **25 维特征 + 启发式评分** (R-02) | P1 | 01 |
| ⭐⭐⭐ | **集成投票器 (ensemble)** (R-03) | P1 | 01 |
| ⭐⭐⭐ | **Tier 边界重校准** (R-04) | P1 | 01 |
| ⭐⭐⭐ | **Plan/Act 模式检测** (R-10) | P0 | 01 |
| ⭐⭐⭐ | **7 阶段消息清洗** (R-11) | P1 | 01 |
| ⭐⭐⭐ | **消费智能 + 自愈 tier** (R-06, 15) | P1 | 01 |
| ⭐⭐⭐ | **Provider 健康评分** (R-07) | P1 | 01 |
| ⭐⭐⭐ | **多窗口配额 (5h/weekly/monthly)** (R-08) | P1 | 01 |

### 17.2 推荐做(第 2-3 月,⭐⭐)

| 优先级 | 能力 | 复杂度 | 来源 |
|--------|------|--------|------|
| ⭐⭐ | **多层 MoA (3-layer)** (M-02) | P1 | 02 |
| ⭐⭐ | **3 阶段元 Prompt 协议** (M-22) | P1 | 03 |
| ⭐⭐ | **Sycophancy / Conformity / Pseudo 3 反群体思维** (E-40) | P1 | 05 |
| ⭐⭐ | **同源去重 / 保留分歧 / 仲裁人自查门** (S-17, 41, 42) | P0 | 05 |
| ⭐⭐ | **谄媚计数器** (E-40) | P0 | 05 |
| ⭐⭐ | **4 段裁决 UI** (M-30, U-36) | P0 | 10 |
| ⭐⭐ | **RAG 关键词检索 + 24h TTL** (R-09) | P0 | 01 |
| ⭐⭐ | **Feedback/Vote (3 源 gold/silver/bronze)** (D-06) | P1 | 01 |
| ⭐⭐ | **TurboQuant 压缩 v3.6** (PF-01..07) | P1 | 01 |
| ⭐⭐ | **Provider auto-execute tools** (M-07) | P1 | 08 |
| ⭐⭐ | **3 套主题 (dark/light/soft) + 2 语言 (en/zh)** (U-27) | P0 | 10 |
| ⭐⭐ | **40+ 模型上下文窗口数据库** (D-57) | P0 | 10 |
| ⭐⭐ | **i18n 12 namespace** (U-42) | P0 | 10 |
| ⭐⭐ | **Settings Modal (Provider/Templates 2 Tab)** (U-39) | P0 | 10 |
| ⭐⭐ | **CLI TUI (Bubbletea)** (U-01..U-06) | P2 | 01 |

### 17.3 可选做(第 4-6 月,🔸)

| 优先级 | 能力 | 来源 |
|--------|------|------|
| 🔸 | **3 反群体思维对冲完整纪律栈** | 05 |
| 🔸 | **Multi-eval averaging + per-section viability** | 09 |
| 🔸 | **开会讨论 L3 模式** (M-49) | 05 |
| 🔸 | **Feedback-aware iteration** (M-19) | 09 |
| 🔸 | **Cross-iteration synthesis** (M-50) | 09 |
| 🔸 | **Integrated synthesis (curation, not invention)** (M-15) | 09 |
| 🔸 | **Socratic Interview 4-quadrant** (E-39) | 06 |
| 🔸 | **Pain Score (7 类权重)** (E-01) | 07 |
| 🔸 | **架构健康评分 + 依赖枢纽** (E-04, E-32) | 07 |
| 🔸 | **Karpathy 原则 (Simplicity First + Surgical)** (E-37,38) | 07 |
| 🔸 | **MCP Streamable HTTP Server (10-15 工具)** (TI-01,02) | 04 |
| 🔸 | **OCSF 事件审计 (4 类)** (S-26) | 04 |
| 🔸 | **Sidecar 守护 + Web Dashboard** (DP-44,45) | 07 |
| 🔸 | **VS Code 扩展** (DP-46) | 07 |
| 🔸 | **Bubble Mode (parent escalate)** (A-06) | 06 |

### 17.4 不建议做(本期跳过,⚪)

| 项 | 跳过原因 |
|----|----------|
| 9 阶段管线 (R-01) | 太复杂,前 3 阶段已够用 |
| OpenFGA 1.8.16 (S-25) | 引入 self-hosted 服务,治理成本高 |
| SCIM v2 (S-30) | 需要 IdP 集成,非核心 |
| OCSF + Merkle 链 (S-26,28) | 合规审计,MVP 不需要 |
| 5 层宪法 (A-07) | 学习成本高,4 层权限栈够用 |
| 8 层权限栈 (A-04) | 简化为 4-5 层 |
| Restate vqueues (DP-05) | 需要单独 Restate cluster |
| EARS/GEARS 模式 (A-16) | SPEC 流程专用 |
| Worktree 隔离 (A-42,43) | Git worktree,本地化专用 |
| MX 注解系统 (A-39) | 16 语言,过度工程 |
| Ray 分布式 (PF-29) | 单机够用 |
| vLLM/SGLang/MLX workers | 推理后端,不是网关职责 |
| ClickHouse (DP-11) | OLAP,MVP 用 JSON+SQLite |
| Debian packaging | CI 后期再做 |
| Neuro-sama 复杂事件枚举 (D-17) | MoA 引擎不需要 40+ 事件 |

### 17.5 关键启示 (Key Insights)

1. **MoA 核心 50 行就够** (02 `moa.py` 50 行 = 完整 MoA 算法),不要过度工程化
2. **inline system 注入是核心** (08),不是 [agg_system, *originals] 旧模式
3. **聚合者必须有完整上下文** (05),不能像 02 那样再调无上下文 API
4. **3 反群体思维对冲 (发言序轮转 + changed_by_new_argument + 收尾盲投漂移)** 是 05 最独特的创新,**必须做**
5. **谄媚计数器是 05 的关键不变量** (movers, flips_toward_majority),**必须做**
6. **Inline system 不破坏 native tool calling** (08 关键设计),**必须遵守**
7. **L0 闸门 (不启 MoA 的机械验证任务)** 是 05 的成本控制核心,**必须做**
8. **dry-run 成本估算** (05) 防 dry-run 少报 subagent+api fallback bug,**必须做**
9. **SSRF 防护** (validate_upstream_path) 是 04 edge 必修,**必须做**
10. **9 类密钥检测 + 3 层豁免 + 0 文件保护** (05,07) 是 CI 门禁核心,**必须做**

---

## 18. 实施路线图 (Implementation Roadmap)

```
┌──────────────────────────────────────────────────────────────────┐
│                          MVP 路线图 (8-12 周)                      │
├──────────────────────────────────────────────────────────────────┤
│ Week 1-2  协议层 (OpenAI + Anthropic + 鉴权 + SSE)                │
│           ↳ 来源: 08 moa-server + 10 Verdex                       │
│           ↳ 交付: 6 端点 (chat/models/score/health)               │
├──────────────────────────────────────────────────────────────────┤
│ Week 3-4  路由层 (打分 + 集成投票 + 边界 + 配额)                  │
│           ↳ 来源: 01 GateSwarm                                    │
│           ↳ 交付: 25 维特征 + 5 tier 路由                        │
├──────────────────────────────────────────────────────────────────┤
│ Week 5-6  MoA 算法层 (单层/多层 + 3 阶段元 Prompt)                │
│           ↳ 来源: 02 TogetherAI + 03 MoA-Engine + 08 moa-server  │
│           ↳ 交付: 2-layer + 3-layer + meta-prompt                │
├──────────────────────────────────────────────────────────────────┤
│ Week 7-8  评估层 (5 维 + 3 反群体思维 + 4 段裁决)                 │
│           ↳ 来源: 05 moa-skill + 09 opencode-moa + 10 Verdex     │
│           ↳ 交付: ELO + Pain Score + 4-段 UI                    │
├──────────────────────────────────────────────────────────────────┤
│ Week 9-10  反馈/学习 + 压缩 (TurboQuant + 边界重校准)              │
│           ↳ 来源: 01 GateSwarm                                    │
│           ↳ 交付: feedback.json + retrain 端点                   │
├──────────────────────────────────────────────────────────────────┤
│ Week 11-12 桌面客户端 (Tauri 3 主题 2 语言)                        │
│           ↳ 来源: 10 Verdex                                       │
│           ↳ 交付: 1280×832 桌面应用                              │
└──────────────────────────────────────────────────────────────────┘
```

---

## 19. 总结 (Summary)

### 19.1 数字

- **覆盖 10 个项目** / 609 个能力点 / 12 大类
- **可移植到 MoA Gateway Pro 的 HIGH 能力**: ~250 项
- **MVP 阶段必须做**: 50-70 项 (P0/P1)
- **第 2 阶段推荐做**: 50-80 项 (⭐⭐)
- **可选/不建议做**: 200+ 项 (🔸/⚪)

### 19.2 关键判断

- **最直接参考**: **08 moa-server** (OpenAI 兼容骨架) + **10 Verdex** (桌面 UI + 4 段裁决) + **05 moa-skill** (反群体思维纪律栈)
- **核心算法参考**: **02 TogetherAI MoA** (MoA 核心 50 行) + **03 MoA-Engine** (单模型元 Prompt 协议) + **09 opencode-moa** (迭代模式 + 验证器)
- **运营级参考**: **01 GateSwarm** (9 阶段管线 + 25 维特征) + **04 MOA-Commercial** (多租户模式,只借鉴思想)
- **不要照搬**: **04 1216 源文件** / **06 100K 行** / **07 大量检查器** — 复杂度溢出

### 19.3 唯一最重要的启示

> **MoA 算法的本质是 "N 个不同模型的盲点 + 结构化聚合"** — 简单 50 行 Python 即可实现核心;真正的工程难度在 **3 反群体思维纪律栈** (05 moa-skill 的核心创新)、**inline system 注入** (08 关键设计) 和 **Provider 自愈 tier 重新平衡** (01 关键能力)。

---

*文档生成时间: 2026-07-13*
*覆盖 10 个分析文件,总 9500+ 行原始内容*
*目标读者: MoA Gateway Pro 项目团队 (架构师 / 后端 / 前端 / DevOps)*
*预计节省工作量: 60-80%(从 0 设计 → 基于这 280+ 能力组合)*
