"""Agent Loop framework — Skills / Harness / Loop architecture.

Provides ReAct and Plan-Execute agent loops with a unified tool executor
and runtime harness.  Loops are decoupled from the model pool via an
injected ``llm_call`` async callback, avoiding circular dependencies.
"""
from __future__ import annotations

from .base import (
    AgentContext,
    AgentLoop,
    LlmOutcome,
    LlmUsage,
    LoopResult,
    ToolCall,
    ToolExecutor,
    ToolResult,
)
from .harness import AgentHarness
from .plan_execute_loop import PlanExecuteLoop
from .react_loop import ReActLoop

__all__ = [
    "AgentContext",
    "AgentHarness",
    "AgentLoop",
    "LlmOutcome",
    "LlmUsage",
    "LoopResult",
    "PlanExecuteLoop",
    "ReActLoop",
    "ToolCall",
    "ToolExecutor",
    "ToolResult",
]
