# MoA Gateway Pro v4.0.0 — P 项清零版（十一轮审计 + 开发计划 P1–P11 全落地）

**Date:** 2026-08-21
**Status:** Production-hardened — 全量回归 0 failed，真实启动链路冒烟 6/6，交付物逐件核验

v3.1.1 完成十轮审计修复后，开发状态评估识别出 11 项计划缺口（P1–P11）。v4.0.0
将其**全部真实落地**，并在收尾对抗复审中再挖出 4 处真实缺陷（CLI 入口路由、
多模态全失败证据丢失、新端点缺限流、WebUI 重复 escapeHtml），一并修复。

## 计划缺口 → 落地对照（P1–P11）

- **P1 SSRF 编码 IP 绕过修复**：`utils/url_validator.py` 自实现 inet_aton 语义归一化
  （十进制/十六进制/八进制/混合点分/全 IPv6 文本形式），DNS 解析前拦截；补堵
  IPv4-compatible IPv6（::/96）与前导零八双解歧义；归一化失败 fail-closed。
  P1-3 JWT 探测改严格正则（不再 `count('.')==2`）
- **P2 多媒体 provider 全接线**：图像生成去空 key 硬编码（按 platform 读真实凭据）；
  新增 `routes/music.py`（MiniMax/天工异步任务）、`routes/video.py` Kling 接通、
  `routes/audio.py` 专用 ASR/TTS（OpenAI/DashScope/iFlytek）provider 参数；
  `.env.example` 补全全部密钥模板
- **P3 MultiModalFanout 多路并发聚合引擎**：一次请求 N 平台 `asyncio.gather` 并发，
  模式 all/fastest/best，聚合结果带逐路 provider/latency/cost/mock 标注，
  接入 X-MOA-Mock 体系；全失败显式报错留逐路证据（v4.0.0 收尾再加固：
  `MultimodalAllFailedError` 携带 FanoutResult，supervisor 自愈可剔除永久坏平台重试）
- **P4 统一工具中枢（ToolHub）**：P4-4 moa_graph workflow 补齐，agent_loop/MoA
  tool loop 全部接入注册工具（含外部 MCP 发现工具），guardrails 统一生效
- **P5 MCP 完整化**：stdio 外连客户端、SSE 投递链路（POST /v1/mcp/sse/messages）、
  外部工具并入 tools/list（带命名空间）、双 server 合并统一 RBAC
- **P6 CLI 真实化**：外部 CLI 注册表 + subprocess 真实执行 + 多路聚合
  （v4.0.0 收尾修复：`python -m moa_gateway --port N` 入口路由 bug）
- **P7 多 AI 同框对话（Dialogue Rooms）**：`moa_gateway/dialogue/` 全新模块，
  round_robin / parallel_think / free_talk 三模式，全部真实 `model_pool.call`，
  SQLite WAL 持久化重启可恢复，SSE 逐参与者推送 + 环形缓冲回放；
  P7-4 真实冒烟：uvicorn 实启（端口 18910）6/6 通过（WebUI/房间 CRUD/SSE keep-alive）
- **P8 主动任务分析升级**：TaskAnalyzer（LLM 真实分解，无启发式兜底）→
  CapabilityRouter → TaskSupervisor（waves 调度 + 重试 + self-heal 自愈）闭环
- **P9 Windows 桌面端（Electron）**：`desktop/` 独立工程（419 文件，不含 node_modules），
  electron-builder NSIS + portable 双形态，版本 4.0.0 对齐
- **P10 Android 端（Capacitor）**：`mobile/` 独立工程（83 文件），完整 Android
  Gradle 工程 + build-apk 脚本，首版 v1.0.0
- **P11 回归 + 打包**：见下

## 验证结果

- **单元测试：** 1435 collected, **1435 passed, 0 failed**（10 warnings，555.25s，EXIT=0；
  含本轮全部最新修复的干净重跑）
  （v3.1.1 基线 1071 → 新增 364 例：多模态接线 42、对话 46、SSRF 变体 25、
  fanout/pipeline/CLI 等其余项）
- **真实启动链路冒烟（P7-4）：** uvicorn 实启 + 真实 API key 鉴权，6/6 通过
- **端点实测：** 穿透 FastAPI 0.139 `_IncludedRouter` 惰性包装逐条清点 ——
  237 条路由路径、262 个 (method, path) 端点（GET 86 / POST 156 / DELETE 12 / PUT 8），
  清单见仓库 `endpoint_list.txt`（README 原标 141 系旧版统计口径过时，本版更正）
- **收尾对抗复审（不派子代理、直接人肉走查）新发现并修复 4 处真实缺陷**：
  1. `__main__.py`：`--port/--host` 首参被误当子命令 → `invalid choice`（实测复现后修复）
  2. `task_pipeline.py`：multimodal 全失败抛裸 RuntimeError 丢失逐平台证据，自愈重路由
     对永久坏平台无效（`MultimodalAllFailedError` + 坏平台剔除，新增 2 回归测试）
  3. `routes/multimodal.py` / `routes/task_pipeline.py` 缺 per-key 限流（补齐）
  4. `webui/index.html` 重复 `escapeHtml`（删不完整版本，node --check 通过）

## 诚实性说明（零虚假政策，延续 v3.1.1）

- 无真实 provider key 时仍按 `settings.mock.mode` 返回**显式标注**的合成结果
  （`X-MOA-Mock: true` / `mock:true`），配置真实 key 后优先真实 provider；
  本次新增的 fanout/对话/任务管线全部遵循同一政策，失败记录真实失败证据
  （status=error/timeout/no_key），绝不伪造内容
- v3.1.1 README 宣称"141 个 API 端点、236 个测试用例"与实测不符（统计口径过时 +
  未穿透惰性路由包装），v4.0.0 以可复现脚本 `count_endpoints.py` 实测更正
- desktop/mobile 为独立客户端工程，随源码交付；安装包构建需本机 Node/Android
  工具链，未在本审计环境产出二进制（诚实标注，不做假构建产物）

## 升级须知

- 生产部署仍需通过环境变量提供 `MOA_ADMIN_PASSWORD`、`MOA_GATEWAY_KEY`、`MOA_JWT_SECRET`
- 从 v3.1.1 升级：直接替换包即可；新增 dialogue 相关表由 storage 层首次启动自动建立（SQLite WAL）
- CLI 行为变化：`python -m moa_gateway` 不带子命令 = 启动 server（同 `serve`），
  `--host/--port/--workers` 现可直接使用
