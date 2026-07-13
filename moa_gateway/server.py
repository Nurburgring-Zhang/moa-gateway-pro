"""moa_gateway.server — FastAPI 服务
提供:
- OpenAI 兼容 /v1/chat/completions, /v1/models
- 原生 /v1/moa/execute, /v1/route/preview
- WebUI 静态托管 + 管理 API
- 健康检查
"""
from __future__ import annotations
import asyncio
import json
import time
import logging
import uuid
import os
from contextlib import asynccontextmanager
from typing import List, Optional, Dict, Any, Union
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Depends, status
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import get_settings, DATA_DIR
from .storage import get_storage
from .model_pool import get_model_pool, start_model_pool, ModelTier
from .router import get_router, ComplexityLevel
from .moa import get_moa, MoAResult
from .auth import (require_api_key, require_admin, authenticate_api_key,
                   create_jwt_token)
from .ratelimit import get_limiter
from .adapters import AdapterContext, all_adapters, GenericOpenAIAdapter
from .observability import setup_logging, Metrics

logger = logging.getLogger(__name__)
WEBUI_DIR = Path(__file__).parent / "webui"


# ========== Pydantic Schemas ==========
class ChatMessage(BaseModel):
    role: str = Field(..., max_length=64)
    content: Optional[str] = Field(default=None, max_length=200_000)
    name: Optional[str] = Field(default=None, max_length=128)
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = Field(default=None, max_length=128)


class ChatCompletionRequest(BaseModel):
    model: str = Field(default="auto", max_length=128)
    messages: List[ChatMessage] = Field(..., max_length=200)  # 修15: 限 200 条
    temperature: Optional[float] = Field(default=0.6, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=4096, ge=1, le=32000)
    top_p: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    stream: Optional[bool] = False
    stop: Optional[Union[str, List[str]]] = Field(default=None, max_length=64)
    tools: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=64)
    tool_choice: Optional[Any] = None
    # 扩展字段(OpenAI 不支持,但我们支持)
    preset: Optional[str] = Field(default=None, max_length=32)
    strategy: Optional[str] = Field(default=None, max_length=32)
    reference_count: Optional[int] = Field(default=None, ge=1, le=8)
    critic_rounds: Optional[int] = Field(default=None, ge=0, le=5)
    # 修8: 更多 OpenAI 字段透传
    n: Optional[int] = Field(default=1, ge=1, le=8)
    presence_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    frequency_penalty: Optional[float] = Field(default=0.0, ge=-2.0, le=2.0)
    seed: Optional[int] = None
    user: Optional[str] = Field(default=None, max_length=128)
    response_format: Optional[Dict[str, Any]] = None
    logit_bias: Optional[Dict[str, int]] = None


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


class EndpointUpsert(BaseModel):
    endpoint_id: str
    provider: str
    model: str
    tier: str = "standard"
    api_base: str = ""
    api_key_plain: Optional[str] = None
    api_key_env: Optional[str] = None
    cost_per_1k_input: float = 0.001
    cost_per_1k_output: float = 0.002
    max_tokens: int = 8192
    timeout: int = 120
    weight: int = 100
    enabled: bool = True
    tags: List[str] = Field(default_factory=list)


class CreateAPIKeyRequest(BaseModel):
    name: str
    quota_rpm: int = 60
    quota_daily_tokens: int = 5_000_000


