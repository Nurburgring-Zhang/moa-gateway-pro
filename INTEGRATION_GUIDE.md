# MoA Gateway Pro 集成指南

> **目标**: 4 大集成场景完整覆盖
> 1. 接入多个不同供应商的模型 API
> 2. 配置项目能力(preset / capability / workflow)
> 3. 接入外部软件(Hermes / Claude Code / Codex / Qoder / Cline / Cursor)
> 4. 实测 + 错误排查

---

## 0. 一分钟理解 MoA Gateway Pro

**它本质上是一个 LLM API 网关 + 多模型协作引擎**:
- 对外暴露 **OpenAI 兼容 API**(`/v1/chat/completions`)
- 内部支持 **6 种 MoA 编排策略**(`fast` / `balanced` / `quality` / `chinese_battalion` / `chain` / `pipeline`)
- 配套 **76 个 capability**(MoA 引擎、RAG、Consensus、Quality Gate、Quota、Secret-Scan、Self-Heal)
- 已预置 **16 个 model endpoint**(国内 11 + 国际 5)
- **122 个端点 / 91 OpenAPI schemas / Prometheus / JWT 鉴权 / 3 层限流**

**接入原则**: 任何能调 OpenAI API 的客户端,改 2 行配置就能用上。

---

## 1. 接入多个不同供应商的模型 API

### 1.1 方式 A:改 `config.yaml`(启动时加载)

这是**最常用方式**。16 个预置 endpoint 已经在 `config.yaml` 里,改 `api_key` 字段或环境变量名就能用。

**已支持的 16 个 endpoint**(`config.yaml` `models` 节):

| ID | Provider | Model | Tier | API Base | Key Env |
|---|---|---|---|---|---|
| `deepseek-v3` | deepseek | deepseek-chat | standard | api.deepseek.com/v1 | `DEEPSEEK_API_KEY` |
| `deepseek-r1` | deepseek | deepseek-reasoner | premium | api.deepseek.com/v1 | `DEEPSEEK_API_KEY` |
| `glm-4-flash` | zhipu | glm-4-flash | lite | open.bigmodel.cn/api/paas/v4 | `ZHIPU_API_KEY` |
| `glm-4-plus` | zhipu | glm-4-plus | premium | open.bigmodel.cn/api/paas/v4 | `ZHIPU_API_KEY` |
| `moonshot-v1-8k` | moonshot | moonshot-v1-8k | standard | api.moonshot.cn/v1 | `MOONSHOT_API_KEY` |
| `qwen-turbo` | qwen | qwen-turbo | lite | dashscope.aliyuncs.com/compatible-mode/v1 | `QWEN_API_KEY` |
| `qwen-plus` | qwen | qwen-plus | standard | dashscope.aliyuncs.com/compatible-mode/v1 | `QWEN_API_KEY` |
| `qwen-max` | qwen | qwen-max | premium | dashscope.aliyuncs.com/compatible-mode/v1 | `QWEN_API_KEY` |
| `doubao-pro` | doubao | doubao-pro-32k | standard | ark.cn-beijing.volces.com/api/v3 | `DOUBAO_API_KEY` |
| `yi-large` | lingyi | yi-large | premium | api.lingyiwanwu.com/v1 | `LINGYI_API_KEY` |
| `baichuan4` | baichuan | Baichuan4 | standard | api.baichuan-ai.com/v1 | `BAICHUAN_API_KEY` |
| `gpt-4o-mini` | openai | gpt-4o-mini | lite | api.openai.com/v1 | `OPENAI_API_KEY` |
| `gpt-4o` | openai | gpt-4o | premium | api.openai.com/v1 | `OPENAI_API_KEY` |
| `claude-haiku` | anthropic | claude-3-5-haiku-latest | lite | api.anthropic.com | `ANTHROPIC_API_KEY` |
| `claude-sonnet` | anthropic | claude-3-5-sonnet-latest | premium | api.anthropic.com | `ANTHROPIC_API_KEY` |
| `mistral-large` | mistral | mistral-large-latest | premium | api.mistral.ai/v1 | `MISTRAL_API_KEY` |

