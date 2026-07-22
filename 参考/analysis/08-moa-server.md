# 08-moa-server 深度分析

## 项目概述

**08-moa-server** 是一个轻量级 **Mixture of Agents (MoA)** 代理服务器,对外暴露与 **OpenAI Chat Completions API 完全兼容** 的 HTTP 接口。它通过 FastAPI + Uvicorn 实现,核心思想是:

- **并行调用** 多个 reference model(参考模型)对同一个对话产生候选答案
- 由一个 **aggregator model**(聚合模型)读取所有候选答案并合成最终答案
- 所有下游模型调用都通过 **OpenAI 兼容的 Chat Completions HTTP API**(例如 vLLM / LiteLLM / Ollama / OpenRouter / llama.cpp server / OpenAI),**不加载任何本地 transformers/torch 模型**
- 完整保留 `tools` / `tool_choice` / `response_format` / 流式输出 / 错误格式等 OpenAI 协议行为,客户端无需任何修改即可对接

整个项目仅 9 个源文件 / 约 2400 行 Python 代码,模块边界清晰,设计目标明确:做一个"瘦中间层",把所有"推理"都委托给下游 OpenAI 兼容后端。

文件清单(全部已逐行阅读):

| 文件 | 行数 | 角色 |
|---|---:|---|
| `__init__.py` | 8 | 包入口,re-export 配置常量与 `MixtureOfAgents` |
| `config.py` | 37 | 配置常量(模型名 / temperature / max_tokens) + 环境变量解析 |
| `logging_utils.py` | 106 | 终端日志 + 敏感字段脱敏 + JSON 格式化与截断 |
| `requirements.txt` | 5 | fastapi / uvicorn / openai / pydantic / loguru |
| `run.sh` | 29 | 一键启动脚本 + 所有常用环境变量示例 |
| `server.py` | 483 | FastAPI HTTP 层:中间件、路由、参数净化、错误处理 |
| `moa.py` | 851 | MoA 核心编排:并行参考、注入提示、聚合、工具调用、重试 |
| `models.py` | 533 | OpenAI 兼容下游客户端:非流式/流式调用、工具调用规范化 |
| `README.md` | 615 | 俄文使用文档 |

---

## 核心模块清单

```
moa_server/
├── __init__.py            包入口
├── config.py              静态默认配置(模型名、temperature、max_tokens)
├── logging_utils.py       日志 + 脱敏 + JSON 美化
├── models.py              OpenAIChatModel:OpenAI 兼容 API 异步客户端
├── moa.py                 MixtureOfAgents:核心 MoA 编排器
├── server.py              FastAPI:HTTP 服务 + 中间件 + 工具注册表
├── run.sh                 启动脚本
├── requirements.txt       依赖
└── README.md              文档
```

模块依赖方向(单向、无环):

```
server.py ──► moa.py ──► models.py
        └─► logging_utils.py ◄──┘
        └─► config.py(只读默认值)
```

---

## 详细能力列表

### 1. HTTP 服务能力(server.py)

#### 1.1 OpenAI 兼容端点
- `POST /v1/chat/completions`:聊天补全,支持 `stream: true/false`
- `GET /v1/models`:列出当前 MoA 路由涉及的全部模型(reference + aggregator 去重),按 `{"object":"list","data":[{"id":...,"object":"model","owned_by":"moa-downstream"}]}` 格式返回

#### 1.2 工具注册表 `ToolRegistry` 与内置工具
- `ToolRegistry.register(name)` 装饰器把本地函数注册成 OpenAI 风格的 tool
- 默认注册 2 个示例工具:
  - `echo(args)`:原样回显参数
  - `get_current_time(args)`:返回 UTC ISO8601 时间戳
- 通过 `tool_executor(name, arguments)` 统一调度

#### 1.3 自定义 ASGI 请求日志中间件 `RequestBodyLoggingMiddleware`
- **纯 ASGI 实现**,不用 `BaseHTTPMiddleware`,避免 StreamingResponse 上的 `http.request` 重复消息导致 "ASGI callable returned without completing response" 问题
- 进入时一次性消费请求体并缓存,异步回放给下游 FastAPI
- 关键能力:
  - 为每个请求生成 `req_<ms_timestamp>` 形式的 `request_id`
  - 记录 method / path / query / client IP / content-type / content-length
  - 对 `/v1/chat/completions` 路径额外 DEBUG 记录完整 header(脱敏)+ body(可读 JSON)
  - `replay_receive` 在回放 body 之后继续转发真实 ASGI 事件,过滤掉多余的 `http.request` 消息,避免破坏 StreamingResponse 的 disconnect 监听
  - 异常路径记录 `traceback.format_exc()` 并抛出
  - 4xx/5xx 时记录错误日志,2xx/3xx 时记录 done 日志

