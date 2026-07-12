# MoAI-ADK 多智能体开发框架 — 深度能力分析

> **项目路径**: `D:\MoA Gateway Pro\参考\extracted\06-moai-adk-multiagent\moai-adk-main`
> **项目类型**: 高性能 AI Agent 开发框架 / Harness Engineering 平台
> **总规模**: ~100K 行 Go 代码, 100+ 包, 49MB 完整项目
> **分析日期**: 2026-07-13
> **分析者**: file-search-specialist (MiniMax-M3)

---

## 一、项目概述

**MoAI-ADK (Agentic Development Kit for Claude Code)** 是 modu-ai 公司推出的多智能体 AI 编程环境,采用 Go 从头重写 (替代原 73,000 行 Python 实现)。它将 8 个常驻 AI Agent (7 个 MoAI 自定义 + 1 个 Anthropic 内置 `Explore`) 与 27 个 `moai-*` 模板管理 Skill 协同运行,通过 Harness Engineering 范式 (即"为 AI Agent 设计执行环境而非直接写代码") 产出高质量代码。核心创新在于把 5 阶段 SPEC 工作流 (Plan → Run → Sync)、Ralph 反馈循环状态机、5 层宪法安全闸门、3 阶段质量门控、4 维度独立审计 (sync-auditor) 与 8 层配置/权限栈编织成一套面向 Claude Code 的运行时骨架,以单一 Go 二进制形式分发,5ms 启动、零依赖、原生 goroutine 并发。本仓库是 Claude Code 真正执行 `/moai plan / run / sync / fix / loop` 等命令时的运行时载体与生命周期底座。

---

## 二、核心模块清单

