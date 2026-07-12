# 项目分析:07-moat-ops-auditor (Moat — AI 编码守门员 / 运维审计员)

## 项目概述

**Moat** 是一款 AI 编码守门员 / 运维审计工具,定位为"AI 编码护城河"——在 AI 生成代码前后对其进行**四层门禁检查**,拦截 SQL 注入、硬编码密钥、依赖项漏洞、竞态条件等常见 AI 代码问题。项目标语为"AI 编码守门员 — 零配置 + 实时拦截 + 处方化提示 + 精准拦截 + 性能飞跃 + 测试覆盖率优化 + Bug 修复"。

**版本**:v1.1.2 | **许可证**:Apache-2.0 | **定位**:"AI 编码护城河,防止 AI 改代码时搞坏系统"。

**核心价值定位**:与一般 lint 工具不同,Moat 关注三个差异化能力:
1. **架构守护**(实时 Gatekeeper,防止架构破坏)
2. **安全注入拦截**(10+ 类硬编码密钥、SQL 注入、依赖漏洞检测,零误报)
3. **极低性能开销**(< 0.2s/check,LRU 缓存优化 1.7x 加速)

**使用对象**:AI 开发者(Claude Code、Cursor、Codex、Copilot)与人类工程师协同工作流中的"刹车片"。

**技术栈**:
- 语言:Python 3.10+ (主)
- 辅助:TypeScript/JavaScript/Go/Bash(检查目标)
- AST:Python `ast` + `tree-sitter` (v0.20.0+)
- Web 看板:FastAPI + Uvicorn + HTML/CSS/JS
- 实时监控:`watchdog` (文件变化) + FastAPI
- 数据库:SQLite (One Memory 集成)
- HTTP 客户端:`httpx`
- 配置:JSON (`.moat/moat.json`、`.moat/gatekeeper_config.json`)
- 文档:MkDocs (Material 主题)
- 依赖管理:`pyproject.toml` + `uv.lock`

---

## 核心模块清单

### 1. CLI 入口与运行器
- `moat/cli.py` — 17 个子命令的主入口
- `moat/runner.py` — MoatResult 容器 + 插件化检查调度
- `moat/__main__.py` — `python -m moat` 入口

### 2. 核心检查系统 (L0-L4)
- `moat/checks/base.py` — Check 抽象基类 + CheckResult dataclass
- `moat/checks/quick_check.py` — 5+ 条常识规则(快速模式)
- `moat/checks/l1_import.py` — 启动链 + 语法检查
- `moat/checks/l1_files.py` — 文件完整性
- `moat/checks/l1_modules.py` — 核心模块实例化
- `moat/checks/l1_subsystems.py` — 子系统健康 + 内容哈希
- `moat/checks/l1_behavior.py` — 行为验证(测试/CI 存在性)
- `moat/checks/l1_api.py` — API 端点存活 + OpenAPI schema
- `moat/checks/l2_schema.py` — JSON 结构契约
- `moat/checks/l2_architecture.py` — 代码熵增 + 依赖枢纽
- `moat/checks/l3_correlation.py` — 跨模块关联(循环依赖)
- `moat/checks/l4_baseline.py` — 基线对比 + 文件哈希 + 熵增

### 3. 安全专项规则(SQL/密钥/依赖/导出)
- `moat/checks/secrets.py` — SECRETS-001:硬编码密钥检测(13 模式)
- `moat/checks/sql_injection.py` — SQL-001:T-SQL 注入 AST 检测
- `moat/checks/enhanced_sql_injection.py` — SQL-002:ORM 增强注入检测(Django/SQLAlchemy/asyncpg)
- `moat/checks/dependency_security.py` — DEPS-001:依赖项漏洞(pip-audit/npm audit/内置 DB)
- `moat/checks/unused_exports.py` — UNUSED-001:未使用导出(Python `__all__` + TS/Go)

### 4. 优化检查(Ponytail 集成)
- `moat/checks/optimization.py` — 复杂度、YAGNI、死代码、重复代码、标准库优先
- `moat/checks/fail_open.py` — `@fail_open` 装饰器(辅助工具不打扰)
- `moat/rules/karpathy_principles.py` — Karpathy 原则加载
- `moat/rules/surgical_changes.py` — 手术刀式修改检查(diff 限制)
- `moat/rules/simplicity_checker.py` — Simplicity First(复杂度/函数/类大小)

### 5. Gatekeeper 实时守门系统
- `moat/gatekeeper/checker.py` — 守门检查器(执行+豁免+审计)
- `moat/gatekeeper/cli.py` — `moat gatekeeper` 子命令
- `moat/gatekeeper/types.py` — 规则/配置/豁免类型(3 层豁免)
- `moat/gatekeeper/rules/__init__.py` — 4 类架构规则(目录责任/分层/命名/框架)
- `moat/gatekeeper/rules/test_coverage_gate.py` — 测试覆盖率门票(CRITICAL)

### 6. 多语言检查适配器
- `moat/checks/typescript/__init__.py` — TS 检查导出
- `moat/checks/typescript/syntax.py` — TS 语法检查
- `moat/checks/typescript/dedup.py` — 去重逻辑
- `moat/checks/typescript/race_condition.py` — 竞态条件
- `moat/checks/typescript/timing_doc.py` — 时序文档
- `moat/checks/typescript/error_handling.py` — 错误处理
- `moat/checks/typescript/null_safety.py` — null 安全
- `moat/checks/typescript/any_type.py` — any 类型滥用
- `moat/checks/typescript/async_race.py` — 异步竞态
- `moat/checks/typescript/semantic.py` — CodeGraph 语义分析
- `moat/checks/typescript/perf_pattern.py` — 性能模式
- `moat/checks/typescript/export_check.py` — 导出检查
- `moat/checks/go/__init__.py` — Go 检查
- `moat/checks/go/concurrency_safety.py` — 并发安全
- `moat/checks/go/error_handling.py` — 错误处理
- `moat/checks/go/goroutine_leak.py` — Goroutine 泄漏

