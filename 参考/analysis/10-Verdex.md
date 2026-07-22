# Verdex 项目深度分析

> 分析对象：`D:\MoA Gateway Pro\参考\extracted\10-Verdex\Verdex-main`
> 项目版本：package.json `0.1.1` / Cargo.toml `0.1.2` / License MIT
> 分析方式：逐行阅读全部源码 + 配置 + 文档 + 测试
> 生成时间：2026-07-13

---

## 一、项目概述

**Verdex** 是一个**纯本地、无服务器的多模型裁判综合引擎（Mixture-of-Agents Synthesis Engine）桌面客户端**。它的核心定位是：把用户的同一个问题并行发给多个 AI 模型（**Panel 层**），然后由一个或多个**裁判模型（Judge）**综合这些回答，输出一份结构化的**四段裁决**（核心共识 / 观点碰撞 / 独特盲点 / 最终裁决）。

### 关键设计立场（来自 README + HANDOFF.md）

| 维度 | 立场 |
|---|---|
| AI 框架 | **拒绝**任何第三方框架（LangChain / AutoGen / SDK），调度逻辑全部用原生 `Promise.all` |
| 数据存储 | 纯本地；明文 `config.json`（Tauri `appDataDir`） + 浏览器 `localStorage` 兜底 |
| 协议适配 | OpenAI Chat Completions + Anthropic Messages API（`/v1/messages`），同一 `streamChat` 入口按 `protocol` 字段切换 |
| 持久化域 | 四个**完全解耦**的全局域：providers / roleTemplates / judgePrompts / sessions |
| 角色模型 | Provider **不绑定**角色（Panel / Judge），由会话决定；同一 provider 可同时担任 Panel 和 Judge |
| 主题/语言 | 3 套主题（dark / light / soft）+ 2 套语言（en / zh），全部 CSS 变量驱动，持久化到 `config.json` |
| 测试 | Vitest 26 个单元测试，覆盖 `extractAnthropicSystem`、`checkInputLimits`、`parseJudgeResponse` |
| CI/CD | GitHub Actions `release.yml`：4 平台矩阵（macOS arm64 / x64、Ubuntu、Windows），用 `tauri-action` 在打 tag `v*` 时发布 |

### 核心数字

- 前端代码行数：App.tsx 316 + useMoa.ts 912 + moaEngine.ts 573 + httpClient.ts 582 + configStore.ts 306 + 9 个组件 ≈ 3600+ 行 TypeScript
- 后端 Rust 代码：`lib.rs` 18 行 + `main.rs` 6 行（极薄壳）
- 默认数据：4 个 Provider（Llama 3.3 / Qwen Plus / DeepSeek Chat / Claude 3.5 Sonnet）、10 个角色模板（5 英 + 5 中）、6 个 Judge 提示词模板（3 英 + 3 中）
- 测试用例：18 个（8 `extractAnthropicSystem` + 7 `checkInputLimits` + 11 `parseJudgeResponse` 等）

---

## 二、核心模块清单

### 1. 根目录配置文件

| 文件 | 行数 | 作用 |
|---|---|---|
| `package.json` | 35 | npm 脚本（dev / build / test / tauri）+ 依赖清单（React 18 / TS 5 / Vite 5 / Tailwind 4 / i18next） |
| `tsconfig.json` | 31 | ES2020 / strict / `@/*` 路径别名指向 `src/*` |
| `tsconfig.node.json` | 11 | 给 `vite.config.ts` 用的独立 tsconfig |
| `vite.config.ts` | 43 | 固定端口 1420 + HMR 1421 + 监听 `src-tauri/**` 排除 + Chrome105/safari13 target + esbuild 压缩 |
| `index.html` | 13 | Vite 入口；`<html lang="zh-CN" class="dark">` 默认深色 |
| `.gitignore` | 43 | 忽略 `node_modules/`、`dist/`、`src-tauri/target/`、`config.json`（**含 API Key，绝不提交**） |
| `README.md` / `README_CN.md` | 130 / 130 | 中英双语快速入门 |
| `HANDOFF.md` | 238 | **架构决策记录 + 7 个历史坑 + 后续任务**（极重要） |
| `LICENSE` | 21 | MIT 2026 |
| `app-icon.png` | 30 KB | 1024×1024 PNG（由 `scripts/gen-icon.mjs` 生成） |

### 2. 前端源码（`src/`）

| 路径 | 作用 |
|---|---|
| `main.tsx` | ReactDOM 挂载 + `<Suspense>` + StrictMode |
| `App.tsx` | **应用 shell** —— 加载屏 + 侧栏 + 头部 + 配置栏 + 错误条 + 消息流 + 输入框 + 模态框 |
| `index.css` | **主题系统核心** —— 3 套主题 CSS 变量 + Tailwind v4 `@theme` 桥接 + 滚动条/动画 |
| `vite-env.d.ts` | 声明 `window.__TAURI_INTERNALS__` 全局 |
| `i18n/index.ts` | i18next 初始化（`lng: "en"`, `fallbackLng: "en"`, `returnNull: false`） |
| `i18n/en.json` / `i18n/zh.json` | 中英双语文案（193 / 194 行，对称结构） |
| `types/moa.ts` | **全部数据结构的单一真相源**（300 行） |
| `services/httpClient.ts` | **协议适配核心**（582 行）—— `streamChat` + `testProvider` + `normalizeBase` + `extractAnthropicSystem` |
| `services/moaEngine.ts` | **MoA 调度核心**（573 行）—— `runMoaSynthesis` + `checkInputLimits` + `parseJudgeResponse` + 3 个内置 Judge 提示词 |
| `services/configStore.ts` | **配置持久化**（306 行）—— Tauri fs / localStorage 双后端 + 模板加载 + 旧 5-key 迁移 |
| `services/config.template.json` | **出厂默认**（141 行） |
| `services/modelContextDB.ts` | **40+ 模型上下文窗口数据库**（118 行） |
| `hooks/useMoa.ts` | **状态机**（912 行）—— 4 域 CRUD + send 调度 + 节流刷新 + 防抖写盘 |
| `components/Sidebar.tsx` | 侧栏（会话列表 + 语言/主题选择 + 入口按钮） |
| `components/MoAConfigBar.tsx` | 会话级 MoA 配置栏（模式切换 + Panel/Judge 选择） |
| `components/SettingsModal.tsx` | 标签页设置（Provider + 模板统一入口） |
| `components/TemplatesModal.tsx` | 模板管理模态框 |
| `components/JudgeMessage.tsx` | 四段裁决卡（🎯⚔️💡⚖️） + 降级视图 |
| `components/PanelCollapseGroup.tsx` | 并行 Panel 状态卡 |
| `components/ChatInput.tsx` | 自适应高度输入框 + Ctrl/Cmd+Enter 发送 |
| `components/HelpModal.tsx` | 帮助弹窗 |
| `components/UserMessage.tsx` | 用户消息气泡（18 行，最小） |

### 3. 后端 + 构建（`src-tauri/`）

| 路径 | 行数 | 作用 |
|---|---|---|
| `Cargo.toml` | 32 | 依赖 `tauri 2` + `tauri-plugin-http` + `tauri-plugin-fs` + `serde` + `serde_json` |
| `tauri.conf.json` | 39 | `identifier: com.verdex.app`、窗口 1280×832、`bundle.targets: "all"` |
| `build.rs` | 3 | 调 `tauri_build::build()` |
| `capabilities/default.json` | 19 | 权限：`http:default`（http://** + https://**）+ `fs:allow-appdata-read/write/meta` |
| `src/main.rs` | 6 | 调 `verdex_lib::run()` |
| `src/lib.rs` | 18 | 注册 `tauri_plugin_http` + `tauri_plugin_fs` |
| `icons/` | 多尺寸 | 32/64/128/128@2x + .ico + .icns + Square 全套 + 1024 PNG |

### 4. 工具脚本 + CI

