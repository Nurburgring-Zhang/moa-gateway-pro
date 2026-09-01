"""Platform adapter registry population (M8).

Importing this module registers every bundled adapter with the shared
``registry`` (OpenClacky ``Adapters.register`` pattern):
telegram, feishu, dingtalk, wecom, discord.
"""

from ..base import registry
from .dingtalk import DingTalkAdapter, dingtalk_sign
from .discord import DiscordAdapter
from .feishu import FeishuAdapter
from .telegram import TelegramAdapter
from .wecom import WeComAdapter

__all__ = [
    "registry",
    "TelegramAdapter",
    "FeishuAdapter",
    "DingTalkAdapter",
    "WeComAdapter",
    "DiscordAdapter",
    "dingtalk_sign",
]
