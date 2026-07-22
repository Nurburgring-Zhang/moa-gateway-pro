# MoA Gateway Pro v1.8.1 — Code Review Report

**审查人**: verifier
**日期**: 2026-07-20
**审查范围**: 6 个文件 (`start.py`, `moa_gateway/__init__.py`, `moa_gateway/__main__.py`, `moa_gateway/mcp_server.py`, `moa_gateway/server.py` line 376-414, `pyproject.toml`)
**方法**: L1 语法 / L2 规范 / L3 业务契约 / L4 架构安全 / L5 业务命名 + code-harness-soul + security-owasp

---

## ⚠️ 摘要 — 5 个 P0,不要发布 v1.8.1

| # | 文件 | 级别 | 问题 | 影响 |
|---|------|------|------|------|
| P0-1 | `server.py:377` `/v1/mcp` | **安全 L4** | 完全无鉴权,任何人都能调 admin 级 tool | 远程未授权管理 + 模型费用盗刷 |
| P0-2 | `__main__.py` | **L1/L2** | 没有 `def main()`,pyproject 的 `moa-gateway` console script 会 AttributeError | `pip install -e . && moa-gateway` 装包即崩 |
| P0-3 | `mcp_server.py:226` `tool_quality_gate` | **L3 契约** | 发 `text`,服务端读 `query` | 静默返回对空字符串的门控结果 |
| P0-4 | `mcp_server.py:241` `tool_consensus` | **L3 契约** | 调 `/v1/capability/consensus`,该端点**不存在** | 100% 404 |
| P0-5 | `mcp_server.py:158` `tool_moa` | **L3 契约** | 发 `{query, preset, max_tokens}`,服务端是 `ChatCompletionRequest` 必填 `messages` | Pydantic 422 |

**3 个 P1 + 7 个 P2** 见下文。

---

## 文件 1: `start.py`

### P2-1.1 文档说 7 个子命令,实际 9 个

**位置**: start.py:1-19
**问题**: docstring 写 "7 个子命令",实际是 9 个 (serve/direct/mcp/init-data/venv/install/test/version/check)。
**修复**:
```python
"""MoA Gateway Pro — 启动入口

9 个子命令:
  serve     默认: venv + 装依赖 + watchdog 父子进程 + 故障自动重启
  direct    直接启动(不创建 venv)— 开发用
  mcp       启动独立 MCP server (stdio / SSE 提示)
  init-data 初始化数据目录(SQLite + admin 用户)
  venv      仅创建/复用 venv
  install   仅安装依赖
  test      跑 smoke test (perf/chaos.py)
  version   版本
  check     自检(环境 / 端口 / 端点 / 鉴权)
"""
```

### P2-1.2 `cmd_init_data` 是个假动作

**位置**: start.py:35-46
**问题**: 函数名是 init_data,实际**没初始化任何东西**。只 mkdir + 读 settings + 打印信息,没调 `storage.init_db()` / `create_admin()`。用户跑 `start.py init-data` 后 SQLite 表可能不存在,admin 密码也没写入。
**修复**:
```python
def cmd_init_data(args):
    """初始化数据目录 + 创建 admin + 建表"""
    import os
    from moa_gateway.config import DATA_DIR
    from moa_gateway.storage import get_storage, init_storage_schema, ensure_admin_user

    pwd = os.environ.get("MOA_ADMIN_PASSWORD", "")
    if not pwd:
        print("[ERROR] 必须设 MOA_ADMIN_PASSWORD 环境变量才能 init-data")
        print("        export MOA_ADMIN_PASSWORD='YourStrong#Pass1'")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    init_storage_schema(storage)  # 实际存在的初始化函数,先确认签名
    ensure_admin_user(storage, username="admin", password=pwd)
    print(f"OK  data dir: {DATA_DIR}")
    print(f"OK  db path:  {storage.db_path}")
    print(f"OK  admin password: {'*' * len(pwd)} (len={len(pwd)})")
```

