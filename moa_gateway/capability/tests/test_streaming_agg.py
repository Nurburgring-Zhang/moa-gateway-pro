"""moa_gateway.capability.streaming_agg 真实测试(非 mock)"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from moa_gateway.capability.streaming_agg import (
    CHUNK_SIZE,
    MockStreamingProvider,
    StreamChunk,
    StreamError,
    StreamResult,
    aggregate_stream,
    aggregate_with_fallback,
    merge_deltas,
)


def _run(coro):
    """asyncio.run wrapper for tests"""
    return asyncio.run(coro)


# =============================================================================
# MockStreamingProvider chat_stream
# =============================================================================


def test_mock_provider_text_stream_yields_chunks():
    """chat_stream 是异步生成器,yield StreamChunk"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    prompt = "Hello world, this is a streaming test prompt."
    agen = provider.chat_stream(prompt, model="gpt-test")
    assert hasattr(agen, "__aiter__"), "chat_stream must return async iterator"
    chunks = _run(_collect_async(agen))
    assert len(chunks) > 0
    assert all(isinstance(c, StreamChunk) for c in chunks)
    print(f"  ✓ test_mock_provider_text_stream_yields_chunks "
          f"(n_chunks={len(chunks)})")
    return True


def test_mock_provider_chunk_size_around_120():
    """text chunk 大小 ≈ 120 字符(最后一 chunk 可能更短)"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    # prompt 长度确保 > 120 → 至少 2 个 text chunks
    prompt = "A" * 350
    chunks = _run(_collect_async(provider.chat_stream(prompt, model="m")))
    text_chunks = [c for c in chunks if c.delta_type == "text"]
    assert len(text_chunks) >= 2, f"expected >=2 text chunks, got {len(text_chunks)}"
    for c in text_chunks[:-1]:
        assert len(c.content) == CHUNK_SIZE, (
            f"non-last chunk size should be {CHUNK_SIZE}, got {len(c.content)}"
        )
    last = text_chunks[-1]
    assert 0 < len(last.content) <= CHUNK_SIZE
    print(f"  ✓ test_mock_provider_chunk_size_around_120 "
          f"(text chunks={len(text_chunks)}, last size={len(last.content)})")
    return True


def test_mock_provider_finish_chunk_is_last():
    """finish chunk 一定在最后,且 delta_type=finish"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    chunks = _run(_collect_async(provider.chat_stream("any prompt", model="m")))
    last = chunks[-1]
    assert last.delta_type == "finish"
    assert last.chunk_idx == len(chunks) - 1
    print(f"  ✓ test_mock_provider_finish_chunk_is_last (last idx={last.chunk_idx})")
    return True


def test_mock_provider_tool_call_stream():
    """prompt 含 TOOL:get_weather → 触发 tool_call_start + tool_call_delta + finish"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    chunks = _run(_collect_async(
        provider.chat_stream("TOOL:get_weather please", model="m")
    ))
    starts = [c for c in chunks if c.delta_type == "tool_call_start"]
    deltas = [c for c in chunks if c.delta_type == "tool_call_delta"]
    finishes = [c for c in chunks if c.delta_type == "finish"]
    assert len(starts) == 1
    assert starts[0].tool_call_id == "call_weather_001"
    assert starts[0].tool_call_name == "get_weather"
    assert len(deltas) >= 1, "expected at least 1 tool_call_delta"
    assert all(d.tool_call_id == "call_weather_001" for d in deltas)
    assert len(finishes) == 1
    # chunks 顺序:start → deltas → finish
    types = [c.delta_type for c in chunks]
    assert types[0] == "tool_call_start"
    assert types[-1] == "finish"
    print(f"  ✓ test_mock_provider_tool_call_stream "
          f"(deltas={len(deltas)}, all id='call_weather_001')")
    return True


def test_mock_provider_random_failure_raises():
    """fail_prob=1.0 → 一定抛 StreamError"""
    provider = MockStreamingProvider(fail_prob=1.0, seed=1)
    raised = False
    try:
        _run(_collect_async(provider.chat_stream("any", model="m")))
    except StreamError:
        raised = True
    assert raised, "expected StreamError when fail_prob=1.0"
    assert provider.stats()["n_stream_failures"] >= 1
    print("  ✓ test_mock_provider_random_failure_raises")
    return True


# =============================================================================
# aggregate_stream
# =============================================================================


def test_aggregate_stream_success_concatenates_content():
    """aggregate_stream 成功 → full_content = 所有 text chunks 拼接"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    prompt = "hello streaming world"
    result = _run(aggregate_stream(provider, prompt, model="m"))
    assert isinstance(result, StreamResult)
    assert result.streaming_succeeded is True
    assert result.total_chunks > 0
    # full_content 包含 prompt 的核心字
    assert "hello" in result.full_content
    assert "streaming" in result.full_content
    assert "world" in result.full_content
    # full_content 长度 = text chunks 长度之和
    text_total = sum(len(c.content) for c in result.chunks if c.delta_type == "text")
    assert len(result.full_content) == text_total
    print(f"  ✓ test_aggregate_stream_success_concatenates_content "
          f"(len={len(result.full_content)}, chunks={result.total_chunks})")
    return True