### 7. 验收算子系统(7 个 Operator)
- `moat/verification/operator.py` — VerificationOperator ABC
- `moat/verification/orchestrator.py` — VerifyOrchestrator(组合+评分)
- `moat/verification/types.py` — Violation/OperatorResult/VerificationContext/VerificationReport
- `moat/verification/verify_cli.py` — `moat verify` CLI
- `moat/verification/operators/directory_responsibility.py` — 目录责任验收
- `moat/verification/operators/minimal_module_drill.py` — 最小模块钻取
- `moat/verification/operators/api_response_spec.py` — API 响应规格
- `moat/verification/operators/framework_usage.py` — 框架使用
- `moat/verification/operators/runtime_evidence.py` — 运行证据包
- `moat/verification/operators/architecture_health_score.py` — 架构健康评分
- `moat/verification/operators/truth_document.py` — 真实文档生成

### 8. Sidecar 守护进程
- `moat/sidecar/daemon.py` — SidecarDaemon(PID/日志/前后台)
- `moat/sidecar/watcher.py` — FileChangeHandler(watchdog)
- `moat/sidecar/api.py` — FastAPI REST 接口
- `moat/dashboard/server.py` — Web 看板(FastAPI+前端)

### 9. AST 与影响域分析
- `moat/ast/builder.py` — ProjectSkeleton(函数调用图)
- `moat/ast/diff.py` — ASTDiffer(增量对比)
- `moat/ast/diff_enhanced.py` — 增强 diff
- `moat/ast/tree_sitter.py` — Tree-sitter 包装
- `moat/ast/__init__.py`

### 10. 痛觉评分与 AI 修复
- `moat/pain/scorer.py` — PainScorer(0-100 加权评分)
- `moat/pain/feedback.py` — 痛觉反馈
- `moat/fixer.py` — FixEngine(AI 修复建议)
- `moat/fix_strategies.py` — 修复策略库

### 11. 进化指标与记忆系统
- `moat/evolution.py` — EvolutionEngine(从 Insight 生成规则)
- `moat/evolution_metrics.py` — EvolutionTracker + Evaluator
- `moat/evolution_cli.py` — `moat evolution` CLI
- `moat/memory/bridge.py` — SharedStorageBridge(SQLite 与 One Memory 通信)
- `moat/memory/filter.py` — 记忆写入过滤器
- `moat/memory/sync.py` — 同步状态

### 12. 监控与基线
- `moat/monitor.py` — 实时日志监控(tail -f + 着色)
- `moat/baseline.py` — BaselineManager(基线保存/对比/回滚)
- `moat/discovery.py` — 项目自动发现 + Claude Code Hook 生成
- `moat/contract.py` — CONTRACT.md 生成
- `moat/cache.py` — HashCacheManager(并行扫描)
- `moat/cache_enhanced.py` — 增强缓存
- `moat/config_enhanced.py` — 多源配置
- `moat/report.py` — ReportGenerator(text/md/json)
- `moat/report_enhanced.py` — 增强报告
- `moat/architecture_report.py` — ArchitectureReportGenerator

### 13. AI 测试与免疫系统
- `moat/ai_test/cli.py` — AI 测试生成
- `moat/immune/cli.py` — Moat Immune(测试免疫系统)
- `moat/immune/unit/generator.py` — AITestGateway(单元测试生成)

### 14. 适配器
- `moat/adapters/__init__.py` — Claude/Cursor/pre-commit 适配器

### 15. VS Code 插件
- `vscode-moat/src/extension.ts` — TS 扩展
- `vscode-moat/package.json` — 插件元数据

---

## 详细能力列表

### A. API 能力 (CLI 子命令 17 个)

| 命令 | 模式 | 核心能力 |
|------|------|---------|
| `moat check` | 快速 (默认) | 只检查 git diff 修改的文件,5+ 条常识规则,<5s |
| `moat check --full` | 完整 | 所有文件 + L1/L2/L3/L4 复杂规则 + 架构熵增 |
| `moat check --diff` | 增量 | AST 对比 + 影响域分析 + Pain Score,<10s |
| `moat check --quick` | 快速 | 同默认,显式指定 |
| `moat check --legacy` | 旧版 | 向后兼容的旧 L1 检查 |
| `moat check --skip-architecture` | 跳过 L2 | CI 加速 |
| `moat check --optimize` | 启用优化 | YAGNI/复杂度/标准库优先 |
| `moat watch -l log --filter PATTERN` | 实时监控 | tail -f + 错误着色 + 统计 |
| `moat init [--no-interactive]` | 初始化 | 零配置 + 自动检测 + 基线保存 |
| `moat report [--format text/md/json] [--copy]` | 报告 | text/md/json 格式 + 剪贴板 |
| `moat architecture [--format text/md/json]` | 架构 | 健康评分 0-100 + 熵增 + 依赖枢纽 |
| `moat baseline save/show/diff` | 基线 | JSON 基线管理 |
| `moat dashboard [--port 9876]` | Web 看板 | 实时错误 + 状态 + 基线管理 |
| `moat fix [--no-dry-run] [--format ...] [--copy]` | AI 修复 | 策略库匹配 + 置信度 + 自动修复 |
| `moat sidecar start/stop/restart/status [--foreground]` | 守护 | 实时文件监控 + REST API |
| `moat adapter claude/precommit/all` | 适配器 | CLAUDE.md / pre-commit hook |
| `moat rules explain <RULE_ID>` | 规则解释 | 规则的 why/fix/disable |
| `moat rules list` | 规则列表 | 所有规则的 ID/严重性 |
| `moat verify [--all] [--operator NAME] [--json] [--fail-on-score N]` | 验收 | 7 个算子 + 评分 |
| `moat gatekeeper start/stop/status/check/rules` | 守门 | 实时架构守门 |
| `moat evolution report/adjust/record` | 进化 | 指标 + 神经衰弱检测 |
| `moat immune unit/contract/bdd/visual/pipeline` | 免疫 | AI 测试体系 |
| `moat test generate/checkpoint/run` | 测试 | 旧版 AI 测试 |

### B. 数据模型 (12 个核心数据结构)

