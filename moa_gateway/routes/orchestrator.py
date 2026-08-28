"""Orchestrator API — 自主编排引擎对外端点 (O7)。

  POST /v1/orchestrator/run           全自动编排(分析->匹配->联合执行->强化)
  POST /v1/orchestrator/plan          仅出计划(不执行)
  POST /v1/orchestrator/analyze       仅任务分析
  GET  /v1/orchestrator/capabilities  能力目录
  POST /v1/orchestrator/skills        开发+校验+自动部署新 skill (admin/operator)
  GET  /v1/orchestrator/skills        已部署 skill 列表
  GET  /v1/orchestrator/scores        能力评分
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..orchestrator.engine import get_orchestrator
from ..orchestrator.skill_factory import SkillFactoryError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["orchestrator"])


class OrchestrateRequest(BaseModel):
    task: str = Field(..., min_length=1, max_length=8000)
    input: dict[str, Any] = Field(default_factory=dict)


class DevelopSkillRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(default="", max_length=512)
    params: list[Any] = Field(default_factory=list)
    code: str = Field(default="", max_length=20000)
    nl_spec: str = Field(default="", max_length=4000)
    test_input: dict[str, Any] = Field(default_factory=dict)


def _require_privileged(key_info: dict[str, Any]) -> None:
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(403, "only admin/operator can develop/deploy skills")


def _is_privileged(key_info: dict[str, Any]) -> bool:
    """v3.2.1 hardening: the orchestrator honors the same admin/operator
    trust model as /v1/agent — non-privileged callers never reach
    RCE-capable skills (planner filters + executor re-checks)."""
    return (key_info.get("role", "readonly")) in ("admin", "operator")


@router.post("/v1/orchestrator/run")
async def orchestrator_run(req: OrchestrateRequest, key_info: dict[str, Any] = Depends(require_api_key)):
    """全自动智能编排: 分析任务 -> 匹配能力 -> 联合执行 -> 强化。"""
    orch = get_orchestrator()
    try:
        return await orch.run(
            req.task, req.input, privileged=_is_privileged(key_info), role=key_info.get("role", "readonly")
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("orchestrator run failed")
        raise HTTPException(500, f"orchestration failed: {e}") from e


@router.post("/v1/orchestrator/plan")
async def orchestrator_plan(req: OrchestrateRequest, key_info: dict[str, Any] = Depends(require_api_key)):
    orch = get_orchestrator()
    return orch.plan(req.task, req.input, privileged=_is_privileged(key_info))


@router.post("/v1/orchestrator/analyze")
async def orchestrator_analyze(req: OrchestrateRequest, key_info: dict[str, Any] = Depends(require_api_key)):
    orch = get_orchestrator()
    return orch.analyze(req.task, req.input)


@router.get("/v1/orchestrator/capabilities")
async def orchestrator_capabilities(key_info: dict[str, Any] = Depends(require_api_key)):
    orch = get_orchestrator()
    return orch.capabilities()


@router.post("/v1/orchestrator/skills")
async def orchestrator_develop_skill(req: DevelopSkillRequest, key_info: dict[str, Any] = Depends(require_api_key)):
    """开发 + 校验 + 自动部署一个新 skill (admin/operator)。"""
    _require_privileged(key_info)
    orch = get_orchestrator()
    spec = {
        "name": req.name,
        "description": req.description,
        "params": req.params,
        "code": req.code,
        "nl_spec": req.nl_spec,
        "test_input": req.test_input,
    }
    try:
        return await orch.develop_skill(spec)
    except SkillFactoryError as e:
        raise HTTPException(400, f"skill development rejected: {e}") from e
    except Exception as e:  # noqa: BLE001
        logger.exception("skill development failed")
        raise HTTPException(500, f"skill development failed: {e}") from e


@router.get("/v1/orchestrator/skills")
async def orchestrator_list_skills(key_info: dict[str, Any] = Depends(require_api_key)):
    orch = get_orchestrator()
    return orch.skills()


@router.get("/v1/orchestrator/scores")
async def orchestrator_scores(key_info: dict[str, Any] = Depends(require_api_key)):
    orch = get_orchestrator()
    return orch.scores()
