# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [4.2.0] — 2026-08-30 — 自主编排引擎回移植 + 盲审安全加固

> 主线：从 GitHub v3.2.1（Nurburgring-Zhang/moa-gateway-pro）回移植自主编排
> 引擎，收敛 v4.1.0 双盲审核的全部 MEDIUM 发现。

### 新增 — GitHub 主线回移植
- **M13 自主编排引擎** `moa_gateway/orchestrator/`（O1–O6，1898 行 + 42 测试）：
  能力注册表（真实枚举 agent loop / workflow / MCP / channels / dispatch 全部能力）、
  任务分析器、能力组合 DAG 规划器、编排执行器（非特权 loop 永不注册危险工具的
  纵深防御）、结果强化器（能力评分持久化）、Skill 工厂（技能热部署 + 名称碰撞/
  危险导入/语法校验三重闸）；HTTP：`/v1/orchestrator/*`；诚实政策与全局
  X-MOA-Mock 标注体系同构（无 key 时显式标注 mock，绝不假执行）
- v4.1 缺口适配：`DANGEROUS_TOOLS`/`BUILTIN_TOOL_NAMES` 名称碰撞守卫自
  v3.2.1 移植进 skill_factory/executor（import 时冻结，防热部署自碰撞）

### 加固 — v4.1.0 双盲审核发现（甲乙双 APPROVE_WITH_FINDINGS）
- **F-1（甲）**：`subagent_routing/runner.py` 落地真实 ModelPool 执行器，
  lifespan 按 `function_call` 开关注册——`invoke_lite_subagent` 从永久
  dry-run 变为真实执行
- **M-2（乙）**：Telegram / 飞书 / Discord webhook 验签密钥未配置时改
  **fail-closed**（未认证输入不再驱动 chat 管道，与钉钉/企微对齐），
  新增 3 例 fail-closed 回归守卫

### 版本
- moa-gateway-pro 4.2.0（wheel/sdist 同步构建）；desktop 4.2.0；mobile 1.2.0（versionCode 3）

## [4.1.0] — 2026-08-26 — 三项目能力集成（OmniRoute / OpenClacky / MemoraX Code）

> 版本主线：把三个 MIT 开源项目的生产级能力移植进网关，全部真实实现、
> 全部测试覆盖、全部能力开关可关、全部默认不改变既有流量。
> 许可归属见 THIRD_PARTY_NOTICES.md。

### 新增 — OmniRoute 集成（https://github.com/diegosouzapw/OmniRoute，MIT）
- **M1 路由策略引擎** `moa_gateway/routing_strategies/`：20 个策略真实落地
  （priority / weighted / round-robin / context-relay / fill-first / p2c /
  random / least-used / cost-optimized / reset-aware / reset-window / headroom /
  strict-random / auto（12 因子加权）/ lkgp / context-optimized /
  cache-optimized / fusion / pipeline / quota-share），统一
  candidate+context→排序/回退链接口；TelemetryStore 滚动遥测（延迟/错误率/成本）；
  `routing_fusion` 桥接注册进既有 MoA 策略注册表；
  HTTP：GET /v1/routing/strategies、POST /v1/routing/resolve（dry-run 排序）、
  GET /v1/routing/telemetry
- **M2 配额调度器** `moa_gateway/quota_scheduler/`：QuotaValue 遥测模型
  （来源优先级 provider_api > response_headers > configured > estimated）、
  响应头解析、quota_snapshots 持久化、自适应监控（60s→接近耗尽 15s，
  warn 0.80 / exhaust 0.95）、can_afford 闸门（fail-open 可配）、
  DRR + P2C 配额共享选择器；lifespan 接入自适应轮询循环；
  HTTP：GET /v1/quota(/status|/snapshots)、POST /v1/quota/check、POST /v1/quota/refresh
- **M3 堆叠压缩** `moa_gateway/compression/`：RTK（56 个 CLI/工具输出过滤器库）+
  Caveman（英文规则集 context/dedup/filler/structural/ultra）两级串联，
  模式 off/lite/standard/aggressive/ultra/rtk/stacked，保真度闸门（低于阈值回退原文）、
  cache_control 标记保留、hard_budget_chars 上限、按模式累计统计；
  HTTP：POST /v1/compression/compress、GET /v1/compression/modes、GET /v1/compression/stats
  （apply_to_chat 默认 false：绝不默认改动聊天流量）
- **M4 免费层目录** `moa_gateway/free_tiers/`：OmniRoute 456 条免费模型目录
  完整移植（转换脚本自 TS 源生成，条目数逐条核对），poolKey 池去重、regime 分类、
  provider/regime/名称过滤查询；HTTP：GET /v1/free-tiers、GET /v1/free-tiers/{key}
- **M5 A2A 协议** `moa_gateway/a2a/`：/.well-known/agent.json 运行时真实卡片 +
  POST /v1/a2a 完整 JSON-RPC 2.0（五类错误码/批量/notification）、
  5 个真实技能（chat-completion / model-list / health / routing-advice /
  cache-insight，全部内调网关真实管道）、任务状态机持久化 + TTL + 属主隔离、
  出站凭据消毒

