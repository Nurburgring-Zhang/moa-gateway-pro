"""Plan-Execute loop — decompose, execute, synthesize.

Enhanced with Paseo-style scenario orchestration (P1-4):
- ScenarioStep: structured step with dependencies and expected outputs
- ScenarioExecutor: topological sort + parallel/serial execution
- run_scenario(): execute multi-step scenarios with context passing
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from .base import AgentContext, AgentLoop, LoopResult, ToolCall, ToolExecutor, ToolResult

logger = logging.getLogger(__name__)

LlmCall = Callable[..., Awaitable[str]]

PLAN_SYSTEM_PROMPT = """\
You are a planning agent. Decompose the user's request into a JSON array of steps.

Each step is an object with:
  - "description": what this step does
  - "tool": name of the tool to use (or "llm" to reason with the LLM)
  - "arguments": JSON object of tool arguments (or the prompt if tool is "llm")
  - "depends_on": (optional) list of step indices (0-based) that must complete first
  - "acceptance_criteria": (optional) list of criteria to verify step success

Available tools: {tools}

Respond with ONLY the JSON array, no extra text. Example:
[{{"description": "Search for info", "tool": "web_search", "arguments": {{"query": "example"}}, "depends_on": []}}]
"""

SYNTHESIS_SYSTEM_PROMPT = """\
You are a synthesis agent. Given the original request and the results of each step,\
 produce a coherent final answer.

Original request:
{request}

Step results:
{results}

