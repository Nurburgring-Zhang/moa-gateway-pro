"""World model endpoints - environment simulation and physics reasoning."""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from .._helpers import mock_headers
from ..auth import require_api_key
from ..capability_toggles import require_capability
from ..utils.url_validator import validate_external_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["world_model"], dependencies=[Depends(require_capability("world_model"))])


def _apply_mock_label(provider: Any, response: Response) -> None:
    """Audit F24: label mock-provider responses with the X-MOA-Mock header."""
    if provider.__class__.__name__.startswith("Mock"):
        for _hk, _hv in mock_headers(True).items():
            response.headers[_hk] = _hv


# --- Request/Response Models ------------------------------------------------


class SimulateRequest(BaseModel):
    scenario: str = Field(..., min_length=1, max_length=4000, description="Scenario to simulate")
    steps: int = Field(default=5, ge=1, le=50, description="Number of simulation steps")
    constraints: list[str] = Field(default_factory=list, description="Physical/logical constraints")
    initial_state: dict[str, Any] = Field(default_factory=dict, description="Initial state")
    model: str = Field(default="auto", description="Backend: vlm/cosmos/auto")


class SimulateResponse(BaseModel):
    states: list[dict[str, Any]]
    summary: str = ""
    confidence: float = 0.0


class PredictRequest(BaseModel):
    current_state: dict[str, Any] = Field(..., description="Current world state")
    action: str = Field(..., min_length=1, description="Action to apply")
    context: str = Field(default="", description="Additional context")
    model: str = Field(default="auto")


class PredictResponse(BaseModel):
    next_state: dict[str, Any]
    probability: float = 0.0
    reasoning: str = ""
    side_effects: list[str] = Field(default_factory=list)


class SceneRequest(BaseModel):
    image_url: str | None = Field(None, description="Image URL for visual scene understanding")
    description: str | None = Field(None, description="Text description of the scene")
    model: str = Field(default="auto")


class SceneResponse(BaseModel):
    entities: list[dict[str, Any]] = Field(default_factory=list)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    physical_properties: dict[str, Any] = Field(default_factory=dict)
    affordances: list[str] = Field(default_factory=list)
    environment: dict[str, Any] = Field(default_factory=dict)


# --- Endpoints --------------------------------------------------------------


@router.post("/v1/world/simulate", response_model=SimulateResponse)
async def simulate_world(
    req: SimulateRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Simulate a scenario and predict state transitions.

    Uses VLM (GPT-4o) for world understanding and physics reasoning.
    Optionally routes to NVIDIA Cosmos for GPU-accelerated simulation.
    """
    provider = _get_world_provider(req.model)
    _apply_mock_label(provider, response)

    try:
        result = await provider.simulate(
            scenario=req.scenario,
            steps=req.steps,
            constraints=req.constraints,
            initial_state=req.initial_state,
        )
        return SimulateResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("World simulation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Simulation error: {str(e)}") from e


@router.post("/v1/world/predict", response_model=PredictResponse)
async def predict_state(
    req: PredictRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Predict next state given current state + action.

    Applies causal reasoning and physical constraints.
    """
    provider = _get_world_provider(req.model)
    _apply_mock_label(provider, response)

    try:
        result = await provider.predict_next_state(
            current_state=req.current_state,
            action=req.action,
            context=req.context,
        )
        return PredictResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("State prediction failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Prediction error: {str(e)}") from e


@router.post("/v1/world/scene", response_model=SceneResponse)
async def understand_scene(
    req: SceneRequest,
    response: Response,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Understand a scene - extract entities, relationships, and affordances."""
    if not req.image_url and not req.description:
        raise HTTPException(
            status_code=400, detail="Either image_url or description is required"
        )

    # SSRF prevention: validate user-provided URLs
    if req.image_url:
        validate_external_url(req.image_url)

    provider = _get_world_provider(req.model)
    _apply_mock_label(provider, response)

    try:
        result = await provider.understand_scene(
            image_url=req.image_url,
            description=req.description,
        )
        return SceneResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}") from e
    except Exception as e:
        logger.error("Scene understanding failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Scene analysis error: {str(e)}") from e


# --- Helpers ----------------------------------------------------------------


def _get_world_provider(model: str):
    """Get world model provider. Falls back to MockWorldProvider when no real
    VLM key is configured (mock.mode=explicit) so the pipeline returns 200."""
    from ..config import get_settings
    from ..providers.world_model_provider import (
        CosmosWorldProvider, MockWorldProvider, VLMWorldProvider,
    )

    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")

    if model == "cosmos":
        cosmos_base = os.environ.get("COSMOS_API_BASE", "http://localhost:8080")
        return CosmosWorldProvider(api_key=api_key, api_base=cosmos_base)
    elif api_key:
        return VLMWorldProvider(api_key=api_key, api_base=api_base)
    else:
        # No real VLM key — use mock provider when mock.mode is explicit.
        try:
            mock_mode = get_settings().mock.mode
        except Exception:
            mock_mode = "explicit"
        if mock_mode == "explicit":
            return MockWorldProvider()
        # mock.mode=disabled → no key, no mock → real provider (will 503 on call)
        return VLMWorldProvider(api_key="", api_base=api_base)