| 类型 | 模块 | 字段 |
|------|------|------|
| `CheckResult` | checks/base.py | type, message, file, line, level, metadata |
| `RuleViolation` | gatekeeper/types.py | rule_id, rule_name, message, severity, file_path, line, suggestion, context |
| `GatekeeperResult` | gatekeeper/types.py | file_path, passed, violations, ignored_violations, execution_time, should_block |
| `GatekeeperConfig` | gatekeeper/types.py | ignore_rules, audit_log_path, block_on_critical/error/warning, max_file_size, timeout |
| `Severity` (Enum) | verification/types.py | INFO / WARNING / ERROR / CRITICAL |
| `RuleSeverity` (Enum) | gatekeeper/types.py | INFO / WARNING / ERROR / CRITICAL |
| `Violation` | verification/types.py | rule, message, severity, file_path, line, suggestion, evidence |
| `OperatorResult` | verification/types.py | operator_name, passed, evidence, violations, suggestions, execution_time |
| `VerificationContext` | verification/types.py | project_path, config, timestamp |
| `VerificationReport` | verification/types.py | project_path, operators, overall_score, passed, timestamp |
| `MoatResult` | runner.py | passed, failed, skipped, warnings, start/end_time, errors |
| `PainScore` | pain/scorer.py | score(0-100), level, error, context |
| `EvolutionMetric` | evolution_metrics.py | id, type, value, weight, timestamp, context, is_positive |
| `EvolvedRule` | evolution.py | id, type, module, pattern, confidence, source_insight_id, applied |
| `MoatResult.add_check_result` | runner.py | 自动按 type 分类(pass/fail/warn/skip) |
| `IgnoreMechanism` | gatekeeper/types.py | 三层豁免:行内/文件/配置 |

### C. 算法能力 (4 大算法引擎)

#### 1. 静态分析算法
- **AST 解析**(`ast` 模块):函数/类提取、调用图构建、import 关系图
- **Tree-sitter 多语言**:`tree_sitter_python` 用于精准定位 SQL 注入,BinaryExpression 检测字符串拼接
- **正则匹配**:13 类密钥模式、5 类 SQL 字符串格式、ORM 特定模式
- **AST diff**:`_has_substantial_change()` 比较 `ast.dump()` 输出,精确识别函数增删改
- **循环依赖检测**:DFS 算法 + 路径回溯,输出完整循环链
- **依赖枢纽识别**(`_count_imports`):AST 遍历 import 语句,统计被引用次数,top 10 + threshold ≥ 5
- **代码熵增检测**:`(curr - base) / base * 100` 公式,>100% 红,>50% 黄

#### 2. 复杂度算法
- **圈复杂度(McCabe)**:`_calculate_cyclomatic_complexity()` 统计 if/while/for/except/BoolOp/IfExp
- **认知复杂度(SonarSource)**:`_calculate_cognitive_complexity()` 递归遍历,if+1,循环+2,嵌套+1,递归+3
- **行数统计**:函数长度 >50 行触发警告
- **类方法数**:>15 个方法触发警告
- **死代码检测**:`DeadCodeDetector` AST Visitor 检查 return/raise 后的 Expr 节点
- **重复代码检测**:滑动窗口 5 行,字符串 hash 匹配,默认关闭(性能考虑)
- **基线文件计数对比**:`prev_count * 0.9 < curr_count < prev_count * 1.3`

#### 3. 评分算法
- **Pain Score**(`PainScorer.calculate`):
  - 权重配置:core_business=30, auth_payment=40, api_endpoint=20, race_condition=25, syntax_error=15, missing_doc=5, third_party=-50
  - 关键词匹配:auth/login/token/payment/user/security → 核心业务
  - 阈值映射:≥75=CRITICAL, ≥50=HIGH, ≥25=MEDIUM, else=LOW
  - 总分计算:算术平均,整体等级取 max
  - 增强版:`EnhancedPainScorer` 加载进化规则,动态调整 multiplier (1.0 + confidence * 0.5)

- **架构健康评分**(`_calculate_health_score`):
  - 基础分 100
  - high_entropy -20/个
  - medium_entropy -10/个
  - dependency_hub -5/个
  - file_change -2/个
  - 范围 [0, 100]

- **验收算子评分**(`_calculate_overall_score`):
  - CRITICAL 违规 -20 分/个
  - ERROR -10 分/个
  - WARNING -5 分/个
  - INFO -1 分/个
  - 范围 [0, 100]

- **进化指标评估**(`evaluate_evolution`):
  - 5 维度权重:refactor_success 0.25, performance 0.20, bug_fix_time 0.20, dev_velocity 0.20, false_positive_rate -0.15
  - 神经衰弱检测:负向维度比 ≥0.5 critical, ≥0.3 warning, ≤0.15 encourage
  - 鼓励模式:正向占比 > 70%

#### 4. 影响域算法
- **置信度权重**(`_detect_call_confidence`):直接调用=1.0, 属性调用=0.9, 索引调用=0.3, 其他=0.7
- **风险等级计算**:`direct_callers ≥ 5` 或 `confidence_weight ≥ 4.0` = high, `≥ 2` 或 `total ≥ 5` = medium, else low
- **直接/间接调用者分类**:`confidence >= 0.8` 阈值
- **callers 排序**:按 `confidence_weight` 降序

### D. UI 能力 (3 个前端模块)

| 模块 | 端口 | 功能 |
|------|------|------|
| Web Dashboard | 9876 | 实时错误列表 + 状态 + 基线管理 + 5s 自动刷新 |
| Sidecar API | 9877 | REST 接口供 VS Code 插件调用 |
| VS Code 扩展 | N/A | TS 扩展,可调用 `moat` 命令 |
| 终端输出 | stdout | 颜色编码(ANSI)+ emoji + 分级输出 |
| HTML/CSS/JS | 嵌入式 | 4 卡片布局(错误/警告/项目/基线) + 错误列表 |
| 颜色映射 | 全终端 | ERROR=红, WARNING=黄, INFO=蓝, OK=绿 |
| 进度显示 | CLI | `[1/7] 执行: directory_responsibility...` |

### E. 集成能力 (8 类集成)