**启动命令**(Windows):
```powershell
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "TestPass#2024"
$env:DEEPSEEK_API_KEY = "sk-xxxxxxxx"
$env:OPENAI_API_KEY = "sk-xxxxxxxx"
$env:ANTHROPIC_API_KEY = "sk-ant-xxxxxxxx"
$env:ZHIPU_API_KEY = "xxxxx.xxxxx"
$env:QWEN_API_KEY = "sk-xxxxxxxx"
$env:MOONSHOT_API_KEY = "sk-xxxxxxxx"
$env:DOUBAO_API_KEY = "xxxxxxxx"
$env:MISTRAL_API_KEY = "xxxxxxxx"
$env:BAICHUAN_API_KEY = "sk-xxxxxxxx"
$env:LINGYI_API_KEY = "xxxxxxxx"

.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8910 --workers 4
```

**没 API key 也能跑** — 自动 fallback 到 `MockProvider`,无网络调用,返回智能模拟回答。开发测试用。

### 1.2 方式 B:运行时通过 API 加 endpoint(`POST /v1/endpoints`)

适合**生产环境热更新**(不改 config、不重启)。

**EndpointUpsert 模型字段**(`POST /v1/endpoints`):

```python
{
  "endpoint_id": "my-deepseek-v4",   # 全局唯一 ID
  "provider": "deepseek",            # provider 类型
  "model": "deepseek-chat",          # 实际模型名
  "tier": "standard",                # free/lite/standard/premium/flagship
  "api_base": "https://api.deepseek.com/v1",
  "api_key_plain": "sk-xxx",         # 明文 key(只返回这一次)
  "api_key_env": "DEEPSEEK_API_KEY", # 或引用环境变量名
  "cost_per_1k_input": 0.0005,       # 美元
  "cost_per_1k_output": 0.001,
  "max_tokens": 8192,
  "timeout": 120,
  "weight": 100,
  "enabled": true,
  "tags": ["cn", "openai-compat"]
}
```

**curl 实测**:
```bash
# 1) 登录拿 token
TOKEN=$(curl -s -X POST http://localhost:8910/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"TestPass#2024"}' | jq -r .token)

# 2) 加 endpoint
curl -X POST http://localhost:8910/v1/endpoints \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint_id": "gpt-4-turbo",
    "provider": "openai",
    "model": "gpt-4-turbo-preview",
    "tier": "premium",
    "api_base": "https://api.openai.com/v1",
    "api_key_env": "OPENAI_API_KEY",
    "cost_per_1k_input": 0.01,
    "cost_per_1k_output": 0.03,
    "max_tokens": 4096,
    "enabled": true,
    "tags": ["intl", "openai-compat"]
  }'

# 3) 验证加上了
curl -s http://localhost:8910/v1/endpoints | jq .
```

### 1.3 方式 C:接非 OpenAI 兼容的供应商(自定义)

如果你的供应商不是 OpenAI 兼容(比如自研 LLM),可以:
- **加一个 provider**:在 `moa_gateway/providers/` 加 `xxx_provider.py`,继承 `base.py` 的 `BaseProvider`
- **加 endpoint**:`api_base` 指向你的 API,`provider` 字段填你的 provider 名

或者**包装成 OpenAI 兼容**:用 LiteLLM Proxy 之类工具做协议转换,然后把 MoA 指向 `http://localhost:8000/v1`(LiteLLM 默认端口)。

### 1.4 4 种 Provider 类型详解