### 新增 — OpenClacky 集成（https://github.com/clacky-ai/openclacky，MIT）
- **M6 Token 效率引擎** `moa_gateway/efficiency/`：双 ephemeral cache_control
  标记（末尾 2 条消息）、不可变 system prompt + system_injected 侧信道、
  Insert-then-Compress（真实抽取式压缩 + chunk 归档）、266s 空闲压缩调度器
  （<5min 缓存 TTL；150K tokens / 200 条阈值 → 10K 目标、保留最近 20 条）、
  缓存命中率指标；HTTP：POST /v1/efficiency/prepare、POST /v1/efficiency/compress-session、
  GET /v1/efficiency/metrics
- **M7 技能中心** `moa_gateway/skillhub/`：SKILL.md 加载器（YAML frontmatter）、
  内置技能包 + 扩展目录 + 用户目录、模糊搜索、invoke_skill 元工具（真实走
  ModelPool 管道）、自然语言建技能（LLM 路径 + 确定性回退，产出真实可用文件）、
  自进化钩子（使用统计 + 改进建议持久化）；HTTP 完整 CRUD：
  GET/POST /v1/skills、GET/PUT/DELETE /v1/skills/{name}、
  POST /v1/skills/search、POST /v1/skills/{name}/invoke、
  GET /v1/skills/{name}/stats、GET /v1/skills/evolution/suggestions
- **M8 IM 渠道层** `moa_gateway/channels/`：适配器抽象 + 诚实状态机
  （unconfigured/configured/running），Telegram / 飞书 / 钉钉 / 企业微信 /
  Discord 五平台真实协议实现（httpx，凭据走 MOA_* 环境变量，含平台验签）、
  会话路由持久化、UIController 写回（入站→真实 ModelPool→出站，file:// 噪声过滤）；
  HTTP：GET /v1/channels、GET /v1/channels/bindings、POST /v1/channels/{name}/send、
  POST /v1/channels/{name}/webhook
- **M9 轻量子代理路由** `moa_gateway/subagent_routing/`：fork 前缀检测、
  lite 模型映射注册 API、forbidden_tools 过滤、摘要折叠 + 成本合并；
  invoke_lite_subagent 工具已注册进 /v1/agent legacy 工具面
  （function_call 能力开关守卫）；HTTP：GET /v1/subagent/config、POST /v1/subagent/route

### 新增 — MemoraX Code 集成（https://github.com/memorax-ai/memorax-code，MIT）
- **M10 跨会话记忆层** `moa_gateway/memory/`：5 类记忆
  （core/episodic/semantic/procedural/unclassified）、作用域模型
  effective_user_id=f(base_user, repo_key)、三端点 hook 协议（turn-start /
  writeback / skill-reminder，fail-closed 键白名单）、混合召回
  （top_k=6、dense+sparse、min_semantic_similarity、4000 字符上下文预算、
  `<memories>` XML 渲染）、写回管道（turn 关联→PII 脱敏（邮箱/手机/身份证/
  银行卡 Luhn/API key/信用卡）→缓冲 8 轮/600s/128K→8K 分块 5% 重叠 group_id→
  幂等入库）；已接线 assistant runs（retrieval_enabled / writeback_enabled
  双开关，默认全关，记忆钩子永不破坏 run 路径）；
  HTTP：POST /v1/memory/turn-start|writeback|skill-reminder、
  GET /v1/memory/recall、GET /v1/memory/items、DELETE /v1/memory/items/{item_id}
- **M11 工作区记忆** `moa_gateway/workspace_memory/`：.moa_memory 目录结构、
  facet 脚本机制（真实子进程执行产出 markdown）、adaptive 更新策略（真实 diff）、
  supervisor 锁；HTTP：GET /v1/workspace-memory/status、POST /v1/workspace-memory/update

### 新增 — 管理面与交付物
- **M12 admin-ui**：routing / quota / compression / free-tiers / memory /
  skills / channels 七个管理页面（完整 CRUD + 配置 + 演练面板），
  next build 通过
- 能力开关新增 9 项（routing_strategies / quota_scheduler /
  stacked_compression / free_tiers / a2a / token_efficiency / skillhub /
  channels / memory），关闭即对应端点 503，状态持久化
- 配置：config.py 新增 9 个 pydantic 配置类，config.yaml 同步；
  compression.default_mode 修引号（YAML 1.1 裸 off→布尔 false 问题）
- 桌面端 4.1.0、移动端 1.1.0（versionCode 2）；交付 APK + Windows NSIS/便携版

### 验证
- v4.0.0 基线 1435 测试零回归 + 新增测试（最终数字见 DELIVERY_REPORT_v4.1.md）
- 诚实扫描（stub/mock/fake/placeholder/TODO/FIXME）全部新模块零命中
- 装配冒烟：273 个端点路径，31 个 v4.1 新路由全部注册，routing_fusion 自动注册

## [4.0.0] — 2026-08-21 — 十一轮审计 P 项清零 + 交付物固化

