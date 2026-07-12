# 05 · 多 Agent 接入指南

MoA Gateway Pro 暴露的是 **OpenAI 兼容 API**(`/v1/chat/completions`),
所以理论上**任何支持自定义 OpenAI endpoint 的 Agent / IDE / SDK 都能用**。

本文逐个介绍常见 Agent 的接入方法。**最快的方式是打开 WebUI「接入 Agent」页面,
直接复制对应配置**。

## 5.1 通用 OpenAI SDK

任何语言的 OpenAI SDK 都行,只需改 `base_url` 和 `api_key`:

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8910/v1",
    api_key="mgw-你生成的key"
)

r = client.chat.completions.create(
    model="balanced",
    messages=[{"role": "user", "content": "你好"}]
)
print(r.choices[0].message.content)
```

**Node.js**:
```js
import OpenAI from "openai";
const client = new OpenAI({
  baseURL: "http://localhost:8910/v1",
  apiKey: "mgw-你生成的key"
});
const r = await client.chat.completions.create({
  model: "balanced",
  messages: [{ role: "user", content: "你好" }]
});
console.log(r.choices[0].message.content);
```

**Go**:
```go
import (
    openai "github.com/sashabaranov/go-openai"
)
config := openai.DefaultConfig("mgw-你生成的key", "http://localhost:8910/v1")
client := openai.NewClientWithConfig(config)
resp, _ := client.CreateChatCompletion(ctx, openai.ChatCompletionRequest{
    Model: "balanced",
    Messages: []openai.ChatCompletionMessage{{Role: "user", Content: "你好"}},
})
```

**curl**:
```bash
curl http://localhost:8910/v1/chat/completions \
  -H "Authorization: Bearer mgw-你生成的key" \
  -H "Content-Type: application/json" \
  -d '{"model":"balanced","messages":[{"role":"user","content":"你好"}]}'
```

## 5.2 Hermes Agent v0.18+

Hermes 自带 MoA 机制,但你也可以让它走我们的网关(享受更多 preset + 互审)。

**配置方式 1:命令行**
```bash
hermes config set model.provider custom
hermes config set model.custom.base_url http://localhost:8910/v1
hermes config set model.custom.api_key "mgw-你生成的key"
hermes config set moa.enabled true
hermes config set moa.default_preset balanced
```

**配置方式 2:编辑 `~/.hermes/config.yaml`**
```yaml
model:
  provider: custom
  model: balanced
  custom:
    base_url: "http://localhost:8910/v1"
    api_key: "mgw-你生成的key"

moa:
  enabled: true
  default_preset: balanced
  presets:
    balanced:
      reference_models:
        - { provider: moa-gateway, model: balanced }
        - { provider: moa-gateway, model: quality }
      aggregator:
        provider: moa-gateway
        model: quality
```

**验证**:
```bash
hermes mcp test moa-gateway
hermes moa run "你好,测一下 MoA 协作"
```

## 5.3 OpenClaw

**环境变量方式**(最简单):
```bash
export OPENAI_BASE_URL=http://localhost:8910/v1
export OPENAI_API_KEY="mgw-你生成的key"
openclaw onboard
```

**配置文件方式**:编辑 `~/.openclaw/openclaw.json`:
```json
{
  "models": {
    "mode": "merge",
    "providers": {
      "moa-gateway": {
        "type": "openai",
        "base_url": "http://localhost:8910/v1",
        "api_key": "mgw-你生成的key",
        "timeout": 120
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "moa-gateway/balanced",
        "fallbacks": ["moa-gateway/fast", "moa-gateway/auto"]
      }
    }
  }
}
```

**验证**:
```bash
openclaw doctor
```

## 5.4 QoderWork

1. 打开 Qoder 设置
2. 左侧选「**模型**」
3. 点「**添加自定义模型**」
4. 填写:
   - 名称:`MoA Gateway`
   - 提供商:`Custom`
   - API Base URL:`http://localhost:8910/v1`
   - API Key:`mgw-你生成的key`
   - 模型名:`balanced`
