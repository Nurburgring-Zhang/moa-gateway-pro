"""Tests for the MCP stdio launcher, SSE delivery chain, external tool
merge and standalone-server RBAC.

Every subprocess test spawns a REAL python child process that speaks
JSON-RPC 2.0 over stdio — no mocked subprocesses anywhere.
"""
from __future__ import annotations

import json
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Mini MCP server scripts (written to tmp and spawned as real subprocesses)
# ---------------------------------------------------------------------------

MINI_SERVER = textwrap.dedent(
    '''
    import json
    import os
    import sys

    TOOLS = [
        {"name": "echo", "description": "Echo text back",
         "inputSchema": {"type": "object",
                          "properties": {"text": {"type": "string"}},
                          "required": ["text"]}},
        {"name": "add", "description": "Add two numbers",
         "inputSchema": {"type": "object",
                          "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                          "required": ["a", "b"]}},
        {"name": "env_probe", "description": "Report env/cwd",
         "inputSchema": {"type": "object", "properties": {}}},
        {"name": "boom", "description": "Always fails",
         "inputSchema": {"type": "object", "properties": {}}},
    ]


    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()


    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            send({"jsonrpc": "2.0", "id": None,
                  "error": {"code": -32700, "message": "parse error"}})
            continue
        method = req.get("method")
        rid = req.get("id")
        params = req.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "mini-mcp", "version": "0.1.0"},
                "capabilities": {"tools": {}}}})
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            if name == "echo":
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": args.get("text", "")}],
                    "isError": False}})
            elif name == "add":
                total = float(args.get("a", 0)) + float(args.get("b", 0))
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": str(total)}],
                    "isError": False}})
            elif name == "env_probe":
                payload = {
                    "cwd": os.getcwd(),
                    "probe": os.environ.get("MCP_PROBE_VALUE", ""),
                    "admin_pw": os.environ.get("MOA_ADMIN_PASSWORD", ""),
                    "gw_key": os.environ.get("MOA_GATEWAY_KEY", ""),
                    "jwt": os.environ.get("MOA_JWT_SECRET", ""),
                }
                send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps(payload)}],
                    "isError": False}})
            elif name == "boom":
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32603, "message": "boom tool failed on purpose"}})
            else:
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32602, "message": "unknown tool: %s" % name}})
        else:
            if rid is not None:
                send({"jsonrpc": "2.0", "id": rid,
                      "error": {"code": -32601, "message": "method not found"}})
    '''
)

# Answers initialize, then crashes with a distinctive stderr marker.
CRASH_SERVER = textwrap.dedent(
    '''
    import json
    import sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("method") == "initialize":
            send({"jsonrpc": "2.0", "id": req.get("id"), "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "crash-mcp", "version": "0.0.1"},
                "capabilities": {"tools": {}}}})
        else:
            sys.stderr.write("CRASH-MARKER: deliberate exit\\n")
            sys.stderr.flush()
            sys.exit(3)
    '''
)

# Reads stdin but never answers — for timeout tests.
SILENT_SERVER = textwrap.dedent(
    '''
    import sys

    for line in sys.stdin:
        pass
    '''
)

# Answers using LSP-style Content-Length framing instead of bare lines.
CONTENT_LENGTH_SERVER = textwrap.dedent(
    '''
    import json
    import sys

    TOOLS = [{"name": "echo", "description": "Echo (framed)",
              "inputSchema": {"type": "object",
                               "properties": {"text": {"type": "string"}}}}]


    def send(obj):
        body = json.dumps(obj).encode("utf-8")
        sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\\r\\n")
        sys.stdout.buffer.write(b"\\r\\n")
        sys.stdout.buffer.write(body)
        sys.stdout.buffer.flush()


    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        method = req.get("method")
        rid = req.get("id")
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "framed-mcp", "version": "0.1.0"},
                "capabilities": {"tools": {}}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            args = req.get("params", {}).get("arguments", {}) or {}
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "framed:" + args.get("text", "")}],
                "isError": False}})
        elif method == "ping":
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
    '''
)


@pytest.fixture
def mini_server_path(tmp_path: Path) -> Path:
    p = tmp_path / "mini_mcp_server.py"
    p.write_text(MINI_SERVER, encoding="utf-8")
    return p


