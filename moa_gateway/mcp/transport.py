"""MCP transport layer - SSE and HTTP transport implementations."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator, Optional

from .protocol import JSONRPCRequest, JSONRPCResponse
from .server import MCPServer

logger = logging.getLogger(__name__)


class SSETransport:
    """Server-Sent Events transport for MCP.

    Manages SSE sessions and routes messages between clients and the MCP server.
    """

    def __init__(self, server: MCPServer):
        self.server = server
        self._sessions: dict[str, asyncio.Queue] = {}

    def create_session(self) -> str:
        """Create a new SSE session, return session_id."""
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = asyncio.Queue()
        logger.info("SSE session created: %s", session_id)
        return session_id

    def remove_session(self, session_id: str) -> None:
        """Remove a session when client disconnects."""
        self._sessions.pop(session_id, None)
        logger.info("SSE session removed: %s", session_id)

    async def handle_message(
        self, session_id: str, request: JSONRPCRequest, user: Optional[dict] = None
    ) -> Optional[JSONRPCResponse]:
        """Process a message for a session and push response to the session queue."""
        response = await self.server.handle_request(request, user)
        if response and session_id in self._sessions:
            await self._sessions[session_id].put(response)
        return response

    async def event_stream(
        self, session_id: str, keepalive_interval: float = 15.0
    ) -> AsyncGenerator[str, None]:
        """Generate SSE events for a session."""
        # Send endpoint event
        yield f"event: endpoint\ndata: {json.dumps({'sessionId': session_id})}\n\n"

        queue = self._sessions.get(session_id)
        if not queue:
            return

        while True:
            try:
                response = await asyncio.wait_for(queue.get(), timeout=keepalive_interval)
                data = response.model_dump_json()
                yield f"event: message\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
            except asyncio.CancelledError:
                break


class HTTPTransport:
    """Simple HTTP transport - one request, one response."""

    def __init__(self, server: MCPServer):
        self.server = server

    async def handle(
        self, body: dict[str, Any], user: Optional[dict] = None
    ) -> dict[str, Any]:
        """Handle a single HTTP JSON-RPC request."""
        request = JSONRPCRequest(**body)
        response = await self.server.handle_request(request, user)
        if response is None:
            return {"jsonrpc": "2.0", "id": body.get("id"), "result": "ok"}
        return response.model_dump()