5. 点「**验证连接**」
6. 在对话窗口选「MoA Gateway」即可

## 5.5 Cursor / Cline / Continue.dev / Roo Code

**Cursor** — 编辑 `~/.cursor/config.json`:
```json
{
  "openai": {
    "apiBase": "http://localhost:8910/v1",
    "apiKey": "mgw-你生成的key"
  }
}
```

或环境变量:
```bash
export OPENAI_API_BASE=http://localhost:8910/v1
export OPENAI_API_KEY=mgw-你生成的key
```

**Cline** (VS Code 扩展) — 设置 → API Provider:
- API Provider:`OpenAI Compatible`
- Base URL:`http://localhost:8910/v1`
- API Key:`mgw-你生成的key`
- Model ID:`balanced`

**Continue.dev** (`~/.continue/config.json`):
```json
{
  "models": [{
    "title": "MoA Gateway",
    "provider": "openai",
    "apiBase": "http://localhost:8910/v1",
    "apiKey": "mgw-你生成的key",
    "model": "balanced"
  }]
}
```

**Roo Code / Cline 同理** — 都是 OpenAI 兼容。

## 5.6 Python — 自研 Agent 集成

```python
from openai import OpenAI

class MoAAgent:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8910/v1"):
        self.client = OpenAI(base_url=base_url, api_key=api_key)
    
    def ask(self, prompt: str, preset: str = "auto") -> str:
        r = self.client.chat.completions.create(
            model=preset,
            messages=[{"role": "user", "content": prompt}],
            extra_body={"preset": preset}   # 兼容非标字段
        )
        return r.choices[0].message.content
    
    def ask_with_moa(self, prompt: str) -> str:
        """强制走 MoA 协作"""
        return self.ask(prompt, preset="balanced")
    
    def stream(self, prompt: str, preset: str = "auto"):
        r = self.client.chat.completions.create(
            model=preset,
            messages=[{"role": "user", "content": prompt}],
            stream=True
        )
        for chunk in r:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


# 使用
agent = MoAAgent(api_key="mgw-你生成的key")
print(agent.ask("你好"))
print(agent.ask_with_moa("分析 CAP 定理"))
for piece in agent.stream("写一首诗"):
    print(piece, end="", flush=True)
```

## 5.7 跨网络部署

如果 Agent 和网关不在同一台机器:

1. **修改配置**:编辑 `config.yaml`:
   ```yaml
   server:
     host: "0.0.0.0"  # 监听所有网卡
     port: 8910
     cors_origins: ["*"]
   ```

2. **防火墙开放 8910 端口**

3. **推荐前置反向代理**(Nginx):
   ```nginx
   server {
     listen 443 ssl;
     server_name ai.your-domain.com;
     
     ssl_certificate ...;
     ssl_certificate_key ...;
     
     location / {
       proxy_pass http://127.0.0.1:8910;
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_buffering off;   # 支持 SSE 流式
     }
   }
   ```

4. Agent 端用 `https://ai.your-domain.com/v1` 即可

## 5.8 多 Agent 并行

如果你的应用需要**同时**让多个 Agent 用不同的 Key 调:

```python
import asyncio
from openai import AsyncOpenAI

async def agent_task(name: str, api_key: str, prompt: str):
    client = AsyncOpenAI(
        base_url="http://localhost:8910/v1",
        api_key=api_key
    )
    r = await client.chat.completions.create(
        model="balanced",
        messages=[{"role": "user", "content": prompt}]
    )
    return name, r.choices[0].message.content

async def main():
    results = await asyncio.gather(
        agent_task("researcher", "key1", "..."),
        agent_task("coder", "key2", "..."),
        agent_task("reviewer", "key3", "..."),
    )
    for name, out in results:
        print(f"== {name} ==")
        print(out)

asyncio.run(main())
```

每个 Key 独立 RPM/每日 token 限额,互不影响。
