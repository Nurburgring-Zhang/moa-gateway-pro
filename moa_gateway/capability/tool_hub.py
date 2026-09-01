"""Unified Tool Hub — single aggregation point for all tool registries.

Audit fix (structural gap): before this module existed, three disconnected
tool registries coexisted and the MoA engine could not execute any tool:

1. ``agent_loop.skills.BUILTIN_TOOLS`` — 7 local skills (real handlers)
2. ``mcp.builtin_tools`` — 9 MCP built-in tools (real handlers)
3. ``mcp.external_registry`` — tools discovered on external MCP servers

:class:`ToolHub` aggregates all three sources behind ONE namespace scheme,
ONE metadata format and ONE guarded execution entry point:

- ``local__<tool>``             — agent_loop skills (real handlers)
- ``mcp__<tool>``               — MCP built-in tools (real handlers)
- ``external__<server>__<tool>``— tools discovered from external MCP servers,
  invoked through the real ``ExternalMCPRegistry`` JSON-RPC client

Namespacing guarantees same-named tools from different sources never
overwrite each other and are never lost (e.g. ``local__web_search`` and
``mcp__search_web`` coexist; a hypothetical ``mcp__foo`` / ``local__foo``
pair stays two distinct entries).

Execution pipeline (``execute``):
1. role gate  — RBAC-style ``allowed_roles`` per tool (mirrors the MCP
   registry role model and the agent-loop dangerous-tool gating)
2. guardrails — dangerous-pattern scan reusing ``mcp.guardrails``
3. real execution against the source handler
4. structured :class:`ToolResult` (output/data/usage/latency)

The hub is a pure in-memory registry (no storage dependency). External
tools are re-synced from the live ``ExternalMCPRegistry`` on every listing
and execution, so newly discovered servers become callable immediately.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Namespaces
# ---------------------------------------------------------------------------

LOCAL_PREFIX = "local__"
MCP_PREFIX = "mcp__"
EXTERNAL_PREFIX = "external__"

NAMESPACE_PREFIXES = (LOCAL_PREFIX, MCP_PREFIX, EXTERNAL_PREFIX)

# ---------------------------------------------------------------------------
# Roles (mirrors rbac.Role values)
# ---------------------------------------------------------------------------

ALL_ROLES = frozenset({"admin", "operator", "user", "readonly"})
PRIVILEGED_ROLES = frozenset({"admin", "operator"})
REGULAR_ROLES = frozenset({"admin", "operator", "user"})

# Local skills that provide RCE / filesystem / outbound-probe primitives —
# admin/operator only (AGENTS.md rule 8, same set as routes/agent.py).
DANGEROUS_LOCAL_TOOLS = frozenset(
    {"code_execute", "file_read", "file_write", "file_list", "api_verify"}
)

# External MCP servers are third-party; their tools are privileged-only by
# default because their capabilities are unknown.
EXTERNAL_DEFAULT_ROLES = frozenset({"admin", "operator"})

# Parameter schemas for the local skills (they are plain functions without
# an attached JSON schema; kept here so the hub exposes unified metadata).
LOCAL_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "web_search": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "max_results": {"type": "integer", "description": "Max results.", "default": 5},
        },
        "required": ["query"],
    },
    "code_execute": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Python source code to run."},
            "language": {"type": "string", "description": "Language (python only).", "default": "python"},
            "timeout": {"type": "number", "description": "Wall-clock timeout seconds.", "default": 30},
        },
        "required": ["code"],
    },
    "file_read": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "File path to read."}},
        "required": ["path"],
    },
    "file_write": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path to write."},
            "content": {"type": "string", "description": "Content to write."},
        },
        "required": ["path", "content"],
    },
    "file_list": {
        "type": "object",
        "properties": {"directory": {"type": "string", "description": "Directory to list.", "default": "."}},
    },
    "analyze_data": {
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "Raw data (CSV/JSON text)."},
            "analysis_type": {"type": "string", "description": "summary/trend/anomaly.", "default": "summary"},
        },
        "required": ["data"],
    },
    "api_verify": {
        "type": "object",
        "properties": {
            "endpoint_id": {"type": "string", "description": "Model endpoint id to verify."},
            "test_prompt": {"type": "string", "description": "Prompt to send.", "default": "Hello"},
            "expect_json": {"type": "boolean", "description": "Expect JSON response.", "default": True},
            "url": {"type": "string", "description": "Explicit URL to verify."},
            "method": {"type": "string", "description": "HTTP method.", "default": "POST"},
            "expected_status": {"type": "integer", "description": "Expected HTTP status.", "default": 200},
        },
    },
}


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HubToolSpec:
    """Unified tool metadata across all three sources."""

    name: str  # namespaced hub name, e.g. "local__code_execute"
    raw_name: str  # original tool name in its source registry
    description: str
    parameters: dict[str, Any]  # JSON schema for arguments
    allowed_roles: frozenset[str]
    source: str  # "local" | "mcp" | "external"
    server: str = ""  # external source only: MCP server name

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "raw_name": self.raw_name,
            "description": self.description,
            "parameters": self.parameters,
            "allowed_roles": sorted(self.allowed_roles),
            "source": self.source,
            "server": self.server,
        }

    def to_openai_schema(self) -> dict[str, Any]:
        """OpenAI function-calling schema (for prompt injection)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {"type": "object", "properties": {}},
            },
        }


