"""MoA Optimizer API routes."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import require_api_key

logger = logging.getLogger(__name__)

router = APIRouter(tags=["optimizer"])


def _get_optimizer(request: Request):
    """Get the MoaOptimizer instance from app.state."""
    return getattr(request.app.state, "optimizer", None)


@router.post("/v1/optimizer/run")
async def run_optimization(
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Manually trigger one optimisation round."""
    opt = _get_optimizer(request)
    if opt is None:
        raise HTTPException(503, "Optimizer not initialised")
    result = await opt.run_daily_optimization()
    return result.to_dict()


@router.get("/v1/optimizer/recommendation")
async def get_recommendation(
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Get the current best model combination recommendation."""
    opt = _get_optimizer(request)
    if opt is None:
        raise HTTPException(503, "Optimizer not initialised")
    return opt.get_recommendation()


@router.get("/v1/optimizer/history")
async def get_history(
    request: Request,
    limit: int = 30,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Get optimisation history."""
    opt = _get_optimizer(request)
    if opt is None:
        raise HTTPException(503, "Optimizer not initialised")
    return {"history": opt.get_history(limit=limit)}


@router.get("/v1/optimizer/experiments")
async def get_experiments(
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Get A/B test experiment results."""
    opt = _get_optimizer(request)
    if opt is None:
        raise HTTPException(503, "Optimizer not initialised")
    return {"experiments": opt.get_experiments()}


@router.get("/v1/optimizer/stats")
async def get_strategy_stats(
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Get Bayesian strategy statistics."""
    opt = _get_optimizer(request)
    if opt is None:
        raise HTTPException(503, "Optimizer not initialised")
    return {"strategies": opt.get_strategy_stats()}


@router.get("/v1/strategies")
async def list_strategies(
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """List all available MOA strategies."""
    from ..moa_strategies import STRATEGY_REGISTRY
    from ..moa_strategies import list_strategies as _list

    names = _list()
    result = []
    for name in names:
        s = STRATEGY_REGISTRY.get(name)
        result.append(
            {
                "name": name,
                "class": type(s).__name__ if s else "unknown",
            }
        )
    return {"strategies": result, "total": len(result)}