### 修复 — 多模态接线缺陷（写了没接线 / 空 key 硬编码）
- **P0 图像生成空 key**：`routes/vision.py` `/v1/images/generations` 原先硬编码
  `api_key=""` 调 `build_multimodal_provider`，配了真 key 也永远走 MockImageProvider。
  现按 platform 从 env 读真实凭据（ZHIPU_API_KEY→cogview/zhipu、OPENAI_API_KEY→openai、
  WANX_API_KEY/DASHSCOPE_API_KEY→wanx，含 *_API_BASE 覆盖；占位 key 按 is_mock_key
  视为未配置），`auto` 优先选有 key 的平台；无 key 才按 `settings.mock.mode`
  走 explicit（200+X-MOA-Mock）/ disabled（503）既有分支
- **音乐生成接线**：`music_generation_provider.py`（MiniMax/天工）原无路由。
  新增 `routes/music.py`：`POST /v1/audio/music`（异步任务）+
  `GET /v1/audio/music/tasks/{task_id}`，读 MINIMAX_API_KEY/TIANGONG_API_KEY，
  require_api_key + 任务属主校验 + mock 策略（新增 MockMusicProvider，
  mock 查询仅认本网关创建的任务，任意 id 404）；请求/响应模型进
  `req_models.py`（extra=forbid）；能力开关新增 `music`
- **专用 ASR/TTS 接线**：`routes/audio.py` `/v1/audio/transcriptions` 与
  `/v1/audio/speech` 新增 provider 参数（auto/openai/dashscope/iflytek），
  auto 按可用 key 优先级选（WHISPER/OPENAI → IFLYTEK → 专用 DASHSCOPE_ASR/TTS key），
  无专用 key 时保留 ElevenLabs/开源标注 mock 旧路径；显式 provider 无 key 时
  按 mock.mode explicit（标注 mock 200）/ disabled（503）。ElevenLabs 编辑/克隆路径不变
- **Kling 视频生成接线**：`video_generation_provider.py` KlingVideoProvider 原无调用方。
  `routes/video.py` 新增 `POST /v1/video/generations`（platform=auto/kling/runway）+
  `GET /v1/video/generations/tasks/{task_id}`，kling 读 KLING_API_KEY，与既有
  runway 路径并存；Kling 任务状态归一化为 processing/completed/failed
- `.env.example` 补全上述全部 env（KLING/RUNWAY/TRIPO3D/MESHY/SD/MINIMAX/TIANGONG/
  WHISPER/DASHSCOPE(_ASR/_TTS)/IFLYTEK 及 *_API_BASE），逐项注明对应能力端点
- 测试 `tests/test_multimodal_wiring.py`：42 例（路由注册/OpenAPI 可见性、
  有 key→真实 provider（无 X-MOA-Mock）、无 key×explicit→200+X-MOA-Mock、
  无 key×disabled→503、mock 任务 404、状态归一化、占位 key 视为未配置）

### 新增 — 多 AI 同框对话（Dialogue Rooms）
- 新模块 `moa_gateway/dialogue/`：多个 AI 参与者在同一对话房间内围绕主题实时发言，
  所有发言均为 `model_pool.call` 真实 LLM 调用（无真实 key 时按 `settings.mock.mode`
  走 MockProvider 并显式标注 `mock=true`，延续 D6 显式 mock 政策）
- 三种编排模式：
  - `round_robin` 轮流发言：每轮每个参与者按序调用，上下文=完整共享历史（含其他 AI 发言，标注发言者）
  - `parallel_think` 并行思考：`asyncio.gather` 所有参与者并行调用，全部返回后汇总进历史（每条独立可见）
  - `free_talk` 自由讨论：主持人 LLM 每轮输出 JSON `{speaker, reason}` 决定下一位发言者，
    连续 3 轮无进展或同一发言者连续独白自动收敛
- 每轮 `max_rounds` 上限 + 单参与者超时；调用失败记录真实失败证据（status=error/timeout），绝不伪造内容
- `dialogue/storage.py`：rooms/messages 持久化（DatabaseEngine 工厂，SQLite WAL），重启可恢复，按房间分页查历史
- 路由 `routes/dialogue.py`（全部 `require_api_key` + POST 端点 per-key 限流）：
  `POST/GET /v1/dialogue/rooms`、`GET/DELETE /v1/dialogue/rooms/{id}`、
  `POST /v1/dialogue/rooms/{id}/messages`（用户发言触发一轮多 AI 响应）、
  `GET /v1/dialogue/rooms/{id}/stream`（SSE 逐参与者推送，provider 支持流式时逐 token delta）
- 事件流格式：`{room_id, round, speaker, delta/final, status, mock}`，带环形缓冲支持 SSE 回放
- 请求模型进 `req_models.py`（`extra=forbid`）；`server.py` 注册 `dialogue_router`
- 测试 `tests/test_dialogue.py`：46 例（三模式行为、房间 CRUD、持久化恢复、失败/超时证据、
  max_rounds、事件格式、鉴权、SSE 回放、端到端 2 参与者真实产出）

### 安全 P0 — SSRF 编码 IP 归一化改为平台无关（v3.1.1 遗留绕过）
- `utils/url_validator.py`：编码 IP 字面量的识别与归一化不再委托 `socket.getaddrinfo`
  （其解释平台相关：Windows 把 `http://2130706433/` 解析到公网地址导致放行）。
  现在在 DNS 解析之前用自实现的 inet_aton 语义归一化：纯十进制整数（2130706433）、
  十六进制（0x7f000001、0x7f.0x0.0x0.0x1）、八进制（0177.0.0.1）、混合点分短式
  （127.0.1、0x7f.1）、全部 IPv6 文本形式（::1、::ffff:127.0.0.1、::ffff:7f00:1、
  压缩/展开式），归一化后直接走 `_ip_is_dangerous` 判定