> 备注: `init_storage_schema` / `ensure_admin_user` 名字要按 storage.py 实际存在的改。先 `Select-String -Path "D:\MoA Gateway Pro\moa_gateway\storage.py" -Pattern "^def \w+"` 确认实际函数名。

### P2-1.3 `cmd_check` missing 状态既打 !! 又算 FAIL

**位置**: start.py:200-216
**问题**: 视觉上 `missing` 状态用 `!!`(警告),但 `if status not in ("ok", "recoverable")` 又把它当失败,自相矛盾。`diagnose_venv` 可能返回 `missing`(没有 venv),`diagnose_data` 可能返回 `missing`(没有 data 目录),但用户看输出只看到 !! 以为没事。
**修复**:
```python
icon = "OK" if status == "ok" else (
    "WARN" if status in ("recoverable", "missing") else "FAIL"
)
print(f"  [{icon:4}] {name:10} {status:12} {detail}")
if status not in ("ok", "recoverable", "missing"):
    all_ok = False
print("  RESULT (recoverable items marked WARN, manual fix may be needed):",
      "PASS" if all_ok else "FAIL")
```

### P2-1.4 `init-data` 子命令名带连字符,其他不带

**位置**: start.py:174
**问题**: 命名风格不一致。建议统一为 `init_data`,或者用 `initdata`。
**修复**: 把 `sub.add_parser("init-data")` 改成 `sub.add_parser("init_data", help="...")`,help 文本里说明别名兼容(`-`/`_` 等价)。

### L1-1.5 type hint 缺失

**位置**: 多个 `cmd_*` 函数缺 `-> int` 返回注解,`_version() -> str` 已 OK。
**修复**: 给每个 `cmd_*` 加 `-> int`,main() 已有 → None 可接受。

### P2-1.6 `cmd_mcp --transport sse` 是空操作

**位置**: start.py:90-92 + mcp_server.py:325-333
**问题**: 见 mcp_server 的 P1-4.5 — start.py 把 sse 参数原样转发,但 mcp_server 收到 sse 只 print 提示就返回。

---

## 文件 2: `moa_gateway/__init__.py` — **OK**

只加了 `__version__ = "1.8.1"`,符合规范。无需修改。

---

## 文件 3: `moa_gateway/__main__.py`

### 🔴 P0-3.1 没有 `def main()`,console script 装包即崩

**位置**: __main__.py:1-19 (整个文件)
**问题**: `pyproject.toml` 第 90 行写 `moa-gateway = "moa_gateway.__main__:main"`,但本文件**没有 `def main()` 函数**。一旦 `pip install -e .` 后跑 `moa-gateway --help`,setuptools 会报 `AttributeError: module 'moa_gateway.__main__' has no attribute 'main'`。
**修复** (重写整个文件):
```python
"""moa_gateway.__main__ — 入口,让 `python -m moa_gateway` / `moa-gateway` 都等价于 start.py。

委托给 start.py main(),避免逻辑重复。
"""
from __future__ import annotations
import sys
from pathlib import Path

# 让 start.py 能 `import moa_gateway.bootstrap` 等正常解析
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# import 而不是 exec — 保留 traceback / type hints / 静态分析能力
from start import main as _start_main


def main() -> int:
    """console script 入口 — `moa-gateway` 命令"""
    return _start_main()


if __name__ == "__main__":
    sys.exit(main())
```

> `start.py` 必须放在 `sys.path` 里 (上面已经 `sys.path.insert`)。`start` 这个名字当 module 引入会触发 `start.py` 顶层的 `import` 语句,确认无副作用(没有顶级副作用)后再用。

### P1-3.2 用 `exec(compile(...))` 把 start.py 当字符串跑 (原版)

**位置**: 原 __main__.py:11-18
**问题**: 即使是 `python -m moa_gateway` 也会走这个分支。`exec` 会:
- 丢失 traceback 上下文
- 让 ruff/mypy 看不到 start.py 的类型
- 任何 `start.py` 的导入会重复执行
**修复**: 见 P0-3.1,改用 `from start import main`。

### P1-3.3 `sys.path.insert` 不带 `if guard`

