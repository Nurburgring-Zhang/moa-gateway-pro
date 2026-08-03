"""Embodied AI endpoints - robot action planning and execution."""
from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import require_api_key
from ..utils.url_validator import validate_external_url

logger = logging.getLogger(__name__)
router = APIRouter(tags=["embodied"])


# --- Request/Response Models ------------------------------------------------


class PlanRequest(BaseModel):
    observation: dict[str, Any] = Field(..., description="Current environment observation (can include image_url)")
    goal: str = Field(..., min_length=1, max_length=2000, description="Goal to achieve")
    constraints: list[str] = Field(default_factory=list, description="Physical/safety constraints")
    available_actions: list[str] = Field(default_factory=list, description="Available action types")
    model: str = Field(default="auto", description="Backend: vlm/ros2/auto")
    robot_id: str = Field(default="default")


class PlanResponse(BaseModel):
    actions: list[dict[str, Any]]
    confidence: float = 0.0
    estimated_time_seconds: float = 0.0
    risks: list[str] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    action: dict[str, Any] = Field(..., description="Action to execute: {action, target, params}")
    robot_id: str = Field(default="default")
    model: str = Field(default="auto")


class ExecuteResponse(BaseModel):
    success: bool
    result: str = ""
    new_state: dict[str, Any] = Field(default_factory=dict)
    simulated: bool = False
    error: Optional[str] = None


class StatusResponse(BaseModel):
    robot_id: str
    state: str
    position: dict[str, Any] = Field(default_factory=dict)
    battery: int = 100
    sensors: dict[str, str] = Field(default_factory=dict)
    last_action: Optional[dict[str, Any]] = None
    mode: str = "simulation"


# --- Endpoints --------------------------------------------------------------


@router.post("/v1/embodied/plan", response_model=PlanResponse)
async def plan_actions(
    req: PlanRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Plan a sequence of robot actions to achieve a goal.

    Uses VLM (GPT-4o Vision) to understand the environment and
    generate step-by-step action plans.
    """
    # SSRF prevention: validate image_url in observation if present
    image_url = req.observation.get("image_url")
    if image_url and isinstance(image_url, str):
        validate_external_url(image_url)

    provider = _get_embodied_provider(req.model)

    try:
        result = await provider.plan_actions(
            observation=req.observation,
            goal=req.goal,
            constraints=req.constraints,
            available_actions=req.available_actions,
        )
        return PlanResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}")
    except Exception as e:
        logger.error("Action planning failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Planning error: {str(e)}")


@router.post("/v1/embodied/execute", response_model=ExecuteResponse)
async def execute_action(
    req: ExecuteRequest,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Execute a single action on a robot.

    In simulation mode, returns simulated results.
    With ROS2 bridge configured, forwards to real hardware.
    """
    provider = _get_embodied_provider(req.model)

    try:
        result = await provider.execute_action(
            action=req.action,
            robot_id=req.robot_id,
        )
        return ExecuteResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}")
    except Exception as e:
        logger.error("Action execution failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Execution error: {str(e)}")


@router.get("/v1/embodied/status", response_model=StatusResponse)
async def get_robot_status(
    robot_id: str = "default",
    model: str = "auto",
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Get current robot status and sensor readings."""
    provider = _get_embodied_provider(model)

    try:
        result = await provider.get_status(robot_id=robot_id)
        return StatusResponse(**result)
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=f"Not Implemented: {str(e)}")
    except Exception as e:
        logger.error("Status query failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Status error: {str(e)}")


# --- Helpers ----------------------------------------------------------------


def _get_embodied_provider(model: str):
    """Get embodied AI provider."""
    from ..providers.embodied_provider import VLMEmbodiedProvider, ROS2BridgeProvider

    api_key = os.environ.get("OPENAI_API_KEY", "")
    api_base = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")

    if model == "ros2":
        ros2_base = os.environ.get("ROS2_BRIDGE_URL", "ws://localhost:9090")
        return ROS2BridgeProvider(api_key=api_key, api_base=ros2_base)
    else:  # "auto" or "vlm"
        return VLMEmbodiedProvider(api_key=api_key, api_base=api_base)