#### 1.4 FastAPI 异常处理
- `RequestValidationError → 422`:返回 OpenAI 风格错误结构(在 `error` 字段内嵌套 `details` 保存验证详情),同时把原始 body / headers 写到 ERROR 日志
- `HTTPException`:保留 status_code,把 detail 透传到响应体(detail 为 dict 时原样返回,否则包成 `{"detail":...}`)

#### 1.5 参数白名单与净化
- 硬编码 `DOWNSTREAM_CHAT_PARAM_ALLOWLIST`(约 25 项 OpenAI 官方参数 + 部分新版参数)
- 通过 `MOA_ALLOWED_EXTRA_DOWNSTREAM_PARAMS` 环境变量追加 backend 特定参数(如 `top_k,min_p`)
- 永远剥离 `headers / extra_headers / extra_query / timeout / api_key / base_url / organization / project`
- `model / messages / stream` 在 `server_only` 集合中,不会直接转给 OpenAI SDK kwargs(由 `models.py` 显式注入)
- 被剥离的字段会以 WARNING 日志打印 `dropped keys=... values=...`

#### 1.6 `tool_choice` 规范化
- `_normalize_tool_choice`:支持 `auto / none / required` 字符串以及函数名简写
- 函数名简写会被包装成 `{"type":"function","function":{"name":<name>}}`
- dict 形式原样透传

#### 1.7 `/v1/chat/completions` 处理流程
1. 解析 JSON,失败时返回 400 `invalid_request_error`
2. 校验 `messages` 是 list 且每条都是 dict,每条 `role` 是字符串
3. 读取 `temperature`(默认 0.7)、`max_tokens`(默认 512)、`stream`(默认 false)
4. 调用 `_downstream_openai_params` 净化下游参数
5. 通过 `moa_instance.chat` 或 `moa_instance.chat_stream` 执行 MoA 编排
6. 非流式 → `JSONResponse`;流式 → `StreamingResponse(media_type="text/event-stream")`,末尾追加 `data: [DONE]\n\n`
7. 任何 `Exception` 都被包装成 OpenAI 风格 `server_error` HTTPException

#### 1.8 请求体解析策略
- **故意使用 `await request.json()` 而不是严格 Pydantic 模型** —— 保证对 OpenAI 客户端发来的"非主流"字段(`content` 数组、`tool_calls`、`function_call`、`parallel_tool_calls`、`response_format`、`extra_body`、`metadata` 等)的兼容性
- 仅对 `messages` 数组元素做最轻量的 shape 校验

### 2. MoA 编排能力(moa.py)

#### 2.1 `MixtureOfAgents` 类
构造参数:
- `reference_models`:参考模型名列表(默认取自 `DEFAULT_REFERENCE_MODELS`)
- `aggregator_model`:聚合模型名(默认取自 `DEFAULT_AGGREGATOR_MODEL`)
- `tool_executor`:可选回调,签名 `(tool_name, arguments_dict) -> Any`,被 `asyncio.to_thread` 包装成异步
- `auto_execute_tools`:是否在服务器端执行 aggregator 的 tool calls(亦受 `MOA_AUTO_EXECUTE_TOOLS` 控制)

实例属性:
- `reference_model_names` / `aggregator_model_name`
- 懒构造的 `OpenAIChatModel` 实例字典 `_reference_models` / 单例 `_aggregator_model`
- `max_aggregation_attempts`(`MOA_MAX_AGGREGATION_ATTEMPTS`,默认 2,最小 1)

#### 2.2 并行 reference 调用
- `asyncio.gather` 并行调用所有 reference 模型,**全部非流式**,**共享同一份 `messages / tools / tool_choice / openai_params`**
- 收集到 `ReferenceResult(model, content, tool_calls, finish_reason)`
- `to_aggregation_dict()` 把结果序列化为可注入 prompt 的 dict

#### 2.3 聚合上下文构建 `_inject_references`
采用 **"inline system + 调整后的 user prompt"** 模式(而不是老的 `[agg_system, *originals]` 模式),因为后者会破坏部分 OpenAI 兼容后端的 native tool calling:

- **保留调用者的原始消息顺序与 role**
- 若第一条是 system,聚合指令追加到该 system 后面;否则在头部 `insert(0, ...)` 一个 system
- 聚合指令包含:
  - "你是 Mixture-of-Agents 系统的聚合者"的身份声明
  - "权威任务来源:同一请求中活动 user prompt 才是任务,reference 输出只是候选"
  - "批判性地评估,不要直接拼接"
  - 显式 tool-call 规则:reference 的 tool_calls 是**建议/证据**,若真要调用,aggregator 必须用原始 tool schema 重新 emit,不要伪造 tool result
  - 完整 JSON 序列化的 `serialized_references`(包含 content + tool_calls + finish_reason)
- `_append_original_request_to_latest_user`:在最后一个 user 消息末尾追加 `Original user request: ...` 块,保持 user role 作为活动 prompt

#### 2.4 工具结果消息构造 `_execute_tools`
- 给每个 tool_call 分配 `tool_call_id`(若缺省则生成 `call_<uuid>`)
- 用 `asyncio.to_thread` 同步执行用户函数,避免阻塞事件循环
- 工具结果以 OpenAI 标准格式返回:`{"role":"tool","tool_call_id":...,"content":json.dumps(result, ensure_ascii=False)}`
- 执行失败时 `content` 序列化为 `{"error": str(exc)}`,不抛出

#### 2.5 工具调用 name/arguments 抽取 `_tool_name_and_args`
- 同时支持 OpenAI 嵌套 `function.arguments`(可能为 JSON 字符串)和扁平 `name/arguments` 形式
- `arguments` 为字符串时尝试 `json.loads`;失败时退回 `{"_raw_arguments": <原文>}`
- 缺 `name` 抛 `ValueError`

#### 2.6 旧式 JSON 工具调用解析 `_parse_json_tool_calls`
- 识别 ``` ```json ... ``` ``` 与 ``` ``` ... ``` ``` 包裹
- 尝试 `json.loads`,若顶层 dict 有 `tool_calls` 字段且是 list 则返回
- 用于兼容不原生支持 tool_calls 的老模型

#### 2.7 非流式 chat 主流程
1. `asyncio.gather` 并行获取 reference 结果
2. 注入参考输出 + 注入 `response_format` 强化指令
3. 调 `_generate_aggregated_with_retries` 拿 `Completion`
4. 解析 tool_calls(优先原生,fallback 到旧式 JSON 解析)
5. **三种收尾分支**:
   - `tool_calls + tool_executor + auto_execute_tools` → 拼出 assistant tool 消息 + tool 角色结果,再调一次 aggregator(`tool_choice="none"` 避免循环),`phase="after_tool_execution"`
   - `tool_calls` 但未开 auto-execute → 模仿 OpenAI 行为:`finish_reason="tool_calls"`,**让客户端自己执行工具**
   - 无 tool_calls → 正常返回文本
6. 包成 OpenAI 标准响应:`{"id":"chatcmpl-<uuid>","object":"chat.completion","created":<ts>,"model":<aggregator>,"choices":[...]}`

#### 2.8 流式 chat_stream 主流程
- **参考模型始终非流式**,仅最后的 aggregator 调用流式
- 关键防御逻辑: **只在确实产生 content 或 tool_call delta 之后才发送 `role` 头**,这样若下游只返回 role 就断流,客户端不会看到残缺的开头
- `MOA_MAX_AGGREGATION_ATTEMPTS` 次重试,每次重试用 `_messages_for_empty_retry` 加一段"上轮空响应,重新生成"的 system 指令
- 全部 streaming 尝试都空时,回退到非流式调用再切成 120 字符的 chunk 推送(`MOA_STREAM_FALLBACK_CHUNK_CHARS` 可调)
- 流结束帧的 `finish_reason`:
  - 有 tool_call delta → `"tool_calls"`
  - 否则透传下游 `finish_reason`,缺失则用 `"stop"`

#### 2.9 聚合重试 `_generate_aggregated_with_retries`
- 默认 2 次,环境变量 `MOA_MAX_AGGREGATION_ATTEMPTS` 可调
- "空响应"判定:`content` 空白 **且** 无原生 tool_calls **且** 无旧式 JSON tool_calls
- 非空时立即返回
- 空响应时 DEBUG 输出 `to_dict()` 原始聚合消息、WARNING 报告是否重试

#### 2.10 response_format 强化指令
- `json_schema` → 注入"必须严格符合该 schema,只返回合法 JSON,不要包 Markdown"指令,附 schema 全文
- `json_object` → 注入"必须返回合法非空 JSON object,只返回 JSON"指令
- 其他类型 → 通用 "honor this response_format exactly" + 全文
- 同样采用 "原 system 追加,无 system 则 insert" 的非破坏式注入