| 路径 | 行数 | 作用 |
|---|---|---|
| `scripts/gen-icon.mjs` | 157 | **零依赖生成图标** —— 纯手写 CRC32 + zlib 压缩 + 距离场绘制 "V" 字 |
| `.github/workflows/release.yml` | 57 | 4 平台构建矩阵 + `tauri-action@v0` 发布 |
| `test/httpClient.test.ts` | 81 | `extractAnthropicSystem` 8 个 case |
| `test/moaEngine.test.ts` | 151 | `checkInputLimits` 7 个 + `parseJudgeResponse` 10 个 |

---

## 三、详细能力列表（极端详细）

### 3.1 数据模型层（`src/types/moa.ts`，300 行）

#### 3.1.1 协议原语

- **`ProtocolType = "openai" | "anthropic"`** —— 两种线协议联合类型
- **`ChatMessage { role: "system" | "user" | "assistant"; content: string }`** —— OpenAI 标准消息形态；HTTP 适配器按 `protocol` 字段重写

#### 3.1.2 全局 Provider 配置

- **`AIProvider` 接口**：
  - `id: string` —— 用 `crypto.randomUUID()`
  - `name: string` —— 用户显示名
  - `modelString: string` —— API 实际发的模型 ID
  - `baseUrl: string` —— 规范化后的 Base URL（无尾斜杠、无 `/chat/completions` 后缀）
  - `apiKey: string` —— **明文存储**（用户明确选择）
  - `protocol: ProtocolType` —— 默认 `openai`（向后兼容）
  - `capabilities?: ProviderCapabilities` —— 调度器用
- **`ProviderCapabilities.maxContextChars?: number`** —— 上下文窗口字符数；`undefined` = 无约束

#### 3.1.3 全局提示词模板

- **`RoleTemplate { id; name; systemPrompt }`** —— Panel 角色（中性通用思维工具）
- **`JudgePromptTemplate { id; name; systemPrompt }`** —— Judge 提示词（默认强制四段 JSON）

#### 3.1.4 会话级调度配置

- **`MoaMode = "simple" | "advanced"`** —— 模式枚举
- **`JudgeStrategy = "single" | "collision"``** —— 高级模式子选项
- **`MoASessionConfig`**：
  - `mode: MoaMode`
  - `panelIds: string[]` —— 选中的 provider id 列表
  - `panelRoles: Record<string, string>` —— panelId → roleTemplateId；缺键 = 无角色
  - `judgeIds: string[]` —— 长度 1（simple/single）或 ≥2（collision）
  - `judgeStrategy: JudgeStrategy` —— 简单模式下强制 "single"
  - `judgePromptId: string | null` —— simple/single 用的提示词 id
  - `collisionJudgePromptIds: string[]` —— collision 模式每个 judge 对齐一个提示词

#### 3.1.5 引擎请求 / 响应契约

- **`JudgeSpec { providerId; systemPrompt }`** —— 解析后的 Judge 执行规格
- **`SynthesisRequest { prompt; panelIds; panelRoles; judges; temperature?; maxTokens?; timeoutMs? }`** —— 默认 `temperature=0.7`, `maxTokens=2048`, `timeoutMs=60000`
- **`SynthesisResponse { consensus; divergence; blindspots; verdict }`** —— 四段裁决稳定契约，UI 渲染唯一依据

#### 3.1.6 运行时状态

- **`PanelStatus = "pending" | "streaming" | "done" | "error" | "skipped"`** —— 5 态
- **`PanelState { providerId; label; model; status; rawText; error?; roleName? }`** —— 含 label/model/roleName 的**快照**（避免 provider 后续编辑影响历史）
- **`JudgeStatus = "pending" | "judging" | "streaming" | "done" | "error"`** —— 5 态
- **`JudgeState { judgeId; label; status; raw; response; error? }`** —— 同上含 label 快照

#### 3.1.7 对话单元

- **`Turn { id; prompt; createdAt; panels: PanelState[]; judges: JudgeState[] }`** —— 一个用户问 + 并行 Panels + Judges
- **`ChatSession { sessionId; title; createdAt; config: MoASessionConfig; messages: Turn[] }`** —— 命名对话

#### 3.1.8 引擎回调

- **`MoaCallbacks` 接口**：
  - `onPanelStart(providerId)` —— 开始流式
  - `onPanelDelta(providerId, delta)` —— 每段增量
  - `onPanelRetry(providerId)` —— 重试前（UI 丢弃部分流文本）
  - `onPanelDone(providerId, fullText)` —— 完成
  - `onPanelError(providerId, message)` —— 失败
  - `onPanelSkipped(providerId, reason)` —— 预检跳过
  - `onPanelsComplete()` —— 全部 panel 结束
  - `onJudgeStart/Delta/Done/Error(judgeId, ...)` —— 同上

### 3.2 HTTP 客户端层（`src/services/httpClient.ts`，582 行）

#### 3.2.1 Tauri/Web 双后端 fetch 解析

- **`isTauri()`** —— 检测 `window.__TAURI_INTERNALS__` 是否存在
- **`resolveFetch()`** —— 异步懒加载 `@tauri-apps/plugin-http` 的 `fetch`（绕过 CORS）；失败回退到 `globalThis.fetch`
- 缓存到 `tauriFetchPromise` 单例，避免重复动态导入

#### 3.2.2 Base URL 规范化（`normalizeBase`，行 93-106）

- **输入容忍度**：可接受 `https://x.com/v1/`、`/chat/completions`、`/v1/chat/completions`、`/messages`、`/v1/messages` 等任何形态
- **实现**：迭代去尾斜杠 + `/chat/completions` + `/messages`（但保留 `/v1`）
- **导出**供 UI 实时显示规范化结果（如 `SettingsModal` 行 191-208）

#### 3.2.3 OpenAI 请求构造（`prepareOpenAI`，行 112-130）

- **端点**：`{base}/chat/completions`
- **鉴权**：`Authorization: Bearer {apiKey}`
- **Body 字段**：`model` / `messages` / `temperature`（默认 0.7）/`max_tokens`（默认 2048）/`stream`
- **双 body**：`streamBody`（`stream:true`）+ `nonStreamBody`（`stream:false`）作为回退

#### 3.2.4 Anthropic 请求构造（`prepareAnthropic`，行 176-208）

- **端点**：`{base}/v1/messages`（若 base 已含 `/v1` 则只追加 `/messages`，自动去重）
- **鉴权**：`x-api-key` + `anthropic-version: "2023-06-01"`
- **System 处理**：所有 system 消息 → 顶层 `system`（用 `\n\n` 拼接）
- **强制首条为 user**：若 messages 数组为空或首条非 user，注入一个 user 消息
- **Body 必含 `stream: true`**（HANDOFF 坑 7：只设 Accept 头不行）

#### 3.2.5 Anthropic System 提取（`extractAnthropicSystem`，行 150-174）

- 单元测试覆盖：单 system 提取 / 多 system 拼接 / 空 system 丢弃 / 无 system / 首条 assistant 注入 / 仅有 system 时注入为 user / 空输入注入默认 / 多轮交替保持

#### 3.2.6 SSE 流解析（`streamSse`，行 253-343）

- **协议分流**：
  - OpenAI：`choices[0].delta.content`
  - Anthropic：`type === "content_block_delta"` 时取 `delta.text`
- **解析循环**：按 `\n` 切行 → trim → 跳过空行/注释行（以 `:` 开头）→ 提取 `data:` 后内容 → 跳过 `[DONE]` → JSON.parse → 调 deltaFn
- **流尾处理**：循环结束后再 flush 一次 buffer 中残留的 `data:` 行
- **Read 异常**：try/catch 单条 JSON 解析失败不中断（应对部分 JSON 跨 chunk）

#### 3.2.7 非流式回退（`extractContent`，行 346-370）

- OpenAI：`choices[0].message.content`
- Anthropic：过滤 `content` 数组中 `type === "text"` 的块并 `text` 字段拼接

#### 3.2.8 `streamChat` 主入口（行 378-447）

