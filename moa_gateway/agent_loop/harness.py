"""Agent runtime harness — manages loops, tools, and execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .base import (
    AgentContext,
    AgentLoop,
    LlmOutcome,
    LoopResult,
    ToolExecutor,
)
from .plan_execute_loop import PlanExecuteLoop
from .react_loop import ReActLoop

logger = logging.getLogger(__name__)

LlmCall = Callable[..., Awaitable[str | LlmOutcome]]


class AgentHarness:
    """Agent runtime framework — register loops, tools, and run.

    Tool sourcing:
    - ``tools_source="builtin"`` (default): tools are registered manually via
      :meth:`register_tool` / the skills helpers. Behavior is unchanged.
    - ``tools_source="tool_hub"``: at :meth:`run` time the harness pulls the
      available tools from the unified :class:`capability.tool_hub.ToolHub`,
      filtered by the caller role, and registers them onto the shared
      executor. ``hub_tools`` (run kwarg) optionally restricts the subset by
      namespaced name.
    """

    def __init__(
        self,
        llm_call: LlmCall | None = None,
        tools_source: str = "builtin",
        caller_role: str = "user",
    ) -> None:
        if tools_source not in ("builtin", "tool_hub"):
            raise ValueError(f"unknown tools_source: {tools_source!r}")
        self._tool_executor = ToolExecutor()
        self._loops: dict[str, AgentLoop] = {}
        self._llm_call = llm_call
        self._tools_source = tools_source
        self._caller_role = caller_role

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

    @property
    def tools_source(self) -> str:
        return self._tools_source

    @property
    def caller_role(self) -> str:
        return self._caller_role

    def _sync_tool_hub(self, caller_role: str, hub_tools: list[str] | None) -> None:
        """Register role-filtered ToolHub tools onto the shared executor."""
        from ..capability.tool_hub import get_tool_hub

        hub = get_tool_hub()
        hub.register_to_executor(self._tool_executor, caller_role, only=hub_tools)

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
            caller_role: role used for ToolHub filtering (tools_source="tool_hub").
            hub_tools: optional list of namespaced ToolHub tool names to restrict to.
        """
        loop = self._loops.get(loop_name)
        if loop is None:
            return LoopResult(
                success=False,
                final_response="",
                iterations=0,
                error=f"Unknown loop: {loop_name}",
            )

        # Resolve tool sourcing. For tool_hub, register role-filtered tools
        # onto the shared executor before the loop starts.
        caller_role = kwargs.pop("caller_role", None) or self._caller_role
        hub_tools = kwargs.pop("hub_tools", None)
        if self._tools_source == "tool_hub":
            self._sync_tool_hub(caller_role, hub_tools)

        context: AgentContext | None = kwargs.get("context")
        if context is None:
            max_iter = kwargs.get("max_iterations", 10)
            context = AgentContext(max_iterations=max_iter)

        logger.info("Running loop '%s' with %d messages", loop_name, len(messages))
        return await loop.run(messages, context)
