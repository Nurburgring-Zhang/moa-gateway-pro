"""MoA (Mixture-of-Agents) orchestration endpoints."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from .._helpers import mock_headers
from ..auth import require_admin, require_api_key
from ..moa import get_moa
from ..observability.tracer import get_tracer
from ..providers.base import ProviderError
from ..ratelimit import get_limiter
from ..req_models import (
    CreateMoaBenchmarkRequest,
    CreateMoaCostParetoRequest,
    CreateMoaEvalRequest,
    CreateMoaFlaskRequest,
    CreateMoaSimilarityRequest,
    UpdateMoaPromptsNameRequest,
)
from ..router import get_router
from .chat import ChatCompletionRequest

logger = logging.getLogger(__name__)

router = APIRouter(tags=["moa"])


@router.post("/v1/moa/execute")
async def moa_execute(
    req: ChatCompletionRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """Native MoA endpoint (returns full result, not OpenAI format)"""

    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        raise
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    if not messages:
        raise HTTPException(400, "messages is required")
    moa = get_moa()
    # T5.1: orchestration span under the request trace.
    with get_tracer().start_span("moa.execute", {
        "moa.preset": req.preset or "default",
        "moa.strategy": req.strategy or "",
    }) as span:
        try:
            result = await moa.execute(
                query=messages[-1].get("content", ""),
                context=messages[:-1] if len(messages) > 1 else None,
                tools=req.tools,
                preset=req.preset,
                strategy=req.strategy,
                reference_count=req.reference_count,
                critic_rounds=req.critic_rounds,
                temperature=req.temperature or 0.6,
                max_tokens=req.max_tokens or 4096,
                max_tool_rounds=req.max_tool_rounds,
                caller_role=key_info.get("role") or "readonly",
            )
        except ValueError as e:
            raise HTTPException(400, f"Invalid input: {e}") from e
        except ProviderError as e:
            # v3.1.1 audit P2-5: keep provider status semantics (502 for
            # all-references-failed, 503 for mock-disabled, 504 timeout)
            # instead of flattening everything to 500.
            raise HTTPException(e.status or 502, f"MoA execution failed: {e}") from e
        except (ConnectionError, TimeoutError) as e:
            raise HTTPException(503, f"Upstream service unavailable: {e}") from e
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("moa execute failed: %s", e)
            raise HTTPException(500, "Internal server error") from e
        span.set_attribute("moa.references", len(result.references))
    approx = sum(len(m.get("content", "")) // 3 for m in messages) + len(result.final_content) // 3
    limiter.incr_tokens(key_info, approx)
    # v3.1.1 audit P1-7: label synthetic MoA output explicitly (D6 policy).
    return JSONResponse(
        content=result.to_dict(),
        headers=mock_headers(result.mock_used),
    )


@router.post("/v1/moa/eval")
async def moa_eval(body: CreateMoaEvalRequest, key_info: dict[str, Any] = Depends(require_api_key)):
    """Compare N model answers side-by-side"""
    query = (body.get("query") or "").strip()
    candidates = body.get("candidates") or []
    reference = body.get("reference_answer")
    if not query:
        raise HTTPException(400, "query is required")
    if not candidates or not isinstance(candidates, list):
        raise HTTPException(400, "candidates must be a non-empty list")
    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        raise
    moa = get_moa()
    try:
        res = await moa.evaluate(
            query,
            candidates,
            reference_answer=reference,
            temperature=float(body.get("temperature") or 0.3),
        )
    except ValueError as e:
        raise HTTPException(400, f"Invalid input: {e}") from e
    except (ConnectionError, TimeoutError) as e:
        raise HTTPException(503, f"Upstream service unavailable: {e}") from e
    except Exception as e:
        logger.exception("moa eval failed: %s", e)
        raise HTTPException(500, "Internal server error") from e
    approx = (
        sum(len(c.get("answer", c.get("error", ""))) // 3 for c in res["candidates"])
        + len(res["scores_raw"]) // 3
        + 500
    )
    limiter.incr_tokens(key_info, approx)
    return res


@router.post("/v1/moa/similarity")
async def moa_similarity(
    body: CreateMoaSimilarityRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """Compute multi-dimensional similarity between two candidate answers"""
    from ..moa import get_moa

    # v3.1.1 audit P1-18: this endpoint drives a judge LLM call — it must
    # respect per-key RPM and token quotas like every other LLM endpoint.
    limiter = get_limiter()
    limiter.check_and_incr(key_info)

    query = body.get("query", "")
    a = body.get("candidate_a", "")
    b = body.get("candidate_b", "")
    if not a or not b:
        raise HTTPException(400, "candidate_a and candidate_b required")
    moa = get_moa()
    result = await moa.compute_similarity(query, a, b, body.get("model_id"))
    limiter.incr_tokens(key_info, (len(query) + len(a) + len(b)) // 3)
    return result


@router.post("/v1/moa/flask")
async def moa_flask(
    body: CreateMoaFlaskRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """FLASK 12-dim scoring"""
    from ..moa import get_moa

    # v3.1.1 audit P1-18: FLASK scoring always invokes a judge LLM.
    limiter = get_limiter()
    limiter.check_and_incr(key_info)

    query = body.get("query", "")
    response = body.get("response", "")
    if not query or not response:
        raise HTTPException(400, "query and response required")
    moa = get_moa()
    result = await moa.flask_score(
        query, response, reference=body.get("reference"), judge_model=body.get("judge_model")
    )
    limiter.incr_tokens(key_info, (len(query) + len(response)) // 3)
    return result


@router.post("/v1/moa/benchmark")
async def moa_benchmark(
    body: CreateMoaBenchmarkRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """Built-in Benchmark Suite"""
    from ..benchmark import BENCHMARK_PROMPTS, run_benchmark

    # v3.1.1 audit P1-18: benchmark fires preset×prompt batches of LLM calls.
    limiter = get_limiter()
    limiter.check_and_incr(key_info)

    category = body.get("category", "all")
    limit = int(body.get("limit", 5))
    presets = body.get("presets") or ["balanced", "chinese_battalion"]
    if category == "all":
        prompts = BENCHMARK_PROMPTS
    else:
        prompts = [p for p in BENCHMARK_PROMPTS if p["category"] == category]
    prompts = prompts[:limit]

    results = {}
    summary = {}
    for preset_name in presets:
        try:
            r = await run_benchmark(preset_name, prompts)
            results[preset_name] = r["items"]
            items = r["items"]
            valid_latency = [
                i.get("latency_ms") for i in items if isinstance(i.get("latency_ms"), (int, float))
            ]
            valid_flask = [
                i.get("flask_avg") for i in items if isinstance(i.get("flask_avg"), (int, float))
            ]
            summary[preset_name] = {
                "total_questions": len(items),
                "total_cost": r["total_cost"],
                "avg_latency_ms": (sum(valid_latency) / max(1, len(valid_latency))),
                "avg_flask_score": (sum(valid_flask) / max(1, len(valid_flask))),
                "success_rate": (sum(1 for i in items if i.get("success")) / max(1, len(items))),
            }
        except Exception as e:
            results[preset_name] = {"error": str(e)}
            summary[preset_name] = {"error": str(e)}

    limiter.incr_tokens(
        key_info,
        sum(len(p["text"]) for p in prompts) * max(1, len(presets)) // 3,
    )
    return {
        "categories": sorted(set(p["category"] for p in BENCHMARK_PROMPTS)),
        "prompts_count": len(BENCHMARK_PROMPTS),
        "tested_prompts": len(prompts),
        "prompts": [{"id": p["id"], "category": p["category"], "text": p["text"]} for p in prompts],
        "results": results,
        "summary": summary,
    }


@router.post("/v1/moa/cost-pareto")
async def moa_cost_pareto(
    body: CreateMoaCostParetoRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """Cost Pareto Analysis"""
    from ..benchmark import run_pareto

    # v3.1.1 audit P1-18: cost-pareto runs presets×prompts full MoA executions.
    limiter = get_limiter()
    limiter.check_and_incr(key_info)

    prompts = body.get("prompts", [])
    presets = body.get("presets", ["fast", "balanced", "quality"])
    if not prompts:
        raise HTTPException(400, "prompts required (>= 3)")
    result = await run_pareto(prompts, presets)
    limiter.incr_tokens(
        key_info,
        sum(len(str(p)) for p in prompts) * max(1, len(presets)) // 3,
    )
    return result


@router.post("/v1/moa/tri-review")
async def moa_tri_review(
    req: ChatCompletionRequest, key_info: dict[str, Any] = Depends(require_api_key)
):
    """三模型互审互查互检: Qwen3.8 + Kimi-K3 + GLM-5.2

    使用 tri_model_review 预设:
    1. 三模型并行提议各自答案
    2. GLM-5.2 聚合综合答案
    3. 三轮交叉审查(consensus < threshold 时自动追加)

    返回: 三模型各自提议 + 聚合结果 + 审查意见 + consensus_score
    """
    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        raise
    messages = [m.model_dump(exclude_none=True) for m in req.messages]
    if not messages:
        raise HTTPException(400, "messages is required")
    moa = get_moa()
    # T5.1: tri-review orchestration span under the request trace.
    with get_tracer().start_span("moa.tri_review", {"moa.preset": "tri_model_review"}):
        try:
            result = await moa.execute(
                query=messages[-1].get("content", ""),
                context=messages[:-1] if len(messages) > 1 else None,
                preset="tri_model_review",
                critic_rounds=3,
            )
        except Exception as e:
            logger.exception("tri-review failed: %s", e)
            raise HTTPException(500, str(e)) from e
    # D9 配额对齐: 记账本次互审实际消耗 (输入 + 参考模型真实 tokens + 审查意见 + 聚合输出)
    # 注: 聚合器/critic 的 prompt 侧消耗未单独记录, 此处为保守估算
    ref_tokens = sum(r.prompt_tokens + r.completion_tokens for r in result.references)
    critic_chars = sum(
        len(c.refined_content) + len("".join(c.issues)) + len("".join(c.suggestions))
        for c in result.critics
    )
    approx = (
        sum(len(m.get("content", "")) // 3 for m in messages)
        + ref_tokens
        + critic_chars // 3
        + len(result.final_content or result.aggregated_content) // 3
    )
    # 互审已完成(上游费用已发生), 记账失败不得吞掉结果; 仅告警交由后台对账
    try:
        limiter.incr_tokens(key_info, approx)
    except HTTPException as e:
        logger.warning(
            "tri-review quota accounting rejected after execution (key=%s, tokens=%d): %s",
            key_info.get("key_id", "?"), approx, e.detail,
        )
    return result.to_dict()


@router.get("/v1/moa/presets")
async def list_moa_presets(_: dict[str, Any] = Depends(require_api_key)):
    """List all available presets (full config) — for frontend UI"""
    from ..config import get_settings

    s = get_settings()
    out = []
    for name, cfg in s.moa.presets.items():
        out.append(
            {
                "name": name,
                "strategy": cfg.strategy,
                "description": cfg.description,
                "reference_count": cfg.reference_count,
                "aggregator": cfg.aggregator or None,
                "aggregator_tier": cfg.aggregator_tier,
                "critic_rounds": cfg.critic_rounds,
                "reference_temperature": cfg.reference_temperature,
                "aggregator_temperature": cfg.aggregator_temperature,
                "layer_count": cfg.layer_count,
                "stages": [{"name": s.name, "tier": s.tier} for s in cfg.stages]
                if cfg.stages
                else None,
                "reference_models": [
                    {"id": r.id, "role": r.role, "provider": r.provider, "model": r.model}
                    for r in cfg.reference_models
                ]
                if cfg.reference_models
                else None,
            }
        )
    return {"presets": out, "default": s.moa.default_preset}


@router.get("/v1/moa/prompts")
async def list_moa_prompts(_: dict[str, Any] = Depends(require_api_key)):
    """List all MoA prompt templates"""
    from ..prompts import PLACEHOLDERS, list_templates

    templates = list_templates()
    return {
        "templates": templates,
        "placeholders": PLACEHOLDERS,
        "total": len(templates),
    }


@router.get("/v1/moa/prompts/{name}")
async def get_moa_prompt(name: str, _: dict[str, Any] = Depends(require_api_key)):
    """Read a single prompt template"""
    from ..prompts import get_prompt

    if not name or not all(c.isalnum() or c in "_-" for c in name):
        raise HTTPException(status_code=400, detail="invalid prompt name")
    try:
        content = get_prompt(name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"name": name, "content": content}


@router.put("/v1/moa/prompts/{name}")
async def save_moa_prompt(
    name: str,
    body: UpdateMoaPromptsNameRequest,
    admin: dict[str, Any] = Depends(require_admin),
):
    """Save/overwrite a prompt template.

    v3.1.1 audit P1-4 fix: admin-only. Templates are consumed by every
    tenant's MoA aggregation/critic flows — letting any API key write them
    was a persistent cross-tenant prompt-injection vector.
    """
    if not name or not all(c.isalnum() or c in "_-" for c in name):
        raise HTTPException(status_code=400, detail="invalid prompt name")
    content = body.content
    from ..prompts import save_template

    path = save_template(name, content)
    logger.info("prompt saved by %s: %s (%d bytes)", admin.get("sub"), name, len(content))
    return {"name": name, "path": str(path), "size": len(content), "saved": True}


@router.delete("/v1/moa/prompts/{name}")
async def delete_moa_prompt(
    name: str, admin: dict[str, Any] = Depends(require_admin)
):
    """Delete a user-customized template (restore to system default).

    v3.1.1 audit P1-4 fix: admin-only (template write surface).
    """
    if not name or not all(c.isalnum() or c in "_-" for c in name):
        raise HTTPException(status_code=400, detail="invalid prompt name")
    from ..prompts import delete_template

    deleted = delete_template(name)
    if not deleted:
        raise HTTPException(status_code=404, detail="no user-customized template to delete")
    logger.info("prompt deleted by %s: %s", admin.get("sub"), name)
    return {"name": name, "deleted": True}


@router.get("/v1/route/preview")
async def route_preview(q: str, key_info: dict[str, Any] = Depends(require_api_key)):
    """Preview routing decision (debug)"""
    router_inst = get_router()
    d = router_inst.route(q)
    return d.to_dict()


@router.get("/v1/quota")
async def quota(key_info: dict[str, Any] = Depends(require_api_key)):
    return get_limiter().get_quota(key_info)