- **超时控制**：`AbortController` + `setTimeout`（默认 60s）
- **流式优先**：先试 `streamSse`；失败/超时后自动用 `nonStreamBody` 跑非流式
- **超时错误**：抛 `REQUEST_TIMEOUT`（i18n key）
- **失败错误**：抛 `REQUEST_FAILED`（带原始 message）
- **关键设计**：永不静默失败；所有 `try/finally` 都 `clearTimeout`

#### 3.2.9 Provider 连接测试（`testProvider`，行 471-582）

- **探测请求**：`messages: [{ role: "user", content: "ping" }]`, `max_tokens: 1`, `temperature: 0`, `timeoutMs: 20000`
- **使用 `nonStreamBody`**：避免测试时还要解析 SSE
- **上下文窗口自动检测**（按优先级）：
  1. 调 `GET {base}/models`（仅 OpenAI 协议）找 `context_length` / `context_window`
  2. 调 `lookupContextChars(modelString)` 内置数据库
  3. 都没有则 `detectedContextChars: undefined`
- **错误解析**：尝试提取 `error.message` / `error.code` / `message` / `detail` 等结构化错误
- **返回 `{ ok; message; ms; detectedContextChars? }`** —— UI 徽章显示 `✓ 234ms · 128K ctx`

### 3.3 MoA 引擎层（`src/services/moaEngine.ts`，573 行）

#### 3.3.1 内置 Judge 提示词（`DEFAULT_JUDGE_PROMPTS`，3 个英文）

1. **`judge-default-en`** —— 默认四段裁决（consensus/divergence/blindspots/verdict）
2. **`judge-strict-logic-en`** —— 严格逻辑审计（找谬误/循环论证/不可证伪）
3. **`judge-multi-perspective-en`** —— 多视角综合（识别表面对立 vs 实质对立，保留张力）

> 注：中文版在 `config.template.json` 单独定义（6 个），不通过 moaEngine 导出

#### 3.3.2 输入熔断器（`checkInputLimits`）

- **默认阈值**：`DEFAULT_PROMPT_LIMIT = 100_000`（25K tokens）、`DEFAULT_CONTEXT_LIMIT = 400_000`（100K tokens）
- **可覆盖**：调用方传自定义 `promptLimit` / `contextLimit`（useMoa 会基于 panel 的 `maxContextChars` 动态算）
- **规则**：先校验单条 prompt，再校验 prompt + history 总和
- **i18n 错误**：`PROMPT_TOO_LONG` / `CONTEXT_TOO_LONG`（带实际数字）

#### 3.3.3 Panel 单次重试（`runPanel` + `isRetriableError`，行 174-289）

- **可重试错误**：网络/超时/5xx/429
- **不可重试错误**：401 / 403 / "unauthorized" / "forbidden" / "invalid api key"（字符串匹配）
- **退避**：`PANEL_RETRY_BACKOFF_MS = 800` + `PANEL_MAX_ATTEMPTS = 2`
- **UI 透明**：`onPanelRetry` 回调让 useMoa 重置 rawText buffer（用户看不到重试过程）
- **永不 reject**：try/catch 后总 resolve 一个 `PanelResult`

#### 3.3.4 Panel 调用（`callPanelOnce`，行 207-233）

- **消息组装**：若 `roleSystemPrompt` 非空，前置 `{role:"system", content: ...}`
- **参数**：`temperature` 默认 0.7，`maxTokens` 默认 2048，`timeoutMs` 默认 60000

#### 3.3.5 Judge 提示词构造（`buildJudgeSystemPrompt` + `renderPanelBlock`）

- **Panel 块渲染**（`renderPanelBlock`）：
  - 成功：`### Expert N: <label>` + 回答正文
  - 失败：`(call failed: <message>)` 或 `(this expert returned no content)`
  - i18n：根据 `i18n.language` 切 "专家" / "Expert"
- **模板替换**：若 customPrompt 含 `{PANELS}` 占位符 → 替换；否则追加 `【专家回答】` + 块

#### 3.3.6 Judge 响应解析（`parseJudgeResponse`，行 346-402）

- **容忍度**：
  - 去 ` ```json ` 代码围栏
  - 从 prose 中提取 `{...}` 区间
  - JSON.parse 失败回退
  - 字段缺失用 `(field missing)` / `(field empty)` 占位
  - 数组值用 `；`（全角）拼接为字符串
  - 对象值 `JSON.stringify`
- **fallback verdict**：解析失败时填入 `raw.slice(0, 1000)` 截断原文
- **保证结构完整**：永远返回 4 字段，UI 永不崩

#### 3.3.7 Judge 调用（`runSingleJudge`，行 424-481）

- **System prompt 注入**：直接作为首条 system message
- **User prompt 注入**：根据 i18n 切中英 `用户原始问题 / Original user question`
- **Temperature**：固定 0.3（裁判要稳定）
- **Streaming**：回调 `onJudgeDelta` 累加 raw + 流式通知
- **失败安全**：`onJudgeError` 回调 + resolve `JudgeResult{ ok: false, raw, error }`

#### 3.3.8 顶层综合（`runMoaSynthesis`，行 494-572）

执行流程：
1. **解析 panel providers**（按 id 过滤）
2. **校验 judge 不空**（否则 `JUDGE_EMPTY`）
3. **校验 panel 不空**（否则 `PANEL_EMPTY`）
4. **预检跳过**（`onPanelSkipped`）：遍历 panel，若 `maxContextChars < prompt.length` → 跳过
5. **全部跳过**（`ALL_PANELS_SKIPPED`）：直接返回
6. **Phase 1 Panels**：`Promise.all(panelProviders.map(runPanel))`，**永不 reject**
7. **`onPanelsComplete()`** 通知
8. **解析 judge providers**（按 id 过滤）
9. **judge 全部解析失败**（`JUDGE_NOT_FOUND`）
10. **Phase 2 Judges**：`Promise.all(judgeProviders.map(runSingleJudge))`，**永不 reject**
11. 每个 judge 用 **自己的** `spec.systemPrompt` + 共享的 panel 结果构建 prompt

### 3.4 配置持久化层（`src/services/configStore.ts`，306 行）

#### 3.4.1 `ConfigFile` 数据结构

包含 7 个字段：`providers` / `roleTemplates` / `judgePrompts` / `sessions` / `currentSessionId` / `language` / `theme`

#### 3.4.2 三层加载顺序（`loadConfig`）

1. **当前后端**：Tauri → `appDataDir/config.json`；浏览器 → `localStorage["verdex.config"]`
2. **遗留迁移**（仅浏览器）：若旧 5-key localStorage（`verdex.providers` 等）有数据，合并为 ConfigFile 并清除旧键
3. **模板回退**：`config.template.json`（Vite `?raw` 导入，缓存到 `cachedTemplate`）

#### 3.4.3 Tauri fs 后端（`resolveFsBackend`）

- 动态 import `@tauri-apps/plugin-fs` + `@tauri-apps/api/path`
- 缓存 promise，失败返回 null
- 接口：`readTextFile` / `writeTextFile` / `exists` / `mkdir` / `appDataDir` / `join`

#### 3.4.4 路径

- **Windows**：`%APPDATA%\com.verdex.app\config.json`（HANDOFF 坑 2 强调）
- **macOS/Linux**：`~/.local/share/com.verdex.app/config.json`（Tauri 默认）

#### 3.4.5 写入（`saveToTauri`）

- 调 `mkdir({ recursive: true })` 兜底建目录
- 写 JSON（`null, 2` 缩进）
- **静默失败**：不抛错，best-effort

#### 3.4.6 `normalizeConfigShape`（行 290-305）

- `providers` 数组每个强制 `protocol: "openai"`（旧配置无字段时兜底）
- 5 个数组字段缺一不可（空数组兜底）
- `currentSessionId`: `null` 兜底
- `language`: 强校验 `"en" | "zh"`
- `theme`: 强校验 `"dark" | "light" | "soft"`

### 3.5 模型上下文数据库（`src/services/modelContextDB.ts`，118 行）

#### 3.5.1 内置 40+ 模型

按厂商分组：

- **OpenAI** 10 个：`gpt-4o`/`gpt-4-turbo`/`gpt-4.1`/`gpt-4.5`/`gpt-5`/`o1`/`o3`/`o4-mini`/`gpt-oss-120b`/`gpt-oss-20b`
- **Anthropic** 6 个：`claude-3-5-sonnet`/`claude-3-5-haiku`/`claude-3-opus`/`claude-sonnet-4`/`claude-opus-4`/`claude-haiku`
- **DeepSeek** 5 个：`deepseek-chat`/`deepseek-reasoner`/`deepseek-v4`/`deepseek-v3`/`deepseek-r1`
- **Qwen** 5 个：`qwen-plus`/`qwen-max`/`qwen-turbo`/`qwen2.5`/`qwen3`
- **Llama** 6 个：`llama-3.3-70b`/`llama-3.1-405b`/`llama-3.1-70b`/`llama-3.1-8b`/`llama-4`/`llama-3`
- **Mistral** 3 个：`mistral-large`/`mistral-7b`/`mixtral`
- **Gemini** 4 个：`gemini-1.5-pro`/`gemini-1.5-flash`/`gemini-2`/`gemini-3`
- **Kimi/Moonshot** 2 个、`GLM/ChatGLM` 2 个、`Grok` 3 个、`NVIDIA` 2 个

#### 3.5.2 匹配算法（`lookupContextChars`）

- **最长优先**：keys 按长度降序排序，保证 `deepseek-v4-flash` 命中 `deepseek-v4` 而非 `deepseek-chat`
- **子串匹配**：case-insensitive
- **未命中返回 `undefined`**

#### 3.5.3 `tokensToChars(tokens)`

- 简单乘 4（保守近似）

### 3.6 状态机层（`src/hooks/useMoa.ts`，912 行）

#### 3.6.1 状态切片

11 个 useState：
- `providers` / `roleTemplates` / `judgePrompts` / `sessions` / `currentSessionId`（5 个域）
- `loaded` / `sidebarOpen` / `running` / `lastError` / `language` / `theme`（6 个 UI）

#### 3.6.2 初始化策略

- **初始值用模板**：避免首屏空白（`getTemplateConfig()` 提供 fallback）
- **异步加载覆盖**：`useEffect` 挂载后调 `loadConfig()` → `finalizeConfig()` → `setState` + `setLoaded(true)`
- **`finalizeConfig`**：
  - `sanitizeSessions` —— 任何非终态（pending/streaming/judging）的 panel/judge 改为 `error: SESSION_INTERRUPTED`（应对上次崩溃）
  - `normalizeSessionConfig` —— 旧版 `judgeId`（单数）迁移到 `judgeIds[]`；剔除指向已删 provider 的引用
  - 校验 `currentSessionId` 仍指向存在的 session

#### 3.6.3 持久化 useEffect（行 298-335）

- 监听 7 个域变化；只在 `loaded=true` 后才触发
- **防抖 600ms** 调 `saveConfig`
- **slim 压缩**：
  - panel `rawText` 截到 4000 字符
  - judge `raw` 截到 6000 字符
  - 避免 config.json 膨胀

#### 3.6.4 Provider CRUD（行 360-404）

- `addProvider(partial?)` —— 新建默认 name="新模型/New model"、baseUrl=`https://api.openai.com/v1`、protocol=`openai`
- `updateProvider(id, patch)` —— 浅合并
- `removeProvider(id)` —— **级联清理**：遍历所有 sessions，从 `panelIds` / `panelRoles` / `judgeIds` 中剔除该 id

