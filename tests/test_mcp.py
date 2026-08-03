"""Tests for MCP gateway module - protocol, RBAC, guardrails, server."""
from __future__ import annotations

import pytest

from moa_gateway.mcp import (
    JSONRPCRequest,
    JSONRPCResponse,
    MCPServer,
    ToolDefinition,
    ToolRegistry,
    GuardrailEngine,
)
from moa_gateway.mcp.builtin_tools import register_builtin_tools


# ==================== Fixtures ====================


@pytest.fixture
def registry():
    """Fresh tool registry with builtin tools registered."""
    reg = ToolRegistry()
    register_builtin_tools(reg)
    return reg


@pytest.fixture
def empty_registry():
    """Empty tool registry for unit tests."""
    return ToolRegistry()


@pytest.fixture
def guardrails():
    """Fresh guardrail engine."""
    return GuardrailEngine()


@pytest.fixture
def mcp_server(registry, guardrails):
    """MCP server with builtin tools."""
    return MCPServer(registry=registry, guardrails=guardrails)


# ==================== Protocol Tests ====================


class TestProtocol:
    """Test MCP protocol data models."""

    def test_jsonrpc_request_defaults(self):
        req = JSONRPCRequest(method="tools/list")
        assert req.jsonrpc == "2.0"
        assert req.method == "tools/list"
        assert req.id is None
        assert req.params is None

    def test_jsonrpc_request_with_params(self):
        req = JSONRPCRequest(
            id=42,
            method="tools/call",
            params={"name": "test_tool", "arguments": {"x": 1}},
        )
        assert req.id == 42
        assert req.params["name"] == "test_tool"

    def test_tool_definition_schema(self):
        tool = ToolDefinition(
            name="my_tool",
            description="A test tool",
            inputSchema={"type": "object", "properties": {"q": {"type": "string"}}},
        )
        dumped = tool.model_dump()
        assert dumped["name"] == "my_tool"
        assert dumped["inputSchema"]["type"] == "object"


# ==================== Registry Tests ====================


class TestRegistry:
    """Test tool registry and RBAC filtering."""

    def test_register_and_list(self, empty_registry):
        async def handler():
            return "ok"

        tool = ToolDefinition(name="test", description="test tool")
        empty_registry.register(tool, handler, allowed_roles={"admin"})
        assert empty_registry.tool_count == 1
        assert empty_registry.get_tool("test") is not None

    def test_list_tools_no_role_shows_all(self, registry):
        tools = registry.list_tools(user_role=None)
        assert len(tools) == 9  # 9 builtin tools

    def test_list_tools_admin_sees_all(self, registry):
        tools = registry.list_tools(user_role="admin")
        assert len(tools) == 9

    def test_list_tools_user_sees_subset(self, registry):
        tools = registry.list_tools(user_role="user")
        names = [t.name for t in tools]
        assert "moa_list_models" in names
        assert "moa_check_quota" in names
        assert "moa_route_preview" not in names  # operator+ only

    def test_list_tools_readonly_sees_nothing(self, registry):
        tools = registry.list_tools(user_role="readonly")
        assert len(tools) == 0

    def test_check_access(self, registry):
        assert registry.check_access("moa_list_models", "user") is True
        assert registry.check_access("moa_route_preview", "user") is False
        assert registry.check_access("moa_route_preview", "admin") is True

    def test_unregister(self, empty_registry):
        async def handler():
            return "ok"

        tool = ToolDefinition(name="temp", description="temp")
        empty_registry.register(tool, handler)
        assert empty_registry.unregister("temp") is True
        assert empty_registry.tool_count == 0
        assert empty_registry.unregister("nonexistent") is False


# ==================== Server Tests ====================


