# 05-moa-skill 详细能力分析

> **项目**: `moa-skill` v1.3.2 — Claude Code 的多模型委员会(Mixture-of-Agents)技能
> **作者**: sds.rs  · **许可证**: MIT  · **Python**: 3.9+  · **测试**: 126 通过
> **源码量**: `moa.py` ≈ 1426 行 + 6 个 references + 6 个测试文件 + 8 个 E2E 报告样例

---

## 1. 项目概述

**MoA Skill** 是一个 Claude Code 插件,把当前主 agent 升级为"**五模型委员会主席**"——**最多 4 个异构 LLM 委员**(OpenAI / Anthropic / Google / xAI 家族)并行盲审 → 结构化互动 → 证据驱动收敛,产出比单一模型更可靠的判断。

**核心架构思想**:
- **Mixture-of-Agents (MoA)**:不同模型盲点不同,独立盲审 + 结构化聚合突破单模型上限
- **仅对 LLM-judge 型主观任务有正收益**(评审/决策/头脑风暴);算术/事实检索等机械验证任务**显式不启动**
- **盲审隔离 + 反群体思维纪律栈**作为系统化对冲
- **聚合者 = 持完整上下文的当前 agent**(不调无上下文的 API),fallback 链只降委员不降仲裁人
- **三通道混合(CH1 子代理 + CH2 codex CLI + CH3 API)**,成本自适应

**目录结构总览**:
```
moa-skill-main/
├── .claude-plugin/                # Claude Code 插件清单
│   ├── marketplace.json
│   └── plugin.json
├── scripts/
│   └── bump-version.sh            # 版本号单一来源同步器
├── skills/moa/                    # 核心 SKILL 资产
│   ├── SKILL.md                   # Claude 加载入口(107 行)
│   ├── assets/
│   │   └── config.example.yaml    # 委员配置模板
│   ├── references/                # 7 份参考契约
│   │   ├── briefing.md            # 简报(黑板)写法
│   │   ├── routing.md             # auto 模式 4 步路由
│   │   ├── synthesis.md           # 仲裁人收敛硬规则
│   │   ├── discuss.md             # 开会讨论编排
│   │   ├── roles-review.md        # 评审场景 5 个对抗角色
│   │   ├── roles-decide.md        # 决策场景认领角色模板
│   │   └── roles-brainstorm.md    # 头脑风暴 5 个发散人格
│   ├── scripts/
│   │   └── moa.py                 # 1426 行核心脚本
│   └── tests/                     # 6 个测试文件
│       ├── test_moa.py            # 离线核心测试(主)
│       ├── test_discuss.py        # 开会讨论模式测试
│       ├── test_fault_injection.py # 故障注入(重试/JSON 修复/中止)
│       ├── test_routing.py        # auto 路由回归
│       ├── test_safety.py         # §8 安全:密钥扫描/敏感材料告警
│       └── test_triggers.py       # SKILL.md 触发词回归
└── moa-reports/                   # 8 个真实 E2E 证据样例
    ├── auto-full/                 # auto 顶配 4 席实跑
    ├── cost-m4/                   # M4 成本实测
    ├── e2e-brainstorm/            # 头脑风暴流程
    ├── e2e-decide/                # 决策流程
    ├── e2e-disagree/              # 构造分歧评审
    ├── e2e-discuss/               # 开会讨论 2 轮+盲投
    ├── e2e-fault/                 # 故障注入(全挂→中止)
    ├── e2e-full/                  # 顶配 4 席 review 三通道
    └── e2e-selfmoa/               # Self-MoA 主动
```

---

## 2. 核心模块清单

| 模块 | 路径 | 职责 | 行数 |
|---|---|---|---|
| **M1 入口 / CLI 路由** | `moa.py main()` (L1358-1422) | argparse 子命令解析、调度 9 个子命令 | 65 |
| **M2 通道调度** | `moa.py _dispatch_channels` (L404-442) | fallback 链展开、api/cli 通道调度 | 39 |
| **M3 精炼轮 / 开会讨论** | `cmd_refine` + `cmd_discuss_*` (L1193-1328) | 匿名互评、交叉审查、顺序讨论、盲投 | 136 |
| **M4 统计块** | `compute_stats` / `compute_refine_stats` / `compute_discuss_stats` (L689-890) | 三模式分模式统计;token 用量;谄媚计数;从众检测 | 202 |
| **M5 产物落盘 / I/O** | `write_member` / `load_members` / `append_transcript` (L660-548) | JSON 序列化、安全文件名、round 隔离 | ~80 |
| **M6 HTTP / 协议层** | `http_post` / `endpoint_and_headers` / `call_with_json_repair` (L168-318) | urllib 纯标准 HTTP,代理优先,JSON 一次性自修复 | 150 |
| **M7 CH2 codex CLI 通道** | `call_cli_codex` (L323-364) | subprocess 调用 codex exec,错误分类 | 42 |
| **M8 安全 / 密钥扫描** | `scan_secrets` / `warn_sensitive_material` / `leak_check` (L893-994) | 9 类密钥正则 + 占位符抑制 + 脱敏 | 102 |
| **M9 quorum 宽限窗** | `dispatch_with_quorum` (L589-639) | 并行执行 + 早返回 + 落伍者 skipped_grace | 51 |
| **M10 dry-run 预演** | `dry_run` / `cmd_dry_run` (L999-1023) | 阵容展示、计费判定、敏感扫描告警 | 25 |
| **M11 配置校验** | `resolve_config` / `validate_config` (L1028-1076) | 路径解析、缺字段指名报错、文件覆盖检测 | 49 |
| **M12 custom 模式** | `build_custom_members` / `apply_custom_committee` (L1084-1112) | `--models`/`--members` CLI 入口,Self-MoA 触发 | 29 |
| **M13 角色契约加载** | `load_role_prompt` (L153-165) | custom_roles > 角色 md 段落 > 兜底 | 13 |
| **M14 错误分类** | `PermanentError` / `TransientError` / `classify_http_error` (L225-249) | 瞬态/永久两分,带 err_class 与 hint | 25 |
| **M15 匿名化** | `anonymize_others` (L744-753) | 精炼轮去身份化(甲乙丙丁戊己庚辛) | 10 |
| **M16 schema 系统** | `REVIEW_SCHEMA` / `DECIDE_SCHEMA` / `BRAINSTORM_SCHEMA` + 精炼 (L43-128) | 5 个 JSON Schema 字符串,生成 + 精炼双套 | 86 |
| **M17 SKILL.md 入口** | `skills/moa/SKILL.md` | 触发词、4 步工作流、使用纪律、固有限制 | 107 |
| **M18 references 契约** | `references/*.md` × 7 | 简报、路由、合成、讨论、3 个角色集 | ~480 |
| **M19 plugin 清单** | `.claude-plugin/{plugin,marketplace}.json` | 分发元数据 | 41 |
| **M20 版本同步** | `scripts/bump-version.sh` | 4 处版本号单一来源同步器 | 67 |

