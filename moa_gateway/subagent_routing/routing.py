"""Subagent routing decision engine (pure functional core).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Sources:
- ``lib/clacky/agent.rb`` — ``fork_subagent`` (model selection incl. the
  virtual ``lite`` role, forbidden-tools notice text) and ``run_detached``;
- ``lib/clacky/providers.rb`` — lite-model resolution.

The gateway twist: OpenClacky forks subagents imperatively from inside an
agent run; here the same decision logic is exposed as a PURE function,
:func:`route_subagent_request`, so callers (agent harness, HTTP dry-run
endpoint, tests) can inspect the decision before anything executes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .registry import LiteModelRegistry, get_lite_registry

__all__ = [
    "DEFAULT_FORK_PREFIXES",
    "DEFAULT_SUBAGENT_MAX_ITERATIONS",
    "DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS",
    "SUBAGENT_SYSTEM_NOTICE",
    "SubagentContext",
    "SubagentRouteDecision",
    "detect_fork_prefix",
    "classify_task_category",
    "tool_name_of",
    "filter_forbidden_tools",
    "forbidden_notice",
    "build_subagent_instructions",
    "route_subagent_request",
]

# Prefixes that turn a task string into an explicit subagent request.
DEFAULT_FORK_PREFIXES: tuple[str, ...] = ("/fork", "fork:")

# Budget defaults for a forked lite subagent (bounded work, like OpenClacky's
# detached runs; the parent stays responsive because the fork is isolated).
DEFAULT_SUBAGENT_MAX_ITERATIONS = 30
DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS = 8192

# Port of the FORKED SUBAGENT MODE notice appended by fork_subagent. Kept in
# English: it is the exact instruction text the upstream project injects.
SUBAGENT_SYSTEM_NOTICE = (
    "CRITICAL: TASK CONTEXT SWITCH - FORKED SUBAGENT MODE\n\n"
    "You are now running as a forked subagent — a temporary, isolated agent "
    "spawned by the parent agent to handle a specific task. You run "
    "independently and cannot communicate back to the parent mid-task. When "
    "you finish (i.e., you stop calling tools and return a final response), "
    "your output will be automatically summarized and returned to the parent "
    "agent as a result so it can continue."
)

# Keyword sets for the task classifier (deliberately small and explainable —
# routing hints only, never a security boundary).
_READ_WORDS = frozenset(
    {
        "read", "show", "list", "find", "search", "grep", "locate", "look",
        "check", "inspect", "summarize", "explain", "describe", "count",
        "查看", "读取", "搜索", "查找", "列出", "总结", "解释",
    }
)
_WRITE_WORDS = frozenset(
    {
        "write", "create", "add", "edit", "modify", "update", "fix",
        "implement", "build", "refactor", "delete", "remove", "move",
        "rename", "generate", "apply", "patch",
        "编写", "创建", "修改", "修复", "实现", "删除", "生成",
    }
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.\-]*|[\u4e00-\u9fff]+")


@dataclass(frozen=True)
class SubagentContext:
    """Everything the router needs to know about the parent run."""

    primary_model: str | None = None
    provider_id: str | None = None
    available_tools: list[Any] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    max_iterations: int | None = None
    max_output_tokens: int | None = None
    # Explicit model override ("lite" keyword or a concrete model name).
    requested_model: str | None = None
    category: str | None = None


@dataclass(frozen=True)
class SubagentRouteDecision:
    """Outcome of :func:`route_subagent_request`."""

    route: str  # "fork" | "inline"
    task: str  # cleaned task text (prefix stripped)
    forked: bool
    model: str | None
    model_source: str  # "override" | "lite_mapping" | "primary" | "none"
    category: str
    tools: list[Any]
    forbidden_tools: list[str]
    budget: dict[str, int]
    reason: str
    instructions: str | None = None  # fork-mode notice (cache-reuse message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "task": self.task,
            "forked": self.forked,
            "model": self.model,
            "model_source": self.model_source,
            "category": self.category,
            "tools": self.tools,
            "forbidden_tools": list(self.forbidden_tools),
            "budget": dict(self.budget),
            "reason": self.reason,
            "instructions": self.instructions,
        }


def detect_fork_prefix(
    task: str, prefixes: tuple[str, ...] | list[str] | None = None
) -> tuple[bool, str]:
    """Detect an explicit fork prefix and return ``(is_fork, cleaned_task)``.

    ``"/fork analyze the logs"`` -> ``(True, "analyze the logs")``;
    ``"fork:analyze the logs"``  -> ``(True, "analyze the logs")``;
    ``"fix the fork bug"``       -> ``(False, "fix the fork bug")`` (the
    prefix must LEAD the task; occurrences later in the text never fork).
    """
    text = (task or "").strip()
    for prefix in prefixes or DEFAULT_FORK_PREFIXES:
        if not prefix:
            continue
        if text == prefix:
            return True, ""
        if text.startswith(prefix):
            rest = text[len(prefix):]
            # "/fork xyz" needs whitespace after the prefix; "fork:" carries
            # its own separator. Guard stops "/forkable" matching "/fork".
            if prefix.endswith(":") or rest[:1].isspace():
                return True, rest.strip()
    return False, text


def classify_task_category(task: str) -> str:
    """Cheap keyword classifier: ``read_only`` / ``write`` / ``general``.

    Port intent: OpenClacky picks lite companions per provider, not per task;
    the gateway additionally records a category hint so callers can bias
    budgets (read-only forks are cheaper to retry).
    """
    words = [w.lower() for w in _WORD_RE.findall(task or "")]
    read_hits = sum(1 for w in words if w in _READ_WORDS)
    write_hits = sum(1 for w in words if w in _WRITE_WORDS)
    if write_hits > read_hits:
        return "write"
    if read_hits > 0:
        return "read_only"
    return "general"


def tool_name_of(tool: Any) -> str | None:
    """Best-effort name extraction from a tool definition or plain name.

    Accepts OpenAI-style ``{"function": {"name": ...}}``, flat
    ``{"name": ...}`` dicts, and bare strings.
    """
    if isinstance(tool, str):
        return tool or None
    if isinstance(tool, dict):
        func = tool.get("function")
        if isinstance(func, dict) and func.get("name"):
            return str(func["name"])
        if tool.get("name"):
            return str(tool["name"])
    return None


def filter_forbidden_tools(
    tools: list[Any], forbidden_tools: list[str] | set[str]
) -> list[Any]:
    """Drop forbidden tools from a tool list (names or definitions).

    OpenClacky keeps the registry intact for cache reuse and blocks at
    runtime via hooks; callers that want that behavior should pass the
    unfiltered list and use :func:`forbidden_notice` + a runtime guard. This
    helper is for callers that prefer to never expose the tools at all.
    """
    if not forbidden_tools:
        return list(tools)
    banned = {str(t) for t in forbidden_tools}
    out: list[Any] = []
    for tool in tools:
        name = tool_name_of(tool)
        if name is not None and name in banned:
            continue
        out.append(tool)
    return out


def forbidden_notice(forbidden_tools: list[str]) -> str:
    """Port of the [System Notice] text fork_subagent appends when tools are
    forbidden. Returns ``""`` for an empty list."""
    if not forbidden_tools:
        return ""
    tool_list = ", ".join(f"`{t}`" for t in forbidden_tools)
    return (
        "\n\n[System Notice] The following tools are disabled in this "
        f"subagent and will be rejected if called: {tool_list}"
    )


def build_subagent_instructions(
    system_prompt_suffix: str | None = None,
    forbidden_tools: list[str] | None = None,
) -> str:
    """Port of the system_injected fork notice (role/constraints + optional
    suffix + forbidden-tools notice)."""
    return (
        f"{SUBAGENT_SYSTEM_NOTICE}\n\n{system_prompt_suffix or ''}"
        f"{forbidden_notice(forbidden_tools or [])}"
    )


def _resolve_model(
    ctx: SubagentContext, registry: LiteModelRegistry | None
) -> tuple[str | None, str, str]:
    """Returns ``(model, source, reason_fragment)``.

    Port of fork_subagent's model branch:
    - explicit concrete model -> used as-is ("override");
    - "lite" keyword          -> registry pairing for the current primary,
      falling back to the primary itself when no lite exists;
    - nothing requested       -> primary stays ("primary").
    """
    reg = registry or get_lite_registry()
    requested = (ctx.requested_model or "").strip()
    if requested and requested.lower() != "lite":
        return requested, "override", f"explicit model '{requested}' requested"
    if requested.lower() == "lite":
        lite = reg.resolve(ctx.provider_id or "", ctx.primary_model)
        if lite:
            return lite, "lite_mapping", (
                f"lite keyword resolved via pairing table "
                f"({ctx.provider_id or '?'}/{ctx.primary_model or '?'} -> {lite})"
            )
        return ctx.primary_model, "primary", (
            "lite requested but no lite pairing exists for the current "
            "primary — staying on the primary model"
        )
    return ctx.primary_model, "primary", "no model requested — keeping primary"


def route_subagent_request(
    task: str,
    ctx: SubagentContext | None = None,
    prefixes: tuple[str, ...] | list[str] | None = None,
    registry: LiteModelRegistry | None = None,
) -> SubagentRouteDecision:
    """Decide how a task string should run. Pure function, no side effects.

    - No fork prefix -> ``route="inline"``: the task stays on the primary
      thread with the full tool list (default behavior unchanged; forking is
      strictly opt-in via prefix or ``requested_model``).
    - Fork prefix -> ``route="fork"``: resolve the lite model, remove
      forbidden tools, size the budget, and build the fork notice.
    """
    context = ctx or SubagentContext()
    is_fork, cleaned = detect_fork_prefix(task, prefixes)
    category = context.category or classify_task_category(cleaned)
    forbidden = [str(t) for t in (context.forbidden_tools or [])]
    budget = {
        "max_iterations": int(
            context.max_iterations
            if context.max_iterations is not None
            else DEFAULT_SUBAGENT_MAX_ITERATIONS
        ),
        "max_output_tokens": int(
            context.max_output_tokens
            if context.max_output_tokens is not None
            else DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS
        ),
    }

    if not is_fork and not (context.requested_model or "").strip():
        return SubagentRouteDecision(
            route="inline",
            task=cleaned,
            forked=False,
            model=context.primary_model,
            model_source="primary",
            category=category,
            tools=list(context.available_tools),
            forbidden_tools=forbidden,
            budget=budget,
            reason="no fork prefix detected — run on the primary thread",
        )

    model, source, model_reason = _resolve_model(context, registry)
    tools = filter_forbidden_tools(context.available_tools, forbidden)
    instructions = build_subagent_instructions(
        system_prompt_suffix=f"Task: {cleaned}" if cleaned else None,
        forbidden_tools=forbidden,
    )
    reason = (
        f"fork prefix detected; {model_reason}; "
        f"{len(forbidden)} forbidden tool(s) filtered; "
        f"{len(tools)} tool(s) available to the subagent"
    )
    return SubagentRouteDecision(
        route="fork",
        task=cleaned,
        forked=True,
        model=model,
        model_source=source,
        category=category,
        tools=tools,
        forbidden_tools=forbidden,
        budget=budget,
        reason=reason,
        instructions=instructions,
    )