- 补堵 IPv4-compatible IPv6（::/96）：Python ipaddress 标志位视其为公网，
  实际可达内嵌 IPv4（::7f00:1 即 127.0.0.1），整段拉黑
- 补堵前导零八/十进制双解歧义（010.010.010.010、02130706433）：任一种解读
  落入危险段即拦截，两种解读均为公网才放行（如 01.1.1.1）
- 归一化失败且非合法域名形式 → fail-closed 拦截（如 127.0.0.256、1.2.3.4.5）；
  合法普通域名行为不变，仍走 DNS 全解析检查
- `tests/test_v311_fixes.py`：TestSSRFValidator 新增 19 个编码变体拦截用例 +
  6 个公网放行反例（8.8.8.8、example.com 等），既有用例期望不变

### 修复 — 收尾审计新发现（P7–P10 清零过程中的真实缺陷）
- **CLI 入口路由 bug**：`python -m moa_gateway --port 8088` 原会把 `--port`
  当子命令透传给 `cli.main`，报 `invalid choice: '--port'`。`__main__.py` 改为
  先判别首参：非已知子命令（chat/run-moa/models/discover/prompts/mcp/config/
  params/workflow/setup/ask）一律按 serve 处理，argparse 解析 `--host/--port/--workers`
  后启动 uvicorn；已知子命令仍委托 `cli.main`
- **多模态全失败证据丢失**：`task_pipeline.py` 原先 multimodal fanout 全败时抛裸
  `RuntimeError`，FanoutResult 里的逐平台失败证据被丢弃，supervisor 自愈也无从利用。
  新增 `MultimodalAllFailedError`（携带 `result.to_dict()`），`_run_one` 捕获后把
  证据写入 `task.output`；`_heal_reroute` 改为剔除永久不可用平台（no_key /
  skipped_mock_unavailable）后重试，永久不可用且无可重试平台时保持 failed 终态并留证
- **新端点缺 per-key 限流**：`routes/multimodal.py`、`routes/task_pipeline.py`
  补齐与 chat/moa/dialogue 一致的 `get_limiter().check_and_incr` 鉴权限流
- **WebUI 重复 escapeHtml**：`index.html` 存在两个 `escapeHtml`（行 1451 版本不完整，
  未转义引号），删除之，保留行 2277 完整版；`node --check` 通过，单 `<script>` 标签
- **真实冒烟（P7-4）**：真实启动 uvicorn（端口 18910，真实 API key 鉴权），
  6/6 通过：WebUI 加载、房间创建/查询/列表/删除、SSE 流连接（keep-alive 帧）
- 新增测试：`tests/test_cli_basic.py` +2（入口路由 serve 标志 / 已知子命令委托）、
  `tests/test_task_pipeline.py` +2（自愈剔除坏平台 / 永久不可用保持 failed）
- 全量回归 **1435 passed, 0 failed**（干净重跑，含本轮全部修复，详见 RELEASE_NOTES_v4.0.md）；
  版本 3.1.1 → 4.0.0

## [3.1.1] — 2026-08-16 — 十轮全量审计修复（P0/P1 清零）

对 v3.1.0 执行十轮全量审计（243 端点扫描、6 路并行深审、本地真实 LLM 链路注入、
浏览器 E2E、chaos 故障注入、对抗性复审），修复全部 P0 与 P1，以及对抗复审二轮新发现项。
测试基线由 593 提升至 **1071 passed, 0 failed**。

### P0 — Agent 沙箱逃逸（RCE）
- 重写 `agent_loop/skills/code_execute.py` + 新增 `agent_loop/sandbox_exec.py` 子进程隔离执行
- AST 层：全 dunder 属性封禁、subscript 字符串键封禁、format 属性遍历封禁、**任意含 dunder 的字符串字面量封禁**
- 导入白名单移除 `operator`（attrgetter/methodcaller）与 `string`（Formatter）——二者可在运行时走属性遍历绕过 AST 封禁
- 运行时兜底：注入模块统一包 `_ModuleProxy`，封禁一切 dunder 动态访问（拦截 chr() 拼接的 format 攻击）
- 危险工具（code_execute/file_read/file_write/file_list/api_verify）仅 admin/operator 可用，API-key 用户 403

### 安全 P1
- **secret-scan**：提权 require_admin + commonpath 限定项目/数据目录 + Finding 源头脱敏（不再回显密钥原文）
- **in-flight**：忽略调用方 state_dir，固定 `DATA_DIR/in_flight_state`（封堵任意目录写原语）
- **health restore/purge**：提权 require_admin，restore 用 EndpointUpsert 严格校验
- **moa prompts PUT/DELETE**：提权 require_admin（封堵跨租户提示词注入）
- **SSRF 统一强化**（`utils/url_validator.py`）：DNS 解析全部落地地址、编码 IP（十/八/十六进制）、
  内部域名、IPv4-mapped IPv6，fail-closed；**显式拉黑 IANA 特殊段含 RFC 6598 CGNAT 100.64.0.0/10
  （阿里云元数据 100.100.100.200 所在段）**；api_verify 与 MCP 外部注册统一委托

