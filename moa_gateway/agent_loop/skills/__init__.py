"""Built-in skills for the agent loop framework.

Each skill is an async function that can be registered with ToolExecutor.
"""
from __future__ import annotations

from .api_verify import api_verify
from .code_execute import code_execute
from .data_analysis import analyze_data
from .file_ops import file_list, file_read, file_write
from .web_search import web_search

__all__ = [
    "analyze_data",
    "code_execute",
    "file_list",
    "file_read",
    "file_write",
    "web_search",
    "api_verify",
]

# Tool registration helper
BUILTIN_TOOLS = {
    "web_search": (web_search, "Search the web for information"),
    "code_execute": (code_execute, "Execute Python code and return output"),
    "file_read": (file_read, "Read a file"),
    "file_write": (file_write, "Write content to a file"),
    "file_list": (file_list, "List files in a directory"),
    "analyze_data": (analyze_data, "Analyze data (summary/trend/anomaly)"),
    "api_verify": (api_verify, "Verify API endpoint response (status, fields, assertions)"),
}

# v3.2.1 hardening: single source of truth for admin/operator-only tools
# (RCE-capable primitives: code execution / filesystem / outbound probing).
# Enforced by routes/agent.py AND the orchestrator (planner filter +
# executor defense-in-depth) so no entry point re-exposes them to
# API-key/readonly users.
DANGEROUS_TOOLS = frozenset(
    {"code_execute", "file_read", "file_write", "file_list", "api_verify"}
)

# Pristine builtin names, frozen at import time — BEFORE any hot-deployed
# custom skill can be registered into the (mutable) BUILTIN_TOOLS dict.
# Name-collision guards must check this, not the live dict, or a skill
# registered earlier in the process would "collide with itself".
BUILTIN_TOOL_NAMES = frozenset(BUILTIN_TOOLS)


def register_all(harness) -> None:
    """Register all built-in skills onto an AgentHarness or ToolExecutor."""
    from ..base import ToolExecutor  # type: ignore

    executor = (
        harness._tool_executor
        if hasattr(harness, "_tool_executor")
        else harness
    )
    if not isinstance(executor, ToolExecutor):
        raise TypeError("Expected AgentHarness or ToolExecutor")

    for name, (handler, desc) in BUILTIN_TOOLS.items():
        executor.register(name, handler, desc)  # type: ignore[arg-type]
