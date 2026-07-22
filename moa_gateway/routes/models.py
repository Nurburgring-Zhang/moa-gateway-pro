"""OpenAI-compatible /v1/models endpoint."""
from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Request

from ..auth import authenticate_api_key
from ..model_pool import get_model_pool

logger = logging.getLogger(__name__)

router = APIRouter(tags=["models"])


@router.get("/v1/models")
async def list_models(request: Request):
    """OpenAI compatible: list available models"""
    pool = get_model_pool()
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
        out.append(
            {
                "id": mid,
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moa-gateway",
                "description": desc,
                "permission": [],
            }
        )
    if info:
        for ep in pool.endpoints.values():
            if ep.config.enabled:
                out.append(
                    {
                        "id": ep.id,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": ep.config.provider,
                        "description": f"{ep.config.provider} / {ep.config.model} / tier={ep.tier.value}",
                        "permission": [],
                    }
                )
    return {"object": "list", "data": out}