### 诚实性 P1（D6 显式 mock 政策闭环）
- MoA 全链路 provider 追踪（ReferenceResult/CriticResult.provider + MoAResult.mock_used），
  `/v1/moa/execute` 返回 `X-MOA-Mock` 头 + `mock` 字段
- MoA 渐进流式补 mock 头（`predict_stream_mock`）
- channels / reference-router 端点显式 mock 标注（头 + `mock:true` + mock_note）
- MoA 参考模型全失败 → 显式 `ProviderError(502)` 并保留逐模型失败证据，**不再静默降级**
- **缓存命中重放 mock 标注**：缓存条目携带 mock 信封，HIT 时重放 `X-MOA-Mock`（修复 mock 输出经缓存后丢标注）

### 功能 P1
- **服务层死方法清零**：修复 quota/routing/quality/knowledge/config/consensus/agent/safety/moa/observability
  共 60+ 处 ImportError/签名错配（含 self_heal promote/demote 错接线），全部改走真实 capability 实现
- **GDPR 被遗忘权真实生效**：路由传真实 db_conn、改删 `admin_users`、按 key_id 解析后匿名化日志；
  匿名化改**加盐 HMAC-SHA256（盐即弃，不可彩虹表还原）**；删除后清理内存 user_id 残留、审计日志不再留存明文 user_id
- **流式配额计费**：`stream=true` 计入每日 token 配额（封堵 stream 绕过计费）
- **MoA 高耗端点限流**：similarity/flask/benchmark/cost-pareto 补 RPM 检查 + token 计费
- **请求模型真实类型化**：85 个 req_models 改真实类型 + `extra=forbid`（未知字段 422）

### 打包与版本
- wheel 补数据文件：prompts/、workflows/builtin/、webui/、param_templates/、migrations（v3.1.0 的 wheel 为零数据文件）
- 版本号四处统一 3.1.1：`__init__.py` / `pyproject.toml` / server.py openapi / routes/health.py
- 前端 admin-ui 会话修复：硬刷新/深链不再丢登录态（请求时 localStorage 兜底读 token + 模块加载即恢复）

### 测试
- 新增 `test_sandbox_escape.py`、`test_v311_fixes.py`、`test_v311_round2.py`、`test_service_methods_real.py` 等
- 全量 **1071 passed, 0 failed**；活体 41/41 + 二轮 4/4；前端 E2E 硬刷新 6/6

## [Unreleased] — v2.1.x

### v2.1.0 (2026-08-06) — 全链路真实化（Wave B1–B5）

#### Wave B1 — 基础修复
- **D1 HMAC 签名链修复** — `audit.py` 改用 `settings.auth.jwt_secret`，移除 type-ignore hack，补签名/验签往返测试
- **D11 config.yaml 乱码清理** — GBK/UTF-8 混写行统一 UTF-8（无 BOM/LF）
- **D9 tri-review 配额对齐** — 三模型互审执行后按真实消耗 `incr_tokens` 记账，记账失败不吞结果仅告警
- **D8 Agent 沙箱收紧** — 沙箱根由 cwd 改为 `data/agent_sandbox`（自动创建 + 路径逃逸校验）

#### Wave B2 — Mock 显式化 + Purge 自毁拆除
- **D6 Mock 显式化** — `mock: {mode: explicit|disabled}` 配置；所有 mock 响应注入 `X-MOA-Mock: true` 头 + usage `mock=true`；mode=disabled 时无 Key 调用返回明确 503，严禁静默模拟
- **T2.4 /health 展示 mock 规模** — 返回 `mock_endpoints_count` / `real_endpoints_count` / `mock_mode`
- **D3 Purge 自毁拆除** — 首轮 purge 延迟 `purge_initial_delay_seconds`（默认 86400s）；探测/淘汰跳过 mock 端点；`purge_records` 快照恢复机制（快照永不含 API Key）

#### Wave B3 — 数据流转接线
- **D2/D12 内部回调鉴权** — YAML 工作流 `_http_post` 与 Assistant executor 回调统一注入网关 admin Key，修复 401 断链
- **D5 Discovery 接线** — `FreeModelDiscoveryEngine` 注入 `settings.discovery.api_keys`
- **D4 空壳指标接线** — `record_llm_request` / `record_cache_access` 等接入真实调用点，/metrics 非零

#### Wave B4 — Agent/任务系统激活
- **D7 Agent 计量真实化** — `LlmUsage`/`LlmOutcome` + `normalize_llm_outcome()`；ReAct 局部累加；PlanExecute 改用每次 run 新建的 `_UsageAcc` 显式传参，杜绝并发串数
- **D12 Runs 健壮化** — `asyncio.wait_for` 超时（先重读落盘终态防覆写）、`_active_run_ids` 409 并发防重、lifespan 僵尸 run 清扫、submit_tool_outputs 状态翻转先持久化再入队
- **D13 TaskBoard 持久化** — SQLite `agent_tasks` 表 + `/v1/agent/tasks` CRUD；分页 `has_more/total`、显式 null 取消指派（`clear_assignee` 哨兵）