**位置**: 原 __main__.py:6
**问题**: 每次 import 都改一次 sys.path(虽然 insert 到同位置幂等,但污染了其他 module 的查找路径)。
**修复**: 加上 `if str(_ROOT) not in sys.path` 守卫,见 P0-3.1。

---

## 文件 4: `moa_gateway/mcp_server.py`

### 🔴 P0-4.1 `tool_quality_gate` 字段名错 — 静默失败

**位置**: mcp_server.py:226-237
**问题**: MCP tool 接收 `text`,但 server 端 `CreateGateL0Request` 只有 `query` 字段,handler 写 `body.get("query", "")`。所以 `text="xxx"` 被丢弃,服务端对**空字符串**跑门控。
**修复**:
```python
@tool(
    name="quality_gate",
    description="L0 质量门 — 检查 LLM 响应是否通过基础质量阈值。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "要门控的文本(LLM 输出)"},
            "threshold": {"type": "number", "description": "质量阈值 0-1,默认 0.6"},
        },
        "required": ["query"],
    },
)
async def tool_quality_gate(args):
    out = await _http_post("/v1/capability/gate-l0", {
        "query": args["query"],
        "threshold": args.get("threshold", 0.6),
    })
    return {"content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}
```
> 同步把 input_schema 的 `text` 改成 `query` — MCP 客户端会跟着改。如果要兼容老调用,可以在 handler 里做 `args["query"] = args.get("query") or args.get("text", "")`。

### 🔴 P0-4.2 `tool_moa` 用了不存在的请求体形状

**位置**: mcp_server.py:158-167
**问题**: `moa_execute(req: ChatCompletionRequest, ...)` 要求 `messages: List[ChatMessage]`(必填,无 default)。但 mcp_server 发的是 `{query, preset, max_tokens}`。Pydantic 422。
**修复方案 A (推荐,符合 /v1/moa/execute 真实签名)**:
```python
@tool(
    name="moa",
    description="MoA 多模型编排(OpenAI 兼容 /v1/moa/execute)。用 messages 数组传 query。",
    input_schema={
        "type": "object",
        "properties": {
            "model": {"type": "string", "description": "preset 名: fast/balanced/quality/..."},
            "messages": {"type": "array",
                         "items": {"type": "object",
                                   "properties": {
                                       "role": {"type": "string"},
                                       "content": {"type": "string"},
                                   }}},
            "temperature": {"type": "number"},
            "max_tokens": {"type": "integer"},
        },
        "required": ["messages"],
    },
)
async def tool_moa(args):
    payload = {
        "model": args.get("model", "balanced"),
        "messages": args["messages"],
        "stream": False,
    }
    if "temperature" in args:
        payload["temperature"] = args["temperature"]
    if "max_tokens" in args:
        payload["max_tokens"] = args["max_tokens"]
    out = await _http_post("/v1/moa/execute", payload)
    return {
        "content": [{"type": "text",
                     "text": out.get("final", out.get("result", str(out)))}],
        "preset": args.get("model", "balanced"),
        "raw": out,
    }
```

**修复方案 B (更简单,但只适合 query 字符串)**: 调 `/v1/chat/completions` 而非 `/v1/moa/execute`:
```python
async def tool_moa(args):
    query = args["query"]
    out = await _http_post("/v1/chat/completions", {
        "model": args.get("preset", "balanced"),  # chat 端点用 model=preset
        "messages": [{"role": "user", "content": query}],
        "max_tokens": args.get("max_tokens", 4096),
        "stream": False,
    })
    return {"content": [{"type": "text",
                         "text": out.get("choices", [{}])[0]
                                .get("message", {}).get("content", "")}]}
```

### 🔴 P0-4.3 `tool_consensus` 调不存在的端点 — 100% 404