---

## 3. 详细能力列表

### 3.1 API / 命令行接口

| 能力 | 子命令 | 实现位置 | 关键参数 |
|---|---|---|---|
| **生成委员盲审产物** | `generate` | `cmd_generate` (L1115-1154) | `--config --input --mode --collect-dir --member --models --members --topic` |
| **精炼轮(匿名互评 / 交叉审查)** | `refine` | `cmd_refine` (L1193-1232) | `--round` (默认 1) |
| **机械统计块** | `stats` | `cmd_stats` (L1175-1190) | `--mode --round` (产物: `stats.json` / `stats.r{N}.json`) |
| **预演(无 API 调用)** | `dry-run` | `cmd_dry_run` (L1331-1333) | `--refine-rounds {0,1,2}` |
| **开会讨论-单回合** | `discuss-turn` | `cmd_discuss_turn` (L1256-1279) | `--member --round --inject`(CH1 回填) |
| **开会讨论-取精确 prompt** | `discuss-prompt` | `cmd_discuss_prompt` (L1282-1289) | `--blind` 切盲投 prompt |
| **开会讨论-收尾盲投** | `discuss-blindvote` | `cmd_discuss_blindvote` (L1292-1314) | `--inject` 路径 |
| **开会讨论-统计** | `discuss-stats` | `cmd_discuss_stats` (L1317-1328) | 产出 `discuss_stats.json` |
| **静态密钥自查** | `leak-check` | `cmd_leak_check` (L1336-1355) | `[paths...]` 任意路径,默认扫 `moa-reports/` 等 |
| **custom 模式(临时委员会)** | `--models "id1,id2,..."` | `apply_custom_committee` (L1105-1112) | `--members N` 配合单模型 = 主动 Self-MoA |
| **--member 子集过滤** | 多数子命令支持 | `_select_members` (L1165-1172) | 逗号分隔名 |
| **版本号同步 / 校验** | `scripts/bump-version.sh` | 外部 bash 脚本 | `bump-version.sh <x.y.z>` / `--check` |

### 3.2 数据模型

#### 3.2.1 委员配置 (`config.yaml`)

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | str (必填,唯一) | 委员名,落盘文件名 = `member_<name>.json` |
| `seat` | enum `A/B/C/D` | 默认角色映射的 key |
| `channel` | enum `api/cli/subagent` | 通道(CH3/CH2/CH1) |
| `protocol` | enum `openrouter/openai` | 仅 CH3 有效;openai = OpenAI 兼容端点 |
| `model` | str | 模型 ID;codex 席可省(用默认) |
| `fallback` | list | 降级链(永久错误立即试下一条) |
| `cli_extra` | list[str] | codex CLI 额外 flag(如 `-c model_reasoning_effort=high`) |
| `role` | str | 自定义角色 key,优先于 seat 默认 |
| `temperature_generate` | float | 显式温度,覆盖模式默认 |
| `timeout_seconds` | int | 单席超时(覆盖全局) |
| `codex_bin` | str | codex 二进制路径(默认 `codex`) |
| `api_key_env` | str | 自定义 key 环境变量名(默认随 protocol) |
| `base_url` | str | OpenAI 兼容自定义端点 |

#### 3.2.2 全局 options

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `max_tokens_member` | int | 3000 | 推理模型长输出需 ≥ 8000(防空壳) |
| `timeout_seconds` | int | 180 | 全局超时(member 级可覆盖) |
| `min_successful_members` | int | 2 | 运行时 `min(this, seats)`,避免 L1 单委员被误杀 |
| `grace_seconds` | int | 30 | quorum 达到后给落伍者的宽限窗 |

#### 3.2.3 自定义角色 `custom_roles`

`map[role_key] -> prompt_text`,优先级最高(custom > md 段落 > 兜底)

#### 3.2.4 三种 JSON Schema

| 模式 | 字段集 |
|---|---|
| **REVIEW** | `verdict` (pass/conditional/fail), `confidence`, `issues[]` (title/severity/where/why/consequence/suggestion/confidence/kind/source), `assumptions[]`, `would_change_my_mind`, `summary` |
| **DECIDE** | `claimed_option`, `confidence`, `strongest_case[]`, `opponent_fatal_flaws[]` (option/flaw/severity fatal/major/minor), `facts[]` (claim/source), `judgements[]`, `spike_suggestion`, `assumptions[]`, `would_change_my_mind` |
| **BRAINSTORM** | `ideas[]` (title/description/target_scenario/why_gap_exists/one_week_mvp/novelty 1-5/feasibility 1-5) |

#### 3.2.5 精炼轮 Schema(2 个,共用 `REFINE_REVIEW_SCHEMA` / `REFINE_DECIDE_SCHEMA`)

- **REVIEW 精炼**: `verdicts_on_others[]` (ref_title/stance validate|challenge|abstain/reason), `revised_issues[]`, `verdict`, `confidence`, `summary`
- **DECIDE 精炼**: `cross_exam[]` (target_option/attack/attack_severity/is_fact/source), `concessions[]`, `revised_claimed_option`, `revised_confidence`, `would_change_my_mind`

#### 3.2.6 讨论 Schema(2 个)

- **DISCUSS_SCHEMA**: `still_holding`, `responses[]` (to/stance agree|rebut|merge/reason), `new_argument`, `position_changed`, `changed_by_new_argument`, `current_stance`, `confidence`
- **BLIND_VOTE_SCHEMA**: `final_stance`, `confidence`, `key_reason`

#### 3.2.7 产物文件格式

```
<collect-dir>/
├── member_<name>.json              # 生成轮产物
├── member_<name>.r<N>.json         # 精炼轮产物
├── blindvote_<seat>.json           # 讨论盲投产物
├── discussion.jsonl                # 讨论逐回合 transcript(JSON Lines)
├── stats.json                      # 生成轮统计
├── stats.r<N>.json                 # 精炼轮统计
├── discuss_stats.json              # 讨论统计
├── brief.md                        # 简报(用户写)
├── config.yaml                     # 委员配置
└── report.md                       # 仲裁人最终收敛(由 agent 写,不在脚本内)
```