#### Wave B5 — Tracer 接线与总回归
- **T5.1 Tracer 接线** — HTTP 中间件 root span 之下注入 `model_pool.call` / `workflow.step` / `moa.execute` / `moa.tri_review` / `assistant.run` / `assistant.submit` / `assistant.llm_call` / `agent.run_loop` 子 span；`create_span` 区分显式 None（无父 root）与省略（继承上下文）；root span 的 span_id 与 `X-Span-ID` 响应头一致；`observability.otlp_endpoint` 配置项 + lifespan 按 `trace_enabled` 启用
- **T5.2 总回归** — 509+ 测试全过、ruff/mypy 零告警、约 30 端点真实 uvicorn 冒烟矩阵

## [Unreleased-old] — v1.8.x

### v1.8.1 (2026-07-19) — OpenAPI field descriptions + endpoint signature cleanup
- **Pydantic Field descriptions (401 fields)** — `_gen_descriptions.py` 给 84 个 Pydantic model 的字段加中文 description,Swagger UI 直接展示字段含义
- **5 dead `request: Request` removed** — `chat_completions` / `moa_execute` / `route_preview` / `quota` 端点签名不再注入无用 Request
- **`get_client_ip` dependency** — `login` 改用 `Depends(get_client_ip)` 抽离 IP 提取,支持 X-Forwarded-For
- **`list_models` 保留 `request: Request`** — 因 `authenticate_api_key(request)` 真用 Authorization header
- **`_raw_payload` → `raw_payload`** — Pydantic 不允许前导下划线字段名
- **Deep E2E 客户端改长连接池** — `urllib.request.urlopen` 换成 `http.client.HTTPConnection` 复用,解决 Windows ephemeral port 1000 上限的 TIME_WAIT 撞池

### v1.8.0 (2026-07-18) — Pydantic BaseModel for 83 endpoints + 90 OpenAPI schemas

## [v1.7.0] — 2026-07-18 — Production Architecture (5 rounds of fixes)

### Round 1: P0/P1/P2 + 80 deep e2e fails → all fixed
- **Global exception handlers** — TypeError/ValueError/KeyError/AttributeError/JSONDecodeError → 4xx
- **43× `HTTPException(500)` → `_err_500()` smart mapper** — input errors → 4xx, real server errors → 500
- **Aggregator.from_dict added** + **BOM stripped** + **duplicate `except HTTPException: raise` removed** (32 dup clauses)
- **P0-11 per-endpoint async lock** for `_saved_api_key` race condition
- **P0-12 chat_completions fallback recheck** on endpoint removal race
- **P1-2 worktree `__import__("os")` cleanup** → direct `os.environ`
- **P1-9 login rate limit** — IP-based, 10 attempts per 60s, new `login_attempts` table
- **moa-n-layer query type validation** — int query → 422
- **moa-3-layer 422 validation** for invalid proposer/aggregator
- **DEEP E2E RESULT**: 432/512 → 512/512 pass (0 fail)

### Round 2: Service Layer + AgentDispatch + Workflow Engine
- **`services/base.py`** — `ServiceBase`, `ServiceMethod`, `ServiceRegistry`, `service_method` decorator
- **`services/dispatcher.py`** — `AgentDispatcher` + `Workflow` + `WorkflowStep` (DAG executor with real inter-module data flow)
- **7 endpoints under `/v1/agent/*`** — list, dispatch, dispatch_batch, workflows, workflow/run, workflow/register

### Round 3: 10 services + 100 methods
- **MoAService** (4): `run_three_layer`, `run_engine`, `cross_iter`, `validate_config`
- **ConsensusService** (7): `vote_ensemble`, `should_rebalance`, `detect_convergent`, `arbitrate_conflicts`, `synthesize_multi_mode`, `check_group_think`, `evaluate_section_viability`
- **RoutingService** (6): `route`, `chain_info`, `execute_chain`, `classify_error`, `cost_estimate`, `reference_route`
- **QualityService** (7): `score_flask`, `rank_elo`, `gate_l0`, `score_panel`, `brainstorm`, `plan_act`, `meta_prompt`
- **AgentService** (18): comms, session_lock, bubble, MCP (subagent_comms, try_acquire, escalate, etc.)
- **QuotaService** (24): rate_quota, per_provider_rl, token_bucket, request_dedup, self_heal, tier_recalibrate, tier_promo, provider_health, consumption_intel, should_rebalance, cost_estimate
- **KnowledgeService** (12): embed, semantic_search, rag_search, fuzzy_dedup, input_fingerprint, rerank, distill, importance, context_clean, turboquant, prompt_features, goal_eval
- **SafetyService** (10): secret_scan, prompt_canary, tool_screening, output_wrapping, frozen, grace, anthropic_compat, llm_merge, version, worktree
- **ObservabilityService** (4): trace, audit, hook_events, in_flight
- **ConfigService** (8): config, mx, checkpoint, artifact, acceptance, action_policy, tool_replay, brainstorm_decide