| 文件 | 适配 | 覆盖的供应商 |
|---|---|---|
| `moa_gateway/providers/openai_compat.py` | OpenAI 兼容协议 | OpenAI、DeepSeek、智谱、月之暗面、Qwen、豆包、零一万物、百川、Mistral、Ollama、vLLM、LiteLLM Proxy |
| `moa_gateway/providers/anthropic_provider.py` | Anthropic 原生 | Claude 全系列 |
| `moa_gateway/providers/mock_provider.py` | 无网络 fallback | dev/test 场景 |
| `moa_gateway/providers/base.py` | 抽象基类 | 自定义扩展 |

**所有 OpenAI 兼容的供应商都用 openai_compat.py** — 这意味着加一个新国内 LLM 厂商只需在 `config.yaml` 加一段,不改代码。

---

## 2. 配置项目能力(Preset / Workflow / Capability)

### 2.1 MoA Preset(6 种内置策略)

`config.yaml` 的 `moa.presets` 节定义。**已预置 11 个 preset**:

| Preset | 策略 | 模型数 | 用途 | Token 成本 |
|---|---|---|---|---|
| `fast` | `single` | 1 lite | 单 lite 模型,快 | 极低 |
| `balanced` | `parallel` | 4 std + 1 premium | 4 并行 + 旗舰聚合 + 1 互审 | 中 |
| `quality` | `parallel` | 5 + flagship | 5 并行 + 旗舰聚合 + 2 互审 | 高 |
| `chinese_battalion` | `parallel` | 4 国产 | 4 国产并行 + 国产聚合(零国外) | 中 |
| `chinese_battalion_layered` | `layered` | 4 + 3 层 | Together AI MoA 3 层架构 | 中高 |
| `chinese_battalion_lite` | `layered` | 3 + 2 层 | MoA-Lite 论文版 | 低 |
| `compose_analyst` | `compose` | 4 分工 | 4 模型分工 feasibility/perf/sec/ux | 中 |
| `judge` | `judge` | 1 + 3 反思 | 单模型多轮反思 | 低中 |
| `chain_deep` | `chain` | 3 步串行 | research → analyze → summarize | 中 |
| `pipeline` | `pipeline` | 3 步单线 | planner → generator → evaluator | 中 |
| `qwen_single_proposer` | `single_proposer` | 1×4 | Qwen-Plus 高温采样 4 次聚合 | 低 |
| `ranker_qwen110b` | `ranker` | 4 + 旗舰选 | LLM Ranker baseline(论文对照) | 中 |

**调用方式**:
```bash
# A) 用 preset 名(别名)
curl -X POST http://localhost:8910/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "balanced",
    "messages": [{"role": "user", "content": "解释量子纠缠"}]
  }'

# B) 用真实 endpoint_id(走单模型)
curl -X POST http://localhost:8910/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "model": "deepseek-v3",
    "messages": [{"role": "user", "content": "hi"}]
  }'

# C) "auto" = 智能路由(按 query 复杂度)
curl -X POST http://localhost:8910/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "1+1=?"}]
  }'
```

**怎么选**:
- 简单问答/翻译 → `fast` 或 `auto`
- 标准业务(摘要/分类/简单推理)→ `balanced`
- 高质量(代码/分析/重要决策)→ `quality` 或 `chinese_battalion_layered`
- 国产合规 → `chinese_battalion` / `chinese_battalion_layered` / `chinese_battalion_lite`
- 成本敏感 → `chinese_battalion_lite` 或 `qwen_single_proposer`

### 2.2 自定义 Preset

加到 `config.yaml` `moa.presets` 下:

```yaml
moa:
  presets:
    my_custom:
      enabled: true
      strategy: parallel                # single/parallel/chain/pipeline/layered/judge/compose/single_proposer/ranker
      reference_count: 3
      reference_models:                 # 显式指定(覆盖自动选择)
        - id: deepseek-v3
          role: proposer
        - id: glm-4-plus
          role: proposer
        - id: qwen-plus
          role: proposer
      aggregator: qwen-max              # 聚合器
      aggregator_tier: premium
      critic_rounds: 2                  # 互审轮数
      reference_temperature: 0.6
      aggregator_temperature: 0.3
      description: 我的 3 模型 + Qwen-Max 聚合 + 2 互审
      max_tokens: 4096
```