#### 2.11 日志埋点
- 整个聚合过程至少有:参考数量、聚合消息数、首条原消息 role、attempt 计数与 phase、`response_format` / `tool_choice`、聚合消息 debug dump、原始 `to_dict()` dump
- phase 取值:`initial` / `after_tool_execution` / `stream_fallback_non_streaming`

### 3. OpenAI 兼容客户端能力(models.py)

#### 3.1 `Completion` 数据类
- 字段:`content / role / tool_calls / finish_reason`
- `to_dict()`:当 `tool_calls` 存在时,`content` 为 None(对齐 OpenAI native 行为 —— tool-call assistant message 的 content 必须是 null,否则客户端工具循环会断)

#### 3.2 `OpenAIChatModel` 客户端
- 构造时按模型名解析 base_url 与 api_key(见下)
- 内部 `client: AsyncOpenAI` 实例
- 关键保护:
  - `RESERVED_KWARGS = {model, messages, stream, tools, tool_choice}` —— 在合并 openai_params 时被剔除,保证由函数显式注入,避免覆盖
  - `ALLOWED_CHAT_KWARGS` 约 25 项 + 用户可配置额外项
  - **绝不让** `litellm_session_id` / `headers` / `extra_headers` / `extra_query` / `timeout` / `api_key` / `base_url` / `organization` / `project` 这些客户端控制类字段泄漏到 OpenAI SDK
  - `stream_options` 仅在 `stream=True` 时保留(非流式调用被显式 pop 掉,部分后端会在无 stream 时拒绝它)

#### 3.3 消息清洗 `_clean_messages`
- 仅保留 `role / content / name / tool_call_id / tool_calls` 字段
- `tool` 角色消息的 `content` 可为 None;其他角色若没有 `content` 字段则补空串,防止部分 provider 拒绝

#### 3.4 两种调用形态
- `await generate(...)`:非流式,返回 `Completion`(同时也是 `await stream_chat(...)` 之外的内部默认路径)
- `async for ... in stream_chat(...)`:流式,**同时保留 content delta 和原生 streaming tool_calls delta**,用于 `chat_stream` 的最终聚合步骤
- `await generate_stream(...)`:仅推送 content 文本片段(辅助接口,主流程未使用)

#### 3.5 Tool_calls 规范化
- `_normalize_tool_calls`:把 SDK `tool_calls` 对象转 JSON 友好的 list[dict],字段为 `id / type / function.{name, arguments}`
- `_normalize_stream_tool_call_deltas`:流式 tool_call delta 的特殊处理 —— 保留 `index`、首个 delta 的 `id / type`、部分 `function.arguments` 字符串,**不**在服务端做"粘合",直接代理给客户端

#### 3.6 Base URL / API Key 解析优先级
按模型名 slug(非字母数字字符替换为 `_`、转大写):

```
MOA_MODEL_<SLUG>_API_KEY
  > MOA_MODEL_<SLUG>_BASE_URL
  > MOA_OPENAI_API_KEY / MOA_OPENAI_BASE_URL
  > OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_API_BASE
  > 默认值("EMPTY" / None)
```

例如 `Qwen3.4-4B` → `MOA_MODEL_QWEN3_4_4B_BASE_URL`

### 4. 配置能力(config.py)

- `DEFAULT_REFERENCE_MODELS`:逗号分隔,默认 `["Qwen3.4-4B", "Gemma4-E4B"]`,来自 `MOA_REFERENCE_MODELS`
- `DEFAULT_AGGREGATOR_MODEL`:默认 `"Gemma4-E4B"`,来自 `MOA_AGGREGATOR_MODEL`
- `DEFAULT_TEMPERATURE`:默认 `0.7`,来自 `MOA_TEMPERATURE`
- `DEFAULT_MAX_TOKENS`:默认 `512`,来自 `MOA_MAX_TOKENS`
- `_split_list` 工具函数:对空字符串友好,自动 strip

### 5. 日志与脱敏能力(logging_utils.py)

#### 5.1 Logger 配置
- 名称 `moa_server`
- 默认 DEBUG,可通过 `MOA_LOG_LEVEL` 覆盖
- `propagate=False` 避免与 Uvicorn 重复
- 单例 handler,formatter:`%(asctime)s %(levelname)s [%(name)s] %(message)s`
- 输出 stdout

#### 5.2 敏感字段识别
- 关键字集合:`authorization / api_key / apikey / access_token / refresh_token / token / password / secret / client_secret / x-api-key`
- 任意键只要 lower-case 后**包含** `authorization / api_key / token / secret / password` 也命中
- `redact(value)`:递归处理 dict / list,命中字段替换为 `"<redacted>"`
- `redact_headers(headers)`:对 headers dict 同样规则处理

