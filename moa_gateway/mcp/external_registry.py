"""External MCP Server registry and management.

Manages connections to external MCP servers (stdio/SSE/HTTP transports),
tracks discovered tools, and supports config-driven auto-registration.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

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


class ExternalMCPRegistry:
    """External MCP Server registry with tool discovery tracking."""

    def __init__(self):
        self._servers: dict[str, ExternalMCPServer] = {}
        self._discovered_tools: dict[str, dict] = {}  # tool_name -> {server, definition}

    def register(self, server: ExternalMCPServer) -> None:
        """Register or replace an external MCP server."""
        self._servers[server.name] = server
        logger.info("Registered external MCP server: %s (transport=%s)", server.name, server.transport)

    def unregister(self, name: str) -> None:
        """Remove an external MCP server and clean up its discovered tools."""
        if name in self._servers:
            del self._servers[name]
            self._discovered_tools = {
                k: v for k, v in self._discovered_tools.items() if v.get("server") != name
            }
            logger.info("Unregistered external MCP server: %s", name)

    def list_servers(self) -> list[dict]:
        """Return all registered servers as a list of dicts."""
        return [
            {
                "name": s.name,
                "transport": s.transport,
                "enabled": s.enabled,
                "url": s.url,
                "command": s.command,
                "auto_discover": s.auto_discover,
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


# Global singleton
_registry = ExternalMCPRegistry()


def get_external_mcp_registry() -> ExternalMCPRegistry:
    """Get the global ExternalMCPRegistry singleton."""
    return _registry