| 模块路径 | 职责 | 关键文件 |
|---------|------|---------|
| `cmd/moai/main.go` | 入口点,ExitCoder 错误链 | 29 行 |
| `internal/cli/` | Cobra CLI 命令树 (70+ 子命令) | 200+ 文件 |
| `internal/loop/` | **Ralph 反馈循环状态机** (核心) | controller.go / state.go / storage.go / feedback_channel.go |
| `internal/ralph/` | **决策引擎** (Continue/Converge/RequestReview/Abort) | engine.go |
| `internal/agent/` | Agent 抽象定义 | (markdown 形式定义于 .claude/agents/moai/) |
| `internal/skill/` | Skill 抽象 + 模板 | (markdown 形式于 .claude/skills/moai-*/) |
| `internal/hook/` | **Hook 注册中心 + 27 个事件处理器** | registry.go (417 行) + 100+ 处理器 |
| `internal/session/` | **会话/状态/检查点持久化** | registry.go / store.go / state.go |
| `internal/goal/` | 条件驱动的目标求值器 (2-tier) | evaluate.go / schema.go / state.go |
| `internal/harness/` | **多 Agent 编排 + 模式学习 + 5 层安全** | types.go / learner.go / applier.go / router/ / safety/ |
| `internal/spec/` | **SPEC 文档解析 + EARS/GEARS 校验** | lint.go (1085 行) / parser.go / era.go |
| `internal/permission/` | **8 层权限栈 + 气泡模式** | stack.go / resolver.go / conflict.go |
| `internal/astgrep/` | **AST 静态分析** (多语言) | scanner.go / analyzer.go / rules.go |
| `internal/lsp/` | **LSP 客户端 (gopls/subprocess/transport)** | gopls/ / core/ / hook/ / transport/ |
| `internal/constitution/` | **5 层宪法安全闸门** | pipeline.go / validator.go / frozen_guard.go / canary.go |
| `internal/runtime/` | **审计门控 + 预算控制 + 缓存** | audit_gate.go / budget.go / cache_control.go |
| `internal/evolution/` | **学习进化 + 毕业** | learning.go / safety.go / apply.go / graduation.go |
| `internal/workflow/` | **Worktree 工作流编排** | worktree_orchestrator.go |
| `internal/worktree/` | **Git Worktree 快照/差异** | state_guard.go / divergence_log.go |
| `internal/github/` | **GitHub API 集成** (gh CLI wrapper) | gh.go / issue_closer.go / pr_merger.go |
| `internal/astgrep/` | AST-grep 集成 (C#/Kotlin/PHP/Ruby/Elixir) | scanner.go + 5 fixtures/ |
| `internal/mx/` | **MX 代码注解系统** (NOTE/WARN/ANCHOR/REASON/TODO/SPEC) | tag.go / scanner.go / resolver.go / fanin.go |
| `internal/tmux/` | **Tmux 面板/GUI 编排** | session.go (407 行) / detector.go |
| `internal/runner/` | ProcessRunner 抽象 | (子包) |
| `internal/config/` | **8 层配置合并** | resolver.go (1156 行) / manager.go / loader.go |
| `internal/constitution/` | 宪法规则加载 + 验证 | loader.go / rule.go / zone.go |
| `internal/statusline/` | Claude Code 状态栏渲染 | statusline.go |
| `internal/settings/` | AgentFM (Agent Format) 管理 | agentfm/ + yamlpatch/ |
| `internal/core/` | 核心 git/quality/project/integration | (子包) |
| `internal/feedback/` | 反馈链路 | (子包) |
| `pkg/models/` | 公共数据模型 | models.go |
| `pkg/version/` | 版本元数据 | version.go |
| `.claude/agents/moai/` | 9 个 Agent markdown 定义 | manager-* / plan-auditor / sync-auditor / builder-harness / super-advisor / manager-design |
| `.claude/skills/` | 27 个 Skill markdown 包 | moai-foundation-* / moai-workflow-* / moai-domain-* / moai-ref-* / moai-meta-* / moai-harness-* |
| `.claude/rules/moai/` | 5 类规则 (core/workflow/development/languages/design/quality) | 80+ markdown 规则 |
| `.claude/commands/moai/` | 13 个 slash command | plan/run/sync/loop/fix/mx/review 等 |
| `internal/template/` | **项目脚手架模板** | .claude/ + .moai/ + .github/ + .git_hooks/ |

---

## 三、详细能力列表

### 3.1 API 能力 (CLI & 内部接口)

| 能力 | 描述 | 关键位置 |
|------|------|---------|
| **70+ CLI 子命令** | init, spec, harness, worktree, hook, plan, run, sync, fix, loop, review, mx, codemaps, clean, gate, doctor, profile, deps, status, web, glm, cc, cg, astgrep, telemetry, constitution, migration, inventory, preference, pr, update, version, state, agentlint 等 | `internal/cli/*.go` |
| **Cobra 命令框架** | 基于 spf13/cobra + charm.land/fang/v2 样式化 | `internal/cli/root.go` + `fang.go` |
| **子命令分组** | launch / project / tools 三大分组 | `root.go:115-119` |
| **懒加载依赖** | REQ-PERF-003-A: trivial 命令跳过 InitDependencies | `root.go:43-67` |
| **ExitCoder 接口** | 自定义退出码 (worktree verify: 0=clean, 1=divergence, 2=suspect, 3=both) | `cmd/moai/main.go:17-19` |
| **fang 退出码桥接** | 保持 ExitCoder 错误链穿越 fang 包装 | `fang.go` + `TestFangExitCoderCharacterization` |
| **JSON 协议** | Hook 5 MiB stdin 上限 + 空 stdin 优雅无操作 | `internal/hook/protocol.go:17-56` |
| **Bubbletea TUI** | 状态栏 (statusline) + Web 仪表板 | `internal/tui/` + `internal/web/` |
| **Worktree subcommand tree** | snapshot/diff/verify/clean/status | `internal/cli/worktree/` + `internal/cli/worktree/worktree_validation.go` |
| **PR 子命令** | 监视、合并、审计 | `internal/cli/pr_watch_cmd.go` |
| **CG Mode** | Claude Code GLM 面板 (team mode) | `internal/cli/cg.go` + `cg_mode_hardening_test.go` |
| **GLM Tools** | 工具策略 | `internal/cli/glm_tools.go` (39758 行, 最大文件之一) |
| **Launch 命令** | `moai cc` / `moai cg` / `moai glm` 启动 Claude Code | `internal/cli/launcher.go` (27677 行) |
| **Profile Setup** | Claude Code 配置生成 | `internal/cli/profile_setup.go` (32221 行) |
| **Update CLI** | 自更新机制 (121 KB update.go) | `internal/cli/update.go` |
| **MCP 集成** | MCP 服务器配置 + Doctor | `internal/cli/mcp_doctor_coverage_test.go` |
| **OAuth 令牌保留** | 更新时保护 OAuth 凭证 | `internal/cli/oauth_token_preservation_test.go` |
| **诊断输出格式** | text / JSON / SARIF 2.1.0 | `internal/spec/lint.go:73-75` |
| **Hooks 协议** | Claude Code JSON stdin/stdout,支持 27 事件类型 | `internal/hook/types.go:20-134` |

### 3.2 数据模型 (Data Models)

| 模型 | 描述 | 位置 |
|------|------|------|
| **Entry (Session)** | `SessionID/SpecID/Phase/StartedAt/LastHeartbeat/PID/Host/CWD` 多会话注册 | `internal/session/registry.go:81-90` |
| **LoopState** | 反馈循环状态:`SpecID/Phase/Iteration/MaxIter/Feedback[]/StartedAt/UpdatedAt` | `internal/loop/state.go:76-84` |
| **Feedback** | 循环反馈:`TestsPassed/Failed/LintErrors/BuildSuccess/Coverage/Duration/Notes/Diagnostics` | `internal/loop/state.go:112-131` |
| **Decision** | 决策:`Action(continue/converge/request_review/abort)/NextPhase/Converged/Reason` | `internal/loop/state.go:134-139` |
| **PhaseState** | 会话阶段状态 (含 BlockerRpt/Checkpoint/Provenance) | `internal/session/store.go` |
| **Goal** | 条件驱动目标 (`Status: pending/active/cleared/satisfied/ceiling_exit`) | `internal/goal/schema.go` |
| **Eval + Verdict** | 2-tier 求值:机械条件 + 模型声明 | `internal/goal/evaluate.go:42-53` |
| **FailedCond** | 失败条件细节 (cmd/exit/tail) | `internal/goal/evaluate.go:21-25` |
| **CeilingVerdict** | 5-section 报告: Claim/Evidence/Baseline/Gaps/Residual-risk | `internal/goal/evaluate.go:30-36` |
| **Event (JSONL)** | 12+ 字段可扩展事件类型 (moai_subcommand/agent_invocation/spec_reference/feedback/session_stop/subagent_stop/user_prompt/apply_outcome/tool_failure/test_fail) | `internal/harness/types.go:87-217` |
| **Pattern** | `(event_type, subject, context_hash)` 模式聚合 + Count + Confidence + Tier | `internal/harness/types.go:287-309` |
| **Tier (enum)** | observation / heuristic / rule / auto_update (1/3/5/10 阈值) | `internal/harness/types.go:228-242` |
| **Promotion** | Tier 晋升事件 (JSONL) | `internal/harness/types.go:313-331` |
| **Proposal** | 自动更新提案 (ID/TargetPath/FieldKey/NewValue/PatternKey/Tier) | `internal/harness/types.go:342-366` |
| **Decision (Safety)** | 5 层安全闸门结果: approved / rejected / pending_approval | `internal/harness/types.go:384-396` |
| **HookInput** | Claude Code → MoAI 的 JSON stdin (100+ 字段) | `internal/hook/types.go` (658 行) |
| **HookOutput** | MoAI → Claude Code 的 JSON stdout | 同上 |
| **EventType (28 个)** | SessionStart/PreToolUse/PostToolUse/Stop/SubagentStop/PreCompact/PostToolUseFailure/Notification/SubagentStart/UserPromptSubmit/PermissionRequest/TeammateIdle/TaskCompleted/WorktreeCreate/WorktreeRemove/PostCompact/InstructionsLoaded/StopFailure/ConfigChange/TaskCreated/CwdChanged/FileChanged/Elicitation/ElicitationResult/PermissionDenied/PostToolBatch/UserPromptExpansion/MessageDisplay/Setup | `internal/hook/types.go:20-134` |
| **PermissionMode (5)** | default / acceptEdits / bypassPermissions / plan / bubble | `internal/permission/stack.go:22-43` |
| **Source (8 tier)** | policy / user / project / local / plugin / skill / session / builtin | `internal/config/types.go` |
| **Decision (Permission)** | allow / deny / ask | `internal/hook/types.go:178-188` |
| **Verdict (Audit)** | PASS / FAIL / FAIL_WARNED / BYPASSED / INCONCLUSIVE | `internal/runtime/audit_gate.go:14-36` |
| **LoopPhase (4)** | analyze / implement / test / review (闭环循环) | `internal/loop/state.go:27-32` |
| **Linter/Rule/Report/Finding** | SPEC lint 结果 + SARIF 输出 | `internal/spec/lint.go:19-69` |
| **Acceptance** | 树形验收标准 (Given/When/Then + REQ 映射) | `internal/spec/parser.go` |
| **SPEC (Frontmatter)** | YAML 元数据 (Priority/Tags/Title/HarnessLevel) | `internal/spec/parser.go` + `internal/harness/router/router.go:51-63` |
| **Level (Harness)** | minimal / standard / thorough | `internal/harness/router/router.go:21-28` |
| **Snapshot (Worktree)** | `SchemaVersion/CapturedAt/SnapshotID/HeadSHA/Branch/PorcelainLines/UntrackedSpecs` | `internal/worktree/state_guard.go:57-65` |
| **Divergence** | worktree 前/后差异 (Head/Branch/Untracked/Porcelain) | `internal/worktree/state_guard.go:135-145` |
| **Finding (astgrep)** | `RuleID/Severity/Message/File/Line/Column/EndLine/Language/Metadata(CWE/OWASP)` | `internal/astgrep/scanner.go:47-73` |
| **Range/Position/Diagnostic/LSP** | LSP 3.17 协议类型 | `internal/lsp/models.go` |
| **MX Tag (6 类)** | NOTE / WARN / ANCHOR / REASON / TODO / SPEC | `internal/mx/tag.go` |
| **Zone (4-enum)** | frozen-canonical / frozen-safety / evolvable-tuning / evolvable-experimental | `internal/constitution/validator.go:47-52` |
| **AmendmentProposal** | 宪法修订提案 | `internal/constitution/amendment.go` |
| **PermissionRule** | 8 层栈规则: Pattern + Action + Source + Origin | `internal/permission/stack.go:75-91` |
| **LearningEntry** | 进化学习记录 (LEARN-YYYYMMDD-NNN ID 格式) | `internal/evolution/learning.go` |

### 3.3 算法与策略 (Algorithms)

| 算法 | 描述 | 位置 |
|------|------|------|
| **Ralph 反馈循环** | 4 阶段 (analyze→implement→test→review) 状态机,支持 Start/Pause/Resume/Cancel | `internal/loop/controller.go:275-391` |
| **Decision Engine** | 5 优先级决策:MaxIter > QualityGate > Stagnation > HumanReview > Continue | `internal/ralph/engine.go:34-90` |
| **Stagnation Detection** | 整数指标停滞 + LSP 诊断数变化趋势 (REQ-LL-010) | `internal/loop/feedback.go:13-38` |
| **Quality Gate** | TestsFailed==0 ∧ LintErrors==0 ∧ BuildSuccess ∧ Coverage>=85% | `internal/loop/feedback.go:66-74` |
| **Bounded Channel (Drop Oldest)** | 64 容量反馈通道 + 溢出时丢弃最旧事件 (REQ-LL-009) | `internal/loop/feedback_channel.go:33-60` |
| **2-tier Goal Evaluation** | Tier 1 机械命令 + Tier 2 模型声明 + 自主/半自主双模 | `internal/goal/evaluate.go:109-208` |
| **Stagnation Threshold** | N 连续相同 progress notes 触发停滞告警 (默认 3) | `internal/goal/evaluate.go:86-101` |
| **5-section Ceiling Report** | Claim/Evidence/Baseline/Gaps/Residual-risk 结构化报告 | `internal/goal/evaluate.go:231-241` |
| **Tier Classification** | 4 阈值 [1,3,5,10] 触发 Tier promotion,confidence<0.70 强制 observation | `internal/harness/learner.go:113-140` |
| **Pattern Key Building** | `<event_type>:<subject>:<context_hash>` 唯一键 | `internal/harness/learner.go:99-101` |
| **Promotion Eligibility** | 过滤 degenerate (空 context hash + 空/unknown subject) lifecycle noise | `internal/harness/learner.go:163-171` |
| **Harness Routing** | 5 优先级: spec_override > force_thorough > escalation > auto_detection > mode_defaults | `internal/harness/router/router.go:92-128` |
| **Auto-Detection Rules** | minimal (file≤3, single_domain, bugfix/docs/config) → standard → thorough | `internal/harness/router/router.go:174-189` |
| **Force-Thorough Keywords** | 安全/支付/关键关键字触发 thorough 模式 | `internal/harness/router/router.go:191-200+` |
| **Atomic Write** | temp + rename 文件原子写入 (loop state, checkpoint) | `internal/loop/storage.go:96-114` |
| **Multi-Session Coordination** | 3 步协议: Register + Purge(>30min zombie) + Query + stderr 提示 | `internal/session/registry.go` + `internal/hook/session_start.go:60-72` |
| **Advisory Lock** | 文件锁 + 3-retry/10ms-backoff 防止并发写 | `internal/session/store.go:113-119` |
| **Staleness TTL** | 跳过 staleness check 需 `--resume` 标志 | `internal/session/store.go:168-176` |
| **In-Flight Transition Detection** | 扫描 .moai/state/ 检测 phase 转换中断 | `internal/session/store.go:184-212` |
| **Team Checkpoint Merge** | 多个 agent 检查点按 phase 规则合并 (Run 累加 / Plan/Sync 取最后) | `internal/session/store.go:269-324` |
| **UTF-8 校验** | 文本 artifacts (.md/.txt/.json/.yaml/.yml) 写入前必须 UTF-8 | `internal/session/store.go:382-388` |
| **EARS/GEARS Pattern Matching** | 5 GEARS 模式: Ubiquitous/Event/State/Where/Compound + 6 EARS legacy 兼容 | `internal/spec/ears.go` + `internal/spec/lint.go` |
| **Given/When/Then 树构建** | acceptance criteria 嵌套继承 + 重复 ID 检测 | `internal/spec/parser.go:116-183` |
| **AC ID 提取** | `AC-XXX-NNN-NN[.a[.i]]` 正则 + REQ 映射提取 | `internal/spec/parser.go:218` |
| **8-tier Config Resolution** | 优先级: Policy > User > Project > Local > Plugin > Skill > Session > Builtin | `internal/config/resolver.go:80-108` |
| **Diff-aware Reload** | 单 tier 重新加载,保留其他 tier,避免全量重载 | `internal/config/resolver.go:118-150+` |
| **Permission Stack Resolve** | Hook > BypassMode > PlanMode > Tier 遍历 (8 层) | `internal/permission/resolver.go:137-200+` |
| **Shell Command Bypass Defense** | 检测 `;`/`&&`/`$IFS` 等命令链绕过 + 词法扫描 + parser 双层防护 (CWE-214) | `internal/permission/stack.go:133-150` |
| **5-layer Safety Pipeline** | FrozenGuard → Canary(可跳过) → ContradictionDetector → RateLimiter → HumanOversight | `internal/constitution/pipeline.go:56-100+` |
| **Frozen Zone Detection** | 8 个 sentinel:HARNESS_FROZEN_AGENT/SKILL/RULE/COMMAND/HOOK/OUTPUTSTYLE/INSTRUCTION/CONFIG | `internal/hook/pre_tool.go:33-67` |
| **ast-grep 调用** | 调 `sg` CLI 外部进程 + JSON 输出解析 | `internal/astgrep/scanner.go` |
| **Multi-language ast-grep** | C#/Kotlin/PHP/Ruby/Elixir fixtures 验证 | `internal/astgrep/testdata/fixtures/` |
| **SARIF 2.1.0 转换** | Finding → SARIF 报告 | `internal/astgrep/sarif.go` |
| **LSP Diagnostics Bridge** | 旧 `gopls.Bridge` + 新 `Aggregator` 桥接 (向后兼容) | `internal/loop/go_feedback.go:19-28` |
| **LSP Aggregator** | 多 LSP server 聚合 (GOPLS-BRIDGE-001 / SPEC-LSP-AGG-003) | `internal/lsp/aggregator/` |
| **Worktree Snapshot/Diff** | git rev-parse + status --porcelain + ls-files + 排除过滤 | `internal/worktree/state_guard.go:81-132` |
| **Divergence Categorization** | HeadSHA/Branch/Porcelain/Untracked 四维差异 | `internal/worktree/state_guard.go:160-177` |
| **MX Tag Fan-in 计算** | AST 解析定位 MX 注解的引用计数 | `internal/mx/fanin.go` (6046 行) |
| **MX Tag LSP Query** | 通过 LSP 协议查询 call hierarchy/symbol references | `internal/mx/fanin_lsp.go` |
| **MX 16-language Resolver** | 16 编程语言注释前缀识别 | `internal/mx/resolver_16lang_test.go` |
| **Learning Cap (50)** | 进化学习最多 50 active,超限归档最旧 | `internal/evolution/learning.go:65-67` |
| **LEARN ID Format** | `LEARN-YYYYMMDD-NNN` 正则,防 path traversal | `internal/evolution/learning.go:19-28` |
| **Tmux Pane Layout** | MaxVisible 默认 3,前 N 用 vertical split,后用 horizontal | `internal/tmux/session.go:117-150` |
| **Sensitive Env Injection** | argv-safe 通道注入敏感 env (CWE-214) | `internal/tmux/session.go:60-69` |
| **4-dimension Quality Audit** | Functionality(40%) / Security(25%) / Craft(20%) / Consistency(15%) | `agents/moai/sync-auditor.md:39-44` |
| **Hierarchical Scoring (HRN-003)** | sub-criteria min/mean 聚合,二选一评分模型 | `agents/moai/sync-auditor.md:48-55` |
| **Socratic Interview** | 4-quadrant (Known-Knowns/Unknowns/Unknown-Knowns/Unknown-Unknowns) 模糊度分析 | `CLAUDE.md:142-153` |
| **6-Mode Orchestration** | Phase 0.95 6-mode catalog 决策树 (--team/--solo 强制覆盖) | `agents/moai/CLAUDE.md:53-56` |
| **Routing Observation Ledger** | append-only routing-ledger.jsonl 记录路由决策 + 隐私 digest | `skills/moai/SKILL.md:36-38` |

### 3.4 UI / 前端能力

| 能力 | 描述 | 位置 |
|------|------|------|
| **Bubbletea TUI** | 状态栏/选择器/交互式向导 | `internal/tui/` |
| **huh 表单** | interactive 提示 (yes/no, multi-choice, input) | `internal/cli/profile_setup.go` |
| **Lipgloss 样式** | 终端彩色输出 (Catppuccin 主题) | `internal/cli/theme.go` + `internal/cli/uikit/` |
| **Banner 渲染** | 启动横幅 + 版本信息 | `internal/cli/uikit/printBanner` |
| **Fang 帮助系统** | 样式化 help/errors/version 输出 | `internal/cli/fang.go` |
| **Statusline Hook** | Claude Code 状态栏注入 | `internal/cli/statusline.go` |
| **Golden Snapshots** | TUI 输出快照回归测试 | `internal/tui/golden/` |
| **Worktree 状态展示** | 多 worktree 状态可视化 | `internal/cli/worktree_validation.go` |
| **Progress 展示** | phase 进度条 + 阶段指示 | `internal/runtime/audit_report.go` |
| **Markdown Renderer** | termenv 渲染 markdown 到终端 | `internal/cli/printer/` |
| **Constitution List/Validate** | 规则表/JSON 输出 | `internal/cli/constitution.go` |
| **Web Dashboard** | `moai web` 启动本地 web 端口 | `internal/cli/web.go` + `internal/cli/web_port.go` |
| **Web 资源 (templ)** | a-h/templ 纯 Go HTML 模板 | `internal/web/` + `pkg/models` |
| **Mermaid 集成** | SPEC 文档自动生成 Mermaid 图 | `skills/moai-workflow-spec/` |
| **Nextra 文档站** | 生成静态文档站 (.docs-site/) | `agents/moai/manager-docs.md` |

### 3.5 集成能力 (Integrations)

| 集成 | 描述 | 位置 |
|------|------|------|
| **Claude Code 协议** | 27 事件 hook stdin/stdout JSON 协议 | `internal/hook/protocol.go` + `internal/hook/types.go` |
| **GitHub API (gh CLI)** | issue close / PR merge / review | `internal/github/gh.go` + `issue_closer.go` + `pr_merger.go` + `pr_reviewer.go` |
| **Git Worktree 隔离** | manager-develop/manager-design `isolation: worktree` 字段 | `agents/moai/manager-develop.md:14` |
| **Git Strategy 加载** | git-strategy.yaml 合并策略 | `internal/config/git_strategy_loader_test.go` |
| **MCP Servers** | .mcp.json 客户端 + WebSearch/WebFetch/Context7 | `.mcp.json` + `internal/mcp_doctor_coverage_test.go` |
| **LSP 3.17** | gopls/subprocess/transport/aggregator/cache | `internal/lsp/` (5 子包) |
| **gopls Bridge** | GOPLS-BRIDGE-001: gopls.Diagnostic 收集 | `internal/lsp/gopls/` + `internal/loop/go_feedback.go` |
| **ast-grep (sg)** | 外部 `sg` CLI 调用 + JSON 解析 | `internal/astgrep/scanner.go` |
| **Lifsea 16 languages** | 16 语言 AST 解析 (注释前缀识别) | `internal/mx/resolver_16lang_test.go` |
| **Mermaid 文档** | SPEC 文档自动 Mermaid 图 | `skills/moai-workflow-spec/modules/` |
| **OAuth Token Preservation** | 更新时保留 OAuth 凭证 | `internal/cli/oauth_token_preservation_test.go` |
| **tmux 集成** | 启动 Claude Code 多 pane 会话 (CG Mode) | `internal/tmux/session.go` + `internal/cli/cg.go` |
| **GLM 面板** | Claude Code GLM pane (worktree --team) | `internal/cli/glm.go` + `glm_tools.go` (39758 行) |
| **worktree verify exit codes** | 0=clean, 1=divergence, 2=suspect, 3=both | `cmd/moai/main.go:17` + `internal/cli/worktree/worktree_validation.go` |
| **MCP doctor** | MCP 集成健康检查 | `internal/cli/mcp_doctor_coverage_test.go` |
| **LSP doctor** | LSP server 状态诊断 | `internal/cli/lsp_doctor.go` |
| **权限 AST 集成** | `mvdan.cc/sh/v3/syntax` shell 命令 AST 解析 (防 $IFS bypass) | `internal/permission/stack.go:11` |
| **Conventional Commits** | `<type>(<scope>): <subject>` 提交规范 | `agents/moai/manager-git.md:55-60` |
| **Git 标签 checkpoint** | `moai_cp/$(date)` 注解标签 | `agents/moai/manager-git.md:48-52` |
| **Pre-commit Hook** | lefthook.yml 集成 | `lefthook.yml` |
| **GitHub Actions CI** | pre-built workflows | `.github/workflows/` + `.github/required-checks.yml` |
| **OS 信号处理** | posix/windows 分离 | `internal/cli/launch_exec_posix.go` + `launch_exec_windows.go` |
| **Windows 8.3 短路径修复** | MOAI_TEMP_DIR 环境变量 | `README.md:142-156` |

### 3.6 工具能力 (Tool/Function Calling)

| 工具 | 描述 | 位置 |
|------|------|------|
| **@MX 注解系统** | AI Agent 阅读代码时的 6 类上下文标签 (NOTE/WARN/ANCHOR/REASON/TODO/SPEC) | `internal/mx/tag.go` |
| **fan_in 计算** | 跨文件引用计数,识别关键路径 | `internal/mx/fanin.go` + `fanin_lsp.go` |
| **MX 扫描器** | 解析项目代码的 MX 注解 + 缺失/孤儿检测 | `internal/mx/scanner.go` |
| **MX Sidecar** | 在 LSP server 内部嵌入 MX 协议 | `internal/mx/sidecar.go` |
| **SPEC ID 检测** | 文本中提取 `SPEC-[DOMAIN]-[NUM]` 模式 | `internal/workflow/worktree_orchestrator.go:22-25` |
| **Moai-sidechain (moai mx)** | CLI 命令: 扫描/查询 MX 注解 | `internal/cli/mx_query.go` |
| **Globs/Grep 工具** | 复用 Claude Code 工具集 (子 agent) | `agents/moai/*.md` frontmatter `tools:` |
| **Read/Write/Edit/Bash** | Claude Code 标准工具,所有 manager-* agent 通用 | 同上 |
| **WebSearch/WebFetch** | 外部 API 研究 (manager-spec, plan-auditor 等) | `agents/moai/manager-spec.md:9` |
| **MCP Context7** | 文档查询 (manager-spec, builder-harness) | 同上 |
| **SendMessage** | Agent 间通信 (所有 manager-* 都列) | 同上 |
| **TaskCreate/Update/List/Get** | 任务管理 (所有 manager-* 都列) | 同上 |
| **DesignSync** | Claude Design 双向同步 (manager-design 专属) | `agents/moai/manager-design.md:10` |
| **Shell (sh)** | sh shell AST parser (`mvdan.cc/sh/v3/syntax`) | `internal/permission/stack.go:11` |
| **sh 词法/语法双层防护** | lex (fast) + parser (handle `$IFS`) 防 bypass | `internal/permission/stack.go:139-146` |

### 3.7 安全能力 (Security)

| 安全能力 | 描述 | 位置 |
|---------|------|------|
| **8 层 Permission Stack** | Policy > User > Project > Local > Plugin > Skill > Session > Builtin | `internal/permission/stack.go` + `resolver.go` |
| **5 个 Permission Mode** | default / acceptEdits / bypassPermissions / plan / **bubble** (新增) | `internal/permission/stack.go:22-43` |
| **Bubble Mode** | fork agent 的权限请求 escalate 到 parent session 的 AskUserQuestion | `internal/permission/stack.go:39-43` |
| **Fork Depth Limitation** | depth>3 时所有 mode (除 plan) 降级为 bubble | `internal/permission/resolver.go:31-32` |
| **Strict Mode** | security.yaml.strict_mode=true 时拒绝 bypassPermissions | `internal/permission/resolver.go:33-35` |
| **Shell Command Bypass Defense** | CWE-214 防护,`;`/`&&`/`$IFS` 检测 (双层:lex+parser) | `internal/permission/stack.go:133-150` |
| **UpdatedInput Recursion Guard** | hook 改写输入后清空 HookResponse 防无限循环 | `internal/permission/resolver.go:147-164` |
| **Plan Mode Hard Lock** | plan mode 下所有写操作强制 deny | `internal/permission/resolver.go:182-195` |
| **Pre-allowlist** | builtin tier 的安全工具白名单 (Read/LS/Glob/Grep 等) | `internal/permission/resolver.go:113-121` |
| **HARNESS_FROZEN_* 8 sentinels** | PreToolUse 拦截 harness-learner 写 FROZEN zone | `internal/hook/pre_tool.go:33-67` |
| **5-layer Constitution Pipeline** | FrozenGuard → Canary → Contradiction → RateLimiter → HumanOversight | `internal/constitution/pipeline.go` |
| **Frozen Zone** | frozen-canonical / frozen-safety 区不被自动学习修改 | `internal/constitution/zone.go` + `frozen_guard.go` |
| **Canary Gate** | 影子评估,score drop 超阈值拒绝 (canaryScoreDropThreshold) | `internal/constitution/canary.go` |
| **Contradiction Detector** | 检测新规则与已有规则冲突 | `internal/constitution/contradiction.go` |
| **Rate Limiter** | 自动更新频率限制 | `internal/constitution/rate_limiter.go` |
| **Human Oversight** | L5 强制人工审批 | `internal/constitution/human_oversight.go` |
| **Single-writer Lock** | 修宪流程文件锁,防并发 | `internal/constitution/pipeline.go:58-63` |
| **Constitution Validator** | 9 个 sentinel: DRIFT / SOURCE_FILE_MISSING / ZONE_UNREGISTERED / FROZEN_WITHOUT_CANARY / ANCHOR_NOT_FOUND / DUPLICATE_ID / STALE_ENTRY / DUPLICATE_ZONE_MARKER / INVALID_ZONE_CLASS | `internal/constitution/validator.go:13-44` |
| **Zone Registry** | `.claude/rules/moai/core/zone-registry.md` SSOT | `internal/constitution/loader.go` |
| **Audit Gate** | plan-auditor 5 步协议: hash → cache → invoke → route → persist | `internal/runtime/audit_gate.go:199-297` |
| **24h Audit Cache** | PASS 缓存 24h 复用 (REQ-WAG-003) | `internal/runtime/audit_cache.go` |
| **7-day Grace Window** | 审计 FAIL 在合并后 7 天仅警告不阻塞 | `internal/runtime/audit_gate.go:38-40` |
| **Audit Bypass** | `--skip-audit` / `MOAI_SKIP_PLAN_AUDIT=1` 显式绕过 | `internal/runtime/audit_gate.go:46-48, 207-219` |
| **Skipped/Malformed Verdict** | INCONCLUSIVE 不等价 PASS (REQ-WAG-007) | `internal/runtime/audit_gate.go:30-36, 252-258` |
| **Worktree Guard** | snapshot/diff 异常检测 (CI 自主权) | `internal/worktree/state_guard.go` |
| **Sentinel Catalog** | 8 HARNESS_FROZEN_* 字符串模式 | `internal/hook/sentinel_catalog_test.go` |
| **Sensitive Env Argv-safe** | tmux set-environment 旁路,CWE-214 防御 | `internal/tmux/session.go:60-69` |
| **MOAI_CONSTITUTION_SKIP_VALIDATE** | 验证旁路环境变量 | `internal/constitution/validator.go:43` |
| **OAuth Token Preservation** | 更新时 OAuth 凭证保护 | `internal/cli/oauth_token_preservation_test.go` |
| **Sub-agent Boundary** | subagent spawn 边界检查 | `internal/harness/subagent_boundary_test.go` |
| **Cohabitation Guard** | 多个 hook 共存时防冲突 | `internal/hook/cohabitation_guard_test.go` |
| **OWASP Checklist** | 引用 moai-ref-owasp-checklist skill | `skills/moai-ref-owasp-checklist/` |
| **LLM Security** | 引用 moai-ref-llm-security skill | `skills/moai-ref-llm-security/` |
| **SPEC-V3R6-SESSION-ID-ATTRIBUTION-REPAIR** | session_id 缺失时 stderr 警告 | `internal/hook/session_start.go:73-85` |
| **SessionMultiSessionCoord** | 多 session 协调防冲突 (advisory lock) | `internal/session/registry.go` |

### 3.8 性能能力 (Performance)

| 性能能力 | 描述 | 位置 |
|---------|------|------|
| **5ms 启动** | 单一 Go 二进制,无 Python 解释器开销 | `README.md:54` |
| **goroutine 并发** | 原生并发,无 asyncio/threading | `README.md:55` |
| **Compile-time Type Safety** | Go 静态类型,无运行时检查 | `README.md:56` |
| **Pre-built Cross-platform** | macOS/Linux/Windows 二进制分发 | `README.md:57` |
| **Lazy Init** | REQ-PERF-003-A: trivial 命令跳过全量依赖 | `internal/cli/root.go:43-67` |
| **Bounded Channel** | 64 容量反馈通道,溢出 drop oldest (非阻塞) | `internal/loop/feedback_channel.go` |
| **Async TraceWriter** | REQ-OBS-003: 非阻塞 trace 写入 | `internal/hook/registry.go:353-394` |
| **24h Audit Cache** | plan-auditor PASS 缓存 24h | `internal/runtime/audit_cache.go` |
| **Diff-aware Config Reload** | 只重载变化 tier,不全量 | `internal/config/resolver.go:118-150+` |
| **Per-Spec Loop Caching** | loop state 按 specID 索引 (FileStorage) | `internal/loop/storage.go` |
| **Atomic Write** | temp + rename 防半写 | `internal/loop/storage.go:96-114` |
| **Drift Index Caching** | SPEC drift index 缓存 (perf) | `internal/spec/drift_cache.go` |
| **Drift Timebox** | 365 leaves perf benchmark | `internal/spec/drift_test.go` + `perf_365_leaves_testdata` |
| **Hook Timeout 30s** | 单个 hook 30s timeout,防卡死 | `internal/hook/types.go:15` + `DefaultHookTimeout` |
| **ReadAll Limit (5 MiB)** | hook stdin 上限,防 OOM | `internal/hook/protocol.go:17` |
| **Active Sessions Cap** | session_id 注册去重 (idempotent) | `internal/session/registry.go:170-178` |
| **Atomic Lock 3-retry/10ms** | 文件锁防并发写开销 | `internal/session/store.go:113-119` |
| **Frozen Guard (no I/O)** | L1 闸门无 I/O,纯内存 | `internal/constitution/frozen_guard.go` |
| **Stable Sort (insertion)** | 进化学习 < 50 项用 insertion sort | `internal/evolution/learning.go:186-189` |
| **Go Backend Coverage 85-100%** | 85-100% 测试覆盖率 | `README.md:62` |
| **TUI Golden Snapshots** | 预渲染快照回归,无重新计算 | `internal/tui/golden/` |

### 3.9 部署/分发能力 (Deployment)

| 能力 | 描述 | 位置 |
|------|------|------|
| **单一二进制分发** | 零依赖 (与 Python pip+venv 对比) | `README.md:53` |
| **macOS / Linux / WSL** | install.sh 安装 | `install.sh` (10867 行) |
| **Windows PowerShell 7.x+** | install.ps1 安装 (15 KB) | `install.ps1` (15156 行) |
| **Windows install.bat** | 命令行包装 (6640 行) | `install.bat` |
| **go install** | `go install ./cmd/moai` | `Makefile:38` |
| **go build (LDFLAGS)** | 注入 Version/Commit/Date | `Makefile:9` |
| **GoReleaser** | 跨平台构建配置 | `.goreleaser.yml` |
| **Local Release** | `make release-local` 写 `~/.moai/releases/` | `Makefile:27-33` |
| **Auto Update** | `moai update` 自更新机制 (121 KB) | `internal/cli/update.go` |
| **Update Cleanup** | 旧版本清理 + 命名空间保护 | `internal/cli/update_cleanup.go` + `update_namespace_protect.go` |
| **Update Archive** | 归档 + 强制重装 | `internal/cli/update_archive.go` + `update_clean_install.go` |
| **CI Mirror** | `make ci-local` 镜像 GitHub Actions | `scripts/ci-mirror/run.sh` |
| **Cross-compile** | GOOS/GOARCH 矩阵 | `.goreleaser.yml` + `Makefile` |
| **Preflight Gate** | `make preflight` = lint-fast + test-race-short + build | `Makefile:110-117` |
| **Templates** | `.claude/` + `.moai/` + `.github/` + `.git_hooks/` 模板 | `internal/template/templates/` |
| **Migration** | `moai migrate agency` 旧版兼容 | `internal/cli/migrate_agency.go` (22248 行) |
| **Migration Rollback** | `migrate_restore_skill.go` | 同包 |
| **Migration Tracking** | `migrations/` 目录 | `internal/migration/migrations/` |
| **Windows Korean Username Fix** | MOAI_TEMP_DIR 环境变量 | `README.md:142-156` |
| **WSL 推荐** | 文档明确推荐 WSL | `README.md:99` |
| **Git for Windows 必需** | 安装检查 | `install.sh` + `install.ps1` |
| **Coverage Tooling** | `go tool cover` HTML 输出 | `Makefile:46-48` |
| **Makefile Multi-target** | 30+ target | `Makefile:16` |
| **CI Watch** | ciwatch 守护进程 | `internal/ciwatch/` |
| **Versioning** | git tag + git describe | `Makefile:7-8` |
| **Pre-built Binaries** | GitHub Releases 自动分发 | `.goreleaser.yml` |

### 3.10 测试能力 (Testing)

| 能力 | 描述 | 位置 |
|------|------|------|
| **go test -race** | 全量竞态检测 | `Makefile:41` |
| **85-100% Coverage** | 模块级测试覆盖率 | `README.md:62` |
| **Coverage HTML** | `make coverage` 输出 | `Makefile:46-48` |
| **Unit Tests** | 数百 _test.go 文件 | 各包 |
| **Integration Tests** | e2e + 端到端集成测试 | `internal/cli/integration_test.go` + `internal/session/integration_test.go` |
| **Protocol E2E** | hook 协议端到端 | `internal/hook/protocol_e2e_test.go` (17193 行) |
| **E2E Worktree** | 端到端 worktree 测试 | `internal/cli/e2e_ios_test.go` (5923 行) |
| **Golden Snapshots (TUI)** | 终端输出快照回归 | `internal/tui/golden/` |
| **Property Tests** | `goleak` goroutine 泄漏检测 | `go.mod:20` |
| **Mock Tests** | `_mock_test.go` 接口 mock | `internal/cli/mock_test.go` |
| **FakeClock** | 确定性时间注入 | `internal/session/registry.go:103-110` |
| **Test Data Fixtures** | 数百 testdata 目录 | 各包 `testdata/` |
| **SPEC TestData** | valid/cycle-a/cycle-b/breaking-no-bcid/dangling-rule 等 17+ 场景 | `internal/spec/testdata/` |
| **constitution_test** | 9 个 sentinel 验证 | `internal/constitution/*_test.go` |
| **audit-gate tests** | 5 verdict 路径测试 | `internal/cli/audit-gate/` |
| **Plan Audit D7/D8** | Phase D7/D8 单独测试 | `internal/cli/plan_audit_d7_d8_test.go` |
| **In-Flight Transition** | 阶段中断检测测试 | `internal/session/inflight_test.go` |
| **Team Mode** | Team merge 测试 | `internal/session/team_merge_test.go` |
| **Permission Stack Tests** | 8 tier + bypass + IFS | `internal/permission/*_test.go` |
| **Resilience Tests** | 故障注入 | `internal/resilience/` |
| **Sandbox Tests** | 沙箱隔离 | `internal/sandbox/` |
| **Mock Tmux** | 注入 RunFunc | `internal/tmux/WithSessionRunFunc` |
| **Performance Bench** | `drift_perf_test.go` 365 leaves | `internal/spec/drift_perf_test.go` |
| **bench_test** | Resolver 基准 | `internal/config/resolver_bench_test.go` |
| **concurrent_test** | spec_linker 并发 | `internal/github/spec_linker_concurrent_test.go` |
| **Testify Suite** | go-playground/validator | `go.mod:14` |
| **Lefthook** | pre-commit hook | `lefthook.yml` |
| **CI workflow** | go test / golangci-lint / coverage | `.github/workflows/ci.yml` |
| **CodeQL** | SAST 静态分析 | `.github/workflows/codeql.yml` |
| **CodeRabbit** | PR AI review | `.coderabbit.yaml` |
| **Required Checks** | `required-checks.yml` 锁定 | `.github/required-checks.yml` + `verify-required-checks` |
| **Constitution-check** | zone-registry 完整性 | `make constitution-check` |
| **TUI Snapshot Verify** | `make tui-snapshot-verify` | `Makefile:106-108` |

---

## 四、技术栈

### 4.1 核心语言 & 框架
- **Go 1.26.4** (核心实现, ~100K 行)
- **Templ 0.3.1020** (HTML 模板引擎,纯 Go 编译)
- **Cobra v1.10.2** (CLI 命令框架)
- **YAML v3** (配置解析)
- **Validator v10.30.3** (struct 验证)

### 4.2 终端/TUI
- **charm.land/fang/v2 v2.0.1** (样式化 CLI)
- **charm.land/lipgloss/v2 v2.0.5** (终端样式)
- **charmbracelet/bubbletea v1.3.10** (TUI 框架)
- **charmbracelet/huh v1.0.0** (交互式表单)
- **charmbracelet/lipgloss v1.1.0** (旧版本)
- **charmbracelet/x/powernap v0.1.6** (工具集)
- **muesli/termenv v0.16.0** (ANSI)
- **mattn/go-isatty v0.0.22** (TTY 检测)
- **mattn/go-runewidth v0.0.24** (字符宽度)
- **muesli/cancelreader v0.2.2** (取消读)
- **muesli/mango v0.1.0** + **mango-cobra v1.2.0** + **mango-pflag v0.1.0** (manual 解析)

### 4.3 并发 & 同步
- **golang.org/x/sync v0.21.0** (errgroup, singleflight)
- **golang.org/x/sys v0.46.0** (OS 调用)
- **golang.org/x/text v0.38.0** (Unicode normalization)
- **golang.org/x/crypto v0.53.0** (间接,密码学)
- **golang.org/x/mod v0.37.0** (模块管理)
- **golang.org/x/net v0.56.0** (网络)
- **golang.org/x/tools v0.46.0** (工具)

### 4.4 静态分析
- **smacker/go-tree-sitter** (增量解析器, 多语言 AST)
- **mvdan.cc/sh/v3 v3.13.1** (shell AST 解析, 防 IFS bypass)

### 4.5 国际化
- **fatih/color v1.19.0** (彩色输出)
- **natefinch/atomic v1.0.1** (原子写)
- **aymanbagabas/go-osc52/v2 v2.0.1** (OSC52 剪贴板)
- **atotto/clipboard v0.1.4** (剪贴板)
- **dustin/go-humanize v1.0.1** (人类可读)
- **leodido/go-urn v1.4.0** (URN 解析)
- **rivo/uniseg v0.4.7** (Unicode 分段)
- **clipperhouse/displaywidth v0.11.0** + **clipperhouse/uax29/v2 v2.7.0** (宽度/分词)
- **xo/terminfo v0.0.0-20220910002029** (terminfo)
- **hashstructure v2 v2.0.2** (哈希)
- **catppuccin/go v0.3.0** (主题)
- **fsnotify/fsnotify v1.10.1** (文件监视)
- **cenkalti/backoff/v4 v4.3.0** (指数退避)
- **goleak v1.3.0** (goroutine 泄漏)
- **inconshreveable/mousetrap v1.1.0** (Windows 鼠标)
- **sourcegraph/jsonrpc2 v0.2.1** (JSON-RPC)
- **spf13/pflag v1.0.10** (POSIX flag)
- **charmbracelet/ultraviolet v0.0.0-20260205113103** (终端处理)

### 4.6 跨平台
- 分平台文件:`*_posix.go` / `*_windows.go` (lock, exec, web_port, migrate_agency, update_cleanup)
- `runtime.GOOS` 检测

### 4.7 Agent / Skill 定义语言
- **YAML Frontmatter** (Claude Code Agent/Skill 格式)
- **Markdown 主体**
- **MX 注释** (Go/TS/Python/... 16 语言)

### 4.8 外部 CLI
- `git` (必需)
- `gh` (GitHub CLI, PR/issue)
- `sg` (ast-grep)
- `tmux` (CG mode)
- `go` (test/vet, 反馈循环)
- `gopls` (LSP server)
- `golangci-lint` (lint)
- `yq` (YAML 处理)

### 4.9 文档
- **Nextra** (React 静态站点)
- **Mermaid** (架构图)
- **MDX** (Markdown + JSX)

### 4.10 工具链
- **lefthook** (git hooks)
- **goreleaser** (跨平台发布)
- **CodeQL** (SAST)
- **CodeRabbit** (AI PR review)
- **Codecov** (覆盖率)

---

## 五、关键代码片段 (Top 10 核心函数)

### 1. **LoopController.runLoop** — Ralph 反馈循环核心 (`internal/loop/controller.go:275-391`)

```go
// runLoop executes the feedback loop phases in sequence.
// It runs in a dedicated goroutine and responds to context cancellation.
func (c *LoopController) runLoop(ctx context.Context) {
    defer close(c.done)

    for {
        // Check context cancellation.
        select {
        case <-ctx.Done():
            c.mu.Lock()
            c.running = false
            c.mu.Unlock()
            return
        default:
        }

        // Collect feedback for the current phase.
        fb, err := c.feedback.Collect(ctx)
        if err != nil {
            if ctx.Err() != nil {
                c.mu.Lock()
                c.running = false
                c.mu.Unlock()
                return
            }
            fb = &Feedback{}
        }

        // Record feedback under lock.
        c.mu.Lock()
        fb.Phase = c.state.Phase
        fb.Iteration = c.state.Iteration
        c.state.Feedback = append(c.state.Feedback, *fb)
        c.state.UpdatedAt = time.Now()
        if err := c.storage.SaveState(c.state); err != nil {
            slog.Default().Warn("failed to save loop state after feedback collection", ...)
        }

        currentPhase := c.state.Phase
        c.mu.Unlock()

        // At the review phase, invoke the decision engine.
        if currentPhase == PhaseReview {
            c.mu.Lock()
            stateCopy := *c.state
            stateCopy.Feedback = make([]Feedback, len(c.state.Feedback))
            copy(stateCopy.Feedback, c.state.Feedback)
            c.mu.Unlock()

            decision, decErr := c.engine.Decide(ctx, &stateCopy, fb)
            if decErr != nil {
                decision = &Decision{Action: ActionContinue, NextPhase: PhaseAnalyze}
            }

            c.mu.Lock()
            switch decision.Action {
            case ActionConverge:
                c.converged = true
                c.running = false
                if err := c.storage.DeleteState(c.state.SpecID); err != nil { ... }
                c.mu.Unlock()
                return
            case ActionAbort:
                c.running = false
                c.storage.DeleteState(c.state.SpecID)
                c.mu.Unlock()
                return
            case ActionRequestReview:
                c.running = false
                c.paused = true
                c.storage.SaveState(c.state)
                c.mu.Unlock()
                return
            case ActionContinue:
                c.state.Iteration++
                c.state.Phase = PhaseAnalyze
                c.state.UpdatedAt = time.Now()
                c.storage.SaveState(c.state)
                c.mu.Unlock()
                continue
            default:
                c.mu.Unlock()
            }
        }

        // Advance to the next phase (non-review phases).
        c.mu.Lock()
        c.state.Phase = NextPhase(c.state.Phase)
        c.state.UpdatedAt = time.Now()
        c.mu.Unlock()
    }
}
```
**为什么核心**: 这是整个"AI 自迭代"心跳。goroutine 模式 + 锁边界 + 4 阶段闭环 + 4 决策 (converge/abort/continue/request_review) + 原子持久化,是 MoAI 与简单 LLM wrapper 的本质区别。

### 2. **Registry.Dispatch** — Hook 事件分发 (`internal/hook/registry.go:71-163`)

```go
// Dispatch sends an event to all registered handlers for the given event type.
// Handlers are executed sequentially within a timeout context. If any handler
// returns Decision "block", remaining handlers are skipped and the block result
// is returned immediately (REQ-HOOK-003).
func (r *registry) Dispatch(ctx context.Context, event EventType, input *HookInput) (*HookOutput, error) {
    r.mu.Lock()
    handlers := make([]Handler, len(r.handlers[event]))
    copy(handlers, r.handlers[event])
    r.mu.Unlock()

    if len(handlers) == 0 {
        slog.Debug("no handlers registered for event", "event", string(event))
        return r.defaultOutputForEvent(event, input), nil
    }

    // Lazily initialize TraceWriter on first Dispatch
    if input != nil {
        r.ensureTraceWriter(input.SessionID)
    }

    ctx, cancel := context.WithTimeout(ctx, r.timeout)
    defer cancel()

    merged := r.defaultOutputForEvent(event, input)

    for i, h := range handlers {
        slog.Debug("dispatching handler", "event", string(event), "handler_index", i, "handler_total", len(handlers))
        start := time.Now()
        output, err := h.Handle(ctx, input)
        elapsed := time.Since(start)

        if err != nil {
            if errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled) || ctx.Err() != nil {
                slog.Error("hook execution timed out", ...)
                r.writeTrace(input, event, h, elapsed, nil, err)
                return nil, fmt.Errorf("%w: %v", ErrHookTimeout, err)
            }
            r.writeTrace(input, event, h, elapsed, nil, err)
            return nil, fmt.Errorf("handler %d for event %s: %w", i, event, err)
        }
        r.writeTrace(input, event, h, elapsed, output, nil)

        // Handler returned block: short-circuit
        if output != nil && isBlockDecision(output) {
            reason := getBlockReason(output)
            slog.Info("handler blocked action", ...)
            return output, nil
        }

        // Handler signalled exit code 2 (TeammateIdle keep-working, TaskCompleted reject)
        if output != nil && output.ExitCode == 2 {
            return output, nil
        }

        mergeHandlerOutput(merged, output)
    }
    return merged, nil
}
```
**为什么核心**: MoAI 之所以能拦截 Claude Code 的每一次工具调用,都在这里。支持 block 短路、超时、退出码 2、output 合并、Trace 写入 — 是 harness engineering 的"神经系统"。

### 3. **PermissionResolver.Resolve** — 8 层权限栈 (`internal/permission/resolver.go:137-200+`)

```go
// Resolve determines the permission decision for a tool invocation.
// It walks the 8-tier stack in priority order and returns the first non-empty decision.
func (r *PermissionResolver) Resolve(tool string, input json.RawMessage, ctx ResolveContext) (*ResolveResult, error) {
    r.mu.RLock()
    defer r.mu.RUnlock()

    inputStr := string(input)
    trace := ResolutionTrace{Tool: tool, Input: inputStr}

    // Handle UpdatedInput from hook - re-run resolution with mutated input
    if ctx.HookResponse != nil && len(ctx.HookResponse.UpdatedInput) > 0 && ctx.HookResponse.PermissionDecision == "" {
        newCtx := ctx
        newCtx.HookResponse = nil // Clear hook to avoid infinite loop
        newInput := ctx.HookResponse.UpdatedInput
        result, err := r.Resolve(tool, newInput, newCtx)
        if err != nil { return nil, err }
        result.UpdatedInput = newInput
        result.Trace = trace
        return result, nil
    }

    // Step 1: Handle bypassPermissions mode (short-circuit)
    if ctx.Mode == ModeBypassPermissions && !ctx.StrictMode {
        if ctx.IsFork {
            return r.handleBypassInFork(tool, inputStr, ctx, &trace), nil
        }
        return &ResolveResult{Decision: DecisionAllow, ResolvedBy: config.SrcBuiltin, Origin: "bypassPermissions mode", Trace: trace}, nil
    }

    // Step 2: Handle plan mode (deny all writes)
    if ctx.Mode == ModePlan && IsWriteOperation(tool, inputStr) {
        return &ResolveResult{Decision: DecisionDeny, ResolvedBy: config.SrcBuiltin, Origin: "plan mode denies writes", SystemMessage: "plan mode denies writes", Trace: trace}, nil
    }

    // Step 3: Apply hook decision (overrides all tiers)
    if ctx.HookResponse != nil && ctx.HookResponse.PermissionDecision != "" {
        hookDecision := Decision(ctx.HookResponse.PermissionDecision)
        trace.Tries = append(trace.Tries, TierTry{Tier: config.SrcBuiltin, Matched: true, Reason: "hook overrides tier walk"})
        return &ResolveResult{Decision: hookDecision, ResolvedBy: config.SrcBuiltin, Origin: "hook decision", Trace: trace}, nil
    }

    // ... Steps 4-9: Walk 8-tier stack from highest to lowest priority
    for _, source := range config.AllSources() {
        rules := ctx.RulesByTier[source]
        for _, rule := range rules {
            if rule.Matches(tool, inputStr) {
                ...
                return &ResolveResult{Decision: rule.Action, ResolvedBy: source, Origin: rule.Origin, Trace: trace}, nil
            }
        }
    }
    return &ResolveResult{Decision: DecisionDeny, ...}, nil
}
```
**为什么核心**: Claude Code 的每次工具调用都要经此。8 层栈、bubble mode、bypass/plan/hook 短路、`$IFS` bypass 防御、UpdatedInput 递归保护 — 是 MoAI 整个安全模型的实现核心。

### 4. **Goal.Evaluate** — 2-tier 目标求值器 (`internal/goal/evaluate.go:109-208`)

```go
// Evaluate runs the 2-tier evaluation cycle (plan §B.3 steps 1-8). It MUTATES
// the goal (increments TurnsUsed, sets Status, appends a Progress entry) and
// returns the Verdict to emit + whether the turn should be blocked.
func (e *Eval) Evaluate(ctx context.Context, g *Goal) (Verdict, bool) {
    // Step 1: inactive goal → no block.
    if g == nil || g.Status == StatusCleared || g.Status == StatusSatisfied {
        return Verdict{}, false
    }

    // Step 4 (checked before ceiling so a native /goal always wins): yield.
    if e.NativeGoalActive {
        return Verdict{Yielded: true, Reason: "native /goal active — stop-goal yields (no double-block)"}, false
    }

    // Step 2: increment turns; ceiling → 5-section verdict, no block.
    g.TurnsUsed++
    if g.Ceiling.MaxTurns > 0 && g.TurnsUsed >= g.Ceiling.MaxTurns {
        g.Status = StatusCeilingExit
        v := Verdict{CeilingExit: true, Verdict: e.ceilingReport(g, "turn ceiling reached")}
        e.appendProgress(g, "ceiling-exit")
        return v, false
    }

    // Step 3: stagnation → stop + E1/E3 escalation note, no block.
    if e.isStagnant(g) {
        g.Status = StatusCeilingExit
        v := Verdict{Stagnation: true, Verdict: e.ceilingReport(g, "stagnation: no progress for N iterations (escalate E1/E3)")}
        e.appendProgress(g, "stagnation-exit")
        return v, false
    }

    // Step 5: Tier 1 — run mechanical conditions.
    var failed []FailedCond
    hasMechanical := false
    for _, c := range g.Conditions {
        if c.Type != ConditionMechanical { continue }
        hasMechanical = true
        exit, out, err := e.Runner.Run(ctx, c.Cmd)
        expect := c.ExpectExit
        condFailed := err != nil || exit != expect
        if condFailed {
            fc := FailedCond{Cmd: c.Cmd, Exit: exit, Tail: tail(out)}
            if err != nil { fc.Tail = tail(out + " (" + err.Error() + ")") }
            failed = append(failed, fc)
        }
    }

    blockNeeded := len(failed) > 0

    // Step 6: Tier 2 — only when all mechanical pass AND ≥1 model condition.
    if !blockNeeded && g.HasModelConditions() {
        blockNeeded = true // model claim pending orchestrator evaluation
    }

    // Step 7: all conditions satisfied → no block, status satisfied.
    if !blockNeeded {
        g.Status = StatusSatisfied
        e.appendProgress(g, "all conditions satisfied")
        return Verdict{}, false
    }

    // A block is needed. Build the failed-condition detail
    detail := e.blockReason(g, failed, hasMechanical)

    // Step 8 (D8): semi-autonomous checkpoint branch.
    if g.ProgressionMode == ProgressionSemiAutonomous {
        e.appendProgress(g, "semi-autonomous checkpoint")
        v := Verdict{
            Decision:     "block",
            Reason:       fmt.Sprintf("semi-autonomous checkpoint: orchestrator to confirm continuation (turn %d of %d)", g.TurnsUsed, g.Ceiling.MaxTurns),
            Mode:         string(ProgressionSemiAutonomous),
            Turn:         g.TurnsUsed,
            Ceiling:      g.Ceiling.MaxTurns,
            LastProgress: lastProgress(g),
        }
        if len(failed) > 0 { v.FailedConditions = failed }
        return v, true
    }

    // Autonomous mode: plain block
    e.appendProgress(g, detail)
    return Verdict{Decision: "block", Reason: detail}, true
}
```
**为什么核心**: `/moai goal` 是 MoAI 的"条件驱动无限循环" — 用户声明完成条件,系统持续跑直到条件满足。8 步状态机 + 2 tier(机械+模型) + ceiling/stagnation 防护 + autonomous/semi-autonomous 双模,是"agentic loop"的灵魂。

### 5. **GatewayConfig.Invoke** — 计划审计门控 (`internal/runtime/audit_gate.go:199-297`)

```go
// Invoke executes the full audit gate logic for the configured SPEC.
//
// The function follows the 5-step protocol defined in run.md Phase 0.5:
// Step 1: compute plan artifact hash
// Step 2: check 24h cache
// Step 3: invoke plan-auditor (if cache miss)
// Step 4: route verdict
// Step 5: persist to progress.md and daily report
func (c *GateConfig) Invoke(ctx context.Context) (*AuditResult, error) {
    now := c.Clock.Now().UTC()
    result := &AuditResult{SpecID: c.SpecID, AuditAt: now}

    // [BYPASS PATH] REQ-WAG-006
    if c.SkipAudit {
        result.Verdict = VerdictBypassed
        result.BypassUser = c.UserName
        result.BypassReason = c.BypassReason
        if c.BypassReason == "" { result.BypassReason = "non-interactive" }
        if err := c.Reporter.AppendRun(c.SpecID, result); err != nil {
            fmt.Fprintf(os.Stderr, "[plan-audit] warning: failed to write bypass report: %v\n", err)
        }
        return result, nil
    }

    // Step 1: compute plan artifact hash
    hash, err := c.Cache.ComputeHash(c.SpecDir)
    if err != nil {
        result.Verdict = VerdictInconclusive
        result.AuditorVersion = "plan-auditor/hash-error"
        _ = c.Reporter.AppendRun(c.SpecID, result)
        return result, fmt.Errorf("plan artifact hash: %w", err)
    }

    // Step 2: check 24h cache
    if cached, cacheHit := c.Cache.Lookup(c.SpecID, hash, now); cacheHit {
        result.Verdict = VerdictPass
        result.CacheHit = true
        result.CachedAuditAt = cached.AuditAt
        result.AuditorVersion = cached.AuditorVersion
        result.ReportPath = cached.ReportPath
        fmt.Printf("[plan-audit] cache hit (verdict=PASS, age=%s)\n", now.Sub(cached.AuditAt).Round(time.Minute))
        _ = c.Reporter.AppendRun(c.SpecID, result)
        return result, nil
    }

    // Step 3: invoke plan-auditor
    fmt.Printf("[plan-audit] invoking plan-auditor for %s (gate=mandatory)\n", c.SpecID)
    verdict, reportPath, auditErr := c.Auditor.Audit(ctx, c.SpecDir)
    result.ReportPath = reportPath
    result.AuditorVersion = "plan-auditor/v1"

    if auditErr != nil {
        // REQ-WAG-007: auditor error → INCONCLUSIVE
        result.Verdict = VerdictInconclusive
        fmt.Printf("[plan-audit] verdict=INCONCLUSIVE, falling back to manual prompt\n")
        _ = c.Reporter.AppendRun(c.SpecID, result)
        return result, nil
    }

    // Step 4: route verdict
    switch verdict {
    case VerdictPass:
        result.Verdict = VerdictPass
        c.Cache.Store(c.SpecID, hash, result)
        fmt.Printf("[plan-audit] verdict=PASS, persisted to progress.md, proceeding to Phase 1\n")
    case VerdictFail:
        graceActive := c.isGraceWindowActive(now)
        result.GraceWindowActive = graceActive
        result.GraceWindowRemainingDays = c.graceWindowRemainingDays(now)
        if graceActive {
            result.Verdict = VerdictFailWarned
            fmt.Printf("[plan-audit] verdict=FAIL [grace-window], D-%d until auto-block\n", result.GraceWindowRemainingDays)
        } else {
            result.Verdict = VerdictFail
            fmt.Printf("[plan-audit] verdict=FAIL, blocking Run phase, report=%s\n", reportPath)
        }
    case VerdictInconclusive:
        result.Verdict = VerdictInconclusive
    default:
        result.Verdict = VerdictInconclusive
    }

    // Step 5: persist to daily report.
    _ = c.Reporter.AppendRun(c.SpecID, result)
    return result, nil
}
```
**为什么核心**: 每次 `/moai run SPEC-XXX` 都要走这 5 步审计。bypass path + 24h cache + 7-day grace window + 5 verdict 路由 — 是 plan-auditor 与 orchestrator 的契约。

### 6. **RegisterSession** — 多会话协调原语 (`internal/session/registry.go:161-192`)

```go
// Register atomically appends a new entry with started_at and
// last_heartbeat set to the current time. If an entry with the same
// sessionID already exists, it is updated in place (idempotent on
// session_id collision per §F.5 mitigation).
func (r *Registry) Register(sessionID, specID, phase string) error {
    if sessionID == "" {
        return errors.New("session registry: sessionID cannot be empty")
    }
    host, _ := os.Hostname()
    cwd, _ := os.Getwd()
    now := r.clock.Now().UTC()
    return r.withLock(func(entries []Entry) ([]Entry, error) {
        // Idempotent: update in place if sessionID exists; else append.
        for i := range entries {
            if entries[i].SessionID == sessionID {
                entries[i].SpecID = specID
                entries[i].Phase = phase
                entries[i].LastHeartbeat = now
                // Preserve original StartedAt + PID + Host (PID may differ
                // across reconnects but we treat first-seen as canonical).
                return entries, nil
            }
        }
        entries = append(entries, Entry{
            SessionID:     sessionID,
            SpecID:        specID,
            Phase:         phase,
            StartedAt:     now,
            LastHeartbeat: now,
            PID:           os.Getpid(),
            Host:          host,
            CWD:           cwd,
        })
        return entries, nil
    })
}
```
**为什么核心**: SPEC-V3R6-MULTI-SESSION-COORD-001 L1 原语。advisory lock + idempotent + 原子写 + 跨进程 host/PID/CWD 记录 — 让多个 Claude Code session 在同一项目上协作不冲突。

### 7. **RalphEngine.Decide** — 决策引擎 (`internal/ralph/engine.go:34-90`)

```go
// Decide evaluates the current loop state and feedback to produce a Decision.
// Decision priority (highest to lowest):
//  1. Max iterations reached -> abort
//  2. Perfect quality gate -> converge
//  3. Stagnation detected (auto_converge) -> converge
//  4. Human review requested (human_review) -> request_review
//  5. Default -> continue
func (e *RalphEngine) Decide(_ context.Context, state *loop.LoopState, feedback *loop.Feedback) (*loop.Decision, error) {
    if state == nil { return nil, fmt.Errorf("ralph: cannot decide on nil state") }
    if feedback == nil { return nil, fmt.Errorf("ralph: cannot decide on nil feedback") }

    // 1. Max iterations check.
    if state.Iteration >= state.MaxIter {
        return &loop.Decision{Action: loop.ActionAbort, Converged: false, Reason: "max iterations reached"}, nil
    }

    // 2. Perfect success: all quality gates satisfied.
    if loop.MeetsQualityGate(feedback) {
        return &loop.Decision{Action: loop.ActionConverge, NextPhase: loop.PhaseAnalyze, Converged: true, Reason: "quality gate satisfied"}, nil
    }

    // 3. Stagnation detection (auto-converge enabled).
    if e.cfg.AutoConverge {
        prev := loop.FindPreviousReviewFeedback(state.Feedback, state.Iteration)
        if prev != nil && loop.IsStagnant(prev, feedback) {
            return &loop.Decision{Action: loop.ActionConverge, Converged: true, Reason: "no improvement detected (stagnant)"}, nil
        }
    }

    // 4. Human review breakpoint.
    if e.cfg.HumanReview && state.Phase == loop.PhaseReview {
        return &loop.Decision{Action: loop.ActionRequestReview, NextPhase: loop.PhaseAnalyze, Converged: false, Reason: "human review requested"}, nil
    }

    // 5. Default: continue to next iteration.
    return &loop.Decision{Action: loop.ActionContinue, NextPhase: loop.PhaseAnalyze, Converged: false, Reason: "continuing to next iteration"}, nil
}
```
**为什么核心**: 把 5 步决策拍平成 5 行代码,但每个分支都是 MoAI 自迭代的"出口":abort(放弃) / converge(达成) / request_review(求助人类) / continue(继续)。`AutoConverge` 和 `HumanReview` 是 Ralph 引擎的两大可调参数。

### 8. **GoFeedbackGenerator.Collect** — 反馈收集器 (`internal/loop/go_feedback.go:65-135`)

```go
// Collect runs go test and go vet, parsing results into a Feedback struct.
// The context controls timeout — callers should set appropriate deadlines.
func (g *GoFeedbackGenerator) Collect(ctx context.Context) (*Feedback, error) {
    start := time.Now()
    fb := &Feedback{Phase: PhaseTest, BuildSuccess: true}

    // Run go test with JSON output and coverage.
    coverFile := filepath.Join(g.projectRoot, ".moai", "state", "loop", "coverage.out")
    testCmd := exec.CommandContext(ctx, "go", "test", "-count=1", "-json", "-coverprofile="+coverFile, "./...")
    testCmd.Dir = g.projectRoot

    var testOut bytes.Buffer
    testCmd.Stdout = &testOut
    testCmd.Stderr = &bytes.Buffer{}

    testErr := testCmd.Run()
    if testErr != nil {
        fb.BuildSuccess = false
    }

    // Parse test JSON output for pass/fail counts.
    passed, failed := measure.ParseGoTestJSON(testOut.Bytes())
    fb.TestsPassed = passed
    fb.TestsFailed = failed
    if failed == 0 && passed > 0 {
        fb.BuildSuccess = true
    }

    // Parse coverage from the profile file.
    fb.Coverage = measure.ParseCoverageFile(coverFile)

    // Run go vet for lint errors.
    vetCmd := exec.CommandContext(ctx, "go", "vet", "./...")
    vetCmd.Dir = g.projectRoot
    var vetStderr bytes.Buffer
    vetCmd.Stdout = &bytes.Buffer{}
    vetCmd.Stderr = &vetStderr
    _ = vetCmd.Run()
    fb.LintErrors = measure.CountNonEmptyLines(vetStderr.Bytes())

    fb.Duration = time.Since(start)

    // GOPLS-BRIDGE-001: when bridge is non-nil, collect gopls.Diagnostic.
    if g.bridge != nil {
        diags, err := g.bridge.GetDiagnostics(ctx, g.projectRoot)
        if err != nil { slog.Warn("gopls diagnostic collection failed, skipping", "error", err) }
        else { fb.Diagnostics = diags }
    }

    // REQ-LL-002: when aggregator is non-nil, collect lsp.Diagnostic into LSPDiagnostics.
    if g.aggregator != nil {
        lspDiags, err := g.aggregator.GetDiagnostics(ctx, g.projectRoot)
        if err != nil { slog.Warn("aggregator diagnostic collection failed, skipping", "error", err) }
        else { fb.LSPDiagnostics = filterGoOnlyDiagnostics(lspDiags) }
    }
    return fb, nil
}
```
**为什么核心**: 拉起 `go test -json` + `go vet` + LSP 诊断,组合成 1 个 Feedback 数据结构 — 这是 Ralph 循环每轮迭代的"测试数据源"。3 通道集成(legacy gopls.Bridge / new lsp.Aggregator / native go toolchain)的兼容性示例。

### 9. **FileSessionStore.Checkpoint** — 阶段状态持久化 (`internal/session/store.go:82-133`)

```go
// Checkpoint persists the phase state to disk with atomic write.
// SPEC-V3R2-RT-004 REQ-040: prevents concurrent writes via advisory lock (3-retry / 10ms-backoff).
func (fs *FileSessionStore) Checkpoint(state PhaseState) error {
    if err := fs.ensureStateDir(); err != nil {
        return fmt.Errorf("create state dir: %w", err)
    }

    // Inline BlockerRpt check
    if state.BlockerRpt != nil && !state.BlockerRpt.Resolved {
        return ErrBlockerOutstanding
    }

    // SPEC-V3R2-RT-004 AC-04: scan blocker files on disk (keyed by phase+specID)
    if err := fs.checkBlockerFiles(state.Phase, state.SPECID); err != nil {
        return err
    }

    // SPEC-V3R2-RT-004 REQ-004: Validate checkpoint before write
    if state.Checkpoint != nil {
        if err := state.Checkpoint.Validate(); err != nil {
            return fmt.Errorf("%w: %v", ErrCheckpointInvalid, err)
        }
    }

    data, err := json.MarshalIndent(state, "", "  ")
    if err != nil { return fmt.Errorf("marshal state: %w", err) }

    filename := fs.checkpointPath(state.Phase, state.SPECID)
    tmpFile := filename + ".tmp"

    // SPEC-V3R2-RT-004 REQ-040: Acquire advisory lock (3-retry / 10ms-backoff)
    lock := newFileLock()
    if err := acquireWithRetry(lock, filename, 3, 10*time.Millisecond); err != nil {
        return fmt.Errorf("acquire lock: %w", err)
    }
    defer func() { _ = lock.release() }() // Ignore lock release failures (checkpoint already written)

    // Write to temporary file
    if err := os.WriteFile(tmpFile, data, 0644); err != nil {
        return fmt.Errorf("write tmp file: %w", err)
    }

    // Atomic rename
    if err := os.Rename(tmpFile, filename); err != nil {
        _ = os.Remove(tmpFile) // Clean up on failure
        return fmt.Errorf("atomic rename: %w", err)
    }
    return nil
}
```
**为什么核心**: 每次 plan → run → sync 阶段切换都调一次。blocker 校验 + checkpoint 校验 + 文件锁 + temp+rename 原子写 — 是 SPEC 工作流的"账本"。

### 10. **router.Route** — Harness 路由决策 (`internal/harness/router/router.go:92-128`)

```go
// Route determines the harness level based on a SPECInput and HarnessConfig.
// Priority order (REQ-HRN-001-003/007/008/015):
//  1. SPEC frontmatter harness_level: override (REQ-015) — highest priority.
//  2. force-thorough override (REQ-008) — security/payment keywords, critical priority.
//  3. Escalation (REQ-009) — accumulative (the router only determines the initial value).
//  4. auto_detection rules (REQ-007) — minimal -> standard -> thorough.
//  5. mode_defaults (REQ-014) — lowest-priority fallback.
func (r *defaultRouter) Route(doc *SPECInput, cfg *config.HarnessConfig) (Level, Rationale, error) {
    signals := ExtractSignals(doc)
    rationale := Rationale{
        FileCount:    signals.FileCount,
        DomainCount:  signals.DomainCount,
        SpecType:     signals.SpecType,
        SpecPriority: doc.Priority,
        Keywords:     []string{},
    }

    // 1. SPEC frontmatter harness_level: override (REQ-HRN-001-015).
    if doc.HarnessLevel != "" {
        switch Level(doc.HarnessLevel) {
        case LevelMinimal, LevelStandard, LevelThorough:
            rationale.MatchedRule = "spec_override"
            return Level(doc.HarnessLevel), rationale, nil
        }
    }

    // 2. force-thorough override (REQ-HRN-001-008).
    forcedKeywords := matchForceThoroughKeywords(doc)
    isCritical := isCriticalPriority(doc.Priority)
    isSensitiveDomain := isSensitiveTagDomain(doc.Tags)

    if len(forcedKeywords) > 0 || isCritical || isSensitiveDomain {
        rationale.MatchedRule = "force_thorough"
        rationale.Keywords = forcedKeywords
        return LevelThorough, rationale, nil
    }

    // 3. auto_detection rules (REQ-HRN-001-007): order is minimal -> standard -> thorough.
    level, rule := applyAutoDetectionRules(signals, cfg)
    rationale.MatchedRule = rule
    return level, rationale, nil
}
```
**为什么核心**: Harness v4 编排的"入口决策"。`minimal / standard / thorough` 3 档决策,影响后续的 audit gate 严苛度、sync-auditor 评分维度、Manager 的 effort 配置 — 单一函数决定整个 SPEC 的工作流深度。

---

## 六、集成点 (Integration Points)

### 6.1 与 Claude Code 的集成

| 集成点 | 机制 | 位置 |
|--------|------|------|
| **Hook 事件** | 27 个事件通过 stdin/stdout JSON 协议 | `internal/hook/protocol.go` |
| **Agent 调度** | Sub-agent 通过 `Agent(agent_type=X)` 调用,Manager 通过工具注册 | `agents/moai/*.md` frontmatter `tools:` |
| **Memory** | Project memory + User memory 双模式 | `agents/moai/*.md` `memory:` 字段 |
| **Permission** | `permissionMode: bypassPermissions` / `acceptEdits` / `plan` / `bubble` | `agents/moai/*.md` + `internal/permission/stack.go` |
| **Isolation** | `isolation: worktree` 自动创建 worktree | `agents/moai/manager-develop.md:14` |
| **Skills** | 27 个 skill 在 `skills:` 字段声明,自动加载 | `agents/moai/*.md` + `.claude/skills/` |
| **MCP** | WebSearch / WebFetch / Context7 / DesignSync | `agents/moai/*.md` `mcp__*` 工具 |
| **Status Line** | `moai statusline` 注入状态栏 | `internal/cli/statusline.go` |
| **Slash Commands** | `.claude/commands/moai/{plan,run,sync,loop,fix,mx,review,...}.md` | `internal/cli/root.go` |

### 6.2 与 Git/GitHub 的集成

| 集成点 | 机制 |
|--------|------|
| **Git Worktree 隔离** | `internal/git/WorktreeManager` 接口 + `internal/worktree/state_guard.go` |
| **GitHub PR** | `internal/github/gh.go` + `gh pr create/merge/review` |
| **GitHub Issues** | `internal/github/issue_parser.go` + `issue_closer.go` |
| **Conventional Commits** | `<type>(<scope>): <subject>` 规范 |
| **Annotated Tags** | `moai_cp/YYYYMMDD_HHMMSS` 检查点标签 |
| **Submodule/Branch Strategy** | `git-strategy.yaml` 配置 + Tier S/M/L 路由 |

### 6.3 与 LSP/IDE 的集成

| 集成点 | 机制 |
|--------|------|
| **gopls** | `internal/lsp/gopls/` 子包,LSP 3.17 协议 |
| **ast-grep (sg)** | 外部 `sg` CLI + JSON 解析,16+ 语言 |
| **multi-LSP aggregator** | `internal/lsp/aggregator/` 多 server 聚合 |
| **LSP cache** | `internal/lsp/cache/` 诊断缓存 |
| **Subprocess transport** | `internal/lsp/subprocess/` 进程管理 |
| **LSP hook** | `internal/lsp/hook/` 与 MoAI hook 桥接 |
| **LSP doctor** | `internal/cli/lsp_doctor.go` 状态检查 |

### 6.4 与外部工具的集成

| 集成 | 工具 |
|------|------|
| **AST 分析** | `sg` (ast-grep) |
| **Shell AST** | `mvdan.cc/sh/v3/syntax` (Go 库) |
| **多语言 AST** | `smacker/go-tree-sitter` |
| **TUI** | `bubbletea` + `huh` + `lipgloss` + `fang` |
| **Web 仪表板** | `templ` + `a-h/templ` (纯 Go) |
| **更新** | `MOAI_SKIP_PLAN_AUDIT` / `MOAI_TEMP_DIR` / `MOAI_AUDIT_GATE_T0` |
| **OAuth** | `oauth_token_preservation_test.go` |
| **Tmux** | `internal/tmux/` (CG 模式) |
| **GLM 面板** | `internal/cli/glm.go` |

### 6.5 项目脚手架 (`internal/template/`)

```
internal/template/templates/
├── .claude/
│   ├── agents/moai/          # 9 agent 模板
│   ├── commands/moai/        # 13 slash command
│   ├── hooks/moai/           # hook handlers
│   ├── output-styles/moai/   # 输出样式
│   ├── rules/moai/           # 5 类规则 (80+)
│   └── skills/moai/          # 27 skill (含 moai/ 核心)
├── .github/
│   ├── actions/              # 自定义 GitHub Action
│   └── workflows/            # 预制 CI
├── .git_hooks/               # pre-commit 等
├── .moai/
│   ├── branches/             # 分支策略
│   ├── config/               # 配置 (含 astgrep/evaluator-profiles/sections)
│   ├── decisions/            # 决策记录
│   ├── docs/                 # 文档
│   ├── evolution/            # 进化/学习
│   ├── learning/             # 学习笔记
│   ├── logs/                 # 日志
│   ├── project/              # 项目级文档
│   ├── reports/              # 报告
│   └── state/                # 状态 (gitignored)
```

### 6.6 配置文件 (`.moai/config/sections/`)

| 配置 | 作用 |
|------|------|
| `user.yaml` | 用户信息 |
| `language.yaml` | 多语言配置 |
| `quality.yaml` | 质量门 (coverage/DDD/TDD) |
| `harness.yaml` | Harness 等级 / evaluator_mode |
| `constitution.yaml` | 宪法 |
| `git-strategy.yaml` | Git 策略 (Tier S/M/L) |
| `design.yaml` | 设计系统 |
| `tool-policy.yaml` | 工具策略 (SSOT for permissions) |
| `evaluator-profiles/` | 评估器 profile (hierarchical scoring) |
| `astgrep/` | ast-grep 规则 |

### 6.7 SPEC 文档结构 (`.moai/specs/SPEC-{DOMAIN}-{NUM}/`)

```
SPEC-AUTH-001/
├── spec.md         # 业务需求 (EARS/GEARS)
├── plan.md         # 实施计划
├── acceptance.md   # 验收标准 (Given/When/Then)
├── design.md       # 架构设计 (可选)
├── research.md     # 研究报告 (可选)
└── tasks.md        # 任务列表 (可选)
```

### 6.8 Agent 通信机制 (Communication)

| 通信 | 机制 |
|------|------|
| **Parent → Subagent** | `Agent(agent_type="X", prompt="...")` 工具调用 |
| **Subagent → Parent** | 结构化返回 (`blocker report` / `decision report`) |
| **Subagent ↔ Subagent** | `SendMessage` 工具 |
| **Subagent → User** | `AskUserQuestion` 工具 (受 orchestration 调度) |
| **Hook → Claude Code** | `HookOutput{decision, reason, additionalContext, ...}` |
| **Orchestrator ← Subagent** | 阻塞报告模式 (per `agent-common-protocol.md`) |
| **Skill → Subagent** | 渐进式披露 (~100 / 5K / on-demand tokens) |
| **MX 注解** | 跨文件代码上下文 (NOTE/WARN/ANCHOR/REASON/TODO/SPEC) |
| **Routing Ledger** | `append-only routing-ledger.jsonl` (append `moai harness ledger record`) |
| **Outcome Ledger** | `apply_outcome` events 写入 `usage-log.jsonl` |

---

## 七、与其他多智能体框架的对比要点 (Context)

| 维度 | MoAI-ADK (Go) | LangGraph (Python) | AutoGen (Python) | CrewAI (Python) |
|------|----------------|-------------------|------------------|----------------|
| **语言** | Go (单二进制, 5ms 启动) | Python | Python | Python |
| **Agent 抽象** | Markdown + YAML frontmatter | Python class | Python class | Python class |
| **状态管理** | 文件 (`.moai/state/`) + JSON | Checkpointer | 内存 | 内存/SQLite |
| **循环模式** | Ralph 状态机 (4 阶段) | Graph + cycles | Group chat | Crew tasks |
| **Tool calling** | Claude Code hook 协议 | ToolNode | function calling | Tool 类 |
| **Memory** | progress.md + task-ledger | Store | ConversationHistory | Memory |
| **质量门** | 5 层宪法 + 4 维审计 + 24h cache | Custom | Custom | Custom |
| **多 session** | `.moai/state/active-sessions.json` | Threads | 进程级 | 进程级 |
| **Worktree 隔离** | 内建 | 外部 (subprocess) | 外部 | 外部 |
| **配置层级** | 8 tier (policy → builtin) | env | env | env |

---

## 八、关键观察 (Observations)

1. **Harness Engineering 范式**: MoAI-ADK 不直接写代码,而是设计"AI Agent 的执行环境"。所有 hook、permission、constitution、harness level 都是为了约束 + 赋能 agent,而非让 agent 自由发挥。

2. **Bilingual 注释/文档**: Go 代码注释、commit message、JSONL log 多为中英/韩/日混排,反映 modu-ai 团队的国际化开发背景 (韩国公司)。

3. **保守 + 渐进**: 大量 `@MX:ANCHOR` 标签标识关键代码,frozen zone (8 个 sentinel) 保护核心资产不被自动学习破坏 — 是"AI 改造自己"的安全设计。

4. **可观测性第一**: TraceWriter、routing-ledger、usage-log.jsonl、outcome capture 体系完整 — 便于后续学习 (Learner) + 毕业 (Graduation) + 进化 (Evolution)。

5. **SPEC 是一等公民**: 整个框架以 SPEC 为中心,plan → run → sync → close 全流程围绕 `.moai/specs/SPEC-XXX/` 目录,SPEC 校验、SPEC 漂移检测、SPEC 关闭、SPEC 归档独立成子系统。

6. **Worktree 是隔离基元**: manager-develop/manager-design `isolation: worktree` 直接对应 Claude Code 2.1.49+ 的 `WorktreeCreate`/`WorktreeRemove` hook,实现"agent per worktree"的安全并行。

7. **Learning 与 Safety 对抗**: Tier 4 promotion 才能提案修改 frozen 文件,5 层 constitution pipeline 强制走 canary + contradiction + rate limit + human oversight — 防止 AI 失控。

8. **6 模式编排目录 (Phase 0.95)**: `--team` / `--solo` / 默认 catalog,自动选择 sub-agent / agent-team / sequential 等 6 种编排模式 — 但实际上 Mode 3 (agent-team) 已 RETIRED,只走 Mode 5 (sub-agent) + Mode 1-2-4-6 默认。

9. **EARS → GEARS 迁移**: 历史 EARS 模式 6 个月向后兼容窗口 (2026-11-22 截止),新 SPEC 必须用 GEARS (`shall` 形式,5 种 pattern)。`lint_haiku_residual.go` 检测残留。

10. **`moai` vs `moai-adk`**: 仓库名是 `moai-adk`,但 CLI binary 是 `moai`,agent/skill prefix 是 `moai-*`,家族统一。

---

**分析完成时间**: 2026-07-13
**总耗时**: 全量阅读 ~100K 行 Go 代码 + 27 个 agent/skill markdown + 80+ 规则文件 + 70+ CLI 入口
**核心发现**: MoAI-ADK 是一个**面向 Claude Code 的运行时底座 + AI 编程方法论框架**, 通过 100+ 子模块、27 个 hook 事件、9 个 AI agent、27 个 skill、5 层安全闸门、8 层权限栈、4 维质量审计、4 阶段反馈循环,把"AI 写代码"这件事变成一个可观察、可回滚、可学习、可治理的工程系统。