| 集成目标 | 实现方式 |
|---------|---------|
| Claude Code | 生成 `CLAUDE.md` + `.claude/settings.json` (PreToolUse/PostToolUse Hooks) |
| Cursor | 生成 `.cursor/rules.mdc` (mdc 格式规则) |
| Pre-commit | 写入 `.git/hooks/pre-commit` (chmod 0o755) |
| Git | 调用 `git diff --cached/--name-only`, 解析 `git show HEAD:<file>` |
| pip-audit | subprocess 调 `pip-audit --desc --fix`, 解析 JSON 输出 |
| npm audit | subprocess 调 `npm audit --json`, 解析 vulnerabilities |
| One Memory (TS) | SQLite 共享 `.moat/memory.db` (WAL 模式) |
| FastAPI/uvicorn | 可选 `[dashboard]`/`[sidecar]` extra |
| watchdog | 可选 `[sidecar]` extra, 文件事件防抖 2s |
| httpx | 探测 API 端点存活 |
| Tree-sitter | 必装 `tree-sitter>=0.20.0`, Python grammar 可选 |
| MkDocs | 文档站生成(独立 site/) |
| GitHub Actions | 简易集成 `pip install moat && moat check` |
| VS Code | tasks.json + keybindings.json + extension.ts 插件 |
| clipboard | macOS pbcopy / pyperclip (需 `[vscode]` extra) |

### F. 工具能力

| 工具类 | 文件 | 描述 |
|--------|------|------|
| `@fail_open` 装饰器 | checks/fail_open.py | 异常时返回默认值,不阻断流程(WARNING/DEBUG 级别) |
| `@fail_open_safe` | checks/fail_open.py | 完全静默版 |
| `HashCacheManager` | cache.py | SHA256 缓存 + mtime 失效 + 并行 ThreadPoolExecutor |
| `BaselineManager` | baseline.py | save/show/diff + 误报率统计 + 架构基线 + 回滚 |
| `ReportGenerator` | report.py | text/md/json + AI 建议 + 影响分析 + 技术债务分类 |
| `ArchitectureReportGenerator` | architecture_report.py | 架构健康报告 + 健康评分 + 改进建议 |
| `FixEngine` | fixer.py | 错误修复 + 策略库匹配 + 置信度评分 + PR 描述生成 |
| `EvolutionEngine` | evolution.py | 从 One Memory Insight 生成 Moat 规则 |
| `EvolutionTracker` | evolution_metrics.py | 5 类指标记录(refactor/perf/bugfix/fp/velocity) |
| `EvolutionEvaluator` | evolution_metrics.py | 神经衰弱检测 + 策略建议 |
| `SharedStorageBridge` | memory/bridge.py | SQLite 桥接(9 张表:WAL+busy_timeout) |
| `SharedStorageBridge` 表 | memory/bridge.py | bug_memories/fix_history/weak_points/insights/fix_patterns/sync_status/dream_triggers/smart_hints/contract_baselines/api_contracts |
| `TestCoverageGateRule` | gatekeeper/rules/test_coverage_gate.py | 测试文件存在性 + 覆盖率 ≥ 80%/85% + AI 触发 |
| `IgnoreMechanism` | gatekeeper/types.py | 三层豁免(行内 `# moat-ignore: <id>` / 文件头 / `.moat/gatekeeper_config.json`) |

### G. 安全能力 (重点关注)

#### 1. SECRETS-001 硬编码密钥检测 (`checks/secrets.py`, 13 模式)
| 模式名 | 正则 | 严重性 | 说明 |
|-------|------|-------|------|
| aws_access_key | `AKIA[0-9A-Z]{16}` | CRITICAL | AWS Access Key ID |
| github_token | `ghp_[0-9a-zA-Z]{36}` | CRITICAL | GitHub PAT |
| github_oauth | `gho_[0-9a-zA-Z]{36}` | CRITICAL | GitHub OAuth |
| slack_token | `xox[baprs]-[0-9a-zA-Z]{10,48}` | CRITICAL | Slack |
| google_api_key | `AIza[0-9A-Za-z_-]{20,}` | CRITICAL | Google API |
| generic_api_key | `api[_-]key["\s:=]+["\x27]([a-zA-Z0-9_-]{16,})["\x27]` | HIGH | 通用 API Key |
| password_assignment | `(?:password\|passwd\|pwd)\s*[:=]\s*["\']([^"\']{8,})["\']` | CRITICAL | 硬编码密码 |
| secret_assignment | `(?:secret\|api_secret\|app_secret)\s*[:=]\s*["\']([^"\']{8,})["\']` | CRITICAL | 硬编码 Secret |
| private_key_rsa | `-----BEGIN RSA PRIVATE KEY-----` | CRITICAL | RSA 私钥 |
| private_key_ecdsa | `-----BEGIN EC PRIVATE KEY-----` | CRITICAL | ECDSA 私钥 |
| private_key_pkcs8 | `-----BEGIN PRIVATE KEY-----` | CRITICAL | PKCS#8 |
| jwt_token | `[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}` | HIGH | JWT(可能) |

**误报抑制机制**:
- 跳过注释行(`#`、`//`、`/*`、`*`)
- 12 类占位符模式(`YOUR_`、`_HERE`、`<[^>]+>`、`{{}}`、`CHANGE_ME`、`xxx`/`yyy`/`zzz` 等)
- 12 类环境变量读取模式(`os.getenv`、`os.environ.get`、`process.env.`、`ENV[` 等)
- 上下文回溯:检查前一行的环境变量读取
- 跳过测试文件(`test_*` / `*_test.py` / `tests/`)
- 跳过示例/演示/测试文件(`example`/`demo`/`sample`/`fixture`/`mock`)
- 跳过 `.env.*` 文件

#### 2. SQL-001 / SQL-002 SQL 注入检测
**SQL-001**(`checks/sql_injection.py`):
- Tree-sitter AST 解析 + 正则后备
- 6 类执行点:`.execute(`、`.executemany(`、`.raw(`、`.query(`、`db.execute(`、`cursor.execute(`
- 6 类插值模式:f-string、.format()、%s、%d、字符串拼接
- 上下文回溯:检查前 5 行 + 当前行
- `@fail_open` 包裹,Tree-sitter 解析失败降级正则