def test_aggregate_stream_failure_marks_flag():
    """aggregate_stream 失败 → streaming_succeeded=False, chunks 仍保留(可能非空)"""
    provider = MockStreamingProvider(fail_prob=1.0, seed=1)
    result = _run(aggregate_stream(provider, "any prompt", model="m"))
    assert result.streaming_succeeded is False
    # chunks 可能在抛错前已收到 0 个 → 仍允许为空
    assert result.finish_reason is None
    # full_content 必为空(失败前没收到 text)
    assert result.full_content == ""
    assert result.tool_calls == []
    print(f"  ✓ test_aggregate_stream_failure_marks_flag "
          f"(chunks={result.total_chunks}, full_content='{result.full_content}')")
    return True


def test_aggregate_stream_tool_calls_parsed():
    """tool_call 流 → result.tool_calls 解析成 OpenAI 风格"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    result = _run(aggregate_stream(
        provider, "TOOL:get_weather please", model="m"
    ))
    assert result.streaming_succeeded is True
    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc["id"] == "call_weather_001"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    # 拼回的 arguments 应为合法 JSON
    import json as _json
    args = _json.loads(tc["function"]["arguments"])
    assert args["city"] == "Beijing"
    assert args["unit"] == "celsius"
    print(f"  ✓ test_aggregate_stream_tool_calls_parsed "
          f"(args={tc['function']['arguments']!r})")
    return True


def test_aggregate_stream_finish_reason_extracted():
    """finish chunk 携带 finish_reason"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    result = _run(aggregate_stream(provider, "extract finish reason", model="m"))
    assert result.streaming_succeeded is True
    # finish chunk 的 content 为空 → 默认填 "stop"
    assert result.finish_reason == "stop"
    print(f"  ✓ test_aggregate_stream_finish_reason_extracted "
          f"(finish_reason={result.finish_reason!r})")
    return True


# =============================================================================
# aggregate_with_fallback
# =============================================================================


