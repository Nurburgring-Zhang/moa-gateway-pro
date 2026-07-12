# 04 · API 参考

## 4.1 概述

MoA Gateway Pro 提供三类 API:

1. **OpenAI 兼容 API** (`/v1/...`) — 任何 OpenAI 客户端可直接调用
2. **原生 MoA API** (`/v1/moa/...`) — 返回完整 MoA 编排结果
3. **管理 API** (`/api/...`) — WebUI 后端,需 JWT 鉴权

**Base URL**: `http://your-host:8910`

---

## 4.2 OpenAI 兼容 API

### 4.2.1 `GET /v1/models`

列出可用模型。

**Headers**:
- `Authorization: Bearer <key>` (可选,不传时只列预设别名)

**响应**:
```json
{
  "object": "list",
  "data": [
    {
      "id": "auto",
      "object": "model",
      "owned_by": "moa-gateway",
      "description": "智能路由(按复杂度自动分配)"
    },
    {
      "id": "balanced",
      "object": "model",
      "owned_by": "moa-gateway",
      "description": "平衡模式:4 模型并行+旗舰聚合"
    },
    ...
  ]
}
```

**预设别名**:
| ID | 含义 |
|---|---|
| `auto` | 自动路由(按复杂度选模型) |
| `fast` | 单 lite 模型,最便宜 |
| `balanced` | 4 模型并行 + 旗舰聚合 + 1 互审 |
| `quality` | 5 模型并行 + 旗舰聚合 + 2 互审 |
| `moa-balanced` | 同 `balanced` |
| `moa-quality` | 同 `quality` |
| `pipeline` | planner→generator→evaluator |

如果鉴权通过,会额外列出所有已启用 + 已配 Key 的具体模型端点 ID。

### 4.2.2 `POST /v1/chat/completions`

OpenAI 兼容的 chat completions。

**请求**:
```json
{
  "model": "balanced",
  "messages": [
    {"role": "system", "content": "你是 helpful 助手"},
    {"role": "user", "content": "你好"}
  ],
  "temperature": 0.6,
  "max_tokens": 4096,
  "stream": false,
  "tools": null,
  "preset": "balanced",
  "strategy": "parallel",
  "reference_count": 4,
  "critic_rounds": 1
}
```

**扩展字段**(非 OpenAI 标准,但兼容处理):
- `preset`:fast / balanced / quality / pipeline
- `strategy`:single / parallel / pipeline
- `reference_count`:参考模型数(1-8)
- `critic_rounds`:互审轮数(0-3)

**响应**(非流式):
```json
{
  "id": "chatcmpl-abc123",
  "object": "chat.completion",
  "created": 1717800000,
  "model": "gpt-4o",
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "..."},
    "finish_reason": "stop"
  }],
  "usage": {
    "prompt_tokens": 1234,
    "completion_tokens": 567,
    "total_tokens": 1801
  },
  "moa_meta": {
    "moa_preset": "balanced",
    "moa_strategy": "parallel",
    "moa_references": ["deepseek-v3", "qwen-plus", "glm-4-plus", "kimi-k2.0"],
    "moa_consensus": 0.42,
    "moa_iterations": 2,
    "moa_cost": 0.0123
  }
}
```

**错误码**:
- `401 Unauthorized`:API Key 无效
- `429 Too Many Requests`:触发限流(RPM 或每日 token 超限)
- `502 Bad Gateway`:所有模型都调用失败
- `503 Service Unavailable`:没有任何可用模型

### 4.2.3 流式输出

设置 `"stream": true`,服务器按 SSE 协议返回 `data: {...}` 块,与 OpenAI 一致。

---

## 4.3 原生 MoA API

### 4.3.1 `POST /v1/moa/execute`

返回完整的 MoA 编排过程。

**请求**:
```json
{
  "model": "balanced",
  "messages": [{"role": "user", "content": "..."}],
  "preset": "balanced",
  "reference_count": 4,
  "critic_rounds": 1
}
```