**位置**: mcp_server.py:241-258
**问题**: 调 `/v1/capability/consensus`,但 `grep "@app\\.post\\(\"/v1/capability/consensus\"" server.py` 0 命中。`capability/consensus.py` 是**纯库** (`ensemble_vote`/`_vote_majority` 等函数),没人注册为 FastAPI 路由。
**修复方案 A (推荐,直连 capability 库)**: 在 server.py 加一个真端点(在 consensus.py import 之后),然后 mcp_server 调它:
```python
# server.py — 加在 /v1/capability/ensemble-vote 附近
@app.post("/v1/capability/consensus")
async def capability_consensus(
    body: CreateConsensusRequest,
    key_info: Dict[str, Any] = Depends(require_api_key),
):
    """Multi-model consensus — N models run on same query, compute agreement."""
    from .capability.consensus import ensemble_vote, Vote
    votes = [Vote(**v) for v in body.get("votes", [])]
    method = body.get("method", "weighted")
    result = ensemble_vote(votes, method=method)
    return result.to_dict() if hasattr(result, "to_dict") else {"consensus": result.consensus_score, "winner": result.winner}
```
然后 req_models.py 加 `CreateConsensusRequest`:
```python
class CreateConsensusRequest(_ModelBase):
    """Request body for POST /v1/capability/consensus."""
    votes: Optional[Any] = Field(None, description="投票列表 [{member_id, content, score}]")
    method: Optional[Any] = Field(None, description="算法: majority/weighted/borda/approval")
```

**修复方案 B (快速临时,直接走 /v1/chat/completions N 次)**: 不用新端点,client 自己做 consensus:
```python
async def tool_consensus(args):
    """调 N 个模型各跑一次,计算 token 重叠度(简化版)"""
    query = args["query"]
    model_count = args.get("model_count", 3)
    members = ["deepseek-v3", "gpt-4o-mini", "claude-haiku"][:model_count]
    answers = []
    for m in members:
        out = await _http_post("/v1/chat/completions", {
            "model": m, "messages": [{"role": "user", "content": query}],
            "stream": False,
        })
        text = out.get("choices", [{}])[0].get("message", {}).get("content", "")
        answers.append({"model": m, "text": text})
    # 简化一致度:Jaccard 词集重叠
    from collections import Counter
    word_sets = [set(a["text"].split()) for a in answers]
    inter = set.intersection(*word_sets) if word_sets else set()
    union = set.union(*word_sets) if word_sets else set()
    jaccard = len(inter) / max(len(union), 1)
    return {"content": [{"type": "text",
                         "text": json.dumps({"answers": answers, "jaccard": jaccard,
                                             "above_threshold": jaccard >= args.get("threshold", 0.7)},
                                            ensure_ascii=False)}]}
```

### P1-4.4 `tool_rag_search` 缺 `corpus` — 静默返回空结果

**位置**: mcp_server.py:265-275
**问题**: server.py:3267 `body.get("corpus", [])` 当 corpus 不传时是 `[]`,然后 `rag_search(query, [], ...)` 返回空。客户端以为在搜,实际啥也没搜到。
**修复**:
```python
async def tool_rag_search(args):
    """RAG 搜索 — corpus 必须由调用方提供(本地知识库 chunk 列表)"""
    corpus = args.get("corpus")
    if not corpus:
        return {
            "content": [{"type": "text",
                         "text": json.dumps({"error": "corpus required",
                                             "hint": "传 corpus=[\"doc1 text\", \"doc2 text\", ...]"}, ensure_ascii=False)}],
            "isError": True,
        }
    out = await _http_post("/v1/capability/rag-search", {
        "query": args["query"],
        "corpus": corpus,
        "max_results": args.get("max_results", args.get("top_k", 5)),
    })
    return {"content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}
```
> 同时把 input_schema 改 `top_k` → `max_results`(跟 server 一致),`corpus` 设为必填。

### P1-4.5 `tool_secret_scan` `fail_on` 类型不匹配

**位置**: mcp_server.py:222-230
**问题**: server `should_block(result, fail_on: int = 3)`,但 mcp 发 `fail_on="high"`。MCP 字符串进 `int` 比较会 TypeError。
**修复**:
```python
_FAIL_ON_MAP = {"low": 1, "medium": 2, "high": 3, "critical": 4}

async def tool_secret_scan(args):
    fail_on_str = args.get("fail_on", "high")
    fail_on_int = _FAIL_ON_MAP.get(fail_on_str.lower(), 3)
    out = await _http_post("/v1/capability/secret-scan", {
        "path": args["path"],
        "fail_on": fail_on_int,
    })
    return {"content": [{"type": "text",
                         "text": json.dumps(out, ensure_ascii=False, default=str)}]}
```