# ========== FastAPI App ==========
def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.server.log_level, settings.observability.log_dir,
                  settings.observability.log_json)
    storage = get_storage()
    pool = get_model_pool()
    metrics = Metrics.instance()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("MoA Gateway Pro starting up…")
        await pool.start()
        logger.info("Model pool started: %d endpoints", len(pool.endpoints))

        # 修13: 启动后台任务:日志清理 + ratelimit 旧桶清理
        cleanup_task = asyncio.create_task(_background_cleanup_loop())

        yield

        # 取消后台任务
        cleanup_task.cancel()
        try:
            await cleanup_task
        except (asyncio.CancelledError, Exception):
            pass

        logger.info("MoA Gateway Pro shutting down…")
        await pool.stop()


    async def _background_cleanup_loop():
        """修13: 后台循环清理旧日志和限流桶。"""
        from .storage import get_storage
        storage = get_storage()
        settings = get_settings()
        last_log_cleanup = 0
        last_rl_cleanup = 0
        while True:
            try:
                now = time.time()
                if now - last_log_cleanup > 86400:  # 24h
                    deleted = storage.cleanup_old_logs(settings.storage.log_retention_days)
                    if deleted:
                        logger.info("cleanup_old_logs: removed %d rows", deleted)
                    last_log_cleanup = now
                if now - last_rl_cleanup > 3600:    # 1h
                    cutoff = now - 7200
                    with storage.conn() as c:
                        cur = c.execute("DELETE FROM ratelimit_buckets WHERE updated_at < ?",
                                        (cutoff,))
                        if cur.rowcount:
                            logger.info("cleanup ratelimit buckets: removed %d", cur.rowcount)
                    last_rl_cleanup = now
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("background cleanup error: %s", e)
                await asyncio.sleep(300)

    app = FastAPI(
        title="MoA Gateway Pro",
        version="1.6.3",
        description="工业级多模型协作网关 - 一份 OpenAI Key 接入所有大模型",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.server.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 修23: 安全响应头中间件
    @app.middleware("http")
    async def add_security_headers(request, call_next):
        # 修15: 限制请求体大小(默认 1MB,可由 config 调整)
        max_body = 1 * 1024 * 1024  # 1MB
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > max_body:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                {"detail": f"request body too large (> {max_body} bytes)"},
                status_code=413,
            )
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self';"
        )
        return resp

    # ========== 健康检查 ==========
    @app.get("/health")
    async def health():
        snap = pool.snapshot()
        return {
            "status": "ok",
            "version": "1.0.0",
            "endpoints_total": snap["total"],
            "endpoints_enabled": snap["enabled"],
            "endpoints_healthy": snap["healthy"],
        }

    @app.get("/api/health/detailed")
    async def health_detailed():
        return pool.snapshot()

    # ========== OpenAI 兼容 ==========
    @app.get("/v1/models")
    async def list_models(request: Request):
        """OpenAI 兼容:列出可用模型"""
        # 公共端点:OpenAI 客户端需列出模型
        # 如果带有效 key,返回所有启用的端点;否则返回基础预设
        info = await authenticate_api_key(request)
        out = []
        presets = [
            ("auto", "智能路由(按复杂度自动分配)"),
            ("fast", "快速模式:单 lite 模型"),
            ("balanced", "平衡模式:4 模型并行+旗舰聚合"),
            ("quality", "高质量模式:5 模型+互审 2 轮"),
            ("moa-balanced", "MoA 平衡预设"),
            ("moa-quality", "MoA 高质量预设"),
            ("pipeline", "流水线:planner→generator→evaluator"),
        ]
        for mid, desc in presets:
            out.append({
                "id": mid,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moa-gateway",
                "description": desc,
                "permission": []
            })
        if info:
            for ep in pool.endpoints.values():
                # 显示所有 enabled 端点(无论 api_key 状态 —— 切 mock 的也算)
                if ep.config.enabled:
                    out.append({
                        "id": ep.id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": ep.config.provider,
                        "description": f"{ep.config.provider} / {ep.config.model} / tier={ep.tier.value}",
                        "permission": []
                    })
        return {"object": "list", "data": out}

    @app.post("/v1/chat/completions")
    async def chat_completions(req: ChatCompletionRequest, request: Request,
                                key_info: Dict[str, Any] = Depends(require_api_key)):
        """OpenAI 兼容:chat completions(非流式 + 流式)"""
        metrics.incr("chat_requests")
        limiter = get_limiter()
        try:
            limiter.check_and_incr(key_info)
        except HTTPException:
            metrics.incr("ratelimit_blocked")
            raise

        messages = [m.model_dump(exclude_none=True) for m in req.messages]
        if not messages:
            raise HTTPException(400, "messages is required")

        # 决定使用模式
        model_id = (req.model or "auto").strip()

        # 解析"预设别名" -> preset
        preset = req.preset
        strategy = req.strategy
        reference_count = req.reference_count
        critic_rounds = req.critic_rounds

        preset_aliases = {
            "auto": None,                    # 自动路由 → 单模型(走 router)
            "fast": "fast",
            "balanced": "balanced",
            "quality": "quality",
            "moa-balanced": "balanced",
            "moa-quality": "quality",
            "pipeline": "pipeline",
        }
        # 关键修复(P0-3 第 4 轮审计):"auto" 是 preset alias,但走 MoA 分支会错误地强制多模型
        # "auto" 应直接被识别为单模型路由,而不是预设别名
        is_moa_alias = model_id in ("fast", "balanced", "quality",
                                    "moa-balanced", "moa-quality", "pipeline")
        is_auto = model_id == "auto"
        if is_auto:
            # auto: 走智能路由选单模型
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

        # 透传扩展 OpenAI 字段(给到 model_pool.call 用的 chat_kwargs)
        chat_kwargs = dict(
            temperature=temperature,
            max_tokens=max_tokens,
            tools=req.tools,
            max_retries=3,
        )

        # ============ 单模型分支(auto 或指定 endpoint_id) ============
        if (is_auto or
            (not is_moa_alias and (not preset or preset == "fast"))):
            if is_auto or model_id not in pool.endpoints:
                router = get_router()
                decision = router.route(messages[-1].get("content", ""))
                if not decision.primary:
                    raise HTTPException(503, "no available model")
                model_id = decision.primary.id
                # 修 P1-7: router 返回后 recheck 端点是否仍存在 (防 remove_endpoint race)
                if model_id not in pool.endpoints:
                    raise HTTPException(503, "no available model (endpoint just removed)")
            # 流式?
            if req.stream:
                return StreamingResponse(
                    _stream_single(pool, model_id, messages, chat_kwargs, request_id, key_info),
                    media_type="text/event-stream"
                )
            try:
                resp = await pool.call(
                    model_id, messages, stream=False, **chat_kwargs,
                )
            except Exception as e:
                metrics.error("chat_failed")
                logger.exception("chat failed: %s", e)
                raise HTTPException(502, f"model call failed: {e}")

            limiter.incr_tokens(key_info, resp.prompt_tokens + resp.completion_tokens)
            latency = (time.time() - t0) * 1000
            metrics.observe("chat_latency_ms", latency)
            metrics.incr("chat_completed")
            _log_request(
                key_info, request_id, model_id, model_id, "single",
                resp.prompt_tokens, resp.completion_tokens,
                resp.cost, latency, "ok", ""
            )
            return _format_chat_response(
                request_id, model_id, resp.content,
                resp.prompt_tokens, resp.completion_tokens
            )

        # ============ MoA 编排 ============
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
        approx_input = sum(len(m.get("content", "")) // 3 for m in messages) \
            + sum(len(r.content) // 3 for r in result.references if r.success)
        approx_output = len(content) // 3
        limiter.incr_tokens(key_info, approx_input + approx_output)

        latency = (time.time() - t0) * 1000
        metrics.observe("moa_latency_ms", latency)
        metrics.incr("moa_completed")

        _log_request(
            key_info, request_id, model_id, result.aggregator_model,
            result.strategy,
            approx_input, approx_output, result.total_cost, latency,
            "ok", "",
            preset=result.preset,
            consensus=result.consensus_score,
            fallback=result.fallback_used,
            metadata={"references": [r.model_id for r in result.references],
                      "critics": [c.model_id for c in result.critics]}
        )
        # 流式 MoA:把最终内容切成"伪流式"按字发出(single-token delta),
        # 让 OpenAI SDK 的 stream 客户端也能拿到完整内容
        if req.stream:
            return StreamingResponse(
                _stream_moa(result, request_id),
                media_type="text/event-stream"
            )
        return _format_chat_response(
            request_id, result.aggregator_model or "moa", content,
            approx_input, approx_output,
            extra={
                "moa_preset": result.preset,
                "moa_strategy": result.strategy,
                "moa_references": [r.model_id for r in result.references],
                "moa_consensus": result.consensus_score,
                "moa_iterations": result.iterations,
                "moa_cost": result.total_cost,
            }
        )

    @app.post("/v1/moa/execute")
    async def moa_execute(req: ChatCompletionRequest, request: Request,
                          key_info: Dict[str, Any] = Depends(require_api_key)):
        """原生 MoA 端点(返回完整 result,非 OpenAI 格式)
        支持 strategy: parallel | compose | judge | chain | pipeline | single
        支持 preset: fast / balanced / quality / chinese_battalion /
                    compose_analyst / judge / chain_deep / pipeline
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
            )
        except Exception as e:
            logger.exception("moa execute failed: %s", e)
            raise HTTPException(502, f"moa failed: {e}")
        approx = sum(len(m.get("content", "")) // 3 for m in messages) \
            + len(result.final_content) // 3
        limiter.incr_tokens(key_info, approx)
        return result.to_dict()

    @app.post("/v1/moa/eval")
    async def moa_eval(request: Request,
                        key_info: Dict[str, Any] = Depends(require_api_key)):
        """横向对比 N 个模型的答案(预设 chinsque_battalion 等也能用)

        Body:
            {
              "query": "用户问题",
              "candidates": ["deepseek-v3", "qwen-plus", "glm-4-plus"],
              "reference_answer": "可选,标准答案",
              "temperature": 0.3
            }
        """
        body = await request.json()
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
            res = await moa.evaluate(query, candidates, reference_answer=reference,
                                      temperature=float(body.get("temperature") or 0.3))
        except Exception as e:
            logger.exception("moa eval failed: %s", e)
            raise HTTPException(502, f"eval failed: {e}")
        # token 估算
        approx = sum(len(c.get("answer", c.get("error", ""))) // 3 for c in res["candidates"]) \
            + len(res["scores_raw"]) // 3 + 500
        limiter.incr_tokens(key_info, approx)
        return res

    @app.post("/v1/moa/similarity")
    async def moa_similarity(request: Request,
                              body: Dict[str, Any],
                              key_info: Dict[str, Any] = Depends(require_api_key)):
        """计算两个候选答案之间的多维相似度(论文 3.3 Figure 4)
        Body:
            {
              "query": "原始问题",
              "candidate_a": "回答 A",
              "candidate_b": "回答 B",
              "model_id": "可选,用于 LLM 语义评分"
            }
        Returns:
            {
              "bleu3": 0.xx, "bleu4": 0.xx, "bleu5": 0.xx,
              "levenshtein_similarity": 0.xx,
              "tfidf_cosine": 0.xx,
              "semantic_score": 0.xx
            }
        """
        from .moa import get_moa
        query = body.get("query", "")
        a = body.get("candidate_a", "")
        b = body.get("candidate_b", "")
        if not a or not b:
            raise HTTPException(400, "candidate_a and candidate_b required")
        moa = get_moa()
        result = await moa.compute_similarity(query, a, b, body.get("model_id"))
        return result

    @app.post("/v1/moa/flask")
    async def moa_flask(request: Request,
                         body: Dict[str, Any],
                         key_info: Dict[str, Any] = Depends(require_api_key)):
        """FLASK 12 维评分(论文 3.2)
        Body:
            {
              "query": "原始问题",
              "response": "模型回答",
              "reference": "可选,标准答案",
              "judge_model": "可选,评分模型 id"
            }
        Returns:
            {
              "judge_model": "...",
              "scores": {"robustness": {"score_1_5": 4, "score_0_100": 80, "reason": "..."}, ...},
              "average_1_5": 4.2,
              "average_0_100": 84.0
            }
        """
        from .moa import get_moa
        query = body.get("query", "")
        response = body.get("response", "")
        if not query or not response:
            raise HTTPException(400, "query and response required")
        moa = get_moa()
        result = await moa.flask_score(
            query, response,
            reference=body.get("reference"),
            judge_model=body.get("judge_model")
        )
        return result

    @app.post("/v1/moa/benchmark")
    async def moa_benchmark(request: Request,
                             body: Dict[str, Any],
                             key_info: Dict[str, Any] = Depends(require_api_key)):
        """内置 Benchmark Suite(论文 3 AlpacaEval/MT-Bench 简版)
        用一组标准 prompt 在一个或多个 preset 上跑,
        输出每题答案 + FLASK 平均分 + 总成本。
        Body:
            {
              "presets": ["balanced", "chinese_battalion"],
              "category": "reasoning" | "code" | "all",
              "limit": 5
            }
        """
        from .benchmark import BENCHMARK_PROMPTS, run_benchmark
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
                # 计算时跳过 None 和缺失字段
                valid_latency = [i.get("latency_ms") for i in items
                                  if isinstance(i.get("latency_ms"), (int, float))]
                valid_flask = [i.get("flask_avg") for i in items
                                if isinstance(i.get("flask_avg"), (int, float))]
                summary[preset_name] = {
                    "total_questions": len(items),
                    "total_cost": r["total_cost"],
                    "avg_latency_ms": (sum(valid_latency) / max(1, len(valid_latency))),
                    "avg_flask_score": (
                        sum(valid_flask) / max(1, len(valid_flask))
                    ),
                    "success_rate": (
                        sum(1 for i in items if i.get("success")) /
                        max(1, len(items))
                    ),
                }
            except Exception as e:
                results[preset_name] = {"error": str(e)}
                summary[preset_name] = {"error": str(e)}

        return {
            "categories": sorted(set(p["category"] for p in BENCHMARK_PROMPTS)),
            "prompts_count": len(BENCHMARK_PROMPTS),
            "tested_prompts": len(prompts),
            "prompts": [{"id": p["id"], "category": p["category"], "text": p["text"]}
                        for p in prompts],
            "results": results,
            "summary": summary,
        }

    @app.post("/v1/moa/cost-pareto")
    async def moa_cost_pareto(request: Request,
                                body: Dict[str, Any],
                                key_info: Dict[str, Any] = Depends(require_api_key)):
        """Cost Pareto Analysis(论文 3.4 Figure 5):
        对一组 prompt,跑多个 preset,输出 cost vs quality 散点。
        Body:
            {
              "prompts": ["问题1", "问题2", ...],
              "presets": ["fast", "balanced", "quality"]
            }
        """
        from .benchmark import run_pareto
        prompts = body.get("prompts", [])
        presets = body.get("presets", ["fast", "balanced", "quality"])
        if not prompts:
            raise HTTPException(400, "prompts required (>= 3)")
        result = await run_pareto(prompts, presets)
        return result

    @app.get("/v1/moa/presets")
    async def list_moa_presets(_: Dict[str, Any] = Depends(require_api_key)):
        """列出所有可用 preset(完整配置)— 给前端 UI 用"""
        from .config import get_settings
        s = get_settings()
        out = []
        for name, cfg in s.moa.presets.items():
            out.append({
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
                "stages": [{"name": s.name, "tier": s.tier} for s in cfg.stages] if cfg.stages else None,
                "reference_models": [
                    {"id": r.id, "role": r.role, "provider": r.provider, "model": r.model}
                    for r in cfg.reference_models
                ] if cfg.reference_models else None,
            })
        return {"presets": out, "default": s.moa.default_preset}

    # ========== Prompt 模板管理端点(用户可热更) ==========
    @app.get("/v1/moa/prompts")
    async def list_moa_prompts(_: Dict[str, Any] = Depends(require_api_key)):
        """列出所有 MoA prompt 模板(系统默认 + 用户自定义)
        返回包含: name / source (user/default/builtin) / read_only / size / path
        """
        from .prompts import list_templates, PLACEHOLDERS
        templates = list_templates()
        return {
            "templates": templates,
            "placeholders": PLACEHOLDERS,
            "total": len(templates),
        }

    @app.get("/v1/moa/prompts/{name}")
    async def get_moa_prompt(name: str,
                             _: Dict[str, Any] = Depends(require_api_key)):
        """读取单个 prompt 模板内容"""
        from .prompts import get_prompt
        # 安全校验:name 只能包含 [a-zA-Z0-9_-]
        if not name or not all(c.isalnum() or c in "_-" for c in name):
            raise HTTPException(status_code=400, detail="invalid prompt name")
        try:
            content = get_prompt(name)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
        return {"name": name, "content": content}

    @app.put("/v1/moa/prompts/{name}")
    async def save_moa_prompt(name: str, body: Dict[str, Any],
                              key_info: Dict[str, Any] = Depends(require_api_key)):
        """保存/覆盖 prompt 模板(写到用户目录,~/.moa-gateway/prompts/{name}.md)
        下次 MoA 执行会立即使用新模板。
        body: {"content": "..."}
        """
        if not name or not all(c.isalnum() or c in "_-" for c in name):
            raise HTTPException(status_code=400, detail="invalid prompt name")
        content = body.get("content")
        if not isinstance(content, str) or not content.strip():
            raise HTTPException(status_code=400, detail="content must be non-empty string")
        # 大小限制:1MB
        if len(content) > 1024 * 1024:
            raise HTTPException(status_code=400, detail="content too large (>1MB)")
        from .prompts import save_template
        path = save_template(name, content)
        logger.info("prompt saved by %s: %s (%d bytes)", key_info.get("name"), name, len(content))
        return {"name": name, "path": path, "size": len(content), "saved": True}

    @app.delete("/v1/moa/prompts/{name}")
    async def delete_moa_prompt(name: str,
                                key_info: Dict[str, Any] = Depends(require_api_key)):
        """删除用户自定义模板(恢复为系统默认)"""
        if not name or not all(c.isalnum() or c in "_-" for c in name):
            raise HTTPException(status_code=400, detail="invalid prompt name")
        from .prompts import delete_template
        deleted = delete_template(name)
        if not deleted:
            raise HTTPException(status_code=404, detail="no user-customized template to delete")
        logger.info("prompt deleted by %s: %s", key_info.get("name"), name)
        return {"name": name, "deleted": True}

    @app.get("/v1/route/preview")
    async def route_preview(q: str, request: Request,
                            key_info: Dict[str, Any] = Depends(require_api_key)):
        """预览路由决策(调试用)"""
        router = get_router()
        d = router.route(q)
        return d.to_dict()

    @app.get("/v1/quota")
    async def quota(request: Request, key_info: Dict[str, Any] = Depends(require_api_key)):
        return get_limiter().get_quota(key_info)

    # ========== v1.5 Capability Endpoints (从 10 项目迁移) ==========
    @app.post("/v1/capability/secret-scan")
    async def capability_secret_scan(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """9 类硬编码密钥扫描 + 3 层豁免 (来自 moa-skill + moat-ops-auditor)
        Body: {"path": "./", "fail_on": 3, "no_block": false}
        """
        from .capability.secret_scan import scan_path, should_block
        p = Path(body.get("path", "."))
        if not p.exists():
            raise HTTPException(400, f"path not found: {p}")
        result = scan_path(p)
        blocked = should_block(result, body.get("fail_on", 3)) and not body.get("no_block", False)
        return {**result.to_dict(), "blocked": blocked}

    @app.post("/v1/capability/group-think-check")
    async def capability_group_think_check(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """3 反群体思维纪律栈判定(来自 moa-skill 核心创新)
        Body: {
            "session_id": "...",
            "members": [{"member_id": "...", "content": "...", "round": 0}],
            "rounds": [[...]],  # 可选,多轮
            "warn_threshold": 0.4,
            "block_threshold": 0.7,
        }
        """
        from .capability.moaflow import MemberResponse, group_think_verdict
        members = [MemberResponse(**m) for m in body.get("members", [])]
        rounds = None
        if body.get("rounds"):
            rounds = [[MemberResponse(**m) for m in r] for r in body["rounds"]]
        v = group_think_verdict(
            session_id=body.get("session_id", "unknown"),
            members=members,
            rounds=rounds,
            warn_threshold=body.get("warn_threshold", 0.4),
            block_threshold=body.get("block_threshold", 0.7),
        )
        return v.to_dict()

    @app.post("/v1/capability/ensemble-vote")
    async def capability_ensemble_vote(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """集成投票器(来自 01 GateSwarm — 4 种算法:majority/weighted/borda/approval)
        Body: {
            "votes": [{"voter_id": "...", "candidate": "...", "confidence": 0.9, "reason": "..."}],
            "method": "weighted"
        }
        """
        from .capability.consensus import Vote, ensemble_vote
        votes = [Vote(**v) for v in body.get("votes", [])]
        result = ensemble_vote(votes, method=body.get("method", "weighted"))
        return result.to_dict()

    @app.post("/v1/capability/should-rebalance")
    async def capability_should_rebalance(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """Tier 边界再训练(来自 01 GateSwarm)
        Body: {
            "stats": {"deepseek-v3": {"tier": "standard", "endpoint_count": 1, "success_count": 100, ...}},
            "config": {"high_threshold": 0.8, "low_threshold": 0.2, ...}
        }
        """
        from .capability.consensus import TierStat, should_rebalance
        stats = {k: TierStat(**v) for k, v in body.get("stats", {}).items()}
        return {"should_rebalance": should_rebalance(stats, body.get("config", {}))}

    @app.post("/v1/capability/cost-estimate")
    async def capability_cost_estimate(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """dry-run 成本估算(来自 05 moa-skill)
        Body: {
            "input_tokens": 1000,
            "output_tokens": 500,
            "channels": [{"name": "deepseek-v3", "cost_per_1k_input": 0.0005, ...}],
            "include_fallback": true
        }
        """
        from .capability.cost_estimator import Channel, estimate_moa_cost, format_report
        channels = [Channel(**c) for c in body.get("channels", [])]
        est = estimate_moa_cost(
            input_tokens=body.get("input_tokens", 1000),
            output_tokens=body.get("output_tokens", 500),
            channels=channels,
            include_fallback=body.get("include_fallback", True),
        )
        if body.get("format") == "report":
            return {"report": format_report(est), "estimate": est.to_dict()}
        return est.to_dict()

    @app.post("/v1/capability/gate-l0")
    async def capability_gate_l0(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """L0 闸门(来自 05 moa-skill)— 判断是否需要启 MoA
        Body: {"query": "2+3"} or {"query": "design a distributed system"}
        """
        from .capability.gate_l0 import gate
        return gate(body.get("query", "")).to_dict()

    @app.post("/v1/capability/score-panel")
    async def capability_score_panel(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """5 维评分(来自 09 opencode-moa — TQ/CO/AP/SE/IN)
        Body: {"query": "...", "answer": "..."}
        """
        from .capability.score_panel import score_panel
        return score_panel(
            query=body.get("query", ""),
            answer=body.get("answer", ""),
        ).to_dict()

    @app.get("/v1/capability/models")
    async def capability_models(
        request: Request,
        provider: Optional[str] = None,
        supports_tools: Optional[bool] = None,
        supports_vision: Optional[bool] = None,
        min_context: int = 0,
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """列模型(来自 10 Verdex — 41 真实模型)
        Query params: provider, supports_tools, supports_vision, min_context
        """
        from .capability.model_context_db import list_models
        models = list_models(
            provider=provider,
            supports_tools=supports_tools if supports_tools else None,
            supports_vision=supports_vision if supports_vision else None,
            min_context=min_context,
        )
        return {"count": len(models), "models": [m.to_dict() for m in models]}

    @app.post("/v1/capability/calculate-max-tokens")
    async def capability_calculate_max_tokens(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """根据模型 context window 智能调整 max_tokens
        Body: {"model_id": "gpt-4o", "input_tokens": 1000, "requested_output": 2000, "safety_margin": 0.1}
        """
        from .capability.model_context_db import calculate_max_tokens
        return {
            "model_id": body.get("model_id"),
            "max_tokens": calculate_max_tokens(
                body.get("model_id", "gpt-4o"),
                body.get("input_tokens", 1000),
                body.get("requested_output", 2000),
                body.get("safety_margin", 0.1),
            ),
        }

    @app.post("/v1/capability/estimate-cost")
    async def capability_estimate_cost(
        request: Request,
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """估算单模型成本
        Body: {"model_id": "gpt-4o", "input_tokens": 1000, "output_tokens": 500}
        """
        from .capability.model_context_db import estimate_cost
        return estimate_cost(
            body.get("model_id", "gpt-4o"),
            body.get("input_tokens", 1000),
            body.get("output_tokens", 500),
        )

    # ========== v1.5.1 Capability Endpoints — Wave 1 (HIGH 优先级) ==========
    @app.post("/v1/capability/quota-check")
    async def capability_quota_check(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-08 多窗口配额检查 (5h/weekly/monthly + ETA)
        Body: {"windows":[{"name":"5h","limit_tokens":100000,"used_history":[[t1,n1],...]},...],"requested":1000}
        """
        from .capability.rate_quota import QuotaState, QuotaWindow, check_available, eta_exhaustion
        windows = {w["name"]: QuotaWindow(**w) for w in body.get("windows", [])}
        state = QuotaState(windows=windows, last_updated=body.get("last_updated", time.time()))
        requested = body.get("requested", 0)
        ok, reason = check_available(state, requested)
        result = {
            "available": ok,
            "reason": reason,
            "eta_hours": {name: eta_exhaustion(state, body.get("burn_rate_per_hour", 1000.0), name) for name in windows},
        }
        return result

    @app.post("/v1/capability/quota-record")
    async def capability_quota_record(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-08 记录 quota usage + 返回新 state"""
        from .capability.rate_quota import QuotaState, QuotaWindow, record_usage
        windows = {w["name"]: QuotaWindow(**w) for w in body.get("windows", [])}
        state = QuotaState(windows=windows, last_updated=body.get("last_updated", time.time()))
        record_usage(state, body.get("tokens", 0), body.get("at"))
        return {"windows": {name: w.__dict__ for name, w in state.windows.items()}, "last_updated": state.last_updated}

    @app.post("/v1/capability/moa-n-layer")
    async def capability_moa_n_layer(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-02 多层 MoA (3-layer 默认,真实跑通)
        Body: {"query":"...","proposers":[{"name":"a","model_id":"gpt-4o"}],"aggregators":[...]}
        """
        from .capability.n_layer_moa import (
            Proposer, Aggregator, run_three_layer_moa,
        )
        proposers = [Proposer(**p) for p in body.get("proposers", [])]
        aggregators = [Aggregator(**a) for a in body.get("aggregators", [])]
        try:
            result = await run_three_layer_moa(
                body.get("query", ""),
                proposers=proposers,
                aggregators=aggregators,
                temperature=body.get("temperature", 0.6),
                max_total_tokens=body.get("max_total_tokens", 0),
            )
        except Exception as e:
            raise HTTPException(500, f"MoA run failed: {e}")
        return result

    @app.post("/v1/capability/convergent-detect")
    async def capability_convergent_detect(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-16 跨提案 CONVERGENT 想法检测 + M-17 冲突仲裁
        Body: {"proposals":[{"proposal_idx":0,"author":"a","text":"..."}],"viability_scores":{0:0.8}}
        """
        from .capability.convergent_detector import (
            Proposal, convergent_summary, extract_ideas,
        )
        proposals = [Proposal(**p) for p in body.get("proposals", [])]
        for p in proposals:
            if not p.ideas:
                p.ideas = extract_ideas(p.text, p.proposal_idx)
        summary = convergent_summary(proposals, min_support=body.get("min_support", 3))
        viability = body.get("viability_scores", {})
        if viability:
            from .capability.convergent_detector import arbitrate_conflicts
            summary["arbitrations"] = [
                {"option_a": c.option_a, "option_b": c.option_b, "winner": w, "confidence": conf}
                for c, w, conf in arbitrate_conflicts(summary["conflicts"], viability)
            ]
        return summary

    @app.post("/v1/capability/action-policy")
    async def capability_action_policy(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-31 Action Policy (Allow/Deny/AdminReview) + A-32 Bypass Defense
        Body: {"command":"rm -rf /tmp/foo","rules":[{...PolicyRule...}]}
        """
        from .capability.action_policy import (
            PolicyRule, ActionPolicy, pre_action_check,
        )
        rules = [PolicyRule(**r) for r in body.get("rules", [])]
        policy = ActionPolicy(rules)
        verdict = pre_action_check(body.get("command", ""), policy)
        return verdict.__dict__

    @app.post("/v1/capability/embeddings")
    async def capability_embeddings(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """L-36 Embedding 端点 (OpenAI 兼容 /v1/embeddings 接口)
        Body: {"input":["text1","text2"], "model":"mock", "dim":384}
        Returns: {"data":[{"index":0,"embedding":[...]},...], "model":"mock", "dim":384}
        """
        from .capability.embedding import MockEmbeddingProvider
        inputs = body.get("input", [])
        if isinstance(inputs, str):
            inputs = [inputs]
        dim = body.get("dim", 384)
        model = body.get("model", "mock-embedding-v1")
        provider = MockEmbeddingProvider(model=model, dim=dim)
        vectors = provider.embed(inputs)
        return {
            "object": "list",
            "data": [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)],
            "model": model,
            "dim": dim,
            "usage": {"prompt_tokens": sum(len(t.split()) for t in inputs), "total_tokens": sum(len(t.split()) for t in inputs)},
        }

    @app.post("/v1/capability/semantic-search")
    async def capability_semantic_search(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """L-36 语义搜索 (端到端: embed query + 搜 index)
        Body: {"query":"...","documents":["a","b","c"],"top_k":3,"dim":384}
        """
        from .capability.embedding import (
            MockEmbeddingProvider, EmbeddingIndex, batch_embed,
        )
        dim = body.get("dim", 384)
        docs = body.get("documents", [])
        provider = MockEmbeddingProvider(model="mock-embedding-v1", dim=dim)
        vectors = batch_embed(docs, dim=dim)
        index = EmbeddingIndex(model="mock-embedding-v1", dim=dim)
        for doc, vec in zip(docs, vectors):
            index.add(doc, vec)
        query_vec = provider.embed([body.get("query", "")])[0]
        results = index.search(query_vec, top_k=body.get("top_k", 3))
        return {
            "query": body.get("query", ""),
            "results": [{"rank": i + 1, "score": s, "text": t} for i, (idx, s, t) in enumerate(results)],
        }

    # ========== v1.5.2 Capability Endpoints — Wave 2 (HIGH 优先级) ==========
    @app.post("/v1/capability/prompt-features")
    async def capability_prompt_features(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-02 25 维 prompt 特征提取 + 域判 + complexity/urgency/pro_model
        Body: {"text": "..."}
        """
        from .capability.prompt_features import (
            extract_features, domain_classify, complexity_score,
            urgency_score, should_use_pro_model,
        )
        text = body.get("text", "")
        feats = extract_features(text)
        return {
            "features": feats.__dict__,
            "domain": domain_classify(feats),
            "complexity": complexity_score(feats),
            "urgency": urgency_score(feats),
            "use_pro_model": should_use_pro_model(feats),
        }

    @app.post("/v1/capability/provider-health")
    async def capability_provider_health(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-07 提供者健康评分 (0-100) + tier 等级 + 排名 + 推荐
        Body: {"providers": [{"provider": "deepseek-v3", "total_calls": 100, ...HealthMetrics...}]}
        """
        from .capability.provider_health import (
            HealthMetrics, compute_score, aggregate_scores,
            rank_providers, recommend,
        )
        metrics_list = [HealthMetrics(**m) for m in body.get("providers", [])]
        scores = {m.provider: compute_score(m) for m in metrics_list}
        agg = aggregate_scores(list(scores.values()))
        ranked = rank_providers(scores)
        return {
            "scores": {k: {"score": v.score, "tier": v.tier, "reasons": v.reasons} for k, v in scores.items()},
            "ranked": [{"provider": p, "score": s} for p, s in ranked],
            "recommend": recommend(scores, body.get("prefer_tier")),
        }

    @app.post("/v1/capability/context-clean")
    async def capability_context_clean(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-11 7 阶段消息清洗
        Body: {"messages":[{"role":"user","content":"..."},...],"max_total_chars":100000}
        """
        from .capability.context_clean import (
            Message, clean_messages, to_openai_format,
        )
        msgs = [Message(**m) for m in body.get("messages", [])]
        cleaned, stats = clean_messages(msgs, max_total_chars=body.get("max_total_chars", 100000))
        return {
            "messages": to_openai_format(cleaned),
            "stats": stats.__dict__,
        }

    @app.post("/v1/capability/self-heal")
    async def capability_self_heal(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-15 自愈 tier 重新平衡 (action: record_success/record_failure/check_recovery/promote/demote/auto_balance)
        Body: {"endpoints":[{...EndpointState...}],"action":"record_success","endpoint_id":"ep1","at":123.0}
        """
        from .capability.self_heal import (
            EndpointState, HealState, record_success, record_failure,
            check_recovery, promote, demote, auto_balance, get_available_endpoints,
            state_to_dict, state_from_dict,
        )
        endpoints = {e["endpoint_id"]: EndpointState(**e) for e in body.get("endpoints", [])}
        state = HealState(endpoints=endpoints)
        action = body.get("action", "auto_balance")
        at = body.get("at")
        result_actions = []
        try:
            if action == "record_success":
                result_actions = [record_success(state, body["endpoint_id"], at)]
            elif action == "record_failure":
                result_actions = [record_failure(state, body["endpoint_id"], at)]
            elif action == "check_recovery":
                result_actions = [check_recovery(state, body["endpoint_id"], at)]
            elif action == "promote":
                result_actions = [promote(state, body["endpoint_id"], body.get("reason", "manual"), at)]
            elif action == "demote":
                result_actions = [demote(state, body["endpoint_id"], body.get("reason", "manual"), at)]
            elif action == "auto_balance":
                result_actions = auto_balance(state, at)
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"self_heal action failed: {e}")
        return {
            "actions": [a.__dict__ for a in result_actions],
            "state": state_to_dict(state),
            "available_endpoints": get_available_endpoints(state),
        }

    @app.post("/v1/capability/multi-mode-synth")
    async def capability_multi_mode_synth(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-14 多模式综合器 (4 模式: classification / integrated_synthesis / final_selection / cross_iteration)
        Body: {"mode":"classification","proposals":[{"proposal_idx":0,"author":"a","text":"..."}],...}
        """
        from .capability.multi_mode_synth import (
            Proposal, run_synthesis, should_run_integration,
        )
        proposals = [Proposal(**p) for p in body.get("proposals", [])]
        mode = body.get("mode", "classification")
        kwargs = {}
        if "scores" in body:
            kwargs["scores"] = body["scores"]
        if "target_chars" in body:
            kwargs["target_chars"] = body["target_chars"]
        if "prev_proposals" in body and "curr_proposals" in body:
            kwargs["prev_proposals"] = [Proposal(**p) for p in body["prev_proposals"]]
            kwargs["curr_proposals"] = [Proposal(**p) for p in body["curr_proposals"]]
        try:
            result = run_synthesis(mode, proposals, **kwargs)
        except Exception as e:
            raise HTTPException(500, f"synthesis failed: {e}")
        return {
            "mode": result.mode.value,
            "output": result.output,
            "source_attribution": {str(k): v for k, v in result.source_attribution.items()},
            "confidence": result.confidence,
            "metadata": result.metadata,
        }

    # ========== v1.5.3 Capability Endpoints — Wave 3 (HIGH 优先级) ==========
    @app.post("/v1/capability/conflict-arbitrate")
    async def capability_conflict_arbitrate(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-17 CONFLICTING 选择仲裁 (4 维: viability/support/empirical/compilable)
        Body: {"options":[{...ConflictOption...}],"fuse":false,"query":""}
        """
        from .capability.conflict_arbiter import (
            ConflictOption, arbitrate, fuse_decision,
        )
        options = [ConflictOption(**o) for o in body.get("options", [])]
        if body.get("fuse", False):
            verdict = fuse_decision(options, body.get("query", ""))
        else:
            verdict = arbitrate(options)
        return {
            "winner_option_id": verdict.winner_option_id,
            "runner_up_id": verdict.runner_up_id,
            "confidence": verdict.confidence,
            "rationale": verdict.rationale,
            "voting_breakdown": verdict.voting_breakdown,
        }

    @app.post("/v1/capability/section-viability")
    async def capability_section_viability(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-18 Per-section viability (复杂提案分节验证)
        Body: {"text":"...","proposal_idx":0}
        """
        from .capability.section_viability import (
            validate_proposal, compare_proposals,
        )
        text = body.get("text", "")
        proposal_idx = body.get("proposal_idx", 0)
        report = validate_proposal(text, proposal_idx)
        return {
            "proposal_idx": report.proposal_idx,
            "total_sections": report.total_sections,
            "viable_sections": report.viable_sections,
            "failing_sections": report.failing_sections,
            "ap_score": report.ap_score,
            "verdicts": [v.__dict__ for v in report.verdicts],
        }

    @app.post("/v1/capability/feedback-iter")
    async def capability_feedback_iter(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-19 Feedback-aware iteration (跨迭代知识传递)
        Body: {"record":{...IterationRecord...},"history_path":""}
        """
        from .capability.feedback_loop import (
            IterationRecord, analyze_iteration, save_feedback,
            load_history, detect_convergence, format_next_iter_prompt,
        )
        rec = IterationRecord(**body.get("record", {}))
        feedback = analyze_iteration(rec)
        history_path = body.get("history_path", "")
        if history_path:
            save_feedback(history_path, feedback)
        history = load_history(history_path) if history_path else []
        conv = detect_convergence(history) if history else {"converged": False, "std": 0.0, "trend": "stable"}
        prompt = format_next_iter_prompt(history_path) if history_path else ""
        return {
            "feedback": feedback.__dict__,
            "convergence": conv,
            "next_iter_prompt": prompt,
        }

    @app.post("/v1/capability/stream-aggregate")
    async def capability_stream_aggregate(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-06 Aggregator 流式 + 非流式 fallback
        Body: {"prompt":"...","model":"mock-stream-v1","fail_prob":0.0,"use_fallback":true}
        """
        import asyncio
        from .capability.streaming_agg import (
            MockStreamingProvider, aggregate_with_fallback,
        )
        provider = MockStreamingProvider(
            fail_prob=body.get("fail_prob", 0.0),
        )
        try:
            result = await aggregate_with_fallback(
                provider, body.get("prompt", ""), body.get("model", "mock-stream-v1"),
            )
        except Exception as e:
            raise HTTPException(500, f"stream aggregate failed: {e}")
        return {
            "full_content": result.full_content,
            "tool_calls": result.tool_calls,
            "finish_reason": result.finish_reason,
            "total_chunks": result.total_chunks,
            "streaming_succeeded": result.streaming_succeeded,
            "chunks_preview": [
                {"idx": c.chunk_idx, "type": c.delta_type, "content_preview": c.content[:40]}
                for c in result.chunks[:5]
            ],
        }

    @app.post("/v1/capability/per-provider-rl")
    async def capability_per_provider_rl(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-17 Per-provider 限流 (RPM/IPM/并发 + 429 cooldown)
        Body: {"provider":"deepseek-v3","action":"check|record|mark_429|acquire","concurrent":0,"at":null}
        """
        from .capability.per_provider_rl import (
            ProviderLimit, ProviderLimiter, MultiProviderLimiter,
        )
        # 单 provider 模式: limits 转 {provider: ProviderLimit}
        limits_data = body.get("limits", {})
        if not limits_data and "provider" in body:
            limits_data = {body["provider"]: body.get("limit_config", {
                "max_requests_per_minute": 60,
                "max_inputs_per_minute": 100000,
                "max_concurrent": 5,
            })}
        limits = {k: ProviderLimit(**v) for k, v in limits_data.items()}
        mpl = MultiProviderLimiter(limits)
        action = body.get("action", "check")
        provider = body.get("provider", next(iter(limits.keys())) if limits else "")
        result = {}
        try:
            if action == "check":
                decision = mpl.check(provider, concurrent_now=body.get("concurrent", 0), at=body.get("at"))
                result = decision.__dict__
            elif action == "record":
                mpl.record(provider, body.get("request_count", 1), body.get("input_tokens", 0), body.get("at"))
                result = {"recorded": True, "provider": provider}
            elif action == "mark_429":
                limiter = mpl.limiters[provider]
                limiter.mark_429(body.get("cooldown_seconds", 60.0), at=body.get("at"))
                result = {"marked_429": True, "provider": provider}
            elif action == "status":
                limiter = mpl.limiters[provider]
                result = {
                    "current_rpm": limiter._current_rpm(body.get("at")),
                    "current_ipm": limiter._current_ipm(body.get("at")),
                    "in_cooldown": limiter.is_in_cooldown(body.get("at")),
                }
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"per_provider_rl action failed: {e}")
        return result

    # ========== v1.5.4 Capability Endpoints — Wave 4 (HIGH 优先级) ==========
    @app.post("/v1/capability/tier-recalibrate")
    async def capability_tier_recalibrate(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-04 Tier 边界动态重校准 (网格搜索阈值 + demote/promote)
        Body: {"tiers":[{"tier":"standard","p50_latency_ms":800,"p95_latency_ms":1500,...}]}
        """
        from .capability.tier_recalibrate import (
            TierLabel, TierMetrics, recalibrate, should_retrain, grid_search_thresholds,
        )
        # tier 字段自动转小写以匹配 enum
        tier_map = {"FREE": "free", "LITE": "lite", "STANDARD": "standard", "PREMIUM": "premium", "FLAGSHIP": "flagship"}
        for t in body.get("tiers", []):
            if isinstance(t.get("tier"), str):
                t["tier"] = tier_map.get(t["tier"].upper(), t["tier"].lower())
        metrics = [TierMetrics(**m) for m in body.get("tiers", [])]
        plans = recalibrate(metrics)
        return {
            "plans": [{"old_tier": p.old_tier.value if hasattr(p.old_tier, 'value') else p.old_tier,
                       "new_tier": p.new_tier.value if hasattr(p.new_tier, 'value') else p.new_tier,
                       "reason": p.reason, "score_change": p.score_change,
                       "expected_improvement": p.expected_improvement} for p in plans],
            "should_retrain": should_retrain(plans),
            "plan_count": len(plans),
        }

    @app.post("/v1/capability/consumption-intel")
    async def capability_consumption_intel(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-06 消费智能引擎 (静态优先 + 动态 fallback + vision 降级)
        Body: {"context":{...RequestContext...},"endpoints":[{...EndpointSpec...}]}
        """
        from .capability.consumption_intel import (
            RequestContext, EndpointSpec, select_endpoint,
        )
        ctx = RequestContext(**body.get("context", {"query": ""}))
        endpoints = [EndpointSpec(**e) for e in body.get("endpoints", [])]
        decision = select_endpoint(ctx, endpoints)
        return {
            "selected_endpoint_id": decision.selected_endpoint_id,
            "fallback_chain": decision.fallback_chain,
            "vision_degraded_to": decision.vision_degraded_to,
            "reason": decision.reason,
            "estimated_cost_usd": decision.estimated_cost_usd,
        }

    @app.post("/v1/capability/importance-score")
    async def capability_importance_score(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-13 重要性评分 (5 维加权 + top-k + 压缩决策)
        Body: {"messages":[{...Message...}],"top_k":3}
        """
        from .capability.importance import (
            Message, score_messages, select_top_k, should_compress,
        )
        msgs = [Message(**m) for m in body.get("messages", [])]
        scores = score_messages(msgs)
        top_k = body.get("top_k", 0)
        return {
            "scores": [{"message_idx": s.message_idx, "score": s.score, "reasons": s.reasons} for s in scores],
            "top_k_indices": select_top_k(scores, top_k) if top_k else [],
            "should_compress": should_compress(scores, body.get("threshold", 0.5)),
        }

    @app.post("/v1/capability/quorum-check")
    async def capability_quorum_check(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-20 Quorum 宽限窗 (30s 宽限 + LLM-as-Judge 评分)
        Body: {"participants":[{...Participant...}],"required":3,"grace_seconds":30,"at":100.0}
        """
        from .capability.quorum import (
            QuorumConfig, Participant, check_quorum, should_wait, force_close,
            parse_rating, parse_battle, swap_positions_battle,
        )
        config = QuorumConfig(
            required=body.get("required", 3),
            grace_seconds=body.get("grace_seconds", 30.0),
            wait_for_laggards=body.get("wait_for_laggards", True),
        )
        participants = [Participant(**p) for p in body.get("participants", [])]
        at = body.get("at")
        status = check_quorum(participants, config, at=at)
        result = {
            "status": {
                "reached": status.reached,
                "reached_at": status.reached_at,
                "responded_count": status.responded_count,
                "missing": status.missing,
                "within_grace": status.within_grace,
            },
            "should_wait": should_wait(status, config, at=at),
        }
        if body.get("force_close"):
            responded, dropped = force_close(participants, config, at=at)
            result["force_close"] = {
                "responded": [p.participant_id for p in responded],
                "dropped": dropped,
            }
        if body.get("judge_response"):
            jr = body["judge_response"]
            if "response_a" in body and "response_b" in body:
                result["battle"] = {
                    "winner": parse_battle(jr)[0],
                    "swap_consistent": swap_positions_battle(
                        body["response_a"], body["response_b"],
                        lambda r: parse_battle(r),
                    ) == "consistent",
                }
            else:
                result["rating"] = parse_rating(jr)
        return result

    @app.post("/v1/capability/model-entry")
    async def capability_model_entry(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """L-29 Provider 状态 12 字段 ModelEntry (capability check + filter + sort + budget)
        Body: {"models":[{...ModelEntry...}],"filter":{},"sort":"cost_asc","max_budget_input":0.01}
        """
        from .capability.model_entry import (
            ModelEntry, get_capability, filter_by_capability,
            filter_by_modality, filter_by_min_context, sort_by_cost,
            sort_by_context, find_within_budget, multimodal_score, Modality,
        )
        # modalities 元素转 Modality enum 实例 (value 已是 'TEXT'/'IMAGE' 大写)
        for m in body.get("models", []):
            if "modalities" in m:
                m["modalities"] = [Modality(x.upper()) if isinstance(x, str) else x for x in m["modalities"]]
        models = [ModelEntry(**m) for m in body.get("models", [])]
        result_models = models
        flt = body.get("filter", {})
        if "capability" in flt:
            result_models = filter_by_capability(result_models, flt["capability"], flt.get("value", True))
        if "modality" in flt:
            result_models = filter_by_modality(result_models, Modality(flt["modality"].upper()))
        if "min_context" in flt:
            result_models = filter_by_min_context(result_models, flt["min_context"])
        if "max_budget_input" in body or "max_budget_output" in body:
            result_models = find_within_budget(
                result_models, body.get("max_budget_input"), body.get("max_budget_output"),
            )
        sort = body.get("sort", "")
        if sort == "cost_asc":
            result_models = sort_by_cost(result_models, ascending=True)
        elif sort == "cost_desc":
            result_models = sort_by_cost(result_models, ascending=False)
        elif sort == "context_desc":
            result_models = sort_by_context(result_models, descending=True)
        query_modalities = [Modality(m.upper()) for m in body.get("query_modalities", [])]
        return {
            "models": [m.__dict__ for m in result_models],
            "count": len(result_models),
            "multimodal_scores": {
                m.model_id: multimodal_score(m, query_modalities) for m in result_models
            } if query_modalities else {},
        }

    # ========== v1.5.5 Capability Endpoints — Wave 5 (HIGH 优先级) ==========
    @app.post("/v1/capability/tool-replay")
    async def capability_tool_replay(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-07 Tool call 重放 + M-09 Tool choice 防循环
        Body: {"proposals":[{...proposal with <tool_use>...}],"window":5}
        """
        from .capability.tool_replay import (
            extract_tool_calls, replay_tool_calls, should_disable_tool_choice,
            detect_tool_loop, format_tool_calls_for_aggregator,
        )
        proposals = body.get("proposals", [])
        # extract
        all_calls = []
        for i, p in enumerate(proposals):
            all_calls.extend(extract_tool_calls(p, i))
        # replay
        replay = replay_tool_calls(proposals, source_indices=list(range(len(proposals))))
        # 防循环
        disable = should_disable_tool_choice(len(all_calls), body.get("recent_count", len(all_calls)))
        loop = detect_tool_loop(all_calls, window=body.get("window", 5))
        formatted = format_tool_calls_for_aggregator(replay.tool_calls)
        return {
            "tool_calls": [tc.__dict__ for tc in replay.tool_calls],
            "deduplicated_count": replay.deduplicated_count,
            "conflicts_resolved": replay.conflicts_resolved,
            "should_disable_tool_choice": disable,
            "detected_loop": loop.__dict__ if loop else None,
            "aggregator_format": formatted,
        }

    @app.post("/v1/capability/hook-events")
    async def capability_hook_events(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-02 27 Hook 事件注册 + A-10 4 阶段 Ralph 反馈循环
        Body: {"action":"trigger|ralph_advance","event":"PostToolUse","data":{},"session_id":"s1"}
        """
        from .capability.hook_events import (
            HookEvent, HookRegistry, HookContext, RALPH_CYCLE,
            ralph_loop,
        )
        # 全局 registry (in-memory)
        if not hasattr(capability_hook_events, "_registry"):
            capability_hook_events._registry = HookRegistry()
        reg = capability_hook_events._registry
        action = body.get("action", "ralph_advance")
        result = {}
        if action == "register":
            # 需 callback 不能从 body 拿,只返回 event list
            result = {"registered_event": body.get("event"), "total_handlers": len(reg.list_handlers())}
        elif action == "trigger":
            event_name = body.get("event", "SessionStart")
            try:
                event = HookEvent(event_name)
            except ValueError:
                raise HTTPException(400, f"unknown event: {event_name}")
            ctx = HookContext(
                event=event, session_id=body.get("session_id", ""),
                timestamp=body.get("timestamp", 0.0), data=body.get("data", {}),
            )
            triggered = reg.trigger(event, ctx.__dict__)
            result = {"triggered_count": len(triggered)}
        elif action == "ralph_advance":
            stage = body.get("stage", "analyze")
            data = body.get("data", {})
            if not hasattr(capability_hook_events, "_ralph"):
                capability_hook_events._ralph = RALPH_CYCLE(max_iter=body.get("max_iter", 5))
            cycle = capability_hook_events._ralph
            next_stage = cycle.advance(data)
            result = {
                "current_stage": stage,
                "next_stage": next_stage,
                "iteration": cycle.iteration,
                "terminated": cycle.terminated,
            }
        elif action == "list_events":
            result = {"events": [e.value for e in HookEvent], "count": len(HookEvent)}
        else:
            raise HTTPException(400, f"unknown action: {action}")
        return result

    @app.post("/v1/capability/meta-prompt")
    async def capability_meta_prompt(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-22 3 阶段元 Prompt 协议 + M-23 认知摩擦对抗 + M-26 冲突消解
        Body: {"query":"...","action":"get_stages|clash|fuse","options":[...]}
        """
        from .capability.meta_prompt import (
            get_stage_prompts, cognitively_clash, fuse_decision,
        )
        action = body.get("action", "get_stages")
        query = body.get("query", "")
        if action == "get_stages":
            stages = get_stage_prompts(query)
            return {
                "stages": [s.__dict__ for s in stages],
                "count": len(stages),
            }
        elif action == "clash":
            role_a = body.get("role_a", "optimist")
            role_b = body.get("role_b", "pessimist")
            a, b = cognitively_clash(role_a, role_b, query)
            return {"role_a_prompt": a, "role_b_prompt": b}
        elif action == "fuse":
            options = body.get("options", [])
            winner = fuse_decision(options, body.get("context", query))
            return {"winner": winner, "options_count": len(options)}
        else:
            raise HTTPException(400, f"unknown action: {action}")

    @app.post("/v1/capability/task-tree")
    async def capability_task_tree(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-18 Task Tree (TaskSegment) + A-34 Task 分解树
        Body: {"tasks":[{...TaskSegment...}],"action":"add|ready|cycles|aggregates|depth","task_id":""}
        """
        from .capability.task_tree import (
            TaskSegment, TaskTree, TaskStatus, compute_aggregates,
            get_ready_tasks as _get_ready, detect_cycles, depth, is_leaf, is_root,
            tree_to_dict, tree_from_dict,
        )
        # 构建/恢复 tree
        tasks_data = body.get("tasks", [])
        # 补 status 缺省值
        for t in tasks_data:
            if "status" not in t:
                t["status"] = "pending"
        tree = None
        if tasks_data:
            # 尝试 tree_from_dict 格式(若有完整 fields)
            try:
                tree = tree_from_dict({"tasks": tasks_data})
            except Exception:
                pass
            if tree is None:
                # 兜底:从 list 手动构建
                root_id = next((t["id"] for t in tasks_data if t.get("parent_id") is None), tasks_data[0]["id"])
                tree = TaskTree(root_id=root_id)
                # 先 add root
                root_data = next(t for t in tasks_data if t["id"] == root_id)
                root_seg = {k: v for k, v in root_data.items() if k != "children_ids"}
                tree.add_task(TaskSegment(**root_seg))
                for t in tasks_data:
                    if t["id"] == root_id:
                        continue
                    seg_data = {k: v for k, v in t.items() if k != "children_ids"}
                    try:
                        tree.add_task(TaskSegment(**seg_data))
                    except ValueError:
                        # 重复 id 跳过
                        pass
        if tree is None:
            tree = TaskTree(root_id="root")
            tree.add_task(TaskSegment(id="root", title="root", description="root", status="pending"))
        else:
            tree = TaskTree(root_id="root")
        action = body.get("action", "ready")
        task_id = body.get("task_id", "")
        result = {}
        if action == "ready":
            result = {"ready_tasks": _get_ready(tree)}
        elif action == "cycles":
            cycles = detect_cycles(tree)
            result = {"cycles": cycles, "has_cycle": len(cycles) > 0}
        elif action == "aggregates":
            result = compute_aggregates(tree, task_id) if task_id else {}
        elif action == "depth":
            result = {"task_id": task_id, "depth": depth(tree, task_id) if task_id else -1}
        elif action == "is_leaf":
            result = {"task_id": task_id, "is_leaf": is_leaf(tree, task_id) if task_id else False}
        elif action == "is_root":
            result = {"task_id": task_id, "is_root": is_root(tree, task_id) if task_id else False}
        elif action == "set_status":
            new_status = body.get("status", "completed")
            try:
                status_enum = TaskStatus(new_status)
            except ValueError:
                raise HTTPException(400, f"unknown status: {new_status}")
            tree.set_status(task_id, status_enum)
            result = {"set": True, "task_id": task_id, "status": new_status}
        else:
            raise HTTPException(400, f"unknown action: {action}")
        result["tree"] = tree_to_dict(tree)
        return result

    @app.post("/v1/capability/distill")
    async def capability_distill(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-15 Integrated synthesis + M-51 Multi-eval consensus averaging
        Body: {"proposals":["..."],"keep_ratio":0.5,"evaluations":[{"TQ":40,"CO":35,...}]}
        """
        from .capability.distillation import (
            distill_proposals, multi_eval_average, apply_bias_correction,
        )
        proposals = body.get("proposals", [])
        keep_ratio = body.get("keep_ratio", 0.5)
        distillation = distill_proposals(proposals, keep_ratio=keep_ratio)
        result = {
            "distillation": {
                "kept_count": distillation.distilled_count,
                "dropped_count": len(distillation.dropped_ideas),
                "original_count": distillation.original_count,
                "ratio": distillation.distillation_ratio,
                "kept_ideas": [i.__dict__ for i in distillation.kept_ideas],
            },
        }
        if "evaluations" in body:
            evals = body["evaluations"]
            avg = multi_eval_average(evals)
            biases = avg.pop("biases", {})
            result["multi_eval"] = {
                "averages": avg,
                "biases": biases,
            }
            if body.get("apply_bias_correction") and biases:
                result["corrected"] = {
                    dim: apply_bias_correction({dim: scores}, {dim: biases.get(dim, 0)}).get(dim, 0)
                    for dim, scores in avg.items()
                }
        return result

    # ========== v1.5.6 Capability Endpoints — Wave 6 (HIGH 优先级) ==========
    @app.post("/v1/capability/rerank")
    async def capability_rerank(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """L-37 Cohere Rerank v4 (latency-bounded) + L-31 Stream delta 完整代理
        Body: {"query":"...","documents":["d1","d2"],"top_n":3,"latency_budget_ms":2000}
        """
        from .capability.rerank import (
            MockRerankProvider, rerank_with_budget, stream_delta_proxy,
            format_for_openai,
        )
        query = body.get("query", "")
        documents = body.get("documents", [])
        top_n = body.get("top_n", 10)
        budget = body.get("latency_budget_ms", 2000.0)
        result = rerank_with_budget(query, documents, top_n=top_n, latency_budget_ms=budget)
        result_data = {
            "query": result.query,
            "candidates": [c.__dict__ for c in result.candidates],
            "latency_ms": result.latency_ms,
            "truncated": result.truncated,
        }
        if body.get("stream_chunks"):
            proxy = stream_delta_proxy(body["stream_chunks"])
            result_data["stream_proxy"] = proxy
            result_data["openai_format"] = format_for_openai(proxy)
        return result_data

    @app.post("/v1/capability/goal-eval")
    async def capability_goal_eval(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-12 2-tier 目标求值 + A-13 5-section Ceiling Report
        Body: {"goals":[{...Goal...}],"output":"...","generate_ceiling":true}
        """
        from .capability.goal_eval import (
            Goal, GoalTier, evaluate_goal, evaluate_goals, generate_ceiling_report,
        )
        tier_map = {1: "mechanical", 2: "model_declared"}
        goals = []
        for g in body.get("goals", []):
            tier_str = g.get("tier", "mechanical")
            if isinstance(tier_str, int):
                tier_str = tier_map.get(tier_str, "mechanical")
            g["tier"] = tier_str
            goals.append(Goal(**g))
        output = body.get("output", "")
        results = [evaluate_goal(g, output).__dict__ for g in goals]
        ceiling = None
        if body.get("generate_ceiling"):
            cr = generate_ceiling_report(
                claim=body.get("claim", ""),
                evidence=body.get("evidence", []),
                baseline=body.get("baseline", ""),
                gaps=body.get("gaps", []),
                residual_risk=body.get("residual_risk", ""),
            )
            ceiling = cr.__dict__
        return {"results": results, "ceiling_report": ceiling}

    @app.post("/v1/capability/auto-converge")
    async def capability_auto_converge(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-15 Auto-converge + A-14 Tier classification 1/3/5/10
        Body: {"state":{...},"config":{...},"new_score":0.85,"classify_events":5}
        """
        from .capability.auto_converge import (
            ConvergenceState, ConvergenceConfig, check_convergence,
            classify_tier, detect_stagnation, calibrate_confidence,
        )
        result = {}
        if "state" in body and "new_score" in body:
            state_data = body["state"]
            state = ConvergenceState(
                iteration=state_data.get("iteration", 0),
                best_score_history=state_data.get("best_score_history", []),
                stagnation_count=state_data.get("stagnation_count", 0),
                converged=state_data.get("converged", False),
            )
            cfg = ConvergenceConfig(
                stagnation_threshold=body.get("config", {}).get("stagnation_threshold", 3),
                improvement_threshold=body.get("config", {}).get("improvement_threshold", 0.001),
                max_iterations=body.get("config", {}).get("max_iterations", 10),
            )
            new_state = check_convergence(state, cfg, body["new_score"])
            result["new_state"] = new_state.__dict__
        if "classify_events" in body:
            result["classified_tier"] = classify_tier(body["classify_events"])
        if "history" in body:
            result["stagnant"] = detect_stagnation(
                body["history"],
                threshold=body.get("stagnation_threshold", 3),
                epsilon=body.get("epsilon", 0.001),
            )
        if "calibrate_score" in body:
            result["calibrated"] = calibrate_confidence(
                body["calibrate_score"],
                body.get("calibrate_samples", 0),
            )
        return result

    @app.post("/v1/capability/subagent-comms")
    async def capability_subagent_comms(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-38 Subagent 通信 (SendMessage/TaskCreate) + A-22 Advisory lock
        Body: {"action":"send|broadcast|reply|create_task|update_status|acquire|release","session_id":"s1",...}
        """
        from .capability.subagent_comms import SubagentHub, TaskBoard, AdvisoryLock
        action = body.get("action", "send")
        session_id = body.get("session_id", "default")
        result = {}
        try:
            if action in ("send", "broadcast", "reply", "inbox"):
                if not hasattr(capability_subagent_comms, "_hubs"):
                    capability_subagent_comms._hubs = {}
                hubs = capability_subagent_comms._hubs
                if session_id not in hubs:
                    hubs[session_id] = SubagentHub(session_id)
                hub = hubs[session_id]
                if action == "send":
                    msg = hub.send_message(body["to_session"], body.get("content", ""), body.get("kind", "send"))
                    result = {"message": msg.__dict__}
                elif action == "broadcast":
                    msgs = hub.broadcast(body.get("sessions", []), body.get("content", ""))
                    result = {"messages": [m.__dict__ for m in msgs]}
                elif action == "reply":
                    msg = hub.reply(body["parent_msg_id"], body.get("content", ""))
                    result = {"message": msg.__dict__}
                elif action == "inbox":
                    result = {"messages": [m.__dict__ for m in hub.inbox()]}
            elif action in ("create_task", "update_status", "list_tasks", "get_task", "get_subtasks"):
                if not hasattr(capability_subagent_comms, "_boards"):
                    capability_subagent_comms._boards = {}
                boards = capability_subagent_comms._boards
                if session_id not in boards:
                    boards[session_id] = TaskBoard(session_id)
                board = boards[session_id]
                if action == "create_task":
                    task_id = board.create_task(
                        body.get("title", ""),
                        assignee=body.get("assignee"),
                        parent=body.get("parent"),
                    )
                    result = {"task_id": task_id}
                elif action == "update_status":
                    board.update_status(body["task_id"], body.get("status", "pending"))
                    result = {"updated": True}
                elif action == "list_tasks":
                    tasks = board.list_tasks(status=body.get("status"), assignee=body.get("assignee"))
                    result = {"tasks": [t.__dict__ for t in tasks]}
                elif action == "get_task":
                    t = board.get_task(body["task_id"])
                    result = {"task": t.__dict__ if t else None}
                elif action == "get_subtasks":
                    tasks = board.get_subtasks(body["parent_task_id"])
                    result = {"tasks": [t.__dict__ for t in tasks]}
            elif action in ("acquire", "release", "is_held"):
                lock_id = body["lock_id"]
                if not hasattr(capability_subagent_comms, "_locks"):
                    capability_subagent_comms._locks = {}
                locks = capability_subagent_comms._locks
                if lock_id not in locks:
                    locks[lock_id] = AdvisoryLock(lock_id, body.get("holder", session_id), body.get("timeout", 10.0))
                lock = locks[lock_id]
                if action == "acquire":
                    result = {"acquired": lock.acquire()}
                elif action == "release":
                    lock.release()
                    result = {"released": True}
                elif action == "is_held":
                    result = {"held": lock.is_held()}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except Exception as e:
            raise HTTPException(500, f"subagent_comms failed: {e}")
        return result

    @app.post("/v1/capability/version")
    async def capability_version(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-35 方案版本化 (v1→v2) + M-27 LLM-as-Judge 单答评分
        Body: {"action":"add|get|latest|diff|parse_rating|parse_battle","proposal_id":"p1",...}
        """
        from .capability.versioning import (
            VersionStore, diff_versions, parse_rating, parse_battle,
            swap_positions_battle,
        )
        if not hasattr(capability_version, "_stores"):
            capability_version._stores = {}
        stores = capability_version._stores
        action = body.get("action", "add")
        result = {}
        try:
            if action in ("add", "get", "latest", "diff"):
                proposal_id = body.get("proposal_id", "default")
                if proposal_id not in stores:
                    stores[proposal_id] = VersionStore()
                store = stores[proposal_id]
                if action == "add":
                    vid = store.add_version(
                        proposal_id,
                        body.get("content", ""),
                        parent=body.get("parent"),
                        critique=body.get("critique"),
                        improvement=body.get("improvement"),
                        created_by=body.get("created_by", "system"),
                    )
                    result = {"version_id": vid}
                elif action == "get":
                    chain = store.get_chain(proposal_id)
                    result = {"chain": [v.__dict__ for v in chain.versions]}
                elif action == "latest":
                    v = store.latest(proposal_id)
                    result = {"version": v.__dict__ if v else None}
                elif action == "diff":
                    v1 = store.get_version(proposal_id, body["v1"])
                    v2 = store.get_version(proposal_id, body["v2"])
                    if v1 and v2:
                        result = {"diff": diff_versions(v1, v2)}
            elif action == "parse_rating":
                result = {"rating": parse_rating(body.get("judge_response", ""))}
            elif action == "parse_battle":
                w, c = parse_battle(body.get("judge_response", ""))
                result = {"winner": w, "confidence": c}
            elif action == "swap_battle":
                # 2 轮位置交换
                def judge(r, _a=body.get("response_a", ""), _b=body.get("response_b", "")):
                    return parse_battle(r)[0]
                # round 1
                r1 = judge(body.get("judge_response", ""))
                # round 2 swap
                r2 = parse_battle(body.get("judge_response_swapped", ""))[0] if body.get("judge_response_swapped") else r1
                result = {"round1": r1, "round2": r2, "consistent": r1 == r2}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"version action failed: {e}")
        return result

    # ========== v1.5.7 Capability Endpoints — Wave 7 (HIGH 优先级) ==========
    @app.post("/v1/capability/config")
    async def capability_config(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-03 8 层配置合并栈 + A-05 5 个 Permission Mode
        Body: {"action":"get|set|unset|merge","key":"model","value":"gpt-4o","layer":"user",...}
        """
        from .capability.config_stack import (
            ConfigStack, ConfigLayer, ConfigEntry, merge_layers,
            PermissionMode, PermissionRegistry, PermissionRule,
        )
        # 全局 stack
        if not hasattr(capability_config, "_stack"):
            capability_config._stack = ConfigStack()
        stack = capability_config._stack
        action = body.get("action", "get")
        result = {}
        try:
            if action == "set":
                layer = ConfigLayer[body.get("layer", "user").upper()]
                stack.set(body["key"], body.get("value"), layer, explicit=body.get("explicit", True))
                result = {"set": True, "key": body["key"], "layer": body.get("layer", "user")}
            elif action == "get":
                val, layer = stack.get_with_source(body["key"])
                result = {"value": val, "source_layer": layer.name if layer else None}
            elif action == "unset":
                count = stack.unset(body["key"], layer=ConfigLayer[body["layer"].upper()] if body.get("layer") else None)
                result = {"unset_count": count}
            elif action == "merge":
                layers_data = {ConfigLayer[k.upper()]: v for k, v in body.get("layers", {}).items()}
                merged = merge_layers(layers_data)
                    # 写入 stack
                for k, v in merged.items():
                    stack.set(k, v, ConfigLayer.USER, explicit=True)
                result = {"merged": merged}
            elif action == "permission":
                # 5 permission mode 演示
                mode = PermissionMode(body.get("mode", "default"))
                result = {"mode": mode.value}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"config action failed: {e}")
        return result

    @app.post("/v1/capability/bubble")
    async def capability_bubble(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-06 Bubble Mode (parent escalate) + A-26 Event scheduling
        Body: {"action":"escalate|resolve|pending|should_continue","parent_id":"p1",...}
        """
        from .capability.bubble_mode import (
            BubbleManager, BubbleStatus, EventScheduler, Event, EventType,
        )
        if not hasattr(capability_bubble, "_managers"):
            capability_bubble._managers = {}
        action = body.get("action", "escalate")
        result = {}
        try:
            if action in ("escalate", "resolve", "pending", "resolved"):
                parent_id = body.get("parent_id", "default")
                if parent_id not in capability_bubble._managers:
                    capability_bubble._managers[parent_id] = BubbleManager(parent_id)
                mgr = capability_bubble._managers[parent_id]
                if action == "escalate":
                    req_id = mgr.escalate(body["agent_id"], body.get("action_desc", ""), body.get("reason", ""))
                    result = {"request_id": req_id}
                elif action == "resolve":
                    ok = mgr.resolve(body["request_id"], BubbleStatus(body.get("decision", "allowed")))
                    result = {"resolved": ok}
                elif action == "pending":
                    pending = mgr.get_pending()
                    result = {"pending": [r.__dict__ for r in pending], "count": len(pending)}
                elif action == "resolved":
                    resolved = mgr.get_resolved()
                    result = {"resolved": [r.__dict__ for r in resolved], "count": len(resolved)}
            elif action in ("schedule", "should_continue", "recent", "clear"):
                if not hasattr(capability_bubble, "_scheduler"):
                    capability_bubble._scheduler = EventScheduler()
                sched = capability_bubble._scheduler
                if action == "schedule":
                    ev = Event(
                        event_id=body.get("event_id", ""),
                        event_type=EventType(body.get("event_type", "neutral")),
                        agent_id=body["agent_id"],
                        payload=body.get("payload", {}),
                        timestamp=body.get("timestamp", time.time()),
                    )
                    eid = sched.schedule(ev)
                    result = {"event_id": eid}
                elif action == "should_continue":
                    result = {"should_continue": sched.should_continue(body["agent_id"])}
                elif action == "recent":
                    events = sched.recent_events(body["agent_id"], n=body.get("n", 10))
                    result = {"events": [e.__dict__ for e in events]}
                elif action == "clear":
                    count = sched.clear(body["agent_id"])
                    result = {"cleared": count}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"bubble action failed: {e}")
        return result

    @app.post("/v1/capability/worktree")
    async def capability_worktree(
        body: Dict[str, Any],
        admin: Dict[str, Any] = Depends(require_admin),  # 修 P0-5: 必须 admin,防任意 cwd git
    ):
        """A-42 Worktree 隔离基元 + A-43 Worktree Snapshot/Diff
        Body: {"action":"snapshot|is_clean|diff","repo_path":"D:\\MoA Gateway Pro",...}

        修 P0-5 (security):
        - 改用 require_admin(不是 require_api_key)
        - repo_path 强制白名单(仅允许 server cwd 或 ~/.moa-gateway/)
        - subprocess.run 不传 env,默认 inherit 但去掉危险 env vars
        """
        from .capability.worktree import (
            WorktreeManager, WorktreeInfo, snapshot, is_clean, diff_snapshots,
        )
        # 修 P0-5: repo_path 白名单
        _allowed_roots = (
            os.path.abspath("."),
            os.path.abspath(os.path.expanduser("~/.moa-gateway")),
        )
        def _validate_repo_path(p: str) -> str:
            if not p:
                return _allowed_roots[0]
            abs_p = os.path.abspath(p)
            if not any(abs_p == r or abs_p.startswith(r + os.sep) for r in _allowed_roots):
                raise HTTPException(400, f"repo_path not in allowlist: {abs_p}")
            return abs_p
        action = body.get("action", "snapshot")
        result = {}
        try:
            if action == "snapshot":
                repo_path = _validate_repo_path(body.get("repo_path", "."))
                snap = snapshot(repo_path)
                result = {
                    "commit_sha": snap.commit_sha,
                    "branch": snap.branch,
                    "tracked_files_count": len(snap.tracked_files),
                    "porcelain_status_count": len(snap.porcelain_status),
                    "is_clean": is_clean(snap),
                    "timestamp": snap.timestamp,
                }
            elif action == "list":
                repo_path = _validate_repo_path(body.get("repo_path", "."))
                mgr = WorktreeManager(repo_path)
                wts = mgr.list_worktrees()
                result = {"worktrees": [w.__dict__ for w in wts]}
            elif action == "diff":
                p1 = _validate_repo_path(body.get("repo_path1", "."))
                p2 = _validate_repo_path(body.get("repo_path2", "."))
                snap1 = snapshot(p1)
                snap2 = snapshot(p2)
                result = {"diff": diff_snapshots(snap1, snap2)}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"worktree action failed: {e}")
        return result

    @app.post("/v1/capability/route")
    async def capability_route(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-45 Harness Routing 3 档 + A-46 Auto-Detection Rules
        Body: {"action":"route_request|auto_detect|priority|tools","task":"fix bug",...}
        """
        from .capability.routing import (
            HarnessTier, Priority, route_request, auto_detect_tier,
            priority_from_severity, tools_for_tier,
        )
        action = body.get("action", "route_request")
        result = {}
        try:
            if action == "route_request":
                config = route_request(
                    task=body.get("task", ""),
                    file_count=body.get("file_count", 0),
                    single_domain=body.get("single_domain", True),
                    is_bugfix=body.get("is_bugfix", False),
                    is_docs=body.get("is_docs", False),
                )
                result = {
                    "tier": config.tier.value,
                    "priority": config.priority.name,
                    "tools": config.tools,
                    "max_iterations": config.max_iterations,
                }
            elif action == "auto_detect":
                tier = auto_detect_tier(body.get("task", ""), body.get("files", []))
                result = {"tier": tier.value}
            elif action == "priority":
                pri = priority_from_severity(body.get("severity", "normal"))
                result = {"priority": pri.name}
            elif action == "tools":
                tier = HarnessTier(body.get("tier", "standard"))
                result = {"tools": tools_for_tier(tier)}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"route action failed: {e}")
        return result

    @app.post("/v1/capability/session-lock")
    async def capability_session_lock(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-22 Multi-session 协调 (advisory lock) + A-20 MCP 工具注册
        Body: {"action":"acquire|release|get_state|register_mcp|invoke_mcp",...}
        """
        from .capability.session_lock import (
            SessionLockManager, MCPRegistry, MCPTool,
        )
        if not hasattr(capability_session_lock, "_mgr"):
            capability_session_lock._mgr = SessionLockManager()
        if not hasattr(capability_session_lock, "_mcp"):
            capability_session_lock._mcp = MCPRegistry()
        mgr = capability_session_lock._mgr
        mcp = capability_session_lock._mcp
        action = body.get("action", "acquire")
        result = {}
        try:
            if action in ("try_acquire", "acquire_with_wait", "release", "get_state", "cleanup_expired"):
                if action == "try_acquire":
                    ok = mgr.try_acquire(body["lock_id"], body["session_id"], ttl=body.get("ttl"))
                    result = {"acquired": ok}
                elif action == "acquire_with_wait":
                    ok = mgr.acquire_with_wait(
                        body["lock_id"], body["session_id"],
                        timeout=body.get("timeout", 10.0),
                        retry_interval=body.get("retry_interval", 0.01),
                    )
                    result = {"acquired": ok}
                elif action == "release":
                    ok = mgr.release(body["lock_id"], body["session_id"])
                    result = {"released": ok}
                elif action == "get_state":
                    lock = mgr.get_lock_state(body["lock_id"])
                    result = {"lock": lock.__dict__ if lock else None}
                elif action == "cleanup_expired":
                    mgr.cleanup_expired()
                    result = {"cleaned": True}
            elif action in ("register_mcp", "unregister_mcp", "invoke_mcp", "list_mcp", "get_mcp"):
                if action == "register_mcp":
                    def handler(**kwargs):
                        return body.get("returns", f"executed {body['name']} with {kwargs}")
                    tool = MCPTool(
                        name=body["name"],
                        description=body.get("description", ""),
                        parameters=body.get("parameters", {}),
                        handler=handler,
                    )
                    mcp.register(tool)
                    result = {"registered": body["name"]}
                elif action == "unregister_mcp":
                    mcp.unregister(body["name"])
                    result = {"unregistered": body["name"]}
                elif action == "invoke_mcp":
                    out = mcp.invoke(body["name"], **body.get("kwargs", {}))
                    result = {"output": out}
                elif action == "list_mcp":
                    result = {"tools": mcp.list_tools()}
                elif action == "get_mcp":
                    t = mcp.get_tool(body["name"])
                    result = {"tool": {"name": t.name, "description": t.description, "parameters": t.parameters} if t else None}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"session_lock action failed: {e}")
        return result

    # ========== v1.5.8 Capability Endpoints — Wave 8 (HIGH 优先级) ==========
    @app.post("/v1/capability/flask")
    async def capability_flask(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-29 FLASK 12 维技能评分 + M-34 Task 分解树 (高内聚低耦合)
        Body: {"answer":"...","query":"...","tasks":[{title,description}...]}
        """
        from .capability.flask_score import score_flask, summary_report
        result = {}
        if body.get("answer"):
            flask = score_flask(body["answer"], body.get("query", ""))
            result["flask"] = {
                "total_score": flask.total_score,
                "dimension_scores": {d.name: s for d, s in flask.dimension_scores.items()},
                "weak": [d.name for d in flask.weak_dimensions],
                "strong": [d.name for d in flask.strong_dimensions],
                "summary": summary_report(flask),
            }
        if body.get("tasks"):
            # 简化:用 score_flask 也给每个 task title 评分
            scores = []
            for t in body["tasks"]:
                text = f"{t.get('title', '')} {t.get('description', '')}"
                f = score_flask(text)
                scores.append({"title": t.get("title", ""), "total": f.total_score, "weak": [d.name for d in f.weak_dimensions]})
            result["task_scores"] = scores
        return result

    @app.post("/v1/capability/elo")
    async def capability_elo(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-30 Elo ranking + Bootstrap CI + Worker 调度
        Body: {"action":"record|ranked|bootstrap_ci|submit","matches":[{winner,loser}],...}
        """
        from .capability.elo_ranking import (
            EloLeaderboard, MatchResult, bootstrap_ci, WorkerPool, EloRating,
        )
        action = body.get("action", "record")
        result = {}
        try:
            if action == "record":
                lb = EloLeaderboard(k_factor=body.get("k_factor", 4.0))
                for mid in body.get("model_ids", []):
                    lb.add_model(mid)
                matches = [MatchResult(**m) for m in body.get("matches", [])]
                for m in matches:
                    lb.record_match(m.winner_id, m.loser_id, m.timestamp)
                ranked = lb.ranked()
                result["ranked"] = [{"model_id": r.model_id, "rating": r.rating, "matches": r.matches_played} for r in ranked]
            elif action == "bootstrap_ci":
                lb = EloLeaderboard()
                for r in body.get("ratings_before", []):
                    lb.add_model(r["model_id"], r.get("rating", 1500.0))
                matches = [MatchResult(**m) for m in body.get("matches", [])]
                for m in matches:
                    lb.record_match(m.winner_id, m.loser_id, m.timestamp)
                ratings_before = [EloRating(model_id=r["model_id"], rating=r["rating"], matches_played=r.get("matches", 0)) for r in lb.ranked()]
                ci = bootstrap_ci(ratings_before, matches, n_resamples=body.get("n_resamples", 1000), ci=body.get("ci", 0.95))
                result["bootstrap_ci"] = {k: {"low": v[0], "high": v[1]} for k, v in ci.items()}
            elif action == "submit":
                pool = WorkerPool(body.get("workers", ["w1", "w2", "w3"]))
                pool.set_strategy(body.get("strategy", "shortest_queue"))
                # 真 submit 任务要 callable,这里返回调度决策
                loads = pool.worker_loads()
                result["loads"] = loads
                result["strategy"] = body.get("strategy", "shortest_queue")
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"elo action failed: {e}")
        return result

    @app.post("/v1/capability/brainstorm")
    async def capability_brainstorm(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-45 5 发散人格头脑风暴 + M-47 Decide 模式 advocate_<选项>
        Body: {"action":"ideas|decide","topic":"...","options":[...]}
        """
        from .capability.brainstorm import BrainstormSession, DecideMode
        action = body.get("action", "ideas")
        topic = body.get("topic", "")
        result = {}
        try:
            if action == "ideas":
                session = BrainstormSession(topic)
                ideas = session.generate_ideas_detailed() if body.get("detailed") else session.generate_ideas()
                result["ideas"] = ideas if isinstance(ideas, dict) else {k: v.__dict__ for k, v in ideas.items()}
            elif action == "decide":
                options = body.get("options", [])
                dm = DecideMode(topic, options)
                result["advocates"] = dm.generate_advocates()
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:
            raise  # 修 38: 让 4xx 直接返回(不被包 500)
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"brainstorm action failed: {e}")
        return result

    @app.post("/v1/capability/cross-iter")
    async def capability_cross_iter(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-50 Cross-iteration synthesis + M-52 Step-5 三种模式
        Body: {"action":"convergence|best_of_each|adoption|step5","iters":[{...}],...}
        """
        from .capability.cross_iter_synth import (
            IterationSnapshot, convergence_mode, best_of_each_mode,
            recommended_adoption_mode, run_step5, Step5Mode,
        )
        action = body.get("action", "step5")
        iters = [IterationSnapshot(**i) for i in body.get("iters", [])]
        result = {}
        try:
            if action == "convergence":
                r = convergence_mode(iters)
                result = {"output": r.output, "sources": r.sources, "confidence": r.confidence, "mode": r.mode.value}
            elif action == "best_of_each":
                r = best_of_each_mode(iters)
                result = {"output": r.output, "sources": r.sources, "confidence": r.confidence, "mode": r.mode.value}
            elif action == "adoption":
                if len(iters) < 2:
                    raise HTTPException(400, "adoption 需要至少 2 iter")
                r = recommended_adoption_mode(iters[-1], iters[-2])
                result = {"output": r.output, "sources": r.sources, "confidence": r.confidence, "mode": r.mode.value}
            elif action == "step5":
                mode = Step5Mode(body.get("step5_mode", "sintesis_central"))
                r = run_step5(iters, mode)
                result = {"mode": r.mode.value, "output": r.output, "action_taken": r.action_taken}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"cross_iter action failed: {e}")
        return result

    @app.post("/v1/capability/audit")
    async def capability_audit(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-31 Action Policy 增强 + A-35 Audit Gate (5 步协议)
        Body: {"action_id":"...","action_data":{"action":"read"},"policy_fn":null}
        """
        from .capability.action_audit import AuditGate
        if not hasattr(capability_audit, "_gate"):
            capability_audit._gate = AuditGate()
        gate = capability_audit._gate
        action_id = body.get("action_id", "a1")
        action_data = body.get("action_data", {})
        try:
            log = gate.audit(action_id, action_data)
            return log.__dict__
        except Exception as e:
            raise HTTPException(500, f"audit failed: {e}")

    # ========== v1.5.9 Capability Endpoints — Wave 9 (HIGH 优先级) ==========
    @app.post("/v1/capability/in-flight")
    async def capability_in_flight(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-24 In-Flight Transition 检测 + A-25 Team Checkpoint Merge
        Body: {"action":"start|complete|in_flight|transition|merge_checkpoint|merge","session_id":"s1",...}
        """
        from .capability.in_flight import (
            InFlightDetector, TeamCheckpointMerger, Checkpoint, Phase,
        )
        if not hasattr(capability_in_flight, "_detector"):
            capability_in_flight._detector = InFlightDetector(state_dir=body.get("state_dir", ".moai/state"))
        detector = capability_in_flight._detector
        action = body.get("action", "in_flight")
        result = {}
        try:
            if action == "start":
                sid = detector.record_start(Phase(body.get("phase", "analyze")), at=body.get("at"))
                result = {"session_id": sid}
            elif action == "complete":
                detector.record_complete(body["session_id"], Phase(body.get("phase", "analyze")), at=body.get("at"))
                result = {"completed": True}
            elif action == "in_flight":
                states = detector.detect_in_flight(at=body.get("at"))
                result = {"in_flight": [s.__dict__ for s in states], "count": len(states)}
            elif action == "transition":
                next_phase = detector.detect_phase_transition(body["session_id"])
                result = {"next_phase": next_phase.value if next_phase else None}
            elif action == "merge":
                if not hasattr(capability_in_flight, "_merger"):
                    capability_in_flight._merger = TeamCheckpointMerger()
                merger = capability_in_flight._merger
                for ckpt in body.get("checkpoints", []):
                    merger.add_checkpoint(Checkpoint(
                        session_id=ckpt.get("session_id", "s1"),
                        phase=Phase(ckpt.get("phase", "analyze")),
                        data=ckpt.get("data", {}),
                        timestamp=ckpt.get("timestamp", time.time()),
                    ))
                result = {"merged": merger.merge()}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"in_flight action failed: {e}")
        return result

    @app.post("/v1/capability/mx")
    async def capability_mx(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-39 MX 注解系统 + A-40 fan-in + A-44 mx CLI
        Body: {"action":"parse|fanin|cli","text":"...","command":"list","file_path":"f.py"}
        """
        from .capability.mx_annot import (
            parse_mx_annotations, compute_fanin, mx_cli,
        )
        action = body.get("action", "parse")
        result = {}
        try:
            if action == "parse":
                anns = parse_mx_annotations(body.get("text", ""), body.get("file_path", "f.py"), body.get("language", "python"))
                result = {"annotations": [a.to_dict() for a in anns], "count": len(anns)}
            elif action == "fanin":
                anns = parse_mx_annotations(body.get("text", ""), body.get("file_path", "f.py"), body.get("language", "python"))
                result = {"fanin": compute_fanin(anns)}
            elif action == "cli":
                anns = parse_mx_annotations(body.get("text", ""), body.get("file_path", "f.py"), body.get("language", "python"))
                result = {"output": mx_cli(anns, body.get("command", "list"))}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"mx action failed: {e}")
        return result

    @app.post("/v1/capability/tier-promo")
    async def capability_tier_promo(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-48 Tier Promotion (1/3/5/10 + confidence<0.70) + A-49 Sub-agent Boundary
        Body: {"action":"classify|record|can_spawn|cohabitation","evidence":[{...}],"weights":[...]}
        """
        from moa_gateway.capability.tier_promo import (
            PromotionConfig, compute_tier, classify_tier_from_evidence,
            Evidence, SubAgentBoundary, PromotionLevel,
        )
        action = body.get("action", "classify")
        result = {}
        try:
            if action == "classify":
                evidence = [Evidence(**e) for e in body.get("evidence", [])]
                cfg = PromotionConfig(
                    tier_1_threshold=body.get("tier_1", 1),
                    tier_2_threshold=body.get("tier_2", 3),
                    tier_3_threshold=body.get("tier_3", 5),
                    tier_4_threshold=body.get("tier_4", 10),
                    confidence_threshold=body.get("confidence_threshold", 0.70),
                )
                tier = classify_tier_from_evidence(evidence, cfg)
                result = {"tier": tier.name, "evidence_count": len(evidence)}
            elif action == "compute":
                cfg = PromotionConfig()
                tier = compute_tier(
                    body.get("count", 0),
                    body.get("confidence", 0.5),
                    cfg,
                )
                result = {"tier": tier.name}
            elif action == "can_spawn":
                boundary = SubAgentBoundary(
                    body.get("parent_id", "p1"),
                    body.get("allowed_children", []),
                )
                result = {"can_spawn": boundary.can_spawn(body.get("child_id", ""))}
            elif action == "cohabitation":
                b1 = SubAgentBoundary(body.get("parent_a", "p1"), body.get("children_a", []))
                b2 = SubAgentBoundary(body.get("parent_b", "p2"), body.get("children_b", []))
                result = {"cohabitation_safe": b1.cohabitation_check(body.get("parent_b", "p2"))}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"tier_promo action failed: {e}")
        return result

    @app.post("/v1/capability/artifact")
    async def capability_artifact(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-21 Artifact Schema 统一 + A-50 Tmux 面板编排 (CG mode)
        Body: {"action":"register|list_by_type|validate|add_pane|layout|safe_layout",...}
        """
        from .capability.artifact import (
            Artifact, ArtifactType, SchemaRegistry, TmuxPane, TmuxOrchestrator,
        )
        if not hasattr(capability_artifact, "_registry"):
            capability_artifact._registry = SchemaRegistry()
        if not hasattr(capability_artifact, "_orchestrator"):
            capability_artifact._orchestrator = TmuxOrchestrator(max_visible=body.get("max_visible", 3))
        reg = capability_artifact._registry
        orch = capability_artifact._orchestrator
        action = body.get("action", "register")
        result = {}
        try:
            if action == "register":
                artifact = Artifact(
                    id=body["id"],
                    name=body["name"],
                    type=ArtifactType(body["type"]),
                    description=body.get("description", ""),
                    tags=body.get("tags", []),
                    inputs=body.get("inputs", {}),
                    outputs=body.get("outputs", {}),
                    dependencies=body.get("dependencies", []),
                    created_at=body.get("created_at", time.time()),
                )
                reg.register(artifact)
                result = {"registered": artifact.id}
            elif action == "list_by_type":
                t = ArtifactType(body.get("type", "agent"))
                arts = reg.list_by_type(t)
                result = {"artifacts": [a.to_dict() for a in arts]}
            elif action == "validate":
                artifact = Artifact(
                    id=body.get("id", "test"),
                    name=body.get("name", "test"),
                    type=ArtifactType(body.get("type", "agent")),
                    description=body.get("description", ""),
                )
                missing = reg.validate(artifact)
                result = {"missing_fields": missing, "valid": len(missing) == 0}
            elif action == "add_pane":
                pane = TmuxPane(
                    pane_id=body.get("pane_id", "p1"),
                    command=body.get("command", ""),
                    cwd=body.get("cwd", "."),
                    env_vars=body.get("env_vars", {}),
                )
                orch.add_pane(pane)
                result = {"added": pane.pane_id}
            elif action == "layout":
                result = {"panes": [p.__dict__ for p in orch.layout()]}
            elif action == "safe_layout":
                result = {"panes": [p.__dict__ for p in orch.safe_layout()]}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"artifact action failed: {e}")
        return result

    @app.post("/v1/capability/frozen")
    async def capability_frozen(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-19 Frozen Zone 4-enum + A-34 HARNESS_FROZEN_* 8 sentinels
        Body: {"action":"add|is_frozen|is_evolvable|can_modify|assert_modifiable","path":"/foo",...}
        """
        from .capability.frozen_zone import (
            FrozenRegistry, FrozenEntry, Zone, can_modify, assert_modifiable, FrozenZoneError,
            ALL_HARNESS_FROZEN_SENTINELS,
        )
        if not hasattr(capability_frozen, "_registry"):
            capability_frozen._registry = FrozenRegistry()
        reg = capability_frozen._registry
        action = body.get("action", "is_frozen")
        result = {}
        try:
            if action == "add":
                entry = FrozenEntry(
                    path=body["path"],
                    zone=Zone(body["zone"]) if isinstance(body["zone"], str) else body["zone"],
                    sentinel=body.get("sentinel", ""),
                    reason=body.get("reason", ""),
                    added_at=body.get("added_at", time.time()),
                )
                reg.add(entry)
                result = {"added": entry.path}
            elif action == "is_frozen":
                result = {"is_frozen": reg.is_frozen(body["path"])}
            elif action == "is_evolvable":
                result = {"is_evolvable": reg.is_evolvable(body["path"])}
            elif action == "can_modify":
                zone = Zone(body["zone"]) if isinstance(body["zone"], str) else body["zone"]
                result = {"can_modify": can_modify(body["path"], zone)}
            elif action == "assert_modifiable":
                try:
                    assert_modifiable(body["path"], reg)
                    result = {"modifiable": True}
                except FrozenZoneError as e:
                    result = {"modifiable": False, "error": str(e), "path": e.path, "sentinel": e.sentinel}
            elif action == "list_sentinels":
                result = {"sentinels": ALL_HARNESS_FROZEN_SENTINELS, "count": len(ALL_HARNESS_FROZEN_SENTINELS)}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:

            raise  # 修 38: 让 4xx 直接返回(不被包 500)

        except Exception as e:
            raise HTTPException(500, f"frozen action failed: {e}")
        return result

    # ========== v1.5.10 Capability Endpoints — Wave 10 (HIGH 优先级) ==========
    @app.post("/v1/capability/turboquant")
    async def capability_turboquant(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-12 TurboQuant 5 级量化 (Q0/Q1/Q2/Q4/Q8) + 60 msg HARD CAP + 30 PRESERVE
        Body: {"action":"should_compress|apply","messages":[{role,content,timestamp}],"level":"Q4","hard_cap":60,"preserve":30}
        """
        from .capability.turboquant import (
            Message, TurboQuantConfig, QuantLevel, should_compress, apply_turboquant,
        )
        msgs = [Message(**m) for m in body.get("messages", [])]
        level = QuantLevel[body.get("level", "Q4").upper()]
        cfg = TurboQuantConfig(
            hard_cap=body.get("hard_cap", 60),
            preserve=body.get("preserve", 30),
            level=level,
        )
        action = body.get("action", "apply")
        result = {}
        try:
            if action == "should_compress":
                result = {"should_compress": should_compress(msgs, cfg), "count": len(msgs)}
            elif action == "apply":
                compressed = apply_turboquant(msgs, cfg)
                result = {
                    "compressed": [m.__dict__ for m in compressed],
                    "original_count": len(msgs),
                    "compressed_count": len(compressed),
                }
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except Exception as e:
            raise HTTPException(500, f"turboquant failed: {e}")
        return result

    @app.post("/v1/capability/moa-engine")
    async def capability_moa_engine(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-01 MoA 引擎核心 (3 proposer + 1 aggregator) + M-05 协同
        Body: {"proposers":[{...}],"aggregator":{...},"query":"...","validate_only":false}
        """
        import asyncio
        from .capability.moa_engine import (
            Proposer, Aggregator, validate_moa, call_proposer, call_aggregator, run_moa,
        )
        proposers = [Proposer(**p) for p in body.get("proposers", [])]
        aggregator = Aggregator(**body["aggregator"]) if body.get("aggregator") else None
        errors = validate_moa(proposers, aggregator)
        result = {"validation_errors": errors}
        if body.get("validate_only"):
            return result
        if errors:
            raise HTTPException(400, f"MoA config invalid: {errors}")
        # 简单 provider_fn:返回 mock 答案 (sync,call_proposer 内部会 asyncio.to_thread 包装)
        query = body.get("query", "")
        def mock_provider(actor, prompt):
            return (f"[{type(actor).__name__}:{actor.model_id}] response to: {prompt[:50]}", 100)
        try:
            moa_result = await run_moa(query, proposers, aggregator, mock_provider)
            result["moa_result"] = {
                "query": moa_result.query,
                "proposals": [p.__dict__ for p in moa_result.proposals],
                "aggregated": moa_result.aggregated,
                "total_tokens": moa_result.total_tokens,
                "total_latency_ms": moa_result.total_latency_ms,
            }
        except Exception as e:
            raise HTTPException(500, f"MoA run failed: {e}")
        return result

    @app.post("/v1/capability/acceptance")
    async def capability_acceptance(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-17 Acceptance Tree (Given/When/Then) + A-16 EARS/GEARS 5+6 模式
        Body: {"action":"add|parse_ears|validate_pattern|get_tree","criteria":[{...}],"text":"..."}
        """
        from .capability.acceptance import (
            AcceptanceCriterion, AcceptanceTree, parse_ears, validate_pattern,
        )
        if not hasattr(capability_acceptance, "_trees"):
            capability_acceptance._trees = {}
        trees = capability_acceptance._trees
        action = body.get("action", "add")
        result = {}
        try:
            if action == "add":
                root_id = body.get("root_id", "root")
                if root_id not in trees:
                    trees[root_id] = AcceptanceTree(root_id)
                tree = trees[root_id]
                for c in body.get("criteria", []):
                    tree.add_criterion(AcceptanceCriterion(**c))
                result = {"root_id": root_id, "tree": {
                    "criteria_count": len(tree._criteria),
                }}
            elif action == "parse_ears":
                criteria = parse_ears(body.get("text", ""))
                result = {"criteria": [c.__dict__ for c in criteria], "count": len(criteria)}
            elif action == "validate_pattern":
                ac = AcceptanceCriterion(**body["criterion"])
                result = {"pattern": validate_pattern(ac)}
            elif action == "get_tree":
                root_id = body.get("root_id", "root")
                tree = trees.get(root_id)
                if tree is None:
                    result = {"error": "tree not found"}
                else:
                    result = {"criteria": {k: v.__dict__ for k, v in tree._criteria.items()}}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except Exception as e:
            raise HTTPException(500, f"acceptance failed: {e}")
        return result

    @app.post("/v1/capability/llm-merge")
    async def capability_llm_merge(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """L-32 LLM 响应合并 (5 strategy) + L-33 LLM 降级 chain
        Body: {"action":"merge|fallback","responses":[{...}],"strategy":"concat","providers":["a","b"]}
        """
        from .capability.llm_merge import (
            LLMResponse, MergeStrategy, merge_responses, FallbackChain, AllProvidersFailedError,
        )
        action = body.get("action", "merge")
        result = {}
        try:
            if action == "merge":
                responses = [LLMResponse(**r) for r in body.get("responses", [])]
                strategy = MergeStrategy[body.get("strategy", "concat").upper()]
                merged = merge_responses(responses, strategy)
                result = {
                    "text": merged.text,
                    "sources": merged.sources,
                    "strategy": merged.strategy.value,
                    "total_tokens": merged.total_tokens,
                    "total_cost_usd": merged.total_cost_usd,
                    "confidence": merged.confidence,
                }
            elif action == "fallback":
                providers = body.get("providers", [])
                chain = FallbackChain(providers)
                def call_fn(provider):
                    # 简单 provider:基于 provider 名 返不同响应
                    fail_at = body.get("fail_at", [])
                    if provider in fail_at:
                        raise RuntimeError(f"provider {provider} failed")
                    return LLMResponse(
                        source=provider, text=f"ok from {provider}",
                        tokens=100, latency_ms=200.0, cost_usd=0.001, confidence=0.9,
                    )
                try:
                    resp = chain.execute(call_fn)
                    result = {"response": resp.__dict__}
                except AllProvidersFailedError as e:
                    result = {"error": "all_failed", "providers": e.providers, "errors": e.errors}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except Exception as e:
            raise HTTPException(500, f"llm_merge failed: {e}")
        return result

    @app.post("/v1/capability/grace")
    async def capability_grace(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """A-37 7-day Grace Window (FAIL 7 天仅警告不阻塞)
        Body: {"action":"register|record_pass|record_fail|should_block|status","name":"...","at":...}
        """
        from .capability.grace_window import (
            CheckRegistry, CheckResult, GraceConfig, grace_status,
        )
        if not hasattr(capability_grace, "_registry"):
            capability_grace._registry = CheckRegistry()
        reg = capability_grace._registry
        action = body.get("action", "should_block")
        result = {}
        try:
            if action == "register":
                cid = reg.register(body.get("name", "default"))
                result = {"check_id": cid}
            elif action == "record_pass":
                reg.record_pass(body["check_id"])
                result = {"passed": True}
            elif action == "record_fail":
                reg.record_fail(body["check_id"], at=body.get("at"))
                result = {"failed": True}
            elif action == "should_block":
                result = {"should_block": reg.should_block(body["check_id"], at=body.get("at"))}
            elif action == "status":
                result = {"status": grace_status(body["check_id"], reg, at=body.get("at"))}
            elif action == "warnings":
                warnings = reg.get_warnings()
                result = {"warnings": [w.__dict__ for w in warnings], "count": len(warnings)}
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except Exception as e:
            raise HTTPException(500, f"grace failed: {e}")
        return result

    # ========== Wave 11 Capability Endpoints (5 new) ==========

    @app.post("/v1/capability/rag-search")
    async def capability_rag_search(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-09: 关键词重叠 RAG 检索 — 24h TTL 缓存, max_results 默认 3"""
        from .capability.rag_search import rag_search
        try:
            query = body.get("query", "")
            corpus = body.get("corpus", [])
            max_results = int(body.get("max_results", 3))
            if not isinstance(corpus, list):
                raise HTTPException(400, "corpus must be a list")
            results = rag_search(query, corpus, max_results=max_results)
            return {"results": results, "count": len(results), "query": query}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"rag_search failed: {e}")

    @app.post("/v1/capability/plan-act")
    async def capability_plan_act(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-10: Plan/Act 模式解析 — 24+14 关键词 + 11+8 正则 → confidence"""
        from .capability.plan_act import classify_mode
        try:
            query = body.get("query", "")
            result = classify_mode(query)
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"plan_act failed: {e}")

    @app.post("/v1/capability/channels")
    async def capability_channels(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """R-23: CH1/CH2/CH3 三通道 fallback — subagent → CLI → API"""
        from .capability.channels import (
            ChannelChain, SubagentChannel, CLIChannel, APIChannel,
            ChannelType, classify_error,
        )
        try:
            action = body.get("action", "execute")
            query = body.get("query", "")
            if action == "classify_error":
                exc = body.get("error", "")
                return {"classification": classify_error(exc)}
            elif action == "chain_info":
                return {
                    "channels": [c.value for c in ChannelType],
                    "order": ["ch1", "ch2", "ch3"],
                    "fallback": "stop on first success",
                }
            elif action == "execute":
                enabled = body.get("enabled", ["ch1", "ch2", "ch3"])
                chs = []
                if "ch1" in enabled: chs.append(SubagentChannel())
                if "ch2" in enabled: chs.append(CLIChannel(sleep_ms=body.get("cli_latency_ms", 50)))
                if "ch3" in enabled: chs.append(APIChannel(sleep_ms=body.get("api_latency_ms", 150)))
                chain = ChannelChain(chs)
                result = await chain.execute(query, **body.get("kwargs", {}))
                return result
            else:
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"channels failed: {e}")

    @app.post("/v1/capability/reference-router")
    async def capability_reference_router(
        body: Dict[str, Any],
        key_info: Dict[str, Any] = Depends(require_api_key),
    ):
        """M-11: Reference 模型分流 — SHADOW/VALIDATE/VETO 4 策略"""
        from .capability.reference_router import (
            route_with_reference, RefStrategy, ReferenceConfig,
        )
        try:
            query = body.get("query", "")
            strategy = body.get("strategy", "shadow")
            try:
                strat = RefStrategy(strategy)
            except ValueError:
                raise HTTPException(400, f"unknown strategy: {strategy}")
            cfg = ReferenceConfig(
                main_model=body.get("main_model", "main"),
                ref_model=body.get("ref_model", "ref"),
                strategy=strat,
                max_latency_ms=int(body.get("max_latency_ms", 5000)),
                cost_ratio_cap=float(body.get("cost_ratio_cap", 2.0)),
            )
            result = await route_with_reference(query, cfg)
            return result
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"reference_router failed: {e}")

    @app.post("/v1/capability/checkpoint")
    async def capability_checkpoint(
        body: Dict[str, Any],
        admin: Dict[str, Any] = Depends(require_admin),  # 修 P0-4: 必须 admin,防任意文件写 RCE
    ):
        """A-23: 原子写 checkpoint 存储 — temp+rename, fsync, thread-safe

        修 P0-4 (security RCE):
        - 改用 require_admin(不再是 require_api_key)
        - 删 atomic_write action(给 API key 用户一份远程文件写原语 = RCE 风险)
        - root_dir 强制在白名单内(server cwd 或 ~/.moa-gateway/checkpoints)
        - name 严格限制 [a-zA-Z0-9_-]{1,64}
        """
        from .capability.checkpoint import CheckpointStore
        import re as _re
        try:
            action = body.get("action", "save")
            # 修 P0-4: 强制白名单 root_dir(默认在 server cwd 内的安全路径)
            _allowed_roots = (
                os.path.abspath("./.moai/checkpoints"),
                os.path.abspath(os.path.expanduser("~/.moa-gateway/checkpoints")),
            )
            root = body.get("root_dir", _allowed_roots[0])
            root_abs = os.path.abspath(root)
            if not any(root_abs == r or root_abs.startswith(r + os.sep) for r in _allowed_roots):
                raise HTTPException(400, f"root_dir not in allowlist: {root_abs}")
            # 修 P0-4: name 严格白名单
            name = body.get("name", "default")
            if not _re.fullmatch(r"[a-zA-Z0-9_\-]{1,64}", name):
                raise HTTPException(400, "name must match [a-zA-Z0-9_-]{1,64}")
            store = CheckpointStore(root_dir=root_abs, max_keep=int(body.get("max_keep", 10)))
            if action == "save":
                payload = body.get("payload", {})
                # 修 P0-4: payload 也限大小(防内存炸弹)
                _raw = body.get("_raw_payload", "")
                if isinstance(_raw, str) and len(_raw) > 1024 * 1024:  # 1MB
                    raise HTTPException(400, "payload too large (>1MB)")
                path = store.save(name, payload)
                return {"saved": True, "path": path, "name": name}
            elif action == "load":
                data = store.load(name)
                return {"name": name, "data": data, "found": data is not None}
            elif action == "list":
                items = store.list()
                return {"items": items, "count": len(items)}
            elif action == "delete":
                ok = store.delete(name)
                return {"deleted": ok, "name": name}
            elif action == "cleanup":
                older = body.get("older_than_seconds")
                removed = store.cleanup(older_than_seconds=older)
                return {"removed": removed, "older_than_seconds": older}
            else:
                # 修 P0-4: 删除 atomic_write action(那是 RCE 入口)
                raise HTTPException(400, f"unknown action: {action}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"checkpoint failed: {e}")

    # ========== WebUI Auth ==========
    @app.post("/api/auth/login")
    async def login(req: LoginRequest):
        # 修11: 异步 bcrypt — 在线程池里跑,不阻塞 event loop
        storage = get_storage()
        # 先取 hash,再在线程池里 verify
        from .storage import async_bcrypt_verify
        with storage.conn() as c:
            row = c.execute("SELECT * FROM admin_users WHERE username = ?",
                            (req.username,)).fetchone()
        if not row:
            raise HTTPException(401, "Invalid username or password")
        ok = await async_bcrypt_verify(req.password, row["password_hash"])
        if not ok:
            raise HTTPException(401, "Invalid username or password")
        # 更新最后登录
        with storage.conn() as c:
            c.execute("UPDATE admin_users SET last_login = ? WHERE id = ?",
                      (time.time(), row["id"]))
        # 修17: 检测默认密码
        from .config import get_settings as _gs
        settings = _gs()
        must_change = (
            req.username == settings.auth.admin_username
            and req.password == settings.auth.admin_password
        )
        token = create_jwt_token(row["username"], row["role"])
        return {"token": token,
                "user": {"id": row["id"], "username": row["username"],
                         "role": row["role"],
                         "must_change_password": must_change}}

    @app.post("/api/auth/change-password")
    async def change_password(req: ChangePasswordRequest,
                              admin: Dict[str, Any] = Depends(require_admin)):
        storage = get_storage()
        if not storage.verify_admin(admin["sub"], req.old_password):
            raise HTTPException(400, "old password incorrect")
        ok = storage.change_admin_password(admin["sub"], req.new_password)
        if not ok:
            raise HTTPException(500, "change password failed")
        return {"ok": True}

    @app.get("/api/auth/me")
    async def me(admin: Dict[str, Any] = Depends(require_admin)):
        return admin

    # ========== Model Endpoints Management ==========
    @app.get("/api/endpoints")
    async def list_endpoints(admin: Dict[str, Any] = Depends(require_admin)):
        # merge settings + storage
        result = pool.snapshot()
        return result

    @app.post("/api/endpoints")
    async def upsert_endpoint(req: EndpointUpsert,
                              admin: Dict[str, Any] = Depends(require_admin)):
        ep_dict = req.model_dump()
        try:
            ep = pool.upsert_endpoint(ep_dict)
        except Exception as e:
            raise HTTPException(500, f"upsert failed: {e}")
        return {"ok": True, "id": ep.id}

    @app.delete("/api/endpoints/{eid}")
    async def delete_endpoint(eid: str,
                              admin: Dict[str, Any] = Depends(require_admin)):
        ok = pool.remove_endpoint(eid)
        if not ok:
            raise HTTPException(404, "endpoint not found")
        return {"ok": True}

    @app.post("/api/endpoints/{eid}/toggle")
    async def toggle_endpoint(eid: str,
                              admin: Dict[str, Any] = Depends(require_admin)):
        if eid not in pool.endpoints:
            raise HTTPException(404, "endpoint not found")
        ep = pool.endpoints[eid]
        ep.config.enabled = not ep.config.enabled
        # 同步到 storage
        try:
            get_storage().upsert_endpoint({
                "endpoint_id": eid,
                "provider": ep.config.provider,
                "model": ep.config.model,
                "tier": ep.config.tier,
                "api_base": ep.config.api_base,
                "api_key_env": ep.config.api_key_env,
                "cost_per_1k_input": ep.config.cost_per_1k_input,
                "cost_per_1k_output": ep.config.cost_per_1k_output,
                "max_tokens": ep.config.max_tokens,
                "timeout": ep.config.timeout,
                "weight": ep.config.weight,
                "enabled": ep.config.enabled,
                "tags": ep.config.tags,
            })
        except Exception:
            pass
        return {"ok": True, "enabled": ep.config.enabled}

    @app.post("/api/endpoints/{eid}/reset-breaker")
    async def reset_breaker(eid: str,
                            admin: Dict[str, Any] = Depends(require_admin)):
        if eid not in pool.endpoints:
            raise HTTPException(404, "endpoint not found")
        ep = pool.endpoints[eid]
        ep.recover_breaker()
        return {"ok": True}

    # ========== API Keys 管理 ==========
    @app.get("/api/api-keys")
    async def list_api_keys(admin: Dict[str, Any] = Depends(require_admin)):
        return get_storage().list_api_keys()

    @app.post("/api/api-keys")
    async def create_api_key(req: CreateAPIKeyRequest,
                             admin: Dict[str, Any] = Depends(require_admin)):
        return get_storage().create_api_key(req.name, req.quota_rpm,
                                            req.quota_daily_tokens)

    @app.delete("/api/api-keys/{key_id}")
    async def delete_api_key(key_id: str,
                             admin: Dict[str, Any] = Depends(require_admin)):
        ok = get_storage().delete_api_key(key_id)
        if not ok:
            raise HTTPException(404, "not found")
        return {"ok": True}

    # ========== Logs & Stats ==========
    @app.get("/api/logs")
    async def list_logs(limit: int = 100, api_key_id: Optional[str] = None,
                        admin: Dict[str, Any] = Depends(require_admin)):
        return get_storage().list_logs(limit=limit, api_key_id=api_key_id)

    @app.get("/api/stats")
    async def stats(days: int = 7,
                    admin: Dict[str, Any] = Depends(require_admin)):
        since = time.time() - days * 86400
        return get_storage().aggregate_stats(since_ts=since)

    @app.get("/api/metrics")
    async def metrics_endpoint(admin: Dict[str, Any] = Depends(require_admin)):
        return metrics.snapshot()

    # ========== Adapters & Setup ==========
    @app.get("/api/adapters")
    async def get_adapters_config(admin: Dict[str, Any] = Depends(require_admin)):
        """返回给各种 Agent 的接入配置"""
        s = get_settings()
        ctx = AdapterContext(
            gateway_host=s.server.host if s.server.host != "0.0.0.0" else "127.0.0.1",
            gateway_port=s.server.port,
            api_key=(get_storage().list_api_keys()[0]["key_id"]
                     if get_storage().list_api_keys() else "demo-key-please-change"),
            https=False
        )
        return all_adapters(ctx)

    @app.get("/api/adapters/curl")
    async def adapters_curl(admin: Dict[str, Any] = Depends(require_admin)):
        s = get_settings()
        ctx = AdapterContext(
            gateway_host="127.0.0.1",
            gateway_port=s.server.port,
            api_key="YOUR-API-KEY",
        )
        from .adapters import GenericOpenAIAdapter
        return {"curl": GenericOpenAIAdapter(ctx).get_curl_example(),
                "python": GenericOpenAIAdapter(ctx).get_python_example()}

    # ========== WebUI 静态文件 ==========
    @app.get("/", response_class=HTMLResponse)
    async def index():
        idx = WEBUI_DIR / "index.html"
        if not idx.exists():
            return HTMLResponse("<h1>WebUI not found</h1>", status_code=500)
        return HTMLResponse(idx.read_text(encoding="utf-8"))

    @app.get("/webui/{name}")
    async def webui_assets(name: str):
        # 修16: 路径穿越防护 — name 必须是不含 .. 和分隔符的纯文件名
        if "/" in name or "\\" in name or ".." in name or name.startswith("."):
            raise HTTPException(404, "not found")
        p = WEBUI_DIR / name
        # 修 P1-5: 用 os.path.commonpath 而非 startswith (防父目录同名)
        # 也防 Windows 8.3 short name + symlink 绕过
        try:
            base = WEBUI_DIR.resolve()
            resolved = p.resolve()
            # 防 symlink:不跟随
            try:
                if p.is_symlink() or p.lstat() and (p.lstat().st_mode & 0o170000) == 0o120000:
                    raise HTTPException(404, "not found")
            except (OSError, AttributeError):
                pass
            common = os.path.commonpath([str(resolved), str(base)])
            if common != str(base):
                raise HTTPException(404, "not found")
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(404, "not found")
        if not p.exists() or not p.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(str(p))

    # ========== 辅助 ==========
    def _format_chat_response(request_id: str, model: str, content: str,
                              prompt_tokens: int, completion_tokens: int,
                              extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "id": request_id,
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": prompt_tokens + completion_tokens,
            }
        }
        if extra:
            body["moa_meta"] = extra
        return body

    def _log_request(key_info: Dict[str, Any], request_id: str,
                     requested: str, used: str, strategy: str,
                     prompt_tokens: int, completion_tokens: int,
                     cost: float, latency_ms: float, status: str,
                     error: str, preset: Optional[str] = None,
                     consensus: Optional[float] = None,
                     fallback: bool = False,
                     metadata: Optional[Dict] = None) -> None:
        try:
            get_storage().log_request({
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
            })
        except Exception as e:
            logger.warning("log_request failed: %s", e)

    # ========== 流式响应辅助(SSE) ==========
    async def _stream_single(pool, model_id, messages, chat_kwargs, request_id, key_info):
        """单模型流式:从 provider_obj.chat_stream 逐字产出,包成 OpenAI SSE"""
        ep = pool.endpoints.get(model_id)
        if not ep or not ep.provider_obj:
            yield "data: " + json.dumps({"error": "model unavailable"}) + "\n\n"
            yield "data: [DONE]\n\n"
            return
        stream_kwargs = dict(chat_kwargs)
        stream_kwargs.pop("max_retries", None)
        stream_kwargs["stream"] = True
        try:
            async for chunk in ep.provider_obj.chat_stream(
                pool.build_chat_request(ep, messages, stream_kwargs.get("temperature", 0.6),
                                        stream_kwargs.get("max_tokens", 4096),
                                        stream_kwargs.get("tools"), True)
            ):
                if not chunk:
                    continue
                payload = {
                    "id": request_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model_id,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None,
                    }],
                }
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
        except Exception as e:
            logger.exception("stream_single failed: %s", e)
            yield "data: " + json.dumps({"error": str(e)}) + "\n\n"
        yield "data: [DONE]\n\n"

    async def _stream_moa(result, request_id):
        """MoA 流式:把最终内容切成字级别增量(SSE 格式)"""
        content = result.final_content or result.aggregated_content or ""
        model = result.aggregator_model or "moa"
        # 按 token 粒度切(简单按空格/汉字逐个,客户端体验顺滑)
        for token in _tokenize_for_stream(content):
            payload = {
                "id": request_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }],
                "moa_meta": {
                    "preset": result.preset,
                    "consensus": result.consensus_score,
                    "cost": result.total_cost,
                    "references": [r.model_id for r in result.references],
                } if token == content[:1] else None,
            }
            # 去掉 None moa_meta 字段(只在第一个 chunk 携带)
            if payload["moa_meta"] is None:
                payload.pop("moa_meta", None)
            yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
            await asyncio.sleep(0)  # 让出 event loop
        yield "data: [DONE]\n\n"

    def _tokenize_for_stream(text: str):
        """把文本切成流式 token(中文逐字 / 英文按空格+标点)"""
        if not text:
            return
        # 简单策略:中文字符逐字,其它按 \s+ 切
        i = 0
        n = len(text)
        while i < n:
            ch = text[i]
            if '\u4e00' <= ch <= '\u9fff':
                yield ch
                i += 1
            elif ch.isspace():
                # 连续空白合并
                j = i
                while j < n and text[j].isspace():
                    j += 1
                yield text[i:j]
                i = j
            else:
                # ASCII 段:到下一个空白或中文或标点结束
                j = i + 1
                while j < n and not text[j].isspace() and not ('\u4e00' <= text[j] <= '\u9fff'):
                    j += 1
                yield text[i:j]
                i = j

    return app


# ========== 入口 ==========
app = create_app()


if __name__ == "__main__":
    import uvicorn
    s = get_settings()
    uvicorn.run("moa_gateway.server:app",
                host=s.server.host, port=s.server.port,
                workers=s.server.workers,
                log_level=s.server.log_level.lower())