**响应**:
```json
{
  "request_id": "moa_abc123",
  "query": "...",
  "preset": "balanced",
  "strategy": "parallel",
  "references": [
    {
      "model_id": "deepseek-v3",
      "success": true,
      "latency_ms": 1234.5,
      "cost": 0.002,
      "tokens": 567,
      "preview": "..."
    },
    ...
  ],
  "critics": [
    {
      "model_id": "claude-haiku",
      "success": true,
      "issues_count": 2,
      "suggestions_count": 3,
      "latency_ms": 890.1,
      "cost": 0.001
    }
  ],
  "aggregator_model": "claude-sonnet",
  "consensus_score": 0.42,
  "iterations": 2,
  "total_latency_ms": 4567.8,
  "total_cost": 0.015,
  "fallback_used": false,
  "final_content": "..."
}
```

### 4.3.2 `GET /v1/route/preview?query=...`

预览路由决策(调试用)。

**响应**:
```json
{
  "complexity": "complex",
  "tier": "premium",
  "primary": "gpt-4o",
  "fallback_chain": ["claude-sonnet", "qwen-max"],
  "estimated_cost": 0.0123,
  "reason": "complexity=complex, tier=premium, model=gpt-4o"
}
```

### 4.3.3 `GET /v1/quota`

查询当前 API Key 的配额使用情况。

**响应**:
```json
{
  "daily_tokens_used": 12345,
  "daily_tokens_limit": 5000000,
  "rpm_limit": 60
}
```

---

## 4.4 管理 API (WebUI)

> 所有 `/api/...` 端点需要 JWT(从 `/api/auth/login` 获取)。
> 浏览器中由 WebUI 自动处理。

### 4.4.1 鉴权

**登录**:`POST /api/auth/login`
```json
{ "username": "admin", "password": "admin" }
```
返回:`{ "token": "eyJhbGciOi...", "user": {...} }`

**改密码**:`POST /api/auth/change-password`
```json
{ "old_password": "admin", "new_password": "newone" }
```

**当前用户**:`GET /api/auth/me`

### 4.4.2 模型端点

- `GET /api/endpoints` — 列表
- `POST /api/endpoints` — 新增/更新
- `DELETE /api/endpoints/{id}` — 删除
- `POST /api/endpoints/{id}/toggle` — 启用/停用切换
- `POST /api/endpoints/{id}/reset-breaker` — 手动复位熔断

### 4.4.3 API Keys

- `GET /api/api-keys` — 列表
- `POST /api/api-keys` — 创建
- `DELETE /api/api-keys/{key_id}` — 删除

### 4.4.4 日志与统计

- `GET /api/logs?limit=200` — 最近调用日志
- `GET /api/stats?days=7` — 聚合统计
- `GET /api/metrics` — 进程内指标
- `GET /api/health/detailed` — 详细健康状态

### 4.4.5 适配器配置

- `GET /api/adapters` — 全量适配器配置
- `GET /api/adapters/curl` — cURL + Python 示例

---

## 4.5 健康检查

`GET /health` — 简单健康检查(无鉴权)
```json
{
  "status": "ok",
  "version": "1.0.0",
  "endpoints_total": 20,
  "endpoints_enabled": 15,
  "endpoints_healthy": 14
}
```

---

## 4.6 限流

每个 API Key 都带:
- **RPM**(每分钟请求数)
- **每日 token 数**

触发限流时:
- HTTP 429
- 响应头 `Retry-After: 60`(RPM 时)
- Body 包含具体超限信息

---

## 4.7 错误处理

所有错误响应格式:
```json
{ "detail": "错误描述" }
```

常见错误:
| 状态码 | 含义 |
|---|---|
| 400 | 请求格式错误 |
| 401 | 未鉴权 / API Key 无效 |
| 403 | 权限不足 |
| 404 | 资源不存在 |
| 422 | 请求参数校验失败 |
| 429 | 触发限流 |
| 500 | 内部错误 |
| 502 | 所有上游模型都失败 |
| 503 | 没有任何可用模型 |