@pytest.fixture
def crash_server_path(tmp_path: Path) -> Path:
    p = tmp_path / "crash_mcp_server.py"
    p.write_text(CRASH_SERVER, encoding="utf-8")
    return p


@pytest.fixture
def silent_server_path(tmp_path: Path) -> Path:
    p = tmp_path / "silent_mcp_server.py"
    p.write_text(SILENT_SERVER, encoding="utf-8")
    return p


@pytest.fixture
def framed_server_path(tmp_path: Path) -> Path:
    p = tmp_path / "framed_mcp_server.py"
    p.write_text(CONTENT_LENGTH_SERVER, encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _clean_external_registry():
    """Never leak registered servers/subprocesses across tests."""
    from moa_gateway.mcp.external_registry import get_external_mcp_registry

    registry = get_external_mcp_registry()
    before = set(registry.server_names())
    yield
    for name in set(registry.server_names()) - before:
        registry.unregister(name)


def _make_client(script: Path, **kwargs):
    from moa_gateway.mcp.stdio_client import StdioMCPClient

    kwargs.setdefault("timeout", 15.0)
    kwargs.setdefault("shutdown_timeout", 3.0)
    return StdioMCPClient(sys.executable, [str(script)], **kwargs)


# =====================================================================
# 1. StdioMCPClient — real subprocess lifecycle
# =====================================================================
class TestStdioMCPClient:
    async def test_connect_initialize_handshake(self, mini_server_path):
        client = _make_client(mini_server_path, name="mini")
        try:
            result = await client.connect()
            assert result["serverInfo"]["name"] == "mini-mcp"
            assert client.connected is True
            assert client.server_info["name"] == "mini-mcp"
            assert client.pid is not None and client.exit_code is None
        finally:
            await client.shutdown()

    async def test_list_tools_real_discovery(self, mini_server_path):
        client = _make_client(mini_server_path)
        try:
            await client.connect()
            tools = await client.list_tools()
            names = {t.name for t in tools}
            assert names == {"echo", "add", "env_probe", "boom"}
            echo = next(t for t in tools if t.name == "echo")
            assert echo.inputSchema["required"] == ["text"]
        finally:
            await client.shutdown()

    async def test_call_tool_echo_roundtrip(self, mini_server_path):
        client = _make_client(mini_server_path)
        try:
            await client.connect()
            result = await client.call_tool("echo", {"text": "hello stdio mcp"})
            assert result["isError"] is False
            assert result["content"][0]["text"] == "hello stdio mcp"
        finally:
            await client.shutdown()

    async def test_call_tool_add_passes_arguments(self, mini_server_path):
        client = _make_client(mini_server_path)
        try:
            await client.connect()
            result = await client.call_tool("add", {"a": 2, "b": 3})
            assert float(result["content"][0]["text"]) == 5.0
        finally:
            await client.shutdown()

    async def test_ping_live_server(self, mini_server_path):
        client = _make_client(mini_server_path)
        try:
            await client.connect()
            assert await client.ping() is True
        finally:
            await client.shutdown()

    async def test_jsonrpc_error_from_server_surfaces(self, mini_server_path):
        """A remote JSON-RPC error is reported honestly, not swallowed."""
        client = _make_client(mini_server_path)
        try:
            await client.connect()
            result = await client.call_tool("boom", {})
            assert "error" in result
            assert result["error"]["code"] == -32603
            assert "boom tool failed on purpose" in result["error"]["message"]
        finally:
            await client.shutdown()

    async def test_shutdown_terminates_and_reaps(self, mini_server_path):
        client = _make_client(mini_server_path)
        await client.connect()
        assert client.pid is not None
        await client.shutdown()
        assert client.running is False
        assert client.connected is False
        # Reaped: the exit code is known (wait()ed), so no zombie remains.
        assert client.exit_code is not None

    async def test_crash_propagation(self, crash_server_path):
        """Server crash mid-session surfaces as a real error on next call."""
        from moa_gateway.mcp.stdio_client import StdioMCPError

        client = _make_client(crash_server_path, name="crasher")
        try:
            await client.connect()  # initialize succeeds
            with pytest.raises(StdioMCPError) as exc_info:
                await client.call_tool("anything", {})
            message = str(exc_info.value)
            assert "crasher" in message
            assert "code=3" in message or "exit" in message.lower()
            assert "CRASH-MARKER" in client.stderr_tail
        finally:
            await client.shutdown()

    async def test_crash_before_any_response(self, tmp_path):
        """A server that dies before answering initialize fails the connect."""
        from moa_gateway.mcp.stdio_client import StdioMCPError

        script = tmp_path / "instant_exit.py"
        script.write_text("import sys\nsys.stderr.write('born to die\\n')\nsys.exit(9)\n")
        client = _make_client(script, name="instant-exit", timeout=5.0)
        with pytest.raises(StdioMCPError):
            await client.connect()
        assert client.connected is False

    async def test_timeout_propagation(self, silent_server_path):
        from moa_gateway.mcp.stdio_client import StdioMCPError

        client = _make_client(silent_server_path, name="silent", timeout=1.0)
        try:
            with pytest.raises(StdioMCPError) as exc_info:
                await client.connect()
            assert "timed out" in str(exc_info.value)
        finally:
            await client.shutdown()

    async def test_spawn_failure_propagates(self):
        """Command not found -> honest StdioMCPError, no placeholder."""
        from moa_gateway.mcp.stdio_client import StdioMCPClient, StdioMCPError

        client = StdioMCPClient(
            "moa-nonexistent-binary-xyz",
            allowed_commands={"moa-nonexistent-binary-xyz"},
            timeout=3.0,
        )
        with pytest.raises(StdioMCPError, match="command not found"):
            await client.start()

    async def test_env_injection_and_secret_stripping(self, mini_server_path, monkeypatch):
        """Operator env reaches the child; gateway secrets never do."""
        monkeypatch.setenv("MOA_ADMIN_PASSWORD", "super-secret-admin-pw")
        monkeypatch.setenv("MOA_GATEWAY_KEY", "super-secret-gw-key")
        monkeypatch.setenv("MOA_JWT_SECRET", "super-secret-jwt")
        client = _make_client(mini_server_path, env={"MCP_PROBE_VALUE": "probe-42"})
        try:
            await client.connect()
            result = await client.call_tool("env_probe", {})
            payload = json.loads(result["content"][0]["text"])
            assert payload["probe"] == "probe-42"
            assert payload["admin_pw"] == ""
            assert payload["gw_key"] == ""
            assert payload["jwt"] == ""
        finally:
            await client.shutdown()

    async def test_env_override_not_stripped_when_explicit(self, mini_server_path, monkeypatch):
        """An explicitly passed var wins over stripping (deliberate choice)."""
        monkeypatch.setenv("MOA_ADMIN_PASSWORD", "parent-secret")
        client = _make_client(
            mini_server_path, env={"MOA_ADMIN_PASSWORD": "deliberate-override"}
        )
        try:
            await client.connect()
            result = await client.call_tool("env_probe", {})
            payload = json.loads(result["content"][0]["text"])
            assert payload["admin_pw"] == "deliberate-override"
        finally:
            await client.shutdown()

    async def test_cwd_honored(self, mini_server_path, tmp_path):
        workdir = tmp_path / "workdir"
        workdir.mkdir()
        client = _make_client(mini_server_path, cwd=str(workdir))
        try:
            await client.connect()
            result = await client.call_tool("env_probe", {})
            payload = json.loads(result["content"][0]["text"])
            assert Path(payload["cwd"]).resolve() == workdir.resolve()
        finally:
            await client.shutdown()

    async def test_content_length_framing_server(self, framed_server_path):
        """The reader understands Content-Length framed responses too."""
        client = _make_client(framed_server_path, name="framed")
        try:
            await client.connect()
            assert client.server_info["name"] == "framed-mcp"
            tools = await client.list_tools()
            assert [t.name for t in tools] == ["echo"]
            result = await client.call_tool("echo", {"text": "abc"})
            assert result["content"][0]["text"] == "framed:abc"
        finally:
            await client.shutdown()


# =====================================================================
# 2. Command allowlist
# =====================================================================
class TestCommandAllowlist:
    def test_allowlist_rejects_unknown_command_at_construction(self):
        from moa_gateway.mcp.stdio_client import StdioMCPClient, StdioMCPError

        with pytest.raises(StdioMCPError, match="not in the allowed command list"):
            StdioMCPClient("powershell", ["-Command", "dir"])

    def test_allowlist_default_set(self):
        from moa_gateway.mcp.stdio_client import DEFAULT_STDIO_ALLOWED_COMMANDS

        assert {"python", "python3", "node", "npx", "uvx"} <= set(
            DEFAULT_STDIO_ALLOWED_COMMANDS
        )

    def test_is_command_allowed_matching(self):
        from moa_gateway.mcp.stdio_client import is_command_allowed

        allowed = ["python", "python3", "node", "npx", "uvx"]
        assert is_command_allowed("python", allowed)
        assert is_command_allowed("python3", allowed)
        assert is_command_allowed("node", allowed)
        assert is_command_allowed("npx", allowed)
        assert is_command_allowed("uvx", allowed)
        assert is_command_allowed(sys.executable, allowed)  # .../python.exe
        assert is_command_allowed("/usr/local/bin/python3", allowed)
        assert is_command_allowed("npx.cmd", allowed)

    def test_is_command_allowed_rejects(self):
        from moa_gateway.mcp.stdio_client import is_command_allowed

        allowed = ["python", "node"]
        assert not is_command_allowed("bash", allowed)
        assert not is_command_allowed("cmd", allowed)
        assert not is_command_allowed("powershell.exe", allowed)
        assert not is_command_allowed("", allowed)
        assert not is_command_allowed("python -c evil", allowed)  # not a bare name

    def test_custom_allowlist_extends(self):
        from moa_gateway.mcp.stdio_client import is_command_allowed

        assert is_command_allowed("my-runner", ["my-runner"])
        assert not is_command_allowed("python", ["my-runner"])


# =====================================================================
# 3. ExternalMCPRegistry — stdio branch
# =====================================================================
class TestExternalRegistryStdio:
    async def test_stdio_connect_and_discover(self, mini_server_path):
        from moa_gateway.mcp.external_registry import (
            ExternalMCPServer,
            get_external_mcp_registry,
        )

        registry = get_external_mcp_registry()
        registry.register(
            ExternalMCPServer(
                name="demo",
                command=sys.executable,
                args=[str(mini_server_path)],
                transport="stdio",
            )
        )
        result = await registry.connect_and_discover("demo")
        assert result["status"] == "connected"
        assert result["tools_discovered"] == 4
        assert set(result["tools"]) == {"echo", "add", "env_probe", "boom"}
        # Namespaced tool ids on the merged surface
        tools = registry.get_all_discovered_tools()
        assert "external__demo__echo" in tools
        assert tools["external__demo__echo"]["tool"] == "echo"
        assert registry.get_server("demo").status == "connected"

    async def test_stdio_allowlist_denied_via_settings(self, mini_server_path):
        """A command outside settings.mcp.stdio_allowed_commands never spawns."""
        from moa_gateway.mcp.external_registry import (
            ExternalMCPServer,
            get_external_mcp_registry,
        )

        registry = get_external_mcp_registry()
        registry.register(
            ExternalMCPServer(
                name="evil",
                command="bash",
                args=[str(mini_server_path)],
                transport="stdio",
            )
        )
        result = await registry.connect_and_discover("evil")
        assert result["status"] == "error"
        assert "allowed command" in result["error"]
        assert registry.get_client("evil") is None

    async def test_stdio_missing_command_error(self):
        from moa_gateway.mcp.external_registry import (
            ExternalMCPServer,
            get_external_mcp_registry,
        )

        registry = get_external_mcp_registry()
        registry.register(ExternalMCPServer(name="nocmd", transport="stdio"))
        result = await registry.connect_and_discover("nocmd")
        assert result["status"] == "error"
        assert "command is required" in result["error"]

    async def test_registry_call_tool_forwards_to_subprocess(self, mini_server_path):
        from moa_gateway.mcp.external_registry import (
            ExternalMCPServer,
            get_external_mcp_registry,
        )

        registry = get_external_mcp_registry()
        registry.register(
            ExternalMCPServer(
                name="fwd",
                command=sys.executable,
                args=[str(mini_server_path)],
                transport="stdio",
            )
        )
        await registry.connect_and_discover("fwd")
        result = await registry.call_tool("fwd", "echo", {"text": "via-registry"})
        assert result["content"][0]["text"] == "via-registry"

    async def test_unregister_really_terminates_subprocess(self, mini_server_path):
        from moa_gateway.mcp.external_registry import (
            ExternalMCPServer,
            get_external_mcp_registry,
        )

        registry = get_external_mcp_registry()
        registry.register(
            ExternalMCPServer(
                name="doomed",
                command=sys.executable,
                args=[str(mini_server_path)],
                transport="stdio",
            )
        )
        await registry.connect_and_discover("doomed")
        client = registry.get_client("doomed")
        assert client is not None and client.running
        registry.unregister("doomed")
        assert client.running is False
        assert client.exit_code is not None  # reaped
        assert registry.get_client("doomed") is None
        assert registry.get_all_discovered_tools() == {}

    async def test_reconnect_replaces_stale_client_and_tools(self, mini_server_path):
        from moa_gateway.mcp.external_registry import (
            ExternalMCPServer,
            get_external_mcp_registry,
        )

        registry = get_external_mcp_registry()
        registry.register(
            ExternalMCPServer(
                name="repl",
                command=sys.executable,
                args=[str(mini_server_path)],
                transport="stdio",
            )
        )
        await registry.connect_and_discover("repl")
        first_client = registry.get_client("repl")
        await registry.connect_and_discover("repl")  # reconnect
        assert registry.get_client("repl") is not first_client
        assert first_client.running is False  # old subprocess torn down
        tools = registry.get_all_discovered_tools()
        assert sum(1 for k in tools if k.startswith("external__repl__")) == 4


# =====================================================================
# 4. HTTP routes — registration, tool merge, forwarding, SSE delivery
# =====================================================================
@pytest.fixture
def app():
    from moa_gateway.config import Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "StdioTest#2024",
            "jwt_secret": "stdio-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": ["stdio-test-key-001"],
        }
    )
    with patch("moa_gateway.config.get_settings", return_value=settings):
        with patch("moa_gateway.config._settings", settings):
            from moa_gateway.server import create_app

            yield create_app()


