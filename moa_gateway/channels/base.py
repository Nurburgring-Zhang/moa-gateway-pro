"""Channel adapter abstraction + registry (M8).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/adapters/base.rb`` — the ``Adapters.register`` /
  ``Adapters.find`` registry and the ``Base`` adapter contract
  (``platform_id``, ``start``, ``stop``, ``send_text``, ``validate_config``);
- the credential-validation-then-status discipline from ``channel_config.rb``.

Adaptations for the gateway:
- async httpx transport (OpenClacky uses net/http);
- an explicit status state machine — ``unconfigured -> configured -> running``
  (+ ``error:<reason>``) — so credential-less deployments honestly report
  ``unconfigured`` instead of pretending to be online;
- constructor dependency-injection of the httpx client so protocol tests can
  drive real adapter code against ``httpx.MockTransport`` (controlled test
  double boundary: transport layer only).
"""

from __future__ import annotations

import abc
import asyncio
import json
import logging
import os
import time
from typing import Any, Awaitable, Callable

import httpx

from .events import InboundEvent, SendResult

logger = logging.getLogger(__name__)

#: status constants (honest state machine — an adapter only reports states it is actually in)
STATUS_UNCONFIGURED = "unconfigured"
STATUS_CONFIGURED = "configured"
STATUS_RUNNING = "running"

OnEvent = Callable[[InboundEvent], Awaitable[None]]

#: default request timeout for outbound platform API calls (seconds)
DEFAULT_HTTP_TIMEOUT = 15.0


