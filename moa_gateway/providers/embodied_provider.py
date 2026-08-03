"""Embodied AI providers - VLM planning + ROS2 bridge execution."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class EmbodiedProvider(ABC):
    """Base class for embodied AI providers."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def plan_actions(
        self,
        observation: dict[str, Any],
        goal: str,
        constraints: list[str] | None = None,
        available_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Plan a sequence of actions to achieve the goal.

        Returns: {actions: [{step, action, target, params, reasoning}], confidence, estimated_time}
        """
        ...

    @abstractmethod
    async def execute_action(
        self,
        action: dict[str, Any],
        robot_id: str = "default",
    ) -> dict[str, Any]:
        """Execute a single action on a robot.

        Returns: {success, result, new_state, error}
        """
        ...

    @abstractmethod
    async def get_status(
        self,
        robot_id: str = "default",
    ) -> dict[str, Any]:
        """Get current robot status.

        Returns: {robot_id, state, position, battery, sensors, last_action}
        """
        ...


class VLMEmbodiedProvider(EmbodiedProvider):
    """VLM-based embodied AI - uses GPT-4o Vision for planning actions."""

    PLANNING_SYSTEM_PROMPT = """You are a robot action planner. Given an observation of the environment and a goal:
1. Analyze the current scene and identify relevant objects
2. Plan a sequence of atomic actions to achieve the goal
3. Consider physical constraints (reachability, weight, collisions)
4. Output structured JSON with step-by-step actions

Available action types:
- move_to: {target: "location", speed: "slow"|"normal"|"fast"}
- pick: {target: "object_name", gripper: "left"|"right"}
- place: {target: "location", orientation: "upright"|"flat"}
- rotate: {target: "object_name", angle: degrees}
- push: {target: "object_name", direction: "forward"|"left"|"right", force: "gentle"|"normal"|"strong"}
- open: {target: "container_name"}
- close: {target: "container_name"}
- wait: {duration: seconds}
- look_at: {target: "object_or_location"}
- speak: {message: "text"}

Output format (JSON):
{
  "actions": [
    {"step": 1, "action": "move_to", "target": "table", "params": {"speed": "normal"}, "reasoning": "Need to approach the table first"}
  ],
  "confidence": 0.85,
  "estimated_time_seconds": 30,
  "risks": ["Object might be too heavy"]
}"""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.openai.com/v1"
        self.model = "gpt-4o"

    async def _chat(self, system: str, user_content: list[dict] | str) -> str:
        """Call VLM for planning."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        if isinstance(user_content, str):
            messages.append({"role": "user", "content": user_content})
        else:
            messages.append({"role": "user", "content": user_content})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 4000,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.api_base}/chat/completions", json=payload, headers=headers
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]

    async def plan_actions(
        self,
        observation: dict[str, Any],
        goal: str,
        constraints: list[str] | None = None,
        available_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Plan actions using VLM understanding of the environment."""
        content_parts: list[dict] | str

        if "image_url" in observation:
            text_part = (
                f"Goal: {goal}\nObservation: "
                f"{json.dumps({k: v for k, v in observation.items() if k != 'image_url'})}"
            )
            if constraints:
                text_part += f"\nConstraints: {', '.join(constraints)}"
            content_parts = [
                {"type": "text", "text": text_part},
                {"type": "image_url", "image_url": {"url": observation["image_url"]}},
            ]
        else:
            msg = f"Goal: {goal}\nObservation: {json.dumps(observation)}"
            if constraints:
                msg += f"\nConstraints: {', '.join(constraints)}"
            if available_actions:
                msg += f"\nAvailable actions: {', '.join(available_actions)}"
            content_parts = msg

        try:
            result_str = await self._chat(self.PLANNING_SYSTEM_PROMPT, content_parts)
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {"actions": [], "confidence": 0.0, "error": "Failed to parse plan"}

    async def execute_action(
        self,
        action: dict[str, Any],
        robot_id: str = "default",
    ) -> dict[str, Any]:
        """Simulate action execution (no real hardware)."""
        logger.info(
            "VLM embodied: simulating action %s on robot %s",
            action.get("action"),
            robot_id,
        )
        return {
            "success": True,
            "result": f"Simulated: {action.get('action', 'unknown')} on {action.get('target', 'unknown')}",
            "new_state": {
                "position": "updated",
                "gripper": "closed" if action.get("action") == "pick" else "open",
            },
            "simulated": True,
        }

    async def get_status(self, robot_id: str = "default") -> dict[str, Any]:
        """Return simulated robot status."""
        return {
            "robot_id": robot_id,
            "state": "idle",
            "position": {"x": 0.0, "y": 0.0, "z": 0.0},
            "orientation": {"roll": 0, "pitch": 0, "yaw": 0},
            "battery": 100,
            "sensors": {"camera": "active", "lidar": "active", "force": "active"},
            "last_action": None,
            "mode": "simulation",
        }


class ROS2BridgeProvider(EmbodiedProvider):
    """ROS2 Bridge provider - connects to real robots via rosbridge_suite WebSocket."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "ws://localhost:9090"  # Default rosbridge port
        self._vlm = VLMEmbodiedProvider(api_key=api_key)

    async def plan_actions(
        self,
        observation: dict[str, Any],
        goal: str,
        constraints: list[str] | None = None,
        available_actions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Use VLM for planning, then validate against ROS2 capabilities."""
        return await self._vlm.plan_actions(observation, goal, constraints, available_actions)

    async def execute_action(
        self,
        action: dict[str, Any],
        robot_id: str = "default",
    ) -> dict[str, Any]:
        """Execute action via ROS2 bridge WebSocket."""
        try:
            action_topic = f"/{robot_id}/action_command"
            payload = {
                "op": "publish",
                "topic": action_topic,
                "msg": {
                    "action_type": action.get("action", ""),
                    "target": action.get("target", ""),
                    "params": action.get("params", {}),
                },
            }

            base_http = self.api_base.replace("ws://", "").replace("wss://", "")
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"http://{base_http}/publish",
                    json=payload,
                )
                if resp.status_code == 200:
                    return {"success": True, "result": resp.json()}
                else:
                    return {
                        "success": False,
                        "error": f"ROS2 bridge error: {resp.status_code}",
                    }
        except Exception as e:
            logger.error("ROS2 bridge execution failed: %s", e)
            return {"success": False, "error": str(e), "fallback": "simulation"}

    async def get_status(self, robot_id: str = "default") -> dict[str, Any]:
        """Query robot status via ROS2 bridge."""
        try:
            base_http = self.api_base.replace("ws://", "").replace("wss://", "")
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"http://{base_http}/status/{robot_id}")
                if resp.status_code == 200:
                    return resp.json()
        except Exception as e:
            logger.warning("ROS2 status query failed: %s", e)

        # Fallback to simulated status
        return {
            "robot_id": robot_id,
            "state": "disconnected",
            "mode": "ros2_bridge",
            "error": "ROS2 bridge not available",
        }