@pytest.fixture
async def http_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


AUTH = {"Authorization": "Bearer stdio-test-key-001"}


async def _register_stdio(http_client, script: Path, name: str) -> dict:
    resp = await http_client.post(
        "/v1/mcp/external/servers",
        json={
            "name": name,
            "transport": "stdio",
            "command": sys.executable,
            "args": [str(script)],
            "auto_discover": True,
        },
        headers=AUTH,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "connected", body
    return body


class TestStdioRegistrationRoutes:
    async def test_register_stdio_server_real_connect(self, http_client, mini_server_path):
        body = await _register_stdio(http_client, mini_server_path, "route-demo")
        assert body["connect"]["tools_discovered"] == 4
        assert "echo" in body["connect"]["tools"]
        assert body["server"]["transport"] == "stdio"

    async def test_register_stdio_command_outside_allowlist_400(
        self, http_client, mini_server_path
    ):
        resp = await http_client.post(
            "/v1/mcp/external/servers",
            json={
                "name": "route-evil",
                "transport": "stdio",
                "command": "powershell",
                "args": [str(mini_server_path)],
            },
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "allowlist" in resp.json()["detail"]

    async def test_register_stdio_missing_command_400(self, http_client):
        resp = await http_client.post(
            "/v1/mcp/external/servers",
            json={"name": "route-nocmd", "transport": "stdio"},
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "command is required" in resp.json()["detail"]

    async def test_register_name_with_namespace_separator_400(
        self, http_client, mini_server_path
    ):
        resp = await http_client.post(
            "/v1/mcp/external/servers",
            json={
                "name": "bad__name",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(mini_server_path)],
            },
            headers=AUTH,
        )
        assert resp.status_code == 400
        assert "__" in resp.json()["detail"]

    async def test_register_bad_transport_400(self, http_client):
        resp = await http_client.post(
            "/v1/mcp/external/servers",
            json={"name": "bad-transport", "transport": "carrier-pigeon", "url": ""},
            headers=AUTH,
        )
        assert resp.status_code == 400

    async def test_unregister_route_terminates_subprocess(self, http_client, mini_server_path):
        await _register_stdio(http_client, mini_server_path, "route-doomed")
        from moa_gateway.mcp.external_registry import get_external_mcp_registry

        client = get_external_mcp_registry().get_client("route-doomed")
        assert client is not None and client.running
        resp = await http_client.delete(
            "/v1/mcp/external/servers/route-doomed", headers=AUTH
        )
        assert resp.status_code == 200
        assert client.running is False
        assert client.exit_code is not None


class TestExternalToolSurfaceMerge:
    async def test_rest_tools_list_includes_external(self, http_client, mini_server_path):
        await _register_stdio(http_client, mini_server_path, "merge-demo")
        resp = await http_client.get("/v1/mcp/tools", headers=AUTH)
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["tools"]]
        # built-ins first, external appended
        assert "moa_list_models" in names
        assert "external__merge-demo__echo" in names
        assert "external__merge-demo__add" in names
        assert names.index("moa_list_models") < names.index("external__merge-demo__echo")

    async def test_jsonrpc_tools_list_includes_external(self, http_client, mini_server_path):
        await _register_stdio(http_client, mini_server_path, "merge-rpc")
        resp = await http_client.post(
            "/v1/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        names = [t["name"] for t in resp.json()["result"]["tools"]]
        assert "external__merge-rpc__echo" in names

    async def test_jsonrpc_tools_call_external_forwards(self, http_client, mini_server_path):
        await _register_stdio(http_client, mini_server_path, "call-demo")
        resp = await http_client.post(
            "/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "external__call-demo__echo",
                    "arguments": {"text": "forwarded!"},
                },
            },
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("error") is None
        assert body["result"]["isError"] is False
        assert body["result"]["content"][0]["text"] == "forwarded!"

    async def test_rest_call_external_tool(self, http_client, mini_server_path):
        await _register_stdio(http_client, mini_server_path, "rest-call")
        resp = await http_client.post(
            "/v1/mcp/tools/external__rest-call__add/call",
            json={"arguments": {"a": 20, "b": 22}},
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        assert float(resp.json()["content"][0]["text"]) == 42.0

    async def test_guardrails_apply_to_external_calls(self, http_client, mini_server_path):
        await _register_stdio(http_client, mini_server_path, "guard-demo")
        resp = await http_client.post(
            "/v1/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "external__guard-demo__echo",
                    "arguments": {"text": "please run rm -rf / now"},
                },
            },
            headers=AUTH,
        )
        body = resp.json()
        assert body["error"]["code"] == -32602
        assert "Blocked dangerous pattern" in body["error"]["message"]

    async def test_external_rbac_denied_for_non_admin_role(self):
        """Server-level check: user role cannot call external tools."""
        from moa_gateway.mcp.protocol import JSONRPCRequest
        from moa_gateway.routes.mcp import get_mcp_server

        server = get_mcp_server()
        registry = server.external_registry
        registry.add_discovered_tool(
            "echo", "rbac-demo", {"name": "echo", "description": "x", "inputSchema": {}}
        )
        try:
            req = JSONRPCRequest(
                id=9,
                method="tools/call",
                params={"name": "external__rbac-demo__echo", "arguments": {}},
            )
            resp = await server.handle_request(req, user={"role": "user", "username": "u"})
            assert resp.error is not None
            assert "Permission denied" in resp.error["message"]
        finally:
            registry.remove_discovered_tool("external__rbac-demo__echo")

    async def test_tools_list_hides_external_from_non_admin(self):
        from moa_gateway.mcp.protocol import JSONRPCRequest
        from moa_gateway.routes.mcp import get_mcp_server

        server = get_mcp_server()
        registry = server.external_registry
        registry.add_discovered_tool(
            "echo", "hidden-demo", {"name": "echo", "description": "x", "inputSchema": {}}
        )
        try:
            req = JSONRPCRequest(id=10, method="tools/list")
            resp_user = await server.handle_request(req, user={"role": "user"})
            names_user = [t["name"] for t in resp_user.result["tools"]]
            assert not any(n.startswith("external__") for n in names_user)
            resp_admin = await server.handle_request(req, user={"role": "admin"})
            names_admin = [t["name"] for t in resp_admin.result["tools"]]
            assert "external__hidden-demo__echo" in names_admin
        finally:
            registry.remove_discovered_tool("external__hidden-demo__echo")


