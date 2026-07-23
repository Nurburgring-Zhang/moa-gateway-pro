"""MCP (Model Context Protocol) endpoints.

Provides:
- POST /v1/mcp         JSON-RPC 2.0 entry point
- GET  /v1/mcp/sse     SSE transport (keepalive stream)
- GET  /v1/mcp/tools   REST convenience: list tools
- POST /v1/mcp/tools/{name}/call  REST convenience: call a tool
- GET  /v1/mcp/servers            List connected external MCP servers
- POST /v1/mcp/servers            Register an external MCP server
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from ..auth import require_api_key
from ..mcp import GuardrailEngine, JSONRPCRequest, MCPServer, ToolRegistry
from ..mcp.builtin_tools import register_builtin_tools
from ..mcp.external_registry import ExternalMCPServer, get_external_mcp_registry
from ..mcp.transport import HTTPTransport, SSETransport

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

# --- Singleton MCP Server instance ---
_registry = ToolRegistry()
_guardrails = GuardrailEngine()
_mcp_server = MCPServer(registry=_registry, guardrails=_guardrails)
_sse_transport = SSETransport(_mcp_server)
_http_transport = HTTPTransport(_mcp_server)

# Register built-in tools on module load
register_builtin_tools(_registry)

# Track connected external MCP servers
_external_servers: list[dict[str, Any]] = []


def get_mcp_server() -> MCPServer:
    """Get the singleton MCP server (for testing/DI)."""
    return _mcp_server


# ==================== JSON-RPC Endpoints ====================


@router.post("/v1/mcp")
async def mcp_jsonrpc(
    req: dict[str, Any],
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """MCP over HTTP - JSON-RPC 2.0 entry point.

    Supports all MCP methods: initialize, tools/list, tools/call, ping.
    Requires API key authentication; RBAC enforced on tool calls.
    """
    request = JSONRPCRequest(**req)
    response = await _mcp_server.handle_request(request, user=key_info)
    if response is None:
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": "ok"}
    return response.model_dump()


@router.get("/v1/mcp/sse")
async def mcp_sse(key_info: dict[str, Any] = Depends(require_api_key)):
    """MCP over SSE - Server-Sent Events keepalive stream.

    Client connects via GET, receives session ID, then POSTs requests
    to /v1/mcp with session context.
    """
    session_id = _sse_transport.create_session()

    async def event_stream():
        try:
            async for event in _sse_transport.event_stream(session_id):
                yield event
        finally:
            _sse_transport.remove_session(session_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ==================== REST Convenience Endpoints ====================


@router.get("/v1/mcp/tools")
async def list_mcp_tools(key_info: dict[str, Any] = Depends(require_api_key)):
    """List available MCP tools filtered by user role."""
    role = key_info.get("role") if key_info else None
    tools = _registry.list_tools(user_role=role)
    return {
        "tools": [t.model_dump() for t in tools],
        "total": len(tools),
    }


@router.post("/v1/mcp/tools/{tool_name}/call")
async def call_mcp_tool(
    tool_name: str,
    body: dict[str, Any] = None,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """REST convenience endpoint to call a specific tool.

    Body: {"arguments": {...}}
    """
    body = body or {}
    arguments = body.get("arguments", {})

    # Build JSON-RPC request internally
    request = JSONRPCRequest(
        method="tools/call",
        params={"name": tool_name, "arguments": arguments},
    )
    response = await _mcp_server.handle_request(request, user=key_info)
    if response is None:
        raise HTTPException(status_code=500, detail="No response from MCP server")
    if response.error:
        status_code = 403 if "Permission denied" in response.error.get("message", "") else 400
        raise HTTPException(status_code=status_code, detail=response.error["message"])
    return response.result


# ==================== External MCP Server Management ====================


@router.get("/v1/mcp/servers")
async def list_external_servers(key_info: dict[str, Any] = Depends(require_api_key)):
    """List registered external MCP servers."""
    return {"servers": _external_servers, "total": len(_external_servers)}


@router.post("/v1/mcp/servers")
async def register_external_server(
    body: dict[str, Any],
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Register an external MCP server for tool discovery.

    Body: {"url": "http://...", "api_key": "...", "name": "..."}
    """
    # Only admin/operator can register servers
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Only admin/operator can register MCP servers")

    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    server_entry = {
        "url": url,
        "name": body.get("name", url),
        "api_key": body.get("api_key"),
        "status": "registered",
        "tools_discovered": 0,
    }
    _external_servers.append(server_entry)

    # Optionally connect and discover tools
    if body.get("auto_discover", False):
        from ..mcp.client import MCPClient

        try:
            client = MCPClient(server_url=url, api_key=body.get("api_key"))
            await client.connect()
            tools = await client.list_tools()
            server_entry["status"] = "connected"
            server_entry["tools_discovered"] = len(tools)
            await client.disconnect()
        except Exception as e:
            server_entry["status"] = f"error: {e}"

    return server_entry


# ==================== External MCP Server Registry ====================


@router.get("/v1/mcp/external/servers")
async def list_external_mcp_servers(key_info: dict[str, Any] = Depends(require_api_key)):
    """List all registered external MCP servers (registry-based)."""
    registry = get_external_mcp_registry()
    servers = registry.list_servers()
    return {"servers": servers, "total": len(servers)}


@router.post("/v1/mcp/external/servers")
async def register_external_mcp_server(
    body: dict[str, Any],
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Register a new external MCP server via the registry.

    Body: {"name": "...", "command": "npx", "args": [...], "transport": "stdio",
           "url": "", "env": {}, "enabled": true, "auto_discover": true}
    """
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Only admin/operator can register MCP servers")

    name = body.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")

    registry = get_external_mcp_registry()
    server = ExternalMCPServer(
        name=name,
        command=body.get("command", ""),
        args=body.get("args", []),
        env=body.get("env", {}),
        transport=body.get("transport", "stdio"),
        url=body.get("url", ""),
        enabled=body.get("enabled", True),
        auto_discover=body.get("auto_discover", True),
    )
    registry.register(server)
    return {"status": "registered", "name": name, "server": registry.list_servers()[-1]}


@router.delete("/v1/mcp/external/servers/{name}")
async def unregister_external_mcp_server(
    name: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Unregister an external MCP server by name."""
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Only admin/operator can unregister MCP servers")

    registry = get_external_mcp_registry()
    if not registry.get_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    registry.unregister(name)
    return {"status": "unregistered", "name": name}


@router.get("/v1/mcp/external/tools")
async def list_external_discovered_tools(key_info: dict[str, Any] = Depends(require_api_key)):
    """List all tools discovered from external MCP servers."""
    registry = get_external_mcp_registry()
    tools = registry.get_all_discovered_tools()
    return {"tools": tools, "total": len(tools)}