### P1-4.6 `cmd_mcp --transport sse` 是空操作

**位置**: mcp_server.py:325-333 `run_server(transport="sse")` 块
**问题**: 只 print 3 行提示就 return,host/port 参数全没用。用户跑 `start.py mcp --transport sse --port 8911` 啥也没发生。
**修复方案 A (CLI 直接调 uvicorn 拉起 server.py)**:
```python
def run_server(transport: str = "stdio", host: str = "127.0.0.1", port: int = 8911) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    if transport == "stdio":
        try:
            asyncio.run(run_stdio())
        except KeyboardInterrupt:
            print("\n[mcp-server] stopped", file=sys.stderr, flush=True)
    elif transport == "sse":
        # CLI 直接拉起 uvicorn,把 /v1/mcp 和 /v1/mcp/sse 一并暴露
        import uvicorn
        from moa_gateway.server import app
        uvicorn.run(app, host=host, port=port, log_level="info")
    else:
        print(f"unknown transport: {transport}", file=sys.stderr)
        sys.exit(1)
```
**修复方案 B (更干净:start.py 直接转发到 uvicorn,不走 mcp_server)**: 删掉 mcp_server 里的 sse 分支,start.py:
```python
def cmd_mcp(args):
    if args.transport == "stdio":
        from moa_gateway.mcp_server import run_server
        run_server(transport="stdio")
    else:  # sse
        import uvicorn
        from moa_gateway.config import get_settings
        from moa_gateway.server import app
        s = get_settings()
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
```

### P1-4.7 `_ensure_token` 竞态

**位置**: mcp_server.py:48-71
**问题**: `global GATEWAY_TOKEN`,并发 10 个 tool 同时触发时,每个都看到空,都跑 login,最后一个写赢。前 9 个的 Bearer 是空,请求会 401。
**修复**:
```python
import asyncio

_LOGIN_LOCK = asyncio.Lock()
GATEWAY_TOKEN: str = ""

async def _ensure_token() -> str:
    global GATEWAY_TOKEN
    if GATEWAY_TOKEN:
        return GATEWAY_TOKEN
    async with _LOGIN_LOCK:
        if GATEWAY_TOKEN:  # double-check after acquiring
            return GATEWAY_TOKEN
        pwd = os.environ.get("MOA_ADMIN_PASSWORD", "")
        if not pwd:
            return ""
        import urllib.request
        url = GATEWAY_URL.rstrip("/") + "/api/auth/login"
        req = urllib.request.Request(
            url, method="POST",
            headers={"Content-Type": "application/json"},
            data=json.dumps({"username": "admin", "password": pwd}).encode("utf-8"),
        )
        try:
            loop = asyncio.get_running_loop()
            token = await loop.run_in_executor(None, _do_login_sync, req)
            if token:
                GATEWAY_TOKEN = token
                print(f"[mcp-server] auto-login OK, token len={len(token)}",
                      file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[mcp-server] auto-login failed: {e}", file=sys.stderr, flush=True)
    return GATEWAY_TOKEN
```

### P1-4.8 `asyncio.get_event_loop()` 4 处 — Python 3.10+ 弃用

**位置**: mcp_server.py:62, 75, 90, 280
**问题**: `asyncio.get_event_loop()` 在没有 running loop 时,3.10+ 触发 DeprecationWarning,3.12+ 会直接抛 RuntimeError("no running event loop")。
**修复**: 全部换成 `asyncio.get_running_loop()`:
```python
loop = asyncio.get_running_loop()  # 必须在 async 函数内,这里都是
```
注意:`get_running_loop()` 只能在 async 函数内调用,所有 4 个位置都在 async 函数内,安全。

### P1-4.9 `_do_request` 把 HTTPError 转 RuntimeError,丢状态码结构

