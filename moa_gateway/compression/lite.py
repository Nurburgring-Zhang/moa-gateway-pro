"""Lite compression engine — the five deterministic whitespace/dedup passes.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/lite.ts``:

1. ``whitespace``      — collapse 3+ newlines and trailing blanks.
2. ``system-dedup``    — drop duplicate system prompts (first 200-char key).
3. ``tool-compress``   — truncate >2000-char tool results at a word boundary.
4. ``redundant-remove``— drop consecutive same-role identical messages.
5. ``image-placeholder``— replace base64 images with ``[image: fmt]`` markers
   ONLY when the target model does not support vision.
"""

from __future__ import annotations

import re
from typing import Any

#: Same hard cap as OmniRoute ``compressToolResults``.
MAX_TOOL_LENGTH = 2000
TOOL_TRUNCATION_LOOKBACK = 80

Message = dict[str, Any]

# Common vision-model families (gateway-side list; mirrors the OmniRoute gate:
# only strip images when the caller explicitly says the model cannot see).
_VISION_HINTS = (
    "vision",
    "-v",
    "vl",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "claude",
    "gemini",
    "qwen-vl",
    "glm-4v",
    "pixtral",
    "llava",
)


def model_supports_vision(model: str | None) -> bool:
    if not model:
        return False
    lowered = model.lower()
    return any(hint in lowered for hint in _VISION_HINTS)


def _normalize_whitespace(content: str) -> str:
    if not content:
        return ""
    content = re.sub(r"\n{3,}", "\n\n", content)
    return re.sub(r"[ \t]+$", "", content, flags=re.MULTILINE)


def collapse_whitespace(
    messages: list[Message], preserve_system_prompt: bool = True
) -> tuple[list[Message], bool]:
    applied = False
    out: list[Message] = []
    for msg in messages:
        if preserve_system_prompt and msg.get("role") == "system":
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, str):
            out.append(msg)
            continue
        normalized = _normalize_whitespace(content)
        if normalized != content:
            applied = True
            msg = {**msg, "content": normalized}
        out.append(msg)
    return out, applied


def dedup_system_prompt(
    messages: list[Message], preserve_system_prompt: bool = True
) -> tuple[list[Message], bool]:
    if preserve_system_prompt:
        return messages, False
    seen: set[str] = set()
    applied = False
    out: list[Message] = []
    for msg in messages:
        if msg.get("role") != "system" or not isinstance(msg.get("content"), str):
            out.append(msg)
            continue
        key = msg["content"].strip()[:200]
        if key in seen:
            applied = True
            continue
        seen.add(key)
        out.append(msg)
    return out, applied


def _is_word_char(char: str | None) -> bool:
    return char is not None and not char.isspace()


def _back_off_to_word_boundary(content: str, cut_index: int) -> int:
    """Adjust a hard cut to the nearest whitespace within the lookback window."""
    if cut_index >= len(content):
        return len(content)
    on_boundary = not _is_word_char(content[cut_index - 1] if cut_index > 0 else None) or (
        cut_index < len(content) and not _is_word_char(content[cut_index])
    )
    if on_boundary:
        return cut_index

    window_start = max(0, cut_index - TOOL_TRUNCATION_LOOKBACK)
    for i in range(cut_index, window_start, -1):
        if not _is_word_char(content[i - 1]):
            return i - 1

    window_end = min(len(content), cut_index + TOOL_TRUNCATION_LOOKBACK)
    for i in range(cut_index, window_end):
        if not _is_word_char(content[i]):
            return i

    return cut_index


def compress_tool_results(messages: list[Message]) -> tuple[list[Message], bool]:
    applied = False
    out: list[Message] = []
    for msg in messages:
        content = msg.get("content")
        if msg.get("role") != "tool" or not isinstance(content, str):
            out.append(msg)
            continue
        if len(content) <= MAX_TOOL_LENGTH:
            out.append(msg)
            continue
        applied = True
        cut = _back_off_to_word_boundary(content, MAX_TOOL_LENGTH)
        out.append({**msg, "content": content[:cut] + "\n...[truncated]"})
    return out, applied


def remove_redundant_content(
    messages: list[Message], preserve_system_prompt: bool = True
) -> tuple[list[Message], bool]:
    applied = False
    out: list[Message] = []
    for i, msg in enumerate(messages):
        if preserve_system_prompt and msg.get("role") == "system":
            out.append(msg)
            continue
        content = msg.get("content")
        content_str = content if isinstance(content, str) else str(content)
        if i > 0:
            prev = messages[i - 1]
            prev_content = prev.get("content")
            if (
                prev.get("role") == msg.get("role")
                and isinstance(prev_content, str)
                and prev_content == content_str
            ):
                applied = True
                continue
        out.append(msg)
    return out, applied


def replace_image_urls(
    messages: list[Message], supports_vision: bool | None = None
) -> tuple[list[Message], bool]:
    if supports_vision is not False:
        return messages, False
    applied = False
    out: list[Message] = []
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue
        new_content = []
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "image_url"
                and isinstance(part.get("image_url"), dict)
                and isinstance(part["image_url"].get("url"), str)
            ):
                url = part["image_url"]["url"]
                if url.startswith("data:image/"):
                    applied = True
                    fmt = url[len("data:image/"): url.find(";")] or "unknown"
                    new_content.append({"type": "text", "text": f"[image: {fmt}]"})
                    continue
            new_content.append(part)
        out.append({**msg, "content": new_content})
    return out, applied


def apply_lite_compression(
    messages: list[Message],
    preserve_system_prompt: bool = True,
    compress_results: bool = True,
    supports_vision: bool | None = None,
) -> tuple[list[Message], list[str]]:
    """Run all five lite passes in order; return (messages, techniques)."""
    current = messages
    techniques: list[str] = []

    current, applied = collapse_whitespace(current, preserve_system_prompt)
    if applied:
        techniques.append("whitespace")

    current, applied = dedup_system_prompt(current, preserve_system_prompt)
    if applied:
        techniques.append("system-dedup")

    if compress_results:
        current, applied = compress_tool_results(current)
        if applied:
            techniques.append("tool-compress")

    current, applied = remove_redundant_content(current, preserve_system_prompt)
    if applied:
        techniques.append("redundant-remove")

    current, applied = replace_image_urls(current, supports_vision)
    if applied:
        techniques.append("image-placeholder")

    return current, techniques
