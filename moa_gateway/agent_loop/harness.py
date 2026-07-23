"""Agent runtime harness — manages loops, tools, and execution."""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from .base import (
    AgentContext,
    AgentLoop,
    LoopResult,
    ToolExecutor,
)
from .plan_execute_loop import PlanExecuteLoop
from .react_loop import ReActLoop

logger = logging.getLogger(__name__)

LlmCall = Callable[..., Awaitable[str]]


class AgentHarness:
    """Agent runtime framework — register loops, tools, and run."""

    def __init__(self, llm_call: LlmCall | None = None) -> None:
        self._tool_executor = ToolExecutor()
        self._loops: dict[str, AgentLoop] = {}
        self._llm_call = llm_call

        # Register default loops when llm_call is provided
        if llm_call:
            self.register_loop(
                "react",
                ReActLoop(llm_call, self._tool_executor),
            )
            self.register_loop(
                "plan_execute",
                PlanExecuteLoop(llm_call, self._tool_executor),
            )

    def register_loop(self, name: str, loop: AgentLoop) -> None:
        """Register or replace a named loop."""
        self._loops[name] = loop

    def unregister_loop(self, name: str) -> None:
        """Remove a named loop."""
        self._loops.pop(name, None)

    def list_loops(self) -> list[str]:
        """Return names of all registered loops."""
        return list(self._loops.keys())

    def register_tool(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        description: str = "",
    ) -> None:
        """Register a tool with the shared tool executor."""
        self._tool_executor.register(name, handler, description)

    def unregister_tool(self, name: str) -> None:
        """Remove a registered tool."""
        self._tool_executor.unregister(name)

    def list_tools(self) -> list[str]:
        """Return names of all registered tools."""
        return self._tool_executor.list_tools()

    async def run(
        self,
        messages: list[dict[str, Any]],
        loop_name: str = "react",
        **kwargs: Any,
    ) -> LoopResult:
        """Run the named loop with the given messages.

        Keyword args:
            max_iterations: override the default iteration cap.
            context: a pre-built AgentContext (optional).
        """
        loop = self._loops.get(loop_name)
        if loop is None:
            return LoopResult(
                success=False,
                final_response="",
                iterations=0,
                error=f"Unknown loop: {loop_name}",
            )

        context: AgentContext | None = kwargs.get("context")
        if context is None:
            max_iter = kwargs.get("max_iterations", 10)
            context = AgentContext(max_iterations=max_iter)

        logger.info("Running loop '%s' with %d messages", loop_name, len(messages))
        return await loop.run(messages, context)