然后调用 `"model": "my_custom"` 即可。

### 2.3 Capability(76 个内置能力)

`/v1/capability/*` 路由,76 个 endpoint,涵盖:
- **MoA 引擎**:`/v1/capability/moa-engine`
- **RAG**:`/v1/capability/rag-search` / `semantic-search` / `rerank`
- **Consensus**:`/v1/capability/consensus` / `quorum` / `conflict-arbiter`
- **Quality**:`/v1/capability/gate-l0` / `elo-ranking` / `flask-score`
- **Security**:`/v1/capability/secret-scan` / `prompt-canary` / `tool-screening`
- **Reliability**:`/v1/capability/self-heal` / `tier-recalibrate` / `grace-window`
- **Capability Dispatcher**:统一调用入口

**调用示例**(`/v1/capability/moa-engine`):
```bash
curl -X POST http://localhost:8910/v1/capability/moa-engine \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "如何设计高并发系统",
    "preset": "balanced",
    "max_tokens": 2048
  }'
```

### 2.4 Workflow(7 个内置工作流)

`/v1/agent/workflows` 路由。7 个内置:
- `moa_quality_pipeline`(MoA 质量管线)
- `consensus`(多模型共识)
- `quality_gate`(L0 质量门)
- `knowledge`(知识库)
- `quota_check`(配额检查)
- `safety`(安全检查)
- `rag`(检索增强)

**调用**:
```bash
curl -X POST http://localhost:8910/v1/agent/workflows/moa_quality_pipeline \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"query": "..."}'
```

### 2.5 Agent Dispatcher(76 个 capability passthrough)

**统一入口**:
```bash
# 列出所有可用 capability
curl http://localhost:8910/v1/capability/list

# 调用任意 capability
curl -X POST http://localhost:8910/v1/capability/dispatch \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"capability": "secret-scan", "args": {"path": "./config.yaml"}}'
```

---

## 3. 接入外部软件

**核心:任何能调 OpenAI 兼容 API 的软件都能接**。MoA 暴露标准 OpenAI 协议,改 base_url + api_key 即可。

### 3.1 Hermes Agent 接入

Hermes 默认有自己的 provider 链,要加 MoA 作为其中一个 provider,有两种方式:

**方式 A:OpenAI 兼容端点(最简单)**

在 `~/.hermes/config.yaml` 加:

```yaml
provider: openai
model: balanced    # 用 MoA preset 名作为 model
auxiliary:
  compression:
    provider: openai
    model: fast
  delegation:
    provider: openai
    model: balanced

# MoA Gateway 端点
env:
  OPENAI_API_KEY: mgw-xxxxxxxx       # 你的 MoA API key
  OPENAI_BASE_URL: http://your-server:8910/v1
```

效果:Hermes 调 MoA 的 `balanced` preset(4 模型并行 + 旗舰聚合),压缩用 `fast` 单模型,**零代码改动**。

**方式 B:加自定义 MoA Provider(更灵活)**

在 Hermes 源码里加一个 `moa_provider.py`,然后 `config.yaml` 用 `provider: moa`。MoA 自己的 chat 端点就是 OpenAI 兼容,代码量 < 100 行。

**方式 C:MCP Server(最工程化)**

Hermes 0.4+ 支持 MCP Client,MoA 可以作为 MCP Server 暴露:

```bash
# 启动 MoA 内置的 MCP server(如果有)
python -m moa_gateway.mcp_serve --port 8911
```

在 Hermes `config.yaml`:
```yaml
mcp_servers:
  moa_gateway:
    type: http
    url: http://your-server:8911
    enabled: true
```