Provide a clear, comprehensive final answer.
"""


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """Extract a JSON array from LLM text, tolerating markdown fences."""
    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text).strip()
    # Find the first [ ... ] block
    match = re.search(r"\[.*\]", cleaned, re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse plan JSON: %s", exc)
    return []


# ============ P1-4: Scenario Orchestration ============


@dataclass
class ScenarioStep:
    """A single step in a test scenario (Paseo Task-inspired).

    Attributes:
        id: Unique step identifier.
        action: Action type -- "plan", "execute", "verify", or "transform".
        depends_on: IDs of steps that must complete before this one.
        inputs: Input parameters for this step.
        expected_output: Optional expected output for validation.
    """

    id: str
    action: str  # plan / execute / verify / transform
    depends_on: list[str] = field(default_factory=list)
    inputs: dict[str, Any] = field(default_factory=dict)
    expected_output: dict[str, Any] | None = None


def _topological_sort(steps: list[ScenarioStep]) -> list[list[ScenarioStep]]:
    """Topologically sort scenario steps into execution waves.

    Inspired by Paseo's computeExecutionOrder() -- returns a list of waves,
    where each wave contains steps that can execute in parallel (all deps
    satisfied).

    Returns:
        List of waves (each wave is a list of steps to execute in parallel).

    Raises:
        ValueError: If a circular dependency is detected.
    """
    step_map = {s.id: s for s in steps}
    completed: set[str] = set()
    waves: list[list[ScenarioStep]] = []
    remaining = list(steps)

    while remaining:
        ready = [
            s for s in remaining
            if all(dep in completed for dep in s.depends_on)
        ]
        if not ready:
            remaining_ids = [s.id for s in remaining]
            raise ValueError(
                f"Circular or missing dependency detected. "
                f"Unresolved steps: {remaining_ids}"
            )
        waves.append(ready)
        for s in ready:
            completed.add(s.id)
            remaining.remove(s)

    return waves


class ScenarioExecutor:
    """Scenario orchestration executor -- embedded in PlanExecuteLoop.

    Executes scenario steps in topological order, with parallel execution
    of independent steps within each wave.  Context (outputs) from earlier
    steps are available as inputs to later steps.
    """

    def __init__(self, plan_execute_loop: "PlanExecuteLoop") -> None:
        self._loop = plan_execute_loop

    async def execute_scenario(
        self,
        steps: list[ScenarioStep],
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute scenario steps in topological order.

        Args:
            steps: List of ScenarioStep defining the scenario.
            context: Initial context dictionary (outputs from prior runs).

        Returns:
            dict with keys:
            - success: bool
            - step_results: dict[str, Any] (step_id -> result)
            - context: dict (final context with all outputs)
            - error: str (empty if success)
        """
        ctx = dict(context) if context else {}
        step_results: dict[str, Any] = {}
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []

        try:
            waves = _topological_sort(steps)
        except ValueError as exc:
            return {
                "success": False,
                "step_results": step_results,
                "context": ctx,
                "error": str(exc),
            }

        for wave_idx, wave in enumerate(waves):
            logger.info(
                "Scenario wave %d: %d step(s) -- %s",
                wave_idx + 1,
                len(wave),
                [s.id for s in wave],
            )

            if len(wave) == 1:
                result = await self._execute_step(wave[0], ctx)
                self._collect_results(
                    wave[0], result, step_results, ctx,
                    all_tool_calls, all_tool_results,
                )
            else:
                tasks = [self._execute_step(s, ctx) for s in wave]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for step, result in zip(wave, results):
                    if isinstance(result, Exception):
                        step_results[step.id] = {
                            "success": False,
                            "error": str(result),
                            "output": "",
                        }
                        ctx[f"_step_{step.id}_error"] = str(result)
                    else:
                        self._collect_results(
                            step, result, step_results, ctx,
                            all_tool_calls, all_tool_results,
                        )

        # Verify expected outputs
        for step in steps:
            if step.expected_output and step.id in step_results:
                self._verify_expected_output(
                    step, step_results[step.id]
                )

        overall_success = all(
            isinstance(r, dict) and r.get("success", True)
            and not r.get("verification_failed", False)
            for r in step_results.values()
        )

        return {
            "success": overall_success,
            "step_results": step_results,
            "context": ctx,
            "tool_calls": all_tool_calls,
            "tool_results": all_tool_results,
            "error": "" if overall_success else "One or more steps failed",
        }

    async def _execute_step(
        self,
        step: ScenarioStep,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single scenario step.

        Uses the PlanExecuteLoop's LLM and tool executor.
        Context variables are available via {{var_name}} template substitution.
        """
        resolved_inputs = self._resolve_templates(step.inputs, context)
        action = step.action.lower()

        if action == "plan":
            prompt = resolved_inputs.get("prompt", resolved_inputs.get("query", ""))
            messages = [{"role": "user", "content": str(prompt)}]
            try:
                response = await self._loop._llm_call(messages)
                return {"success": True, "output": response, "action": "plan"}
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "output": "", "error": str(exc), "action": "plan"}

        elif action == "execute":
            tool_name = resolved_inputs.get("tool", "llm")
            arguments = resolved_inputs.get("arguments", {})
            if not isinstance(arguments, dict):
                arguments = {"query": str(arguments)}

            if tool_name == "llm":
                llm_prompt = arguments.get("query", arguments.get("prompt", ""))
                try:
                    response = await self._loop._llm_call([
                        {"role": "user", "content": str(llm_prompt)}
                    ])
                    return {"success": True, "output": response, "action": "execute"}
                except Exception as exc:  # noqa: BLE001
                    return {"success": False, "output": "", "error": str(exc), "action": "execute"}
            else:
                tool_call = ToolCall(name=tool_name, arguments=arguments)
                tool_result = await self._loop._tool_executor.execute(tool_call)
                return {
                    "success": tool_result.success,
                    "output": tool_result.output,
                    "error": tool_result.error,
                    "tool_call": tool_call,
                    "tool_result": tool_result,
                    "action": "execute",
                }

        elif action == "verify":
            verify_tool = resolved_inputs.get("tool", "api_verify")
            tool_call = ToolCall(name=verify_tool, arguments=resolved_inputs)
            tool_result = await self._loop._tool_executor.execute(tool_call)
            return {
                "success": tool_result.success,
                "output": tool_result.output,
                "error": tool_result.error,
                "tool_call": tool_call,
                "tool_result": tool_result,
                "action": "verify",
            }

        elif action == "transform":
            prompt = resolved_inputs.get("prompt", resolved_inputs.get("query", ""))
            messages = [{"role": "user", "content": str(prompt)}]
            try:
                response = await self._loop._llm_call(messages)
                return {"success": True, "output": response, "action": "transform"}
            except Exception as exc:  # noqa: BLE001
                return {"success": False, "output": "", "error": str(exc), "action": "transform"}

        else:
            return {
                "success": False,
                "output": "",
                "error": f"Unknown action: {action}",
                "action": action,
            }

    def _resolve_templates(
        self,
        inputs: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Resolve {{var_name}} template variables in input values."""
        resolved: dict[str, Any] = {}
        for key, value in inputs.items():
            if isinstance(value, str):
                resolved_val = value
                for ctx_key, ctx_val in context.items():
                    if isinstance(ctx_val, str):
                        resolved_val = resolved_val.replace(
                            "{{" + ctx_key + "}}", ctx_val
                        )
                resolved[key] = resolved_val
            elif isinstance(value, dict):
                resolved[key] = self._resolve_templates(value, context)
            else:
                resolved[key] = value
        return resolved

    def _collect_results(
        self,
        step: ScenarioStep,
        result: dict[str, Any],
        step_results: dict[str, Any],
        context: dict[str, Any],
        all_tool_calls: list[ToolCall],
        all_tool_results: list[ToolResult],
    ) -> None:
        """Collect step results into the scenario context."""
        step_results[step.id] = result
        context[f"_step_{step.id}_output"] = result.get("output", "")
        context[step.id] = result.get("output", "")
        if "tool_call" in result:
            all_tool_calls.append(result["tool_call"])
        if "tool_result" in result:
            all_tool_results.append(result["tool_result"])

    def _verify_expected_output(
        self,
        step: ScenarioStep,
        result: dict[str, Any],
    ) -> None:
        """Verify step output against expected output."""
        if not step.expected_output:
            return
        output = result.get("output", "")
        for key, expected in step.expected_output.items():
            if key == "contains":
                if isinstance(expected, str) and expected not in output:
                    result["verification_failed"] = True
                    result["verification_error"] = (
                        f"Expected output to contain '{expected}'"
                    )
            elif key == "success":
                if result.get("success") != expected:
                    result["verification_failed"] = True
                    result["verification_error"] = (
                        f"Expected success={expected}, got {result.get('success')}"
                    )


class PlanExecuteLoop(AgentLoop):
    """Plan-Execute loop: plan -> execute each step -> synthesize.

    Enhanced with scenario orchestration (P1-4):
    - run_scenario(): execute multi-step scenarios with dependencies
    - ScenarioExecutor: topological sort + parallel execution
    """

    def __init__(
        self,
        llm_call: LlmCall,
        tool_executor: ToolExecutor | None = None,
        max_iterations: int = 10,
    ) -> None:
        super().__init__(tool_executor)
        self._llm_call = llm_call
        self._default_max_iterations = max_iterations
        self._scenario_executor = ScenarioExecutor(self)

    async def run(
        self,
        messages: list[dict[str, Any]],
        context: AgentContext | None = None,
    ) -> LoopResult:
        """Execute the Plan-Execute loop."""
        ctx = context or AgentContext(max_iterations=self._default_max_iterations)
        tool_names = self._tool_executor.list_tools()
        tools_str = ", ".join(tool_names) if tool_names else "(none)"

        # --- Phase 1: Plan ---
        user_request = messages[-1].get("content", "") if messages else ""
        plan_messages = [
            {"role": "system", "content": PLAN_SYSTEM_PROMPT.format(tools=tools_str)},
            {"role": "user", "content": user_request},
        ]

        try:
            plan_response = await self._llm_call(plan_messages)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Plan generation failed")
            return LoopResult(
                success=False,
                final_response="",
                iterations=0,
                error=f"Plan generation failed: {exc}",
            )

        steps = _extract_json_array(plan_response)
        if not steps:
            return LoopResult(
                success=False,
                final_response="",
                iterations=0,
                error="Failed to parse plan from LLM response",
            )

        logger.info("Plan generated with %d steps", len(steps))

        # --- Phase 2: Execute ---
        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        step_results: list[str] = []
        iteration = 0

        for i, step in enumerate(steps):
            iteration += 1
            if iteration > ctx.max_iterations:
                logger.warning("Plan-Execute hit max_iterations during execution")
                break

            step_desc = step.get("description", f"Step {i + 1}")
            tool_name = step.get("tool", "llm")
            arguments = step.get("arguments", {})

            if not isinstance(arguments, dict):
                arguments = {"query": str(arguments)}

            logger.info("Executing step %d: %s (tool=%s)", i + 1, step_desc, tool_name)

            if tool_name == "llm":
                # Use LLM directly for this step
                llm_prompt = arguments.get("query", arguments.get("prompt", step_desc))
                try:
                    step_result = await self._llm_call([
                        {"role": "user", "content": str(llm_prompt)}
                    ])
                    step_results.append(f"Step {i + 1} ({step_desc}): {step_result}")
                except Exception as exc:  # noqa: BLE001
                    step_results.append(f"Step {i + 1} ({step_desc}): ERROR - {exc}")
            else:
                # Execute via tool executor
                tool_call = ToolCall(name=tool_name, arguments=arguments)
                all_tool_calls.append(tool_call)
                tool_result = await self._tool_executor.execute(tool_call)
                all_tool_results.append(tool_result)

                if tool_result.success:
                    step_results.append(
                        f"Step {i + 1} ({step_desc}): {tool_result.output}"
                    )
                else:
                    step_results.append(
                        f"Step {i + 1} ({step_desc}): FAILED - {tool_result.error}"
                    )

        # --- Phase 3: Synthesize ---
        results_text = "\n".join(step_results)
        synthesis_messages = [
            {
                "role": "system",
                "content": SYNTHESIS_SYSTEM_PROMPT.format(
                    request=user_request,
                    results=results_text,
                ),
            },
            {"role": "user", "content": "Provide the final answer."},
        ]

        try:
            final_response = await self._llm_call(synthesis_messages)
        except Exception:  # noqa: BLE001
            logger.exception("Synthesis failed")
            final_response = results_text

        return LoopResult(
            success=True,
            final_response=final_response,
            iterations=iteration,
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
        )

    async def run_scenario(
        self,
        steps: list[ScenarioStep],
        context: dict[str, Any] | None = None,
    ) -> LoopResult:
        """Execute a multi-step scenario with dependency ordering.

        Inspired by Paseo's Task dependency graph and execution-order.ts.

        Args:
            steps: List of ScenarioStep defining the scenario.
            context: Initial context (outputs from prior runs).

        Returns:
            LoopResult with scenario execution results.
        """
        result = await self._scenario_executor.execute_scenario(steps, context)

        step_summaries = []
        for step_id, step_result in result.get("step_results", {}).items():
            success = step_result.get("success", True)
            output = step_result.get("output", "")[:500]
            status = "OK" if success else "FAIL"
            step_summaries.append(f"[{step_id}] {status}: {output}")

        final_response = "\n".join(step_summaries) if step_summaries else "(no steps)"

        return LoopResult(
            success=result["success"],
            final_response=final_response,
            iterations=len(result.get("step_results", {})),
            tool_calls=result.get("tool_calls", []),
            tool_results=result.get("tool_results", []),
            error=result.get("error", ""),
        )
