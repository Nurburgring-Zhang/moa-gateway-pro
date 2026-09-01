"""moa_gateway.a2a — Agent-to-Agent (A2A) protocol layer.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT license):
agent card (src/app/.well-known/agent.json/route.ts), JSON-RPC 2.0 router
(src/app/a2a/route.ts), task manager (src/lib/a2a/taskManager.ts) and the five
A2A skills (src/lib/a2a/skills/*). See each submodule header for the exact
source mapping; THIRD_PARTY_NOTICES.md carries the MIT attribution.

Public surface:
  - build_agent_card()       — GET /.well-known/agent.json payload
  - handle_raw_body()        — POST /v1/a2a JSON-RPC dispatcher
  - SKILL_REGISTRY           — the five skills (real internal calls)
  - A2ATaskManager           — persisted task lifecycle
"""

from .agent_card import PROTOCOL_VERSION, build_agent_card
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    handle_raw_body,
)
from .skills import SKILL_REGISTRY, SkillExecutionError, SkillSpec, sanitize_outbound
from .task_manager import (
    A2ATask,
    A2ATaskManager,
    InvalidTransitionError,
    TaskTransitionError,
    get_task_manager,
    reset_task_manager,
)

__all__ = [
    "A2ATask",
    "A2ATaskManager",
    "INTERNAL_ERROR",
    "INVALID_PARAMS",
    "INVALID_REQUEST",
    "InvalidTransitionError",
    "METHOD_NOT_FOUND",
    "PARSE_ERROR",
    "PROTOCOL_VERSION",
    "SKILL_REGISTRY",
    "SkillExecutionError",
    "SkillSpec",
    "TaskTransitionError",
    "build_agent_card",
    "get_task_manager",
    "handle_raw_body",
    "reset_task_manager",
    "sanitize_outbound",
]
