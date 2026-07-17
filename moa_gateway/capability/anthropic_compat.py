"""Anthropic Messages API 兼容层

实现 MoA Gateway Pro 对 Anthropic Claude Messages API (2024+) 的双向转换:
- 入站: Anthropic 请求体 → 内部统一 chat 格式
- 出站: 内部 chat 响应 / 流式 delta → Anthropic 响应 / SSE 事件

参考:
    L-04  Anthropic Messages API 基础结构 (id/type/role/content/model/stop_reason/usage)
    L-05  system 提取: messages 里 role=system 的条目 → 拼到顶 system 字段
    L-06  空 user 兜底: 缺少 user message 时插入一个空 user 触发对话
    L-07  stream=true 强约束: stream 字段必须显式转发,否则返回 400
    L-08  content_block_delta SSE 格式 (event: + data: 双行 + 空行分隔)
    L-13  tool_use 块 (assistant 调用工具)
    L-14  tool_result 块 (user 回填工具结果)
    L-15  tool_choice 语义: any/auto/tool 三档

设计原则:
    - 纯 dict 转换,不依赖 anthropic-sdk / httpx
    - try/except 兜底,异常时不崩溃而返回安全默认
    - type hints + 中文 docstring,贴近仓库既有风格
"""

from __future__ import annotations

import json
import uuid
from typing import Any

ANTHROPIC_API_VERSION: str = "2023-06-01"

DEFAULT_MAX_TOKENS: int = 4096
DEFAULT_TEMPERATURE: float = 1.0
MAX_TEXT_CHARS: int = 200_000

_VALID_ROLES = {"user", "assistant", "system"}
_KNOWN_CONTENT_TYPES = {"text", "image", "tool_use", "tool_result", "tool_calls"}


def _new_msg_id() -> str:
    raw = uuid.uuid4().hex
    return f"msg_{raw[:26]}"


def _truncate(text: str, limit: int = MAX_TEXT_CHARS) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[...truncated]"


def _safe_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for blk in content:
            if not isinstance(blk, dict):
                parts.append(str(blk))
                continue
            btype = blk.get("type", "text")
            if btype == "text":
                parts.append(str(blk.get("text", "")))
            elif btype == "image":
                parts.append("[image]")
            elif btype == "tool_use":
                parts.append(f"[tool_use:{blk.get('name', '?')}]")
            elif btype == "tool_result":
                parts.append(f"[tool_result:{blk.get('tool_use_id', '?')}]")
            else:
                parts.append(f"[{btype}]")
        return "\n".join(parts)
    return str(content)


def _normalize_content(content: Any) -> Any:
    if content is None:
        return ""
    if isinstance(content, str):
        return _truncate(content)
    if isinstance(content, list):
        norm: list[dict[str, Any]] = []
        for blk in content:
            if not isinstance(blk, dict):
                norm.append({"type": "text", "text": _truncate(str(blk))})
                continue
            try:
                btype = str(blk.get("type", "text"))
                if btype not in _KNOWN_CONTENT_TYPES:
                    btype = "text"
                if btype == "text":
                    norm.append({"type": "text", "text": _truncate(str(blk.get("text", "")))})
                elif btype == "image":
                    src = blk.get("source") or {}
                    if isinstance(src, dict):
                        norm.append({
                            "type": "image",
                            "source": {
                                "type": str(src.get("type", "base64")),
                                "media_type": str(src.get("media_type", "image/png")),
                                "data": str(src.get("data", "")),
                            },
                        })
                    else:
                        norm.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}})
                elif btype == "tool_use":
                    norm.append({
                        "type": "tool_use",
                        "id": str(blk.get("id", f"toolu_{uuid.uuid4().hex[:24]}")),
                        "name": str(blk.get("name", "")),
                        "input": blk.get("input") if isinstance(blk.get("input"), dict) else {},
                    })
                elif btype == "tool_result":
                    rc = blk.get("content")
                    if isinstance(rc, list):
                        rc_text = _safe_text(rc)
                    else:
                        rc_text = "" if rc is None else str(rc)
                    norm.append({
                        "type": "tool_result",
                        "tool_use_id": str(blk.get("tool_use_id", "")),
                        "content": _truncate(rc_text),
                        "is_error": bool(blk.get("is_error", False)),
                    })
                else:
                    norm.append({"type": "text", "text": _truncate(_safe_text(blk))})
            except Exception:
                norm.append({"type": "text", "text": _truncate(str(blk))})
        return norm
    return _truncate(str(content))