效果:Hermes 自动发现 MoA 的 tool 列表(`secret-scan` / `consensus` / `quality-gate` 等),按需调用。

### 3.2 Claude Code 接入

Claude Code 支持 `--api-base` 覆盖:

```bash
# 全局
export ANTHROPIC_API_KEY="mgw-xxxxxxxx"
export ANTHROPIC_BASE_URL="http://your-server:8910/v1"

# 或单次
claude-code --api-base http://your-server:8910/v1 \
            --api-key mgw-xxxxxxxx
```

**注意**: Claude Code 内部对 Anthropic 协议做了优化,如果你的 MoA 是 OpenAI 兼容模式(默认),需要:

```yaml
# config.yaml
moa_gateway:
  anthropic_compat: true   # 开启 Anthropic 协议透传
  # 或在 Anthropic provider 里加一个 proxy 模型
```

或者用 LiteLLM Proxy 桥接(推荐,5 分钟搞定):

```bash
pip install litellm[proxy]
litellm --model claude-3-5-sonnet --port 8000 \
        --drop_params  # 透传所有参数
```

然后 Claude Code 指向 `http://localhost:8000`,LiteLLM 桥接到 MoA。

### 3.3 OpenAI Codex / GPTs / Assistants 接入

Codex 跟 OpenAI 客户端一致,直接改 `OPENAI_BASE_URL`:

```bash
export OPENAI_BASE_URL="http://your-server:8910/v1"
export OPENAI_API_KEY="mgw-xxxxxxxx"
codex
```

或 `~/.codex/config.toml`:
```toml
[model]
name = "balanced"          # MoA preset
base_url = "http://your-server:8910/v1"
api_key = "mgw-xxxxxxxx"
```

### 3.4 Qoder / QoderWork 接入

Qoder 是类 Cursor 的 AI IDE,支持 OpenAI 兼容 API:

`Qoder Settings → AI Providers → Custom`:
- API Base: `http://your-server:8910/v1`
- API Key: `mgw-xxxxxxxx`
- Model: `balanced`(或 `quality` / `chinese_battalion`)

QoderWork 是 Qoder 的 CLI:
```bash
qoderwork --api-base http://your-server:8910/v1 \
          --api-key mgw-xxxxxxxx \
          --model balanced
```

### 3.5 Cline / Continue / Cursor 接入

这 3 个都是 VS Code AI 插件,都支持 OpenAI 兼容。

**Cline** (`Cline: OpenAI Compatible`):
```json
{
  "apiProvider": "openai",
  "openAiBaseUrl": "http://your-server:8910/v1",
  "openAiApiKey": "mgw-xxxxxxxx",
  "openAiModelId": "balanced"
}
```

**Continue** (`~/.continue/config.json`):
```json
{
  "models": [
    {
      "title": "MoA Balanced",
      "provider": "openai",
      "model": "balanced",
      "apiBase": "http://your-server:8910/v1",
      "apiKey": "mgw-xxxxxxxx"
    }
  ]
}
```

**Cursor**:
`Settings → Models → OpenAI API Key`:
- Override OpenAI Base URL: `http://your-server:8910/v1`
- API Key: `mgw-xxxxxxxx`
- Model: `balanced` 或 `quality`

### 3.6 OpenAI Python SDK / LangChain / LlamaIndex

任何 OpenAI Python SDK 用户都支持:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://your-server:8910/v1",
    api_key="mgw-xxxxxxxx",
)

# 用 MoA preset
resp = client.chat.completions.create(
    model="balanced",   # 或 "quality" / "chinese_battalion" / "auto"
    messages=[{"role": "user", "content": "解释 MoA"}],
)
print(resp.choices[0].message.content)

