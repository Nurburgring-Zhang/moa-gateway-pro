# MoA Gateway Pro v4.1.0 — 三项目能力集成版（OmniRoute / OpenClacky / MemoraX Code）

**Date:** 2026-08-26
**Status:** Production-grade — 基线 1435 + 新增 495 + 收口补齐 9（free-tiers
HTTP 面）共 **1939 测试全绿零回归**（2026-08-29 终版复跑 1939 passed /
0 failed / 871.59s 实证），装配冒烟 271 端点路径 / 34 个 v4.1 新路由实测注册
（宣称复核与 M4 补齐见验证结果节）

v4.0.0 之后，本版把三个 MIT 开源项目的生产级能力完整移植进网关：
OmniRoute（路由/配额/压缩/免费层/A2A）、OpenClacky（token 效率/技能/IM 渠道/
子代理路由）、MemoraX Code（跨会话记忆/工作区记忆）。全部真实实现、全部
测试覆盖、全部能力开关可关、默认不改变任何既有流量行为。许可归属见
`THIRD_PARTY_NOTICES.md`（三项目 MIT 许可证全文 + 版权人 + 移植组件清单）。

## 新增能力（M1–M12）

### OmniRoute 集成
- **M1 路由策略引擎**（`moa_gateway/routing_strategies/`）：20 个策略真实落地，
  统一 candidate+context→排序/回退链接口；TelemetryStore 滚动遥测；
  `routing_fusion` 注册进既有 MoA 策略注册表；HTTP：/v1/routing/*
- **M2 配额调度器**（`moa_gateway/quota_scheduler/`）：QuotaValue 遥测模型
  （provider_api > response_headers > configured > estimated）、自适应监控
  （60s→接近耗尽 15s，warn 0.80 / exhaust 0.95）、can_afford 闸门（fail-open 可配）、
  DRR + P2C 配额共享选择器；lifespan 接入自适应轮询循环；HTTP：/v1/quota/*
- **M3 堆叠压缩**（`moa_gateway/compression/`）：RTK（56 个工具输出过滤器）+
  Caveman（5 英文规则集）两级串联，7 档位模式，保真度闸门回退、
  cache_control 保留、按模式统计；HTTP：/v1/compression/*
  （apply_to_chat 默认 false，绝不默认改动聊天流量）
- **M4 免费层目录**（`moa_gateway/free_tiers/`）：456 条免费模型目录完整移植
  （转换脚本条目数逐条核对），poolKey 去重、regime 分类、过滤查询；
  HTTP：/v1/free-tiers/*
- **M5 A2A 协议**（`moa_gateway/a2a/`）：/.well-known/agent.json 运行时卡片 +
  POST /v1/a2a 完整 JSON-RPC 2.0，5 个真实技能全部内调网关管道，
  任务状态机持久化 + TTL + 属主隔离 + 出站凭据消毒

### OpenClacky 集成
- **M6 Token 效率引擎**（`moa_gateway/efficiency/`）：双 ephemeral cache_control
  标记、不可变 system prompt、Insert-then-Compress、266s 空闲压缩调度器、
  缓存命中率指标；HTTP：/v1/efficiency/*
- **M7 技能中心**（`moa_gateway/skillhub/`）：SKILL.md 加载器、内置技能包、
  模糊搜索、invoke_skill 元工具（真实走 ModelPool）、自然语言建技能、
  自进化钩子；HTTP 完整 CRUD：/v1/skills/*
- **M8 IM 渠道层**（`moa_gateway/channels/`）：Telegram / 飞书 / 钉钉 / 企业微信 /
  Discord 五平台真实协议实现（httpx + 平台验签 + MOA_* 环境变量凭据），
  诚实状态机（unconfigured/configured/running）、会话路由持久化、
  UIController 写回；HTTP：/v1/channels/*
- **M9 轻量子代理路由**（`moa_gateway/subagent_routing/`）：fork 前缀检测、
  lite 模型映射、forbidden_tools 过滤、摘要折叠 + 成本合并；
  invoke_lite_subagent 注册进 /v1/agent legacy 工具面（function_call 开关守卫）；
  HTTP：/v1/subagent/*

### MemoraX Code 集成
- **M10 跨会话记忆层**（`moa_gateway/memory/`）：5 类记忆、作用域模型、
  三端点 hook 协议（fail-closed 白名单）、混合召回（dense+sparse）、
  写回管道（PII 脱敏→缓冲→分块→幂等入库）；已接线 assistant runs
  （retrieval/writeback 双开关默认全关，钩子永不破坏 run 路径）；
  HTTP：/v1/memory/*
- **M11 工作区记忆**（`moa_gateway/workspace_memory/`）：.moa_memory 目录、
  facet 脚本真实子进程执行、adaptive 真实 diff 更新、supervisor 锁；
  HTTP：/v1/workspace-memory/*

### 管理面与交付物
- **M12 admin-ui**：routing / quota / compression / free-tiers / memory /
  skills / channels 七个管理页面（完整 CRUD + 配置 + 演练面板），next build 通过
- 能力开关新增 9 项，关闭即对应端点 503，状态持久化
- 配置：9 个新 pydantic 配置类 + config.yaml 同步；修复 compression.default_mode
  YAML 1.1 裸 off→布尔问题
- 桌面端 4.1.0（Electron NSIS + 便携版）、移动端 1.1.0（versionCode 2，APK）

## 验证结果

- **全量回归：** **1939 collected / 1939 passed, 0 failed**
  （基线 1435 + 本轮新增 495：路由策略+配额 124、效率+子代理 95、
  技能+渠道 118、记忆+工作区记忆 108、A2A 50；压缩+免费层库测试 +
  收口 free-tiers HTTP 面 9 例；2026-08-29 独立终跑 871.59s 实证）
- **磁盘事故如实记录：** 首轮全量回归后段 C 盘耗尽（Errno 28），4 个
  子进程密集文件共 49 例 error（非失败）；释放空间后整 4 文件 152 例
  重跑全绿，确认非代码回归
- **装配冒烟（2026-08-29 终版实测）：** 穿透 FastAPI 0.139 `_IncludedRouter`
  惰性包装清点 —— **271 唯一路径 / 299 (method,path) 端点对 / 34 个 v4.1
  前缀路径全部注册、routing_fusion 自动注册**。初版宣称的 "273/31" 无法
  在当前环境复现，且复核发现 **M4 免费层目录仅有 catalog 库而无 HTTP 面**
  （/v1/free-tiers 未实现、未注册，admin-ui 页面却在调用）——已补齐
  `routes/free_tiers.py`（GET /v1/free-tiers、GET /v1/free-tiers/{key}，
  API key + 能力开关 + enabled 503 门控）+ server 注册 + 9 条真实测试。
- **诚实扫描（2026-08-29 复核）：** 宽口径扫描命中 TODO 24 / placeholder 52 /
  mock 724 / stub 5 / FIXME 4 / NotImplemented 39，逐类核验均为合法用途——
  显式 mock 标注体系（X-MOA-Mock/显式策略，v3.1.1 审计确认的既定设计）、
  扫描器自身的标记正则（mx_annot/workspace_memory facets）、模板占位符、
  "非 stub" 否定式声明、抽象方法——**无真实 stub 或降级混入生产路径**。
  初版"零命中"表述口径不严谨，按实测修订。

## 诚实性说明（零虚假政策，延续 v4.0.0）

- 三条已知边界如实披露（详见 DELIVERY_REPORT_v4.1.md）：
  1. tool_hub 面暂不暴露 invoke_lite_subagent（legacy 面 + /v1/subagent API +
     编程式注册已覆盖同等能力）
  2. 上游响应头配额摄入未接入 provider 抽象（provider 不传响应头）；
     配额监控经 /v1/quota/refresh + observe_state + lifespan 自适应循环供数
  3. chat 空闲压缩自动 arm 需服务端会话历史基建（跟进项）；现可经
     POST /v1/efficiency/compress-session 显式使用
- 本轮无真实 LLM API key 的调用路径按既有显式标注体系呈现，绝无静默降级
- APK release 变体在无 keystore 环境按 build.gradle 设计回退 debug 签名，
  构建日志显式声明（如需上架签名，配置 MOA_KEYSTORE_* 环境变量重建）

## 升级指引

1. 解压/拉取 v4.1.0 源码，`pip install -e .`
2. 新能力默认全部开启但均为 opt-in 语义（不改既有流量）；如需关闭：
   管理面"能力管理"页或 `POST /v1/capability/toggles`
3. 记忆能力默认关闭：`memory.retrieval_enabled` / `memory.writeback_enabled`
4. 压缩默认不动聊天流量：`compression.apply_to_chat: false`
5. IM 渠道凭据经 MOA_* 环境变量注入（清单见 INTEGRATION_GUIDE.md §9.8）
6. 详细接入示例：INTEGRATION_GUIDE.md 第 9 章