### 3.3 算法 / 逻辑

#### 3.3.1 Quorum 宽限窗(`dispatch_with_quorum`)

```
工作线程池(每委员一线程)
  ↓
ok >= quorum_target ?  → 设 grace_deadline
  ↓ (yes)
等待 grace 秒内完结的任务
  ↓ (超时)
shutdown(wait=False)  ← 关键:不 join 落伍线程
  返回:已 ok + 落伍者标 skipped_grace
```

**关键性质**:
- **立即返回**(P0-1 回归保护):`shutdown(wait=not abandoned)`,函数返回 wall < 落伍者时长
- **每完成一个就回调 `on_done`** → 法定数先达,先落盘
- **quorum 目标 = max(min_ok, dispatchable-1)**

#### 3.3.2 计费判定(`_effective_billing`)

**关键修复**: 不看主 channel,而看 `resolve_channel` 真正会跑的首个 try。
- `cli` (codex) → 订阅免费
- `api` → 计费
- `subagent` 无 fallback → 仲裁人免费派发
- `subagent` + `api fallback` → ⚠ 实走计费 API(dry-run 显式打 ⚠)

#### 3.3.3 谄媚计数器(精炼轮 review)

```
prior_verdicts = {a: fail, b: fail, c: pass, d: pass}  # 多数 = fail
refine_verdicts = {a: fail, b: fail, c: fail, d: fail}  # c, d 翻向多数
movers = 2  (c, d 改了 verdict)
flips_toward_majority = 2  (都翻向 fail,且未提任何 challenge)
sycophancy_alert = (movers > 0) and (flips_toward_majority / movers) > 0.5
```

#### 3.3.4 早停信号

- **review**: 本轮 verdict 全一致 **且** 无 disputed → `early_stop_suggested=true`
- **decide**: 所有席 `revised_claimed_option` 一致 → `early_stop_suggested=true`
- **discuss**: 末轮 `pseudo_discussion_rounds` → 早停建议

#### 3.3.5 从众检测(discuss 模式)

```
for turn in transcript:
  if turn.position_changed and not turn.changed_by_new_argument:
    conformity_alerts.append(turn)  # 无新论据却改立场 = 从众
```

#### 3.3.6 假讨论检测

```
for round in rounds:
  if all(turns in round have empty new_argument):
    pseudo_discussion_rounds.append(round)
```

#### 3.3.7 漂移检测(讨论 vs 盲投)

```
for each seat:
  drift_pair = {
    "discussion_final": last turn's current_stance,
    "blind_final": blindvote's final_stance  # 不看 transcript 复述
  }
  if drift_pair.disagree:  # 仲裁人按证据判断
    flag 该席立场在讨论中漂移,按盲投计其真实立场
```

#### 3.3.8 角色调度映射 `DEFAULT_SEAT_ROLE`

| 模式 \ seat | A | B | C | D |
|---|---|---|---|---|
| **review** | feasibility_skeptic | maintainability_reviewer | security_auditor | user_advocate |
| **brainstorm** | radical_innovator | cross_industry_transplanter | grounded_diverger | edge_user_voice |
| **decide** | (动态) | (动态) | (动态) | (动态) |

`decide` 模式无 seat 默认——`custom_roles` 里按 `advocate_<选项>` 动态注入。

#### 3.3.9 Custom 模式构建(`build_custom_members`)

```
--models "a,b,c"          → [a=A, b=B, c=C] (3 席)
--models "a,b" --members 3 → ERROR: 数不一致
--models "x" --members 3    → [x=A, x=B, x=C] (Self-MoA)
--models "x,x"             → [x=A, x=B] (显式重复 = Self-MoA)
--models 5 个               → ERROR: 上限 4 席
```

### 3.4 UI / 用户接口

#### 3.4.1 SKILL.md 触发词(`description` 字段)

**中文触发词**: `moa模式 / 多人评审 / 多人委员会 / 委员会 / 多模型 / 第二意见 / 交叉验证 / 对上面的分析做出建议 / 对上面的总结做出建议 / moa / council`

**判断类信号**(自调触发): `评审 / 决策 / 选型 / 头脑风暴 / 拿不定主意 / 哪个最优 / 做出建议 / 没把握 / 置信度 / 方案 / review / brainstorm / decide / 评估`

**L0 闸门负信号**(可机械验证 → 不启动): `计算 / 等于几 / 多少 / 首都 / 是什么意思 / 翻译 / 几点 / 怎么读取 / 改成 / 列出 / 前五位 / 命令是什么 / 语法错误 / 第几行 / 加 / 乘以 / 状态码`

#### 3.4.2 dry-run 输出格式

```
=== DRY RUN (review) ===
material: 1240 chars | refine rounds: 1
member            seat  channel    model                       protocol
skeptic-a         A     cli        (codex default)             codex
maintainer-b      B     subagent   claude-opus-4-8             -
security-c        C     api        google/gemini-3.1-pro-preview  openrouter
user-d            D     api        openai/gpt-5.6-sol          openrouter

外部委员调用数 = 4 席 × 2(生成+精炼) = 8
  其中 API 计费通道(CH3): 2 席 × 2 = 4 次
  订阅通道(CH1/CH2): 2 席 × 2 = 4 次(只计次数,不折算美元)
收敛由当前 agent(仲裁人)完成,不计外部调用。
proxy: via {'http': 'http://127.0.0.1:7890'}
⚠ 敏感信息告警: 材料中检出疑似密钥/凭据 openai_key×1, ...
确认无误后去掉 dry-run 正式运行。
```

#### 3.4.3 收敛报告结构(由仲裁人/agent 写)

**review 模式**:
1. 结论摘要(通过/有条件/不通过 + 置信度)
2. 高置信问题(按 severity 排序)
3. 单一来源问题
4. 待人工裁决的分歧
5. 各委员意见摘要
6. 固定免责声明

**decide 模式**: `RECOMMEND <选项>` / `INCONCLUSIVE` + 对比矩阵 + 结论失效条件 + 依赖顺序

**brainstorm 模式**: Top 5 推荐 + 显性机会区 + 高风险高差异区(孤例保护) + 已淘汰点子

#### 3.4.4 命名规则(匿名化)

`ANON_LABELS = "甲乙丙丁戊己庚辛"` — 8 个标签循环,精炼轮里去掉 name/model,只留 `{"评审员": "甲", "意见": {...}}`