#### 3.6.5 角色模板 CRUD（行 408-440）

- `addRoleTemplate(partial?)` —— 默认 name="新角色/New role"
- `updateRoleTemplate(id, patch)`
- `removeRoleTemplate(id)` —— **级联清理**：从所有 sessions 的 `panelRoles` 中删除 value 等于该 id 的键

#### 3.6.6 Judge 提示词 CRUD（行 444-484）

- `addJudgePrompt(partial?)` —— 默认 name="新裁判提示词/New judge prompt"
- `updateJudgePrompt(id, patch)`
- `removeJudgePrompt(id)` —— **级联清理**：`judgePromptId` 若是则置 null；从 `collisionJudgePromptIds` 中过滤

#### 3.6.7 Session CRUD（行 488-530）

- `newSession()` —— `makeEmptySession(providers)`：前 3 个 provider 当 panel，最后一个当 judge；mode=simple；调用 `genId()` 生成 uuid
- `selectSession(id)` —— 切 `currentSessionId`
- `renameSession(id, title)` —— 改 title
- `removeSession(id)` —— 删除后若删的是当前，自动选第一个剩余
- `updateSessionConfig(id, patch)` —— 浅合并到 `config`
- **`currentSession`** —— computed：`sessions.find(...) ?? null`

#### 3.6.8 节流刷新（`scheduleFlush`，行 538-570）

- **60ms 节流**：用 `flushTimer` ref 防抖
- **buffered map**：panel/judge 各自 `Record<id, string>` 缓冲
- **同步 flush**：超时则清空 timer + 直接写 state

#### 3.6.9 `send` 核心（行 574-877）

1. 截断 prompt；空/running 则直接 return
2. 取 currentSession；`panelIds` 或 `judgeIds` 为空则 return
3. 解析 panelProviders / judgeProviders（按 id 过滤）
4. **构造 history**：所有历史 turn 的 `prompt + panels[].rawText + judges[].raw` 拼接
5. **动态限流**：从 `panelProviders.capabilities.maxContextChars` 取 `min`（若存在），`promptLimit = min*0.5`、`contextLimit = min*0.8`；否则用 DEFAULT（100K/400K）
6. 调 `checkInputLimits`；失败 `setLastError(reason)` 并 return
7. 解析 per-panel 角色（template id → systemPrompt），记录 `roleName`
8. 解析 per-judge 提示词（collision 模式按 `collisionJudgePromptIds[idx]` 数组对齐；single 用 `judgePromptId`）
9. 重置 buffers
10. 构造 `newTurn`（所有 panel/judge 初始 status=pending、rawText=""）
11. 追加到 sessions，**若是首条**则用 `titleFromPrompt` 自动取前 24 字符当 title
12. 启动 `runMoaSynthesis`，挂 10 个回调映射到 `setPanel/setJudge` + buffer
13. **finally**：commit 残余 buffer；`setRunning(false)`

#### 3.6.10 流式回调映射（行 770-831）

- `onPanelStart(pid)` → buffer 清空 + 状态 streaming
- `onPanelDelta(pid, delta)` → 累加 buffer + scheduleFlush
- `onPanelRetry(pid)` → 重置 buffer + 状态 streaming（用户看不到重试）
- `onPanelDone(pid, text)` → buffer 同步 + 状态 done
- `onPanelError(pid, msg)` → 状态 error
- `onPanelSkipped(pid, reason)` → 状态 skipped
- `onJudgeStart(jid)` → buffer 清空 + 状态 judging
- `onJudgeDelta(jid, delta)` → 累加 buffer + judging 首次收到 delta 时改 streaming
- `onJudgeDone(jid, response, raw)` → buffer 同步 + 状态 done + 设 response
- `onJudgeError(jid, msg)` → 状态 error

#### 3.6.11 `genId()` / `titleFromPrompt()`

- `genId()` 优先 `crypto.randomUUID()`，回退 `${Date.now()36}-${Math.random()36}`
- `titleFromPrompt(prompt)` 截前 24 字符加 `…`

### 3.7 UI 组件层

#### 3.7.1 `App.tsx`（316 行）

- **加载屏**（`!moa.loaded`）：渐变 "V" + 脉冲点 + `Loading config…`
- **空会话视图**（`!session`）：侧栏 + "No active session" + "+ New chat" 按钮
- **正常视图**：
  - **侧栏** + **头部**：标题 + 模式 + Panel 数量 + Judge 摘要 + running 指示
  - **MoAConfigBar**
  - **错误条**（`moa.lastError`）—— 可关闭
  - **消息流**：每 turn = UserMessage + PanelCollapseGroup + 多个 JudgeMessage
  - **ChatInput**（底部固定）