def split_message(text: str, limit: int) -> list[str]:
    """Chunk ``text`` into pieces of at most ``limit`` characters.

    Port of OpenClacky telegram adapter ``split_message``: cut at the best
    available boundary — blank line (``\\n\\n``), then newline, then space,
    then hard cut — so platform message limits never break mid-word when
    avoidable.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    text = text or ""
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        window = rest[:limit]
        cut = -1
        for boundary in ("\n\n", "\n", " ", "\t"):
            idx = window.rfind(boundary)
            if idx > limit // 4:  # never produce degenerate tiny chunks
                cut = idx + len(boundary)
                break
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:]
    if rest:
        chunks.append(rest)
    return chunks


class AdapterRegistry:
    """Registry of adapter classes keyed by platform id.

    Mirrors OpenClacky ``Adapters.register(:telegram, Adapter)`` /
    ``Adapters.find(:telegram)``.
    """

    def __init__(self):
        self._classes: dict[str, type[BaseChannelAdapter]] = {}

    def register(self, platform_id: str, cls: type["BaseChannelAdapter"]) -> None:
        if platform_id in self._classes:
            logger.debug("channels: adapter '%s' re-registered", platform_id)
        self._classes[platform_id] = cls

    def find(self, platform_id: str) -> type["BaseChannelAdapter"] | None:
        return self._classes.get(platform_id)

    def platform_ids(self) -> list[str]:
        return sorted(self._classes)

    def build_all(self, env: dict[str, str] | None = None, env_prefix: str | None = None) -> dict[str, "BaseChannelAdapter"]:
        """Instantiate every registered adapter from environment credentials.

        ``env_prefix`` defaults to ``settings.channels.env_prefix`` ("MOA_").
        Adapters without credentials still get built — they simply report
        status ``unconfigured`` (an honest state — never reported as online).
        """
        if env_prefix is None:
            from ..config import get_settings

            env_prefix = get_settings().channels.env_prefix
        if env is None:
            env = dict(os.environ)
        out: dict[str, BaseChannelAdapter] = {}
        for pid, cls in sorted(self._classes.items()):
            out[pid] = cls.from_env(env, env_prefix)
        return out


#: module-level registry, same shape as OpenClacky's Adapters module
registry = AdapterRegistry()


class BaseChannelAdapter(abc.ABC):
    """Contract every IM platform adapter implements."""

    #: platform identifier, e.g. "telegram"
    platform_id: str = ""
    #: env-var suffixes (after the MOA_ prefix) that configure this adapter
    env_keys: tuple[str, ...] = ()
    #: per-platform outbound chunk limit (chars)
    max_send_chars: int = 4000

    def __init__(
        self,
        config: dict[str, str],
        http_client: httpx.AsyncClient | None = None,
    ):
        self.config = {k: (v or "") for k, v in config.items()}
        self._owned_client = http_client is None
        self._http = http_client
        self._state = STATUS_UNCONFIGURED
        self._error = ""
        self._running = False
        self._on_event: OnEvent | None = None
        self._task: asyncio.Task | None = None
        self._last_activity = 0.0

    # ---------- construction ----------

    @classmethod
    def from_env(cls, env: dict[str, str], prefix: str) -> "BaseChannelAdapter":
        """Read this adapter's credentials from env using the MOA_ prefix."""
        config: dict[str, str] = {}
        for suffix in cls.env_keys:
            key = f"{prefix}{suffix}"
            config[suffix.lower()] = (env.get(key) or "").strip()
        adapter = cls(config)
        adapter.validate_and_set_state()
        return adapter

    # ---------- state machine ----------

    def validate_and_set_state(self) -> None:
        errors = self.validate_config(self.config)
        if errors:
            self._state = STATUS_UNCONFIGURED
        elif self._state == STATUS_UNCONFIGURED:
            self._state = STATUS_CONFIGURED

    def status(self) -> dict[str, Any]:
        """Real adapter state — never reports online without credentials."""
        state = self._state
        if state == STATUS_UNCONFIGURED and self._error:
            state = f"unconfigured (error: {self._error})"
        elif state == STATUS_RUNNING and self._error:
            state = f"running (last error: {self._error})"
        return {
            "platform": self.platform_id,
            "state": state,
            "configured": self._state in (STATUS_CONFIGURED, STATUS_RUNNING),
            "running": self._running,
            "last_activity": self._last_activity,
            "required_env": [f"MOA_{k}" for k in self.env_keys],
            "missing_env": self.validate_config(self.config),
        }

    # ---------- contract ----------

    @abc.abstractmethod
    def validate_config(self, config: dict[str, str]) -> list[str]:
        """Return missing/invalid credential field descriptions (empty = ok)."""

    @abc.abstractmethod
    async def send_text(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        """Deliver outbound text through the platform's real API."""

    async def start(self, on_event: OnEvent) -> None:
        """Begin receiving messages (polling platforms override ``_run_loop``).

        Webhook-only platforms keep the base behavior: mark running and wait
        for ``handle_webhook`` calls from the HTTP route.
        """
        if self._state == STATUS_UNCONFIGURED:
            raise RuntimeError(f"{self.platform_id}: cannot start, unconfigured")
        self._on_event = on_event
        self._running = True
        self._state = STATUS_RUNNING
        loop_task = getattr(self, "_run_loop", None)
        if loop_task is not None:
            self._task = asyncio.create_task(self._guarded_loop())

    async def _guarded_loop(self) -> None:
        assert self._task is not None
        try:
            await self._run_loop()  # type: ignore[attr-defined]
        except asyncio.CancelledError:
            pass
        except Exception as e:  # keep adapter honest about failures
            self._error = str(e)
            logger.error("channels: %s poll loop crashed: %s", self.platform_id, e)
        finally:
            self._running = False
            if self._state == STATUS_RUNNING:
                self._state = STATUS_CONFIGURED

    def set_event_handler(self, on_event: OnEvent | None) -> None:
        """Wire the inbound event callback without starting the receive loop
        (used by webhook-driven platforms)."""
        self._on_event = on_event

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._state == STATUS_RUNNING:
            self._state = STATUS_CONFIGURED

    async def handle_webhook(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """Process one inbound webhook POST.

        Returns the HTTP response body the route should send back (platform
        challenge/ack semantics). Default: platforms without webhook inbound
        reject.
        """
        return {"error": f"{self.platform_id} does not accept webhook inbound"}

    async def handle_raw(
        self, raw_body: bytes, query: dict[str, str], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        """Webhook entrypoint with the raw request body.

        Default: JSON-decode and delegate to ``handle_webhook``. Adapters that
        must verify signatures over the exact body or parse non-JSON payloads
        (Feishu signature, WeCom encrypted XML) override this method.
        Returns ``(http_status, response_body)``.
        """
        try:
            payload = json.loads(raw_body) if raw_body.strip() else {}
        except json.JSONDecodeError:
            return 400, {"error": "invalid JSON payload"}
        if not isinstance(payload, dict):
            return 400, {"error": "payload must be a JSON object"}
        result = await self.handle_webhook(payload, headers)
        return 200, result

    # ---------- shared helpers ----------

    def http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT)
        return self._http

    async def aclose(self) -> None:
        if self._owned_client and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def emit(self, event: InboundEvent) -> None:
        self._last_activity = time.time()
        if self._on_event is not None:
            await self._on_event(event)

    async def send_chunked(self, chat_id: str, text: str, reply_to: str = "") -> SendResult:
        """Split long text at the platform limit and send each chunk for real."""
        max_chars = self.max_send_chars
        from ..config import get_settings

        cap = get_settings().channels.max_message_chars
        if cap > 0:
            max_chars = min(max_chars, cap) if max_chars else cap
        chunks = split_message(text, max_chars)
        last = SendResult(ok=True)
        for i, chunk in enumerate(chunks):
            last = await self.send_text(
                chat_id, chunk, reply_to=reply_to if i == 0 else ""
            )
            if not last.ok:
                last.chunks = i + 1
                return last
        last.chunks = len(chunks)
        return last