class TestMCPServer:
    """Test MCP server request handling."""

    @pytest.mark.asyncio
    async def test_initialize(self, mcp_server):
        req = JSONRPCRequest(id=1, method="initialize", params={
            "protocolVersion": "2024-11-05",
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        resp = await mcp_server.handle_request(req)
        assert resp.id == 1
        assert resp.error is None
        assert resp.result["protocolVersion"] == "2024-11-05"
        assert "tools" in resp.result["capabilities"]

    @pytest.mark.asyncio
    async def test_ping(self, mcp_server):
        req = JSONRPCRequest(id=2, method="ping")
        resp = await mcp_server.handle_request(req)
        assert resp.id == 2
        assert resp.result == {}
        assert resp.error is None

    @pytest.mark.asyncio
    async def test_tools_list_as_admin(self, mcp_server):
        req = JSONRPCRequest(id=3, method="tools/list")
        user = {"role": "admin", "username": "admin"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is None
        tools = resp.result["tools"]
        assert len(tools) == 9
        names = [t["name"] for t in tools]
        assert "moa_list_models" in names

    @pytest.mark.asyncio
    async def test_tools_list_as_readonly(self, mcp_server):
        req = JSONRPCRequest(id=4, method="tools/list")
        user = {"role": "readonly", "username": "viewer"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is None
        assert len(resp.result["tools"]) == 0

    @pytest.mark.asyncio
    async def test_tool_call_success(self, mcp_server):
        req = JSONRPCRequest(id=5, method="tools/call", params={
            "name": "moa_list_models",
            "arguments": {},
        })
        user = {"role": "admin", "username": "admin"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is None
        assert resp.result is not None
        assert resp.result["isError"] is False

    @pytest.mark.asyncio
    async def test_tool_call_rbac_denied(self, mcp_server):
        """Readonly user cannot call any tool."""
        req = JSONRPCRequest(id=6, method="tools/call", params={
            "name": "moa_list_models",
            "arguments": {},
        })
        user = {"role": "readonly", "username": "viewer"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is not None
        assert "Permission denied" in resp.error["message"]

    @pytest.mark.asyncio
    async def test_tool_call_operator_restricted(self, mcp_server):
        """User role cannot call operator-only tools."""
        req = JSONRPCRequest(id=7, method="tools/call", params={
            "name": "moa_route_preview",
            "arguments": {"prompt": "test"},
        })
        user = {"role": "user", "username": "regular"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is not None
        assert "Permission denied" in resp.error["message"]

    @pytest.mark.asyncio
    async def test_tool_call_unknown_tool(self, mcp_server):
        req = JSONRPCRequest(id=8, method="tools/call", params={
            "name": "nonexistent_tool",
            "arguments": {},
        })
        user = {"role": "admin", "username": "admin"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is not None
        assert "Unknown tool" in resp.error["message"]

    @pytest.mark.asyncio
    async def test_method_not_found(self, mcp_server):
        req = JSONRPCRequest(id=9, method="unknown/method")
        resp = await mcp_server.handle_request(req)
        assert resp.error is not None
        assert resp.error["code"] == -32601

    @pytest.mark.asyncio
    async def test_notifications_initialized(self, mcp_server):
        """notifications/initialized should return None (no response)."""
        req = JSONRPCRequest(method="notifications/initialized")
        resp = await mcp_server.handle_request(req)
        assert resp is None


# ==================== Guardrails Tests ====================


class TestGuardrails:
    """Test tool call guardrails."""

    @pytest.mark.asyncio
    async def test_safe_input_passes(self, guardrails):
        result = await guardrails.pre_call("test", {"query": "hello world"})
        assert result == {"query": "hello world"}

    @pytest.mark.asyncio
    async def test_dangerous_rm_rf_blocked(self, guardrails):
        with pytest.raises(ValueError, match="Blocked dangerous pattern"):
            await guardrails.pre_call("test", {"cmd": "rm -rf /"})

    @pytest.mark.asyncio
    async def test_dangerous_drop_table_blocked(self, guardrails):
        with pytest.raises(ValueError, match="Blocked dangerous pattern"):
            await guardrails.pre_call("test", {"sql": "DROP TABLE users"})

    @pytest.mark.asyncio
    async def test_dangerous_delete_all_blocked(self, guardrails):
        with pytest.raises(ValueError, match="Blocked dangerous pattern"):
            await guardrails.pre_call("test", {"sql": "DELETE FROM orders WHERE 1=1"})

    @pytest.mark.asyncio
    async def test_nested_dict_checked(self, guardrails):
        with pytest.raises(ValueError, match="Blocked dangerous pattern"):
            await guardrails.pre_call("test", {"nested": {"cmd": "rm -rf /tmp"}})

    @pytest.mark.asyncio
    async def test_post_call_passthrough(self, guardrails):
        result = await guardrails.post_call("test", {"data": "safe"})
        assert result == {"data": "safe"}

    @pytest.mark.asyncio
    async def test_custom_pre_hook(self, guardrails):
        async def add_timestamp(tool_name, arguments, user):
            arguments["_hooked"] = True
            return arguments

        guardrails.add_pre_hook(add_timestamp)
        result = await guardrails.pre_call("test", {"x": 1})
        assert result["_hooked"] is True

    @pytest.mark.asyncio
    async def test_custom_post_hook(self, guardrails):
        async def redact(tool_name, result, user):
            if isinstance(result, dict):
                result["_redacted"] = True
            return result

        guardrails.add_post_hook(redact)
        result = await guardrails.post_call("test", {"secret": "value"})
        assert result["_redacted"] is True


# ==================== Integration Tests ====================


class TestBuiltinTools:
    """Test built-in tool execution end-to-end."""

    @pytest.mark.asyncio
    async def test_list_models_returns_data(self, mcp_server):
        req = JSONRPCRequest(id=10, method="tools/call", params={
            "name": "moa_list_models",
            "arguments": {"provider": "openai"},
        })
        user = {"role": "admin", "username": "admin"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is None
        # Result should contain model data (may be empty if no openai provider configured)
        import json
        content = resp.result["content"][0]["text"]
        data = json.loads(content)
        assert "models" in data
        assert "total" in data
        assert all(m["provider"] == "openai" for m in data["models"])

    @pytest.mark.asyncio
    async def test_check_quota_returns_data(self, mcp_server):
        req = JSONRPCRequest(id=11, method="tools/call", params={
            "name": "moa_check_quota",
            "arguments": {},
        })
        user = {"role": "user", "username": "testuser"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is None
        import json
        content = resp.result["content"][0]["text"]
        data = json.loads(content)
        assert "quota" in data

    @pytest.mark.asyncio
    async def test_route_preview_operator(self, mcp_server):
        req = JSONRPCRequest(id=12, method="tools/call", params={
            "name": "moa_route_preview",
            "arguments": {"prompt": "Hello, can you help me with coding?"},
        })
        user = {"role": "operator", "username": "ops"}
        resp = await mcp_server.handle_request(req, user=user)
        assert resp.error is None
        import json
        content = resp.result["content"][0]["text"]
        data = json.loads(content)
        assert "recommended_tier" in data
        assert "prompt" in data
