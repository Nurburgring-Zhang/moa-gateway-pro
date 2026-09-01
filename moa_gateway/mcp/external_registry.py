"""External MCP Server registry and management.

Manages connections to external MCP servers (stdio/SSE/HTTP transports),
tracks discovered tools, and supports config-driven auto-registration.

Real-connection semantics:
- ``stdio`` transport uses :class:`StdioMCPClient` to spawn the server as a
  child process (command allowlist enforced) and performs a genuine
  JSON-RPC ``initialize`` handshake + ``tools/list`` discovery over stdin/
  stdout. The live client is retained so discovered tools can be *called*.
- ``http``/``sse`` transports use :class:`MCPClient` for the same handshake
  over the network.
- Connection status/error is tracked per server and surfaced via the API.

Discovered tools are stored under namespaced keys
``external__<server>__<tool>`` so they can be merged into the local tool
surface without colliding with built-in tools.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Prefix used when external tools are merged into the local MCP tool surface.
EXTERNAL_TOOL_PREFIX = "external__"

#: Separator between the server name and the tool name in namespaced ids.
_EXTERNAL_SEP = "__"


def make_external_tool_name(server_name: str, tool_name: str) -> str:
    """Build the namespaced id used on the local tool surface."""
    return f"{EXTERNAL_TOOL_PREFIX}{server_name}{_EXTERNAL_SEP}{tool_name}"


def parse_external_tool_name(
    namespaced: str, known_servers: list[str] | None = None
) -> tuple[str, str] | None:
    """Split ``external__<server>__<tool>`` back into (server, tool).

    Prefers matching against ``known_servers`` so server names containing a
    single underscore survive; falls back to the first ``__`` boundary.
    Returns None when the id is not a valid external tool reference.
    """
    if not namespaced or not namespaced.startswith(EXTERNAL_TOOL_PREFIX):
        return None
    rest = namespaced[len(EXTERNAL_TOOL_PREFIX):]
    if known_servers:
        for server_name in known_servers:
            prefix = f"{server_name}{_EXTERNAL_SEP}"
            if rest.startswith(prefix) and len(rest) > len(prefix):
                return server_name, rest[len(prefix):]
    server, sep, tool = rest.partition(_EXTERNAL_SEP)
    if not sep or not server or not tool:
        return None
    return server, tool


@dataclass
class ExternalMCPServer:
    """External MCP Server configuration."""

    name: str
    command: str = ""  # npx/uvx/python etc. (must be on the stdio allowlist)
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    transport: str = "stdio"  # stdio/sse/http
    url: str = ""  # SSE/HTTP mode URL
    cwd: str = ""  # optional working directory for stdio servers
    enabled: bool = True
    auto_discover: bool = True
    # Runtime status (never persisted as truth — re-derived on reconnect)
    status: str = "registered"  # registered/connected/error/disabled
    last_error: str = ""
    tools_discovered: int = 0


class ExternalMCPRegistry:
    """External MCP Server registry with REAL connection + tool discovery."""

    def __init__(self):
        self._servers: dict[str, ExternalMCPServer] = {}
        self._discovered_tools: dict[str, dict] = {}  # namespaced -> {server, tool, definition}
        self._clients: dict[str, Any] = {}  # name -> live MCPClient/StdioMCPClient

    def register(self, server: ExternalMCPServer) -> None:
        """Register or replace an external MCP server.

        Re-registering an existing name first tears down the old client so a
        replaced stdio server never leaks its subprocess.
        """
        if server.name in self._clients:
            self._close_client(server.name)
        self._servers[server.name] = server
        logger.info(
            "Registered external MCP server: %s (transport=%s)", server.name, server.transport
        )

    def unregister(self, name: str) -> None:
        """Remove an external MCP server and clean up its client + tools.

        For stdio servers this REALLY terminates the child process (graceful
        close, then kill, then reap) — no orphaned subprocesses.
        """
        self._close_client(name)
        if name in self._servers:
            del self._servers[name]
            self._discovered_tools = {
                k: v for k, v in self._discovered_tools.items() if v.get("server") != name
            }
            logger.info("Unregistered external MCP server: %s", name)

    def _close_client(self, name: str) -> None:
        """Tear down the live client registered under ``name`` (if any)."""
        client = self._clients.pop(name, None)
        if client is not None:
            self._close_stale(client, name)

    def _close_stale(self, client: Any, name: str) -> None:
        """Really close a client: stdio children are terminated + reaped,
        HTTP clients disconnected (async-scheduled when a loop is running)."""
        # Stdio clients expose a sync terminate() that kills+reaps the child.
        terminate = getattr(client, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception as e:  # noqa: BLE001 - cleanup must not raise
                logger.warning("failed to terminate stdio MCP client %s: %s", name, e)
            return
        # HTTP clients need an async disconnect.
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and loop.is_running():
            loop.create_task(client.disconnect())
        else:
            try:
                asyncio.run(client.disconnect())
            except Exception as e:  # noqa: BLE001
                logger.warning("failed to disconnect MCP client %s: %s", name, e)

    async def shutdown_all(self) -> None:
        """Terminate every live client (app-shutdown hook)."""
        for name in list(self._clients):
            client = self._clients.get(name)
            if client is None:
                continue
            try:
                await client.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.warning("shutdown: failed to close MCP client %s: %s", name, e)
            self._clients.pop(name, None)

    async def connect_and_discover(self, name: str) -> dict[str, Any]:
        """Actually connect to the server and discover its tools.

        Returns an honest status dict. Never fabricates success:
        - stdio transport  -> real subprocess spawn + JSON-RPC handshake
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
            return await self._connect_stdio(server)

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

        self._record_success(server, client, [t.model_dump() for t in tools])
        return {
            "name": name,
            "status": "connected",
            "tools_discovered": len(tools),
            "tools": [t.name for t in tools],
        }

    async def _connect_stdio(self, server: ExternalMCPServer) -> dict[str, Any]:
        """Spawn a stdio server subprocess and run the real MCP handshake."""
        from ..config import get_settings
        from .stdio_client import StdioMCPClient

        name = server.name
        if not server.command:
            server.status = "error"
            server.last_error = "command is required for stdio transport"
            return {"name": name, "status": "error", "error": server.last_error}

        settings = get_settings()
        mcp_cfg = settings.mcp
        try:
            client = StdioMCPClient(
                command=server.command,
                args=list(server.args or []),
                env=dict(server.env or {}),
                cwd=server.cwd or None,
                name=name,
                timeout=mcp_cfg.stdio_request_timeout,
                shutdown_timeout=mcp_cfg.stdio_shutdown_timeout,
                allowed_commands=list(mcp_cfg.stdio_allowed_commands),
                strip_secret_env=mcp_cfg.stdio_strip_secret_env,
            )
            await client.connect()
            tools = await client.list_tools()
        except Exception as e:
            server.status = "error"
            server.last_error = f"{type(e).__name__}: {e}"
            try:
                await client.shutdown()
            except Exception:
                pass
            logger.warning("external MCP stdio server %s connect failed: %s", name, server.last_error)
            return {"name": name, "status": "error", "error": server.last_error}

        self._record_success(server, client, [t.model_dump() for t in tools])
        return {
            "name": name,
            "status": "connected",
            "tools_discovered": len(tools),
            "tools": [t.name for t in tools],
            "pid": client.pid,
        }

    def _record_success(
        self, server: ExternalMCPServer, client: Any, tool_defs: list[dict]
    ) -> None:
        """Retain a live client and record its discovered tools (namespaced)."""
        # Drop stale tools from a previous connection of the same server.
        self._discovered_tools = {
            k: v for k, v in self._discovered_tools.items() if v.get("server") != server.name
        }
        # Tear down a previous live client (reconnect) — never leak its
        # subprocess / HTTP session.
        previous = self._clients.get(server.name)
        if previous is not None and previous is not client:
            self._clients.pop(server.name, None)
            self._close_stale(previous, server.name)
        self._clients[server.name] = client
        server.status = "connected"
        server.last_error = ""
        server.tools_discovered = len(tool_defs)
        for definition in tool_defs:
            tool_name = definition.get("name", "")
            if not tool_name:
                continue
            self._discovered_tools[make_external_tool_name(server.name, tool_name)] = {
                "server": server.name,
                "tool": tool_name,
                "definition": definition,
            }
        logger.info(
            "external MCP server %s connected, %d tools discovered",
            server.name,
            len(tool_defs),
        )

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

    def call_external_tool_by_namespaced_name(
        self, namespaced: str, arguments: dict | None = None
    ) -> Any:
        """Resolve ``external__<server>__<tool>`` and return a coroutine that
        performs the real forwarded call. Raises KeyError when unknown."""
        parsed = parse_external_tool_name(namespaced, list(self._servers))
        if parsed is None:
            raise KeyError(f"invalid external tool name: {namespaced}")
        server_name, tool_name = parsed
        if server_name not in self._servers:
            raise KeyError(f"server '{server_name}' not registered")
        return self.call_tool(server_name, tool_name, arguments)

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

    def server_names(self) -> list[str]:
        """Names of all registered servers (for namespaced-tool parsing)."""
        return list(self._servers)

    def get_client(self, name: str) -> Any | None:
        """Get the live client for a connected server (or None)."""
        return self._clients.get(name)

    def add_discovered_tool(self, tool_name: str, server_name: str, definition: dict) -> None:
        """Register a discovered tool (stored under its namespaced id)."""
        self._discovered_tools[make_external_tool_name(server_name, tool_name)] = {
            "server": server_name,
            "tool": tool_name,
            "definition": definition,
        }

    def remove_discovered_tool(self, namespaced_name: str) -> bool:
        """Remove one discovered tool by its namespaced id. True if removed."""
        return self._discovered_tools.pop(namespaced_name, None) is not None

    def get_all_discovered_tools(self) -> dict:
        """Return all discovered tools (namespaced name -> metadata)."""
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
                    "cwd": s.cwd,
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
