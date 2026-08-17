"""Shared helpers used across route modules.

Extracted from server.py to enable route modularization.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def mock_headers(mock_used: bool) -> dict[str, str]:
    """D6 explicit-mock: response headers labeling mock-generated output.

    Returns {settings.mock.header_name: "true"} when *mock_used* else {}.
    Clients MUST treat labeled responses as simulated (no real model call).
    """
    if not mock_used:
        return {}
    try:
        from .config import get_settings

        return {get_settings().mock.header_name: "true"}
    except Exception:
        return {"X-MOA-Mock": "true"}


def err_500(e: Exception, action: str) -> HTTPException:
    """Smart 500 wrapper: input errors -> 4xx, real server errors -> 500.

    Round-1 fix: don't wrap everything as 500. TypeError/KeyError/AttributeError
    are user input errors and should be 4xx so clients can retry/fix.
    """
    if isinstance(e, HTTPException):
        return e  # pass through
    if isinstance(e, (TypeError, AttributeError, IndexError, KeyError)):
        logger.warning("%s: input error: %s", action, e)
        return HTTPException(422, f"{action}: invalid input - {e}")
    if isinstance(e, ValueError):
        msg = str(e) or ""
        logger.warning("%s: business validation failed: %s", action, msg[:200])
        return HTTPException(
            400, f"{action}: {msg[:200]}" if msg else f"{action}: invalid value"
        )
    # Real server error -> 500
    logger.exception("%s: server error", action, exc_info=e)
    return HTTPException(500, f"{action}: {e}")


def format_chat_response(
    request_id: str,
    model: str,
    content: str,
    prompt_tokens: int,
    completion_tokens: int,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }
    if extra:
        body["moa_meta"] = extra
    return body


def log_request(
    key_info: dict[str, Any],
    request_id: str,
    requested: str,
    used: str,
    strategy: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost: float,
    latency_ms: float,
    status: str,
    error: str,
    preset: str | None = None,
    consensus: float | None = None,
    fallback: bool = False,
    metadata: dict | None = None,
) -> None:
    from .storage import get_storage

    try:
        get_storage().log_request(
            {
                "request_id": request_id,
                "api_key_id": key_info.get("key_id"),
                "model_requested": requested,
                "model_used": used,
                "preset": preset or "",
                "strategy": strategy,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
                "cost": cost,
                "latency_ms": latency_ms,
                "status": status,
                "error": error,
                "consensus_score": consensus,
                "fallback_used": fallback,
                "metadata": metadata or {},
            }
        )
    except Exception as e:
        logger.warning("log_request failed: %s", e)


async def stream_single(pool, model_id, messages, chat_kwargs, request_id, key_info, stream_options=None):
    """Single model streaming: produce OpenAI SSE from provider_obj.chat_stream"""
    ep = pool.endpoints.get(model_id)
    if not ep or not ep.provider_obj:
        yield "data: " + json.dumps({"error": "model unavailable"}) + "\n\n"
        yield "data: [DONE]\n\n"
        return
    # Fix P0-9: copy provider ref to avoid replacement during streaming
    provider = ep.provider_obj
    stream_kwargs = dict(chat_kwargs)
    stream_kwargs.pop("max_retries", None)
    stream_kwargs["stream"] = True
    include_usage = bool(stream_options and stream_options.get("include_usage"))
    t0 = time.monotonic()
    stream_ok = True
    completion_tokens = 0
    try:
        async for chunk in provider.chat_stream(
            pool.build_chat_request(
                ep,
                messages,
                stream_kwargs.get("temperature", 0.6),
                stream_kwargs.get("max_tokens", 4096),
                stream_kwargs.get("tools"),
                True,
            )
        ):
            if not chunk:
                continue
            # Audit fix: estimate tokens from chunk content length (~4 chars/token),
            # not "1 per chunk" — each SSE delta can carry multiple tokens.
            completion_tokens += max(1, len(chunk) // 4)
            payload = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }
                ],
            }
            yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    except Exception as e:
        stream_ok = False
        # Don't leak internal exception details to clients. Log full error
        # server-side. Audit fix: emit a TOP-LEVEL SSE error event (OpenAI's
        # mid-stream error shape) WITHOUT finish_reason:"stop" — a "stop" chunk
        # would make standard SDK clients treat the failed stream as a normal,
        # successful completion.
        logger.exception("stream_single failed (request_id=%s): %s", request_id, e)
        error_payload = {
            "error": {
                "message": "Streaming interrupted due to an upstream error.",
                "type": "upstream_error",
                "code": None,
            }
        }
        yield "data: " + json.dumps(error_payload, ensure_ascii=False) + "\n\n"
    # B3 互审 m4: streaming bypasses pool.call(), so mirror the attempt into
    # moa_llm_* here or /metrics would systematically undercount stream traffic.
    # Token counts are unavailable from raw text chunks; record duration only.
    try:
        from .model_pool import _record_llm_metrics
        from .providers import MockProvider

        _record_llm_metrics(
            ep,
            "success" if stream_ok else "error",
            time.monotonic() - t0,
            None,
            is_mock=isinstance(provider, MockProvider),
        )
    except Exception:
        logger.debug("failed to record stream metrics", exc_info=True)
    # v3.1.1 audit P1-17: streaming must consume the per-key daily token
    # quota exactly like the non-streaming path. v3.1.0 never accounted
    # stream tokens, so clients could bypass quota billing with stream=true.
    try:
        from .ratelimit import get_limiter

        _prompt_est = sum(len(str(m.get("content", ""))) // 4 for m in messages)
        get_limiter().incr_tokens(key_info, _prompt_est + completion_tokens)
    except Exception:
        logger.warning(
            "stream quota accounting failed (request_id=%s)", request_id, exc_info=True
        )
    # Final stop chunk (OpenAI spec) when the stream completed cleanly.
    if stream_ok:
        yield "data: " + json.dumps(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            ensure_ascii=False,
        ) + "\n\n"
    # Emit usage chunk if stream_options.include_usage is true (OpenAI spec)
    if include_usage and stream_ok:
        prompt_tokens = sum(len(m.get("content", "")) // 4 for m in messages)
        usage_payload = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
            "choices": [],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            },
        }
        yield "data: " + json.dumps(usage_payload, ensure_ascii=False) + "\n\n"
    yield "data: [DONE]\n\n"


async def stream_moa(result, request_id):
    """MoA streaming: split final content into character-level SSE deltas.

    NOTE: This is buffered streaming — the full MoA orchestration completes before
    the first byte is sent. Clients should check the X-MoA-Streaming: buffered header.
    """
    content = result.final_content or result.aggregated_content or ""
    model = result.aggregator_model or "moa"
    for token in _tokenize_for_stream(content):
        payload = {
            "id": request_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }
            ],
            "moa_meta": {
                "preset": result.preset,
                "consensus": result.consensus_score,
                "cost": result.total_cost,
                "references": [r.model_id for r in result.references],
            }
            if token == content[:1]
            else None,
        }
        # Only include moa_meta in the first chunk
        if payload["moa_meta"] is None:
            payload.pop("moa_meta", None)
        yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        await asyncio.sleep(0)  # yield to event loop
    yield "data: [DONE]\n\n"


async def stream_moa_progressive(
    moa_stream, model_id, request_id, stream_options=None, prompt_tokens=0, key_info=None
):
    """Progressive MoA streaming: aggregator output is streamed token-by-token as generated.

    Unlike stream_moa() which buffers the entire response, this streams directly from
    the aggregator provider via chat_stream(). TTFT = reference_time + aggregator_first_token.

    OpenAI-spec completeness (audit F33): emits a final ``finish_reason:"stop"``
    chunk and, when ``stream_options.include_usage`` is set, a trailing usage
    chunk — matching the behaviour clients expect from /v1/chat/completions.
    """
    include_usage = bool(stream_options and stream_options.get("include_usage"))
    completion_tokens = 0
    stream_ok = True
    try:
        async for chunk in moa_stream:
            if not chunk:
                continue
            # Audit fix: estimate tokens from chunk content length (~4 chars/token),
            # not "1 per chunk" — each SSE delta can carry multiple tokens.
            completion_tokens += max(1, len(chunk) // 4)
            payload = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }
                ],
            }
            yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
    except Exception as e:
        stream_ok = False
        # Audit fix: emit a top-level SSE error event WITHOUT finish_reason:"stop"
        # so SDK clients do not treat a failed stream as a successful completion.
        logger.exception("stream_moa_progressive failed (request_id=%s): %s", request_id, e)
        error_payload = {
            "error": {
                "message": "Streaming interrupted due to an internal error.",
                "type": "internal_error",
                "code": None,
            }
        }
        yield "data: " + json.dumps(error_payload, ensure_ascii=False) + "\n\n"

    # v3.1.1 audit P1-17: account streamed tokens against the daily quota.
    if key_info is not None:
        try:
            from .ratelimit import get_limiter

            get_limiter().incr_tokens(key_info, prompt_tokens + completion_tokens)
        except Exception:
            logger.warning(
                "moa stream quota accounting failed (request_id=%s)",
                request_id,
                exc_info=True,
            )

    # Final stop chunk (OpenAI spec) when the stream completed cleanly.
    if stream_ok:
        yield "data: " + json.dumps(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            ensure_ascii=False,
        ) + "\n\n"

    # Trailing usage chunk when requested.
    if include_usage and stream_ok:
        yield "data: " + json.dumps(
            {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model_id,
                "choices": [],
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
            ensure_ascii=False,
        ) + "\n\n"
    yield "data: [DONE]\n\n"


def _tokenize_for_stream(text: str):
    """Split text into streaming tokens (Chinese char-by-char, ASCII by whitespace)"""
    if not text:
        return
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if "\u4e00" <= ch <= "\u9fff":
            yield ch
            i += 1
        elif ch.isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            yield text[i:j]
            i = j
        else:
            j = i + 1
            while j < n and not text[j].isspace() and not ("\u4e00" <= text[j] <= "\u9fff"):
                j += 1
            yield text[i:j]
            i = j
