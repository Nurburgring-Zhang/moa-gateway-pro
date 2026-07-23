"""MCP Server - handles tool registration, discovery, and invocation."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from .guardrails import GuardrailEngine
from .protocol import JSONRPCRequest, JSONRPCResponse, MCPMethod
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"


class MCPServer:
    """MCP Server implementation with RBAC and guardrails."""

    def __init__(
        self,
        registry: Optional[ToolRegistry] = None,
        guardrails: Optional[GuardrailEngine] = None,
    ):
        self.registry = registry or ToolRegistry()
        self.guardrails = guardrails or GuardrailEngine()
        self.server_info = {"name": "moa-gateway-mcp", "version": "2.0.0"}

    async def handle_request(
        self, request: JSONRPCRequest, user: Optional[dict] = None
    ) -> Optional[JSONRPCResponse]:
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
                error={"code": -32603, "message": f"Internal error: {e}"},
            )

    def _handle_initialize(self, req: JSONRPCRequest) -> JSONRPCResponse:
        return JSONRPCResponse(
            id=req.id,
            result={
                "protocolVersion": PROTOCOL_VERSION,
                "serverInfo": self.server_info,
                "capabilities": {
                    "tools": {"listChanged": True},
                    "resources": {},
                    "prompts": {},
                },
            },
        )

    def _handle_list_tools(
        self, req: JSONRPCRequest, user: Optional[dict]
    ) -> JSONRPCResponse:
        role = user.get("role", "readonly") if user else None
        tools = self.registry.list_tools(user_role=role)
        return JSONRPCResponse(
            id=req.id,
            result={"tools": [t.model_dump() for t in tools]},
        )

    async def _handle_call_tool(
        self, req: JSONRPCRequest, user: Optional[dict]
    ) -> JSONRPCResponse:
        params = req.params or {}
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})

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
                        {"type": "text", "text": json.dumps(result, ensure_ascii=False, default=str)}
                    ],
                    "isError": False,
                },
            )
        except Exception as e:
            logger.exception("Tool call failed: %s", tool_name)
            return JSONRPCResponse(
                id=req.id,
                result={
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            )
