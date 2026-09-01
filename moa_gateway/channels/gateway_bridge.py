"""Channel -> gateway -> channel writeback bridge (M8).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/channel_ui_controller.rb`` — the writeback
  controller: take an inbound channel message, run it through the agent,
  write the answer back to the channel; suppress output noise (e.g. strip
  ``file://`` links that are meaningless inside an IM client);
- ``lib/clacky/server/channel/channel_manager.rb`` — ``build_prompt_with_context``
  (prepend ``[Sender: user_id]``) and command/dedup gating before the agent.

The gateway processing step is the gateway's own real chat pipeline
(``ModelPool``) — the same call chain that serves ``/v1/chat/completions``.
With no configured provider keys the gateway's built-in credential-less
provider semantics apply (``providers.build_provider``); every call travels
the real provider pipeline.
"""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from typing import Any

from .base import BaseChannelAdapter
from .events import InboundEvent
from .session_router import SessionRouter

logger = logging.getLogger(__name__)

#: per-session conversation context window (messages kept for multi-turn)
CONTEXT_TURNS = 12

_FILE_LINK_RE = re.compile(r"^\s*.*file://\S+.*$", re.MULTILINE)


def sanitize_outbound(text: str) -> str:
    """Writeback noise suppression (channel_ui_controller.rb discipline).

    Strips ``file://`` links — local paths are meaningless inside an IM
    client — and collapses trailing whitespace.
    """
    cleaned = _FILE_LINK_RE.sub("", text or "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


class ChannelGatewayBridge:
    """Routes inbound channel events into gateway sessions and writes answers
    back to the originating channel."""

    def __init__(self, router: SessionRouter | None = None):
        self.router = router or SessionRouter()
        #: session_id -> bounded conversation history (multi-turn context)
        self._contexts: dict[str, deque[dict[str, str]]] = {}

    # ---------- main entrypoint ----------

    async def process_event(
        self, adapter: BaseChannelAdapter, event: InboundEvent
    ) -> dict[str, Any]:
        """Full inbound cycle: dedup -> routing -> gateway -> writeback."""
        key = f"{event.channel}:{event.chat_id}:{event.user}"

        if event.unsupported:
            return {"ignored": "unsupported message type"}
        if event.chat_type == "group" and not event.mentioned:
            return {"ignored": "group message not addressed to bot"}
        if self.router.is_duplicate(key, event):
            logger.info("channels: dropped duplicate message on %s", key)
            return {"ignored": "duplicate within dedup window"}

        session_id = self.router.resolve_session(event)
        event.session_id = session_id

        started = time.perf_counter()
        reply = await self.chat(event)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        result: dict[str, Any] = {
            "session_id": session_id,
            "reply_chars": len(reply),
            "pipeline_ms": round(elapsed_ms, 2),
            "sent": False,
        }
        if reply:
            send = await adapter.send_chunked(
                event.chat_id, reply, reply_to=event.message_id
            )
            result["sent"] = send.ok
            result["chunks"] = send.chunks
            if not send.ok:
                result["send_error"] = send.error
                logger.error(
                    "channels: writeback failed on %s: %s", event.channel, send.error
                )
        return result

    # ---------- gateway chat pipeline ----------

    def build_messages(self, event: InboundEvent) -> list[dict[str, str]]:
        """Messages for the pipeline: system context + per-session history +
        the new user turn with the OpenClacky sender prefix."""
        system = (
            f"You are the MOA gateway assistant answering through the "
            f"'{event.channel}' IM channel. Be concise and format answers for "
            f"a chat client (plain text or simple markdown)."
        )
        # OpenClacky build_prompt_with_context: "[Sender: user_id]"
        user_text = f"[Sender: {event.user}] {event.text}"
        history = self._contexts.get(event.session_id)
        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        return messages

    def _remember(self, session_id: str, user_text: str, reply: str) -> None:
        ctx = self._contexts.setdefault(session_id, deque(maxlen=CONTEXT_TURNS))
        ctx.append({"role": "user", "content": user_text})
        ctx.append({"role": "assistant", "content": reply})

    async def chat(self, event: InboundEvent) -> str:
        """Run the event through the gateway's real model pipeline."""
        from ..model_pool import ModelTier, get_model_pool

        pool = get_model_pool()
        tier = ModelTier.STANDARD
        ep = pool.select_one(tier)
        while ep is None and tier.rank > 0:
            tier = tier.previous()
            ep = pool.select_one(tier)
        if ep is None:
            logger.warning("channels: no model endpoint available for writeback")
            return ""

        user_text = f"[Sender: {event.user}] {event.text}"
        messages = self.build_messages(event)
        try:
            resp = await pool.call(ep.id, messages)
        except Exception as e:
            logger.error("channels: pipeline call failed (%s): %s", ep.id, e)
            return ""
        reply = sanitize_outbound(resp.content)
        if reply:
            self._remember(event.session_id, user_text, reply)
        logger.info(
            "channels: %s session=%s user=%s -> %d chars via %s",
            event.channel, event.session_id, event.user, len(reply), ep.id,
        )
        return reply