#### 3.4.5 进度输出(stderr)

```
[generate] dispatching 3 members (review) ...
  - radical-a (radical_innovator, 2.3s) via api: OK
  - transplant-b (cross_industry_transplanter, 1.8s) via api: OK
  - grounded-c (grounded_diverger, 0.0s) via -: FAIL[skipped_grace]: ...
[generate] done: 2/3 ok [DEGRADED: ...] -> moa-reports/run
```

### 3.5 集成 / 外部 API

#### 3.5.1 CH3 API 通道

| 协议 | 端点 | key 环境变量 | 额外头 |
|---|---|---|---|
| **openrouter** (默认) | `https://openrouter.ai/api/v1/chat/completions` | `OPENROUTER_API_KEY` | `HTTP-Referer`, `X-Title` |
| **openai** | `https://api.openai.com/v1/chat/completions` 或自定义 `base_url` | `OPENAI_API_KEY` 或 `api_key_env` | 无 |
| **openai 自定义** | `base_url: http://local:8000/v1` | `api_key_env: MYKEY` | 无 |

**OpenAI 兼容端点**: 任何遵守 chat completions 协议的端点(LM Studio / vLLM / OpenRouter / 一言 / etc.)

#### 3.5.2 CH2 codex CLI 通道

```
codex exec -s read-only --skip-git-repo-check --ephemeral --color never
  --output-last-message <last.txt> [-m <model>] [<cli_extra>...] -
```

- `prompt` 走 stdin(防 ARG_MAX 与注入)
- `--output-last-message` 把最终消息写文件,干净取 JSON(不刮 streaming)
- `-s read-only` 文件系统级只读(委员无写权限)
- `--ephemeral` 不留会话文件
- `--skip-git-repo-check` 允许非 git 目录

**错误分类**:
- stderr 含 `login/auth/credential/401/403` → **Permanent** (auth)
- `subprocess.TimeoutExpired` → **Transient** (timeout)
- 非零退出 + 其他 → **Transient** (cli)
- 0 退出 + 空输出 → **Transient** (empty,配额耗尽静默空壳)

#### 3.5.3 CH1 子代理通道(脚本外)

```
moa.py 看到 channel=subagent  → 跳过(留给仲裁人)
仲裁人用 Agent 工具外派发子代理
  → 提示词 = 角色契约 + 简报
  → 明令子代理:禁工具/仅 JSON 输出
  → 子代理返回 JSON
  → 仲裁人写入 collect-dir/member_<name>.json
  → 后续 moa.py stats 覆盖全部席位(含 CH1)
```

#### 3.5.4 4 家族顶级模型 slug(2026-07 实测可服务)

| 家族 | 模型 | 状态 |
|---|---|---|
| OpenAI | `openai/gpt-5.6-sol`, `openai/gpt-5`, `openai/gpt-5-codex` | ✔ http200 |
| Google | `google/gemini-3.1-pro-preview`, `google/gemini-2.5-pro` | ✔ http200 |
| Anthropic | `anthropic/claude-opus-4.8` | ✔ http200 |
| xAI | `x-ai/grok-4.5`, `x-ai/grok-4.x` | ✘ list-only 404(需 x-ai 供给的 key) |
| DeepSeek | `deepseek/deepseek-v3.2` | ✘ list-only 404 |

### 3.6 工具 / 工具函数

| 函数 | 用途 | 关键行为 |
|---|---|---|
| `http_post` | 纯 stdlib HTTP POST | 自动应用代理(`ProxyHandler`) |
| `parse_json` | JSON 解析,容错 | 剥离 ```json ``` 围栏,正则提取 `{.*}` |
| `call_with_json_repair` | 输出修复 | 失败时**花一次额外调用**自愈(不丢弃视角) |
| `call_cli_codex` | codex 封装 | tempfile + subprocess,exit code 分类 |
| `dispatch_with_quorum` | 并行+宽限窗 | 立即返回不 join 落伍者 |
| `load_role_prompt` | 角色契约加载 | 3 级 fallback(custom > md > 兜底) |
| `warn_sensitive_material` | 外发前告警 | stderr 脱敏告警,不阻断 |
| `leak_check` | 静态密钥自查 | 递归扫文本文件,跳过二进制 |
| `scan_secrets` | 9 类密钥检测 | 9 正则,占位符抑制,脱敏预览 |
| `_redact` | 脱敏预览 | 前 3 字符 + `***(len N)`,不过 6 字符则全遮 |
| `anonymize_others` | 匿名化 | 甲乙丙丁戊己庚辛,8 标签循环 |
| `_safe_name` | 文件名消毒 | 仅 `[A-Za-z0-9._-]`,`..`/空 → `member` |
| `endpoint_and_headers` | 端点构造 | openrouter/openai 双协议,自定义 base_url 支持 |
| `_bypass_proxy` | no_proxy 判定 | 支持 `NO_PROXY=*` 通配、子域匹配 |
| `classify_http_error` | HTTP 状态码分类 | 4xx → permanent(除 429),5xx → transient |
| `build_custom_members` | custom 模式 | Self-MoA 触发,席位上限 4 |
| `validate_config` | 配置 schema 校验 | 7 类缺字段错误,指名报错 |
| `resolve_config` | 路径解析 | refine/discuss 禁止静默回退示例配置(P1-2) |
| `dispatch_with_quorum` | 调度 | min_ok 阈值,quorum + grace |
| `compute_stats` / `compute_refine_stats` / `compute_discuss_stats` | 统计 | 三模式分模式,token 只累加计费席 |
| `_aggregate_usage` | token 累加 | CH3 计费/CH1+CH2 订阅分离 |
| `_merge_usage` | usage 合并 | 容错(缺字段按 0) |
| `_dispatch_channels` | 通道调度核心 | fallback 链展开,错误分类即抛 |
| `load_transcript` / `append_transcript` | discussion.jsonl I/O | JSON Lines 格式,append-only |
| `format_transcript` | transcript 格式化 | 按轮次分组,发言署名只到 席+角色 |

### 3.7 安全