def parse_anthropic_request(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        body = {}

    result: dict[str, Any] = {
        "model": str(body.get("model", "") or ""),
        "system": None,
        "messages": [],
        "max_tokens": DEFAULT_MAX_TOKENS,
        "temperature": DEFAULT_TEMPERATURE,
        "stream": bool(body.get("stream", False)),
        "tools": body.get("tools"),
        "tool_choice": body.get("tool_choice"),
        "stop_sequences": body.get("stop_sequences"),
        "metadata": body.get("metadata"),
    }

    try:
        mt = body.get("max_tokens")
        if mt is not None:
            mt_int = int(mt)
            if mt_int > 0:
                result["max_tokens"] = mt_int
    except (TypeError, ValueError):
        pass

    try:
        temp = body.get("temperature")
        if temp is not None:
            t_f = float(temp)
            if t_f < 0.0:
                t_f = 0.0
            elif t_f > 1.0:
                t_f = 1.0
            result["temperature"] = t_f
    except (TypeError, ValueError):
        pass

    top_system = body.get("system")
    sys_parts: list[str] = []
    if top_system is not None:
        sys_parts.append(_safe_text(top_system))

    raw_msgs = body.get("messages") or []
    if not isinstance(raw_msgs, list):
        raw_msgs = []

    out_msgs: list[dict[str, Any]] = []
    for m in raw_msgs:
        if not isinstance(m, dict):
            continue
        try:
            role = str(m.get("role", "user")).lower()
            content = m.get("content", "")
            if role == "system":
                sys_parts.append(_safe_text(content))
                continue
            if role not in ("user", "assistant"):
                role = "user"
            norm_msg = {
                "role": role,
                "content": _normalize_content(content),
            }
            out_msgs.append(norm_msg)
        except Exception:
            continue

    if sys_parts:
        result["system"] = _truncate("\n\n".join([p for p in sys_parts if p]))

    if not out_msgs:
        out_msgs.append({"role": "user", "content": ""})
    elif all(m["role"] == "assistant" for m in out_msgs):
        out_msgs.insert(0, {"role": "user", "content": ""})

    result["messages"] = out_msgs
    return result


def _map_stop_reason(openai_reason: str) -> str:
    if openai_reason in ("length", "max_tokens"):
        return "max_tokens"
    if openai_reason in ("tool_calls", "function_call", "tool_use"):
        return "tool_use"
    if openai_reason in ("stop_sequence",):
        return "stop_sequence"
    if openai_reason in ("content_filter", "safety"):
        return "end_turn"
    return "end_turn"


def format_anthropic_response(chat_response: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(chat_response, dict):
        chat_response = {}

    msg_id = str(chat_response.get("id") or _new_msg_id())
    model = str(chat_response.get("model") or "")

    choices = chat_response.get("choices") or []
    first_choice: dict[str, Any] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        first_choice = choices[0]

    raw_msg = first_choice.get("message") or {}
    if not isinstance(raw_msg, dict):
        raw_msg = {}

    role = str(raw_msg.get("role", "assistant"))
    content_blocks: list[dict[str, Any]] = []

    text_val = raw_msg.get("content")
    if text_val is not None:
        if isinstance(text_val, str):
            if text_val:
                content_blocks.append({"type": "text", "text": _truncate(text_val)})
        elif isinstance(text_val, list):
            for blk in text_val:
                if not isinstance(blk, dict):
                    continue
                btype = str(blk.get("type", "text"))
                if btype == "text":
                    t = str(blk.get("text", ""))
                    if t:
                        content_blocks.append({"type": "text", "text": _truncate(t)})
                elif btype == "tool_calls":
                    for tc in (blk.get("tool_calls") or []):
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function") or {}
                        if not isinstance(fn, dict):
                            fn = {}
                        args_raw = fn.get("arguments", "")
                        try:
                            args_obj = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
                        except (TypeError, ValueError):
                            args_obj = {"_raw": args_raw}
                        if not isinstance(args_obj, dict):
                            args_obj = {"_raw": args_obj}
                        content_blocks.append({
                            "type": "tool_use",
                            "id": str(tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}")),
                            "name": str(fn.get("name", "")),
                            "input": args_obj,
                        })

    if not any(b.get("type") == "tool_use" for b in content_blocks):
        for tc in (raw_msg.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") or {}
            if not isinstance(fn, dict):
                fn = {}
            args_raw = fn.get("arguments", "")
            try:
                args_obj = json.loads(args_raw) if isinstance(args_raw, str) else (args_raw or {})
            except (TypeError, ValueError):
                args_obj = {"_raw": args_raw}
            if not isinstance(args_obj, dict):
                args_obj = {"_raw": args_obj}
            content_blocks.append({
                "type": "tool_use",
                "id": str(tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}")),
                "name": str(fn.get("name", "")),
                "input": args_obj,
            })

    if not content_blocks:
        content_blocks.append({"type": "text", "text": ""})

    openai_stop = str(first_choice.get("finish_reason") or "stop").lower()
    stop_reason = _map_stop_reason(openai_stop)

    usage_raw = chat_response.get("usage") or {}
    if not isinstance(usage_raw, dict):
        usage_raw = {}
    try:
        input_tokens = int(usage_raw.get("prompt_tokens", 0) or 0)
    except (TypeError, ValueError):
        input_tokens = 0
    try:
        output_tokens = int(usage_raw.get("completion_tokens", 0) or 0)
    except (TypeError, ValueError):
        output_tokens = 0

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant" if role != "user" else "assistant",
        "content": content_blocks,
        "model": model,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
    }


def format_anthropic_sse_chunk(
    delta: str,
    model: str,
    stop_reason: str | None = None,
) -> str:
    if not isinstance(delta, str):
        try:
            delta = str(delta or "")
        except Exception:
            delta = ""

    if stop_reason is not None:
        delta_event = (
            f"event: content_block_delta\n"
            f"data: {json.dumps({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': delta}}, ensure_ascii=False)}\n"
            f"\n"
        )
        stop_event = (
            f"event: message_delta\n"
            f"data: {json.dumps({'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': 0}}, ensure_ascii=False)}\n"
            f"\n"
            f"event: message_stop\n"
            f"data: {json.dumps({'type': 'message_stop'}, ensure_ascii=False)}\n"
            f"\n"
        )
        return delta_event + stop_event

    payload = {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "text_delta", "text": delta},
    }
    return (
        f"event: content_block_delta\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n"
        f"\n"
    )


def format_anthropic_message_start(
    msg_id: str | None = None,
    model: str = "",
) -> str:
    if not msg_id:
        msg_id = _new_msg_id()
    payload = {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    }
    return (
        f"event: message_start\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n"
        f"\n"
    )


def format_anthropic_content_block_start(index: int = 0) -> str:
    payload = {
        "type": "content_block_start",
        "index": index,
        "content_block": {"type": "text", "text": ""},
    }
    return (
        f"event: content_block_start\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n"
        f"\n"
    )


def format_anthropic_content_block_stop(index: int = 0) -> str:
    payload = {"type": "content_block_stop", "index": index}
    return (
        f"event: content_block_stop\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n"
        f"\n"
    )


def format_anthropic_tool_use(
    tool_id: str,
    name: str,
    input: dict,
) -> dict[str, Any]:
    if not isinstance(input, dict):
        try:
            input = dict(input) if input else {}
        except Exception:
            input = {"_raw": input}
    return {
        "type": "tool_use",
        "id": str(tool_id or f"toolu_{uuid.uuid4().hex[:24]}"),
        "name": str(name or ""),
        "input": input,
    }


def format_anthropic_tool_result(
    tool_use_id: str,
    content: str,
    is_error: bool = False,
) -> dict[str, Any]:
    if content is None:
        content = ""
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            content = str(content)
    return {
        "type": "tool_result",
        "tool_use_id": str(tool_use_id or ""),
        "content": _truncate(content),
        "is_error": bool(is_error),
    }


def format_anthropic_error(
    openai_error: dict[str, Any],
    status: int = 400,
) -> dict[str, Any]:
    if not isinstance(openai_error, dict):
        openai_error = {}
    err = openai_error.get("error") or {}
    if not isinstance(err, dict):
        err = {}
    msg = str(err.get("message") or openai_error.get("message") or "unknown error")
    etype = str(err.get("type") or err.get("code") or "api_error")
    return {
        "type": "error",
        "error": {
            "type": etype,
            "message": msg,
        },
    }


def normalize_tool_choice(choice: Any) -> str:
    if choice is None:
        return "auto"
    if isinstance(choice, str):
        c = choice.strip().lower()
        if c in ("auto", "any", "none"):
            return c
        if c.startswith("tool:"):
            return c
        if c and c not in ("auto", "any", "none"):
            return f"tool:{c}"
        return "auto"
    if isinstance(choice, dict):
        t = str(choice.get("type", "auto")).lower()
        if t == "tool" and choice.get("name"):
            return f"tool:{choice['name']}"
        if t in ("auto", "any", "none"):
            return t
    return "auto"


__all__ = [
    "ANTHROPIC_API_VERSION",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
    "MAX_TEXT_CHARS",
    "parse_anthropic_request",
    "format_anthropic_response",
    "format_anthropic_sse_chunk",
    "format_anthropic_message_start",
    "format_anthropic_content_block_start",
    "format_anthropic_content_block_stop",
    "format_anthropic_tool_use",
    "format_anthropic_tool_result",
    "format_anthropic_error",
    "normalize_tool_choice",
]