**SQL-002 增强版**(`checks/enhanced_sql_injection.py`):
- 增加 SQLAlchemy:`session.execute(`、`engine.execute(`、`text(`
- 增加 Django ORM:`objects.raw(`、`objects.filter(`、`objects.get(`
- 增加异步驱动:`conn.execute(`、`connection.execute(`
- 10 类 ORM 特定危险模式(如 `\.raw\(f["\']`)

#### 3. DEPS-001 依赖项安全漏洞 (`checks/dependency_security.py`)
**4 优先级检测策略**:
1. **pip-audit** (Python) — 解析输出
2. **npm audit** (Node) — 解析 JSON
3. **本地缓存**(`vulnerability_db` 字典)
4. **静态漏洞检查**(内置 17 个已知漏洞)

**内置漏洞数据库**:
- Python:`requests<=2.25.0` (CVE-2021-33503), `<=2.19.0` (CVE-2018-18074), `django<2.2.28` (CVE-2021-33203), `<3.2.0` (CVE-2021-33503), `flask<2.0.0` (CVE-2019-1010083), `pillow<8.3.0` (CVE-2022-30515), `urllib3<1.26.5`, `jinja2<2.11.3` (CVE-2020-28493), `pyyaml<5.4` (CVE-2020-14343), `cryptography<3.0` (CVE-2020-36242)
- Node:`axios<0.21.0` (CVE-2021-3749), `lodash<4.17.21` (CVE-2021-23337), `express<4.17.1` (CVE-2022-24999), `minimist<1.2.5` (CVE-2020-7598)

**支持文件**:
- Python:`requirements.txt`、`pyproject.toml`(tomllib/tomli/正则降级)、`Pipfile`、`poetry.lock`
- Node:`package.json`、`package-lock.json`、`yarn.lock`
- Go:`go.mod`、`go.sum`

**版本比较**:`packaging.version` 解析,支持 `<=`、`<`、`>=`、`>`、`==` 操作符

#### 4. UNUSED-001 未使用导出检测 (`checks/unused_exports.py`)
- Python:AST 提取 `__all__` 列表 + 函数/类/变量定义 + `ast.walk(Name/Attribute)`,比较
- TypeScript:正则匹配 `export (default )?(function|const|class|interface|type) X`,统计 `usage_count`
- Go:正则 `^func ([A-Z]\w*)\(` + `^type ([A-Z]\w*)`,统计使用
- 严重性:LOW (warn)

#### 5. 鉴权检测(API-002,内置于 `quick_check.py`)
**多框架支持**:
- Python:Flask(`@app.route`)、FastAPI(`@router.`)、Django REST(`@api_view`、`@action`)
- TypeScript:Express.js(`app.get`/`router.get`)
- Go:Gin(`r.GET`)、Fiber(`app.Get`)

**鉴权关键词检查**(13 类):`login_required`、`authenticate`、`authorize`、`permission`、`token`、`jwt`、`oauth`、`@current_user`、`@require_auth`、`Depends(`、`get_current_user`、`AuthenticationError`、`auth_required`、`requires_auth`

**逻辑**:检测到路由装饰器后,检查后续 10 行内是否包含鉴权关键词,缺失则 WARN(注:这是增强版,README 中标记为 API-002)

#### 6. Gatekeeper 三层豁免
- **层级 1(行内)**:在违规行上下 5 行内查找 `# moat-ignore: <rule_id>` 注释
- **层级 2(文件头)**:文件前 10 行内查找 `# moat-ignore: <rule_id>`
- **层级 3(配置)**:`.moat/gatekeeper_config.json` 中 `ignore_rules: {rule_id: [pattern1, pattern2]}`,支持 fnmatch 通配符

**拦截策略**:
- `block_on_critical=True` (CRITICAL 违规阻止)
- `block_on_error=True` (ERROR 违规阻止)
- `block_on_warning=False` (默认不阻止)
- 性能:`max_file_size=1MB`,`timeout=100ms`
- **审计日志**:`audit_log_path` (默认 NDJSON,逐行写入)

#### 7. 其他安全相关
- **SQLAlchemy/Django ORM 模式**:`.raw(f"...")`、`.filter(... %s ...)`、`text(f"...")` 全部 CRITICAL
- **私有密钥模式**:RSA/ECDSA/PKCS#8 全部 CRITICAL
- **JWT 检测**:三段式 base64 模式
- **硬编码 Secret/Token**:>=8 字符 + 变量名 secret/password/api_key

### H. 性能能力

| 优化点 | 实现 | 效果 |
|-------|------|------|
| LRU 文件哈希缓存 | `HashCacheManager` (.moat/hash_cache.json) | 1.7x 加速 |
| 并行扫描 | `ThreadPoolExecutor(max_workers=4)` | 4x 加速 |
| Git diff 增量 | `git diff --cached --name-only` | 只检查修改文件 |
| Tree-sitter 优化 | 优先 AST,失败降级正则 | 精准+容错 |
| Fail-open 装饰器 | 异常降级为默认值 | 工具不阻断 |
| 快速模式 | 5 条常识规则 | < 5s |
| 完整模式 | 跳过架构检查 `--skip-architecture` | 4.3x 加速 |
| 并发支持 | SQLite WAL + busy_timeout=5000ms | 多进程安全 |
| Tree-sitter 缓存 | 进程内 Language 对象 | 减少初始化 |
| 跳过规则配置 | 减少不必要检查 | 灵活 |
| Watchdog 防抖 | `debounce_seconds=2.0` | 避免频繁触发 |
| 缓存失效策略 | mtime + size 双重判断 | 精确性 |
| SidecarWatcher 状态 | `sidecar.json` 状态文件 | 持久化 |
| AST diff 优化 | 只比较 git diff 文件 | 增量 |
| QuickCheck 跳过未变更 | 5+ 规则但只对修改文件运行 | < 5s |

### I. 部署能力

- **PyPI 包**:`moat-ai` v1.1.2 (Apache-2.0)
- **入口点**:`moat = moat.cli:main` (via `[project.scripts]`)
- **包数据**:`moat = ["rules/*.yaml", "rules/*.yml"]`
- **可选依赖组**:
  - `dashboard`:FastAPI + uvicorn
  - `sidecar`:watchdog + FastAPI + uvicorn
  - `vscode`:pyperclip
  - `all`:三者并集
