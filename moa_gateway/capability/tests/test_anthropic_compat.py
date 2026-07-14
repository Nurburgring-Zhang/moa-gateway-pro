"""anthropic_compat 单元测试 (≥30 测试,纯 assert,无 mock)

覆盖:
- 简单 string message
- list content 块
- system 提取 (顶层 + messages 里)
- 多 message 顺序
- tool_use / tool_result round-trip
- max_tokens 默认
- max_tokens 越界保护
- temperature 默认 1.0
- temperature clamp
- stream 标志
- Response format (Anthropic structure)
- stop_reason 映射
- SSE content_block_delta 格式
- SSE stop 双事件 (message_delta + message_stop)
- 空 messages 兜底
- 全 assistant 兜底补 user
- 超长 content 截断
- Unicode (中英混合)
- Tool choice "any" / "auto" / "tool" / dict / None
- Error response 转换 (OpenAI error → Anthropic error)
- image content 块 (base64)
- 多 content 块 (text + image)
- 顶层 tool_calls → tool_use 块
- 消息里 tool_use (assistant) + tool_result (user) 来回
- 异常输入兜底 (非 dict body / 非 list messages)
- usage 转换
- message_start / content_block_start / content_block_stop 格式
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.anthropic_compat import (
    ANTHROPIC_API_VERSION,
    DEFAULT_MAX_TOKENS,
    DEFAULT_TEMPERATURE,
    MAX_TEXT_CHARS,
    parse_anthropic_request,
    format_anthropic_response,
    format_anthropic_sse_chunk,
    format_anthropic_message_start,
    format_anthropic_content_block_start,
    format_anthropic_content_block_stop,
    format_anthropic_tool_use,
    format_anthropic_tool_result,
    format_anthropic_error,
    normalize_tool_choice,
)


# ============================================================
# 1) parse_anthropic_request — 基础
# ============================================================

def test_parse_simple_string_message():
    """单条 string user message → 标准化"""
    body = {
        "model": "claude-3-5-sonnet-20241022",
        "messages": [{"role": "user", "content": "Hello"}],
        "max_tokens": 1024,
    }
    out = parse_anthropic_request(body)
    assert out["model"] == "claude-3-5-sonnet-20241022"
    assert out["messages"] == [{"role": "user", "content": "Hello"}]
    assert out["max_tokens"] == 1024
    assert out["system"] is None
    assert out["stream"] is False
    print("  ✓ test_parse_simple_string_message")


def test_parse_list_content_text_block():
    """list content 块: 单 text 块"""
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": "Hi from list"}],
        }],
    }
    out = parse_anthropic_request(body)
    assert out["messages"][0]["role"] == "user"
    content = out["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[0]["text"] == "Hi from list"
    print("  ✓ test_parse_list_content_text_block")


def test_parse_image_block():
    """image 块保留 source 字段"""
    body = {
        "model": "claude-3-5-sonnet",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": "BASE64DATA==",
                    },
                },
            ],
        }],
    }
    out = parse_anthropic_request(body)
    blocks = out["messages"][0]["content"]
    types = [b["type"] for b in blocks]
    assert types == ["text", "image"]
    assert blocks[1]["source"]["media_type"] == "image/jpeg"
    assert blocks[1]["source"]["data"] == "BASE64DATA=="
    print("  ✓ test_parse_image_block")


def test_parse_multi_content_mixed():
    """text + image 混合 list"""
    body = {
        "model": "m",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "看看图片"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "X"}},
                {"type": "text", "text": "有什么?"},
            ],
        }],
    }
    out = parse_anthropic_request(body)
    blocks = out["messages"][0]["content"]
    assert len(blocks) == 3
    assert blocks[0]["text"] == "看看图片"
    assert blocks[2]["text"] == "有什么?"
    print("  ✓ test_parse_multi_content_mixed")


# ============================================================
# 2) system 提取 (L-05)
# ============================================================

def test_parse_system_top_level():
    """顶层 system 字段直通"""
    body = {
        "model": "m",
        "system": "You are a helpful assistant.",
        "messages": [{"role": "user", "content": "Hi"}],
    }
    out = parse_anthropic_request(body)
    assert out["system"] == "You are a helpful assistant."
    print("  ✓ test_parse_system_top_level")


def test_parse_system_from_messages():
    """messages 里 role=system 提取到顶"""
    body = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "SysA"},
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "system", "content": "SysB"},
            {"role": "user", "content": "Q2"},
        ],
    }
    out = parse_anthropic_request(body)
    assert out["system"] is not None
    assert "SysA" in out["system"]
    assert "SysB" in out["system"]
    assert out["system"].find("SysA") < out["system"].find("SysB")
    # 消息里不再有 role=system
    assert all(m["role"] != "system" for m in out["messages"])
    assert len(out["messages"]) == 3
    print("  ✓ test_parse_system_from_messages")


def test_parse_system_top_and_in_messages_concat():
    """顶层 system + messages 里 system 拼接"""
    body = {
        "model": "m",
        "system": "TopSys",
        "messages": [
            {"role": "system", "content": "MsgSys"},
            {"role": "user", "content": "Q"},
        ],
    }
    out = parse_anthropic_request(body)
    assert "TopSys" in out["system"]
    assert "MsgSys" in out["system"]
    assert out["system"].find("TopSys") < out["system"].find("MsgSys")
    print("  ✓ test_parse_system_top_and_in_messages_concat")


# ============================================================
# 3) 多 message 顺序 / 角色
# ============================================================

def test_parse_multi_message_order_preserved():
    """多 message 顺序不变"""
    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ],
    }
    out = parse_anthropic_request(body)
    roles = [m["role"] for m in out["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    texts = [m["content"] for m in out["messages"]]
    assert texts == ["u1", "a1", "u2", "a2"]
    print("  ✓ test_parse_multi_message_order_preserved")


def test_parse_unknown_role_falls_back_to_user():
    """未知 role → user"""
    body = {
        "model": "m",
        "messages": [
            {"role": "alien", "content": "x"},
            {"role": "user", "content": "y"},
        ],
    }
    out = parse_anthropic_request(body)
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][1]["role"] == "user"
    print("  ✓ test_parse_unknown_role_falls_back_to_user")


# ============================================================
# 4) 兜底 (L-06) / stream (L-07) / max_tokens / temperature
# ============================================================

def test_parse_empty_messages_inserts_blank_user():
    """空 messages → 注入空 user (L-06)"""
    body = {"model": "m", "messages": []}
    out = parse_anthropic_request(body)
    assert len(out["messages"]) == 1
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][0]["content"] == ""
    print("  ✓ test_parse_empty_messages_inserts_blank_user")


def test_parse_all_assistant_prepends_user():
    """全是 assistant → 前补空 user"""
    body = {
        "model": "m",
        "messages": [{"role": "assistant", "content": "only a"}],
    }
    out = parse_anthropic_request(body)
    assert out["messages"][0]["role"] == "user"
    assert out["messages"][1]["role"] == "assistant"
    print("  ✓ test_parse_all_assistant_prepends_user")


def test_parse_stream_flag_default_false():
    """缺省 stream = False (L-07)"""
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    out = parse_anthropic_request(body)
    assert out["stream"] is False
    print("  ✓ test_parse_stream_flag_default_false")


def test_parse_stream_flag_true_passes_through():
    """stream=true 显式直通 (L-07)"""
    body = {"model": "m", "stream": True, "messages": [{"role": "user", "content": "x"}]}
    out = parse_anthropic_request(body)
    assert out["stream"] is True
    print("  ✓ test_parse_stream_flag_true_passes_through")


def test_parse_max_tokens_default():
    """max_tokens 缺省 = DEFAULT_MAX_TOKENS"""
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    out = parse_anthropic_request(body)
    assert out["max_tokens"] == DEFAULT_MAX_TOKENS
    assert DEFAULT_MAX_TOKENS == 4096
    print("  ✓ test_parse_max_tokens_default")


def test_parse_temperature_default_one():
    """temperature 缺省 = 1.0"""
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    out = parse_anthropic_request(body)
    assert out["temperature"] == 1.0
    assert DEFAULT_TEMPERATURE == 1.0
    print("  ✓ test_parse_temperature_default_one")


def test_parse_temperature_clamped_above_one():
    """temperature > 1 → 截到 1.0"""
    body = {"model": "m", "temperature": 5.0, "messages": [{"role": "user", "content": "x"}]}
    out = parse_anthropic_request(body)
    assert out["temperature"] == 1.0
    print("  ✓ test_parse_temperature_clamped_above_one")


def test_parse_temperature_clamped_below_zero():
    """temperature < 0 → 截到 0.0"""
    body = {"model": "m", "temperature": -2.0, "messages": [{"role": "user", "content": "x"}]}
    out = parse_anthropic_request(body)
    assert out["temperature"] == 0.0
    print("  ✓ test_parse_temperature_clamped_below_zero")


# ============================================================
# 5) format_anthropic_response
# ============================================================

def test_format_response_basic_structure():
    """Anthropic 响应基础结构"""
    chat = {
        "id": "chatcmpl-abc",
        "model": "claude-3-5-sonnet",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "Hello back"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    out = format_anthropic_response(chat)
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert isinstance(out["content"], list)
    assert out["content"][0]["type"] == "text"
    assert out["content"][0]["text"] == "Hello back"
    assert out["model"] == "claude-3-5-sonnet"
    assert out["stop_reason"] == "end_turn"
    assert out["usage"]["input_tokens"] == 10
    assert out["usage"]["output_tokens"] == 5
    assert out["id"].startswith("msg_") or out["id"].startswith("chatcmpl-")
    print("  ✓ test_format_response_basic_structure")


def test_format_response_stop_reason_length_to_max_tokens():
    """OpenAI 'length' → Anthropic 'max_tokens'"""
    chat = {
        "model": "m",
        "choices": [{
            "message": {"role": "assistant", "content": "..."},
            "finish_reason": "length",
        }],
    }
    out = format_anthropic_response(chat)
    assert out["stop_reason"] == "max_tokens"
    print("  ✓ test_format_response_stop_reason_length_to_max_tokens")


def test_format_response_stop_reason_tool_calls_to_tool_use():
    """OpenAI 'tool_calls' → Anthropic 'tool_use'"""
    chat = {
        "model": "m",
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "arguments": json.dumps({"city": "SF"}),
                    },
                }],
            },
            "finish_reason": "tool_calls",
        }],
    }
    out = format_anthropic_response(chat)
    assert out["stop_reason"] == "tool_use"
    assert any(b.get("type") == "tool_use" for b in out["content"])
    tu = next(b for b in out["content"] if b["type"] == "tool_use")
    assert tu["name"] == "get_weather"
    assert tu["input"] == {"city": "SF"}
    assert tu["id"] == "call_1"
    print("  ✓ test_format_response_stop_reason_tool_calls_to_tool_use")


def test_format_response_empty_content_safe():
    """空 content 至少一个 text 块 (Anthropic 不允许空数组)"""
    chat = {
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
    }
    out = format_anthropic_response(chat)
    assert len(out["content"]) >= 1
    assert out["content"][0]["type"] == "text"
    print("  ✓ test_format_response_empty_content_safe")


def test_format_response_handles_invalid_choices():
    """异常 choices → 仍然返回有效 Anthropic 结构"""
    out = format_anthropic_response({"model": "m", "choices": "bogus"})
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert isinstance(out["content"], list)
    print("  ✓ test_format_response_handles_invalid_choices")


# ============================================================
# 6) SSE 事件流 (L-08)
# ============================================================

def test_sse_content_block_delta_format():
    """content_block_delta 事件: event: + data: 双行 + 空行"""
    sse = format_anthropic_sse_chunk("Hello", model="m")
    assert sse.startswith("event: content_block_delta\n")
    # data 行
    assert "data: {" in sse
    # 结尾空行
    assert sse.endswith("\n\n")
    # 解析 data 行的 JSON
    data_line = [ln for ln in sse.split("\n") if ln.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: "):])
    assert payload["type"] == "content_block_delta"
    assert payload["index"] == 0
    assert payload["delta"]["type"] == "text_delta"
    assert payload["delta"]["text"] == "Hello"
    print("  ✓ test_sse_content_block_delta_format")


def test_sse_stop_emits_message_delta_and_message_stop():
    """stop_reason 给定时输出 message_delta + message_stop 两个事件"""
    sse = format_anthropic_sse_chunk("final", model="m", stop_reason="end_turn")
    assert "event: content_block_delta\n" in sse
    assert "event: message_delta\n" in sse
    assert "event: message_stop\n" in sse
    # 解析 message_delta 的 data
    md_line = [ln for ln in sse.split("\n") if ln.startswith("data: ")][1]
    md = json.loads(md_line[len("data: "):])
    assert md["type"] == "message_delta"
    assert md["delta"]["stop_reason"] == "end_turn"
    # 解析 message_stop
    ms_line = [ln for ln in sse.split("\n") if ln.startswith("data: ")][2]
    ms = json.loads(ms_line[len("data: "):])
    assert ms["type"] == "message_stop"
    print("  ✓ test_sse_stop_emits_message_delta_and_message_stop")


def test_sse_unicode_passthrough():
    """Unicode (中文) 在 SSE data 中以 ensure_ascii=False 直传"""
    sse = format_anthropic_sse_chunk("你好,世界", model="m")
    assert "你好,世界" in sse
    print("  ✓ test_sse_unicode_passthrough")


def test_sse_message_start_format():
    """message_start 事件格式正确"""
    sse = format_anthropic_message_start(msg_id="msg_test_123", model="claude-x")
    assert sse.startswith("event: message_start\n")
    data_line = [ln for ln in sse.split("\n") if ln.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: "):])
    assert payload["type"] == "message_start"
    assert payload["message"]["id"] == "msg_test_123"
    assert payload["message"]["model"] == "claude-x"
    assert payload["message"]["role"] == "assistant"
    print("  ✓ test_sse_message_start_format")


def test_sse_content_block_start_and_stop():
    """content_block_start / content_block_stop 格式"""
    s = format_anthropic_content_block_start(index=0)
    assert "event: content_block_start" in s
    e = format_anthropic_content_block_stop(index=0)
    assert "event: content_block_stop" in e
    print("  ✓ test_sse_content_block_start_and_stop")


# ============================================================
# 7) tool_use / tool_result 块 (L-13 / L-14 / L-15)
# ============================================================

def test_tool_use_block_shape():
    """tool_use 块结构"""
    tu = format_anthropic_tool_use("toolu_abc", "search", {"q": "x"})
    assert tu["type"] == "tool_use"
    assert tu["id"] == "toolu_abc"
    assert tu["name"] == "search"
    assert tu["input"] == {"q": "x"}
    print("  ✓ test_tool_use_block_shape")


def test_tool_result_block_shape():
    """tool_result 块结构 + is_error 标志"""
    tr = format_anthropic_tool_result("toolu_abc", "42°F", is_error=False)
    assert tr["type"] == "tool_result"
    assert tr["tool_use_id"] == "toolu_abc"
    assert tr["content"] == "42°F"
    assert tr["is_error"] is False
    # is_error=True
    tr2 = format_anthropic_tool_result("toolu_x", "boom", is_error=True)
    assert tr2["is_error"] is True
    print("  ✓ test_tool_result_block_shape")


def test_tool_round_trip_through_parse():
    """tool_use (assistant) + tool_result (user) 来回 parse 保留"""
    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "What's the weather in SF?"},
            {
                "role": "assistant",
                "content": [{
                    "type": "tool_use",
                    "id": "toolu_xyz",
                    "name": "get_weather",
                    "input": {"city": "SF"},
                }],
            },
            {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": "toolu_xyz",
                    "content": "72°F, sunny",
                    "is_error": False,
                }],
            },
        ],
    }
    out = parse_anthropic_request(body)
    assert len(out["messages"]) == 3
    # assistant 块含 tool_use
    asst_blocks = out["messages"][1]["content"]
    assert asst_blocks[0]["type"] == "tool_use"
    assert asst_blocks[0]["id"] == "toolu_xyz"
    assert asst_blocks[0]["input"] == {"city": "SF"}
    # user 块含 tool_result
    user_blocks = out["messages"][2]["content"]
    assert user_blocks[0]["type"] == "tool_result"
    assert user_blocks[0]["tool_use_id"] == "toolu_xyz"
    assert user_blocks[0]["content"] == "72°F, sunny"
    assert user_blocks[0]["is_error"] is False
    print("  ✓ test_tool_round_trip_through_parse")


# ============================================================
# 8) 边界 / 容错
# ============================================================

def test_parse_non_dict_body_safe():
    """body 不是 dict → 返回安全默认"""
    for bad in (None, "string", 123, ["x"]):
        out = parse_anthropic_request(bad)  # type: ignore[arg-type]
        assert isinstance(out, dict)
        assert "messages" in out
        assert len(out["messages"]) >= 1
    print("  ✓ test_parse_non_dict_body_safe")


def test_parse_unicode_content():
    """中英混合 content 保留"""
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "你好,Hello,こんにちは"}],
    }
    out = parse_anthropic_request(body)
    assert "你好" in out["messages"][0]["content"]
    assert "Hello" in out["messages"][0]["content"]
    assert "こんにちは" in out["messages"][0]["content"]
    print("  ✓ test_parse_unicode_content")


def test_parse_truncates_huge_content():
    """超长 content 截断 (避免异常 payload)"""
    huge = "a" * (MAX_TEXT_CHARS + 5000)
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": huge}],
    }
    out = parse_anthropic_request(body)
    assert len(out["messages"][0]["content"]) <= MAX_TEXT_CHARS + 50
    assert "[...truncated]" in out["messages"][0]["content"]
    print("  ✓ test_parse_truncates_huge_content")


def test_parse_tools_field_preserved():
    """tools 字段直通"""
    tools = [{"name": "a", "description": "d", "input_schema": {}}]
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "tools": tools,
    }
    out = parse_anthropic_request(body)
    assert out["tools"] == tools
    print("  ✓ test_parse_tools_field_preserved")


# ============================================================
# 9) Tool choice 归一化
# ============================================================

def test_tool_choice_auto():
    assert normalize_tool_choice("auto") == "auto"
    assert normalize_tool_choice(None) == "auto"
    print("  ✓ test_tool_choice_auto")


def test_tool_choice_any():
    assert normalize_tool_choice("any") == "any"
    print("  ✓ test_tool_choice_any")


def test_tool_choice_tool_named():
    assert normalize_tool_choice("tool:search") == "tool:search"
    assert normalize_tool_choice({"type": "tool", "name": "search"}) == "tool:search"
    # 裸 name → 包装为 tool:<name>
    assert normalize_tool_choice("search") == "tool:search"
    print("  ✓ test_tool_choice_tool_named")


def test_tool_choice_none():
    assert normalize_tool_choice("none") == "none"
    print("  ✓ test_tool_choice_none")


# ============================================================
# 10) Error response 转换
# ============================================================

def test_error_conversion_openai_to_anthropic():
    """OpenAI error → Anthropic error"""
    oai_err = {
        "error": {
            "message": "Rate limit exceeded",
            "type": "rate_limit_error",
            "code": "rate_limit",
        }
    }
    out = format_anthropic_error(oai_err)
    assert out["type"] == "error"
    assert out["error"]["type"] == "rate_limit_error"
    assert out["error"]["message"] == "Rate limit exceeded"
    print("  ✓ test_error_conversion_openai_to_anthropic")


def test_error_conversion_handles_missing_keys():
    """缺字段时仍返回有效 Anthropic 错误"""
    out = format_anthropic_error({})
    assert out["type"] == "error"
    assert "message" in out["error"]
    print("  ✓ test_error_conversion_handles_missing_keys")


# ============================================================
# 11) 其它
# ============================================================

def test_anthropic_api_version_constant():
    """Anthropic API 版本常量"""
    assert ANTHROPIC_API_VERSION == "2023-06-01"
    print("  ✓ test_anthropic_api_version_constant")


def test_metadata_and_stop_sequences_preserved():
    """metadata / stop_sequences 字段保留"""
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "x"}],
        "metadata": {"user_id": "u1"},
        "stop_sequences": ["END"],
    }
    out = parse_anthropic_request(body)
    assert out["metadata"] == {"user_id": "u1"}
    assert out["stop_sequences"] == ["END"]
    print("  ✓ test_metadata_and_stop_sequences_preserved")


def test_format_response_invalid_input_safe():
    """完全非 dict 输入 → 安全默认"""
    for bad in (None, "x", 42, []):
        out = format_anthropic_response(bad)  # type: ignore[arg-type]
        assert out["type"] == "message"
        assert isinstance(out["content"], list)
    print("  ✓ test_format_response_invalid_input_safe")


def test_tool_use_input_must_be_dict():
    """非 dict input → 强制 dict"""
    tu = format_anthropic_tool_use("t1", "f", "notadict")  # type: ignore[arg-type]
    assert isinstance(tu["input"], dict)
    tu2 = format_anthropic_tool_use("t2", "f", None)  # type: ignore[arg-type]
    assert tu2["input"] == {}
    print("  ✓ test_tool_use_input_must_be_dict")


def test_parse_image_block_missing_source_safe():
    """image 块缺 source → 安全默认"""
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": [{"type": "image"}]}],
    }
    out = parse_anthropic_request(body)
    blk = out["messages"][0]["content"][0]
    assert blk["type"] == "image"
    assert blk["source"]["data"] == ""
    print("  ✓ test_parse_image_block_missing_source_safe")


# ============================================================
# runner
# ============================================================

def _run_all() -> int:
    """按定义顺序跑所有 test_*,返回通过数 / 总数。"""
    import inspect
    current = sys.modules[__name__]
    tests = [
        (name, fn)
        for name, fn in inspect.getmembers(current, inspect.isfunction)
        if name.startswith("test_")
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}: {type(e).__name__}: {e}")
    return passed, failed, len(tests)


if __name__ == "__main__":
    p, f, total = _run_all()
    print(f"\n{'-' * 60}")
    print(f"anthropic_compat tests: {p}/{total} pass ({f} fail)")
    if f == 0:
        print("ALL PASS")
    else:
        print("HAS FAILURES")
        sys.exit(1)