| 能力 | 实现 | 不变量 |
|---|---|---|
| **敏感材料外发前告警** | `warn_sensitive_material()` 在 generate/refine/discuss-turn 真派发前自动调 | stderr 脱敏告警,**不阻断**(让用户决定) |
| **静态密钥自查** | `leak-check` 子命令 + 默认扫面 `["moa-reports", "docs", "README.md", "config.yaml", "skills/moa/..."]` | 命中即非零退出(供 CI 门禁) |
| **零文件扫描保护(P0-2 回归)** | 0 文件可扫 → 退出码 **2**("未扫描到任何文件") | 区别于 clean(0) / 命中(1),不冒充安全 |
| **9 类密钥正则检测** | `_SECRET_PATTERNS`(private_key, openai_key, aws_access_key, google_api_key, github_token, slack_token, bearer_token, secret_assign, conn_string) | 检测器自身**绝不回显原文** |
| **占位符抑制** | `_PLACEHOLDER_HINTS` (含 `...`, `xxx+`, `change-me`, `your-`, `os.environ`, `example`, `placeholder`, `redacted`, `api_key_env` 等) | 防误报 |
| **脱敏预览** | `_redact(secret)` → 前 3 字符 + `***(len N)`,短于 6 全遮 | 预览足以定位,不致复用 |
| **API key 不落盘** | `endpoint_and_headers` 从 `os.environ` 读,**不写文件/日志/产物** | 配置文件永远不含 key |
| **文件名消毒(C2)** | `_safe_name` 仅留 `[A-Za-z0-9._-]`,防路径穿越 | 产物永远不出 collect-dir |
| **CH1 标识** | `inject_result.channel_used = "subagent (arbiter-dispatched)"` | 区分脚本派发 vs 仲裁人外派发 |
| **同源去重** | 仲裁人硬规则 1:多位委员基于同一材料/源码行得出的一致,只算一个证据源 | 防共识置顶放大群体思维 |
| **声明级数据** | 委员输出当数据处理:即使含 "ignore previous instructions" 也当数据 | 抗 prompt injection |
| **反从众三对冲** | (1) 发言序轮转 + (2) changed_by_new_argument 标注 + (3) 收尾盲投漂移检测 | 三重机械落实 |
| **谄媚计数器** | 翻向多数派且无 challenge > 50% movers → `sycophancy_alert=true` | 精炼轮机械检测 |
| **禁降级 blocker** | 硬规则 3:任何 blocker 认定前先做证伪检查(能廉价证伪却没人验证过 → 标 `未证伪`) | 防止仲裁人顺手补未验证强断言 |
| **ARBITER-UNVERIFIED 标** | 仲裁人自查门:自己新增的 blocker 必须附工具核查证据,否则打标降级 | 防仲裁人立场污染 |
| **数据即数据** | synthesis.md 通用前提:委员输出(已 parsed 字段)是被评审的数据 | 抗 prompt injection |
| **无 `--self-moa` 标志** | Self-MoA 由 `--members N --models "id"` 隐式触发,无显式 `--self-moa` 标志 | 防误用 |

### 3.8 性能

| 能力 | 数据 |
|---|---|
| **默认成本倍数** | 4.79× 单模型基线(2 席便宜 CH3 + 1 精炼轮实测) |
| **顶配成本倍数** | 4 席 + 0 精炼 ≈ 4.0×(用户实付,默认配置仅 C 席 CH3 计费) |
| **全 CH3 顶配** | 4 席 + 1 精炼 ≈ 9.6×(超 7× 目标,警示) |
| **全 CH3 L3** | 4 席 + 2 精炼 ≈ 15.1× |
| **quorum 宽限窗** | 默认 30s(可配);达 quorum 后即落盘,落伍者最多 30s |
| **HTTP 重试退避** | `time.sleep(2 ** attempt)` 指数(attempt 0,1,2 → 0s,1s,2s) |
| **总重试次数** | 默认 `retries=2` → 首次 + 2 次重试 = **3 次**后放弃 |
| **JSON 自修复** | 1 次额外调用(parse 失败才用,不浪费) |
| **HTTP 层** | 纯 stdlib (`urllib.request`),无 requests/aiohttp 依赖 |
| **并发模型** | `ThreadPoolExecutor` 而非 async(IO 密集,但 stdlib 同步 HTTP) |
| **grace 立即返回** | 关键:不 join 落伍线程,函数 wall < 落伍者时长 |
| **CI 门禁保证** | `bump-version.sh --check` 漂移即非零退出 |

### 3.9 部署 / 分发