- **关键铁律**（HANDOFF 坑 1）：所有 hooks 必须在 early return 之前；`if (!moa.loaded) return ...` 放在 `useEffect` 之后
- **Judge 摘要**：`judgeNames.length === 1` 显示名；多个显示 `{{count}} judges`

#### 3.7.2 `Sidebar.tsx`（276 行）

- **品牌头**（`V` logo + Verdex + 副标题）
- **新建会话** 按钮
- **会话列表**（`SessionRow`）：
  - 单击切换
  - 双击 ✎ 进入编辑（Enter 提交 / Esc 取消 / blur 提交）
  - 悬停显示 ✎ + 🗑 操作
  - 列表项显示 `时间 · N rounds`
  - 删除二次确认 `window.confirm`
- **底部**：
  - ⚙️ Model settings
  - ❓ Help
  - Language 下拉（en / zh）
  - Theme 下拉（dark / light / soft）
- **折叠/展开**（`› / ‹` 按钮，left 位置根据 `open` 动态计算）
- **`formatTime`**：今天显示 `HH:MM`；其他显示 `M/D HH:MM`

#### 3.7.3 `MoAConfigBar.tsx`（364 行）

- **行 1：模式 + 策略 + 就绪状态**
  - 简单/高级模式分段按钮（高级模式 `bg-brand-to` 紫色）
  - 高级模式额外显示 `Judge strategy` 下拉
  - 右对齐的 ● Ready（绿）/ ● Not configured（黄）状态点 + tooltip
- **行 2：Panel 多选**
  - 圆形按钮 chip，未选 = hairline 边框，选中 = accent 边框
  - 缺 API Key 显示 warning 黄色圆点
  - 高级模式下每个 panel 旁边挂角色下拉
- **行 3：Judge 选择 + 提示词**
  - **简单/single**：单选下拉
  - **collision**：多选 chip 按钮（绿色 border 区分）
  - **单选**模式：单 Judge 提示词下拉
  - **collision**模式：每个 judge 对齐一个 `1.<name> <select>` 提示词子选择器
- **自动清理 `collisionJudgePromptIds`**（HANDOFF 已修）：`toggleJudge` 时 `slice(0, next.length)`；`selectSingleJudge` 时 `= []`；`setMode('simple')` 时 `= []`；策略切换非 collision 时 `= []`

#### 3.7.4 `SettingsModal.tsx`（614 行）

- **两个 Tab**（`activeTab: "providers" | "templates"`）
- **Provider Tab**：
  - 描述：管理全局 OpenAI 兼容端点
  - 空状态占位
  - 每个 ProviderRow 含：
    - 名称 + 测试结果徽章（`✓ 234ms · 128K ctx`）
    - 错误详情（红底）
    - Display name / Model / Protocol / Context window / Base URL / API Key
    - Base URL 实时显示规范化结果或最终 endpoint
    - Anthropic 协议额外显示提示
  - **底部按钮**：`+ Add model` + `🔌 Test connection`（并发测试所有 provider）
  - **Done** 关闭按钮
- **Template Tab**：
  - **Panel role templates**（紫色主题）—— 角色名 + 多行 systemPrompt
  - **Judge prompt templates**（绿色主题）—— 提示词名 + 多行 systemPrompt（提示 `{PANELS}` 占位符）
- **键盘**：Esc 关闭
- **测试连接细节**（`runTests`）：
  - `Promise.all(providers.map(testProvider))` —— 并发
  - 自动填充 context window（若检测到且当前未设置）
  - try/finally 包裹（HANDOFF 坑 2 已修）
  - 重开 modal 时 `setTestResults({})` + `setTesting(false)`（HANDOFF 坑 3 已修）

#### 3.7.5 `JudgeMessage.tsx`（204 行）

- **5 个状态分支**：
  - `pending/judging/streaming` → 加载视图（带 3 颗脉冲点 + streaming 时末尾 400 字符预览带 caret 光标）
  - `error` → 红条 + 降级视图（展示所有成功 panel 的 rawText）
  - `done` → 4 段卡片 + raw JSON 折叠
- **4 段卡片**：
  - 🎯 核心共识（blue tint `bg-card-consensus/10 border-card-consensus/20`）
  - ⚔️ 观点碰撞（orange `bg-card-divergence/10`）
  - 💡 独特盲点（purple `bg-card-blindspots/10`）
  - ⚖️ 最终裁决（emerald `bg-card-verdict/5`，**无边框 + 较大字号 + 加粗**）
- **raw JSON 切换**：折叠面板显示 `<pre>` 完整原始流

#### 3.7.6 `PanelCollapseGroup.tsx`（126 行）

- **每 Panel 一个 `PanelCard`**
- 状态徽章颜色：等待=灰、流式=蓝、完成=绿、跳过=黄、错误=红
- 头部含 label + role 标签（紫色 chip）+ 状态
- 模型名（monospace、灰、截断）
- body 状态：
  - busy → "正在收集 {{label}} 的思考…" + 3 颗脉冲点
  - error → 红色错误文本
  - skipped → 黄色跳过原因
  - done → 3 行 `line-clamp-3` 折叠 + 展开按钮
- flex-wrap 横向并排，`flex-1 min-w-[180px]`

#### 3.7.7 `ChatInput.tsx`（95 行）

- **自适应高度** textarea（`MAX_HEIGHT = 200px` 之后滚动）
- **Enter = 换行**；**Ctrl/Cmd+Enter = 发送**
- **硬锁**：`if (running) return` 拦截按钮 + 快捷键 + 持续按键
- 提示行 `Enter 换行 · Ctrl/⌘+Enter 发送`
- 发送后清空 + requestAnimationFrame 重置高度

#### 3.7.8 `HelpModal.tsx`（115 行）

- 6 个 Section：什么是 Verdex / 快速开始 / 模式对比 / 模板说明 / 配置文件 / 快捷键 / 安全提醒
- 安全提醒用 warning 颜色
- 7 行即可获得完整使用说明

#### 3.7.9 `UserMessage.tsx`（18 行）

- 极简：右对齐、accent 背景、on-accent 文字、最大宽度 80%、圆角 + 阴影

#### 3.7.10 `TemplatesModal.tsx`（289 行）

- 与 `SettingsModal` 中 Template Tab 重复（待清理，HANDOFF 已知维护隐患非运行时 bug）
- 角色/Judge 模板 CRUD UI
- 注：当前 SettingsModal 已经合并了此 modal 的功能

### 3.8 主题系统（`src/index.css`，214 行）

#### 3.8.1 三套主题（CSS 变量驱动）

| 主题 | canvas | surface | accent | 强调色 |
|---|---|---|---|---|
| **dark**（默认） | `#020617` slate-950 | `#0f172a` slate-900 | `#2563eb` blue-600 | blue→purple |
| **light** | `#ffffff` | `#f8fafc` slate-50 | `#2563eb` blue-600 | blue→violet |
| **soft** | `#1c1b1f` 暖近黑紫 | `#29272e` 暖炭 | `#a78bfa` violet-400 | violet→fuchsia |

#### 3.8.2 完整变量集（每套主题 28+ 变量）

- 画布层级：`--vd-canvas` / `--vd-surface` / `--vd-surface-2` / `--vd-surface-3`
- 文字层级：`--vd-ink` / `--vd-ink-strong` / `--vd-ink-muted` / `--vd-ink-faint`
- 分隔线：`--vd-hairline` / `--vd-hairline-strong`
- 强调：`--vd-accent` / `--vd-accent-hover` / `--vd-accent-soft` / `--vd-on-accent`
- 状态：`--vd-success` / `--vd-error` / `--vd-warning`
- 品牌渐变：`--vd-brand-from` / `--vd-brand-to`
- **4 张裁决卡 tint**：`--vd-card-consensus` (蓝) / `--vd-card-divergence` (橙) / `--vd-card-blindspots` (紫) / `--vd-card-verdict` (绿)
- 滚动条 + 模态蒙层

#### 3.8.3 Tailwind v4 桥接（`@theme` 块，行 111-135）

