"""Multimodal fanout endpoint (P3): one request -> N platforms in parallel.

POST /v1/multimodal/execute
    modality + platforms[] -> MultiModalFanout 并发执行 + 聚合 (all/fastest/best)

- 鉴权与其他多模态路由一致 (require_api_key)。
- 能力门禁按 modality 动态映射: image->image_gen, audio_tts->tts, audio_asr->stt
  (capability_toggles.is_enabled 直查, 禁用时 503)。
- X-MOA-Mock: 任一成功路由为 mock 时响应头标注 true (接入现有 mock_headers 体系)。
- 无密钥路由按 D6 策略: explicit->带标注 mock (若该模态有 mock provider),
  disabled->逐路 no_key 证据; 全部路由都不可执行时返回 503 (不冒充成功)。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import is_enabled
from ..multimodal_fanout import get_fanout
from ..ratelimit import get_limiter
from ..req_models import CreateMultimodalExecuteRequest

logger = logging.getLogger(__name__)
router = APIRouter(tags=["multimodal"])

#: modality -> 对应能力开关名 (与 capability_toggles 文档映射一致)
MODALITY_CAPABILITY: dict[str, str] = {
    "image": "image_gen",
    "audio_tts": "tts",
    "audio_asr": "stt",
}


def _check_rate_limit(key_info: dict[str, Any]) -> None:
    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        raise


@router.post("/v1/multimodal/execute")
async def multimodal_execute(
    req: CreateMultimodalExecuteRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """同模态多平台并发扇出执行。

    - mode=all: 返回全部成功路由结果 (primary 为结果数组)
    - mode=fastest: 首个成功即主结果, 未完成路由取消并记 cancelled
    - mode=best: 全跑完后按 (真实优先, 低延迟) 确定性选优
    """
    _check_rate_limit(key_info)
    modality = req.modality
    capability = MODALITY_CAPABILITY.get(modality)
    if capability and not is_enabled(capability):
        raise HTTPException(
            status_code=503,
            detail=f"capability '{capability}' is disabled by administrator",
        )

    payload = {
        "prompt": req.prompt,
        "text": req.text,
        "audio_b64": req.audio_b64,
        "language": req.language,
        "size": req.size,
        "n": req.n,
        "voice": req.voice,
        "audio_format": req.audio_format,
    }

    fanout = get_fanout()
    try:
        result = await fanout.execute(
            modality=modality,
            platforms=list(req.platforms),
            payload=payload,
            mode=req.mode,
            per_route_timeout_s=req.per_route_timeout_s,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    executed = [r for r in result.routes if r.status != "no_key"]
    if not executed and result.routes:
        # 全部路由因无密钥/不支持而不可执行 — 如实 503, 逐路证据随附
        raise HTTPException(
            status_code=503,
            detail={
                "error": "no executable route (all platforms lack API keys and mock is disabled/unavailable)",
                "routes": [r.to_dict() for r in result.routes],
            },
        )

    if result.any_mock:
        for hk, hv in mock_headers(True).items():
            response.headers[hk] = hv

    return result.to_dict()
