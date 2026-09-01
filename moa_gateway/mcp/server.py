"""MCP Server - handles tool registration, discovery, and invocation."""

from __future__ import annotations

import json
import logging
from typing import Any

from .guardrails import GuardrailEngine
from .protocol import JSONRPCRequest, JSONRPCResponse, MCPMethod
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"

#: Roles allowed to see/call tools discovered on external MCP servers.
EXTERNAL_TOOL_ROLES = ("admin", "operator")


class MCPServer:
    """MCP Server implementation with RBAC and guardrails.

    When ``external_registry`` is provided, tools discovered from connected
    external MCP servers are merged into ``tools/list`` under the
    ``external__<server>__<tool>`` namespace and ``tools/call`` forwards
    invocations to the owning server's live client. Guardrails and RBAC
    apply to external tools exactly as they do to built-in tools.
    """

    def __init__(
        self,
        registry: ToolRegistry | None = None,
        guardrails: GuardrailEngine | None = None,
        external_registry: Any | None = None,
    ):
        self.registry = registry or ToolRegistry()
        self.guardrails = guardrails or GuardrailEngine()
        self.external_registry = external_registry
        self.server_info = {"name": "moa-gateway-mcp", "version": "2.0.0"}

    async def handle_request(
        self, request: JSONRPCRequest, user: dict | None = None
    ) -> JSONRPCResponse | None:
        """Process a JSON-RPC 2.0 MCP request."""
        method = request.method

        try:
            if method == MCPMethod.INITIALIZE:
                return self._handle_initialize(request)
            elif method == MCPMethod.INITIALIZED:
                return None  # Notification, no response
            elif method == MCPMethod.PING:
                return JSONRPCResponse(id=request.id, result={})
            elif method == MCPMethod.TOOLS_LIST:
                return self._handle_list_tools(request, user)
            elif method == MCPMethod.TOOLS_CALL:
                return await self._handle_call_tool(request, user)
            else:
                return JSONRPCResponse(
                    id=request.id,
                    error={"code": -32601, "message": f"Method not found: {method}"},
                )
        except Exception as e:
            logger.exception("MCP request error: method=%s", method)
            return JSONRPCResponse(
                id=request.id,
                error={"code": -32603, "message": "Internal error"},
            )

    def _handle_initialize(self, req: JSONRPCRequest) -> JSONRPCResponse:
        # Audit F13 fix: only advertise capabilities that are actually
        # implemented. resources/prompts handlers do not exist (they return
        # -32601), so declaring them would overstate the server's abilities.
        return JSONRPCResponse(
            id=req.id,
            result={
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": self.server_info,
                "capabilities": {
                    "tools": {"listChanged": True},
                },
            },
        )

    def _handle_list_tools(self, req: JSONRPCRequest, user: dict | None) -> JSONRPCResponse:
        role = user.get("role", "readonly") if user else None
        tools = self.registry.list_tools(user_role=role)
        tool_dicts = [t.model_dump() for t in tools]
        # Merge tools discovered from connected external MCP servers.
        # External tool execution is privileged -> admin/operator only.
        if self.external_registry is not None and role in EXTERNAL_TOOL_ROLES:
            tool_dicts.extend(self.external_tool_definitions())
        return JSONRPCResponse(
            id=req.id,
            result={"tools": tool_dicts},
        )

    def external_tool_definitions(self) -> list[dict]:
        """Namespaced definitions of all discovered external tools."""
        definitions: list[dict] = []
        for namespaced, meta in self.external_registry.get_all_discovered_tools().items():
            definition = dict(meta.get("definition") or {})
            definitions.append(
                {
                    "name": namespaced,
                    "description": (
                        f"[external:{meta.get('server', '?')}] "
                        f"{definition.get('description', '')}".strip()
                    ),
                    "inputSchema": definition.get("inputSchema", {}) or {},
                }
            )
        return definitions

    async def _handle_call_tool(self, req: JSONRPCRequest, user: dict | None) -> JSONRPCResponse:
        params = req.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

        # External namespace -> forward to the owning external MCP server.
        from .external_registry import EXTERNAL_TOOL_PREFIX

        if isinstance(tool_name, str) and tool_name.startswith(EXTERNAL_TOOL_PREFIX):
            return await self._handle_call_external_tool(req, user, tool_name, arguments)

        # RBAC check
        role = user.get("role", "readonly") if user else "readonly"
        if not self.registry.check_access(tool_name, role):
            logger.warning(
                "MCP RBAC denied: user=%s role=%s tool=%s",
                user.get("username", "?") if user else "?",
                role,
                tool_name,
            )
            return JSONRPCResponse(
                id=req.id,
                error={
                    "code": -32603,
                    "message": f"Permission denied: role '{role}' cannot call '{tool_name}'",
                },
            )

        # Pre-guardrail
        try:
            arguments = await self.guardrails.pre_call(tool_name, arguments, user)
        except ValueError as e:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32602, "message": str(e)},
            )

        # Execute handler
        handler = self.registry.get_handler(tool_name)
        if not handler:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32602, "message": f"Unknown tool: {tool_name}"},
            )

        try:
            result = await handler(arguments)
            # Post-guardrail
            result = await self.guardrails.post_call(tool_name, result, user)
            # Format result
            if isinstance(result, dict) and "content" in result:
                return JSONRPCResponse(id=req.id, result=result)
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, default=str),
                        }
                    ],
                    "isError": False,
                },
            )
        except Exception as e:
            logger.exception("Tool call failed: %s", tool_name)
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": "Tool execution failed due to an internal error.",
                        }
                    ],
                    "isError": True,
                },
            )

    async def _handle_call_external_tool(
        self,
        req: JSONRPCRequest,
        user: dict | None,
        tool_name: str,
        arguments: dict,
    ) -> JSONRPCResponse:
        """Forward a call to a tool discovered on an external MCP server.

        RBAC (admin/operator only) and guardrails apply exactly as they do
        for built-in tools; the real invocation goes through the external
        registry's live client (HTTP or stdio subprocess).
        """
        from .external_registry import parse_external_tool_name

        role = user.get("role", "readonly") if user else "readonly"
        if role not in EXTERNAL_TOOL_ROLES:
            logger.warning(
                "MCP external RBAC denied: user=%s role=%s tool=%s",
                user.get("username", "?") if user else "?",
                role,
                tool_name,
            )
            return JSONRPCResponse(
                id=req.id,
                error={
                    "code": -32603,
                    "message": f"Permission denied: role '{role}' cannot call '{tool_name}'",
                },
            )

        if self.external_registry is None:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32602, "message": f"Unknown tool: {tool_name}"},
            )

        parsed = parse_external_tool_name(tool_name, self.external_registry.server_names())
        if parsed is None:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32602, "message": f"Unknown tool: {tool_name}"},
            )
        server_name, remote_tool = parsed

        # Pre-guardrail (same engine, same blocked patterns as local tools)
        try:
            arguments = await self.guardrails.pre_call(tool_name, arguments, user)
        except ValueError as e:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32602, "message": str(e)},
            )

        try:
            result = await self.external_registry.call_tool(server_name, remote_tool, arguments)
        except KeyError:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32602, "message": f"Unknown tool: {tool_name}"},
            )
        except ConnectionError as e:
            return JSONRPCResponse(
                id=req.id,
                error={"code": -32603, "message": str(e)},
            )
        except Exception:
            # Never leak subprocess/transport internals to the caller.
            logger.exception("External tool call failed: %s", tool_name)
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": "External tool execution failed due to an internal error.",
                        }
                    ],
                    "isError": True,
                },
            )

        # The remote server answered with a JSON-RPC error -> surface it as a
        # tool-level failure (isError), not as a gateway internal error.
        if isinstance(result, dict) and result.get("error") is not None:
            err = result["error"]
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": [
                        {
                            "type": "text",
                            "text": f"External server '{server_name}' returned an error: "
                            f"[{err.get('code')}] {err.get('message')}",
                        }
                    ],
                    "isError": True,
                },
            )

        # Post-guardrail
        result = await self.guardrails.post_call(tool_name, result, user)
        if isinstance(result, dict) and "content" in result:
            return JSONRPCResponse(id=req.id, result=result)
        return JSONRPCResponse(
            id=req.id,
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, default=str),
                    }
                ],
                "isError": False,
            },
        )