#### 5.3 JSON 美化与截断
- `format_json_for_log(value, max_chars=20000)`:尝试 `json.dumps(..., ensure_ascii=False, indent=2, default=str)`,失败则 `repr`
- 超过 `MOA_LOG_MAX_CHARS` 时追加 `... <truncated N chars>` 标记
- `decode_body_for_log(body, max_chars=...)`:先按 utf-8 decode,若 body 看起来是 JSON object/array 则再 parse 后走 redact 流程

### 6. 启动与运行能力(run.sh + uvicorn)

`run.sh` 提供的开箱即用配置:

- `MOA_REFERENCE_CONTEXT_MODE=trimmed` / `MOA_REFERENCE_PASS_TOOLS=0` / `MOA_REFERENCE_PASS_RESPONSE_FORMAT=0` / `MOA_IGNORE_REFERENCE_FAILURES=1`(注释中显示这是参考默认值,但当前代码并未读取这些环境变量,可能源自更早的版本或姊妹项目)
- 可选独立温度 `MOA_REFERENCE_TEMPERATURE=0.6` / `MOA_AGGREGATOR_TEMPERATURE=0.4`
- 允许后端特定参数 `MOA_REFERENCE_ALLOWED_EXTRA_PARAMS=top_k,min_p`(注意:代码实际读取的是 `MOA_ALLOWED_EXTRA_DOWNSTREAM_PARAMS`,命名差异需注意)
- 调试日志:`MOA_LOG_LEVEL=DEBUG` + `MOA_LOG_MAX_CHARS=50000`
- 共享下游:`OPENAI_BASE_URL=http://localhost:8001/v1` + `OPENAI_API_KEY=EMPTY`
- **每模型独立 endpoint 示例**:`MOA_MODEL_QWEN3_4_4B_BASE_URL=http://localhost:12345/v1` / `MOA_MODEL_GEMMA4_E4B_BASE_URL=http://localhost:12346/v1`
- 启动:`uvicorn server:app --host 0.0.0.0 --port 8000 --log-level debug`

### 7. 协议兼容性能力(汇总)

- **请求体兼容**:接受所有 OpenAI Chat Completions 字段以及 LiteLLM 风格扩展(`extra_body` 等),未知字段被剥离并 WARN
- **响应体兼容**:完整 OpenAI `chat.completion` 格式 + `chat.completion.chunk` 流式格式,末尾 `data: [DONE]\n\n`
- **错误格式兼容**:`{"error": {"message","type","param","code"}}` —— `invalid_request_error` 与 `server_error`
- **tool_calls 兼容**:支持原生 OpenAI tool_calls,聚合阶段把 reference 的 tool_calls 序列化为候选证据
- **response_format 兼容**:`json_object` 与 `json_schema`,都附加强化 system 指令
- **客户端 SDK 兼容**:README 展示 `openai.OpenAI(base_url=..., api_key=...)` 与 `client.chat.completions.create(stream=True)` 两种用法

### 8. 已知边界与限制(README 列出)

- 无内置 auth(需自部署在网关后)
- 公共请求 `model` 字段不切换 MoA 配置(routing 由 env 决定)
- 任一 reference 失败会让整个请求失败(无 partial-result 兜底)
- 只取第一个 downstream choice,即使 `n>1`
- 响应里**没有聚合后的 `usage` 字段**(参考调用 + 聚合调用的 token 都被丢弃)
- 无 health-check endpoint
- 服务端工具执行只支持**非流式**
- 实现是单层 reference + 单层 aggregation,没有多层 MoA 叠加
- DEBUG 日志会包含 prompt 与 response 原文

---

## 技术栈

### 语言与运行时
- **Python 3.9+**(README 显式要求,代码使用了 `from __future__ import annotations` 兼容 3.9+ 语法,例如 `set[str]`、`tuple[str, dict[str, Any]]` 等 PEP 604 / 585 注解均通过 `__future__` 兼容)

### Web 框架
- **FastAPI ≥ 0.111.0** —— HTTP 路由、异常处理、`JSONResponse`、`StreamingResponse`、`Request`
- **Uvicorn[standard] ≥ 0.29.0** —— ASGI 服务器,提供 `httptools / uvloop / websockets` 等扩展
- 启动方式:`uvicorn server:app --host 0.0.0.0 --port 8000`