**位置**: mcp_server.py:94-100
**问题**: MCP client 收到 `RuntimeError("HTTP 401 ...")` 字符串,没法按 status code 分支处理。
**修复**:
```python
def _do_request(req, timeout):
    import urllib.error
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {"_raw": raw}
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        # 让 MCP 看到 isError 而不是 500 字符串
        return {
            "_http_error": True,
            "status": e.code,
            "reason": e.reason,
            "body": body[:2000],
        }
    except urllib.error.URLError as e:
        return {"_network_error": True, "reason": str(e.reason)}
```
然后在 tool handler 里检查 `_http_error` 决定要不要标 `isError: True`:
```python
out = await _http_post(...)
if isinstance(out, dict) and out.get("_http_error"):
    return {"content": [{"type": "text",
                         "text": f"HTTP {out['status']}: {out['body'][:500]}"}],
            "isError": True}
```

### P2-4.10 `line_queue.get` 阻塞默认 executor 线程

**位置**: mcp_server.py:280
**问题**: `run_in_executor(None, line_queue.get)` 永久占用 default executor 的 1 个 worker。如果 MCP 客户端只发 1 条请求然后一直等响应,executor 池 1 个 worker 永远卡在 get,后续 tool 调用并发数 = (CPU 核数 - 1) — 单核机器上就是 0。
**修复**: 用 `asyncio.Queue` + 单独的 producer task:
```python
async def run_stdio() -> None:
    print(f"[mcp-server] {SERVER_NAME} v{SERVER_VERSION} (stdio, gateway={GATEWAY_URL})",
          file=sys.stderr, flush=True)
    aio_q: asyncio.Queue[str] = asyncio.Queue()

    def stdin_reader():
        try:
            for line in sys.stdin:
                aio_q.put_nowait(line)  # 同步线程,不能直接 await → 用 run_coroutine_threadsafe
        except Exception as e:
            print(f"[mcp-server] stdin read error: {e}", file=sys.stderr, flush=True)

    loop = asyncio.get_running_loop()
    def _put():
        for line in sys.stdin:
            asyncio.run_coroutine_threadsafe(aio_q.put(line), loop).result(timeout=1)
    threading.Thread(target=_put, daemon=True).start()

    while True:
        line = await aio_q.get()
        line = line.strip()
        if not line: continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            sys.stdout.write(json.dumps(make_error(None, -32700, f"parse error: {e}")) + "\n")
            sys.stdout.flush(); continue
        resp = await handle_request(req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
```

### P2-4.11 `lambda: _do_request(req, timeout)` 重建闭包

**位置**: mcp_server.py:78, 91
**问题**: 微不足道,但 ruff/PLW2901 会报 "redefined-loop-name"。
**修复**:
```python
import functools
return await loop.run_in_executor(None, functools.partial(_do_request, req, timeout))
```

### P2-4.12 token 写到 stderr(虽然只写 length,但仍可疑)

**位置**: mcp_server.py:60
**问题**: 在受限日志环境(CI 抓 stderr),`token len=128` 看起来无害,但会暴露"系统在登录成功"这个事实。
**修复**: 改用 logger:
```python
logger.info("auto-login OK, token_len=%d", len(token))
```
`logger` 已经在 mcp_server.py 顶部定义。

### L5-4.13 tool 命名 `chat` 过于通用

**位置**: mcp_server.py:84
**问题**: 行业标准 MCP tool 名 (像 `mcp-server-time`、Anthropic 官方例子) 用 `chat_completion` / `llm_chat`。`chat` 太短,容易跟其他 MCP server 撞名。
**修复**: 改名为 `chat_completion`,或者同时保留 `chat` 作 alias。`consensus` 同样 → `multi_model_consensus`。

---

## 文件 5: `moa_gateway/server.py` (line 376-414 新端点)

### 🔴 P0-5.1 `/v1/mcp` 完全无鉴权 — 严重安全漏洞

