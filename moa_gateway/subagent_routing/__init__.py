"""Subagent routing toolkit (M9).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License)
— ``fork_subagent`` / ``run_detached`` / ``generate_subagent_summary``
reworked into a pure decision API for the gateway:

- ``registry`` — lite-model pairing tables (OpenClacky presets, verbatim)
                 + runtime registration API;
- ``routing``  — fork-prefix detection, task category, forbidden-tools
                 filtering, ``route_subagent_request`` decision function;
- ``summary``  — ``[SUBAGENT SUMMARY]`` fold into the parent history,
                 bounded transcripts, cost ledger merge;
- ``tools``    — ``invoke_lite_subagent`` tool registration for the agent
                 harness.

Everything is opt-in: no fork happens without an explicit ``/fork`` /
``fork:`` prefix (or an explicit ``requested_model``), and nothing is
registered into any harness at import time.
"""

from .registry import (
    DEFAULT_GLOBAL_LITE,
    DEFAULT_LITE_TABLES,
    LiteModelRegistry,
    get_lite_registry,
)
from .routing import (
    DEFAULT_FORK_PREFIXES,
    DEFAULT_SUBAGENT_MAX_ITERATIONS,
    DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS,
    SUBAGENT_SYSTEM_NOTICE,
    SubagentContext,
    SubagentRouteDecision,
    build_subagent_instructions,
    classify_task_category,
    detect_fork_prefix,
    filter_forbidden_tools,
    forbidden_notice,
    route_subagent_request,
    tool_name_of,
)
from .summary import (
    MAX_TRANSCRIPT_BYTES,
    MAX_TRANSCRIPT_EVENTS,
    SUBAGENT_SUMMARY_HEADER,
    CostLedger,
    extract_subagent_transcript,
    fold_subagent_result,
    generate_subagent_summary,
)
from .tools import (
    INVOKE_LITE_SUBAGENT_TOOL,
    SubagentRunner,
    get_subagent_runner,
    invoke_lite_subagent,
    is_tool_allowed,
    register_subagent_tools,
    set_subagent_runner,
)

__all__ = [
    # registry
    "DEFAULT_GLOBAL_LITE",
    "DEFAULT_LITE_TABLES",
    "LiteModelRegistry",
    "get_lite_registry",
    # routing
    "DEFAULT_FORK_PREFIXES",
    "DEFAULT_SUBAGENT_MAX_ITERATIONS",
    "DEFAULT_SUBAGENT_MAX_OUTPUT_TOKENS",
    "SUBAGENT_SYSTEM_NOTICE",
    "SubagentContext",
    "SubagentRouteDecision",
    "build_subagent_instructions",
    "classify_task_category",
    "detect_fork_prefix",
    "filter_forbidden_tools",
    "forbidden_notice",
    "route_subagent_request",
    "tool_name_of",
    # summary
    "MAX_TRANSCRIPT_BYTES",
    "MAX_TRANSCRIPT_EVENTS",
    "SUBAGENT_SUMMARY_HEADER",
    "CostLedger",
    "extract_subagent_transcript",
    "fold_subagent_result",
    "generate_subagent_summary",
    # tools
    "INVOKE_LITE_SUBAGENT_TOOL",
    "SubagentRunner",
    "get_subagent_runner",
    "invoke_lite_subagent",
    "is_tool_allowed",
    "register_subagent_tools",
    "set_subagent_runner",
]