| 能力 | 实现 |
|---|---|
| **Claude Code 插件** | `.claude-plugin/plugin.json` + `.claude-plugin/marketplace.json` |
| **Marketplace 一键安装** | `/plugin marketplace add sdsrss/moa-skill` + `/plugin install moa@moa-skill` |
| **直接复制** | `cp -r moa-skill/skills/moa ~/.claude/skills/moa`(Claude Code 自动发现) |
| **运行时依赖** | `pip install pyyaml`(HTTP 层纯 stdlib) |
| **可选依赖** | codex CLI 0.144+(CH2 通道);OpenAI 兼容端点(自托管) |
| **Python 兼容** | 3.9+(无 match/case,无 walrus) |
| **跨平台** | POSIX + Windows(PowerShell 写脚本但 bash 即可) |
| **环境变量配置** | `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `http_proxy` / `https_proxy` / `no_proxy` |
| **代理自动检测** | `urllib.request.getproxies()` 读取环境,首调时打印 `[proxy] detected env proxy` |
| **版本同步** | `scripts/bump-version.sh <x.y.z>` 4 处单一来源(plugin.json / marketplace.json / SKILL.md / README*) |
| **CI 门禁** | `bump-version.sh --check` 漂移即非零退出 |
| **测试运行** | `python -m pytest skills/moa/tests/ -q`(126 用例,无网络) |
| **真实 E2E** | `python skills/moa/scripts/moa.py generate ...` 需要真实 key |

### 3.10 测试

#### 3.10.1 测试矩阵(6 个文件)

| 文件 | 用例数 | 覆盖范围 | 关键不变量 |
|---|---|---|---|
| `test_moa.py` | ~50 | parse_json / 代理 / 错误分类 / endpoint / 角色解析 / 通道调度 / fallback / config 校验 / 自定义 / Quorum / 匿名化 / 精炼统计 / 产物读写 | 离线核心行为 |
| `test_discuss.py` | ~10 | 发言署名 / transcript 格式化 / prompt 构造 / 注入 / transcript I/O / 从众/假讨论/漂移/保留分歧 统计 | 讨论模式逻辑 |
| `test_fault_injection.py` | ~10 | 重试退避 / 永久错误不重试 / 重试耗尽 / JSON 修复 / 修复失败 / 全挂中止 / CLI 通道 4 种错误分类 | 故障注入 + 真实代码路径 |
| `test_routing.py` | ~8 | 5 类场景各 ≥2 正例 / 流水线元组形状 / 复合意图边界 / 开会讨论三硬门 | 路由回归 |
| `test_safety.py` | ~10 | 9 类密钥全部检出 / 预览不泄漏原文 / 干净文本不误报 / 二进制跳过 / 0 文件错误码 2 | §8 安全 |
| `test_triggers.py` | ~6 | 正例 ≥10 / 负例 ≥10 / 通过线 ≥ 90% / 已知边界登记 | SKILL.md description 触发 |

#### 3.10.2 测试哲学

- **离线**:`pytest` 无网络
- **真实代码路径**:故障注入在真实 `call_model` / `call_with_json_repair` / `cmd_generate` 上跑,故障只在传输边界 `http_post` 注入
- **行为+文档一致性**:触发词与 routing.md 改了 → 回来同步测试
- **边界失配登记**:`KNOWN_BOUNDARY` 显式记录启发式与语义判断的失配点
- **回归门**:关键 bug(P0-1 grace 不返回 / P0-2 0 文件冒充 clean / P1-1 缺字段裸 KeyError / P1-2 refine 静默回退示例)都有专门测试锁住

#### 3.10.3 8 个真实 E2E 报告样例(`moa-reports/`)

| 目录 | 验证目标 |
|---|---|
| `auto-full` | auto 顶配 4 席 + 三通道 + 认领对抗(API 网关选型) |
| `cost-m4` | M4 成本实测(2 席便宜 CH3,4.79× 倍数) |
| `e2e-brainstorm` | 头脑风暴流程(2 席高温发散) |
| `e2e-decide` | 决策流程(3 席认领 + 1 精炼轮交叉审查 → INCONCLUSIVE) |
| `e2e-disagree` | 构造分歧评审(2 席,A 安全 vs B 务实,精炼后保留分歧) |
| `e2e-discuss` | 开会讨论 2 轮 + 收尾盲投(密码存储方案) |
| `e2e-fault` | 故障注入(全挂 → abort) |
| `e2e-full` | 顶配 4 席 review,三通道齐活 |
| `e2e-selfmoa` | Self-MoA 主动(单模型复制 3 席) |

---

## 4. 技术栈

| 类别 | 技术 | 说明 |
|---|---|---|
| **语言** | Python 3.9+ | 纯 stdlib HTTP + pyyaml(可选用 codex CLI) |
| **HTTP** | `urllib.request` | 纯标准库,`ProxyHandler` 支持 |
| **并发** | `concurrent.futures.ThreadPoolExecutor` | 同步 IO 并发,非 async |
| **配置** | PyYAML | 唯一硬依赖 |
| **CLI 解析** | `argparse` | 9 个子命令 |
| **测试** | `pytest` | 126 用例,离线 |
| **进程调用** | `subprocess.run` | codex CLI 包装 |
| **临时目录** | `tempfile.TemporaryDirectory` | codex `--output-last-message` 落盘 |
| **平台** | POSIX + Windows | `urllib` 跨平台 |
| **JSON 序列化** | `json` stdlib | 产物 / 统计 / transcript |
| **正则** | `re` stdlib | 9 类密钥检测 + 占位符抑制 + JSON 提取 |
| **外部工具(可选)** | `codex` CLI 0.144+ | CH2 通道 |
| **Claude 集成** | Claude Code plugin(`.claude-plugin/plugin.json`) | marketplace 分发 |

---

## 5. 关键代码片段

### 5.1 Quorum 宽限窗(P0-1 关键回归保护)

```python
# moa.py L589-639
def dispatch_with_quorum(members, fn, quorum_target, grace_s, on_done=None):
    results = {}
    ex = ThreadPoolExecutor(max_workers=max(1, len(members)))
    abandoned = False
    try:
        futs = {ex.submit(fn, m): m for m in members}
        pending = set(futs)
        ok = 0
        grace_deadline = None
        while pending:
            timeout = None
            if grace_deadline is not None:
                timeout = max(0.0, grace_deadline - time.monotonic())
            done, pending = concurrent.futures.wait(
                pending, timeout=timeout, return_when=concurrent.futures.FIRST_COMPLETED)
            if not done and grace_deadline is not None:  # 宽限到期: 放弃落伍者, 立即返回
                for fut in list(pending):
                    m = futs[fut]
                    r = _skipped_grace(m)
                    results[m["name"]] = r
                    if on_done:
                        on_done(r)
                    fut.cancel()
                abandoned = True
                break
            for fut in done:
                m = futs[fut]
                r = fut.result()
                results[m["name"]] = r
                if on_done:
                    on_done(r)
                if r.get("parsed"):
                    ok += 1
            if grace_deadline is None and ok >= quorum_target and pending:
                grace_deadline = time.monotonic() + grace_s
    finally:
        ex.shutdown(wait=not abandoned)  # abandoned=True → wait=False 立即交还控制权
    return [results[m["name"]] for m in members if m["name"] in results]
```

### 5.2 谄媚计数器(精炼轮 review)

```python
# moa.py L788-805
prior_by = {r["name"]: r for r in prior_results if r.get("parsed")}
majority = _majority_verdict(prior_results, "verdict")
flips_toward_majority = 0
movers = 0
for r in ok:
    pj = prior_by.get(r["name"])
    if not pj:
        continue
    old_v = pj["parsed"].get("verdict")
    new_v = r["parsed"].get("verdict")
    if old_v != new_v:
        movers += 1
        made_challenge = any((v.get("stance") == "challenge")
                             for v in r["parsed"].get("verdicts_on_others", []) or [])
        if new_v == majority and not made_challenge:
            flips_toward_majority += 1
sycophancy_alert = movers > 0 and (flips_toward_majority / movers) > 0.5
```

### 5.3 计费判定(P 修复:不只看主通道)

```python
# moa.py L388-396
def _effective_billing(member) -> str:
    """dry-run 计费判定:按 moa.py 真正会跑的通道判定"""
    tries = resolve_channel(member)
    if not tries:
        return "sub"                        # 纯 subagent → 仲裁人免费派发
    return "sub" if tries[0][0] == "cli" else "billed"
