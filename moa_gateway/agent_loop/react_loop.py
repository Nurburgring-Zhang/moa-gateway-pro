"""ReAct loop — Reason → Act → Observe iteration."""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Awaitable, Callable

from .base import AgentContext, AgentLoop, LoopResult, ToolCall, ToolExecutor, ToolResult

logger = logging.getLogger(__name__)

# Type alias: async (messages, **params) -> str
LlmCall = Callable[..., Awaitable[str]]

REACT_SYSTEM_PROMPT = """\
You are a ReAct agent. For each step, respond in EXACTLY one of these two formats:

FORMAT A — when you need to use a tool:
Thought: <your reasoning about what to do next>
Action: <tool_name>
Action Input: <JSON object of arguments>

FORMAT B — when you have the final answer:
Thought: <your reasoning>
Final Answer: <your answer to the user>

Rules:
- Always start with "Thought:".
- Use only tools from the provided list. If no tool is needed, use Final Answer.
- Action Input must be a valid JSON object on a single line.
- Be concise. Do not repeat observations.
"""

# Regex patterns for parsing LLM output
_THOUGHT_RE = re.compile(r"Thought:\s*(.*?)(?=\n(?:Action:|Final Answer:)|\Z)", re.DOTALL)
_ACTION_RE = re.compile(r"Action:\s*(.+?)\s*\n\s*Action Input:\s*(.+)", re.DOTALL)
_FINAL_RE = re.compile(r"Final Answer:\s*(.*)", re.DOTALL)


def _parse_react_output(text: str) -> dict[str, Any]:
    """Parse LLM text into thought / action / action_input / final_answer."""
    thought_match = _THOUGHT_RE.search(text)
    thought = thought_match.group(1).strip() if thought_match else ""

    action_match = _ACTION_RE.search(text)
    final_match = _FINAL_RE.search(text)

    if final_match:
        return {
            "thought": thought,
            "final_answer": final_match.group(1).strip(),
        }

    if action_match:
        action_name = action_match.group(1).strip()
        action_input_raw = action_match.group(2).strip()
        try:
            action_input = json.loads(action_input_raw)
        except (json.JSONDecodeError, ValueError):
            action_input = {"query": action_input_raw}
        return {
            "thought": thought,
            "action": action_name,
            "action_input": action_input if isinstance(action_input, dict) else {},
        }

    # Fallback: treat entire text as final answer if no pattern matched
    return {"thought": thought or text[:200], "final_answer": text.strip()}


class ReActLoop(AgentLoop):
    """ReAct loop — Thought -> Action -> Observation iteration."""

    def __init__(
        self,
        llm_call: LlmCall,
        tool_executor: ToolExecutor | None = None,
        max_iterations: int = 10,
    ) -> None:
        super().__init__(tool_executor)
        self._llm_call = llm_call
        self._default_max_iterations = max_iterations

    def _build_system_message(self, tools: list[str]) -> dict[str, str]:
        tool_list = ", ".join(tools) if tools else "(no tools available)"
        return {
            "role": "system",
            "content": f"{REACT_SYSTEM_PROMPT}\nAvailable tools: {tool_list}",
        }

    async def run(
        self,
        messages: list[dict[str, Any]],
        context: AgentContext | None = None,
    ) -> LoopResult:
        """Execute the ReAct loop."""
        ctx = context or AgentContext(max_iterations=self._default_max_iterations)
        tool_names = self._tool_executor.list_tools()

        # Build the working message list
        work_messages: list[dict[str, Any]] = [
            self._build_system_message(tool_names)
        ] + list(messages)

        all_tool_calls: list[ToolCall] = []
        all_tool_results: list[ToolResult] = []
        total_cost = 0.0
        total_tokens = 0

        for iteration in range(1, ctx.max_iterations + 1):
            ctx.current_iteration = iteration

            # --- Call LLM ---
            try:
                llm_response = await self._llm_call(work_messages)
            except Exception as exc:  # noqa: BLE001
                logger.exception("LLM call failed at iteration %d", iteration)
                return LoopResult(
                    success=False,
                    final_response="",
                    iterations=iteration,
                    tool_calls=all_tool_calls,
                    tool_results=all_tool_results,
                    total_cost=total_cost,
                    total_tokens=total_tokens,
                    error=f"LLM call failed: {exc}",
                )

            ctx.add_message("assistant", llm_response, iteration=iteration)

            # --- Parse LLM output ---
            parsed = _parse_react_output(llm_response)

            # Check for final answer
            if "final_answer" in parsed:
                logger.info("ReAct loop completed at iteration %d", iteration)
                return LoopResult(
                    success=True,
                    final_response=parsed["final_answer"],
                    iterations=iteration,
                    tool_calls=all_tool_calls,
                    tool_results=all_tool_results,
                    total_cost=total_cost,
                    total_tokens=total_tokens,
                )

            # Check for tool action
            action_name = parsed.get("action", "")
            action_input = parsed.get("action_input", {})

            if not action_name:
                # LLM didn't produce action or final answer
                return LoopResult(
                    success=True,
                    final_response=llm_response.strip(),
                    iterations=iteration,
                    tool_calls=all_tool_calls,
                    tool_results=all_tool_results,
                    total_cost=total_cost,
                    total_tokens=total_tokens,
                )

            # --- Execute tool ---
            tool_call = ToolCall(name=action_name, arguments=action_input)
            all_tool_calls.append(tool_call)

            logger.info("Iter %d: calling tool %s with %s", iteration, action_name, action_input)
            tool_result = await self._tool_executor.execute(tool_call)
            all_tool_results.append(tool_result)

            # --- Feed observation back to LLM ---
            observation = (
                tool_result.output if tool_result.success
                else f"Error: {tool_result.error}"
            )
            work_messages.append({"role": "assistant", "content": llm_response})
            work_messages.append({
                "role": "user",
                "content": f"Observation: {observation}",
            })

        # Max iterations reached
        logger.warning("ReAct loop hit max_iterations (%d)", ctx.max_iterations)
        return LoopResult(
            success=False,
            final_response="Max iterations reached without final answer.",
            iterations=ctx.max_iterations,
            tool_calls=all_tool_calls,
            tool_results=all_tool_results,
            total_cost=total_cost,
            total_tokens=total_tokens,
            error="max_iterations_exceeded",
        )