class TestSSEMessageDelivery:
    async def test_sse_message_delivered_to_session_queue(self, http_client):
        from moa_gateway.routes.mcp import _sse_transport

        session_id = _sse_transport.create_session()
        try:
            resp = await http_client.post(
                "/v1/mcp/sse/messages",
                json={
                    "session_id": session_id,
                    "message": {"jsonrpc": "2.0", "id": 77, "method": "ping"},
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["id"] == 77
            assert body["result"] == {}
            # The response was really pushed onto the SSE session queue.
            queue = _sse_transport.get_session(session_id)
            assert queue is not None and queue.qsize() == 1
            pushed = queue.get_nowait()
            assert pushed.id == 77 and pushed.result == {}
        finally:
            _sse_transport.remove_session(session_id)

    async def test_sse_message_tools_call_flows_to_stream(self, http_client):
        """A tools/call posted for a session lands on that session's stream."""
        from moa_gateway.routes.mcp import _sse_transport

        session_id = _sse_transport.create_session()
        try:
            resp = await http_client.post(
                "/v1/mcp/sse/messages",
                json={
                    "session_id": session_id,
                    "message": {
                        "jsonrpc": "2.0",
                        "id": 78,
                        "method": "tools/call",
                        "params": {"name": "moa_list_models", "arguments": {}},
                    },
                },
                headers=AUTH,
            )
            assert resp.status_code == 200, resp.text
            queue = _sse_transport.get_session(session_id)
            pushed = queue.get_nowait()
            assert pushed.id == 78
            assert pushed.error is None
            assert pushed.result["isError"] is False
        finally:
            _sse_transport.remove_session(session_id)

    async def test_sse_message_unknown_session_404(self, http_client):
        resp = await http_client.post(
            "/v1/mcp/sse/messages",
            json={
                "session_id": "no-such-session",
                "message": {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            },
            headers=AUTH,
        )
        assert resp.status_code == 404

    async def test_sse_message_malformed_jsonrpc(self, http_client):
        from moa_gateway.routes.mcp import _sse_transport

        session_id = _sse_transport.create_session()
        try:
            resp = await http_client.post(
                "/v1/mcp/sse/messages",
                json={"session_id": session_id, "message": {"id": 5}},  # no method
                headers=AUTH,
            )
            assert resp.status_code == 200
            assert resp.json()["error"]["code"] == -32600
        finally:
            _sse_transport.remove_session(session_id)

    async def test_sse_message_missing_fields_400(self, http_client):
        resp = await http_client.post(
            "/v1/mcp/sse/messages", json={"message": {"method": "ping"}}, headers=AUTH
        )
        assert resp.status_code == 400
        resp2 = await http_client.post(
            "/v1/mcp/sse/messages", json={"session_id": "x"}, headers=AUTH
        )
        assert resp2.status_code == 400

    async def test_sse_event_stream_yields_delivered_message(self):
        """Full chain: deliver -> session queue -> SSE event text."""
        from moa_gateway.mcp.protocol import JSONRPCRequest
        from moa_gateway.routes.mcp import _sse_transport

        session_id = _sse_transport.create_session()
        try:
            request = JSONRPCRequest(id=55, method="ping")
            response = await _sse_transport.handle_message(session_id, request, user=None)
            assert response is not None and response.result == {}
            events = []
            async for event in _sse_transport.event_stream(
                session_id, keepalive_interval=0.2
            ):
                events.append(event)
                if len(events) == 2:  # endpoint event + message event
                    break
            assert events[0].startswith("event: endpoint")
            assert "event: message" in events[1]
            assert '"id":55' in events[1] or '"id": 55' in events[1]
        finally:
            _sse_transport.remove_session(session_id)


# =====================================================================
# 5. Standalone mcp_server.py RBAC (real local HTTP fake gateway)
# =====================================================================
ADMIN_TOKEN = "fake-admin-token-001"
USER_TOKEN = "fake-user-token-002"


class _FakeGatewayHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/auth/me":
            auth = self.headers.get("Authorization", "")
            if auth == f"Bearer {ADMIN_TOKEN}":
                self._json(200, {"sub": "admin", "role": "admin", "aud": "moa-webui"})
            else:
                self._json(401, {"detail": "Invalid or expired token"})
        elif self.path == "/api/endpoints":
            self._json(200, {"endpoints": []})
        else:
            self._json(404, {"detail": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)
        if self.path == "/api/auth/login":
            self._json(200, {"token": ADMIN_TOKEN})
        elif self.path == "/api/endpoints":
            self._json(200, {"ok": True, "endpoint_id": "x"})
        elif self.path.startswith("/v1/capability/"):
            self._json(200, {"ok": True})
        elif self.path == "/v1/chat/completions":
            self._json(
                200,
                {"choices": [{"message": {"content": "hi"}}], "usage": {"total_tokens": 1}},
            )
        else:
            self._json(404, {"detail": "not found"})


@pytest.fixture
def fake_gateway(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGatewayHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"

    import moa_gateway.mcp_server as mcp_server_mod

    monkeypatch.setattr(mcp_server_mod, "GATEWAY_URL", url)
    mcp_server_mod.reset_rbac_cache()
    yield url
    server.shutdown()
    server.server_close()
    mcp_server_mod.reset_rbac_cache()


class TestStandaloneServerRBAC:
    async def test_admin_token_allows_dangerous_tool(self, fake_gateway, monkeypatch):
        import moa_gateway.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "GATEWAY_TOKEN", ADMIN_TOKEN)
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)
        resp = await mcp_server_mod.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "endpoint_upsert",
                    "arguments": {
                        "endpoint_id": "e1",
                        "provider": "openai",
                        "model": "gpt-4o-mini",
                        "api_base": "https://api.openai.com/v1",
                    },
                },
            }
        )
        assert resp.get("error") is None, resp
        assert resp["result"]["endpoint_id"] == "e1"

    async def test_non_admin_token_gets_403(self, fake_gateway, monkeypatch):
        import moa_gateway.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "GATEWAY_TOKEN", USER_TOKEN)
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)
        resp = await mcp_server_mod.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "secret_scan", "arguments": {"path": "."}},
            }
        )
        assert "error" in resp and resp["error"] is not None
        assert resp["error"]["code"] == -32003
        assert "403" in resp["error"]["message"]
        assert "admin" in resp["error"]["message"]

    async def test_no_token_fail_closed(self, fake_gateway, monkeypatch):
        import moa_gateway.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "GATEWAY_TOKEN", "")
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)
        resp = await mcp_server_mod.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "endpoint_upsert", "arguments": {"endpoint_id": "z"}},
            }
        )
        assert resp["error"] is not None
        assert "403" in resp["error"]["message"]

    async def test_unreachable_gateway_fail_closed(self, monkeypatch):
        import moa_gateway.mcp_server as mcp_server_mod

        # Point at a dead port; the RBAC check must deny, never allow.
        monkeypatch.setattr(mcp_server_mod, "GATEWAY_URL", "http://127.0.0.1:1")
        monkeypatch.setattr(mcp_server_mod, "GATEWAY_TOKEN", "whatever")
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)
        mcp_server_mod.reset_rbac_cache()
        resp = await mcp_server_mod.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "secret_scan", "arguments": {"path": "."}},
            }
        )
        assert resp["error"] is not None
        assert "403" in resp["error"]["message"]

    async def test_non_dangerous_tool_not_gated(self, fake_gateway, monkeypatch):
        """endpoint_list is not dangerous -> no RBAC gate; it works even with
        a token the gateway would reject on /api/auth/me."""
        import moa_gateway.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "GATEWAY_TOKEN", USER_TOKEN)
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)
        resp = await mcp_server_mod.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "endpoint_list", "arguments": {}},
            }
        )
        assert resp.get("error") is None, resp
        assert "content" in resp["result"]

    async def test_check_tool_rbac_helper(self, fake_gateway, monkeypatch):
        import moa_gateway.mcp_server as mcp_server_mod

        monkeypatch.setattr(mcp_server_mod, "GATEWAY_TOKEN", ADMIN_TOKEN)
        monkeypatch.delenv("MOA_ADMIN_PASSWORD", raising=False)
        allowed, role = await mcp_server_mod.check_tool_rbac("endpoint_upsert")
        assert allowed is True and role == "admin"
        allowed, _ = await mcp_server_mod.check_tool_rbac("chat")
        assert allowed is True  # non-dangerous short-circuits
        assert "endpoint_upsert" in mcp_server_mod.DANGEROUS_TOOLS
        assert "secret_scan" in mcp_server_mod.DANGEROUS_TOOLS