将 CSS 变量映射为 Tailwind utility class：`bg-canvas` / `text-ink-strong` / `border-hairline` / `bg-card-consensus/10` 等

#### 3.8.4 动画

- `verdex-pulse` 1.2s —— 脉冲点（loading 状态、Panel 流式）
- `verdex-blink` 1s step-end —— 流式光标 `▍`（`verdex-caret` class）
- `verdex-sidebar-transition` 220ms ease —— 侧栏开合

#### 3.8.5 自定义滚动条

细 8px，hover 加深；跨浏览器

### 3.9 国际化（`src/i18n/`）

- **3 个文件**：`index.ts`（26 行初始化）+ `en.json`（194 行）+ `zh.json`（194 行）
- **i18next 配置**：`lng: "en"` / `fallbackLng: "en"` / `returnNull: false` / `escapeValue: false`
- **覆盖域**（每个翻译文件 12 个 namespace）：
  - `common` —— 通用词（删除/关闭/未命名/新会话/轮）
  - `app` —— 应用名/模式/loading
  - `emptyState` —— 3 个示例 prompt
  - `chatInput` —— 输入框提示
  - `judge` —— 4 段标题 + 降级提示
  - `panelStatus` —— 5 个状态 + 角色标签
  - `moaConfigBar` —— 模式/策略/就绪状态
  - `settingsModal` —— 14 个 Provider/Templates 标签
  - `templatesModal` —— 11 个模板管理标签
  - `sidebar` —— 11 个侧栏标签
  - `errors` —— 12 个错误码
  - `help` —— 11 个帮助 Section
- **语言切换**：i18n.changeLanguage + 持久化到 config.json

### 3.10 Tauri 后端（极简）

#### 3.10.1 `lib.rs`（18 行）

- 注册 `tauri_plugin_http::init()` —— 允许 webview 通过 Rust origin 发 fetch，绕过 CORS
- 注册 `tauri_plugin_fs::init()` —— 允许 webview 读写 appDataDir

#### 3.10.2 `main.rs`（6 行）

- `#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]` —— release 模式隐藏控制台
- 调 `verdex_lib::run()`

#### 3.10.3 `capabilities/default.json`（19 行）

- `core:default`
- **`http:default`** + allow `http://**` + `https://**`（允许任何 http(s) URL）
- `fs:allow-appdata-read` / `fs:allow-appdata-write` / `fs:allow-appdata-meta`（HANDOFF 坑 3 强调必须用 appdata 命名空间的权限）

#### 3.10.4 `tauri.conf.json`（39 行）

- `identifier: com.verdex.app`
- 窗口 1280×832（最小 720×520）
- `frontendDist: "../dist"`（生产前端静态文件）
- `devUrl: "http://localhost:1420"`（Vite dev server）
- `beforeDevCommand: "npm run dev"` / `beforeBuildCommand: "npm run build"`
- `bundle.targets: "all"`（生成 Windows installer + macOS dmg + Linux AppImage + deb）
- `csp: null`（不设 CSP，因为要发任意 URL）

### 3.11 图标生成（`scripts/gen-icon.mjs`，157 行）

- **零依赖**：纯 Node.js + zlib
- 1024×1024 RGBA PNG
- 蓝→紫对角渐变背景
- 中心 "V" 字（用距离场 + 距离到线段）
- 圆角 220 透明角
- 输出 `app-icon.png`（再由 `tauri icon` 切出全套 .ico/.icns/各 Square 尺寸）

### 3.12 CI/CD（`.github/workflows/release.yml`，57 行）

- **触发**：`push tags: v*`
- **4 平台矩阵**：
  - macOS arm64 (`--target aarch64-apple-darwin`)
  - macOS x64 (`--target x86_64-apple-darwin`)
  - Ubuntu 22.04（需装 libwebkit2gtk-4.1-dev / libappindicator3-dev / librsvg2-dev / patchelf）
  - Windows 默认
- **Node 22** + **Rust stable**
- **tauri-action@v0** 自动创建 GitHub Release

### 3.13 测试覆盖（26 个用例）

#### 3.13.1 `test/httpClient.test.ts`（8 个）

`extractAnthropicSystem` 全部 8 个边界：单/多 system、首条 assistant 注入、纯 system 注入、empty 默认注入、多轮交替

#### 3.13.2 `test/moaEngine.test.ts`（18 个）

- **`checkInputLimits` 7 个**：空输入/短输入/超限拒绝/边界接受/累加超限/边界接受/数字格式化
- **`parseJudgeResponse` 11 个**：干净 JSON / 代码围栏剥离 / 嵌入 prose 提取 / 字段 trim / 数组值拼接 / 缺字段占位 / 垃圾输入回退 / 空输入回退 / 畸形 JSON 回退 / 超长截断

---

## 四、技术栈

| 层 | 技术 | 版本 |
|---|---|---|
| 桌面壳 | Tauri | 2.x |
| Rust 工具链 | rustc | 1.70+ |
| 前端框架 | React | 18.3.1 |
| 类型系统 | TypeScript | 5.6.3 |
| 构建工具 | Vite | 5.4.10 |
| 样式 | Tailwind CSS | 4.0.0（CSS-first + `@theme` 桥接） |
| 国际化 | i18next | 26.3.6 + react-i18next 17.0.9 |
| 测试 | Vitest | 4.1.10 |
| Tauri HTTP 插件 | @tauri-apps/plugin-http | 2.0.1 |
| Tauri FS 插件 | @tauri-apps/plugin-fs | 2.5.1 |
| Tauri API | @tauri-apps/api | 2.1.1 |
| Tauri CLI | @tauri-apps/cli | 2.1.0 |
| Vite React 插件 | @vitejs/plugin-react | 4.3.3 |
| Tailwind Vite 插件 | @tailwindcss/vite | 4.0.0 |

**明确不使用**：LangChain / AutoGen / 任何第三方 AI 框架 / 任何 UI 组件库（纯手写 Tailwind）/ 任何状态管理库（纯 useState + useRef）

---

## 五、关键代码片段

### 5.1 Promise.all 防失血（`moaEngine.ts` 行 539-572）

```typescript
// Phase 1: Panels
const results = await Promise.all(
  panelProviders.map((p) =>
    runPanel(p, request.prompt, request.panelRoles[p.id], request, cb)
  )
);
cb.onPanelsComplete?.();

// Phase 2: Judges
await Promise.all(
  judgeProviders.map(({ spec, provider }) =>
    runSingleJudge(provider, buildJudgeSystemPrompt(results, spec.systemPrompt), request.prompt, request, cb)
  )
);
```

每个 `runPanel` / `runSingleJudge` 内部 try/catch + resolve，**永不 reject**。

### 5.2 预检跳过（`moaEngine.ts` 行 514-536）

```typescript
for (const p of resolvedPanels) {
  const max = p.capabilities?.maxContextChars;
  if (max !== undefined && max > 0 && promptLen > max) {
    cb.onPanelSkipped?.(p.id, i18n.t("errors.PANEL_SKIP_REASON", { prompt: promptLen.toLocaleString(), max: max.toLocaleString() }));
  } else {
    panelProviders.push(p);
  }
}
if (panelProviders.length === 0) {
  cb.onJudgeError?.("", i18n.t("errors.ALL_PANELS_SKIPPED"));
  return;
}
```

### 5.3 双协议 SSE delta 提取（`httpClient.ts` 行 221-240）

```typescript
function openaiDelta(json: unknown): string {
  const j = json as { choices?: { delta?: { content?: string } }[] };
  return j.choices?.[0]?.delta?.content ?? "";
}

function anthropicDelta(json: unknown): string {
  const j = json as { type?: string; delta?: { text?: string } };
  if (j.type === "content_block_delta") {
    return j.delta?.text ?? "";
  }
  return "";
}
```

### 5.4 Anthropic system 提取 + 首条 user 注入（`httpClient.ts` 行 150-174）

