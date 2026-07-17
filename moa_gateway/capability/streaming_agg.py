"""moa_gateway.capability.streaming_agg — Aggregator 流式 + 非流式 / Stream delta 完整代理

来源: 08 moa-server (流式优先 + 失败回非流式)

提供:
- StreamError: 流式调用失败时的异常
- StreamChunk: 单条流式 chunk(text / tool_call_start / tool_call_delta / finish / error)
- StreamResult: 聚合后的完整结果(chunks + full_content + tool_calls + finish_reason)
- MockStreamingProvider: 模拟 OpenAI SSE 流式响应(按 120 字符分块 + 1/10 概率随机失败)
- aggregate_stream: 跑 provider.chat_stream → 收集 chunks + 拼 full_content + 解析 tool_calls
- aggregate_with_fallback: 先流式,失败 → 一次性非流式,wrap 成 1 个 chunk
- merge_deltas: 把同一 tool_call_id 的 args_delta 拼成完整 tool_call dict

与 FastAPI 集成示例(/v1/chat/completions 转发流式响应):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from moa_gateway.capability.streaming_agg import (
        MockStreamingProvider, aggregate_with_fallback, StreamChunk
    )

    app = FastAPI()
    provider = MockStreamingProvider()

    @app.post("/v1/chat/completions")
    async def chat_completions(req: dict):
        stream = bool(req.get("stream", False))
        prompt = req.get("messages", [{}])[-1].get("content", "")
        result = await aggregate_with_fallback(provider, prompt, req.get("model", "gpt-3.5-turbo"))

        if stream:
            async def sse():
                for ch in result.chunks:
                    yield f"data: {ch.content or ''}\\n\\n"
                yield "data: [DONE]\\n\\n"
            return StreamingResponse(sse(), media_type="text/event-stream")
        else:
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": result.full_content,
                    },
                    "finish_reason": result.finish_reason,
                }],
                "streaming_succeeded": result.streaming_succeeded,
            }

设计约束:
- 真实异步生成器:用 asyncio.sleep 模拟网络延迟,所有方法都是 async
- tool_call 协议:沿用 OpenAI 风格(id / type / function.{name, arguments})
- merge_deltas 把增量 args 拼回 JSON 字符串(不解析,只拼接;下游再 json.loads)
- aggregate_stream 失败时 chunks 仍保留(partial result),streaming_succeeded=False
- aggregate_with_fallback 走非流式时,MockProvider.chat 返回完整 text → wrap 成单个 text chunk + finish chunk
- 本模块不依赖外部 SDK,纯 stdlib + typing
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import (
    Any,
    Literal,
    Protocol,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

__all__ = [
    "StreamError",
    "StreamChunk",
    "StreamResult",
    "MockStreamingProvider",
    "aggregate_stream",
    "aggregate_with_fallback",
    "merge_deltas",
    "CHUNK_SIZE",
]


# =============================================================================
# Constants
# =============================================================================


CHUNK_SIZE: int = 120
"""流式分块大小(字符数)。MockStreamingProvider 按此阈值切分 prompt。"""


# =============================================================================
# Exception
# =============================================================================


class StreamError(Exception):
    """流式调用失败

    用于:
    - MockStreamingProvider 的随机失败
    - aggregate_with_fallback 触发非流式 fallback
    - 上游 SSE 中断 / parse error / 连接重置
    """


# =============================================================================
# Dataclasses
# =============================================================================


DeltaType = Literal["text", "tool_call_start", "tool_call_delta", "finish", "error"]


@dataclass
class StreamChunk:
    """单条流式 chunk

    Attributes:
        chunk_idx: 在流中的序号(0-based)
        content: text 增量内容(对 tool_call_start / tool_call_delta / finish / error 为 "")
        delta_type: 类别
        tool_call_id: tool_call 唯一 id(仅 tool_call_* 类型有)
        tool_call_name: function 名(仅 tool_call_start 有)
        tool_call_args_delta: function arguments 的 JSON 增量字符串(仅 tool_call_delta 有)
    """

    chunk_idx: int
    content: str
    delta_type: DeltaType = "text"
    tool_call_id: str | None = None
    tool_call_name: str | None = None
    tool_call_args_delta: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "chunk_idx": self.chunk_idx,
            "content": self.content,
            "delta_type": self.delta_type,
        }
        if self.tool_call_id is not None:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_call_name is not None:
            d["tool_call_name"] = self.tool_call_name
        if self.tool_call_args_delta is not None:
            d["tool_call_args_delta"] = self.tool_call_args_delta
        return d


@dataclass
class StreamResult:
    """流式聚合结果

    Attributes:
        chunks: 按序的所有 StreamChunk
        full_content: 拼好的纯文本(仅 text chunks 拼接)
        tool_calls: 合并 tool_call_start + tool_call_delta 后还原的 tool_calls
                    list[{id, type, function: {name, arguments}}]
        finish_reason: 来自 finish chunk("stop" / "length" / "tool_calls" / None)
        total_chunks: chunks 数
        streaming_succeeded: 是否成功走完流式;失败时 False 但 chunks 仍保留
    """

    chunks: list[StreamChunk] = field(default_factory=list)
    full_content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    total_chunks: int = 0
    streaming_succeeded: bool = False


# =============================================================================
# Provider protocol(typing only,MockStreamingProvider 实现)
# =============================================================================


@runtime_checkable
class StreamingProviderProtocol(Protocol):
    """Provider 协议 — aggregate_stream / aggregate_with_fallback 依赖的最小接口

    - chat_stream(prompt, model) → AsyncIterator[StreamChunk]
    - chat(prompt) → str (非流式同步返回完整文本;aggregate_with_fallback 用)
    """

    def chat_stream(
        self, prompt: str, model: str = ""
    ) -> AsyncIterator[StreamChunk]: ...

    def chat(self, prompt: str) -> str: ...


# =============================================================================
# Mock Streaming Provider(模拟 OpenAI SSE 流式响应)
# =============================================================================


class MockStreamingProvider:
    """模拟 OpenAI /v1/chat/completions 的 SSE 流式响应

    行为:
    - chat_stream(prompt, model) 按 120 字符切分 prompt → yield text chunks
    - 末尾 yield 1 个 finish chunk
    - 1/10 概率随机抛 StreamError(可由 fail_prob=0 关闭)
    - 工具调用触发:若 prompt 含 "TOOL:get_weather" → 在文本前插入 1 个
      tool_call_start + 多个 tool_call_delta + 1 个 finish
    - chat(prompt) 同步返回完整文本(非流式 fallback 用)

    Args:
        fail_prob: 每次 chat_stream 调用开头随机失败的概率(0.0 ~ 1.0)
        seed: random 种子(测试时让失败可控)
    """

    def __init__(
        self, fail_prob: float = 0.1, seed: int | None = None
    ) -> None:
        if not 0.0 <= fail_prob <= 1.0:
            raise ValueError(f"fail_prob must be in [0, 1], got {fail_prob}")
        self.fail_prob = fail_prob
        self._rng = random.Random(seed)
        self._stats: dict[str, int] = {
            "n_stream_calls": 0,
            "n_stream_failures": 0,
            "n_stream_success": 0,
            "n_chat_calls": 0,
        }

    # ---- public -----------------------------------------------------------

    def chat_stream(
        self, prompt: str, model: str = ""
    ) -> AsyncIterator[StreamChunk]:
        """异步生成器:yield StreamChunk

        1. 入口判失败(1/10)→ raise StreamError
        2. 检查 prompt 是否含 "TOOL:<name>" → 触发 tool_call 流
        3. 否则按 120 字符分块 yield text chunks
        4. 最后 yield finish chunk
        """
        self._stats["n_stream_calls"] += 1

        if self._rng.random() < self.fail_prob:
            self._stats["n_stream_failures"] += 1
            raise StreamError(
                f"MockStreamingProvider: random SSE failure "
                f"(fail_prob={self.fail_prob}, prompt_len={len(prompt)})"
            )

        return self._stream_impl(prompt, model)

    async def _stream_impl(
        self, prompt: str, model: str
    ) -> AsyncIterator[StreamChunk]:
        """真实流式实现(被 chat_stream 委托)"""
        idx = 0

        # ---- 工具调用分支 ----
        if "TOOL:get_weather" in prompt:
            tc_id = "call_weather_001"
            tc_name = "get_weather"
            # tool_call_start
            yield StreamChunk(
                chunk_idx=idx,
                content="",
                delta_type="tool_call_start",
                tool_call_id=tc_id,
                tool_call_name=tc_name,
            )
            idx += 1
            await asyncio.sleep(0)

            # tool_call_delta 拆 JSON 字符串为多段
            args_str = '{"city": "Beijing", "unit": "celsius"}'
            for piece in self._split_into_pieces(args_str, size=8):
                yield StreamChunk(
                    chunk_idx=idx,
                    content="",
                    delta_type="tool_call_delta",
                    tool_call_id=tc_id,
                    tool_call_args_delta=piece,
                )
                idx += 1
                await asyncio.sleep(0)

            # finish
            yield StreamChunk(
                chunk_idx=idx,
                content="",
                delta_type="finish",
            )
            idx += 1
            self._stats["n_stream_success"] += 1
            return

        # ---- 纯文本分支 ----
        if not prompt:
            # 空 prompt → 1 个 finish chunk(不做无意义的 text 块)
            yield StreamChunk(
                chunk_idx=idx,
                content="",
                delta_type="finish",
            )
            self._stats["n_stream_success"] += 1
            return

        text_response = f"Echo: {prompt}" if not prompt.startswith("Echo:") else prompt
        pieces = self._split_into_pieces(text_response, size=CHUNK_SIZE)
        for piece in pieces:
            yield StreamChunk(
                chunk_idx=idx,
                content=piece,
                delta_type="text",
            )
            idx += 1
            await asyncio.sleep(0)

        yield StreamChunk(
            chunk_idx=idx,
            content="",
            delta_type="finish",
        )
        self._stats["n_stream_success"] += 1

    def chat(self, prompt: str) -> str:
        """非流式:同步返回完整文本(给 aggregate_with_fallback 用)

        - 含 "TOOL:get_weather" → 返回 [TOOL_CALL: ...] 标记 + 一句文本
        - 否则返回 "Echo: <prompt>"
        """
        self._stats["n_chat_calls"] += 1
        if "TOOL:get_weather" in prompt:
            return (
                '[TOOL_CALL:{"id":"call_weather_001","type":"function",'
                '"function":{"name":"get_weather","arguments":'
                '"{\\"city\\": \\"Beijing\\", \\"unit\\": \\"celsius\\"}"}}]'
            )
        if not prompt:
            return ""
        if prompt.startswith("Echo:"):
            return prompt
        return f"Echo: {prompt}"

    # ---- helpers ----------------------------------------------------------

    @staticmethod
    def _split_into_pieces(text: str, size: int) -> list[str]:
        """按 size 切分 text → list[str]"""
        size = max(size, 1)
        if not text:
            return []
        return [text[i : i + size] for i in range(0, len(text), size)]

    def stats(self) -> dict[str, int]:
        return dict(self._stats)


# =============================================================================
# delta 合并(还原完整 tool_call)
# =============================================================================


def merge_deltas(chunks: list[StreamChunk]) -> dict[str, Any]:
    """把同一 tool_call_id 的 args_delta 拼成完整 tool_call

    输入: StreamChunk 列表(可能含 text / tool_call_start / tool_call_delta / finish)
    输出: 单个 tool_call dict
        {
            "id": "call_xxx",
            "type": "function",
            "function": {"name": "get_weather", "arguments": "{...}"}
        }

    若有多个 tool_call → 仅合并第一个(aggregate_stream 内部按需循环);
    该函数处理单个 tool_call 的合并,聚合多个 tool_call 由 aggregate_stream 完成。
    """
    tc_id: str | None = None
    tc_name: str | None = None
    args_parts: list[str] = []

    for ch in chunks:
        if ch.delta_type == "tool_call_start":
            # 新的 tool_call 起点:重置
            tc_id = ch.tool_call_id
            tc_name = ch.tool_call_name
            args_parts = []
        elif ch.delta_type == "tool_call_delta":
            if ch.tool_call_args_delta:
                args_parts.append(ch.tool_call_args_delta)
        # text / finish / error 忽略

    return {
        "id": tc_id or "",
        "type": "function",
        "function": {
            "name": tc_name or "",
            "arguments": "".join(args_parts),
        },
    }


# =============================================================================
# Stream Aggregator
# =============================================================================


async def aggregate_stream(
    provider: Any, prompt: str, model: str = ""
) -> StreamResult:
    """流式聚合:跑 provider.chat_stream → 收集 + 解析

    Args:
        provider: 实现 chat_stream(prompt, model) → AsyncIterator[StreamChunk] 的对象
        prompt: 提示词
        model: 模型名(透传给 provider)

    Returns:
        StreamResult:
        - 成功:streaming_succeeded=True, full_content 拼好, tool_calls 解析
        - 失败(抛 StreamError 或任意 Exception):
            streaming_succeeded=False,
            chunks 保留已收到的(可能为空),
            finish_reason=None
    """
    result = StreamResult()
    tool_calls_map: dict[str, dict[str, Any]] = {}

    try:
        agen = provider.chat_stream(prompt, model=model)
        # 支持 async generator / async iterator
        async for chunk in agen:
            result.chunks.append(chunk)
            if chunk.delta_type == "text":
                result.full_content += chunk.content
            elif chunk.delta_type == "tool_call_start":
                if chunk.tool_call_id:
                    tool_calls_map[chunk.tool_call_id] = {
                        "id": chunk.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": chunk.tool_call_name or "",
                            "arguments": "",
                        },
                    }
            elif chunk.delta_type == "tool_call_delta":
                if chunk.tool_call_id and chunk.tool_call_id in tool_calls_map:
                    if chunk.tool_call_args_delta:
                        tool_calls_map[chunk.tool_call_id]["function"][
                            "arguments"
                        ] += chunk.tool_call_args_delta
            elif chunk.delta_type == "finish":
                # finish chunk 的 content 字段可能携带 finish_reason
                fr = (chunk.content or "").strip()
                result.finish_reason = fr or "stop"
    except StreamError as e:
        logger.warning("aggregate_stream: StreamError after %d chunks: %s",
                       len(result.chunks), e)
        result.streaming_succeeded = False
    except Exception as e:  # noqa: BLE001
        logger.warning("aggregate_stream: unexpected error after %d chunks: %s",
                       len(result.chunks), e)
        result.streaming_succeeded = False
    else:
        result.streaming_succeeded = True
    finally:
        result.total_chunks = len(result.chunks)
        # 顺序按 tool_call_id 首次出现
        seen_order: list[str] = []
        for ch in result.chunks:
            if (
                ch.delta_type == "tool_call_start"
                and ch.tool_call_id
                and ch.tool_call_id not in seen_order
            ):
                seen_order.append(ch.tool_call_id)
        result.tool_calls = [tool_calls_map[tid] for tid in seen_order]
        # 若未显式 finish chunk,finish_reason 仍为 None(失败路径)

    return result


# =============================================================================
# Stream + Non-stream Fallback
# =============================================================================


async def aggregate_with_fallback(
    provider: Any, prompt: str, model: str = ""
) -> StreamResult:
    """先流式,失败 → 一次性非流式 fallback

    行为:
    1. 先 aggregate_stream(provider, prompt, model)
    2. 若 streaming_succeeded=True 且 total_chunks > 0 → 直接返回
    3. 否则调 provider.chat(prompt) 取完整文本
       → wrap 成 1 个 text chunk + 1 个 finish chunk
       → streaming_succeeded=False, finish_reason="stop"

    注:若流式"成功但 total_chunks=0"(极端情况,空生成),也走 fallback。
    """
    stream_result = await aggregate_stream(provider, prompt, model=model)

    if stream_result.streaming_succeeded and stream_result.total_chunks > 0:
        return stream_result

    # ---- fallback to non-stream ----
    try:
        full_text = provider.chat(prompt)
    except Exception as e:  # noqa: BLE001
        logger.error(
            "aggregate_with_fallback: non-stream fallback also failed: %s", e
        )
        # 仍返回流式 partial(可能含少量 chunks)
        return stream_result

    # wrap 成 chunks
    chunks: list[StreamChunk] = []
    idx = 0
    if full_text:
        chunks.append(
            StreamChunk(
                chunk_idx=idx,
                content=full_text,
                delta_type="text",
            )
        )
        idx += 1
    chunks.append(
        StreamChunk(
            chunk_idx=idx,
            content="stop",
            delta_type="finish",
        )
    )

    return StreamResult(
        chunks=chunks,
        full_content=full_text or "",
        tool_calls=[],
        finish_reason="stop",
        total_chunks=len(chunks),
        streaming_succeeded=False,
    )
