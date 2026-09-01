"""Aggressive engine — tool-result compression + aging + strong caveman.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/aggressive.ts`` (deterministic subset):

1. Tool/function results go through the RTK line-filter pipeline.
2. Lite whitespace / consecutive-duplicate passes run over everything.
3. Caveman at ``full`` intensity condenses every older message; only the
   most recent user message (the live intent) and, by default, the system
   prompt are left untouched.
"""

from __future__ import annotations

from typing import Any

from .caveman import CavemanConfig, caveman_compress_messages
from .lite import apply_lite_compression
from .rtk import rtk_compress_messages

COMPRESSED_MARKER = "[COMPRESSED:"


def aggressive_compress_messages(
    messages: list[dict[str, Any]],
    preserve_system_prompt: bool = True,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """Returns (messages, techniques_used, rules_applied)."""
    techniques: list[str] = []
    rules: list[str] = []

    # Step 1: RTK over tool results (intensity aggressive = tightest caps).
    current, rtk_techniques, rtk_rules, _ = rtk_compress_messages(
        messages, intensity="aggressive", apply_to_cli_content=False
    )
    if rtk_techniques:
        techniques.extend(rtk_techniques)
        rules.extend(rtk_rules)

    # Step 2: lite normalization passes.
    current, lite_techniques = apply_lite_compression(
        current, preserve_system_prompt=preserve_system_prompt
    )
    techniques.extend(f"lite:{t}" for t in lite_techniques)

    # Step 3: caveman "full" on everything except the live user turn.
    last_user_index = -1
    for i in range(len(current) - 1, -1, -1):
        if current[i].get("role") == "user":
            last_user_index = i
            break

    caveman_cfg = CavemanConfig(
        intensity="full",
        compress_roles=("user", "assistant", "tool"),
        min_message_length=40,
    )
    result = caveman_compress_messages(current, caveman_cfg)
    # The live user turn must survive byte-for-byte.
    if last_user_index >= 0:
        result.messages[last_user_index] = current[last_user_index]
    if result.rules_applied:
        techniques.append("caveman-full")
        rules.extend(f"caveman:{r}" for r in result.rules_applied)

    return result.messages, list(dict.fromkeys(techniques)), list(dict.fromkeys(rules))