```typescript
export function extractAnthropicSystem(input: ChatMessage[]): { system: string; messages: ChatMessage[] } {
  const systemParts: string[] = [];
  const turns: ChatMessage[] = [];
  for (const m of input) {
    if (m.role === "system") {
      if (m.content.trim()) systemParts.push(m.content.trim());
    } else {
      turns.push(m);
    }
  }
  const system = systemParts.join("\n\n");
  let messages = turns;
  if (messages.length === 0 || messages[0].role !== "user") {
    messages = [
      { role: "user", content: system || "Please begin." },
      ...messages,
    ];
  }
  return { system, messages };
}
```

### 5.5 流式超时 + 非流式回退（`httpClient.ts` 行 378-447）

```typescript
export async function streamChat(opts, onDelta) {
  const fetchImpl = await resolveFetch();
  const timeoutMs = opts.timeoutMs ?? 60000;
  const protocol = opts.protocol ?? "openai";
  const prepared = prepareRequest(opts);

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    return await streamSse(fetchImpl, prepared, protocol, controller.signal, onDelta);
  } catch (streamErr) {
    if (controller.signal.aborted) {
      throw new Error(i18n.t("errors.REQUEST_TIMEOUT", { s: Math.round(timeoutMs / 1000) }));
    }
    // Fallback to non-stream
    const fallbackController = new AbortController();
    const fallbackTimer = setTimeout(() => fallbackController.abort(), timeoutMs);
    try {
      const res = await fetchImpl(prepared.url, {
        method: "POST", headers: prepared.headers, body: prepared.nonStreamBody, signal: fallbackController.signal,
      });
      // ... extract + return
    } finally {
      clearTimeout(fallbackTimer);
    }
  } finally {
    clearTimeout(timer);
  }
}
```

### 5.6 60ms 节流刷新（`useMoa.ts` 行 538-570）

```typescript
const scheduleFlush = useCallback((sessionId, turnId) => {
  if (flushTimer.current) return; // already pending
  flushTimer.current = window.setTimeout(() => {
    flushTimer.current = null;
    const panels = { ...panelBuffers.current };
    const judges = { ...judgeBuffers.current };
    setSessions(prev => prev.map(s => s.sessionId !== sessionId ? s : {
      ...s, messages: s.messages.map(t => t.id !== turnId ? t : {
        ...t,
        panels: t.panels.map(p => panels[p.providerId] !== undefined ? { ...p, rawText: panels[p.providerId] } : p),
        judges: t.judges.map(j => judges[j.judgeId] !== undefined ? { ...j, raw: judges[j.judgeId] } : j),
      })
    }));
  }, 60);
}, []);
```

### 5.7 动态熔断（`useMoa.ts` 行 609-625）

```typescript
const configuredLimits = panelProviders
  .map((p) => p.capabilities?.maxContextChars)
  .filter((v): v is number => typeof v === "number" && v > 0);
const minContext = configuredLimits.length > 0 ? Math.min(...configuredLimits) : undefined;
const promptLimit = minContext ? Math.floor(minContext * 0.5) : undefined;
const contextLimit = minContext ? Math.floor(minContext * 0.8) : undefined;
const limitCheck = checkInputLimits(trimmed, history, promptLimit, contextLimit);
```

### 5.8 上下文窗口自动检测（`httpClient.ts` 行 503-549）

```typescript
// First try /models API (OpenAI only)
if (protocol !== "anthropic") {
  const modelsRes = await fetchImpl(`${normalizeBase(provider.baseUrl)}/models`, {
    headers: { Authorization: `Bearer ${provider.apiKey}` }, signal: modelsController.signal,
  });
  if (modelsRes.ok) {
    const modelsData = await modelsRes.json();
    const modelsRaw = modelsData?.data ?? modelsData;
    if (Array.isArray(modelsRaw)) {
      const match = modelsRaw.find(m => (m as {id?:string}).id === provider.modelString || (m as {id?:string}).id?.includes(provider.modelString));
      const ctxTokens = (match as {context_length?:number})?.context_length ?? (match as {context_window?:number})?.context_window;
      if (ctxTokens) detectedChars = tokensToChars(ctxTokens);
    }
  }
}
// Fallback to built-in DB
if (!detectedChars) detectedChars = lookupContextChars(provider.modelString);
```

### 5.9 Base URL 规范化（`httpClient.ts` 行 93-106）

```typescript
export function normalizeBase(baseUrl: string): string {
  let s = baseUrl.trim();
  while (true) {
    const before = s;
    s = s.replace(/\/+$/, "");                        // trailing slashes
    s = s.replace(/\/chat\/completions$/i, "");       // OpenAI endpoint
    s = s.replace(/\/messages$/i, "");                // Anthropic endpoint
    if (s === before) break;
  }
  return s;
}
```

### 5.10 删除 provider 级联清理（`useMoa.ts` 行 384-404）

```typescript
const removeProvider = useCallback((id: string) => {
  setProviders(prev => prev.filter(p => p.id !== id));
  setSessions(prev => prev.map(s => {
    const panelRoles: Record<string, string> = {};
    for (const [pid, rid] of Object.entries(s.config.panelRoles)) {
      if (pid !== id) panelRoles[pid] = rid;
    }
    return {
      ...s,
      config: {
        ...s.config,
        panelIds: s.config.panelIds.filter(pid => pid !== id),
        panelRoles,
        judgeIds: s.config.judgeIds.filter(jid => jid !== id),
      },
    };
  }));
}, []);
```

### 5.11 标题自动取前 24 字符（`useMoa.ts` 行 223-228）

```typescript
function titleFromPrompt(prompt: string): string {
  const clean = prompt.trim().replace(/\s+/g, " ");
  return clean.length > 24 ? clean.slice(0, 24) + "…" : clean || i18n.t("common.newSession");
}
```

---

## 六、集成点（如何接入 Verdex）

### 6.1 可被其他应用集成的接口

由于 Verdex 是个**纯本地桌面应用**（无后端），它的"集成"主要体现为：

1. **导入 config.json** —— 任何 Tauri/Electron/Node 桌面应用都可解析 `%APPDATA%\com.verdex.app\config.json` 来：
   - 读取用户的 provider 列表（含 API Key）
   - 读取 role/judge 模板
   - 复用会话历史
2. **复用 4 域数据模型**（`src/types/moa.ts`） —— `AIProvider` / `RoleTemplate` / `JudgePromptTemplate` / `MoASessionConfig` / `Turn` / `ChatSession` 都是纯 TS 接口，零依赖，可直接 import
3. **复用 moaEngine** —— `runMoaSynthesis` 是个纯 async 函数，参数是 `SynthesisRequest + AIProvider[] + MoaCallbacks`，可在任何 TS 项目中调用
4. **复用 httpClient** —— `streamChat` + `testProvider` + `normalizeBase` + `extractAnthropicSystem` 都是独立的纯函数

### 6.2 配置 JSON Schema（v0.1.1）

```json
{
  "providers": [
    { "id": "uuid", "name": "string", "modelString": "string", "baseUrl": "string", "apiKey": "string", "protocol": "openai|anthropic", "capabilities": { "maxContextChars": 128000 } }
  ],
  "roleTemplates": [
    { "id": "string", "name": "string", "systemPrompt": "string" }
  ],
  "judgePrompts": [
    { "id": "string", "name": "string", "systemPrompt": "string" }
  ],
  "sessions": [
    {
      "sessionId": "uuid", "title": "string", "createdAt": 1234567890,
      "config": {
        "mode": "simple|advanced",
        "panelIds": ["uuid"],
        "panelRoles": { "panelId": "roleTemplateId" },
        "judgeIds": ["uuid"],
        "judgeStrategy": "single|collision",
        "judgePromptId": "string|null",
        "collisionJudgePromptIds": ["string"]
      },
      "messages": [
        {
          "id": "uuid", "prompt": "string", "createdAt": 1234567890,
          "panels": [{ "providerId": "uuid", "label": "string", "model": "string", "status": "pending|streaming|done|error|skipped", "rawText": "string", "error?": "string", "roleName?": "string" }],
          "judges": [{ "judgeId": "uuid", "label": "string", "status": "pending|judging|streaming|done|error", "raw": "string", "response": { "consensus": "string", "divergence": "string", "blindspots": "string", "verdict": "string" } | null, "error?": "string" }]
        }
      ]
    }
  ],
  "currentSessionId": "uuid|null",
  "language": "en|zh",
  "theme": "dark|light|soft"
}
```

