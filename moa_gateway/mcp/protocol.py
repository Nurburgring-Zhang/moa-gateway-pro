"""MCP protocol data models - JSON-RPC 2.0 compliant."""
from __future__ import annotations

from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field


class MCPMethod(str, Enum):
    """Standard MCP methods."""

    INITIALIZE = "initialize"
    INITIALIZED = "notifications/initialized"
    PING = "ping"
    TOOLS_LIST = "tools/list"
    TOOLS_CALL = "tools/call"
    RESOURCES_LIST = "resources/list"
    RESOURCES_READ = "resources/read"
    PROMPTS_LIST = "prompts/list"
    PROMPTS_GET = "prompts/get"


class JSONRPCRequest(BaseModel):
    """JSON-RPC 2.0 request."""

    jsonrpc: str = "2.0"
    id: Optional[str | int] = None
    method: str
    params: Optional[dict[str, Any]] = None


class JSONRPCResponse(BaseModel):
    """JSON-RPC 2.0 response."""

    jsonrpc: str = "2.0"
    id: Optional[str | int] = None
    result: Optional[Any] = None
    error: Optional[dict[str, Any]] = None


class ToolDefinition(BaseModel):
    """MCP tool definition."""

    name: str
    description: str
    inputSchema: dict[str, Any] = Field(default_factory=dict)


class ToolCallRequest(BaseModel):
    """Request to call a tool."""

    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """Result from a tool call."""

    content: List[dict[str, Any]]
    isError: bool = False
