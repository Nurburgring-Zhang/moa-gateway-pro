"""IM channel layer (M8) — OpenClacky-style multi-platform channels.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License);
per-module attribution headers document exactly what was ported from where.

Capabilities:
- ``BaseChannelAdapter`` abstraction + adapter registry (base.py)
- standardized inbound events {channel, session_id, user, text, ts} (events.py)
- real protocol adapters: Telegram (long-poll + webhook), Feishu (token cache +
  im/v1/messages + event signature), DingTalk (robot callback signing +
  sessionWebhook reply), WeCom (message push + official callback crypto),
  Discord (REST + webhook + ed25519 interaction verification)
- session routing with persisted bindings + DEDUP_WINDOW (session_router.py)
- UIController writeback through the gateway's real chat pipeline
  (gateway_bridge.py)

Iron rule honored: with no platform credentials every adapter reports status
``unconfigured`` and no channel is enabled — zero impact on existing behavior.
"""

from .base import (
    STATUS_CONFIGURED,
    STATUS_RUNNING,
    STATUS_UNCONFIGURED,
    AdapterRegistry,
    BaseChannelAdapter,
    registry,
    split_message,
)
from .events import CHAT_DIRECT, CHAT_GROUP, InboundEvent, SendResult
from .gateway_bridge import ChannelGatewayBridge, sanitize_outbound
from .session_router import BINDING_MODES, DEDUP_WINDOW, SessionRouter, channel_key

# populate the registry with all bundled adapters
from . import adapters as _adapters  # noqa: E402,F401

__all__ = [
    "STATUS_UNCONFIGURED",
    "STATUS_CONFIGURED",
    "STATUS_RUNNING",
    "AdapterRegistry",
    "BaseChannelAdapter",
    "registry",
    "split_message",
    "InboundEvent",
    "SendResult",
    "CHAT_DIRECT",
    "CHAT_GROUP",
    "SessionRouter",
    "channel_key",
    "BINDING_MODES",
    "DEDUP_WINDOW",
    "ChannelGatewayBridge",
    "sanitize_outbound",
]