- **MkDocs 文档站**:独立生成于 `site/`
- **Docker 友好**:依赖最小,可容器化
- **CI/CD**:`pip install moat-ai && moat check --full`
- **GitHub Actions**:官方 `.github/workflows/ci.yml` + `docs.yml`
- **VS Code 插件**:`vscode-moat/` (TS),`tsconfig.json` + `package.json`
- **Background 运行**:`nohup`、`screen`、`tmux` 三种方式
- **PID 管理**:SidecarDaemon 写 `.moat/sidecar.pid`
- **macOS 兼容**:`Path.resolve()` 解决 `/var` vs `/private/var` 符号链接
- **Windows 兼容**:PowerShell 5.1 测试

### J. 测试能力

- **963+/968 测试** (99.6% 通过率,README 标注)
- **`tests/` 目录**:50+ 测试文件
  - `test_*.py` pytest 命名约定
  - 子目录:`tests/baseline/`、`tests/fixtures/`、`tests/gatekeeper/`、`tests/integration/`、`tests/metrics/`、`tests/verification/`
- **fixture 文件**:
  - `tests/fixtures/mock_openapi_specs/broken_contract.json`
  - `tests/fixtures/mock_openapi_specs/complete_openapi.json`
  - `tests/fixtures/test_openapi.yaml`
  - `tests/fixtures/projects.py`、`ts_projects.py`
- **覆盖率**:`tests/coverage.json`、`tests/critical_path_coverage.json`
- **Chaos 测试**:`moat/testing/chaos.py`
- **AI 测试门禁**:`TestCoverageGateRule` (CRITICAL,默认拦截)
- **配置**:`pyproject.toml` 的 `[tool.pytest.ini_options]`
- **辅助脚本**:`scripts/verify_install.py`、`scripts/install.sh`
- **发布脚本**:`publish_to_pypi.py`、`verify_fixes.py`、`verify_refactoring.sh`

### K. 其他能力 (Karpathy 原则)

#### Karpathy Principles (`rules/karpathy_principles.yaml` + `.py`)
- **Simplicity First**:max_file_lines=500, max_function_lines=50, max_class_methods=15, max_inheritance_depth=3, max_cyclomatic_complexity=10 (enforcement=critical)
- **Surgical Changes**:max_diff_lines=100, max_files_changed=3 (enforcement=warning)
- 原则加载器:`PrinciplesLoader` 解析 YAML,返回 `Dict[str, Principle]`
- 数据类:`Principle`(name, description, check_type, enforcement, metrics, thresholds)
- `PrincipleViolation`(principle_name, severity, message, file_path, line_number, context)

#### YAML 配置示例
```yaml
principles:
  simplicity_first:
    description: 拒绝过度工程化
    check_type: complexity_analysis
    enforcement: critical
    metrics: [function_lines, class_methods, file_lines]
    thresholds:
      max_function_lines: 50
      max_class_methods: 15
      max_file_lines: 500
      max_cyclomatic_complexity: 10
  surgical_changes:
    description: 修改必须精准
    check_type: diff_size_limit
    enforcement: warning
    metrics: [diff_lines, files_changed]
    thresholds:
      max_diff_lines: 100
      max_files_changed: 3
```

---

## 关键代码片段

### 1. 硬编码密钥正则模式(13 类)
**文件**:`moat/checks/secrets.py:32-105`
```python
SECRET_PATTERNS = [
    ("aws_access_key", r"AKIA[0-9A-Z]{16}", "CRITICAL", "AWS Access Key ID"),
    ("github_token", r"ghp_[0-9a-zA-Z]{36}", "CRITICAL", "GitHub Personal Access Token"),
    ("github_oauth", r"gho_[0-9a-zA-Z]{36}", "CRITICAL", "GitHub OAuth Token"),
    ("slack_token", r"xox[baprs]-[0-9a-zA-Z]{10,48}", "CRITICAL", "Slack Token"),
    ("google_api_key", r"AIza[0-9A-Za-z_-]{20,}", "CRITICAL", "Google API Key"),
    ("generic_api_key", r'api[_-]key["\s:=]+["\x27]([a-zA-Z0-9_-]{16,})["\x27]', "HIGH", "Generic API Key"),
    ("password_assignment", r'(?:password|passwd|pwd)\s*[:=]\s*["\']([^"\']{8,})["\']', "CRITICAL", "Hardcoded Password"),
    ("secret_assignment", r'(?:secret|api_secret|app_secret)\s*[:=]\s*["\']([^"\']{8,})["\']', "CRITICAL", "Hardcoded Secret"),
    ("private_key_rsa", r"-----BEGIN RSA PRIVATE KEY-----", "CRITICAL", "RSA Private Key"),
    ("private_key_ecdsa", r"-----BEGIN EC PRIVATE KEY-----", "CRITICAL", "ECDSA Private Key"),
    ("private_key_pkcs8", r"-----BEGIN PRIVATE KEY-----", "CRITICAL", "PKCS#8 Private Key"),
    ("jwt_token", r'[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}\.[a-zA-Z0-9_-]{20,}', "HIGH", "JWT Token (possible)"),
]
```

### 2. Tree-sitter SQL 注入 AST 检测
**文件**:`moat/checks/sql_injection.py:124-173`
```python
def _check_with_ast(self, content: str, file_path: Path) -> list[CheckResult]:
    parser = Parser(language=Language(tspython.language()))
    tree = parser.parse(bytes(content, "utf8"))

    def traverse(node):
        if node.type == "call":
            func_name = self._get_function_name(node)
            if func_name and any(func_name.endswith(p.replace(r"\.", "").replace(r"\(", ""))
                                 for p in self.SQL_EXEC_PATTERNS):
                sql_arg = self._get_sql_argument(node)
                if sql_arg and self._has_string_concat(sql_arg):
                    line = node.start_point[0] + 1
                    results.append(CheckResult(type="fail", level="CRITICAL", ...))

    traverse(tree.root_node)
```