```

**修的关键 bug**: 旧逻辑只看 `channel: subagent` 就当免费,忽略 `+api fallback` 时实走计费 API。

### 5.4 9 类密钥正则 + 占位符抑制

```python
# moa.py L896-913
_SECRET_PATTERNS = [
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-(?:or-v1-|proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("github_token", re.compile(r"\bgh[posru]_[A-Za-z0-9]{30,}")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("bearer_token", re.compile(r"[Bb]earer\s+[A-Za-z0-9._~+/-]{20,}=*")),
    ("secret_assign", re.compile(
        r"(?i)\b(?:api[_-]?key|apikey|secret|token|passwd|password|access[_-]?key)"
        r"\b\s*[:=]\s*['\"]([^'\"\s]{16,})['\"]")),
    ("conn_string", re.compile(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^/\s:@]+:[^/\s:]{6,}@")),
]
_PLACEHOLDER_HINTS = re.compile(
    r"(?i)\.\.\.|<[^>]*>|\$\{?[A-Za-z_]+|os\.environ|getenv|process\.env|"
    r"your[_-]?|change[_-]?me|example|placeholder|xxx+|redacted|dummy|fake|"
    r"test[_-]?key|_ENV\b|\bENV\b|api_key_env")
```

### 5.5 Self-MoA 触发(无 `--self-moa` 标志)

```python
# moa.py L1084-1102
def build_custom_members(models_csv: str, members_n=None) -> list:
    models = [m.strip() for m in models_csv.split(",") if m.strip()]
    if not models:
        sys.exit('--models 为空:给逗号分隔的模型 ID')
    if members_n is not None:
        if members_n < 1:
            sys.exit(f"--members 需 ≥1,收到 {members_n}")
        if len(models) == 1:
            models = models * members_n            # 单模型 + N 席 = 主动 Self-MoA
        elif len(models) != members_n:
            sys.exit(f"--members {members_n} 与 --models 的 {len(models)} 个模型数不一致")
    if len(models) > len(_CUSTOM_SEATS):
        sys.exit(f"custom 委员数上限 {len(_CUSTOM_SEATS)}")
    return [{"name": f"custom-{_CUSTOM_SEATS[i].lower()}", "seat": _CUSTOM_SEATS[i],
             "channel": "api", "protocol": "openrouter", "model": m}
            for i, m in enumerate(models)]
```

### 5.6 0 文件扫描保护(P0-2 回归)

```python
# moa.py L1336-1355
def cmd_leak_check(args):
    """门禁不变量(修 P0-2): 必须区分「扫过且干净」与「一个文件都没扫到」。"""
    paths = args.paths or _LEAK_SCAN_DEFAULT
    scanned = sum(1 for root in paths if Path(root).exists()
                  for _ in _iter_text_files(Path(root)))
    if scanned == 0:
        print(f"[leak-check] ✗ 未扫描到任何文件 (paths: {', '.join(paths)}) — "
              f"请在项目根目录运行,或用 `leak-check <path>...` 显式指定要扫描的路径。",
              file=sys.stderr)
        sys.exit(2)        # 退出码 2 — 区别于 clean=0 / 命中=1
    findings = leak_check(paths)
    if not findings:
        print(f"[leak-check] clean: 未检出疑似密钥/凭据 (scanned: {', '.join(paths)})")
        return
    print(f"[leak-check] 检出 {len(findings)} 处疑似泄漏 (预览已脱敏,请核查并轮换):",
          file=sys.stderr)
    for h in findings:
        print(f"  {h['file']}:{h['line']}  {h['category']} -> {h['preview']}", file=sys.stderr)
    sys.exit(1)
```

### 5.7 收敛硬规则(节选自 `synthesis.md`)

> **共识优先 + 同源去重**: ≥2 位委员独立指出的同类问题标"高置信"置顶,注明由哪几位提出。但**多位委员基于同一段材料/同一源码行得出的一致,只算一个证据源,不升级证据等级**——否则共识置顶会系统性放大群体思维。

> **保留分歧**: 委员之间的矛盾判断,必须原样进入"待人工裁决的分歧"一节,附上双方论据。**禁止自行折中,禁止选边后隐藏另一方**。

> **禁止淡化 + 晋级前证伪检查**: 任何委员标记为 blocker 的问题,无论你是否认同,都必须出现在报告最前部;你可附不同意见,但不能降级或删除它。**任何 blocker 认定前,先回答两问**:
> (a) 什么单一观察能证明它是错的?
> (b) 这个观察是否一条只读命令(grep/查看文件/运行一次)就能得到?
> 若"能廉价证伪却没人验证过",最高按 high 呈现并标注"未证伪"。

> **仲裁人自查门**: 你自己新增的、任何委员都没提出的 blocker/high 级结论,**必须附上你用工具核查的证据**(读了哪个文件、跑了什么命令、看到什么)。做不到就打标 `[ARBITER-UNVERIFIED]` 并降级呈现。

### 5.8 开会讨论 L3 选路门(routing.md 三硬门)

```python
# test_routing.py L116-121
def refine_stage_for(difficulty, deep_dispute, user_wants_debate, scenario="decide"):
    """auto 精炼阶段选路的近似。开会讨论 = L3 + 根本分歧未化解 + 用户明确要求,三条全满足;
    否则默认:决策→交叉审查 / 评审→匿名互评。"""
    if difficulty == "L3" and deep_dispute and user_wants_debate:
        return "discuss"
    return "cross_exam" if scenario == "decide" else "peer_review"
```

**判定优先级**:"给结论的冲动"< 用户显式要求,第 3 条是硬门——用户没显式要,哪怕分歧再大也不擅自开会讨论。

---

## 6. 集成点

### 6.1 上游(被集成的系统)

| 系统 | 集成方式 | 集成点 |
|---|---|---|
| **Claude Code** | Marketplace 插件 / 直接复制 | `.claude-plugin/plugin.json` + `skills/moa/SKILL.md` |
| **OpenRouter** | HTTPS POST | `https://openrouter.ai/api/v1/chat/completions` (默认) |
| **OpenAI** | HTTPS POST | `https://api.openai.com/v1/chat/completions` 或自定义 `base_url` |
| **任意 OpenAI 兼容端点** | HTTPS POST | `base_url` + `api_key_env` 自定义 |
| **Google Gemini(经 OpenRouter)** | HTTPS POST | `google/gemini-3.1-pro-preview` 等 |
| **Anthropic Claude(经 OpenRouter 或子代理)** | HTTPS POST + Task 工具 | `anthropic/claude-opus-4.8` / CH1 子代理 |
| **xAI Grok(经 OpenRouter)** | HTTPS POST | `x-ai/grok-4.5`(需要 x-ai 供给) |
| **Codex CLI** | subprocess | `codex exec -s read-only --output-last-message ...` |
| **GitHub 仓库** | git clone / marketplace | `https://github.com/sdsrss/moa-skill` |
| **环境变量** | `os.environ` | `OPENROUTER_API_KEY` / `OPENAI_API_KEY` / `http_proxy` / `https_proxy` / `no_proxy` |
| **PyPI** | `pip install pyyaml` | 唯一硬依赖 |

### 6.2 下游(被使用的方式)

| 角色 | 工具 | 用途 |
|---|---|---|
| **用户** | 自然语言 / `/moa <材料>` | 触发评估,提供简报 |
| **Claude(主 agent / 仲裁人)** | 8 个子命令 | dry-run / generate / refine / stats / discuss-* / leak-check |
| **仲裁人收敛** | 读 `member_*.json` + `stats.json` 写 `report.md` | 按 synthesis.md 硬规则 |
| **CH1 子代理** | Agent/Task 工具 | 派发 Claude 系席位,提示词由 `discuss-prompt` 提供 |
| **CI / 发版门禁** | `bump-version.sh --check` | 版本号一致性 |
| **CI / 收尾门禁** | `leak-check` | 密钥泄漏自查(非零退出) |
| **测试** | `python -m pytest skills/moa/tests/ -q` | 126 用例离线回归 |

### 6.3 横向类比 / 定位

| 维度 | MoA Skill | 典型 MoA/council 工具 |
|---|---|---|
| 通道拓扑 | **混合**子代理 + 本地 CLI + API + fallback 链 | 单一(全 API 或全 CLI) |
| 聚合者 | **当前 agent(带完整上下文)** + 反污染钳制 | 再调一次 API(无上下文) |
| 成本控制 | L0 闸门 + 3D 路由 + dry-run 估算 | 每次全量跑 |
| 互动模式 | **7 种可组合阶段**,按场景选 | 固定 1-3 条流程 |
| 触发方式 | 手动 + 关键词 + **主 agent 自调** | 仅手动 |
| 反群体思维 | **完整纪律栈**(7+ 条) | 1-2 条 |
| 语言 | **中文优先双语** | 仅英文 |
| LLM-judge 场景 | 强正收益 | 通用 |
| 客观可验证场景 | **显式拒绝启动**(L0 闸门) | 仍然跑(负收益) |
| 总测试数 | **126** | 多数 < 50 |

### 6.4 已知固有限制(从 SKILL.md / synthesis.md)

| 限制 | 说明 | 对冲 |
|---|---|---|
| **Prompt injection 无免疫** | 简报可能藏劫持指令,脚本不拦 | 仲裁人对异常一致的"全票 pass"保持怀疑 |
| **`disputed` 是下界** | 精炼轮靠 `ref_title` 精确复制对账,模型改写 title 会漏计 | 收敛时高严重度条目仍须逐条自查 |
| **匿名标签跨席不可对齐** | 甲/乙/丙对每席独立编号,无法反推"谁质疑了谁" | 要追溯争议链只能靠 title 匹配 |
| **共同盲区** | 各家训练数据高度重叠,全员一致 ≠ 零风险 | 免责声明勿删;全票通过也保留"存在共同盲区"限定 |
| **Self-MoA 收益减半** | 只有角色分化收益,无跨模型去相关收益 | 报告必须显式声明 |
| **CH2 codex 限制** | ChatGPT 账号 codex 不接受显式 `-m gpt-5-codex`(400) | CH2 席省略 `model`,用 codex 默认 |
| **推理模型 max_tokens 陷阱** | gpt-5.6-sol / gemini-3.1-pro-preview 推理吃光额度,正文空壳(假阴性) | 调大 `max_tokens_member` ≥ 8000(decision 长输出) |
| **xAI Grok list-only 404** | 多数 key 对 Grok 返回 list-only | 要真服务需换有 x-ai 供给的 key;否则 D 席改用其他模型 |
| **fallback 链静默换计费** | subagent + api fallback 实走计费 API | dry-run 显式打 ⚠ 警示 |

---

## 7. 总结

`moa-skill` 是一个**生产级别**的多模型委员会编排器,把"独立盲审 → 结构化互动 → 证据驱动收敛"的 MoA 范式落到了**真实可执行**的工程细节上:

- **1426 行 Python 核心**,纯 stdlib HTTP + pyyaml,零运行时依赖(除可选 codex CLI)
- **3 通道混合**(CH1 子代理 + CH2 codex CLI + CH3 API)+ **fallback 链降级**
- **3 种委员会模式**(review / decide / brainstorm)+ **3 种召集规模**(full / auto / custom)
- **7 阶段可组合互动流水线**(生成 → 精炼 → 收敛 / 含开会讨论 L3 选路)
- **完整反群体思维纪律栈**(7+ 条措施)
- **完整安全/防泄漏体系**(9 类密钥检测 + 脱敏 + 0 文件保护 + 6 套测试)
- **完整测试体系**(126 用例离线 + 8 个真实 E2E 报告样例)
- **生产级工程细节**(代理自动检测、宽限窗立即返回、计费判定修复、配置文件消毒、版本号同步、CI 门禁)
- **真实端到端覆盖**:三通道齐活、评审/决策/头脑风暴、开会讨论、Self-MoA、故障注入、auto 顶配

**核心创新点**:
1. **聚合者 = 持完整上下文的当前 agent**,而非再调无上下文 API(MoA 论文原意)
2. **同源去重**(synthesis.md 硬规则 1):防共识置顶放大群体思维
3. **三重反从众对冲**(发言序轮转 + changed_by_new_argument + 收尾盲投漂移检测)
4. **开会讨论 L3 三硬门**(L3 + 根本分歧未化解 + 用户显式要),"给结论的冲动"不压过"用户显式要求"
5. **quorum 宽限窗 + 立即返回**:不算最慢,不算最拖累 wall
6. **dry-run 成本估算 + subagent 静默换计费**警示(dry-run 必显式打 ⚠)
7. **Sensitive material 自动告警 + leak-check 静态自查**双层防护

**最值得借鉴的工程实践**:
- `dispatch_with_quorum` 立即返回(不 join 落伍线程)的实现
- `shutdown(wait=not abandoned)` 的细节
- `_effective_billing` 修正 dry-run 少报 bug
- `0 文件扫描 → 退出码 2` 区别于 clean / 命中
- `_safe_name` 防路径穿越 + 文件覆盖检测
- `bump-version.sh` 4 处版本号同步
- `refine/discuss` 禁止静默回退示例配置(P1-2)
- `P0/P1/C` 编号的 bug 跟踪文化(代码里到处都是 `修 P0-1 / C2 / 修 C4` 注释)
