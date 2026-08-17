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

# Track connected external MCP servers (legacy in-memory view; the registry
# below is the source of truth and is persisted across restarts).
_external_servers: list[dict[str, Any]] = []

_MCP_PERSIST_KEY = "mcp_external_servers"


def _persist_external_servers() -> None:
    """Persist registered external MCP servers so they survive restarts."""
    try:
        from ..storage import get_storage

        registry = get_external_mcp_registry()
        get_storage().set_config_override(_MCP_PERSIST_KEY, registry.to_config_list())
    except Exception as e:  # pragma: no cover
        logger.warning("failed to persist external MCP servers: %s", e)


async def restore_persisted_external_servers() -> None:
    """Re-register persisted external MCP servers and reconnect (startup hook).

    Honest behavior: each server's live status is re-derived by actually
    attempting the connection; failures are recorded, never hidden.
    """
    from ..storage import get_storage

    registry = get_external_mcp_registry()
    try:
        stored = get_storage().get_config_overrides().get(_MCP_PERSIST_KEY) or []
    except Exception as e:  # pragma: no cover
        logger.warning("could not load persisted external MCP servers: %s", e)
        return
    for entry in stored:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        try:
            server = ExternalMCPServer(
                name=entry["name"],
                command=entry.get("command", ""),
                args=entry.get("args", []),
                env=entry.get("env", {}),
                transport=entry.get("transport", "stdio"),
                url=entry.get("url", ""),
                enabled=entry.get("enabled", True),
                auto_discover=entry.get("auto_discover", True),
            )
        except TypeError:
            continue
        registry.register(server)
        if server.auto_discover and server.enabled:
            try:
                await registry.connect_and_discover(server.name)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "external MCP server %s reconnect failed: %s", server.name, e
                )
    if stored:
        logger.info("restored %d persisted external MCP server(s)", len(stored))


