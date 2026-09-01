"""P8 主动任务分析闭环端点 — POST /v1/tasks/auto.

一句话复合任务进来: 真 LLM 分解 → 能力路由 → 分波并发执行 → 自愈重试 → 全 trace 返回。
dry_run=true 只返回分解计划不执行。
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..auth import require_api_key
from ..ratelimit import get_limiter
from ..req_models import CreateTaskAutoRequest
from ..task_pipeline import (
    CapabilityRouter,
    TaskAnalysisError,
    TaskAnalyzer,
    TaskSupervisor,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["task-pipeline"])


def _check_rate_limit(key_info: dict[str, Any]) -> None:
    limiter = get_limiter()
    try:
        limiter.check_and_incr(key_info)
    except HTTPException:
        raise


@router.post("/v1/tasks/auto")
async def task_auto(
    req: CreateTaskAutoRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """主动任务分析 + 自动执行闭环。

    流程: TaskAnalyzer(真 LLM 分解) → TaskSupervisor(拓扑分波并发执行,
    失败重试一次后 self-heal 换路) → 返回逐子任务 trace。
    无模型端点时分解环节显式 503, 不做启发式兜底。
    """
    _check_rate_limit(key_info)
    analyzer = TaskAnalyzer()
    try:
        plan = await analyzer.analyze(req.task, max_subtasks=req.max_subtasks)
    except TaskAnalysisError as e:
        raise HTTPException(status_code=503, detail=f"task analysis failed: {e}") from e

    if req.dry_run:
        return {
            "success": True,
            "dry_run": True,
            "plan": [t.to_dict() for t in plan],
        }

    supervisor = TaskSupervisor(
        router=CapabilityRouter(), per_subtask_timeout_s=req.per_subtask_timeout_s
    )
    result = await supervisor.run(plan)
    result["dry_run"] = False
    return result
