"""MCP Client - connects to external MCP servers to discover and call tools."""
from __future__ import annotations

import logging
import uuid
from typing import Any, List, Optional

import httpx

from .protocol import JSONRPCRequest, JSONRPCResponse, ToolDefinition

logger = logging.getLogger(__name__)


class MCPClient:
    """Client for connecting to external MCP servers."""

    def __init__(self, server_url: str, api_key: Optional[str] = None, timeout: float = 30.0):
        self.server_url = server_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._tools: List[ToolDefinition] = []
        self._server_info: dict[str, Any] = {}
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def tools(self) -> List[ToolDefinition]:
        return self._tools

    @property
    def server_info(self) -> dict[str, Any]:
        return self._server_info

    async def connect(self) -> dict[str, Any]:
        """Connect to the remote MCP server (initialize handshake)."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        self._client = httpx.AsyncClient(
            base_url=self.server_url,
            headers=headers,
            timeout=self.timeout,
        )

        resp = await self._send(
            JSONRPCRequest(
                method="initialize",
                params={
                    "protocolVersion": "2024-11-05",
                    "clientInfo": {"name": "moa-gateway-client", "version": "2.0.0"},
                    "capabilities": {},
                },
            )
        )
        if resp.result:
            self._server_info = resp.result.get("serverInfo", {})
            self._connected = True
        return resp.result or {}

    async def list_tools(self) -> List[ToolDefinition]:
        """Discover tools from the remote server."""
        resp = await self._send(JSONRPCRequest(method="tools/list"))
        if resp.result:
            self._tools = [
                ToolDefinition(**t) for t in resp.result.get("tools", [])
            ]
        return self._tools

    async def call_tool(self, name: str, arguments: Optional[dict] = None) -> dict[str, Any]:
        """Call a tool on the remote server."""
        resp = await self._send(
            JSONRPCRequest(
                method="tools/call",
                params={"name": name, "arguments": arguments or {}},
            )
        )
        if resp.error:
            return {"error": resp.error}
        return resp.result or {}

    async def ping(self) -> bool:
        """Ping the remote server."""
        try:
            resp = await self._send(JSONRPCRequest(method="ping"))
            return resp.error is None
        except Exception:
            return False

    async def disconnect(self) -> None:
        """Close the connection."""
        if self._client:
            await self._client.aclose()
            self._client = None
        self._connected = False

    async def _send(self, request: JSONRPCRequest) -> JSONRPCResponse:
        """Send a JSON-RPC request and parse the response."""
        if not self._client:
            raise RuntimeError("Not connected. Call connect() first.")
        request.id = request.id or str(uuid.uuid4())
        resp = await self._client.post("/", json=request.model_dump())
        resp.raise_for_status()
        return JSONRPCResponse(**resp.json())