### 6.3 跨平台部署要点

- **Windows**：`%APPDATA%\com.verdex.app\config.json`
- **macOS**：`~/Library/Application Support/com.verdex.app/config.json`
- **Linux**：`~/.local/share/com.verdex.app/config.json`
- **开发模式 fallback**：浏览器 localStorage `verdex.config`

### 6.4 外部 API 端点（panel/judge 可调用的目标）

Verdex 本身**不提供** API 端点，它**消费**第三方 API：
- OpenAI 兼容：`POST {base}/chat/completions`
- OpenAI 兼容 /models：`GET {base}/models`（仅用于自动检测 context）
- Anthropic：`POST {base}/v1/messages`

**预置 4 个免费/付费端点**（需用户填入 API Key）：
- Groq Llama 3.3 70B（免费层）
- Qwen Plus（阿里云 DashScope）
- DeepSeek Chat（深度求索）
- Claude 3.5 Sonnet（Anthropic）

### 6.5 嵌入 MoA 引擎到其他项目

最小接入代码（伪代码）：

```typescript
import { runMoaSynthesis } from "verdex/services/moaEngine";

await runMoaSynthesis(
  {
    prompt: "用户问题",
    panelIds: ["provider1", "provider2", "provider3"],
    panelRoles: {}, // 或 { "provider1": "criticalScrutiny" }
    judges: [{ providerId: "judge1", systemPrompt: "..." }],
  },
  providers, // AIProvider[]
  {
    onPanelStart: (id) => console.log("panel started", id),
    onPanelDelta: (id, d) => appendToUI(id, d),
    onPanelDone: (id, text) => console.log("panel done", id, text),
    onJudgeDone: (id, resp, raw) => console.log("verdict", resp),
  }
);
```

零额外依赖（仅需 React + TS + Vite）。

### 6.6 已识别的扩展点（HANDOFF 第四节 "待做"）

1. **多轮上下文记忆**（下一核心任务）—— 每 Panel 独立 history + 摘要压缩
2. **SettingsModal 暴露 maxContextChars 输入**（类型已有，UI 未做）
3. **导出对话**（markdown/JSON）
4. **会话搜索**
5. **IndexedDB 替代 localStorage**（突破 5MB 限制）

---

## 七、架构亮点与设计哲学

### 7.1 单一真相源原则

- **数据结构**：`types/moa.ts` 一个文件 300 行覆盖全部领域模型
- **内置模板**：`config.template.json` 一个文件覆盖全部出厂默认
- **三语言 i18n**：`en.json` / `zh.json` / `index.ts` 三个文件覆盖全部 UI 文案

### 7.2 关注点分离

- **HTTP 层**（httpClient.ts）不依赖 React
- **引擎层**（moaEngine.ts）不依赖 React、不依赖 UI
- **状态层**（useMoa.ts）只管 state + 持久化，不渲染
- **组件层**（components/）只接收 props + 触发回调

### 7.3 防失血设计

- `runPanel` 永远 resolve（永不 reject）
- `runSingleJudge` 永远 resolve
- 任何 panel/judge 失败不影响其他并发
- 引擎顶层 `runMoaSynthesis` 也永不抛错（错误通过 callback 传递）

### 7.4 数据快照保护

- `PanelState` / `JudgeState` 含 `label` / `model` / `roleName` 字段
- 即使 provider 后续被编辑/删除，历史记录仍显示当时的真实值

### 7.5 CSS-first 主题

- 所有颜色都是 CSS 变量
- Tailwind v4 `@theme` 桥接
- 主题切换 = 改 `<html data-theme="...">` 一行
- 修改 `src/index.css` 即可全主题定制

### 7.6 零依赖图标生成

- `scripts/gen-icon.mjs` 157 行手写 PNG 编码 + CRC32 + zlib
- 不需要 sharp/canvas/puppeteer 等二进制依赖

### 7.7 完整的中英双语

- UI 文案 100% 翻译
- 提示词模板 100% 翻译（5 角色 × 2 语言 + 3 judge × 2 语言 = 16 个内置模板）
- 错误信息 100% 翻译
- 文档 100% 双语（README.md + README_CN.md）

### 7.8 透明性

- **无埋点**、**无 telemetry**、**无第三方调用**
- 唯一的网络出口是用户配置的 provider API
- config.json 明文可读、可手编、可备份

---

## 八、当前已知限制与未来方向（来自 HANDOFF.md）

### 8.1 待做（核心）

1. **多轮上下文记忆**（下一核心任务）
   - 每 Panel 独立历史 messages 数组
   - 摘要压缩（超限时调模型总结）
   - `maxContextChars` UI 暴露
   - 熔断从硬拒改为"超限触发压缩"

### 8.2 待做（次要）

2. SettingsModal 暴露 maxContextChars 编辑框
3. 导出对话（markdown/JSON）
4. 会话搜索
5. IndexedDB 替代 localStorage（5MB 上限突破）

### 8.3 已知低优先级问题

- `extractAnthropicSystem` 纯 system 输入时会双发（潜伏 bug）
- `toggleSidebar` / `clearError` 未 memoize
- `SettingsModal` / `TemplatesModal` 在 App.tsx 两个分支重复挂载（维护隐患）
- `TemplatesModal.tsx` 已被 `SettingsModal` 的 Templates tab 取代（重复代码）

### 8.4 历史已修 Bug（HANDOFF 第三节）

1. Anthropic 流式缺 `stream: true`（HANDOFF 坑 7）
2. SettingsModal `runTests` 缺 try/finally
3. SettingsModal 重开时 `testing` 不重置
4. `collisionJudgePromptIds` 不清理

---

## 九、绝对不能再踩的坑（HANDOFF 第五节，摘录）

1. **React Hooks 规则** —— early return 必须在所有 hooks 之后
2. **bash 子进程里 `%APPDATA%` 不展开** —— 用正斜杠全路径或 `$APPDATA`
3. **Tauri 2 的 fs 权限** —— 必须用 `fs:allow-appdata-*`，不能用通用 `fs:allow-read-file`
4. **端口 1420 占用** —— `netstat` + `taskkill` 清理
5. **Tauri dev 模式的 Vite server** —— 内部机制不是多余服务
6. **JS console.log 不转发** —— 用 DebugOverlay 或 localStorage 调试
7. **Anthropic 流式必须 body 带 `stream: true`** —— 仅设 Accept 头不够

---

## 十、总结

**Verdex** 是一个**工程完成度极高**的本地 MoA 桌面客户端：
- 前端 ≈ 3600+ 行 TypeScript（无第三方 AI 框架）
- 后端 24 行 Rust（仅注册两个 plugin）
- 26 个单元测试覆盖关键纯函数
- 完整的双协议（OpenAI/Anthropic）SSE 流式 + 非流式回退
- 40+ 模型上下文窗口数据库
- 4 域完全解耦的全局状态 + 会话级 MoA 配置
- 3 主题 + 2 语言 + 4 段裁决 UI
- 4 平台构建矩阵 CI/CD
- 极简的 4 个内置 provider + 16 个内置模板（启动即用）

**对 MoA Gateway Pro 的价值**：
- 完整的 `moaEngine.ts` + `httpClient.ts` + `modelContextDB.ts` 可作为**多模型调度的参考实现**
- `config.template.json` 的 4 域数据模型可作为**多租户 provider 池的起点**
- `useMoa.ts` 的状态机模式（4 域 CRUD + 节流流式 + 防抖持久化）可作为**复杂异步状态参考**
- `extractAnthropicSystem` + `parseJudgeResponse` 可作为**协议适配层 + 兜底解析器的样板**
- 主题系统（CSS 变量 + Tailwind v4 @theme）是**多主题桌面的最佳实践**