### 3. Pain Score 加权评分
**文件**:`moat/pain/scorer.py:30-139`
```python
WEIGHTS = {
    "core_business": 30,
    "auth_payment": 40,
    "api_endpoint": 20,
    "race_condition": 25,
    "syntax_error": 15,
    "missing_doc": 5,
    "third_party": -50,
}

def calculate(self, error: dict, context: dict = None) -> PainScore:
    score = 0.0
    file_path = error.get("file", "").lower()
    if self._is_core_business(file_path, context):
        score += self.WEIGHTS["core_business"]
    if self._is_critical_logic(file_path, error_type, message):
        score += self.WEIGHTS["auth_payment"]
    # ... 7 类权重累加
    score = max(0.0, min(100.0, score))
    return PainScore(score=score, level=self._score_to_level(score), ...)

def _score_to_level(self, score):
    if score >= 75: return "CRITICAL"
    elif score >= 50: return "HIGH"
    elif score >= 25: return "MEDIUM"
    else: return "LOW"
```

### 4. Gatekeeper 三层豁免
**文件**:`moat/gatekeeper/types.py:188-271`
```python
class IgnoreMechanism:
    @staticmethod
    def should_ignore(violation, file_path, config) -> bool:
        # 层级 1:行内注释(最细粒度)
        if IgnoreMechanism._has_line_ignore(violation, file_path):
            return True
        # 层级 2:文件头注释
        if IgnoreMechanism._has_file_ignore(violation, file_path):
            return True
        # 层级 3:配置文件
        if IgnoreMechanism._has_config_ignore(violation, file_path, config):
            return True
        return False

    @staticmethod
    def _has_line_ignore(violation, file_path) -> bool:
        content = Path(file_path).read_text()
        lines = content.split('\n')
        start = max(0, (violation.line or 0) - 5)
        end = min(len(lines), (violation.line or 0) + 5)
        context = '\n'.join(lines[start:end])
        return f"# moat-ignore: {violation.rule_id}" in context
```

### 5. Fail-open 装饰器
**文件**:`moat/checks/fail_open.py:23-52`
```python
def fail_open(default_return=None, log_level=logging.WARNING):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logger.log(log_level, f"[{func.__name__}] 执行失败,已跳过(Fail-open): {e}")
                return default_return
        return wrapper
    return decorator
```

### 6. SQLite 桥接器(9 张表)
**文件**:`moat/memory/bridge.py:59-245`
```python
def _create_tables(self):
    self.conn.execute("""
        CREATE TABLE IF NOT EXISTS bug_memories (
            id TEXT PRIMARY KEY, error_type TEXT, file_path TEXT, line INTEGER,
            pain_score REAL, message TEXT, first_seen TIMESTAMP, last_seen TIMESTAMP,
            occurrence_count INTEGER DEFAULT 1, avg_pain REAL, status TEXT DEFAULT 'active',
            metadata TEXT, created_by TEXT DEFAULT 'moat',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # + fix_history / weak_points / insights / fix_patterns / sync_status
    # + dream_triggers / smart_hints / contract_baselines / api_contracts
    # + 10 个索引
```

### 7. 影响域分析(置信度权重)
**文件**:`moat/ast/builder.py:236-296`
```python
def analyze_impacts(self, changes, skeleton_dict):
    for change in changes:
        func_name = change.get("function")
        direct_callers, indirect_callers = [], []
        for caller, callees in call_graph.items():
            if func_name in callees:
                edge = next((e for e in edges if e["target"] == func_name
                            and e["source"] == caller), None)
                confidence = edge["confidence"] if edge else 1.0
                if confidence >= 0.8:
                    direct_callers.append({"caller": caller, "confidence": confidence})
                else:
                    indirect_callers.append(...)

        total_callers = len(direct_callers) + len(indirect_callers)
        confidence_weight = sum(c["confidence"] for c in direct_callers + indirect_callers)

        if len(direct_callers) >= 5 or confidence_weight >= 4.0:
            risk_level = "high"
        elif len(direct_callers) >= 2 or total_callers >= 5:
            risk_level = "medium"
        else:
            risk_level = "low"
```

### 8. LRU 文件哈希缓存(并行扫描)
**文件**:`moat/cache.py:213-311`
```python
def capture_state_with_cache(project_root, cache_mgr=None, parallel=True, max_workers=4):
    if parallel and len(all_files) > 10:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_process_file, f, project_root): f for f in all_files}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    rel_path, file_hash, line_count = result
                    file_hashes[rel_path] = file_hash
                    line_counts[rel_path] = line_count
                    # 更新缓存
    return {"py_files": sorted(py_files), "total_lines": total_lines,
            "file_hashes": file_hashes, "line_counts": line_counts}
```

### 9. 神经衰弱检测(进化系统)
**文件**:`moat/evolution_metrics.py:184-236`
```python
def _detect_neural_fatigue(self, dimension_scores):
    negative_dimensions = 0
    for dim, score in dimension_scores.items():
        weight = self.METRIC_WEIGHTS.get(dim, 0)
        if weight < 0 and score > 0.5:
            negative_dimensions += 1
    dimension_ratio = negative_dimensions / total_dimensions

    negative_weight_sum = sum(
        abs(self.METRIC_WEIGHTS[dim]) * score
        for dim, score in dimension_scores.items()
        if self.METRIC_WEIGHTS.get(dim, 0) < 0
    )
    weight_ratio = negative_weight_sum / total_weight_sum

    negative_ratio = (dimension_ratio + weight_ratio) / 2
    if negative_ratio >= 0.5: status = "critical"
    elif negative_ratio >= 0.3: status = "warning"
    elif negative_ratio <= 0.15: status = "encourage"
    else: status = "normal"
```

### 10. Sidecar 守护进程(watchdog)
**文件**:`moat/sidecar/watcher.py:25-180`
```python
class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, project_root, debounce_seconds=2.0):
        self.debounce_seconds = debounce_seconds
        self.last_event_time = {}

    def on_modified(self, event):
        if self._should_ignore(event.src_path): return
        # 防抖处理
        if time.time() - self.last_event_time.get(path, 0) < self.debounce_seconds:
            return
        self._trigger_check(path, "modified")

    def _trigger_check(self, file_path, event_type):
        from moat.ast.diff import diff_project
        from moat.ast.builder import build_skeleton
        from moat.pain.scorer import calculate_total_pain
        skeleton = build_skeleton(str(self.project))
        changes = diff_project(str(self.project))
        pain_result = calculate_total_pain(changes_as_dict)
        self._update_status(checked=True, errors=..., pain_score=...)
```

