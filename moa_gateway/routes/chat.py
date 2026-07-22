"""Core /v1/chat/completions endpoint — OpenAI compatible."""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Union

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .._helpers import format_chat_response, log_request, stream_moa, stream_single
from ..auth import require_api_key
from ..cache.manager import get_cache_manager
from ..moa import MoAResult, get_moa
from ..model_pool import get_model_pool
from ..observability import Metrics, record_rate_limit_block
from ..observability import record_chat as _prom_record_chat
from ..ratelimit import get_limiter
from ..router import get_router

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


# ========== Pydantic Schemas ==========
class ChatMessage(BaseModel):
    role: str = Field(..., max_length=64)
    content: str | None = Field(default=None, max_length=200_000)
    name: str | None = Field(default=None, max_length=128)
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = Field(default=None, max_length=128)


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="auto", max_length=128)
    messages: list[ChatMessage] = Field(..., max_length=200)
    temperature: float | None = Field(default=0.6, ge=0.0, le=2.0)
    max_tokens: int | None = Field(default=4096, ge=1, le=32000)
    top_p: float | None = Field(default=1.0, ge=0.0, le=1.0)
    stream: bool | None = False
    stop: Union[str, list[str]] | None = Field(default=None, max_length=64)
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=64)
    tool_choice: Any | None = None
    # Extension fields
    preset: str | None = Field(default=None, max_length=32)
    strategy: str | None = Field(default=None, max_length=32)
    reference_count: int | None = Field(default=None, ge=1, le=8)
    critic_rounds: int | None = Field(default=None, ge=0, le=5)
    # More OpenAI pass-through fields
    n: int | None = Field(default=1, ge=1, le=8)
    presence_penalty: float | None = Field(default=0.0, ge=-2.0, le=2.0)
    frequency_penalty: float | None = Field(default=0.0, ge=-2.0, le=2.0)
    seed: int | None = None
    user: str | None = Field(default=None, max_length=128)
    response_format: dict[str, Any] | None = None
    logit_bias: dict[str, int] | None = None