### Round 4: CapabilityDispatcher (76 capability passthroughs)
- **`services/capability_dispatcher.py`** — single service with 76 `call_<endpoint>` methods
- **All capability endpoints** accessible via `service=capability, method=call_<endpoint>`

### Round 5: 7 builtin workflow templates (all pass with real data flow)
- `moa_quality_pipeline` — validate → run_moa → score_flask (3 steps, 8.9ms)
- `consensus_pipeline` — detect_convergent → vote_ensemble (2 steps, 7.6ms)
- `quality_gate` — gate_l0 → brainstorm (2 steps, 9.1ms)
- `knowledge_pipeline` — embed → semantic_search → rerank (3 steps, 2.4ms)
- `quota_check` — cost_estimate → provider_health → should_rebalance (3 steps, 3.7ms)
- `safety_pipeline` — gate_l0 → tool_screening → output_wrapping (3 steps, 11.4ms)
- `rag_pipeline` — rag_search → rerank (2 steps, 0.9ms)

### Round 6: Production deployment
- **`Dockerfile`** — multi-stage Python 3.11-slim
- **`docker-compose.yml`** — production compose with healthcheck, resource limits, log rotation
- **`.dockerignore`** — exclude test files, caches, secrets
- **`DEPLOYMENT.md`** — comprehensive deployment guide (Linux/macOS/Windows/Docker/K8s)
- **Performance test** (`test_perf.py`):
  - Sequential `/health`: 1000 reqs, p50=0.81ms, p99=23.27ms
  - Concurrent `/health`: 200 threads × 10 = 2000 reqs in 0.28s → **7193 RPS**
  - Concurrent `/v1/agent/dispatch`: 50 threads × 5 = 250 dispatches

## [v1.6.6] — 2026-07-15 — Deep E2E catch-up (4 critical bugs)

## [1.6.6] — 2026-07-15 — Deep E2E catch-up

### Fixed
- **goal-eval** (server.py:1841) — schema mismatch: server sent wrong fields to `Goal()`
  - Goal's actual fields are `(id, description, tier, criteria, evaluator_fn)`
  - Server now maps input to right fields with sensible defaults
- **goal-eval** (server.py:1855) — `generate_ceiling=True` crashed when baseline/residual_risk empty
  - Now defaults to placeholder text when fields missing
- **per-provider-rl** (server.py:1373, 1380) — used `mpl.limiters[provider]` (does not exist)
  - `MultiProviderLimiter` only has private `_limiters`; now uses `mpl._get(provider)`
- **task-tree** (server.py:1714) — `else: tree = TaskTree(root_id='root')` wiped out the built tree
  - Removed the bogus override that was discarding the actual constructed tree
- **moa-n-layer** (server.py:980) — validation failures wrapped as 500
  - Added explicit 400 checks for `proposers` non-empty and `aggregators` count = 3

### Added
- **43 4xx pass-through** (server.py) — auto-patched via `scripts/_patch_4xx.py`
  - Added `except HTTPException: raise` before every `except Exception` block that wraps 500
  - Inner code's 4xx now propagates correctly (no longer wrapped as 500)
- `scripts/test_deep_e2e.py` — 509 cases / 76 endpoints / 11 phases deep E2E
  - Data-driven test that catches production bugs basic E2E misses
- `scripts/_patch_4xx.py` — auto-applies 4xx pass-through fix
- `scripts/_fix_patch_order.py` — reverts bad patch order

### Test results
| Suite | v1.6.5 | v1.6.6 |
|-------|--------|--------|
| Unit tests | 1980 | 1980 |
| E2E (basic) | 137 | 137 |
| Deep E2E | 75 fail | **65 fail** (-10) |
| Server routes | 80 | 80 |

## [1.6.5] — 2026-07-14 — Wave 13 (5 new HIGH)

### Added
- **Wave 13 — 5 new HIGH capabilities** (229/229 tests pass)
  - `tool_screening.py` — 9-segment tool input risk detection (SQL/shell/path/code/prompt/URL/file/network/privesc), 50+ patterns, 5 risk levels (59 tests)
  - `anthropic_compat.py` — Anthropic Messages API compatibility (parse/format_response/SSE/tool_use/tool_result) (45 tests)
  - `token_bucket.py` — Token bucket rate limit (lazy refill, multi-key LRU 10000) (47 tests)
  - `request_dedup.py` — Request dedup with EXACT/NORMALIZED/SEMANTIC strategies + response cache (41 tests)
  - `trace.py` — W3C `traceparent` format, span tree, TraceCollector with LRU (37 tests)
- **5 new endpoints**:
  - `POST /v1/capability/tool-screening`
  - `POST /v1/capability/anthropic-compat`
  - `POST /v1/capability/token-bucket`
  - `POST /v1/capability/request-dedup`
  - `POST /v1/capability/trace`

### Fixed
- `scripts/pack_zip.py` — GBK encoding crash on Windows console (replaced `✓` with `[OK]`, `reconfigure stdout to utf-8`)

### Test results
| Suite | v1.6.4 | v1.6.5 |
|-------|--------|--------|
| Unit | 1751 | **1980** (+229) |
| E2E | 126 | **137** (+11) |
| Security regression | 12/12 | 12/12 |
| Server routes | 75 | 80 (+5) |

