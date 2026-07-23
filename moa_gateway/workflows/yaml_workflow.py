"""YAML workflow definition and execution engine.

Fuses Warp's Workflow YAML format with Paseo's Task dependency graph
(execution-order.ts topological sort). Supports multi-step MOA workflows
with variable interpolation, conditional branching, and parallel execution.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Valid step types
VALID_STEP_TYPES = {"moa", "chat", "discover", "transform", "conditional"}


@dataclass
class WorkflowStep:
    """A single workflow step.

    Attributes:
        id: Unique step identifier.
        type: Step type — moa/chat/discover/transform/conditional.
        depends_on: List of step IDs that must complete before this step.
        inputs: Input parameters for the step (prompt, model, strategy, etc.).
        outputs: List of output variable names produced by this step.
        condition: Condition expression for conditional steps.
        if_true: Sub-step definition for true branch (conditional only).
        if_false: Sub-step definition for false branch (conditional only).
    """

    id: str
    type: str = "chat"
    depends_on: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: list[str] = field(default_factory=list)
    condition: str | None = None
    if_true: dict[str, Any] | None = None
    if_false: dict[str, Any] | None = None


class WorkflowYAML:
    """Parse and execute a YAML-defined workflow.

    Supports a DAG of steps with:
    - Variable interpolation via ``{{ variable_path }}`` syntax
    - Topological sort for dependency-aware execution
    - Parallel execution of independent steps
    - Conditional branching (if_true / if_false)
    """

    def __init__(self, yaml_content: str) -> None:
        """Parse YAML content into a workflow definition.

        Args:
            yaml_content: Raw YAML string.

        Raises:
            ValueError: If the YAML is invalid or has a cycle.
        """
        self.definition: dict[str, Any] = yaml.safe_load(yaml_content) or {}
        self.name: str = self.definition.get("name", "unnamed")
        self.description: str = self.definition.get("description", "")
        self.version: str = self.definition.get("version", "1.0")
        self.steps: list[WorkflowStep] = self._parse_steps()
        self._step_map: dict[str, WorkflowStep] = {s.id: s for s in self.steps}
        self._validate_dag()

    def _parse_steps(self) -> list[WorkflowStep]:
        """Parse step definitions from the YAML dict."""
        raw_steps = self.definition.get("steps", [])
        if not isinstance(raw_steps, list):
            raise ValueError("'steps' must be a list")

        steps: list[WorkflowStep] = []
        for i, raw in enumerate(raw_steps):
            if not isinstance(raw, dict):
                raise ValueError(f"Step {i} must be a dict, got {type(raw)}")
            step_id = raw.get("id") or raw.get("name", f"step_{i}")
            step_type = raw.get("type", "chat")
            if step_type not in VALID_STEP_TYPES:
                raise ValueError(
                    f"Step '{step_id}': invalid type '{step_type}'. "
                    f"Must be one of {VALID_STEP_TYPES}"
                )
            step = WorkflowStep(
                id=step_id,
                type=step_type,
                depends_on=raw.get("depends_on", []),
                inputs=raw.get("inputs", {}),
                outputs=raw.get("outputs", []),
                condition=raw.get("condition"),
                if_true=raw.get("if_true"),
                if_false=raw.get("if_false"),
            )
            steps.append(step)

        # Validate unique IDs
        ids = [s.id for s in steps]
        if len(ids) != len(set(ids)):
            dups = [x for x in ids if ids.count(x) > 1]
            raise ValueError(f"Duplicate step IDs: {set(dups)}")

        return steps

    def _validate_dag(self) -> None:
        """Validate the DAG: all depends_on references exist and no cycles."""
        for step in self.steps:
            for dep in step.depends_on:
                if dep not in self._step_map:
                    raise ValueError(
                        f"Step '{step.id}' depends on unknown step '{dep}'"
                    )
        # Check for cycles via topological sort
        self._topological_sort()

    def _topological_sort(self) -> list[str]:
        """Kahn's algorithm topological sort (inspired by Paseo execution-order.ts).

        Returns:
            List of step IDs in execution order.

        Raises:
            ValueError: If a cycle is detected.
        """
        # Build in-degree map
        in_degree: dict[str, int] = {s.id: 0 for s in self.steps}
        dependents: dict[str, list[str]] = {s.id: [] for s in self.steps}

        for step in self.steps:
            for dep in step.depends_on:
                in_degree[step.id] += 1
                dependents[dep].append(step.id)

        # Start with nodes that have no dependencies
        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        order: list[str] = []

        while queue:
            # Sort for deterministic ordering
            queue.sort()
            node = queue.pop(0)
            order.append(node)
            for dependent in dependents[node]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(order) != len(self.steps):
            remaining = set(self._step_map.keys()) - set(order)
            raise ValueError(
                f"Circular dependency detected among steps: {remaining}"
            )

        return order

    async def execute(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute the workflow in topological order.

        Supports parallel execution of independent steps and variable
        interpolation via ``{{ }}`` template syntax.

        Args:
            context: Initial variables (e.g. user_input, model overrides).

        Returns:
            dict with keys:
            - success: bool
            - steps: list of per-step results
            - outputs: dict of all output variables
            - error: str (if failed)
        """
        ctx = context or {}
        step_outputs: dict[str, Any] = {}
        results: list[dict[str, Any]] = []
        order = self._topological_sort()

        completed: set[str] = set()

        while len(completed) < len(order):
            # Find steps whose dependencies are all completed
            ready = [
                sid for sid in order
                if sid not in completed
                and all(d in completed for d in self._step_map[sid].depends_on)
            ]

            if not ready:
                return {
                    "success": False,
                    "error": "No ready steps — possible deadlock",
                    "steps": results,
                    "outputs": step_outputs,
                }

            # Execute ready steps in parallel
            tasks = []
            for sid in ready:
                step = self._step_map[sid]
                rendered_inputs = self._render_value(step.inputs, ctx, step_outputs)
                tasks.append(self._execute_step(step, rendered_inputs, ctx, step_outputs))

            step_results = await asyncio.gather(*tasks, return_exceptions=True)

            for sid, result in zip(ready, step_results):
                step = self._step_map[sid]
                if isinstance(result, Exception):
                    results.append({
                        "step_id": sid,
                        "success": False,
                        "error": str(result),
                    })
                    return {
                        "success": False,
                        "error": f"Step '{sid}' failed: {result}",
                        "steps": results,
                        "outputs": step_outputs,
                    }

                # Store outputs
                output_val = result.get("output", "")
                for out_name in step.outputs:
                    step_outputs[f"steps.{sid}.outputs.{out_name}"] = output_val
                # Also store the raw output under the step ID
                step_outputs[f"steps.{sid}.output"] = output_val

                results.append({
                    "step_id": sid,
                    "success": True,
                    "output": str(output_val)[:2000],
                })
                completed.add(sid)

        return {
            "success": True,
            "steps": results,
            "outputs": step_outputs,
        }

    async def _execute_step(
        self,
        step: WorkflowStep,
        inputs: dict[str, Any],
        context: dict[str, Any],
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single step based on its type."""
        if step.type == "conditional":
            return await self._execute_conditional(step, inputs, context, outputs)
        elif step.type == "moa":
            return await self._execute_moa(inputs)
        elif step.type == "chat":
            return await self._execute_chat(inputs)
        elif step.type == "discover":
            return await self._execute_discover(inputs)
        elif step.type == "transform":
            return await self._execute_transform(inputs)
        else:
            return {"output": "", "error": f"Unknown step type: {step.type}"}

    async def _execute_moa(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a MOA orchestration step."""
        prompt = inputs.get("prompt", inputs.get("message", ""))
        strategy = inputs.get("strategy", "parallel")
        models = inputs.get("models", ["auto"])
        preset = inputs.get("preset", "balanced")

        base_url = _get_gateway_url()
        result = await _http_post(
            f"{base_url}/v1/moa/execute",
            {
                "model": "auto",
                "messages": [{"role": "user", "content": str(prompt)}],
                "preset": preset,
                "strategy": strategy,
            },
        )
        # P2-7: Check for HTTP error in result
        if "error" in result:
            return {"output": "", "raw": result, "success": False, "error": result["error"]}
        content = result.get("final_content") or result.get("aggregated_content", "")
        return {"output": content, "raw": result, "success": True}

    async def _execute_chat(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a chat completion step."""
        model = inputs.get("model", "auto")
        messages = inputs.get("messages", [])
        if not messages and "prompt" in inputs:
            messages = [{"role": "user", "content": inputs["prompt"]}]

        base_url = _get_gateway_url()
        result = await _http_post(
            f"{base_url}/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "stream": False,
            },
        )
        # P2-7: Check for HTTP error in result
        if "error" in result:
            return {"output": "", "raw": result, "success": False, "error": result["error"]}
        choices = result.get("choices", [])
        content = ""
        if choices and isinstance(choices, list):
            content = choices[0].get("message", {}).get("content", "")
        return {"output": content, "raw": result, "success": True}

    async def _execute_discover(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a model discovery step."""
        from moa_gateway.discovery.discovery_engine import FreeModelDiscoveryEngine

        engine = FreeModelDiscoveryEngine()
        models = await engine.discover_all()
        model_list = [
            {"platform": m.platform_id, "model": m.model_id, "tier": m.inferred_tier}
            for m in models[:20]
        ]
        return {
            "output": f"Discovered {len(models)} models",
            "models": model_list,
            "total": len(models),
        }

    async def _execute_transform(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a transformation step (template rendering or Python expression)."""
        template = inputs.get("template", inputs.get("prompt", ""))
        variables = inputs.get("variables", {})
        result = template
        for key, val in variables.items():
            result = result.replace("{{" + key + "}}", str(val))
        return {"output": result}

    async def _execute_conditional(
        self,
        step: WorkflowStep,
        inputs: dict[str, Any],
        context: dict[str, Any],
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Evaluate a condition and execute the appropriate branch."""
        condition = step.condition or ""
        condition_result = self._evaluate_condition(condition, context, outputs)

        branch = step.if_true if condition_result else step.if_false
        if branch is None:
            return {"output": "", "branch": "true" if condition_result else "false"}

        # Execute the branch as a sub-step
        branch_type = branch.get("type", "chat")
        branch_inputs = self._render_value(branch.get("inputs", {}), context, outputs)

        if branch_type == "moa":
            return await self._execute_moa(branch_inputs)
        elif branch_type == "chat":
            return await self._execute_chat(branch_inputs)
        elif branch_type == "transform":
            return await self._execute_transform(branch_inputs)
        else:
            return {"output": "", "error": f"Unknown branch type: {branch_type}"}

    def _evaluate_condition(
        self,
        condition: str,
        context: dict[str, Any],
        outputs: dict[str, Any],
    ) -> bool:
        """Evaluate a condition expression.

        Supports patterns like:
        - ``{{steps.review.outputs.review_text | length > 100}}``
        - ``{{user_input | length > 0}}``
        - Simple boolean values
        """
        # Render variables in the condition
        rendered = self._render_value(condition, context, outputs)
        rendered_str = str(rendered).strip()

        # Remove surrounding {{ }} if present
        if rendered_str.startswith("{{") and rendered_str.endswith("}}"):
            rendered_str = rendered_str[2:-2].strip()

        # Try simple boolean
        if rendered_str.lower() in ("true", "1", "yes"):
            return True
        if rendered_str.lower() in ("false", "0", "no", "", "none"):
            return False

        # Try to evaluate as a comparison expression
        # Pattern: value OP comparator
        for op_str in [">=", "<=", "==", "!=", ">", "<"]:
            if op_str in rendered_str:
                parts = rendered_str.split(op_str, 1)
                if len(parts) == 2:
                    left = parts[0].strip().strip("'\"")
                    right = parts[1].strip().strip("'\"")
                    try:
                        left_val: Any = left
                        right_val: Any = right
                        # Try numeric comparison
                        if left.replace(".", "", 1).lstrip("-").isdigit():
                            left_val = float(left)
                        if right.replace(".", "", 1).lstrip("-").isdigit():
                            right_val = float(right)
                        elif right.isdigit():
                            right_val = int(right)

                        if op_str == ">":
                            return _safe_gt(left_val, right_val)
                        elif op_str == "<":
                            return _safe_lt(left_val, right_val)
                        elif op_str == ">=":
                            return _safe_gte(left_val, right_val)
                        elif op_str == "<=":
                            return _safe_lte(left_val, right_val)
                        elif op_str == "==":
                            return left_val == right_val
                        elif op_str == "!=":
                            return left_val != right_val
                    except Exception:  # noqa: BLE001
                        # Fall through to string comparison
                        if op_str == "==":
                            return left == right
                        elif op_str == "!=":
                            return left != right

        # Default: non-empty string is truthy
        return bool(rendered_str)

    def _render_value(
        self,
        value: Any,
        context: dict[str, Any],
        outputs: dict[str, Any],
    ) -> Any:
        """Recursively render ``{{ }}`` templates in a value.

        Handles strings, dicts, and lists. Looks up variables in
        context (user-provided) and outputs (step results).
        """
        if isinstance(value, str):
            return self._render_string(value, context, outputs)
        elif isinstance(value, dict):
            return {
                k: self._render_value(v, context, outputs)
                for k, v in value.items()
            }
        elif isinstance(value, list):
            return [self._render_value(v, context, outputs) for v in value]
        else:
            return value

    def _render_string(
        self,
        template: str,
        context: dict[str, Any],
        outputs: dict[str, Any],
    ) -> str:
        """Render ``{{ variable_path }}`` patterns in a string.

        Variable lookup order:
        1. outputs (step results) — e.g. ``steps.generate.output``
        2. context (user inputs) — e.g. ``user_input``
        3. context with dotted path — e.g. ``context.model``
        """
        # Find all {{ ... }} patterns
        pattern = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")

        def replacer(match: re.Match) -> str:
            var_path = match.group(1).strip()
            # Handle pipe operators (e.g., "var | length")
            if "|" in var_path:
                parts = var_path.split("|", 1)
                var_path = parts[0].strip()
                transform = parts[1].strip()
                value = self._lookup_variable(var_path, context, outputs)
                if transform == "length":
                    return str(len(value) if value is not None else 0)
                return str(value)

            value = self._lookup_variable(var_path, context, outputs)
            return str(value) if value is not None else match.group(0)

        return pattern.sub(replacer, template)

    def _lookup_variable(
        self,
        path: str,
        context: dict[str, Any],
        outputs: dict[str, Any],
    ) -> Any:
        """Look up a variable by dotted path in context and outputs."""
        # Check outputs first (step results)
        if path in outputs:
            return outputs[path]

        # Check context directly
        if path in context:
            return context[path]

        # Try dotted path in context
        parts = path.split(".")
        current: Any = context
        for part in parts:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
            if current is None:
                return None
        return current


# --- Module-level helpers ---


def _get_gateway_url() -> str:
    """Get the gateway base URL from environment."""
    import os
    return os.environ.get("MOA_GATEWAY_URL", "http://127.0.0.1:8910")


async def _http_post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    """Make an async HTTP POST request and return JSON response."""
    import httpx

    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(url, json=body)
        if resp.status_code == 200:
            return resp.json()
        return {
            "error": f"HTTP {resp.status_code}",
            "detail": resp.text[:500],
        }


def _safe_gt(a: Any, b: Any) -> bool:
    try:
        return a > b
    except TypeError:
        return len(str(a)) > len(str(b))


def _safe_lt(a: Any, b: Any) -> bool:
    try:
        return a < b
    except TypeError:
        return len(str(a)) < len(str(b))


def _safe_gte(a: Any, b: Any) -> bool:
    try:
        return a >= b
    except TypeError:
        return len(str(a)) >= len(str(b))


def _safe_lte(a: Any, b: Any) -> bool:
    try:
        return a <= b
    except TypeError:
        return len(str(a)) <= len(str(b))
