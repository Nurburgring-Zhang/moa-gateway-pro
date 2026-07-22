"""moa_gateway.mcp_server — 零依赖 MCP server

让 MoA Gateway Pro 作为 MCP server 被 Hermes/Claude Code/Cursor 当工具源用。

协议: JSON-RPC 2.0 over stdio (跟官方 MCP 协议兼容)
      SSE transport 通过 FastAPI 的 /v1/mcp/sse 端点(在 server.py)

不依赖 mcp SDK — 全部 stdlib,避免破坏 fastapi ↔ starlette ↔ pydantic 兼容。

设计: 所有 tool 通过 HTTP 调 MoA 自己的端点(/v1/chat/completions,
      /v1/capability/* 等),不直接 import capability 模块,这样:
      1. capability 重构不影响 MCP server
      2. tool 行为跟用户实际用的 API 一致
      3. 0 个 import 冲突风险

用法:
  start.py mcp --transport stdio
  start.py mcp --transport sse --port 8911
  python -m moa_gateway.mcp_server --transport stdio
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("mcp_server")

# ========== 协议常量 ==========
PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "MoA Gateway Pro"
SERVER_VERSION = "1.8.1"

# ========== 配置 ==========
# MCP server 假设 MoA HTTP server 跑在 MOA_GATEWAY_URL(默认 8088)
# 通过 MOA_GATEWAY_URL 环境变量可改
GATEWAY_URL = os.environ.get("MOA_GATEWAY_URL", "http://127.0.0.1:8088")
GATEWAY_TOKEN = os.environ.get("MOA_GATEWAY_TOKEN", "")  # admin token,可选


# ========== HTTP 客户端(用 stdlib urllib,避免 aiohttp 依赖) ==========
async def _ensure_token() -> str:
    """启动时自动 login 拿 token(避免 401)"""
    global GATEWAY_TOKEN
    if GATEWAY_TOKEN:
        return GATEWAY_TOKEN
    pwd = os.environ.get("MOA_ADMIN_PASSWORD", "")
    if not pwd:
        return ""
    import urllib.error
    import urllib.request

    url = GATEWAY_URL.rstrip("/") + "/api/auth/login"
    req = urllib.request.Request(
        url,
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"username": "admin", "password": pwd}).encode("utf-8"),
    )
    try:
        loop = asyncio.get_event_loop()
        token = await loop.run_in_executor(None, lambda: _do_login(req))
        if token:
            GATEWAY_TOKEN = token
            print(
                f"[mcp-server] auto-login OK, token len={len(token)}", file=sys.stderr, flush=True
            )
        return token
    except Exception as e:
        print(f"[mcp-server] auto-login failed: {e}", file=sys.stderr, flush=True)
        return ""


def _do_login(req):
    import urllib.request

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("token", "")


async def _http_post(path: str, body: dict[str, Any], timeout: float = 60.0) -> dict[str, Any]:
    """调 MoA Gateway 自己的端点"""
    await _ensure_token()  # 第一次调用时自动 login
    import urllib.error
    import urllib.request

    url = GATEWAY_URL.rstrip("/") + path
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {GATEWAY_TOKEN}" if GATEWAY_TOKEN else "",
        },
    )
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _do_request(req, timeout))


async def _http_get(path: str, timeout: float = 30.0):
    """GET 请求(无 body)"""
    await _ensure_token()
    import urllib.error
    import urllib.request

    url = GATEWAY_URL.rstrip("/") + path
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {GATEWAY_TOKEN}" if GATEWAY_TOKEN else ""},
    )
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _do_request(req, timeout))


def _do_request(req, timeout):
    import urllib.error

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} {e.reason}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"gateway unreachable: {e.reason}")


# ========== 工具注册表 ==========
_TOOLS: dict[str, dict[str, Any]] = {}


def tool(name: str, description: str, input_schema: dict[str, Any]):
    def deco(fn):
        _TOOLS[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
            "handler": fn,
        }
        return fn

    return deco


# ========== 10 个核心 tool(全部通过 HTTP 调 MoA Gateway) ==========
@tool(
    name="chat",
    description="调用单个 LLM 模型(OpenAI 兼容)。返回模型输出文本。",
    input_schema={
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "模型 ID(端点 ID 或 preset 名)"},
            "messages": {
                "type": "array",
                "description": "OpenAI 风格 messages 数组",
                "items": {"type": "object"},
            },
            "temperature": {"type": "number", "description": "0-2"},
            "max_tokens": {"type": "integer"},
        },
        "required": ["model", "messages"],
    },
)
async def tool_chat(args: dict[str, Any]) -> dict[str, Any]:
    out = await _http_post(
        "/v1/chat/completions",
        {
            "model": args["model"],
            "messages": args["messages"],
            "temperature": args.get("temperature", 0.6),
            "max_tokens": args.get("max_tokens", 4096),
            "stream": False,
        },
    )
    return {
        "content": [
            {
                "type": "text",
                "text": out.get("choices", [{}])[0].get("message", {}).get("content", ""),
            }
        ],
        "model": args["model"],
        "usage": out.get("usage", {}),
    }


@tool(
    name="moa",
    description="MoA 多模型编排 — 走 OpenAI 兼容 /v1/chat/completions,model=preset 名(fast/balanced/quality/chinese_battalion 等)",
    input_schema={
        "type": "object",
        "properties": {
            "model": {
                "type": "string",
                "description": "MoA preset: fast/balanced/quality/chinese_battalion 等,默认 balanced",
            },
            "messages": {
                "type": "array",
                "description": "OpenAI 风格 messages 数组",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"type": "string"},
                        "content": {"type": "string"},
                    },
                },
            },
            "temperature": {"type": "number"},
            "max_tokens": {"type": "integer"},
        },
        "required": ["messages"],
    },
)
async def tool_moa(args: dict[str, Any]) -> dict[str, Any]:
    """实际走 /v1/chat/completions — server.py 的 chat_completions 接受 model=preset alias"""
    payload = {
        "model": args.get("model", "balanced"),
        "messages": args["messages"],
        "stream": False,
    }
    if "temperature" in args:
        payload["temperature"] = args["temperature"]
    if "max_tokens" in args:
        payload["max_tokens"] = args["max_tokens"]
    out = await _http_post("/v1/chat/completions", payload)
    text = out.get("choices", [{}])[0].get("message", {}).get("content", "")
    return {
        "content": [{"type": "text", "text": text}],
        "preset": args.get("model", "balanced"),
        "usage": out.get("usage", {}),
    }


@tool(
    name="endpoint_list",
    description="列出所有配置的 LLM 端点(模型)。",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def tool_endpoint_list(args: dict[str, Any]) -> dict[str, Any]:
    out = await _http_get("/api/endpoints")
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(out, ensure_ascii=False, indent=2, default=str)[:5000],
            }
        ]
    }


@tool(
    name="endpoint_upsert",
    description="添加/更新 LLM 端点(运行时,不需要重启)。",
    input_schema={
        "type": "object",
        "properties": {
            "endpoint_id": {"type": "string"},
            "provider": {"type": "string"},
            "model": {"type": "string"},
            "api_base": {"type": "string"},
            "api_key_env": {"type": "string"},
            "api_key_plain": {"type": "string"},
            "tier": {"type": "string", "enum": ["free", "lite", "standard", "premium", "flagship"]},
            "max_tokens": {"type": "integer"},
        },
        "required": ["endpoint_id", "provider", "model", "api_base"],
    },
)
async def tool_endpoint_upsert(args: dict[str, Any]) -> dict[str, Any]:
    out = await _http_post("/api/endpoints", args)
    return {
        "content": [{"type": "text", "text": f"endpoint {args['endpoint_id']} upserted"}],
        "endpoint_id": args["endpoint_id"],
    }


@tool(
    name="capability_list",
    description="列出所有 76 个 capability(可调用的功能)。",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
async def tool_capability_list(args: dict[str, Any]) -> dict[str, Any]:
    # 走 session-lock action=list_mcp 列出 MCP 工具
    out = await _http_post("/v1/capability/session-lock", {"action": "list_mcp"})
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, indent=2)}]}


@tool(
    name="capability_dispatch",
    description="调用任意 capability(76 个中的任意一个)。",
    input_schema={
        "type": "object",
        "properties": {
            "capability": {
                "type": "string",
                "description": "capability 名,如 secret-scan/moa-engine/rag-search",
            },
            "args": {"type": "object", "description": "capability 参数字典"},
        },
        "required": ["capability"],
    },
)
async def tool_capability_dispatch(args: dict[str, Any]) -> dict[str, Any]:
    cap = args["capability"]
    out = await _http_post(f"/v1/capability/{cap}", args.get("args", {}))
    return {
        "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}],
        "capability": cap,
    }


@tool(
    name="secret_scan",
    description="扫描代码里的硬编码密钥(9 类)。fail_on: low(1)/medium(2)/high(3)/critical(4)",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要扫描的文件或目录"},
            "fail_on": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        },
        "required": ["path"],
    },
)
async def tool_secret_scan(args: dict[str, Any]) -> dict[str, Any]:
    # server.py secret-scan 收 fail_on: int (1=low, 2=medium, 3=high, 4=critical)
    _FAIL_ON_MAP = {"low": 1, "medium": 2, "high": 3, "critical": 4}
    fail_on_str = args.get("fail_on", "high")
    fail_on_int = _FAIL_ON_MAP.get(fail_on_str.lower(), 3)
    out = await _http_post(
        "/v1/capability/secret-scan",
        {
            "path": args["path"],
            "fail_on": fail_on_int,
        },
    )
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}


@tool(
    name="consensus",
    description="多模型共识 — 同一 query 给多个模型跑,看一致性。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "model_count": {"type": "integer", "description": "用几个模型,默认 3"},
            "threshold": {"type": "number", "description": "一致度阈值 0-1,默认 0.7"},
        },
        "required": ["query"],
    },
)
async def tool_consensus(args: dict[str, Any]) -> dict[str, Any]:
    """内部用 N 个 chat-completions 跑同一 query,算 Jaccard 词集重叠度"""
    query = args["query"]
    model_count = args.get("model_count", 3)
    threshold = args.get("threshold", 0.7)
    members = ["deepseek-v3", "gpt-4o-mini", "claude-haiku", "qwen-plus", "glm-4-plus"][
        :model_count
    ]
    answers = []
    for m in members:
        try:
            out = await _http_post(
                "/v1/chat/completions",
                {
                    "model": m,
                    "messages": [{"role": "user", "content": query}],
                    "stream": False,
                },
            )
            text = out.get("choices", [{}])[0].get("message", {}).get("content", "")
            answers.append({"model": m, "text": text})
        except Exception as e:
            answers.append({"model": m, "error": str(e)[:200]})
    valid_texts = [a["text"] for a in answers if a.get("text")]
    word_sets = [set(t.split()) for t in valid_texts]
    if word_sets and len(word_sets) >= 2:
        inter = set.intersection(*word_sets)
        union = set.union(*word_sets)
        jaccard = len(inter) / max(len(union), 1)
    else:
        jaccard = 1.0
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "answers": answers,
                        "jaccard": round(jaccard, 4),
                        "above_threshold": jaccard >= threshold,
                        "threshold": threshold,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            }
        ],
    }


@tool(
    name="quality_gate",
    description="L0 质量门 — 检查 LLM 响应是否通过基础质量阈值(走 /v1/capability/gate-l0,query 字段)。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要门控的文本(LLM 输出)"},
            "threshold": {"type": "number", "description": "质量阈值 0-1,默认 0.6"},
        },
        "required": ["query"],
    },
)
async def tool_quality_gate(args: dict[str, Any]) -> dict[str, Any]:
    out = await _http_post(
        "/v1/capability/gate-l0",
        {
            "query": args["query"],
            "threshold": args.get("threshold", 0.6),
        },
    )
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}


@tool(
    name="rag_search",
    description="RAG 知识库搜索 — 从本地知识库检索相关内容。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "description": "返回条数,默认 5"},
        },
        "required": ["query"],
    },
)
async def tool_rag_search(args: dict[str, Any]) -> dict[str, Any]:
    out = await _http_post(
        "/v1/capability/rag-search",
        {
            "query": args["query"],
            "top_k": args.get("top_k", 5),
        },
    )
    return {"content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False, default=str)}]}


# ========== JSON-RPC 2.0 协议层 ==========
def make_response(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def make_error(req_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": req_id, "error": err}


async def handle_request(req: dict[str, Any]):
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {})
    try:
        if method == "initialize":
            return make_response(
                req_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "capabilities": {"tools": {}},
                },
            )
        elif method == "notifications/initialized":
            return None
        elif method == "tools/list":
            tools = [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
                for t in _TOOLS.values()
            ]
            return make_response(req_id, {"tools": tools})
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            if tool_name not in _TOOLS:
                return make_error(req_id, -32602, f"unknown tool: {tool_name}")
            result = await _TOOLS[tool_name]["handler"](tool_args)
            return make_response(req_id, result)
        elif method == "ping":
            return make_response(req_id, {})
        else:
            return make_error(req_id, -32601, f"method not found: {method}")
    except Exception as e:
        logger.exception("handle_request error")
        return make_error(req_id, -32603, f"internal error: {e}")


# ========== stdio transport ==========
async def run_stdio() -> None:
    print(
        f"[mcp-server] {SERVER_NAME} v{SERVER_VERSION} (stdio, gateway={GATEWAY_URL})",
        file=sys.stderr,
        flush=True,
    )
    # Windows 上 asyncio.StreamReader 跟 stdin pipe 有兼容问题,
    # 用线程 + 同步 readline 更稳
    import queue as q
    import threading

    line_queue: q.Queue[str] = q.Queue()
    SENTINEL = object()

    def stdin_reader():
        try:
            for line in sys.stdin:
                line_queue.put(line)
        except Exception as e:
            print(f"[mcp-server] stdin read error: {e}", file=sys.stderr, flush=True)
        finally:
            line_queue.put(SENTINEL)  # type: ignore

    t = threading.Thread(target=stdin_reader, daemon=True)
    t.start()

    while True:
        line = await asyncio.get_event_loop().run_in_executor(None, line_queue.get)
        if line is SENTINEL:
            break
        if not line:
            continue
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            err = make_error(None, -32700, f"parse error: {e}")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp = await handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


# ========== SSE transport(通过 FastAPI /v1/mcp/sse) ==========
async def process_sse_request(req: dict[str, Any]) -> dict[str, Any]:
    """被 server.py 的 /v1/mcp/sse 端点调用"""
    return await handle_request(req)


# ========== 入口 ==========
def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8911) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    if transport == "stdio":
        try:
            asyncio.run(run_stdio())
        except KeyboardInterrupt:
            print("\n[mcp-server] stopped", file=sys.stderr, flush=True)
    elif transport == "sse":
        print(
            f"[mcp-server] SSE 端点跟 FastAPI server 一起暴露: http://{host}:{port}/v1/mcp/sse",
            file=sys.stderr,
            flush=True,
        )
        print(
            f"[mcp-server] 启动 server:  uvicorn moa_gateway.server:app --port {port}",
            file=sys.stderr,
            flush=True,
        )
        print("[mcp-server] 然后 SSE 端点自动生效,无需额外启动", file=sys.stderr, flush=True)
    else:
        print(f"unknown transport: {transport}", file=sys.stderr)
        sys.exit(1)


def main() -> int:
    p = argparse.ArgumentParser(description="MoA Gateway Pro MCP server")
    p.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8911)
    args = p.parse_args()
    run_server(args.transport, args.host, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
