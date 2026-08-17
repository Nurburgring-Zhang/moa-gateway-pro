"""External MCP Server registry and management.

Manages connections to external MCP servers (stdio/SSE/HTTP transports),
tracks discovered tools, and supports config-driven auto-registration.

Real-connection semantics (audit F10 fix):
- ``http``/``sse`` transports use :class:`MCPClient` to perform a genuine
  JSON-RPC ``initialize`` handshake + ``tools/list`` discovery. The live
  client is retained so discovered tools can actually be *called*.
- ``stdio`` transport is NOT silently accepted: subprocess launching is not
  available in this deployment, so registration reports an honest
  ``unsupported`` status instead of pretending to be connected.
- Connection status/error is tracked per server and surfaced via the API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExternalMCPServer:
    """External MCP Server configuration."""

    name: str
    command: str = ""  # npx/uvx/python etc.
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    transport: str = "stdio"  # stdio/sse/http
    url: str = ""  # SSE/HTTP mode URL
    enabled: bool = True
    auto_discover: bool = True
    # Runtime status (never persisted as truth — re-derived on reconnect)
    status: str = "registered"  # registered/connected/error/unsupported/disabled
    last_error: str = ""
    tools_discovered: int = 0


class ExternalMCPRegistry:
    """External MCP Server registry with REAL connection + tool discovery."""

    def __init__(self):
        self._servers: dict[str, ExternalMCPServer] = {}
        self._discovered_tools: dict[str, dict] = {}  # tool_name -> {server, definition}
        self._clients: dict[str, Any] = {}  # name -> live MCPClient

    def register(self, server: ExternalMCPServer) -> None:
        """Register or replace an external MCP server."""
        self._servers[server.name] = server
        logger.info(
            "Registered external MCP server: %s (transport=%s)", server.name, server.transport
        )

    def unregister(self, name: str) -> None:
        """Remove an external MCP server and clean up its client + tools."""
        client = self._clients.pop(name, None)
        if client is not None:
            try:
                import asyncio

                asyncio.get_event_loop().create_task(client.disconnect())
            except Exception:
                pass
        if name in self._servers:
            del self._servers[name]
            self._discovered_tools = {
                k: v for k, v in self._discovered_tools.items() if v.get("server") != name
            }
            logger.info("Unregistered external MCP server: %s", name)

    async def connect_and_discover(self, name: str) -> dict[str, Any]:
        """Actually connect to the server and discover its tools.

        Returns an honest status dict. Never fabricates success:
        - stdio transport  -> status=unsupported (no subprocess in this deployment)
        - http/sse failure -> status=error with the real exception message
        - success          -> status=connected with real discovered tools
        """
        server = self._servers.get(name)
        if server is None:
            return {"name": name, "status": "error", "error": "server not registered"}
        if not server.enabled:
            server.status = "disabled"
            return {"name": name, "status": "disabled"}

        if server.transport == "stdio":
            server.status = "unsupported"
            server.last_error = (
                "stdio transport requires a subprocess launcher, which is not "
                "available in this deployment. Use transport=http|sse with a URL."
            )
            logger.warning("external MCP server %s: %s", name, server.last_error)
            return {"name": name, "status": server.status, "error": server.last_error}

        if not server.url:
            server.status = "error"
            server.last_error = "url is required for http/sse transport"
            return {"name": name, "status": "error", "error": server.last_error}

        # SSRF guard is enforced at the route layer before registration.
        from .client import MCPClient

        client = MCPClient(server_url=server.url, api_key=server.env.get("api_key") or None)
        try:
            await client.connect()
            tools = await client.list_tools()
        except Exception as e:
            server.status = "error"
            server.last_error = f"{type(e).__name__}: {e}"
            try:
                await client.disconnect()
            except Exception:
                pass
            logger.warning("external MCP server %s connect failed: %s", name, server.last_error)
            return {"name": name, "status": "error", "error": server.last_error}

        # Success — retain the live client and record real tool definitions.
        self._clients[name] = client
        server.status = "connected"
        server.last_error = ""
        server.tools_discovered = len(tools)
        for t in tools:
            self._discovered_tools[t.name] = {
                "server": name,
                "definition": t.model_dump(),
            }
        logger.info(
            "external MCP server %s connected, %d tools discovered", name, len(tools)
        )
        return {
            "name": name,
            "status": "connected",
            "tools_discovered": len(tools),
            "tools": [t.name for t in tools],
        }

    async def call_tool(self, name: str, tool_name: str, arguments: dict | None = None) -> dict:
        """Call a tool on a connected external server (real JSON-RPC call)."""
        server = self._servers.get(name)
        if server is None:
            raise KeyError(f"server '{name}' not registered")
        client = self._clients.get(name)
        if client is None or not client.connected:
            raise ConnectionError(
                f"server '{name}' is not connected (status={server.status}). "
                "Re-register with auto_discover=true or fix its connection."
            )
        return await client.call_tool(tool_name, arguments or {})

    def list_servers(self) -> list[dict]:
        """Return all registered servers as a list of dicts (with live status)."""
        return [
            {
                "name": s.name,
                "transport": s.transport,
                "enabled": s.enabled,
                "url": s.url,
                "command": s.command,
                "auto_discover": s.auto_discover,
                "status": s.status,
                "last_error": s.last_error,
                "tools_discovered": s.tools_discovered,
            }
            for s in self._servers.values()
        ]

    def get_server(self, name: str) -> ExternalMCPServer | None:
        """Get a server by name."""
        return self._servers.get(name)

    def add_discovered_tool(self, tool_name: str, server_name: str, definition: dict) -> None:
        """Register a tool discovered from an external server."""
        self._discovered_tools[tool_name] = {"server": server_name, "definition": definition}

    def get_all_discovered_tools(self) -> dict:
        """Return all discovered tools (tool_name -> metadata)."""
        return self._discovered_tools.copy()

    def load_from_config(self, config: dict) -> None:
        """Load MCP server list from a config dict (e.g. from config.yaml).

        Expected format:
            mcp_servers:
              - name: my-server
                command: npx
                args: ["-y", "@modelcontextprotocol/server-filesystem"]
                transport: stdio
        """
        servers = config.get("mcp_servers", [])
        for s in servers:
            try:
                self.register(ExternalMCPServer(**s))
            except TypeError as e:
                logger.warning("Failed to load MCP server config %s: %s", s.get("name", "?"), e)

    def to_config_list(self) -> list[dict]:
        """Serialize registered servers (config fields only) for persistence.

        Audit F-security: any ``api_key`` in ``env`` is REDACTED before
        persistence so plaintext third-party secrets are never written to the
        config_overrides store. On restart such servers re-register without the
        key and must be re-authorized to reconnect.
        """
        result = []
        for s in self._servers.values():
            env = dict(s.env or {})
            if "api_key" in env and env["api_key"]:
                env["api_key"] = ""  # redact secret; do not persist plaintext
            result.append(
                {
                    "name": s.name,
                    "command": s.command,
                    "args": s.args,
                    "env": env,
                    "transport": s.transport,
                    "url": s.url,
                    "enabled": s.enabled,
                    "auto_discover": s.auto_discover,
                }
            )
        return result


# Global singleton
_registry = ExternalMCPRegistry()


def get_external_mcp_registry() -> ExternalMCPRegistry:
    """Get the global ExternalMCPRegistry singleton."""
    return _registry