def _is_safe_external_url(url: str) -> bool:
    """SSRF guard: reject URLs pointing at private/loopback/link-local addresses.

    Allows public hosts only. Blocks 127.0.0.0/8, 10/8, 172.16/12, 192.168/16,
    169.254/16 (link-local / cloud metadata), ::1, fc00::/7, fe80::/10.

    v3.1.1 audit P1-6 fix: delegates to the hardened shared validator. The
    v3.1.0 version let any non-IP hostname through without resolving it, so
    an attacker-controlled domain whose A record points at 169.254.169.254 or
    an RFC1918 address (DNS rebinding) bypassed the guard. The shared
    validator resolves every hostname via getaddrinfo and rejects any
    internal result.
    """
    from ..utils.url_validator import is_safe_external_url

    ok, _reason = is_safe_external_url(url)
    return ok


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
    try:
        request = JSONRPCRequest(**req)
    except Exception as e:
        # JSON-RPC -32600 Invalid Request: malformed/missing required fields
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "error": {
                "code": -32600,
                "message": "Invalid Request",
                "data": str(e),
            },
        }
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
    body: dict[str, Any] = None,  # type: ignore[assignment]
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

    Body: {"url": "http://...", "api_key": "...", "name": "...",
           "auto_discover": true}

    Delegates to the external registry so the server is persisted, really
    connected, and its tools become discoverable/callable (audit F10 fix).
    """
    # Only admin/operator can register servers
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Only admin/operator can register MCP servers")

    url = body.get("url")
    if not url:
        raise HTTPException(status_code=400, detail="url is required")

    # SSRF guard: block internal/private/metadata URLs
    if not _is_safe_external_url(url):
        raise HTTPException(
            status_code=400,
            detail="Refusing to register server: URL must point to a public host "
            "(private/loopback/link-local addresses blocked). Set "
            "MOA_ALLOW_SSRF_INTERNAL=1 to override for trusted internal deployments.",
        )

    name = body.get("name") or url
    registry = get_external_mcp_registry()
    api_key = body.get("api_key")
    server = ExternalMCPServer(
        name=name,
        transport="http",
        url=url,
        env={"api_key": api_key} if api_key else {},
        enabled=True,
        auto_discover=bool(body.get("auto_discover", False)),
    )
    registry.register(server)

    result: dict[str, Any] = {"name": name, "url": url}
    if server.auto_discover:
        result.update(await registry.connect_and_discover(name))
    else:
        result["status"] = "registered"
        result["tools_discovered"] = 0

    _persist_external_servers()
    # Keep the legacy in-memory view in sync for backward compatibility.
    # NOTE (audit F-security): never store/return the plaintext api_key here —
    # GET /v1/mcp/servers is accessible to any API-key holder, so the secret
    # must not appear in this list. Only a masked hint is kept.
    _external_servers.append(
        {
            "url": url,
            "name": name,
            "has_api_key": bool(api_key),
            "status": result.get("status", "registered"),
            "tools_discovered": result.get("tools_discovered", 0),
        }
    )
    return result


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

    transport = body.get("transport", "stdio")
    url = body.get("url", "")
    # SSRF guard for network transports (audit F10 + existing SSRF policy).
    if transport in ("http", "sse") and url and not _is_safe_external_url(url):
        raise HTTPException(
            status_code=400,
            detail="Refusing to register server: URL must point to a public host "
            "(private/loopback/link-local addresses blocked). Set "
            "MOA_ALLOW_SSRF_INTERNAL=1 to override for trusted internal deployments.",
        )

    registry = get_external_mcp_registry()
    server = ExternalMCPServer(
        name=name,
        command=body.get("command", ""),
        args=body.get("args", []),
        env=body.get("env", {}),
        transport=transport,
        url=url,
        enabled=body.get("enabled", True),
        auto_discover=body.get("auto_discover", True),
    )
    registry.register(server)

    connect_result: dict[str, Any] = {}
    if server.auto_discover and server.enabled:
        # Really attempt connection + discovery so the caller sees the truth.
        connect_result = await registry.connect_and_discover(name)

    _persist_external_servers()
    return {
        "status": connect_result.get("status", server.status),
        "name": name,
        "connect": connect_result or None,
        "server": registry.list_servers()[-1],
    }


@router.post("/v1/mcp/external/servers/{name}/connect")
async def connect_external_mcp_server(
    name: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """(Re)connect to a registered external MCP server and rediscover tools."""
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(status_code=403, detail="Only admin/operator can connect MCP servers")
    registry = get_external_mcp_registry()
    if not registry.get_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    return await registry.connect_and_discover(name)


@router.post("/v1/mcp/external/servers/{name}/tools/{tool_name}/call")
async def call_external_mcp_tool(
    name: str,
    tool_name: str,
    body: dict[str, Any] = None,  # type: ignore[assignment]
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Call a discovered tool on a connected external MCP server (real call).

    Audit F-security: invoking a tool on an external server is a privileged
    operation (it executes whatever the remote server does). Require the same
    admin/operator role as register/connect/unregister so a readonly key cannot
    escalate to arbitrary external tool execution.
    """
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(
            status_code=403,
            detail="Only admin/operator can call tools on external MCP servers",
        )
    body = body or {}
    registry = get_external_mcp_registry()
    if not registry.get_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    try:
        result = await registry.call_tool(name, tool_name, body.get("arguments", {}))
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return result


@router.delete("/v1/mcp/external/servers/{name}")
async def unregister_external_mcp_server(
    name: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Unregister an external MCP server by name."""
    role = key_info.get("role", "readonly")
    if role not in ("admin", "operator"):
        raise HTTPException(
            status_code=403, detail="Only admin/operator can unregister MCP servers"
        )

    registry = get_external_mcp_registry()
    if not registry.get_server(name):
        raise HTTPException(status_code=404, detail=f"Server '{name}' not found")
    registry.unregister(name)
    _persist_external_servers()
    return {"status": "unregistered", "name": name}


@router.get("/v1/mcp/external/tools")
async def list_external_discovered_tools(key_info: dict[str, Any] = Depends(require_api_key)):
    """List all tools discovered from external MCP servers."""
    registry = get_external_mcp_registry()
    tools = registry.get_all_discovered_tools()
    return {"tools": tools, "total": len(tools)}