@dataclass
class ToolResult:
    """Structured result of a hub tool execution."""

    name: str
    success: bool
    output: str = ""  # text form fed back into LLM prompts
    data: Any = None  # raw structured payload returned by the handler
    error: str = ""
    latency_ms: float = 0.0
    usage: dict[str, Any] = field(default_factory=dict)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
            "usage": self.usage,
            "source": self.source,
        }


# ---------------------------------------------------------------------------
# ToolHub
# ---------------------------------------------------------------------------


class ToolHub:
    """Aggregates local skills + MCP built-ins + external MCP tools.

    Pure in-memory registry; external tools are dynamically re-synced from
    the live :class:`ExternalMCPRegistry` on each listing/execution.
    """

    def __init__(self, external_registry: Any | None = None, guardrails: Any | None = None):
        from ..mcp.external_registry import get_external_mcp_registry
        from ..mcp.guardrails import GuardrailEngine

        self._external_registry = (
            external_registry if external_registry is not None else get_external_mcp_registry()
        )
        # Reuse the shared guardrail engine (dangerous-pattern pre-call scan).
        self._guardrails = guardrails or GuardrailEngine()
        self._lock = threading.RLock()
        # MCP built-in registry: built from the SAME registration function the
        # MCP server uses, so handlers/roles are the real ones (single source
        # of truth = mcp.builtin_tools.register_builtin_tools).
        self._mcp_registry = self._build_mcp_registry()

    @staticmethod
    def _build_mcp_registry():
        from ..mcp.builtin_tools import register_builtin_tools
        from ..mcp.registry import ToolRegistry

        registry = ToolRegistry()
        register_builtin_tools(registry)
        return registry

    # ------------------------- source aggregation -------------------------

    def _local_entries(self) -> dict[str, tuple[HubToolSpec, Callable]]:
        """agent_loop skills -> namespaced entries with real handlers."""
        from ..agent_loop.skills import BUILTIN_TOOLS

        entries: dict[str, tuple[HubToolSpec, Callable]] = {}
        for raw_name, (handler, description) in BUILTIN_TOOLS.items():
            roles = (
                frozenset(PRIVILEGED_ROLES)
                if raw_name in DANGEROUS_LOCAL_TOOLS
                else frozenset(REGULAR_ROLES)
            )
            spec = HubToolSpec(
                name=f"{LOCAL_PREFIX}{raw_name}",
                raw_name=raw_name,
                description=description,
                parameters=LOCAL_TOOL_SCHEMAS.get(raw_name, {"type": "object", "properties": {}}),
                allowed_roles=roles,
                source="local",
            )
            entries[spec.name] = (spec, handler)
        return entries

    def _mcp_entries(self) -> dict[str, tuple[HubToolSpec, Callable]]:
        """MCP built-in tools -> namespaced entries with real handlers."""
        entries: dict[str, tuple[HubToolSpec, Callable]] = {}
        for tool in self._mcp_registry.list_tools():
            # Derive allowed roles through the registry's public access check.
            roles = frozenset(
                role for role in ALL_ROLES if self._mcp_registry.check_access(tool.name, role)
            )
            if not roles:
                roles = frozenset(REGULAR_ROLES)
            handler = self._mcp_registry.get_handler(tool.name)
            if handler is None:
                continue
            spec = HubToolSpec(
                name=f"{MCP_PREFIX}{tool.name}",
                raw_name=tool.name,
                description=tool.description,
                parameters=tool.inputSchema or {"type": "object", "properties": {}},
                allowed_roles=roles,
                source="mcp",
            )
            entries[spec.name] = (spec, handler)
        return entries

    def _external_entries(self) -> dict[str, tuple[HubToolSpec, None]]:
        """Discovered external MCP tools, re-synced live from the registry.

        The registry stores tools under namespaced keys
        ``external__<server>__<tool>`` (see mcp.external_registry), which is
        exactly the hub namespace — keys are used as-is, never re-prefixed.
        The handler is ``None``: execution routes through
        ``ExternalMCPRegistry.call_tool`` (real JSON-RPC over the retained
        client connection).
        """
        from ..mcp.external_registry import parse_external_tool_name

        entries: dict[str, tuple[HubToolSpec, None]] = {}
        try:
            discovered = self._external_registry.get_all_discovered_tools()
        except Exception as e:  # noqa: BLE001 - hub must survive registry bugs
            logger.warning("ToolHub: external tool sync failed: %s", e)
            return entries
        for namespaced_name, meta in discovered.items():
            definition = meta.get("definition") or {}
            server = str(meta.get("server", ""))
            raw_name = str(meta.get("tool", ""))
            if not server or not raw_name:
                parsed = parse_external_tool_name(
                    namespaced_name, list(self._external_registry.server_names())
                )
                if parsed is None:
                    continue
                server, raw_name = parsed
            if not namespaced_name.startswith(EXTERNAL_PREFIX):
                namespaced_name = f"{EXTERNAL_PREFIX}{server}__{raw_name}"
            spec = HubToolSpec(
                name=namespaced_name,
                raw_name=raw_name,
                description=str(definition.get("description", ""))
                or f"External tool '{raw_name}' from MCP server '{server}'",
                parameters=definition.get("inputSchema") or {"type": "object", "properties": {}},
                allowed_roles=frozenset(EXTERNAL_DEFAULT_ROLES),
                source="external",
                server=server,
            )
            entries[spec.name] = (spec, None)
        return entries

    def _all_entries(self) -> dict[str, tuple[HubToolSpec, Any]]:
        """Aggregate all three sources. Namespaces make collisions impossible,
        and every entry is kept — nothing is overwritten, nothing is lost."""
        with self._lock:
            entries: dict[str, tuple[HubToolSpec, Any]] = {}
            entries.update(self._local_entries())
            entries.update(self._mcp_entries())
            entries.update(self._external_entries())
            return entries

    # ------------------------------ queries -------------------------------

    def list_tools(self, caller_role: str | None = None) -> list[HubToolSpec]:
        """List tool specs, optionally filtered by caller role."""
        specs = [spec for spec, _handler in self._all_entries().values()]
        if caller_role:
            specs = [s for s in specs if caller_role in s.allowed_roles]
        return sorted(specs, key=lambda s: s.name)

    def get_spec(self, tool_name: str) -> HubToolSpec | None:
        entry = self._all_entries().get(tool_name)
        return entry[0] if entry else None

    def has_tool(self, tool_name: str) -> bool:
        return tool_name in self._all_entries()

    def tool_count(self, caller_role: str | None = None) -> int:
        return len(self.list_tools(caller_role))

    # ----------------------------- execution ------------------------------

    async def execute(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        caller_role: str = "readonly",
    ) -> ToolResult:
        """Unified guarded execution entry point.

        Pipeline: unknown-check -> role gate -> guardrail scan -> real
        execution -> structured ToolResult. Every failure mode returns a
        ToolResult with ``success=False`` and a precise error (never raises
        for expected failures), so LLM loops can observe and react.
        """
        args = dict(arguments or {})
        started = time.perf_counter()

        entry = self._all_entries().get(tool_name)
        if entry is None:
            return ToolResult(
                name=tool_name,
                success=False,
                error=f"Unknown tool: {tool_name}",
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        spec, handler = entry

        # 1) Role gate (RBAC pattern)
        if caller_role not in spec.allowed_roles:
            logger.warning(
                "ToolHub: role denied: tool=%s caller_role=%s allowed=%s",
                tool_name,
                caller_role,
                sorted(spec.allowed_roles),
            )
            return ToolResult(
                name=tool_name,
                success=False,
                error=(
                    f"permission denied: tool '{tool_name}' requires one of "
                    f"{sorted(spec.allowed_roles)}, caller role='{caller_role}'"
                ),
                latency_ms=(time.perf_counter() - started) * 1000,
                source=spec.source,
            )

        # 2) Guardrails: dangerous-pattern scan on arguments (may rewrite args
        #    via registered pre-hooks).
        try:
            args = await self._guardrails.pre_call(tool_name, args, {"role": caller_role})
        except ValueError as e:
            return ToolResult(
                name=tool_name,
                success=False,
                error=f"blocked by guardrails: {e}",
                latency_ms=(time.perf_counter() - started) * 1000,
                source=spec.source,
            )

        # 3) Real execution
        try:
            if spec.source == "local":
                raw = await handler(**args)
            elif spec.source == "mcp":
                raw = await handler(args)
            else:  # external — real JSON-RPC call through the retained client
                raw = await self._external_registry.call_tool(spec.server, spec.raw_name, args)
        except Exception as e:  # noqa: BLE001 - surfaced as structured failure
            logger.exception("ToolHub: tool %s execution failed", tool_name)
            return ToolResult(
                name=tool_name,
                success=False,
                error=f"{type(e).__name__}: {e}",
                latency_ms=(time.perf_counter() - started) * 1000,
                source=spec.source,
            )

        latency = (time.perf_counter() - started) * 1000
        output, data, usage = self._normalize_output(raw)
        return ToolResult(
            name=tool_name,
            success=True,
            output=output,
            data=data,
            latency_ms=latency,
            usage=usage,
            source=spec.source,
        )

    @staticmethod
    def _normalize_output(raw: Any) -> tuple[str, Any, dict[str, Any]]:
        """Normalize a handler return value into (output_text, data, usage)."""
        if isinstance(raw, dict):
            usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
            try:
                output = json.dumps(raw, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                output = str(raw)
            return output, raw, usage or {}
        if isinstance(raw, str):
            return raw, raw, {}
        return str(raw), raw, {}

    # ------------------------- executor integration ------------------------

    def build_handlers(
        self,
        caller_role: str,
        only: list[str] | None = None,
    ) -> dict[str, tuple[Callable[..., Awaitable[str]], str]]:
        """Build ``{namespaced_name: (async_handler, description)}`` pairs.

        The returned handlers adapt ``execute`` to the keyword-argument
        signature expected by ``agent_loop.base.ToolExecutor``:
        ``async handler(**arguments) -> str`` (raises on failure so loops
        record a failed ToolResult with the hub's error message).
        """
        handlers: dict[str, tuple[Callable[..., Awaitable[str]], str]] = {}
        for spec in self.list_tools(caller_role):
            if only is not None and spec.name not in only:
                continue

            def _make_handler(name: str, role: str):
                async def _handler(**arguments: Any) -> str:
                    result = await self.execute(name, arguments, role)
                    if not result.success:
                        raise RuntimeError(result.error or f"tool '{name}' execution failed")
                    return result.output

                return _handler

            handlers[spec.name] = (_make_handler(spec.name, caller_role), spec.description)
        return handlers

    def register_to_executor(
        self,
        executor: Any,
        caller_role: str,
        only: list[str] | None = None,
    ) -> list[str]:
        """Register role-filtered hub tools onto a ToolExecutor-like object.

        Returns the list of registered namespaced tool names.
        """
        registered: list[str] = []
        for name, (handler, description) in self.build_handlers(caller_role, only).items():
            executor.register(name, handler, description)
            registered.append(name)
        return registered


# ---------------------------------------------------------------------------
# Singleton (storage-independent: pure in-memory registry)
# ---------------------------------------------------------------------------

_hub: ToolHub | None = None
_hub_lock = threading.Lock()


def get_tool_hub() -> ToolHub:
    """Get the process-wide ToolHub singleton."""
    global _hub
    if _hub is None:
        with _hub_lock:
            if _hub is None:
                _hub = ToolHub()
    return _hub