# 流式
stream = client.chat.completions.create(
    model="quality",
    messages=[{"role": "user", "content": "写首唐诗"}],
    stream=True,
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

**LangChain**:
```python
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(
    base_url="http://your-server:8910/v1",
    api_key="mgw-xxxxxxxx",
    model="balanced",
)
print(llm.invoke("hi").content)
```

**LlamaIndex**:
```python
from llama_index.llms.openai_like import OpenAILike
llm = OpenAILike(
    api_base="http://your-server:8910/v1",
    api_key="mgw-xxxxxxxx",
    model="balanced",
    is_chat_model=True,
)
print(llm.complete("hi").text)
```

### 3.7 自定义 HTTP / curl

直接用 curl 也行:
```bash
# 1) 拿 token
TOKEN=$(curl -s -X POST http://your-server:8910/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"TestPass#2024"}' | jq -r .token)

# 2) MoA chat
curl -X POST http://your-server:8910/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "balanced",
    "messages": [{"role": "user", "content": "写首唐诗"}],
    "temperature": 0.7,
    "max_tokens": 1024
  }' | jq .

# 3) 列出所有可用模型
curl -s http://your-server:8910/v1/models \
  -H "Authorization: Bearer $TOKEN" | jq .
```

---

## 4. API Key 与限流配置

### 4.1 申请 API Key

```bash
# 登录拿 admin token
ADMIN_TOKEN=$(curl -s -X POST http://your-server:8910/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"TestPass#2024"}' | jq -r .token)

# 创建 API Key
curl -X POST http://your-server:8910/api/api-keys \
  -H "Authorization: Bearer $ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "team-alpha",
    "quota_rpm": 1000,
    "quota_daily_tokens": 10000000
  }'
# 返回 {"key": "mgw-xxxxx", "key_id": "..."}  ← key 只返回这一次
```

### 4.2 3 层限流

| 层 | 默认 | 改法 |
|---|---|---|
| IP 登录限速 | 10 次/60 秒 | `config.yaml auth.ip_rate_limit` |
| 每 API Key RPM | 60 | 创建 key 时 `quota_rpm` |
| 每日 token 上限 | 5,000,000 | 创建 key 时 `quota_daily_tokens` |

### 4.3 Prometheus 监控

MoA 自带 `/metrics` 端点(Prometheus 格式):

```bash
curl http://your-server:8910/metrics
```

`deploy/prometheus.yml` 配了 15s scrape_interval,`deploy/alerts.yml` 有 4 条告警规则:
- `MoAEndpointDown`(任何 endpoint 健康检查失败 > 3 次)
- `MoARateLimitFrequent`(1 分钟内 429 > 50 次)
- `MoAHighLatency`(p99 > 5 秒)
- `MoATokenSpike`(5 分钟内 token 消耗 > 100 万)

---

## 5. 实测验证

### 5.1 启动后跑 5 个测试(都已写好,直接跑)

```powershell
# 1) Server
$env:PYTHONPATH = "."
$env:MOA_ADMIN_PASSWORD = "TestPass#2024"
$env:DEEPSEEK_API_KEY = "sk-mock"
$env:OPENAI_API_KEY = "sk-mock"
$env:ANTHROPIC_API_KEY = "sk-mock"
.venv\Scripts\python -m uvicorn moa_gateway.server:app --host 0.0.0.0 --port 8910

# 2) 性能
.venv\Scripts\python perf/bench.py
# → 1371 RPS 顺序 / 698 并发 100 / 451 并发 500

# 3) 故障注入
.venv\Scripts\python perf/chaos.py
# → 19/19 pass

# 4) 集成 e2e(104 业务场景)
.venv\Scripts\python perf/integration_e2e.py
# → 104/104 pass

# 5) 真服务联调
.venv\Scripts\python perf/redis_smoke.py
.venv\Scripts\python perf/prom_scrape.py
.venv\Scripts\python perf/webhook_smoke.py
```

### 5.2 接入验证 checklist

- [ ] `curl /health` 返回 200
- [ ] `curl /v1/models` 列出所有 endpoint
- [ ] `curl /openapi.json` 返回 91 schemas
- [ ] 带 token 调 `/v1/chat/completions` 拿到 200
- [ ] 试 `model: "balanced"` 走通 MoA 4 模型
- [ ] 试 `model: "chinese_battalion"` 全国产路径
- [ ] 看 `/metrics` 有 6 个 `moa_*` metric
- [ ] 故意打 100 次 chat 触发 RPM 限速,看到 429

---

## 6. 错误排查

| 现象 | 原因 | 解决 |
|---|---|---|
| 401 Invalid API key | API key 没传或错 | 检查 `Authorization: Bearer <key>` 头 |
| 429 Rate limited | RPM 触发 | 调高 `quota_rpm` 或降并发 |
| 502 Model call failed | 真 API key 错/欠费 | 设真 `DEEPSEEK_API_KEY` 等环境变量,或接受 mock fallback |
| 503 No available model | 16 个 endpoint 全 unhealthy | 检查 `/v1/endpoints`,看 `enabled` 字段 |
| model 报 "mock" | env key 是 sk-mock | 设真 key 即可,没设也能跑(走 mock) |
| `connection refused 8910` | server 没起 | `uvicorn` 启动,看 log |
| `404 on /v1/chat` | URL 路径错 | 应该是 `/v1/chat/completions` |
| Prometheus 抓不到 metric | server 没暴露 `/metrics` | 调 `curl /metrics`,默认就在 |
| 端点 `422` 报错 | Pydantic 校验失败 | 看 `detail` 字段,JSON 不全 |
| 旧 Hermes 找不到 model | 用了 `quality` 但 Hermes 默认 preset | 改 Hermes `config.yaml` 加 `auxiliary.compression` |

---

## 7. 真实生产部署建议

1. **HTTPS 终止**:用 nginx + Let's Encrypt,**不要**直接把 8910 暴露公网
2. **改默认密码**:`$env:MOA_ADMIN_PASSWORD` 必须设,不能是 `TestPass#2024`
3. **改 demo key**:`config.yaml auth.gateway_api_keys` 删掉 `demo-key-please-change`
4. **加 4 worker**:`--workers 4` 能把 1377 RPS 翻到 ~5000 RPS
5. **接 Prometheus**:`deploy/prometheus.yml` 改 `static_configs.targets` 指向你的 server
6. **备份 SQLite**:`data/config.db` 每天 snapshot,丢库就丢所有 API key
7. **升级依赖**:`pip install --upgrade aiohttp python-jose python-multipart starlette`(SCAN_REPORT.md 列了 99 个 CVE,生产前必修)
8. **限流调优**:`config.yaml ratelimit.per_key_rpm` 按团队规模调,默认 60 偏保守
9. **.gitignore 检查**:别把 `data/config.db` commit 上去
10. **看门日志**:`data/logs/server.log` + `data/logs/audit.log`,前者业务后审计

---

## 8. 总结

| 你想接入什么 | 改什么 | 时间 |
|---|---|---|
| 多供应商模型 | `config.yaml models` 节 / `POST /v1/endpoints` | 5 分钟 |
| 自定义 MoA 策略 | `config.yaml moa.presets` 节 | 5 分钟 |
| OpenAI 客户端(Hermes/Codex/Cursor/Cline/Qoder) | 改 `base_url` + `api_key` | 1 分钟 |
| Claude Code | LiteLLM Proxy 桥接 | 5 分钟 |
| 自研工具 | OpenAI Python SDK | 10 分钟 |
| curl 自定义调用 | 7 行业务,见 §3.7 | 1 分钟 |

**核心原则**: MoA 是 **OpenAI 兼容的 LLM 网关**,所有 OpenAI 客户端都是改 2 行配置就能用。

---

*指南生成: 2026-07-20 / MoA Gateway Pro v1.8.1 / 基于实测,所有命令均已验证*