---

## 集成点 (详细)

### 与 AI 工具集成
1. **Claude Code**
   - `moat adapter claude` → 在项目根写 `CLAUDE.md`(包含 Moat 铁律 + 命令)
   - `moat init` → 在 `.claude/settings.json` 注入 PreToolUse/PostToolUse Hooks
   - PreToolUse:`moat gatekeeper check --file ${file}` (timeout 5000ms)
   - PostToolUse:`moat check --diff` (timeout 10000ms)

2. **Cursor**
   - `moat adapter all` → 写 `.cursor/rules.mdc`(MDC 格式)
   - globs: `["**/*.py"]`

3. **Pre-commit**
   - `moat adapter precommit` → 写 `.git/hooks/pre-commit` (chmod 0o755)
   - bash:`moat check`,失败 exit 1

4. **VS Code**
   - `vscode-moat/` 完整 TS 扩展
   - `tasks.json`:注册 `Moat: 检查当前文件` / `Moat: 全量检查`
   - `keybindings.json`:`ctrl+shift+m` 触发

5. **GitHub Actions**
   - `pip install moat-ai && moat check --full`
   - 官方 `.github/workflows/ci.yml`

### 与 CI/CD 集成
- **GitHub Actions**:`.github/workflows/ci.yml` + `docs.yml`
- **GitLab CI**:`.gitlab-ci.yml`(自动检测)
- **Jenkinsfile**(自动检测)
- **CircleCI**:`.circleci/config.yml`(自动检测)

### 与外部工具集成
- **pip-audit**:`subprocess.run(['pip-audit', '--desc', '--fix'])` (Python 依赖)
- **npm audit**:`subprocess.run(['npm', 'audit', '--json'])` (Node 依赖)
- **Tree-sitter**:`tree_sitter_python` 解析
- **One Memory (TypeScript)**:`.moat/memory.db` SQLite 桥接(WAL 模式)
- **MkDocs**:文档站生成(独立 `site/` 目录)

### 与运行时集成
- **httpx**:探测 API 端点存活(可选基地址 `http://localhost:8000`)
- **OpenAPI schema**:从 `/openapi.json` 读取路径 + 方法
- **watchdog**:文件变化事件监听(可选依赖)
- **FastAPI/uvicorn**:Web 看板 + Sidecar API(可选依赖)

### 与配置系统集成
- `.moat/moat.json`:主配置(v1.0+)
- `.moat/config.json`:旧版配置(向后兼容)
- `.moat/gatekeeper_config.json`:守门规则 + 豁免
- `.moat/baseline.json`:L4 基线
- `.moat/hash_cache.json`:文件哈希缓存
- `.moat/sidecar.pid`:Sidecar PID
- `.moat/sidecar.log`:Sidecar 日志
- `.moat/sidecar.json`:Sidecar 状态
- `.moat/architecture_intent.md`:架构意图(可选)
- `.moat/memory.db`:SQLite 共享存储(One Memory 集成)
- `.moat/evolution_metrics.json`:进化指标历史
- `.moat/evolved_rules.json`:One Memory 生成的规则
- `.moat/false_positive_stats.json`:误报率统计
- `.moat/baselines/`:架构基线目录(可回滚)

### 与 Karpathy Principles 集成
- `moat/rules/karpathy_principles.yaml` 加载 2 大原则:
  - `simplicity_first`(critical)
  - `surgical_changes`(warning)
- `PrinciplesLoader` 解析,`SurgicalChangesChecker` + `SimplicityChecker` 实施
- `_check_karpathy_principles` 集成到 Gatekeeper

### 报告/分析工具集成
- `moat report` → text/md/json + 剪贴板(pyperclip/pbcopy)
- `moat architecture` → 架构健康评分 + 熵增 + 依赖枢纽
- `moat fix` → AI 修复建议(dry-run / no-dry-run)
- `moat dashboard` → Web 看板(FastAPI + 内嵌前端)
- `moat evolution report` → 进化指标 + 神经衰弱状态

### 跨语言检查集成
- **Python**:直接 AST + Tree-sitter
- **TypeScript/JavaScript**:`tsc --noEmit` (config 配 `tsc_path`)
- **Go**:正则模式识别(manager/engine/bridge/handler/service/provider/agent 关键字)
- **Rust**:仅自动检测文件存在,不检查
- **CodeGraph 集成**:`enable_semantic_checks=true` 启用 TS 深度语义分析

### 监控/告警集成
- `moat watch --log <path>` 实时 tail -f
- 颜色编码:ERROR 红 / WARNING 黄 / INFO 蓝 / OK 绿
- 后台运行:`nohup`、`screen`、`tmux`
- Sidecar 自动触发增量检查 + Pain Score 更新

---

## 项目技术亮点总结

1. **极致零配置**:只需 `pip install moat-ai && moat init`,自动检测项目 + 保存基线
2. **4 层门禁检查体系**(L0-L4):语法→存活→结构→关联→基线,12 秒跑完
3. **7 大审计算子**:目录责任、模块钻取、API 规格、框架使用、运行证据、架构健康、真实文档
4. **13 模式硬编码密钥检测 + 上下文误报抑制**(环境变量/占位符/注释)
5. **Tree-sitter 多语言 AST 分析**(SQL 注入/函数调用/依赖图)
6. **Pain Score 加权评分系统**(0-100,7 类权重,3 类等级)
7. **架构熵增检测 + 依赖枢纽识别**(代码膨胀预警)
8. **3 层豁免机制**(行内/文件/配置)
9. **AI 进化引擎**(从 One Memory Insight 自动生成规则)
10. **9 表 SQLite 共享桥接**(Moat + One Memory 跨语言协作)
11. **LRU 哈希缓存 + 并行扫描**(1.7x 加速)
12. **Watchdog Sidecar 守护进程 + REST API + Web 看板**(实时监控)
13. **Karpathy 原则具象化**(Simplicity First + Surgical Changes)
14. **AI 修复策略库**(置信度 + 自动修复 + PR 描述)
15. **99.6% 测试覆盖率**(963+/968 测试)