**位置**: server.py:377-390
**问题**: `@app.post("/v1/mcp")` 没有 `Depends(require_api_key)` 或 `require_admin`。任何能访问 8088 端口的人都能:
- 调 `endpoint_upsert` 加/改/删 LLM 端点 → 投毒模型池
- 调 `capability_dispatch` 跑任意 capability (包括 `secret-scan` 扫描任意路径)
- 调 `chat` 用你配的 key 跑模型 → 费用盗刷
- 调 `capability_list` 拿到所有 76 个 capability 名 → 枚举攻击

这违反 OWASP A01:2021 (Broken Access Control) 和 A04 (Insecure Design)。
**修复** (二选一):

**方案 A (推荐)**: 复用现有 admin 鉴权:
```python
@app.post("/v1/mcp")
async def mcp_http(
    req: Dict[str, Any],
    key_info: Dict[str, Any] = Depends(require_api_key),  # ← 加这行
):
    """MCP over HTTP — JSON-RPC 2.0,需要 API key。"""
    from .mcp_server import process_sse_request
    resp = await process_sse_request(req)
    return resp
```
`require_api_key` 同时认 JWT (admin) 和 API key,见 auth.py:42 起。

**方案 B (更严)**: 专门搞一个 MCP 范围 token:
```python
def require_mcp_token(
    authorization: str = Header(default=""),
) -> dict:
    """MCP 专用鉴权 — 必须显式生成,不复用 admin JWT"""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    token = authorization[7:].strip()
    # 查 token 是否在 mcp_tokens 表(OAuth 风格:每次生成一次性 token)
    from .storage import get_storage
    storage = get_storage()
    rec = storage.verify_mcp_token(token)  # 需在 storage.py 实现
    if not rec:
        raise HTTPException(401, "invalid MCP token")
    return rec
```

### P1-5.2 `/v1/mcp/sse` 实现是空壳

**位置**: server.py:392-414
**问题**: 只返回 `session_id` 然后 keepalive 60s,**从不接收 client 消息**。客户端发了 POST 也接不到。
**修复** (最小化): 在 SSE 端点同时处理 POST 消息(简化版协议):
```python
# SSE 端点改为只用 GET 维持连接,真正的消息走 POST
@app.post("/v1/mcp")
async def mcp_http(
    req: Dict[str, Any],
    key_info: Dict[str, Any] = Depends(require_api_key),
):
    from .mcp_server import process_sse_request
    return await process_sse_request(req)

@app.get("/v1/mcp/sse")
async def mcp_sse(key_info: Dict[str, Any] = Depends(require_api_key)):
    """SSE keepalive — 客户端用 POST /v1/mcp 发消息,这里只维持连接"""
    from fastapi.responses import StreamingResponse
    import asyncio, uuid
    async def event_stream():
        session_id = uuid.uuid4().hex
        yield f"event: endpoint\ndata: {json.dumps({'sessionId': session_id})}\n\n"
        while True:
            await asyncio.sleep(15)
            yield f": keepalive\n\n"
    return StreamingResponse(event_stream(), media_type="text/event-stream")
```
> 注意:加鉴权后,客户端的 EventSource 默认不发 Authorization header — 客户端必须用 `new EventSource(url, {withCredentials: true})` 或 URL query string `?token=xxx`。这是 SSE 协议的固有限制,如果要做到浏览器友好,需要 query token。

### P2-5.3 注释里 "客户端应该用 POST /v1/mcp 通信" 误导

**位置**: server.py:402-405 docstring
**修复**: 加明确警告。

---

## 文件 6: `pyproject.toml`

### P2-6.1 `version = "1.6.6"` 跟 `__version__ = "1.8.1"` 不一致

**位置**: pyproject.toml:18 vs `moa_gateway/__init__.py`
**问题**: `pip show moa-gateway-pro` 会报 1.6.6,`start.py version` 报 1.8.1,用户混乱。
**修复**:
```toml
[project]
name = "moa-gateway-pro"
version = "1.8.1"  # 跟 __init__.py 同步
```
> 建议改用 dynamic 版本:
> ```toml
> [project]
> name = "moa-gateway-pro"
> dynamic = ["version"]
>
> [tool.setuptools.dynamic]
> version = {attr = "moa_gateway.__version__"}
> ```

