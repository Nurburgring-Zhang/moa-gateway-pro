"""Abstract interfaces and base data structures for agent loops."""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """A tool invocation request produced by the LLM."""

    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Result of executing a single tool call."""

    name: str
    success: bool
    output: str
    error: str = ""
    latency_ms: float = 0.0


@dataclass
class AgentContext:
    """Runtime context shared across loop iterations (blackboard pattern)."""

    variables: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    max_iterations: int = 10
    current_iteration: int = 0

    def add_message(self, role: str, content: str, **extra: Any) -> dict[str, Any]:
        """Append a message to the conversation history."""
        msg = {"role": role, "content": content, "timestamp": time.time(), **extra}
        self.history.append(msg)
        return msg

    def set(self, key: str, value: Any) -> None:
        """Set a shared variable."""
        self.variables[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a shared variable."""
        return self.variables.get(key, default)


@dataclass
class LoopResult:
    """Final result returned by an agent loop."""

    success: bool
    final_response: str
    iterations: int
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)
    total_cost: float = 0.0
    total_tokens: int = 0
    error: str = ""


class ToolExecutor:
    """Unified tool-call executor with registration and async dispatch."""

    def __init__(self) -> None:
        self._tools: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._descriptions: dict[str, str] = {}

    def register(
        self,
        name: str,
        handler: Callable[..., Awaitable[Any]],
        description: str = "",
    ) -> None:
        """Register an async tool handler."""
        self._tools[name] = handler
        self._descriptions[name] = description

    def unregister(self, name: str) -> None:
        """Remove a previously registered tool."""
        self._tools.pop(name, None)
        self._descriptions.pop(name, None)

    async def execute(self, call: ToolCall) -> ToolResult:
        """Execute a *call* and return a :class:`ToolResult`."""
        start = time.time()
        try:
            handler = self._tools.get(call.name)
            if handler is None:
                return ToolResult(
                    name=call.name,
                    success=False,
                    output="",
                    error=f"Unknown tool: {call.name}",
                )
            result = await handler(**call.arguments)
            latency = (time.time() - start) * 1000
            return ToolResult(
                name=call.name,
                success=True,
                output=str(result),
                latency_ms=latency,
            )
        except Exception as exc:  # noqa: BLE001
            latency = (time.time() - start) * 1000
            logger.exception("Tool %s failed", call.name)
            return ToolResult(
                name=call.name,
                success=False,
                output="",
                error=str(exc),
                latency_ms=latency,
            )

    def list_tools(self) -> list[str]:
        """Return names of all registered tools."""
        return list(self._tools.keys())

    def tool_specs(self) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool specifications."""
        return [
            {"name": name, "description": desc or name}
            for name, desc in self._descriptions.items()
        ]


class AgentLoop(ABC):
    """Abstract base class for all agent loops."""

    def __init__(self, tool_executor: ToolExecutor | None = None) -> None:
        self._tool_executor = tool_executor or ToolExecutor()

    @property
    def tool_executor(self) -> ToolExecutor:
        return self._tool_executor

    @abstractmethod
    async def run(
        self,
        messages: list[dict[str, Any]],
        context: AgentContext | None = None,
    ) -> LoopResult:
        """Execute the agent loop and return a :class:`LoopResult`."""
        raise NotImplementedError
