"""Token estimation heuristics for the efficiency harness.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Source: ``lib/clacky/message_history.rb`` — ``estimate_tokens`` /
``estimate_message_tokens`` / ``estimate_content_tokens``.

Heuristic (faithful to the original): ASCII/code averages ~4 chars per token,
CJK / multibyte text averages ~1.5 chars per token. Every message carries ~4
tokens of role/formatting overhead, and each ``tool_calls`` entry adds the
estimated tokens of its function name + serialized arguments.
"""

from __future__ import annotations

import json
import math
from typing import Any

__all__ = [
    "estimate_content_tokens",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "MESSAGE_OVERHEAD_TOKENS",
]

# Per-message overhead (role + formatting), same constant as OpenClacky.
MESSAGE_OVERHEAD_TOKENS = 4

_ASCII_LO = ord(" ")
_ASCII_HI = ord("~")


def _count_ascii(text: str) -> int:
    return sum(1 for ch in text if _ASCII_LO <= ord(ch) <= _ASCII_HI)


def estimate_content_tokens(content: Any) -> int:
    """Estimate tokens for a message ``content`` value.

    Handles the three shapes the gateway passes around:
    - ``str``: plain text content.
    - ``list``: array of content blocks; each dict block contributes the
      estimate of its ``text`` field (non-text blocks such as images are
      counted as 0 here, matching OpenClacky which only sums ``block[:text]``).
    - anything else (``None`` etc.): 0.
    """
    if isinstance(content, str):
        ascii_chars = _count_ascii(content)
        multibyte_chars = len(content) - ascii_chars
        # ceil(ascii/4 + multibyte/1.5) — OpenClacky's exact formula.
        return math.ceil((ascii_chars / 4.0) + (multibyte_chars / 1.5))
    if isinstance(content, list):
        total_tokens = 0
        for block in content:
            if isinstance(block, dict):
                total_tokens += estimate_content_tokens(block.get("text"))
        return total_tokens
    return 0


def estimate_message_tokens(message: dict[str, Any]) -> int:
    """Estimate tokens of a single message including overhead and tool calls."""
    tokens = MESSAGE_OVERHEAD_TOKENS
    tokens += estimate_content_tokens(message.get("content"))

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function") or {}
            tokens += estimate_content_tokens(func.get("name"))
            args = func.get("arguments")
            if not isinstance(args, str):
                # OpenAI allows object arguments; serialize deterministically.
                try:
                    args = json.dumps(args, ensure_ascii=False, sort_keys=True)
                except (TypeError, ValueError):
                    args = str(args)
            tokens += estimate_content_tokens(args)
    return tokens


def estimate_messages_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate total tokens across a full message sequence."""
    return sum(estimate_message_tokens(m) for m in messages if isinstance(m, dict))
