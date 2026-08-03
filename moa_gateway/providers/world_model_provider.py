"""World model providers - VLM-based simulation + Cosmos local inference."""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class WorldModelProvider(ABC):
    """Base class for world model providers."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        self.api_key = api_key
        self.api_base = api_base

    @abstractmethod
    async def simulate(
        self,
        scenario: str,
        steps: int = 5,
        constraints: list[str] | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Simulate a scenario and predict state transitions.

        Returns: {states: [{step, description, entities, properties}], summary}
        """
        ...

    @abstractmethod
    async def predict_next_state(
        self,
        current_state: dict[str, Any],
        action: str,
        context: str = "",
    ) -> dict[str, Any]:
        """Given current state + action, predict next state.

        Returns: {next_state, probability, reasoning, side_effects}
        """
        ...

    @abstractmethod
    async def understand_scene(
        self,
        image_url: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """Understand a scene/environment.

        Returns: {entities, relationships, physical_properties, affordances}
        """
        ...


class VLMWorldProvider(WorldModelProvider):
    """VLM-based world model using GPT-4o/Qwen-VL for world understanding."""

    SIMULATION_SYSTEM_PROMPT = (
        "You are a world simulation engine. Given a scenario description, you must:\n"
        "1. Identify all entities and their initial states\n"
        "2. Apply physical laws, causal reasoning, and common sense\n"
        "3. Predict state transitions step by step\n"
        "4. Output structured JSON with each step's state\n\n"
        "Rules:\n"
        "- Obey physics (gravity, momentum, thermodynamics)\n"
        "- Entities interact according to their properties\n"
        "- Account for uncertainty and probabilistic outcomes\n"
        "- Consider environmental factors\n\n"
        "Output format (JSON):\n"
        "{\n"
        "  \"states\": [\n"
        "    {\"step\": 1, \"time\": \"t+0s\", \"description\": \"...\", "
        "\"entities\": [...], \"changes\": [...]}\n"
        "  ],\n"
        "  \"summary\": \"Overall outcome description\",\n"
        "  \"confidence\": 0.85\n"
        "}"
    )

    PREDICTION_SYSTEM_PROMPT = (
        "You are a state prediction engine. Given the current state and an action:\n"
        "1. Apply the action to the current state\n"
        "2. Consider physical constraints and causal effects\n"
        "3. Predict the resulting state\n"
        "4. Identify side effects and uncertainties\n\n"
        "Output format (JSON):\n"
        "{\n"
        "  \"next_state\": {\"description\": \"...\", \"entities\": [...], "
        "\"properties\": {...}},\n"
        "  \"probability\": 0.9,\n"
        "  \"reasoning\": \"Step-by-step causal reasoning...\",\n"
        "  \"side_effects\": [\"...\"],\n"
        "  \"uncertainty_factors\": [\"...\"]\n"
        "}"
    )

    SCENE_SYSTEM_PROMPT = (
        "You are a scene understanding engine. Analyze the given scene and extract:\n"
        "1. All entities and objects present\n"
        "2. Spatial relationships between entities\n"
        "3. Physical properties (material, weight, temperature, etc.)\n"
        "4. Affordances (what actions are possible)\n"
        "5. Environmental conditions\n\n"
        "Output format (JSON):\n"
        "{\n"
        "  \"entities\": [{\"name\": \"...\", \"type\": \"...\", "
        "\"position\": \"...\", \"properties\": {...}}],\n"
        "  \"relationships\": [{\"subject\": \"...\", \"relation\": \"...\", "
        "\"object\": \"...\"}],\n"
        "  \"physical_properties\": {\"gravity\": \"normal\", "
        "\"temperature\": \"...\", \"lighting\": \"...\"},\n"
        "  \"affordances\": [\"...\", \"...\"],\n"
        "  \"environment\": {\"type\": \"...\", \"conditions\": [...]}\n"
        "}"
    )

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "https://api.openai.com/v1"
        self.model = "gpt-4o"

    async def _chat(
        self, system: str, user_content: list[dict] | str, max_tokens: int = 4000
    ) -> str:
        """Call VLM chat endpoint."""
        messages = [{"role": "system", "content": system}]

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
            "max_tokens": max_tokens,
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.api_base}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]

    async def simulate(
        self,
        scenario: str,
        steps: int = 5,
        constraints: list[str] | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        user_msg = f"Scenario: {scenario}\nSteps to simulate: {steps}"
        if constraints:
            user_msg += f"\nConstraints: {', '.join(constraints)}"
        if initial_state:
            user_msg += f"\nInitial state: {json.dumps(initial_state)}"

        try:
            result_str = await self._chat(self.SIMULATION_SYSTEM_PROMPT, user_msg)
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {"states": [], "summary": result_str, "confidence": 0.5}

    async def predict_next_state(
        self,
        current_state: dict[str, Any],
        action: str,
        context: str = "",
    ) -> dict[str, Any]:
        user_msg = f"Current state: {json.dumps(current_state)}\nAction: {action}"
        if context:
            user_msg += f"\nContext: {context}"

        try:
            result_str = await self._chat(self.PREDICTION_SYSTEM_PROMPT, user_msg)
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {
                "next_state": {},
                "probability": 0.5,
                "reasoning": result_str,
                "side_effects": [],
            }

    async def understand_scene(
        self,
        image_url: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        content: list[dict] | str

        if image_url:
            content = [
                {"type": "text", "text": description or "Analyze this scene in detail."},
                {"type": "image_url", "image_url": {"url": image_url}},
            ]
        else:
            content = description or "No scene provided."

        try:
            result_str = await self._chat(self.SCENE_SYSTEM_PROMPT, content)
            return json.loads(result_str)
        except json.JSONDecodeError:
            return {"entities": [], "relationships": [], "error": "Failed to parse scene"}


class CosmosWorldProvider(WorldModelProvider):
    """NVIDIA Cosmos local inference provider (placeholder for GPU deployments)."""

    def __init__(self, api_key: str = "", api_base: str = ""):
        super().__init__(api_key, api_base)
        if not self.api_base:
            self.api_base = "http://localhost:8080"  # Local Cosmos server

    async def simulate(
        self,
        scenario: str,
        steps: int = 5,
        constraints: list[str] | None = None,
        initial_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Forward to local Cosmos inference server."""
        payload = {
            "scenario": scenario,
            "steps": steps,
            "constraints": constraints or [],
            "initial_state": initial_state or {},
        }

        async with httpx.AsyncClient(timeout=300) as client:
            try:
                resp = await client.post(f"{self.api_base}/simulate", json=payload)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning("Cosmos server unavailable: %s, using VLM fallback", e)
                fallback = VLMWorldProvider(api_key=self.api_key)
                return await fallback.simulate(scenario, steps, constraints, initial_state)

    async def predict_next_state(
        self,
        current_state: dict[str, Any],
        action: str,
        context: str = "",
    ) -> dict[str, Any]:
        payload = {"current_state": current_state, "action": action, "context": context}

        async with httpx.AsyncClient(timeout=120) as client:
            try:
                resp = await client.post(f"{self.api_base}/predict", json=payload)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning("Cosmos predict unavailable: %s", e)
                fallback = VLMWorldProvider(api_key=self.api_key)
                return await fallback.predict_next_state(current_state, action, context)

    async def understand_scene(
        self,
        image_url: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        fallback = VLMWorldProvider(api_key=self.api_key)
        return await fallback.understand_scene(image_url, description)