@router.post("/v1/chat/completions")
async def chat_completions(
    req: ChatCompletionRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """OpenAI compatible: chat completions (non-streaming + streaming)"""
    pool = get_model_pool()
    metrics = Metrics.instance()
    metrics.incr("chat_requests")
    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        metrics.incr("ratelimit_blocked")
        try:
            record_rate_limit_block("rpm")
        except Exception as e:
            logger.warning("Prometheus rate_limit_block recording failed: %s", e)
        raise

    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    if not messages:
        raise HTTPException(400, "messages is required")

    # Determine mode
    model_id = (req.model or "auto").strip()

    # Parse preset aliases
    preset = req.preset
    strategy = req.strategy
    reference_count = req.reference_count
    critic_rounds = req.critic_rounds

    preset_aliases = {
        "auto": None,
        "fast": "fast",
        "balanced": "balanced",
        "quality": "quality",
        "moa-balanced": "balanced",
        "moa-quality": "quality",
        "pipeline": "pipeline",
    }
    is_moa_alias = model_id in (
        "fast",
        "balanced",
        "quality",
        "moa-balanced",
        "moa-quality",
        "pipeline",
    )
    is_auto = model_id == "auto"
    if is_auto:
        preset = None
    elif is_moa_alias and preset is None:
        preset = preset_aliases[model_id]
        if model_id == "pipeline":
            strategy = "pipeline"

    temperature = req.temperature if req.temperature is not None else 0.6
    max_tokens = req.max_tokens or 4096

    request_id = "chatcmpl-" + uuid.uuid4().hex[:12]
    t0 = time.time()
    metrics.incr("chat_started")

    # Pass-through OpenAI fields for model_pool.call
    chat_kwargs = dict(
        temperature=temperature,
        max_tokens=max_tokens,
        tools=req.tools,
        max_retries=3,
    )

    # ============ Cache Lookup ============
    cache_mgr = get_cache_manager()
    if not req.stream and cache_mgr.enabled:
        cache_result = await cache_mgr.get(
            messages, model_id, temperature=temperature, max_tokens=max_tokens
        )
        if cache_result:
            cached_resp = cache_result["response"]
            layer = cache_result["layer"]
            similarity = cache_result.get("similarity", 1.0)
            metrics.incr("cache_hit")
            # Return cached response with cache headers
            resp_body = cached_resp if isinstance(cached_resp, dict) else {"content": cached_resp}
            return JSONResponse(
                content=resp_body,
                headers={
                    "X-Cache": "HIT",
                    "X-Cache-Layer": layer,
                    "X-Cache-Similarity": str(round(similarity, 4)),
                },
            )

    # ============ Single model branch (auto or specific endpoint_id) ============
    if is_auto or (not is_moa_alias and (not preset or preset == "fast")):
        if is_auto or model_id not in pool.endpoints:
            router_inst = get_router()
            decision = router_inst.route(messages[-1].get("content", ""))
            if not decision.primary:
                raise HTTPException(503, "no available model")
            model_id = decision.primary.id
            if model_id not in pool.endpoints:
                raise HTTPException(503, "no available model (endpoint just removed)")
        # Streaming?
        if req.stream:
            return StreamingResponse(
                stream_single(pool, model_id, messages, chat_kwargs, request_id, key_info),
                media_type="text/event-stream",
            )
        try:
            resp = await pool.call(
                model_id,
                messages,
                stream=False,
                **chat_kwargs,
            )
        except Exception as e:
            metrics.error("chat_failed")
            logger.exception("chat failed: %s", e)
            raise HTTPException(502, f"model call failed: {e}")

        limiter.incr_tokens(key_info, resp.prompt_tokens + resp.completion_tokens)
        latency = (time.time() - t0) * 1000
        metrics.observe("chat_latency_ms", latency)
        metrics.incr("chat_completed")
        try:
            _prom_record_chat(model_id, 200, (time.time() - t0))
        except Exception as e:
            logger.warning("Prometheus recording failed: %s", e)
        log_request(
            key_info,
            request_id,
            model_id,
            model_id,
            "single",
            resp.prompt_tokens,
            resp.completion_tokens,
            resp.cost,
            latency,
            "ok",
            "",
        )
        result_body = format_chat_response(
            request_id, model_id, resp.content, resp.prompt_tokens, resp.completion_tokens
        )
        # Store in cache (non-streaming only)
        if cache_mgr.enabled and not req.stream:
            await cache_mgr.set(
                messages, model_id, result_body,
                temperature=temperature, max_tokens=max_tokens,
            )
        return JSONResponse(
            content=result_body,
            headers={"X-Cache": "MISS"},
        )

    # ============ MoA orchestration ============
    moa = get_moa()
    try:
        result: MoAResult = await moa.execute(
            query=messages[-1].get("content", ""),
            context=messages[:-1] if len(messages) > 1 else None,
            tools=req.tools,
            preset=preset,
            strategy=strategy,
            reference_count=reference_count,
            critic_rounds=critic_rounds,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    except Exception as e:
        metrics.error("moa_failed")
        logger.exception("MoA failed: %s", e)
        raise HTTPException(502, f"MoA failed: {e}")

    content = result.final_content or result.aggregated_content
    approx_input = sum(len(m.get("content", "")) // 3 for m in messages) + sum(
        len(r.content) // 3 for r in result.references if r.success
    )
    approx_output = len(content) // 3
    limiter.incr_tokens(key_info, approx_input + approx_output)

    latency = (time.time() - t0) * 1000
    metrics.observe("moa_latency_ms", latency)
    metrics.incr("moa_completed")

    log_request(
        key_info,
        request_id,
        model_id,
        result.aggregator_model,
        result.strategy,
        approx_input,
        approx_output,
        result.total_cost,
        latency,
        "ok",
        "",
        preset=result.preset,
        consensus=result.consensus_score,
        fallback=result.fallback_used,
        metadata={
            "references": [r.model_id for r in result.references],
            "critics": [c.model_id for c in result.critics],
        },
    )
    # Streaming MoA: pseudo-stream the final content
    if req.stream:
        return StreamingResponse(
            stream_moa(result, request_id), media_type="text/event-stream"
        )
    moa_body = format_chat_response(
        request_id,
        result.aggregator_model or "moa",
        content,
        approx_input,
        approx_output,
        extra={
            "moa_preset": result.preset,
            "moa_strategy": result.strategy,
            "moa_references": [r.model_id for r in result.references],
            "moa_consensus": result.consensus_score,
            "moa_iterations": result.iterations,
            "moa_cost": result.total_cost,
        },
    )
    # Store MoA result in cache
    if cache_mgr.enabled:
        await cache_mgr.set(
            messages, model_id, moa_body,
            temperature=temperature, max_tokens=max_tokens,
        )
    return JSONResponse(
        content=moa_body,
        headers={"X-Cache": "MISS"},
    )
