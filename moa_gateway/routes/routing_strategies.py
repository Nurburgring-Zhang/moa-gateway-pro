"""HTTP surface for the routing-strategy engine (M1).

Endpoints (all gated by API key + the ``routing_strategies`` capability):

- ``GET  /v1/routing/strategies`` — catalogue of the 20 strategies.
- ``POST /v1/routing/resolve``    — dry-run ranking over a supplied pool.
- ``GET  /v1/routing/telemetry``  — per-endpoint rolling statistics.

The router is self-contained: it never mutates shared gateway modules. When
``settings.routing_strategies.enabled`` is False the mutating/query surfaces
return 503 — the module is opt-in and cannot influence pre-existing routes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..config import get_settings
from ..routing_strategies.engine import (
    RoutingDisabledError,
    RoutingStrategyEngine,
    UnknownStrategyError,
    get_engine,
)
from ..routing_strategies.models import EndpointCandidate, RoutingContext
from ..routing_strategies.strategies import STRATEGIES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/routing", tags=["routing_strategies"])

_CAPABILITY = "routing_strategies"


class ResolveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[EndpointCandidate] = Field(default_factory=list)
    strategy: str | None = None
    context: RoutingContext | None = None
    # Dry-run ranking: never mutate counters / decks / inflight slots.
    dry_run: bool = True


def _engine(request: Request) -> RoutingStrategyEngine:
    """app.state override (tests) wins; production uses the module singleton."""
    engine = getattr(request.app.state, "routing_engine", None)
    return engine if engine is not None else get_engine()


def _require_enabled() -> None:
    if not get_settings().routing_strategies.enabled:
        raise HTTPException(
            status_code=503,
            detail="routing_strategies is disabled in gateway settings",
        )


@router.get("/strategies")
async def list_strategies(
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    cfg = get_settings().routing_strategies
    return {
        "enabled": cfg.enabled,
        "default_strategy": cfg.default_strategy,
        "count": len(STRATEGIES),
        "strategies": [
            {
                "name": spec.name,
                "description": spec.description,
                "mode": spec.mode,
                "internal": spec.internal,
            }
            for spec in STRATEGIES.values()
        ],
    }


@router.post("/resolve")
async def resolve(
    payload: ResolveRequest,
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    engine = _engine(request)
    try:
        decision, _release = engine.resolve(
            payload.candidates,
            strategy=payload.strategy,
            context=payload.context,
            dry_run=payload.dry_run,
        )
    except RoutingDisabledError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except UnknownStrategyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "strategy": decision.strategy,
        "mode": decision.mode,
        "selected": decision.selected,
        "ordered": decision.ordered,
        "scores": decision.scores,
        "selections": [s.model_dump() for s in decision.selections],
        "dry_run": payload.dry_run,
        "candidate_count": len(payload.candidates),
    }


@router.get("/telemetry")
async def get_telemetry(
    request: Request,
    key_info: dict[str, Any] = Depends(require_api_key),
    _cap: None = Depends(require_capability(_CAPABILITY)),
) -> dict[str, Any]:
    _require_enabled()
    engine = _engine(request)
    store = engine.telemetry
    snapshots = store.all_snapshots()
    return {
        "history_window": store.history_window,
        "endpoint_count": len(snapshots),
        "endpoints": [snapshot.to_dict() for snapshot in snapshots],
    }
