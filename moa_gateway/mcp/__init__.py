"""MCP (Model Context Protocol) gateway module.

Provides:
- MCPServer: Expose tools via JSON-RPC 2.0
- MCPClient: Connect to external MCP servers
- ToolRegistry: Manage tools with role-based access
- GuardrailEngine: Pre/Post call safety hooks
"""
from .client import MCPClient
from .guardrails import GuardrailEngine
from .protocol import (
    JSONRPCRequest,
    JSONRPCResponse,
    MCPMethod,
    ToolCallRequest,
    ToolCallResult,
    ToolDefinition,
)
from .registry import ToolRegistry
from .server import MCPServer
from .stdio_client import StdioMCPClient, StdioMCPError

__all__ = [
    "JSONRPCRequest",
    "JSONRPCResponse",
    "MCPMethod",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolDefinition",
    "ToolRegistry",
    "GuardrailEngine",
    "MCPServer",
    "MCPClient",
    "StdioMCPClient",
    "StdioMCPError",
]