### 下游模型 SDK
- **openai ≥ 1.0.0** —— `AsyncOpenAI` 异步客户端,`client.chat.completions.create(...)` 支持流式与非流式
- 选用 Async 客户端使得 MoA 编排可以 `asyncio.gather` 并行调用多个 reference

### 数据建模与校验
- **pydantic ≥ 1.10.0** —— 仅作为依赖项声明存在;**实际请求/响应处理并未使用 Pydantic 模型**,而是直接 dict 透传以保持对 OpenAI 客户端扩展字段的最大兼容

### 日志
- **loguru ≥ 0.7.0** —— 声明在 requirements 中,但实际代码统一使用 `logging_utils.get_logger()`(基于标准 `logging`),`loguru` 是冗余依赖(可能是历史遗留或预留)

### 标准库
- `asyncio`(并行 + `to_thread`)
- `json`(消息序列化)
- `uuid`(生成 chatcmpl id 与 tool_call id)
- `time`(`created` 时间戳)
- `os`(环境变量)
- `re`(`_model_env_slug`)
- `dataclasses`(`Completion` / `ReferenceResult`)
- `datetime`(`get_current_time` 工具)

### 部署形态
- 单进程 FastAPI + Uvicorn
- 下游依赖外部 OpenAI 兼容推理服务(vLLM / LiteLLM / llama.cpp / Ollama / OpenRouter / OpenAI 等)
- 默认端口 8000,无 auth,建议反代前置

---

## 关键代码片段

### A. MoA 编排核心:并行参考 + 注入 + 聚合 + 工具执行

`moa.py:486-592` `MixtureOfAgents.chat` 主流程:

```python
async def chat(self, messages, *, temperature, max_tokens, tools, tool_choice, openai_params):
    # 1) 并行调所有 reference
    references = await asyncio.gather(*[
        self._run_reference(name, messages, temperature=temperature,
                            max_tokens=max_tokens, tools=tools,
                            tool_choice=tool_choice, openai_params=openai_params)
        for name in self.reference_model_names
    ])

    # 2) 注入参考输出 + response_format 强化指令
    response_format = (openai_params or {}).get("response_format")
    agg_messages = self._inject_references(messages, references)
    agg_messages = self._inject_response_format_instruction(agg_messages, response_format)

    # 3) 聚合(含空响应重试)
    aggregator = await self._get_aggregator()
    agg_completion = await self._generate_aggregated_with_retries(
        aggregator, agg_messages,
        temperature=temperature, max_tokens=max_tokens,
        tools=tools, tool_choice=tool_choice, openai_params=openai_params,
        phase="initial",
    )

    # 4) 解析 tool_calls
    tool_calls = agg_completion.tool_calls or self._parse_json_tool_calls(agg_completion.content)
    finish_reason = agg_completion.finish_reason or "stop"
    if tool_calls:
        agg_completion.tool_calls = tool_calls

    # 5a) 服务端执行工具分支
    if tool_calls and self.tool_executor and self.auto_execute_tools:
        assistant_tool_message = {"role": "assistant", "content": agg_completion.content or "", "tool_calls": tool_calls}
        tool_messages = await self._execute_tools(tool_calls)
        final_messages = agg_messages + [assistant_tool_message] + tool_messages
        agg_completion = await self._generate_aggregated_with_retries(
            aggregator, final_messages,
            temperature=temperature, max_tokens=max_tokens,
            tools=tools, tool_choice="none", openai_params=openai_params,   # 关键:tool_choice="none" 防止循环
            phase="after_tool_execution",
        )
        finish_reason = agg_completion.finish_reason or "stop"
    # 5b) 客户端执行分支(OpenAI 默认行为)
    elif tool_calls:
        finish_reason = "tool_calls"

    # 6) OpenAI 格式响应
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": self.aggregator_model_name,
        "choices": [{
            "index": 0,
            "message": agg_completion.to_dict(),
            "finish_reason": finish_reason,
        }],
    }
```

### B. 聚合上下文构建:inline system + 调整 user

`moa.py:152-211` `_inject_references`:

```python
def _inject_references(self, messages, references):
    new_messages = [msg.copy() for msg in messages]
    serialized_references = [ref.to_aggregation_dict() for ref in references]
    original_request = self._latest_user_request_text(messages)

    system_text = (
        "You are the aggregator in a Mixture-of-Agents system.\n\n"
        "Authoritative task source: use the active user prompt in this same "
        "request as the task. The latest user prompt has also been adjusted "
        "with an explicit 'Original user request' block...\n\n"
        "Reference outputs: responses from the reference models are candidate "
        "answers/proposals only. Critically evaluate them...\n\n"
        "Important tool-call rule: reference models may have proposed tool_calls. "
        "Those tool_calls are serialized below exactly as they were returned by "
        "the downstream OpenAI-compatible APIs. Treat them as proposals/evidence "
        "only. If a tool should actually be called, emit your own "
        "OpenAI-compatible tool_calls...\n\n"
        "Serialized reference outputs and tool-call proposals:\n"
        f"{json.dumps(serialized_references, ensure_ascii=False, indent=2)}"
    )

    if new_messages and new_messages[0].get("role") == "system":
        new_messages[0] = new_messages[0].copy()
        new_messages[0]["content"] = f"{new_messages[0].get('content', '')}\n\n{system_text}"
    else:
        new_messages.insert(0, {"role": "system", "content": system_text})

    if original_request:
        self._append_original_request_to_latest_user(new_messages, original_request)

    return new_messages
```

### C. OpenAI 兼容下游客户端参数净化

`models.py:215-261` `_sanitize_openai_kwargs` —— 两层防御(在 `server.py` 之外再加一道):

```python
@classmethod
def _sanitize_openai_kwargs(cls, kwargs, *, stream):
    allowed = cls.ALLOWED_CHAT_KWARGS | cls._configured_extra_downstream_params()
    allowed = allowed | {"model", "messages", "stream"}

    sanitized, dropped = {}, {}
    for key, value in kwargs.items():
        if key in allowed:
            sanitized[key] = value
        else:
            dropped[key] = value

    for forbidden in ("headers", "extra_headers", "extra_query", "timeout",
                      "api_key", "base_url", "organization", "project"):
        if forbidden in sanitized:
            dropped[forbidden] = sanitized.pop(forbidden, None)

    if not stream and "stream_options" in sanitized:
        dropped["stream_options"] = sanitized.pop("stream_options", None)

    if dropped:
        get_logger().warning(
            "sanitized downstream kwargs dropped keys=%s values=%s",
            sorted(dropped.keys()), format_json_for_log(dropped),
        )
    return sanitized
```

### D. 纯 ASGI 请求日志中间件(规避 StreamingResponse bug)

`server.py:86-222` `RequestBodyLoggingMiddleware` 核心回放:

```python
async def replay_receive():
    """回放 body 一次,然后继续读真实 ASGI 事件,过滤多余 http.request。"""
    nonlocal replayed
    if disconnected and not replayed:
        replayed = True
        return {"type": "http.disconnect"}
    if not replayed:
        replayed = True
        return {"type": "http.request", "body": body, "more_body": False}

    while True:
        message = await receive()
        msg_type = message.get("type")
        if msg_type == "http.disconnect":
            return message
        if msg_type == "http.request":
            # body 已缓存,吞掉多余 http.request,不影响 StreamingResponse 的 disconnect 监听
            continue
        return message
```

### E. 流式代理 + 延迟发送 role 头

`moa.py:675-757` 关键流式逻辑:

```python
async for event in aggregator.stream_chat(attempt_messages, ...):
    delta = event.get("delta") or {}
    finish_reason = event.get("finish_reason")
    if finish_reason:
        downstream_finish_reason = finish_reason

    role = delta.get("role")
    if isinstance(role, str) and role:
        pending_role = role

    out_delta = {}
    content_delta = delta.get("content")
    if isinstance(content_delta, str) and content_delta != "":
        saw_output = True
        out_delta["content"] = content_delta

    tool_call_delta = delta.get("tool_calls")
    if tool_call_delta:
        saw_output = True
        saw_tool_calls = True
        out_delta["tool_calls"] = tool_call_delta

    # 关键:只产生真实 content/tool_call 时才补发 role 头
    if out_delta:
        if not sent_role:
            yield {"id": stream_id, "object": "chat.completion.chunk", "created": int(time.time()),
                   "model": self.aggregator_model_name,
                   "choices": [{"index": 0, "delta": {"role": pending_role or "assistant"}, "finish_reason": None}]}
            sent_role = True
        yield {"id": stream_id, "object": "chat.completion.chunk", "created": int(time.time()),
               "model": self.aggregator_model_name,
               "choices": [{"index": 0, "delta": out_delta, "finish_reason": None}]}

if saw_output:
    yield {... "finish_reason": terminal_finish_reason}   # 终止帧
    return
```

### F. 工具注册表 + 默认工具

`server.py:31-66`:

```python
class ToolRegistry:
    def __init__(self):
        self._funcs = {}

    def register(self, name):
        def decorator(func):
            self._funcs[name] = func
            return func
        return decorator

    def call(self, name, args):
        if name not in self._funcs:
            raise ValueError(f"Unknown tool: {name}")
        return self._funcs[name](args)

tool_registry = ToolRegistry()

@tool_registry.register("echo")
def tool_echo(args):
    return {"echo": args}

@tool_registry.register("get_current_time")
def tool_get_current_time(args):
    import datetime
    return {"current_time": datetime.datetime.utcnow().isoformat() + "Z"}

def tool_executor(name, arguments):
    return tool_registry.call(name, arguments)

moa_instance = MixtureOfAgents(tool_executor=tool_executor)
```

### G. 敏感字段脱敏

`logging_utils.py:48-66`:

```python
SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "access_token",
                  "refresh_token", "token", "password", "secret",
                  "client_secret", "x-api-key"}

def _is_sensitive_key(key: str) -> bool:
    key_lower = key.lower()
    return key_lower in SENSITIVE_KEYS or any(
        part in key_lower for part in ("authorization", "api_key", "token", "secret", "password")
    )

def redact(value):
    if isinstance(value, Mapping):
        return {str(k): ("<redacted>" if _is_sensitive_key(str(k)) else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
```

---

## 集成点

### 1. 作为网关嵌入 MoA Gateway Pro
- **协议层完全兼容 OpenAI** —— 在 Gateway Pro 内部可以把它当作"另一个 OpenAI 兼容 provider"接入,在 `models.py` 风格的客户端里指向 `http://<moa-server>:8000/v1`,传任意 `model` 名字(实际 routing 由 env 决定)
- **多 provider 路由** —— 由于支持每模型独立 `MOA_MODEL_<SLUG>_BASE_URL`,可以混合 vLLM(本地 GPU) + OpenAI(云端) + OpenRouter(路由) 作为不同 reference/aggregator
- **多级 MoA 嵌套** —— 把另一个 MoA server 作为下游 reference 或 aggregator(设置 `MOA_AGGREGATOR_MODEL=moa-via-base-url`),形成 2-3 层 MoA 叠加

### 2. 与 LiteLLM / 其它代理的协同
- 主动 **剥离** `litellm_session_id / extra_headers / extra_query` 等 LiteLLM 私有字段,即使被嵌入 LiteLLM 后端,也不会污染 OpenAI SDK 调用
- `MOA_ALLOWED_EXTRA_DOWNSTREAM_PARAMS` 提供后端特定参数白名单(如 `top_k, min_p`)

### 3. 与 vLLM / Ollama / llama.cpp 协同
- 所有下游都是 OpenAI 兼容 Chat Completions,**单镜像可同时驱动 GPU(vLLM)与 CPU(llama.cpp)推理**
- 默认 `OPENAI_API_KEY=EMPTY` 适配 vLLM/llama.cpp 这类关闭鉴权的服务
- 同时可在 `run.sh` 中演示两个本地推理服务(12345 / 12346)分别跑不同模型

### 4. 与 OpenAI Python SDK 客户端/Responses API 客户端
- README 给出 `from openai import OpenAI; client = OpenAI(base_url="http://localhost:8000/v1")` 用法
- 由于完全保留原生 `tool_calls` / `function_call` / `parallel_tool_calls` / `response_format` / `stream_options` 字段,`openai-agents` / `langchain` / `llamaindex` 等高层框架可直接对接,无需任何适配层

### 5. 作为内部服务的工具执行沙箱
- `ToolRegistry` + `MOA_AUTO_EXECUTE_TOOLS=1` 可在受控内网环境里让 aggregator 直接调本地 Python 函数
- 适合"内部 agent 平台"场景:MoA 选答案 → 调 Python 工具 → 合成自然语言结果,单次请求完成

### 6. 监控 / 审计集成点
- **纯 ASGI 中间件** 在 Starlette 之前接管,任何反向代理(Nginx / Envoy / Traefik)都可继续堆叠在它之前而不冲突
- 日志输出 stdout,可被任意 sidecar(如 Vector / Promtail)直接采集
- 已知 `request_id=req_<ms_ts>`,便于把客户端 trace 与 server 日志关联
- 缺失的健康检查端点需在反代层补齐(`/healthz` 转发到任意 200 即可)

### 7. 多语言 SDK 集成
- 任何实现了 OpenAI Chat Completions 客户端的 SDK 都能 0 改动对接:Node.js `openai`、Java `openai-java`、Go `go-openai`、Rust `async-openai`、C# `OpenAI-DotNet` 等
- 流式场景下 SSE 协议原生支持,无需协议转换
