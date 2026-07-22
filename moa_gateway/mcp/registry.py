"""Tool registry - manages available tools and their access roles."""
from __future__ import annotations

import logging
from typing import Callable, Optional, Set

from .protocol import ToolDefinition

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry for MCP tools with role-based access control."""

    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        self._handlers: dict[str, Callable] = {}
        self._tool_roles: dict[str, Set[str]] = {}  # tool_name -> allowed roles

    def register(
        self,
        tool: ToolDefinition,
        handler: Callable,
        allowed_roles: Optional[Set[str]] = None,
    ) -> None:
        """Register a tool with its handler and optional role restrictions."""
        self._tools[tool.name] = tool
        self._handlers[tool.name] = handler
        if allowed_roles:
            self._tool_roles[tool.name] = allowed_roles
        logger.info("Registered MCP tool: %s (roles=%s)", tool.name, allowed_roles or "all")

    def unregister(self, name: str) -> bool:
        """Remove a tool from the registry."""
        if name in self._tools:
            del self._tools[name]
            self._handlers.pop(name, None)
            self._tool_roles.pop(name, None)
            return True
        return False

    def list_tools(self, user_role: Optional[str] = None) -> list[ToolDefinition]:
        """List tools accessible to a given role. None = all tools."""
        if not user_role:
            return list(self._tools.values())
        return [
            t
            for name, t in self._tools.items()
            if name not in self._tool_roles or user_role in self._tool_roles[name]
        ]

    def get_handler(self, name: str) -> Optional[Callable]:
        """Get the handler callable for a tool."""
        return self._handlers.get(name)

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        """Get tool definition by name."""
        return self._tools.get(name)

    def check_access(self, tool_name: str, user_role: str) -> bool:
        """Check if a role has access to a tool."""
        if tool_name not in self._tool_roles:
            return True  # No restriction = open to all
        return user_role in self._tool_roles[tool_name]

    @property
    def tool_count(self) -> int:
        return len(self._tools)