## [1.6.4] — 2026-07-14 — Wave 12 + 5 P0 + 2 P1

### Added
- **Wave 12 — 5 new HIGH capabilities** (210/210 tests pass)
  - `audit_cache.py` — LRU + TTL 24h audit event cache (36 tests)
  - `prompt_canary.py` — 4 strategies (SUFFIX/PREFIX/INVISIBLE/MULTI) + 18 injection patterns (48 tests)
  - `output_wrapping.py` — `<untrusted_tool_output>` tags + XML escape (34 tests)
  - `fuzzy_dedup.py` — simhash 64-bit local-sensitive hash (38 tests)
  - `input_fingerprint.py` — 4-layer hash fingerprint + collision detect (54 tests)
- **5 new endpoints**

### Fixed (P0 + P1 from v2 bug hunt)
- **P0-6** `feedback-iter` — RCE via `history_path`; now `require_admin` + path allowlist
- **P0-8** `worktree` — `subprocess.run` no timeout; added `timeout=10s` + `GIT_OPTIONAL_LOCKS=0`
- **P0-9** `_stream_single` — provider race; copies `provider = ep.provider_obj` before stream
- **P0-10** `_pending_close` unbounded — `deque(maxlen=100)` + background `_close_pending_loop`
- **P1-13** `change_password` — bcrypt 300ms blocking event loop; now `asyncio.to_thread`

### Test results
| Suite | v1.6.3 | v1.6.4 |
|-------|--------|--------|
| Unit | 1541 | **1751** |
| E2E | 115 | **126** |
| Server routes | 70 | 75 |

## [1.6.3] — 2026-07-14 — 9 security patches (P0 RCE prevention)

### Fixed (5 P0 + 4 P1)
- **P0-4** `checkpoint` — RCE via `atomic_write` action; now `require_admin` + removed
- **P0-5** `worktree` — RCE via `.gitconfig`; now `require_admin` + cwd allowlist
- **P0-1** `incr_rpm` race condition — now `BEGIN IMMEDIATE` atomic
- **P0-2** `_rebuild_provider` resource leak — uses `_pending_close` queue
- **P0-3** Fernet key TOCTOU — `O_CREAT|O_EXCL` + singleflight
- **P1-1** `incr_tokens` permanent lockout — check before increment
- **P1-2** health check dead code — `is not None` not `isinstance`
- **P1-3** JWT detection — strict regex not `count('.')==2`
- **P1-5** webui path traversal — `os.path.commonpath` not `startswith`
- **P1-6** token length limit — max 256 + multi-value header handling
- **P1-7** chat_completions recheck — endpoint exists after router returns

### Added
- `scripts/test_security_regression.py` — 12 security regression tests
- `BUG_HUNT_REPORT.md` — full 5 P0 + 7 P1 audit

## [1.6.2] — 2026-07-14 — Wave 11 (5 HIGH) + 2 patches

### Added
- **Wave 11 — 5 new HIGH capabilities** (173/173 tests pass)
  - `rag_search.py`, `plan_act.py`, `channels.py`, `reference_router.py`, `checkpoint.py`

### Fixed
- `ratelimit.py` — per-key `quota_rpm` now respected (was using global)
- `ratelimit.py` — admin JWT no longer KeyError on `/v1/quota`
- `server.py` — 4xx errors no longer wrapped as 500 (16 routes fixed)
- `feedback_loop.py` — panel_scores dict keys coerced to int
- `model_pool.py` — first-iter health check runs immediately (no 30s wait)

## [1.6.1] — 2026-07-13 — 4 production bug fixes

### Fixed
- `secret_scan.py` — relative path bug
- `model_pool.py` — JWT routing
- `server.py` — bcrypt async
- `pack_zip.py` — exclude `zip/` to prevent recursion (19 GB → 5.8 MB)

## [1.6.0] — 2026-07-13 — First production release

### Added
- 7 P0 capabilities
- 50 HIGH capabilities (Wave 1-10)
- 80+ server routes
- 1300+ unit tests
- 115+ E2E tests
- GitHub release with zip asset

---

## Cumulative metrics

| Version | Capability modules | Unit tests | E2E tests | Server routes |
|---------|-------------------|------------|-----------|---------------|
| v1.6.0  | 57 (7 P0 + 50 HIGH) | 1300+ | 115 | 70+ |
| v1.6.1  | 57 | 1300+ | 115 | 70+ |
| v1.6.2  | 62 (7 P0 + 55 HIGH) | 1541 | 115 | 70+ |
| v1.6.3  | 62 | 1541 | 115 | 70+ |
| v1.6.4  | 67 (7 P0 + 60 HIGH) | 1751 | 126 | 75+ |
| v1.6.5  | 72 (7 P0 + 65 HIGH) | 1980 | 137 | 80+ |
| v1.6.6  | 72 | 1980 | 137 (deep: 65 fail) | 80+ |

## Upcoming (v1.6.7+)

- **Pydantic validation** for 30 endpoints (replace `body: Dict[str, Any]`)
- **Wave 14** — 5 more HIGH capabilities
- **P1 🔸 medium 109** — long-term investment
- **v1.6.5 deferred P0/P1** — rag_search aiosqlite + storage conn pool + LRU caps
