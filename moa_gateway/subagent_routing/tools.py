"""Subagent tool registration for the agent harness.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Source: ``lib/clacky/agent.rb`` — ``run_detached`` / ``fork_subagent`` exposed
as a callable tool, plus the runtime forbidden-tools guard.

Registration seam: :func:`register_subagent_tools` registers the
``invoke_lite_subagent`` tool on a harness' ``ToolExecutor``
(``moa_gateway.agent_loop.base.ToolExecutor.register(name, handler,
description)`` — handler is awaited with ``**call.arguments``). The main
thread performs the wiring; this module only provides the pieces.

Execution seam: the tool executes the forked task through a *runner*
registered via :func:`set_subagent_runner` (``async (task, decision) ->
str``). Without a runner the tool still performs the complete routing
decision and returns it flagged ``executed=False`` — callers get a truthful
dry-run instead of a silent no-op.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Awaitable, Callable

from .registry import get_lite_registry
from .routing import (
    SubagentContext,
    SubagentRouteDecision,
    filter_forbidden_tools,
    route_subagent_request,
)

logger = logging.getLogger(__name__)

__all__ = [
    "INVOKE_LITE_SUBAGENT_TOOL",
    "SubagentRunner",
    "set_subagent_runner",
    "get_subagent_runner",
    "is_tool_allowed",
    "invoke_lite_subagent",
    "register_subagent_tools",
]

INVOKE_LITE_SUBAGENT_TOOL = "invoke_lite_subagent"

_TOOL_DESCRIPTION = (
    "Run a one-off task on a forked lite subagent and return its final "
    "result text. Arguments: task (string; prefix with '/fork ' to force "
    "forking), model (optional, 'lite' keyword or a concrete model name), "
    "forbidden_tools (optional list of tool names to block), provider_id "
    "(optional, for lite pairing lookup), primary_model (optional, the "
    "caller's current model)."
)

# async (task: str, decision: SubagentRouteDecision) -> str final result text
SubagentRunner = Callable[[str, SubagentRouteDecision], Awaitable[str]]

_runner: SubagentRunner | None = None
_runner_lock = threading.Lock()


def set_subagent_runner(runner: SubagentRunner | None) -> None:
    """Install (or clear, with None) the process-wide subagent executor.

    The main thread wires the real runner here (e.g. one that drives an
    AgentLoop with a ModelPool endpoint); tests may install their own.
    """
    global _runner
    with _runner_lock:
        _runner = runner


def get_subagent_runner() -> SubagentRunner | None:
    with _runner_lock:
        return _runner


def is_tool_allowed(tool_name: str, forbidden_tools: list[str] | set[str]) -> bool:
    """Runtime guard ported from fork_subagent's ``before_tool_use`` hook:
    forbidden tools are rejected at call time with a clear reason."""
    return str(tool_name) not in {str(t) for t in forbidden_tools}


async def invoke_lite_subagent(
    task: str = "",
    model: str | None = None,
    forbidden_tools: list[str] | None = None,
    provider_id: str | None = None,
    primary_model: str | None = None,
    available_tools: list[Any] | None = None,
    max_iterations: int | None = None,
) -> str:
    """Tool handler (awaited with **call.arguments by ToolExecutor.execute).

    Routes the task, then executes it through the registered runner. The
    returned string is JSON so the calling agent can parse structured
    results; ``output`` carries the subagent's final text once executed.
    """
    if not isinstance(task, str) or not task.strip():
        raise ValueError("invoke_lite_subagent requires a non-empty 'task' argument")
    forbidden = [str(t) for t in (forbidden_tools or [])]
    # The tool's purpose is the cheap sidekick: with no explicit model we
    # request "lite" (OpenClacky's run_detached defaults to the primary
    # instead — documented divergence; the resolver falls back to the
    # primary with a clear reason when no pairing exists).
    requested = model if model is not None else "lite"
    ctx = SubagentContext(
        primary_model=primary_model,
        provider_id=provider_id,
        available_tools=list(available_tools or []),
        forbidden_tools=forbidden,
        requested_model=requested,
        max_iterations=max_iterations,
    )
    decision = route_subagent_request(task, ctx, registry=get_lite_registry())

    payload: dict[str, Any] = {
        "tool": INVOKE_LITE_SUBAGENT_TOOL,
        "route": decision.route,
        "task": decision.task,
        "model": decision.model,
        "model_source": decision.model_source,
        "forbidden_tools": decision.forbidden_tools,
        "budget": decision.budget,
        "reason": decision.reason,
    }

    runner = get_subagent_runner()
    if runner is None:
        # Truthful dry-run: full routing computed, nothing to execute against.
        payload["executed"] = False
        payload["detail"] = (
            "no subagent runner registered (set_subagent_runner); "
            "returning the computed routing decision"
        )
        logger.info("invoke_lite_subagent dry-run: %s", decision.reason)
        return json.dumps(payload, ensure_ascii=False)

    # Runtime forbidden-tools guard (port of the before_tool_use hook): the
    # runner receives only allowed tools and the guard decision is enforced
    # here for anything addressed by name.
    allowed_tools = filter_forbidden_tools(decision.tools, forbidden)
    guarded = SubagentRouteDecision(
        route=decision.route,
        task=decision.task,
        forked=decision.forked,
        model=decision.model,
        model_source=decision.model_source,
        category=decision.category,
        tools=allowed_tools,
        forbidden_tools=decision.forbidden_tools,
        budget=decision.budget,
        reason=decision.reason,
        instructions=decision.instructions,
    )
    output = await runner(decision.task, guarded)
    payload["executed"] = True
    payload["output"] = str(output)
    return json.dumps(payload, ensure_ascii=False)


def register_subagent_tools(harness: Any) -> list[str]:
    """Register ``invoke_lite_subagent`` on a harness' ToolExecutor.

    *harness* may be an ``AgentLoop`` (uses its ``tool_executor`` property)
    or a bare ``ToolExecutor``. Returns the registered tool names. This
    function is called by the main thread during harness setup — importing
    this module never registers anything by itself (opt-in only).
    """
    executor = getattr(harness, "tool_executor", harness)
    register = getattr(executor, "register", None)
    if not callable(register):
        raise TypeError(
            "register_subagent_tools: harness must expose a ToolExecutor "
            "(tool_executor property or register() method)"
        )
    register(INVOKE_LITE_SUBAGENT_TOOL, invoke_lite_subagent, _TOOL_DESCRIPTION)
    logger.info("subagent_routing: registered tool %s", INVOKE_LITE_SUBAGENT_TOOL)
    return [INVOKE_LITE_SUBAGENT_TOOL]
