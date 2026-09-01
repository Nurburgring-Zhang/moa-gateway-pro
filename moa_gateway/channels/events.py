"""Standardized channel events for the IM channel layer (M8).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/adapters/base.rb`` — the standardized inbound
  message event hash every adapter must emit (platform / chat_id / user_id /
  text / message_id / timestamp / chat_type / raw).

Adapted into a typed dataclass with the gateway's canonical field names:
``{channel, session_id, user, text, ts}`` (session_id is filled by the
SessionRouter after receipt).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

CHAT_DIRECT = "direct"
CHAT_GROUP = "group"


@dataclass
class InboundEvent:
    """One normalized inbound message from any IM platform."""

    channel: str                     # platform id: telegram / feishu / dingtalk / wecom / discord
    chat_id: str                     # platform conversation id (chat/channel/group)
    user: str                        # platform sender id
    text: str                        # normalized message text
    ts: float = field(default_factory=time.time)
    message_id: str = ""             # platform message id (for dedup / replies)
    chat_type: str = CHAT_DIRECT     # "direct" | "group"
    mentioned: bool = True           # group messages: was the bot addressed?
    session_id: str = ""             # filled by SessionRouter
    raw: dict[str, Any] = field(default_factory=dict)
    unsupported: bool = False        # media-only / unsupported msgtype marker

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "session_id": self.session_id,
            "user": self.user,
            "chat_id": self.chat_id,
            "text": self.text,
            "ts": self.ts,
            "message_id": self.message_id,
            "chat_type": self.chat_type,
            "mentioned": self.mentioned,
            "unsupported": self.unsupported,
        }


@dataclass
class SendResult:
    """Outcome of an outbound send."""

    ok: bool
    message_id: str = ""
    error: str = ""
    chunks: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message_id": self.message_id,
            "error": self.error,
            "chunks": self.chunks,
        }
