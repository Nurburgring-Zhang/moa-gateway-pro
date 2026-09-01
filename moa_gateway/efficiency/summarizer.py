"""Extractive session summarizer — real compression primitive.

New module for the moa_gateway_pro v4.1.0 integration, built by COMPOSING two
real primitives that already ship in this gateway:

- ``moa_gateway.capability.distillation`` (extract_ideas / curate_ideas):
  sentence-level extraction with keyword-normalised dedup and
  frequency x importance ranking.
- ``moa_gateway.capability.context_clean`` (from_openai_format /
  clean_messages): structural cleanup of the rebuilt history.

The hierarchical level behaviour (level 1 detailed → level 4 ultra-minimal)
is ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT),
source: ``lib/clacky/agent/message_compressor_helper.rb`` —
``generate_hierarchical_summary`` / ``extract_key_information`` /
``generate_levelN_summary``.

No LLM call is required: this is a deterministic extractive algorithm, which
makes compression callable from the idle scheduler with zero endpoint
dependency and fully reproducible in tests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..capability.distillation import (
    STOPWORDS,
    WORD_RE,
    curate_ideas,
    extract_ideas,
)
from .tokens import estimate_content_tokens

__all__ = [
    "SummaryResult",
    "KeyInformation",
    "extract_key_information",
    "extract_topics",
    "summarize_messages",
    "TOPIC_MAX_ITEMS",
]

# Maximum topic phrases reported per compression (OpenClacky asks the LLM for
# 3-6; the extractive version caps at the same upper bound).
TOPIC_MAX_ITEMS = 6

# Minimum keywords a sentence needs to be topic-worthy.
_TOPIC_MIN_KEYWORDS = 2


@dataclass
class KeyInformation:
    """Structural facts extracted from a message batch (port of
    ``extract_key_information`` — counts, tools used, decisions)."""

    user_msgs: int = 0
    assistant_msgs: int = 0
    tool_msgs: int = 0
    tools_used: list[str] = field(default_factory=list)
    total_chars: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_msgs": self.user_msgs,
            "assistant_msgs": self.assistant_msgs,
            "tool_msgs": self.tool_msgs,
            "tools_used": list(self.tools_used),
            "total_chars": self.total_chars,
        }


@dataclass
class SummaryResult:
    """Outcome of one extractive summarization pass."""

    text: str
    topics: str | None
    level: int
    sentences_used: int
    sentences_available: int
    estimated_tokens: int
    key_info: KeyInformation

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "topics": self.topics,
            "level": self.level,
            "sentences_used": self.sentences_used,
            "sentences_available": self.sentences_available,
            "estimated_tokens": self.estimated_tokens,
            "key_info": self.key_info.to_dict(),
        }


def _message_text(msg: dict[str, Any]) -> str:
    """Flatten a message's content to plain text (string or block arrays)."""
    content = msg.get("content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(f"[{block.get('type') or 'content'}]")
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def _tool_call_names(msg: dict[str, Any]) -> list[str]:
    names: list[str] = []
    tool_calls = msg.get("tool_calls")
    if not isinstance(tool_calls, list):
        return names
    for tc in tool_calls:
        if not isinstance(tc, dict):
            continue
        func = tc.get("function") or {}
        name = func.get("name") or tc.get("name")
        if name:
            names.append(str(name))
    return names


def extract_key_information(messages: list[dict[str, Any]]) -> KeyInformation:
    """Port of OpenClacky's ``extract_key_information`` (subset that is
    meaningful without a filesystem tool layer): role counts, tool names,
    and total character volume."""
    info = KeyInformation()
    seen_tools: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "user":
            info.user_msgs += 1
        elif role == "assistant":
            info.assistant_msgs += 1
        elif role == "tool":
            info.tool_msgs += 1
        for name in _tool_call_names(msg):
            if name not in seen_tools:
                seen_tools.append(name)
        info.total_chars += len(_message_text(msg))
    info.tools_used = seen_tools
    return info


def extract_topics(messages: list[dict[str, Any]], max_items: int = TOPIC_MAX_ITEMS) -> str | None:
    """Deterministic topic phrase extraction: rank non-stopword keywords by
    frequency across the batch and emit the top ones as a comma-separated
    string (same shape as OpenClacky's ``<topics>`` output)."""
    freq: dict[str, int] = {}
    order: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") == "tool":
            continue
        text = _message_text(msg)
        for token in WORD_RE.findall(text):
            tok = token.lower()
            if tok in STOPWORDS or len(tok) < 2:
                continue
            if tok not in freq:
                order.append(tok)
            freq[tok] = freq.get(tok, 0) + 1

    ranked = sorted(
        order,
        key=lambda kw: (-freq[kw], kw),
    )
    topics = [kw for kw in ranked if freq[kw] >= _TOPIC_MIN_KEYWORDS][:max_items]
    if not topics:
        topics = ranked[: max(1, max_items // 2)]
    if not topics:
        return None
    return ", ".join(topics)


def _transcript_text(messages: list[dict[str, Any]]) -> str:
    """Render messages into one transcript for sentence extraction."""
    lines: list[str] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        text = _message_text(msg).strip()
        if not text:
            continue
        role = msg.get("role", "?")
        if role == "tool":
            name = msg.get("name") or "tool"
            lines.append(f"[{name}] {text}")
        else:
            lines.append(text)
    return "\n".join(lines)


def _level_frame(level: int, info: KeyInformation) -> str:
    """Hierarchical header, port of generate_levelN_summary prefixes."""
    if level <= 1:
        return (
            f"Previous conversation summary ({info.user_msgs} user requests, "
            f"{info.assistant_msgs} responses, {info.tool_msgs} tool calls):"
        )
    if level == 2:
        return "Conversation summary:"
    if level == 3:
        return "Project progress:"
    return "Progress:"


def summarize_messages(
    messages: list[dict[str, Any]],
    target_tokens: int,
    level: int = 1,
) -> SummaryResult:
    """Compress *messages* into an extractive summary within *target_tokens*.

    Pipeline (all real computation):
    1. Flatten the batch into a transcript.
    2. ``extract_ideas`` (gateway distillation primitive): split sentences,
       drop fragments under 5 words, dedup by normalised keywords, score each
       sentence by length + keyword density.
    3. ``curate_ideas``: cross-"proposal" merge (here a single proposal) and
       frequency x importance ranking.
    4. Greedy budget pass: pick top-ranked sentences until the token budget
       (*target_tokens*, minus a header allowance) is exhausted. At least the
       single best sentence is kept (truncated if it alone busts the budget).
    5. Levels 3+ collapse to counts-only frames (port of the OpenClacky
       minimal / ultra-minimal tiers) so repeated re-compression keeps
       shrinking instead of re-summarising summaries.
    """
    info = extract_key_information(messages)
    topics = extract_topics(messages)

    transcript = _transcript_text(messages)
    ideas = extract_ideas(transcript, 0) if transcript.strip() else []
    curated = curate_ideas([ideas], keep_ratio=1.0)
    ranked = list(curated.kept_ideas) + list(curated.dropped_ideas)

    header = _level_frame(level, info)

    if level >= 3:
        # Minimal tiers: no sentence payload, only the structural frame.
        if level == 3:
            body = (
                f"{info.user_msgs} requests, {info.tool_msgs} tool calls. "
                + (f"Tools: {', '.join(info.tools_used[:5])}." if info.tools_used else "")
            ).strip()
        else:
            body = (
                f"{info.user_msgs} tasks, tools: "
                f"{', '.join(info.tools_used[-3:]) if info.tools_used else 'n/a'}"
            )
        text = f"{header}\n{body}".strip()
        return SummaryResult(
            text=text,
            topics=topics,
            level=level,
            sentences_used=0,
            sentences_available=len(ranked),
            estimated_tokens=estimate_content_tokens(text),
            key_info=info,
        )

    # Budget the body: reserve room for the header + tool inventory lines.
    header_tokens = estimate_content_tokens(header)
    budget = max(64, int(target_tokens) - header_tokens)

    chosen: list[str] = []
    used_tokens = 0
    for idea in ranked:
        sentence = idea.text.strip()
        if not sentence:
            continue
        cost = estimate_content_tokens(sentence)
        if used_tokens + cost <= budget:
            chosen.append(sentence)
            used_tokens += cost
            continue
        if not chosen:
            # Nothing fit yet: keep the best sentence, hard-truncated to fit.
            lo, hi = 0, len(sentence)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if estimate_content_tokens(sentence[:mid]) <= budget:
                    lo = mid
                else:
                    hi = mid - 1
            chosen.append(sentence[: max(lo, 1)] + ("..." if lo < len(sentence) else ""))
            used_tokens = estimate_content_tokens(chosen[0])
        break

    lines: list[str] = [header]
    if info.tools_used:
        lines.append("Tools: " + ", ".join(info.tools_used))
    if level >= 2:
        # Concise tier keeps at most 3 payload sentences.
        chosen = chosen[:3]
    lines.extend(f"- {s}" for s in chosen)
    lines.append("Continuing with recent conversation...")
    text = "\n".join(lines)

    return SummaryResult(
        text=text,
        topics=topics,
        level=level,
        sentences_used=len(chosen),
        sentences_available=len(ranked),
        estimated_tokens=estimate_content_tokens(text),
        key_info=info,
    )


def parse_topics_tag(content: str | None) -> str | None:
    """Parse a ``<topics>...</topics>`` tag out of LLM-produced compression
    output (port of ``MessageCompressor#parse_topics``). Used when the
    compression call is served by a real LLM instead of the extractive
    summarizer."""
    if not content:
        return None
    m = re.search(r"<topics>(.*?)</topics>", str(content), re.DOTALL)
    return m.group(1).strip() if m else None