### P2-6.2 console script 缺 `moa-gateway-test` / `moa-mcp-test` 等 dev 工具

**位置**: pyproject.toml:90-92
**问题**: 没关系,但用户跑 `perf/_mcp_stdio_test.py` 还得手写 subprocess。
**修复** (可选):
```toml
[project.scripts]
moa-gateway = "moa_gateway.__main__:main"
moa-mcp = "moa_gateway.mcp_server:main"
```

---

## 复测建议(给 producer)

修完上面 5 个 P0 + 3 个 P1 后,跑这两个端到端验证:

### 1. L3 契约全验(用 _mcp_stdio_test.py 加 case)

在 `perf/_mcp_stdio_test.py` 的 requests 数组追加:
```python
{"jsonrpc": "2.0", "id": 8, "method": "tools/call",
 "params": {"name": "moa", "arguments": {"query": "2+3"}}},
{"jsonrpc": "2.0", "id": 9, "method": "tools/call",
 "params": {"name": "quality_gate", "arguments": {"query": "这是一个测试文本"}}},
{"jsonrpc": "2.0", "id": 10, "method": "tools/call",
 "params": {"name": "rag_search", "arguments": {"query": "test", "corpus": ["doc1", "doc2"]}}},
{"jsonrpc": "2.0", "id": 11, "method": "tools/call",
 "params": {"name": "consensus", "arguments": {"query": "test", "model_count": 3}}},
{"jsonrpc": "2.0", "id": 12, "method": "tools/call",
 "params": {"name": "secret_scan", "arguments": {"path": "config.yaml", "fail_on": "high"}}},
```
每个 id 必须返回 `"result"`,不能有 `"error"`(除非是预期的 4xx)。

### 2. `/v1/mcp` 鉴权验证

```bash
# 应该 401
curl -X POST http://127.0.0.1:8088/v1/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# 应该 200 + 10 tools
TOKEN=$(curl -X POST http://127.0.0.1:8088/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"YourStrong#Pass1"}' | jq -r .token)
curl -X POST http://127.0.0.1:8088/v1/mcp \
  -H "Content-Type: application/json" -H "Authorization: Bearer $TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | jq '.result.tools | length'
# 期望输出 10
```

### 3. console script 验证

```bash
pip install -e .
moa-gateway --help
moa-mcp --help
python -m moa_gateway --help
```
三个都必须正常输出 help。

---

## 总结

| 类别 | 数量 | 关键项 |
|------|------|--------|
| P0 安全 | 1 | `/v1/mcp` 鉴权 bypass |
| P0 编译/启动 | 1 | `__main__.main()` 不存在 |
| P0 L3 契约 | 3 | quality_gate/moa/consensus 全坏 |
| P1 | 5 | rag_search/secret_scan/sse-impl/竞态/get_event_loop |
| P2 | 7 | 命名/version/docstring/类型注解/error handling |

**核心问题**: server.py 的能力端点命名/参数形状(尤其 `gate-l0` 收 `query` 而非 `text`)和 mcp_server 的 tool 包装层没有契约对账。建议 producer 写一个**端点参数表** (`server.py` 所有 `@app.post` → request model 字段),然后 mcp_server 改 wrapper 时照表填,别拍脑袋。

**未发现的问题(确认 OK)**:
- `__init__.py` 改 `__version__` — OK
- `bootstrap.bootstrap_and_serve` / `repair_venv` / `_pip_install` / `diagnose_*` 签名跟 start.py 调用对得上
- `AuthConfig`/`ServerConfig` 字段名跟 start.py 访问对得上
- `LoginRequest` 返回 `{token, user}` 跟 mcp_server 读 `data.get("token", "")` 对得上
- `tool_capability_list` 调 `action=list_mcp` 真实存在
- `tool_endpoint_upsert` 字段跟 `EndpointUpsert` 形状对得上
- `_mcp_stdio_test.py` 测试用例覆盖了 6 个 tool,作为回归基线 OK
