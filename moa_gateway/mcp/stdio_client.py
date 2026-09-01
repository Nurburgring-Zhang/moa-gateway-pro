"""Stdio MCP client — launches external MCP servers as child processes.

Speaks JSON-RPC 2.0 over the child's stdin/stdout following the MCP stdio
transport (newline-delimited JSON). The reader side also understands
``Content-Length:`` framed messages so servers using LSP-style framing
interoperate as well.

Security model (enforced before any subprocess is spawned):

- **Command allowlist** — only executables whose basename appears in
  ``allowed_commands`` (settings: ``mcp.stdio_allowed_commands``, default
  ``python/python3/node/npx/uvx``) may be launched. Everything else is
  refused with :class:`StdioMCPError`.
- **Secret stripping** — the gateway's own secret environment variables
  (``MOA_ADMIN_PASSWORD`` / ``MOA_GATEWAY_KEY`` / ``MOA_JWT_SECRET``) are
  removed from the child environment by default. Explicit ``env`` overrides
  supplied by the operator are applied *after* stripping, so anything passed
  through is a deliberate choice.

Lifecycle guarantees:

- stdout is drained by a dedicated reader thread, so slow/chatty servers can
  never block the event loop.
- Startup failure (command not found, permission denied), server crash and
  request timeouts all surface as real :class:`StdioMCPError` exceptions —
  nothing is swallowed.
- :meth:`StdioMCPClient.terminate` closes stdin, waits a grace period, kills
  the process if needed and always reaps it (no zombies). An ``atexit`` hook
  reaps any client that was never explicitly shut down.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import subprocess
import threading
from collections import deque
from collections.abc import Iterable
from typing import Any

from .protocol import ToolDefinition

logger = logging.getLogger(__name__)

#: Gateway secret env vars never inherited by spawned MCP servers.
GATEWAY_SECRET_ENV_VARS: tuple[str, ...] = (
    "MOA_ADMIN_PASSWORD",
    "MOA_GATEWAY_KEY",
    "MOA_JWT_SECRET",
)

#: Fallback allowlist used when no explicit list is supplied. Mirrors the
#: default of ``settings.mcp.stdio_allowed_commands``.
DEFAULT_STDIO_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {"python", "python3", "node", "npx", "uvx"}
)

_CLIENT_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "moa-gateway-stdio-client", "version": "1.0.0"}

# Executable suffixes stripped before matching against the allowlist.
_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".sh", ".py")


class StdioMCPError(RuntimeError):
    """Real failure from the stdio launcher: bad command, spawn error,
    crash, timeout or protocol violation. Never a silent placeholder."""


def is_command_allowed(command: str, allowed_commands: Iterable[str]) -> bool:
    """Return True if ``command``'s executable basename is on the allowlist.

    Handles full paths (``/usr/bin/python3``, ``C:\\Python\\python.exe``)
    and common executable suffixes (``python.exe`` -> ``python``).
    """
    if not command or not isinstance(command, str):
        return False
    base = os.path.basename(command.replace("\\", "/")).strip().lower()
    if not base:
        return False
    candidates = {base}
    lowered_suffixes = _EXE_SUFFIXES
    for suffix in lowered_suffixes:
        if base.endswith(suffix):
            candidates.add(base[: -len(suffix)])
            break
    allowed = {str(a).strip().lower() for a in allowed_commands if str(a).strip()}
    return bool(candidates & allowed)


def build_child_env(
    env_overrides: dict[str, Any] | None = None,
    strip_secrets: bool = True,
) -> dict[str, str]:
    """Build the environment for a spawned MCP server.

    Starts from the gateway's own environment, strips gateway secrets when
    ``strip_secrets`` is true, then applies operator-supplied overrides.
    """
    child_env = os.environ.copy()
    if strip_secrets:
        for var in GATEWAY_SECRET_ENV_VARS:
            child_env.pop(var, None)
    for key, value in (env_overrides or {}).items():
        child_env[str(key)] = "" if value is None else str(value)
    return child_env


# Track live clients so the atexit hook can reap orphaned subprocesses.
_LIVE_CLIENTS: set["StdioMCPClient"] = set()


def _reap_all_at_exit() -> None:  # pragma: no cover - interpreter shutdown
    for client in list(_LIVE_CLIENTS):
        try:
            client.terminate()
        except Exception:
            pass


atexit.register(_reap_all_at_exit)


class StdioMCPClient:
    """JSON-RPC 2.0 client driving an external MCP server subprocess.

    Usage::

        client = StdioMCPClient("python", ["my_server.py"])
        await client.connect()          # spawn + initialize handshake
        tools = await client.list_tools()
        result = await client.call_tool("echo", {"text": "hi"})
        await client.shutdown()         # terminates + reaps the child
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, Any] | None = None,
        cwd: str | None = None,
        *,
        name: str = "",
        timeout: float = 30.0,
        shutdown_timeout: float = 5.0,
        allowed_commands: Iterable[str] | None = None,
        strip_secret_env: bool = True,
    ):
        if not command or not str(command).strip():
            raise StdioMCPError("stdio MCP server: command must be a non-empty string")
        self.command = str(command)
        self.args = [str(a) for a in (args or [])]
        self.env_overrides = dict(env or {})
        self.cwd = cwd
        self.name = name or self.command
        self.timeout = float(timeout)
        self.shutdown_timeout = float(shutdown_timeout)
        self._allowed_commands = (
            frozenset(str(c).strip().lower() for c in allowed_commands if str(c).strip())
            if allowed_commands is not None
            else DEFAULT_STDIO_ALLOWED_COMMANDS
        )
        self._strip_secret_env = bool(strip_secret_env)

        # Fail fast: refuse non-allowlisted commands before anything spawns.
        if not is_command_allowed(self.command, self._allowed_commands):
            raise StdioMCPError(
                f"stdio MCP server refused: command '{self.command}' is not in the "
                f"allowed command list ({sorted(self._allowed_commands)}). "
                "Extend settings.mcp.stdio_allowed_commands to permit it."
            )

        self._proc: subprocess.Popen | None = None
        self._final_exit_code: int | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._stderr_lines: deque[str] = deque(maxlen=50)
        self._initialized = False
        self._eof = False
        self._eof_error: str = ""
        self._server_info: dict[str, Any] = {}
        self._tools: list[ToolDefinition] = []

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        """True while the child process is alive."""
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def connected(self) -> bool:
        """True once the initialize handshake succeeded and the child lives."""
        return self._initialized and self.running

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def exit_code(self) -> int | None:
        if self._proc is not None:
            return self._proc.poll()
        return self._final_exit_code

    @property
    def server_info(self) -> dict[str, Any]:
        return dict(self._server_info)

    @property
    def tools(self) -> list[ToolDefinition]:
        return list(self._tools)

    @property
    def stderr_tail(self) -> str:
        """Last captured stderr lines (diagnostics for crash reporting)."""
        return "\n".join(self._stderr_lines)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Spawn the child process and its reader threads.

        Raises StdioMCPError with the real OS error when the executable
        cannot be launched (not found, permission denied, bad cwd, ...).
        """
        if self._proc is not None:
            raise StdioMCPError(f"stdio MCP server '{self.name}' already started")
        argv = [self.command, *self.args]
        child_env = build_child_env(self.env_overrides, self._strip_secret_env)
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - argv list, never a shell
                argv,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=self.cwd,
                env=child_env,
                shell=False,
            )
        except FileNotFoundError as e:
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' failed to start: command not found: "
                f"{self.command}"
            ) from e
        except (PermissionError, NotADirectoryError, OSError) as e:
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' failed to start: {type(e).__name__}: {e}"
            ) from e

        self._loop = asyncio.get_running_loop()
        _LIVE_CLIENTS.add(self)
        self._reader_thread = threading.Thread(
            target=self._reader_loop, name=f"stdio-mcp-reader-{self.name}", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name=f"stdio-mcp-stderr-{self.name}", daemon=True
        )
        self._reader_thread.start()
        self._stderr_thread.start()
        logger.info(
            "stdio MCP server '%s' spawned: pid=%s argv=%s", self.name, self._proc.pid, argv
        )

    async def initialize(self) -> dict[str, Any]:
        """Perform the MCP initialize handshake (+ initialized notification)."""
        if self._proc is None:
            raise StdioMCPError(f"stdio MCP server '{self.name}' not started")
        result = await self._request(
            "initialize",
            {
                "protocolVersion": _CLIENT_PROTOCOL_VERSION,
                "clientInfo": _CLIENT_INFO,
                "capabilities": {},
            },
        )
        if not isinstance(result, dict):
            await self.shutdown()
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' returned invalid initialize result"
            )
        self._server_info = result.get("serverInfo", {}) or {}
        self._initialized = True
        # Notification (no response expected) — completes the MCP handshake.
        await self._notify("notifications/initialized", {})
        return result

    async def connect(self) -> dict[str, Any]:
        """Spawn the server and complete the initialize handshake.

        On any failure the child is terminated so no orphan process remains.
        """
        await self.start()
        try:
            return await self.initialize()
        except Exception:
            await self.shutdown()
            raise

    async def shutdown(self) -> None:
        """Terminate the child (graceful first, then kill) and reap it."""
        _LIVE_CLIENTS.discard(self)
        self._initialized = False
        proc = self._proc
        if proc is None:
            return
        # Close stdin so a well-behaved server sees EOF and exits on its own.
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=self.shutdown_timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                logger.warning(
                    "stdio MCP server '%s' (pid=%s) did not exit after kill",
                    self.name,
                    proc.pid,
                )
        # Reap/join helper threads (bounded waits — they are daemon threads).
        for t in (self._reader_thread, self._stderr_thread):
            if t is not None and t.is_alive():
                t.join(timeout=1.0)
        self._final_exit_code = proc.returncode
        self._fail_pending(self._exit_error_message())
        self._proc = None

    async def disconnect(self) -> None:
        """Alias kept for interface parity with the HTTP MCPClient."""
        await self.shutdown()

    def terminate(self) -> None:
        """Synchronous variant of :meth:`shutdown` (safe from any thread).

        Used by the registry on unregister so the subprocess is really
        stopped even when no event loop is available.
        """
        _LIVE_CLIENTS.discard(self)
        self._initialized = False
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except OSError:
            pass
        try:
            proc.wait(timeout=self.shutdown_timeout)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=self.shutdown_timeout)
            except subprocess.TimeoutExpired:
                pass
        self._final_exit_code = proc.returncode
        self._fail_pending(self._exit_error_message())
        self._proc = None

    # ------------------------------------------------------------------
    # MCP operations
    # ------------------------------------------------------------------
    async def list_tools(self) -> list[ToolDefinition]:
        """Discover tools from the server (real ``tools/list`` round-trip)."""
        result = await self._request("tools/list", {})
        raw_tools = (result or {}).get("tools", []) if isinstance(result, dict) else []
        tools: list[ToolDefinition] = []
        for entry in raw_tools:
            if not isinstance(entry, dict) or not entry.get("name"):
                continue
            tools.append(
                ToolDefinition(
                    name=str(entry["name"]),
                    description=str(entry.get("description", "")),
                    inputSchema=entry.get("inputSchema", {}) or {},
                )
            )
        self._tools = tools
        return list(tools)

    async def call_tool(self, name: str, arguments: dict | None = None) -> dict[str, Any]:
        """Invoke a tool on the server and return its result dict.

        JSON-RPC level errors are returned as ``{"error": {...}}`` (same
        contract as :class:`MCPClient`); transport failures raise.
        """
        response = await self._request_raw(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        if response.get("error") is not None:
            return {"error": response["error"]}
        result = response.get("result")
        return result if isinstance(result, dict) else {"content": [], "raw": result}

    async def ping(self) -> bool:
        """True if the server answers a ping within the timeout."""
        try:
            await self._request("ping", {})
            return True
        except StdioMCPError:
            return False

    # ------------------------------------------------------------------
    # JSON-RPC plumbing
    # ------------------------------------------------------------------
    def _alloc_id(self) -> int:
        with self._id_lock:
            self._next_id += 1
            return self._next_id

    async def _request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> Any:
        response = await self._request_raw(method, params, timeout)
        if response.get("error") is not None:
            err = response["error"]
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' JSON-RPC error on {method}: "
                f"[{err.get('code')}] {err.get('message')}"
            )
        return response.get("result")

    async def _request_raw(
        self, method: str, params: dict[str, Any] | None = None, timeout: float | None = None
    ) -> dict[str, Any]:
        """Send a request and wait for the matching response (or error)."""
        proc = self._proc
        if proc is None:
            raise StdioMCPError(f"stdio MCP server '{self.name}' is not running")
        if proc.poll() is not None:
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' exited (code={proc.poll()}) before "
                f"'{method}' could be sent. stderr: {self.stderr_tail or '<empty>'}"
            )
        loop = self._loop or asyncio.get_running_loop()
        req_id = self._alloc_id()
        future: asyncio.Future = loop.create_future()
        self._pending[req_id] = future
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
            ensure_ascii=False,
        )
        try:
            await loop.run_in_executor(None, self._write_line, payload)
        except StdioMCPError:
            self._pending.pop(req_id, None)
            raise
        try:
            return await asyncio.wait_for(future, timeout=timeout or self.timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' timed out after "
                f"{timeout or self.timeout}s waiting for '{method}' response"
            ) from None

    async def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        if self._proc is None or self._proc.poll() is not None:
            raise StdioMCPError(
                f"stdio MCP server '{self.name}' is not running (cannot send {method})"
            )
        loop = self._loop or asyncio.get_running_loop()
        payload = json.dumps(
            {"jsonrpc": "2.0", "method": method, "params": params or {}}, ensure_ascii=False
        )
        await loop.run_in_executor(None, self._write_line, payload)

    def _write_line(self, payload: str) -> None:
        """Blocking write of one newline-delimited JSON message to stdin."""
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise StdioMCPError(
                f"stdio MCP server '{self.name}': stdin unavailable (server stopped)"
            )
        data = (payload + "\n").encode("utf-8")
        with self._write_lock:
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                code = proc.poll()
                raise StdioMCPError(
                    f"stdio MCP server '{self.name}' crashed (exit code={code}); "
                    f"write failed: {e}. stderr: {self.stderr_tail or '<empty>'}"
                ) from e

    # ------------------------------------------------------------------
    # Background reader threads
    # ------------------------------------------------------------------
    def _reader_loop(self) -> None:
        """Drain stdout forever: newline-delimited JSON or Content-Length frames."""
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        stdout = proc.stdout
        try:
            while True:
                raw_line = stdout.readline()
                if not raw_line:  # EOF — child closed stdout / exited
                    break
                text = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                if not text.strip():
                    continue
                if text.strip().lower().startswith("content-length:"):
                    payload = self._read_content_length_frame(stdout, text)
                    if payload is None:
                        break
                    self._dispatch(payload)
                else:
                    self._dispatch(text)
        except Exception as e:  # reader must never die silently
            logger.warning("stdio MCP reader '%s' error: %s", self.name, e)
        finally:
            self._eof = True
            self._eof_error = self._exit_error_message()
            self._fail_pending(self._eof_error)

    def _read_content_length_frame(self, stdout, header_line: str) -> str | None:
        """Parse an LSP-style ``Content-Length`` frame; return the payload."""
        try:
            length = int(header_line.split(":", 1)[1].strip())
        except (ValueError, IndexError):
            return None
        # Consume remaining headers until the blank line.
        while True:
            header = stdout.readline()
            if not header:
                return None
            if header.decode("utf-8", errors="replace").strip() == "":
                break
        body = b""
        while len(body) < length:
            chunk = stdout.read(length - len(body))
            if not chunk:
                return None
            body += chunk
        return body.decode("utf-8", errors="replace")

    def _dispatch(self, text: str) -> None:
        """Route one parsed message: resolve a pending future or answer a
        server-initiated request with Method-not-found."""
        try:
            message = json.loads(text)
        except json.JSONDecodeError:
            logger.debug("stdio MCP server '%s' sent non-JSON line: %.200s", self.name, text)
            return
        if not isinstance(message, dict):
            return
        msg_id = message.get("id")
        if "method" in message:
            if msg_id is not None:
                # Server-to-client request: we implement none — answer honestly.
                self._answer_server_request(msg_id)
            return  # notifications are informational only
        if msg_id is None:
            return
        future = self._pending.pop(msg_id, None) if isinstance(msg_id, int) else None
        if future is None:
            # Some servers echo ids as strings; try a tolerant match.
            for key in list(self._pending):
                if str(key) == str(msg_id):
                    future = self._pending.pop(key)
                    break
        if future is None or future.done():
            return
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(future.set_result, message)

    def _answer_server_request(self, req_id: Any) -> None:
        try:
            payload = json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": "client does not serve requests"},
                }
            )
            self._write_line(payload)
        except StdioMCPError:
            pass

    def _stderr_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            for raw in proc.stderr:
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line:
                    self._stderr_lines.append(line)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Failure propagation helpers
    # ------------------------------------------------------------------
    def _exit_error_message(self) -> str:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            # stdout closed but the child may still be exiting — give it a
            # short grace period so we can report the real exit code.
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                return f"stdio MCP server '{self.name}' closed its output stream"
        code = proc.poll() if proc is not None else "n/a"
        tail = self.stderr_tail
        base = f"stdio MCP server '{self.name}' exited (code={code})"
        return f"{base}. stderr: {tail}" if tail else base

    def _fail_pending(self, message: str) -> None:
        """Reject every in-flight request with the real failure reason."""
        pending = list(self._pending.items())
        self._pending.clear()
        loop = self._loop

        def _reject() -> None:
            for _req_id, future in pending:
                if not future.done():
                    future.set_exception(StdioMCPError(message))

        if loop is not None and not loop.is_closed() and loop.is_running():
            loop.call_soon_threadsafe(_reject)
        else:  # no live loop (e.g. sync terminate after tests) — drop futures
            for _req_id, future in pending:
                if not future.done():
                    future.cancel()

    # Convenience used by registry code expecting MCPClient-like attributes.
    async def call_tool_raw(self, name: str, arguments: dict | None = None) -> dict[str, Any]:
        return await self.call_tool(name, arguments)
