"""Subagent routing HTTP routes (M9, OpenClacky port).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License)
— the HTTP surface is NEW to the gateway (OpenClacky forks subagents
in-process); the decision engine in ``moa_gateway.subagent_routing`` is the
port.

Endpoints:
- GET  /v1/subagent/config — lite pairing tables, fork prefixes, budgets
- POST /v1/subagent/route  — DRY-RUN routing decision (never executes)

Auth: ``require_api_key``. Capability gate: ``function_call`` — the gateway
has no dedicated "subagent" toggle and capability_toggles.py is frozen for
this integration, so the agent-loop tools & routing-hints capability gates
this surface (documented mapping).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..subagent_routing.registry import get_lite_registry
from ..subagent_routing.routing import (
    DEFAULT_FORK_PREFIXES,
    DEFAULT_SUBAGENT_MAX_ITERATIONS,
    DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS,
    SubagentContext,
    route_subagent_request,
)
from ..subagent_routing.tools import get_subagent_runner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/subagent", tags=["subagent"])

_MAX_TASK_LEN = 20_000
_MAX_TOOLS = 500


class RouteRequest(BaseModel):
    task: str = Field(default="", min_length=0, max_length=_MAX_TASK_LEN)
    primary_model: str | None = None
    provider_id: str | None = None
    requested_model: str | None = Field(
        default=None,
        description="'lite' keyword or a concrete model name (forces fork).",
    )
    available_tools: list[Any] = Field(default_factory=list, max_length=_MAX_TOOLS)
    forbidden_tools: list[str] = Field(default_factory=list, max_length=_MAX_TOOLS)
    category: str | None = None
    max_iterations: int | None = Field(default=None, ge=1, le=10_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)


@router.get("/config")
async def subagent_config(
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("function_call")),
) -> dict[str, Any]:
    """Static + registered subagent routing configuration."""
    registry = get_lite_registry()
    return {
        "fork_prefixes": list(DEFAULT_FORK_PREFIXES),
        "default_budget": {
            "max_iterations": DEFAULT_SUBAGENT_MAX_ITERATIONS,
            "max_output_tokens": DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS,
        },
        "runner_registered": get_subagent_runner() is not None,
        "providers": registry.providers(),
        **registry.snapshot(),
    }


@router.post("/route")
async def route_subagent(
    body: RouteRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability("function_call")),
) -> dict[str, Any]:
    """DRY-RUN: compute the routing decision for a task without executing.

    Same pure function the agent harness uses (``route_subagent_request``),
    so operators can preview model pairing / forbidden-tools filtering /
    budgets before a task actually forks.
    """
    ctx = SubagentContext(
        primary_model=body.primary_model,
        provider_id=body.provider_id,
        available_tools=list(body.available_tools),
        forbidden_tools=list(body.forbidden_tools),
        max_iterations=body.max_iterations,
        max_output_tokens=body.max_output_tokens,
        requested_model=body.requested_model,
        category=body.category,
    )
    try:
        decision = route_subagent_request(body.task, ctx)
    except Exception as exc:  # noqa: BLE001 — surface as 422, never 500 noise
        raise HTTPException(status_code=422, detail=f"routing failed: {exc}") from exc
    payload = decision.to_dict()
    payload["dry_run"] = True
    payload["runner_registered"] = get_subagent_runner() is not None
    return payload


__all__ = ["router", "RouteRequest"]