def test_aggregate_with_fallback_stream_succeeds():
    """流式成功 → 走流式路径(标志 True)"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    result = _run(aggregate_with_fallback(
        provider, "fallback test prompt", model="m"
    ))
    assert result.streaming_succeeded is True
    assert "fallback" in result.full_content
    assert "test" in result.full_content
    assert "prompt" in result.full_content
    assert result.total_chunks > 0
    print(f"  ✓ test_aggregate_with_fallback_stream_succeeds "
          f"(len={len(result.full_content)}, chunks={result.total_chunks})")
    return True


def test_aggregate_with_fallback_stream_fails_runs_non_stream():
    """流式失败 → 走非流式 fallback"""
    provider = MockStreamingProvider(fail_prob=1.0, seed=1)
    result = _run(aggregate_with_fallback(
        provider, "fallback after failure", model="m"
    ))
    assert result.streaming_succeeded is False
    # non-stream 返回 "Echo: <prompt>"
    assert "Echo:" in result.full_content
    assert "fallback" in result.full_content
    assert result.finish_reason == "stop"
    print(f"  ✓ test_aggregate_with_fallback_stream_fails_runs_non_stream "
          f"(full_content={result.full_content!r})")
    return True


def test_aggregate_with_fallback_non_stream_wrap_into_chunks():
    """非流式结果 wrap 成 1 个 text chunk + 1 个 finish chunk"""
    provider = MockStreamingProvider(fail_prob=1.0, seed=1)
    result = _run(aggregate_with_fallback(provider, "wrap me", model="m"))
    text_chunks = [c for c in result.chunks if c.delta_type == "text"]
    finish_chunks = [c for c in result.chunks if c.delta_type == "finish"]
    assert len(text_chunks) == 1, f"expected 1 text chunk, got {len(text_chunks)}"
    assert len(finish_chunks) == 1
    # text chunk content = non-stream 返回的完整文本
    assert text_chunks[0].content == "Echo: wrap me"
    # finish chunk content = "stop"
    assert finish_chunks[0].content == "stop"
    print(f"  ✓ test_aggregate_with_fallback_non_stream_wrap_into_chunks "
          f"(text chunk content={text_chunks[0].content!r})")
    return True


# =============================================================================
# merge_deltas
# =============================================================================


def test_merge_deltas_concatenates_args():
    """merge_deltas 把同一 tool_call_id 的 args_delta 拼回完整 JSON 字符串"""
    chunks = [
        StreamChunk(
            chunk_idx=0, content="", delta_type="tool_call_start",
            tool_call_id="call_abc", tool_call_name="get_weather",
        ),
        StreamChunk(
            chunk_idx=1, content="", delta_type="tool_call_delta",
            tool_call_id="call_abc", tool_call_args_delta='{"city":',
        ),
        StreamChunk(
            chunk_idx=2, content="", delta_type="tool_call_delta",
            tool_call_id="call_abc", tool_call_args_delta=' "Beijing"}',
        ),
        StreamChunk(chunk_idx=3, content="", delta_type="finish"),
    ]
    tc = merge_deltas(chunks)
    assert tc["id"] == "call_abc"
    assert tc["type"] == "function"
    assert tc["function"]["name"] == "get_weather"
    assert tc["function"]["arguments"] == '{"city": "Beijing"}'
    # 能 json.loads
    import json as _json
    parsed = _json.loads(tc["function"]["arguments"])
    assert parsed["city"] == "Beijing"
    print(f"  ✓ test_merge_deltas_concatenates_args "
          f"(arguments={tc['function']['arguments']!r})")
    return True


def test_merge_deltas_empty_chunks():
    """空 / 无 tool_call → 默认 dict"""
    tc_empty = merge_deltas([])
    assert tc_empty == {
        "id": "",
        "type": "function",
        "function": {"name": "", "arguments": ""},
    }
    tc_text_only = merge_deltas([
        StreamChunk(chunk_idx=0, content="hi", delta_type="text"),
    ])
    assert tc_text_only["id"] == ""
    assert tc_text_only["function"]["name"] == ""
    assert tc_text_only["function"]["arguments"] == ""
    print("  ✓ test_merge_deltas_empty_chunks")
    return True


# =============================================================================
# StreamError
# =============================================================================


def test_stream_error_is_exception():
    """StreamError 是 Exception 子类"""
    assert issubclass(StreamError, Exception)
    e = StreamError("boom")
    assert isinstance(e, Exception)
    assert str(e) == "boom"
    # 可 raise + catch
    try:
        raise StreamError("sse broken")
    except StreamError as caught:
        assert "sse" in str(caught)
    print("  ✓ test_stream_error_is_exception")
    return True


# =============================================================================
# Edge cases
# =============================================================================


def test_empty_prompt_one_finish_chunk():
    """空 prompt → 1 个 finish chunk(无 text)"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    chunks = _run(_collect_async(provider.chat_stream("", model="m")))
    assert len(chunks) == 1
    assert chunks[0].delta_type == "finish"
    print("  ✓ test_empty_prompt_one_finish_chunk")
    return True


def test_multiple_aggregates_have_independent_state():
    """同一 provider 多次 aggregate → 互不影响(无状态污染)"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    r1 = _run(aggregate_stream(provider, "first call", model="m"))
    r2 = _run(aggregate_stream(provider, "second call", model="m"))
    assert r1.streaming_succeeded and r2.streaming_succeeded
    assert "first" in r1.full_content
    assert "second" in r2.full_content
    assert "first" not in r2.full_content
    assert "second" not in r1.full_content
    # stats 累加
    assert provider.stats()["n_stream_calls"] == 2
    assert provider.stats()["n_stream_success"] == 2
    print(f"  ✓ test_multiple_aggregates_have_independent_state "
          f"(stats={provider.stats()})")
    return True


def test_async_runs_with_asyncio_run():
    """asyncio.run 能直接跑 aggregate(端到端 smoke)"""
    provider = MockStreamingProvider(fail_prob=0.0, seed=42)
    result = asyncio.run(aggregate_with_fallback(
        provider, "asyncio.run smoke test", model="m"
    ))
    assert result.streaming_succeeded is True
    assert "asyncio" in result.full_content
    assert result.total_chunks > 0
    print(f"  ✓ test_async_runs_with_asyncio_run "
          f"(chunks={result.total_chunks})")
    return True


# =============================================================================
# Helpers
# =============================================================================


async def _collect_async(agen):
    """把 async generator 收集成 list"""
    out = []
    async for item in agen:
        out.append(item)
    return out
