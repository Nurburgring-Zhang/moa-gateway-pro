"""Double cache-marker strategy (Anthropic prompt-caching semantics).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Source: ``lib/clacky/client.rb`` — ``apply_message_caching`` /
``add_cache_control_to_message`` / ``is_compression_instruction?``.

Why two markers (verbatim rationale from the OpenClacky source):

    Turn N   — marks messages[-2] and messages[-1]; the server caches the
               prefix up to [-1].
    Turn N+1 — messages[-2] is Turn N's last message (still marked) → cache
               READ hit; messages[-1] is the new message (marked) → cache
               WRITE for Turn N+2.

With only one marker, Turn N marks messages[-1]; in Turn N+1 that same message
is now [-2] and carries no marker, so the server sees a different prefix and
the cache misses.

Compression / side-channel instructions (``system_injected: true``) are skipped
as marker candidates — ephemeral injected messages must never anchor a cache
breakpoint.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = [
    "EPHEMERAL_CACHE_CONTROL",
    "MARKER_COUNT",
    "is_compression_instruction",
    "add_cache_control_to_message",
    "apply_cache_markers",
    "strip_cache_markers",
]

# Anthropic prompt-caching marker attached to the trailing breakpoint blocks.
EPHEMERAL_CACHE_CONTROL: dict[str, str] = {"type": "ephemeral"}

# Number of trailing breakpoints (OpenClacky double-marker strategy).
MARKER_COUNT = 2


def is_compression_instruction(message: Any) -> bool:
    """True for system-injected scaffolding messages (compression prompts,
    side-channel notes, compressed summaries, subagent instructions).

    Port of ``Client#is_compression_instruction?``: any message flagged
    ``system_injected`` is ephemeral and never receives a cache marker.
    """
    return isinstance(message, dict) and message.get("system_injected") is True


def add_cache_control_to_message(message: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *message* carrying an ephemeral ``cache_control`` marker.

    Port of ``Client#add_cache_control_to_message``:
    - ``str`` content is wrapped into a single text block that carries the
      marker.
    - ``list`` content gets the marker merged into its LAST block only (the
      breakpoint sits at the end of the message).
    - any other content shape is returned unchanged (still counts as a
      consumed candidate, matching the original).
    """
    content = message.get("content")
    marker = dict(EPHEMERAL_CACHE_CONTROL)

    if isinstance(content, str):
        new_content = [
            {"type": "text", "text": content, "cache_control": dict(marker)}
        ]
    elif isinstance(content, list):
        new_content = []
        last_idx = len(content) - 1
        for idx, block in enumerate(content):
            if idx == last_idx and isinstance(block, dict):
                merged = dict(block)
                merged["cache_control"] = dict(marker)
                new_content.append(merged)
            else:
                new_content.append(copy.deepcopy(block))
    else:
        # Non-textual content (None / exotic): nothing to anchor on.
        return copy.deepcopy(message)

    out = copy.deepcopy(message)
    out["content"] = new_content
    return out


def apply_cache_markers(
    messages: list[dict[str, Any]],
    enabled: bool = True,
    marker_count: int = MARKER_COUNT,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Apply the double-marker strategy to a message sequence.

    Returns ``(marked_messages, marked_indices)``. The input list is never
    mutated; marked messages are deep copies.

    - ``enabled=False`` (or the ``cache_markers`` config switch off) returns a
      deep copy of the input with no markers.
    - Candidate selection walks from the tail and skips system-injected
      messages, exactly like the OpenClacky implementation.
    """
    if not messages:
        return [], []
    if not enabled or marker_count <= 0:
        return copy.deepcopy(messages), []

    candidate_indices: list[int] = []
    for i in range(len(messages) - 1, -1, -1):
        if len(candidate_indices) >= marker_count:
            break
        if not is_compression_instruction(messages[i]):
            candidate_indices.append(i)

    marked_set = set(candidate_indices)
    out: list[dict[str, Any]] = []
    for idx, msg in enumerate(messages):
        if idx in marked_set:
            out.append(add_cache_control_to_message(msg))
        else:
            out.append(copy.deepcopy(msg))
    # Preserve chronological order of indices in the report.
    return out, sorted(candidate_indices)


def strip_cache_markers(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove ``cache_control`` keys again (e.g. for providers without
    prompt-caching support). Inverse of :func:`apply_cache_markers` for the
    marker field only; block structure for string content is unwrapped."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        content = msg.get("content")
        cleaned = copy.deepcopy(msg)
        if isinstance(content, list):
            new_blocks = []
            for block in content:
                if isinstance(block, dict):
                    nb = {k: v for k, v in block.items() if k != "cache_control"}
                    new_blocks.append(nb)
                else:
                    new_blocks.append(block)
            # Unwrap the single-text-block shape we create for string content.
            if (
                len(new_blocks) == 1
                and isinstance(new_blocks[0], dict)
                and new_blocks[0].get("type") == "text"
                and set(new_blocks[0].keys()) == {"type", "text"}
            ):
                cleaned["content"] = new_blocks[0]["text"]
            else:
                cleaned["content"] = new_blocks
        out.append(cleaned)
    return out
