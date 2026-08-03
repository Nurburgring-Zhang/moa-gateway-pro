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
        "You are a physics-aware world model simulator. Given a scenario description, "
        "you must predict the state evolution step by step with rigorous physical reasoning.\n\n"
        "For each simulation step, output a structured JSON object following this schema:\n"
        "{\n"
        "  \"states\": [\n"
        "    {\n"
        "      \"step\": <step_number>,\n"
        "      \"time\": \"t+Ns\",\n"
        "      \"time_delta_sec\": <seconds_elapsed_since_last_step>,\n"
        "      \"description\": \"Detailed description of what happens\",\n"
        "      \"objects\": [\n"
        "        {\"id\": \"obj_name\", \"position\": [x, y, z], "
        "\"velocity\": [vx, vy, vz], \"state\": \"solid|liquid|gas|moving|stationary\"}\n"
        "      ],\n"
        "      \"environment\": {\"gravity\": 9.81, \"temperature_c\": 20, "
        "\"medium\": \"air\", \"friction_coefficient\": 0.3},\n"
        "      \"events\": [\"description of notable events this step\"],\n"
        "      \"changes\": [\"what changed from previous step and why\"]\n"
        "    }\n"
        "  ],\n"
        "  \"summary\": \"Overall outcome with final state description\",\n"
        "  \"physics_principles\": [\"List of physics principles applied\"],\n"
        "  \"confidence\": 0.85\n"
        "}\n\n"
        "Rules:\n"
        "- Apply realistic physics: gravity (9.81 m/s^2), friction, momentum conservation, "
        "energy conservation, thermodynamics\n"
        "- Track position, velocity, and acceleration for moving objects\n"
        "- Model collisions with coefficient of restitution\n"
        "- Consider air resistance for high-speed objects\n"
        "- Account for uncertainty and assign confidence per step\n"
        "- If the scenario involves human behavior, apply common-sense reasoning about "
        "typical actions and reaction times (~0.2s)\n"
        "- Timestamps must be physically consistent (no time travel)\n"
        "- Energy must be conserved or accounted for (friction -> heat)"
    )

    PREDICTION_SYSTEM_PROMPT = (
        "You are a state transition predictor. Given the current state and an action, "
        "predict the next state using causal physics reasoning.\n\n"
        "Output format (JSON):\n"
        "{\n"
        "  \"previous_state_summary\": \"Brief summary of input state\",\n"
        "  \"action_applied\": \"Description of the action and its parameters\",\n"
        "  \"next_state\": {\n"
        "    \"description\": \"Detailed description of resulting state\",\n"
        "    \"objects\": [{\n"
        "      \"id\": \"name\", \"position\": [x,y,z], \"velocity\": [vx,vy,vz],\n"
        "      \"state\": \"current state\", \"properties\": {}\n"
        "    }],\n"
        "    \"changes\": [\"what changed and why\"]\n"
        "  },\n"
        "  \"probability\": 0.9,\n"
        "  \"reasoning\": \"Step-by-step causal chain: A causes B because...\",\n"
        "  \"side_effects\": [\"Secondary effects of the action\"],\n"
        "  \"alternative_outcomes\": [\n"
        "    {\"description\": \"Alternative outcome if X\", \"probability\": 0.1}\n"
        "  ],\n"
        "  \"uncertainty_factors\": [\"Factors that could change the outcome\"],\n"
        "  \"reversibility\": \"reversible|partially_reversible|irreversible\"\n"
        "}\n\n"
        "Rules:\n"
        "- Apply Newton's laws for mechanical interactions\n"
        "- Consider material properties (elasticity, hardness, density)\n"
        "- Account for human factors (strength limits, reaction time)\n"
        "- Identify cascade effects (A->B->C)\n"
        "- Provide alternative outcomes with probabilities summing to <=1.0"
    )

    SCENE_SYSTEM_PROMPT = (
        "You are a scene understanding engine. Analyze the provided scene and extract "
        "a comprehensive physical model of the environment.\n\n"
        "Output format (JSON):\n"
        "{\n"
        "  \"entities\": [\n"
        "    {\n"
        "      \"id\": \"unique_name\",\n"
        "      \"type\": \"furniture|tool|container|food|electronic|person|animal|vehicle\",\n"
        "      \"position\": {\"x\": 0, \"y\": 0, \"z\": 0, "
        "\"reference_frame\": \"world|relative_to_X\"},\n"
        "      \"dimensions\": {\"width\": 0, \"height\": 0, \"depth\": 0, "
        "\"unit\": \"meters\"},\n"
        "      \"properties\": {\n"
        "        \"material\": \"wood|metal|plastic|glass|fabric|organic\",\n"
        "        \"weight_kg\": 0,\n"
        "        \"temperature_c\": 20,\n"
        "        \"is_movable\": true,\n"
        "        \"is_fragile\": false,\n"
        "        \"state\": \"intact|damaged|open|closed\"\n"
        "      }\n"
        "    }\n"
        "  ],\n"
        "  \"relationships\": [\n"
        "    {\"subject\": \"obj_a\", \"relation\": "
        "\"on_top_of|inside|next_to|above|below|attached_to\", \"object\": \"obj_b\"}\n"
        "  ],\n"
        "  \"physical_properties\": {\n"
        "    \"gravity\": \"normal (9.81 m/s^2)\",\n"
        "    \"temperature_c\": 20,\n"
        "    \"lighting\": \"bright|dim|dark|natural|artificial\",\n"
        "    \"surfaces\": [{\"name\": \"floor\", \"material\": \"...\", "
        "\"friction\": 0.5}]\n"
        "  },\n"
        "  \"affordances\": [\n"
        "    \"Actionable description: e.g. pick up the cup from the table\"\n"
        "  ],\n"
        "  \"risks\": [\n"
        "    \"Potential hazards: e.g. glass near edge could fall\"\n"
        "  ],\n"
        "  \"environment\": {\n"
        "    \"type\": \"indoor|outdoor|vehicle|industrial\",\n"
        "    \"conditions\": [\"weather/state factors\"],\n"
        "    \"navigable_space\": \"Description of free space for movement\"\n"
        "  }\n"
        "}\n\n"
        "Rules:\n"
        "- Estimate physical dimensions and weights realistically\n"
        "- Identify ALL spatial relationships (support, containment, adjacency)\n"
        "- List affordances as actionable robot commands\n"
        "- Flag unstable states or potential hazards in risks\n"
        "- Use metric units throughout"
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
