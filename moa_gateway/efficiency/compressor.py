"""Insert-then-Compress session compression engine.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Sources:
- ``lib/clacky/agent/message_compressor.rb`` (COMPRESSION_PROMPT,
  build_compression_message, parse_topics, parse_compressed_result,
  rebuild_with_compression)
- ``lib/clacky/agent/message_compressor_helper.rb``
  (compress_messages_if_needed gates, get_recent_messages_with_tool_pairs,
  truncate_tool_result, calculate_target_recent_count, chunk MD archiving)

Insert-then-Compress strategy (verbatim from the original): instead of a
separate API call for compression, a compression instruction is inserted into
the current conversation flow as a user message. This reuses the existing
prefix cache (system prompt + tools + full history) so the compression itself
only pays for the new instruction, and only ONE cache rebuild happens after
compression instead of two.

The actual summarization is performed by ``moa_gateway.efficiency.summarizer``
(a real extractive pipeline built on the gateway's distillation primitive), so
the engine works without any LLM endpoint; callers that DO have an LLM can
pass its output through :func:`parse_compressed_result` unchanged.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .. import config as _cfg
from ..capability import context_clean
from .summarizer import extract_topics, parse_topics_tag, summarize_messages
from .tokens import estimate_messages_tokens

logger = logging.getLogger(__name__)

__all__ = [
    "COMPRESSION_PROMPT",
    "CompressionResult",
    "build_compression_message",
    "parse_topics",
    "parse_compressed_result",
    "rebuild_with_compression",
    "get_recent_messages_with_tool_pairs",
    "truncate_tool_result",
    "calculate_target_recent_count",
    "compression_needed",
    "idle_compression_needed",
    "resolve_archive_dir",
    "SessionCompressor",
]

# Port of MessageCompressor::COMPRESSION_PROMPT (kept in English — it is the
# exact prompt the upstream project sends to the model; translating it would
# change the cached-prefix semantics the port is meant to preserve).
COMPRESSION_PROMPT = """═══════════════════════════════════════════════════════════════
CRITICAL: TASK CHANGE - MEMORY COMPRESSION MODE
═══════════════════════════════════════════════════════════════
The conversation above has ENDED. You are now in MEMORY COMPRESSION MODE.

CRITICAL INSTRUCTIONS - READ CAREFULLY:

1. This is NOT a continuation of the conversation
2. DO NOT respond to any requests in the conversation above
3. DO NOT call ANY tools or functions
4. DO NOT use tool_calls in your response
5. Your response MUST be PURE TEXT ONLY

YOUR ONLY TASK: Create a comprehensive summary of the conversation above.

REQUIRED RESPONSE FORMAT:
First output a <topics> line listing 3-6 key topic phrases (comma-separated, concise).
Then output the full summary wrapped in <summary> tags.

Example format:
<topics>Rails setup, database config, deploy pipeline, Tailwind CSS</topics>
<summary>
...full summary text...
</summary>

Focus on:
- User's explicit requests and intents
- Key technical concepts and code changes
- Files examined and modified
- Errors encountered and fixes applied
- Current work status and pending tasks

Begin your response NOW. Remember: PURE TEXT only, starting with <topics> then <summary>.
"""

# Truncation cap for tool results kept in the recent window
# (port of truncate_tool_result, 2000 chars).
TOOL_RESULT_MAX_CHARS = 2000
# Tool results archived into chunk MD are trimmed harder (port: 500 chars).
CHUNK_TOOL_RESULT_MAX_CHARS = 500
# Average tokens per message used by calculate_target_recent_count (port).
TOKENS_PER_MESSAGE_ESTIMATE = 500
# Do not compress when the expected reduction is under this fraction of the
# current size (port of the 10% guard in compress_messages_if_needed).
MIN_REDUCTION_FRACTION = 0.1
# Max older-chunk references embedded into a new summary (port).
MAX_VISIBLE_PREVIOUS_CHUNKS = 10

_TOPICS_RE = re.compile(r"<topics>(.*?)</topics>", re.DOTALL)
_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.DOTALL)
_CHUNK_FILE_RE = re.compile(r"^chunk-(\d+)\.md$")


@dataclass
class CompressionResult:
    """Outcome of one SessionCompressor.compress() run."""

    compressed: bool
    messages: list[dict[str, Any]]
    reason: str = ""
    chunk_path: str | None = None
    topics: str | None = None
    level: int = 0
    tokens_before: int = 0
    tokens_after: int = 0
    archived_messages: int = 0
    kept_recent: int = 0
    merged_into_previous_chunk: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "compressed": self.compressed,
            "messages": self.messages,
            "reason": self.reason,
            "chunk_path": self.chunk_path,
            "topics": self.topics,
            "level": self.level,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "archived_messages": self.archived_messages,
            "kept_recent": self.kept_recent,
            "merged_into_previous_chunk": self.merged_into_previous_chunk,
        }


# ─────────────────────────── pure helpers (ports) ───────────────────────────


def build_compression_message(
    messages: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Build the Insert-then-Compress instruction message.

    Port of ``MessageCompressor#build_compression_message``: the instruction
    is a ``system_injected`` USER message so the call reuses the existing
    prefix cache. Returns ``None`` when there is nothing to compress
    (everything is system or recent).
    """
    recent = recent_messages or []
    recent_ids = {id(m) for m in recent}
    to_compress = [
        m
        for m in messages
        if m.get("role") != "system" and id(m) not in recent_ids
    ]
    if not to_compress:
        return None
    return {
        "role": "user",
        "content": COMPRESSION_PROMPT,
        "system_injected": True,
    }


def parse_topics(content: str | None) -> str | None:
    """Extract the ``<topics>`` payload from compression output
    (port of ``MessageCompressor#parse_topics``)."""
    if not content:
        return None
    m = _TOPICS_RE.search(str(content))
    return m.group(1).strip() if m else None


def _strip_topics_block(content: str) -> str:
    return re.sub(r"<topics>.*?</topics>\n*", "", content, flags=re.DOTALL).strip()


def parse_compressed_result(
    result: str | None,
    chunk_path: str | None = None,
    topics: str | None = None,
    previous_chunks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Frame a compressed summary as the single injected user message.

    Port of ``MessageCompressor#parse_compressed_result`` including:
    - role ``user`` + ``system_injected`` (strict providers reject rebuilt
      histories without a user turn boundary),
    - ``compressed_summary`` / ``chunk_path`` / ``topics`` bookkeeping fields,
    - the previous-chunks index (newest first, capped at 10),
    - the current-chunk anchor line.
    """
    content = (result or "").strip()
    if not content:
        return []

    content_without_topics = _strip_topics_block(content)
    # When the producer wrapped the body in <summary> tags, unwrap it.
    summary_match = _SUMMARY_RE.search(content_without_topics)
    if summary_match:
        content_without_topics = summary_match.group(1).strip()

    previous_chunks = previous_chunks or []
    previous_chunks_section = ""
    if previous_chunks:
        visible = previous_chunks[-MAX_VISIBLE_PREVIOUS_CHUNKS:][::-1]
        older_count = len(previous_chunks) - len(visible)
        lines = ["\n\n---\n**Previous chunks (newest first):**"]
        for pc in visible:
            topic_str = f" — {pc['topics']}" if pc.get("topics") else ""
            lines.append(f"- `{pc['basename']}`{topic_str}")
        if older_count > 0:
            oldest = previous_chunks[0]
            lines.append(
                f"- ... and {older_count} older chunks back to `{oldest['basename']}`"
            )
        lines.append("_Read these chunk files to recall details from earlier work._")
        previous_chunks_section = "\n".join(lines)

    anchor = ""
    if chunk_path:
        anchor = (
            f"\n\n---\n**Current chunk archived at:** `{chunk_path}`\n"
            "_Read this chunk file to recall details from this conversation._"
        )

    framed_content = (
        "[Compressed conversation summary — previous turns archived]\n\n"
        f"{content_without_topics}"
        f"{previous_chunks_section}"
        f"{anchor}"
    )
    return [
        {
            "role": "user",
            "content": framed_content,
            "compressed_summary": True,
            "chunk_path": chunk_path,
            "topics": topics,
            "system_injected": True,
        }
    ]


def rebuild_with_compression(
    compressed_content: str,
    original_messages: list[dict[str, Any]],
    recent_messages: list[dict[str, Any]],
    chunk_path: str | None = None,
    topics: str | None = None,
    previous_chunks: list[dict[str, Any]] | None = None,
    pulled_back_messages: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild the history: system + compressed summary + recent + pulled-back.

    Port of ``MessageCompressor#rebuild_with_compression`` including the
    safety nets: system messages are stripped from the recent/pulled-back
    lists so the prompt can never carry two system prompts, and an empty
    parse raises instead of silently dropping the conversation.
    """
    system_msg = next(
        (m for m in original_messages if m.get("role") == "system"), None
    )
    parsed = parse_compressed_result(
        compressed_content,
        chunk_path=chunk_path,
        topics=topics,
        previous_chunks=previous_chunks or [],
    )
    if not parsed:
        raise ValueError("LLM compression failed: unable to parse compressed messages")

    safe_recent = [m for m in recent_messages if m.get("role") != "system"]
    safe_pulled = [
        m for m in (pulled_back_messages or []) if m.get("role") != "system"
    ]
    out: list[dict[str, Any]] = []
    if system_msg is not None:
        out.append(copy.deepcopy(system_msg))
    out.extend(parsed)
    out.extend(copy.deepcopy(m) for m in safe_recent)
    out.extend(copy.deepcopy(m) for m in safe_pulled)
    return out


def _tool_result_message(msg: dict[str, Any]) -> bool:
    """Canonical OpenAI tool result OR legacy Anthropic-native tool_result
    blocks inside a user message (port of tool_result_message?)."""
    if msg.get("role") == "tool":
        return True
    if msg.get("role") != "user":
        return False
    content = msg.get("content")
    return (
        isinstance(content, list)
        and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
    )


def _tool_result_ids(msg: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    if msg.get("role") == "tool" and msg.get("tool_call_id"):
        ids.add(str(msg["tool_call_id"]))
        return ids
    content = msg.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = block.get("tool_use_id")
                if tid:
                    ids.add(str(tid))
    return ids


def _pull_tool_results_after(
    messages: list[dict[str, Any]],
    assistant_idx: int,
    include: set[int],
) -> None:
    tool_calls = messages[assistant_idx].get("tool_calls") or []
    call_ids = set()
    for tc in tool_calls:
        if isinstance(tc, dict) and tc.get("id"):
            call_ids.add(str(tc["id"]))
    j = assistant_idx + 1
    while j < len(messages):
        nxt = messages[j]
        if _tool_result_message(nxt) and (_tool_result_ids(nxt) & call_ids):
            include.add(j)
        elif not _tool_result_message(nxt):
            break
        j += 1


def _pull_assistant_before(
    messages: list[dict[str, Any]],
    tool_result_idx: int,
    include: set[int],
) -> int:
    """Mark the owning assistant message + sibling results. Returns 1 when the
    assistant was newly added (caller increments the collected count)."""
    result_ids = _tool_result_ids(messages[tool_result_idx])
    added = 0
    j = tool_result_idx - 1
    while j >= 0:
        prev = messages[j]
        if prev.get("role") == "assistant" and prev.get("tool_calls"):
            call_ids = {
                str(tc.get("id"))
                for tc in prev.get("tool_calls") or []
                if isinstance(tc, dict) and tc.get("id")
            }
            if call_ids & result_ids:
                if j not in include:
                    include.add(j)
                    added = 1
                _pull_tool_results_after(messages, j, include)
                break
        j -= 1
    return added


def truncate_tool_result(msg: dict[str, Any], max_chars: int = TOOL_RESULT_MAX_CHARS) -> dict[str, Any]:
    """Trim oversized canonical tool results (port of truncate_tool_result)."""
    content = msg.get("content")
    if (
        msg.get("role") == "tool"
        and isinstance(content, str)
        and len(content) > max_chars
    ):
        trimmed = dict(msg)
        trimmed["content"] = (
            content[:max_chars] + "...\n[Content truncated - exceeded "
            f"{max_chars} characters]"
        )
        return trimmed
    return msg


def get_recent_messages_with_tool_pairs(
    messages: list[dict[str, Any]], count: int
) -> list[dict[str, Any]]:
    """Select the trailing *count* messages, never splitting a tool call from
    its results.

    Port of ``MessageCompressorHelper#get_recent_messages_with_tool_pairs``:
    - the system message is never included (it is re-prepended separately);
    - an assistant message with tool_calls pulls in all following results;
    - a tool result pulls in its assistant + sibling results;
    - large tool results are truncated.
    """
    if not messages:
        return []

    include: set[int] = set()
    i = len(messages) - 1
    collected = 0
    while i >= 0 and collected < count:
        msg = messages[i]
        if msg.get("role") == "system":
            i -= 1
            continue
        if i in include:
            i -= 1
            continue
        include.add(i)
        collected += 1

        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            _pull_tool_results_after(messages, i, include)
        if _tool_result_message(msg):
            collected += _pull_assistant_before(messages, i, include)
        i -= 1

    recent = [messages[idx] for idx in sorted(include)]
    return [truncate_tool_result(m) for m in recent]


def calculate_target_recent_count(
    reduction_needed: int,
    target_compressed_tokens: int,
    max_recent_messages: int,
) -> int:
    """Port of ``calculate_target_recent_count``: recent window should be
    ~20% of the compressed target, clamped to [20-derived, max_recent]."""
    recent_budget = int(target_compressed_tokens * 0.2)
    target_messages = recent_budget // TOKENS_PER_MESSAGE_ESTIMATE
    # OpenClacky: [[target_messages, 20].max, MAX_RECENT_MESSAGES].min — the
    # floor constant 20 collapses with max_recent in our config-driven port.
    return max(1, min(max(target_messages, 1), max_recent_messages))


def compression_needed(
    total_tokens: int,
    message_count: int,
    threshold_tokens: int,
    threshold_messages: int,
    target_compressed_tokens: int,
) -> bool:
    """Normal (non-forced) trigger gate, port of compress_messages_if_needed:
    either threshold may trip, but a token-triggered compression is skipped
    when the achievable reduction is under 10% of the current size."""
    token_exceeded = total_tokens >= threshold_tokens
    count_exceeded = message_count >= threshold_messages
    if not (token_exceeded or count_exceeded):
        return False
    reduction_needed = total_tokens - target_compressed_tokens
    if token_exceeded and reduction_needed < total_tokens * MIN_REDUCTION_FRACTION:
        return False
    return True


def idle_compression_needed(
    total_tokens: int,
    message_count: int,
    idle_threshold_tokens: int,
    max_recent_messages: int,
) -> bool:
    """Forced/idle trigger gate (port): enough messages to be worth it AND at
    least the idle token floor."""
    if message_count <= max_recent_messages + 1:
        return False
    return total_tokens >= idle_threshold_tokens


# ─────────────────────────── archive (chunk MD) ────────────────────────────


def resolve_archive_dir(raw_dir: str | None = None) -> Path:
    """Resolve ``settings.efficiency.archive_dir`` to a concrete directory.

    Relative paths are anchored at the gateway DATA_DIR so test isolation
    (conftest monkeypatches ``moa_gateway.config.DATA_DIR``) keeps archives
    inside the per-test temp dir. A leading ``data/`` component in the
    configured value is folded into DATA_DIR (which already ends in /data)
    to avoid ``data/data/...`` nesting.
    """
    settings = _cfg.get_settings()
    raw = Path(raw_dir or settings.efficiency.archive_dir)
    if raw.is_absolute():
        return raw
    parts = list(raw.parts)
    if parts and parts[0] == "data":
        parts = parts[1:]
    return Path(_cfg.DATA_DIR).joinpath(*parts)


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")


def _format_message_content(content: Any) -> str:
    """Port of format_message_content (string / content-block arrays)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, dict):
                parts.append(f"[{block.get('type') or 'content'}]")
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(content)


def _truncate_for_chunk(text: str, max_length: int = CHUNK_TOOL_RESULT_MAX_CHARS) -> str:
    if len(text) <= max_length:
        return text
    return f"{text[:max_length]}\n... [truncated, {len(text)} chars total]"


def render_message_sections(messages: list[dict[str, Any]]) -> list[str]:
    """Port of ``render_message_sections`` — chunk MD body rendering."""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            lines.append("## User")
            lines.append("")
            lines.append(_format_message_content(content))
            lines.append("")
        elif role == "assistant":
            if msg.get("compressed_summary") and msg.get("chunk_path"):
                prev_chunk = Path(str(msg.get("chunk_path"))).name
                lines.append(
                    f"## Assistant [Compressed Summary — original conversation at: {prev_chunk}]"
                )
            else:
                lines.append("## Assistant")
            lines.append("")
            tool_calls = msg.get("tool_calls") or []
            if tool_calls:
                tc_parts: list[str] = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    func = tc.get("function") or {}
                    name = func.get("name") or tc.get("name") or ""
                    if not name:
                        continue
                    args_raw = func.get("arguments") or tc.get("arguments") or {}
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except (ValueError, TypeError):
                            args = {}
                    else:
                        args = args_raw
                    if isinstance(args, dict) and args:
                        compact = {
                            k: (v[:200] + "..." if isinstance(v, str) and len(v) > 200 else v)
                            for k, v in args.items()
                        }
                        tc_parts.append(f"{name} | {json.dumps(compact, ensure_ascii=False)}")
                    else:
                        tc_parts.append(str(name))
                if tc_parts:
                    lines.append(f"_Tool calls: {'; '.join(tc_parts)}_")
                    lines.append("")
            if content:
                lines.append(_format_message_content(content))
                lines.append("")
        elif role == "tool":
            tool_name = msg.get("name") or "tool"
            lines.append(f"### Tool Result: {tool_name}")
            lines.append("")
            lines.append("```")
            lines.append(_truncate_for_chunk(str(content or "")))
            lines.append("```")
            lines.append("")
    return lines


def build_chunk_md(
    messages: list[dict[str, Any]],
    session_id: str,
    chunk_index: int,
    compression_level: int,
    topics: str | None = None,
    now: datetime | None = None,
) -> str:
    """Port of ``build_chunk_md`` — front matter + archived conversation."""
    lines = [
        "---",
        f"session_id: {session_id}",
        f"chunk: {chunk_index}",
        f"compression_level: {compression_level}",
        f"archived_at: {_now_iso(now)}",
        f"message_count: {len(messages)}",
    ]
    if topics:
        lines.append(f"topics: {topics}")
    lines.extend(
        [
            "---",
            "",
            f"# Session Chunk {chunk_index}",
            "",
            "> This file contains the original conversation archived during compression.",
            "> Read this file to recall specific details from this conversation.",
            "",
        ]
    )
    lines.extend(render_message_sections(messages))
    return "\n".join(lines)


def parse_chunk_front_matter(raw: str) -> dict[str, str]:
    """Minimal front-matter reader for our own chunk files."""
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fm: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm


# ─────────────────────────── session compressor ────────────────────────────


@dataclass
class _ChunkInfo:
    index: int
    path: Path
    topics: str | None

    @property
    def basename(self) -> str:
        return self.path.name


class SessionCompressor:
    """Full compression pipeline for one message sequence.

    Steps (mirrors OpenClacky's agent-side flow):
    1. gate: ``compression_needed`` / ``idle_compression_needed``
       (skippable with ``force=True``);
    2. select the recent window preserving tool-call pairs;
    3. build the Insert-then-Compress instruction message;
    4. summarize the archived portion with the real extractive pipeline
       (or a caller-supplied summarizer, e.g. an LLM-backed one);
    5. archive the raw messages as a chunk MD file under
       ``settings.efficiency.archive_dir`` (merge into the previous chunk on
       idle compression, new chunk on threshold compression — port of
       ``force_new_chunk = !force``);
    6. rebuild: system + framed summary + recent.
    """

    def __init__(
        self,
        archive_dir: str | Path | None = None,
        summarizer: Callable[..., Any] | None = None,
    ) -> None:
        self._archive_dir_override = Path(archive_dir) if archive_dir else None
        # summarizer(messages, target_tokens, level) -> SummaryResult. The
        # default is the real extractive pipeline; injection point for an
        # LLM-backed summarizer when the main thread wires one.
        self._summarizer = summarizer or summarize_messages
        self._lock = threading.Lock()
        self._levels: dict[str, int] = {}

    # -- archive helpers ------------------------------------------------

    def archive_root(self, session_id: str) -> Path:
        base = self._archive_dir_override or resolve_archive_dir()
        return base / _safe_session_id(session_id)

    def discover_chunks(self, session_id: str) -> list[_ChunkInfo]:
        """Disk is the source of truth for chunk indices (port of the
        SessionManager discovery; in-memory counting caps at 1 and would
        overwrite chunk-2.md on every later compression)."""
        root = self.archive_root(session_id)
        if not root.is_dir():
            return []
        chunks: list[_ChunkInfo] = []
        for entry in root.iterdir():
            m = _CHUNK_FILE_RE.match(entry.name)
            if not m or not entry.is_file():
                continue
            topics = None
            try:
                fm = parse_chunk_front_matter(entry.read_text(encoding="utf-8"))
                topics = fm.get("topics")
            except OSError:
                pass
            chunks.append(_ChunkInfo(index=int(m.group(1)), path=entry, topics=topics))
        chunks.sort(key=lambda c: c.index)
        return chunks

    def next_chunk_index(self, session_id: str) -> int:
        chunks = self.discover_chunks(session_id)
        return (chunks[-1].index + 1) if chunks else 1

    def _write_chunk(
        self, session_id: str, chunk_index: int, md_content: str
    ) -> Path:
        root = self.archive_root(session_id)
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"chunk-{chunk_index}.md"
        path.write_text(md_content, encoding="utf-8")
        logger.info(
            "efficiency: archived session chunk %s/%s (%d bytes)",
            session_id,
            path.name,
            len(md_content),
        )
        return path

    def _merge_into_previous_chunk(
        self,
        prev: _ChunkInfo,
        messages_to_archive: list[dict[str, Any]],
        compression_level: int,
        topics: str | None,
        now: datetime | None,
    ) -> Path | None:
        """Port of merge_into_previous_chunk: append new sections in place."""
        try:
            raw = prev.path.read_text(encoding="utf-8")
        except OSError:
            return None
        fm = parse_chunk_front_matter(raw)
        if not fm:
            return None
        # Re-emit front matter with updated counters.
        fm["compression_level"] = str(compression_level)
        fm["archived_at"] = _now_iso(now)
        try:
            fm["message_count"] = str(int(fm.get("message_count", "0")) + len(messages_to_archive))
        except ValueError:
            fm["message_count"] = str(len(messages_to_archive))
        fm["merged_count"] = str(int(fm.get("merged_count", "1")) + 1)
        if topics:
            existing = [t.strip() for t in fm.get("topics", "").split(",") if t.strip()]
            incoming = [t.strip() for t in topics.split(",") if t.strip()]
            merged = list(dict.fromkeys(existing + incoming))
            fm["topics"] = ", ".join(merged)

        # Body = everything after the closing '---' of the front matter.
        parts = raw.split("---", 2)
        body = parts[2].rstrip() if len(parts) >= 3 else raw
        lines = ["---"]
        lines.extend(f"{k}: {v}" for k, v in fm.items())
        lines.append("---")
        lines.append(body)
        lines.append("")
        lines.extend(render_message_sections(messages_to_archive))
        try:
            prev.path.write_text("\n".join(lines), encoding="utf-8")
        except OSError:
            return None
        logger.info("efficiency: merged into previous chunk %s", prev.path.name)
        return prev.path

    # -- main entry ------------------------------------------------------

    def compress(
        self,
        messages: list[dict[str, Any]],
        session_id: str,
        force: bool = False,
        now: datetime | None = None,
    ) -> CompressionResult:
        """Compress *messages* in place of the caller's history.

        Returns a :class:`CompressionResult`; ``compressed`` is False (and the
        original list returned untouched) when the gates decide there is
        nothing worth compressing.
        """
        settings = _cfg.get_settings().efficiency
        tokens_before = estimate_messages_tokens(messages)
        message_count = len(messages)

        if not force:
            if not compression_needed(
                tokens_before,
                message_count,
                settings.compression_threshold_tokens,
                settings.compression_threshold_messages,
                settings.target_compressed_tokens,
            ):
                return CompressionResult(
                    compressed=False,
                    messages=copy.deepcopy(messages),
                    reason=(
                        "below thresholds "
                        f"(tokens={tokens_before} < {settings.compression_threshold_tokens}, "
                        f"messages={message_count} < {settings.compression_threshold_messages})"
                    ),
                    tokens_before=tokens_before,
                    tokens_after=tokens_before,
                )
        else:
            if not idle_compression_needed(
                tokens_before,
                message_count,
                settings.idle_threshold_tokens,
                settings.max_recent_messages,
            ):
                return CompressionResult(
                    compressed=False,
                    messages=copy.deepcopy(messages),
                    reason=(
                        "idle gate not met "
                        f"(tokens={tokens_before}, messages={message_count}, "
                        f"idle_threshold_tokens={settings.idle_threshold_tokens})"
                    ),
                    tokens_before=tokens_before,
                    tokens_after=tokens_before,
                )

        with self._lock:
            level = self._levels.get(session_id, 0) + 1
            self._levels[session_id] = level

        reduction_needed = tokens_before - settings.target_compressed_tokens
        target_recent = calculate_target_recent_count(
            reduction_needed,
            settings.target_compressed_tokens,
            settings.max_recent_messages,
        )

        recent_messages = get_recent_messages_with_tool_pairs(messages, target_recent)

        compression_message = build_compression_message(messages, recent_messages)
        if compression_message is None:
            return CompressionResult(
                compressed=False,
                messages=copy.deepcopy(messages),
                reason="nothing to compress (only system/recent messages present)",
                tokens_before=tokens_before,
                tokens_after=tokens_before,
            )

        # ── Insert step: the instruction joins the flow so the compression
        # call reuses the warm prefix cache (Insert-then-Compress).
        inserted = list(messages) + [compression_message]

        # The original messages BEFORE the inserted instruction (port:
        # original_messages = history.to_a[0..-2]).
        original_messages = inserted[:-1]

        # ── Compress step: real summarization of the archived portion.
        recent_identity = {id(m) for m in recent_messages}
        messages_to_archive = [
            m
            for m in original_messages
            if m.get("role") != "system"
            and not m.get("system_injected")
            and not m.get("compressed_summary")
            and id(m) not in recent_identity
        ]

        topics = extract_topics(messages_to_archive) or None
        chunk_path: Path | None = None
        merged = False
        previous_chunks_info: list[dict[str, Any]] = []
        if messages_to_archive:
            existing = self.discover_chunks(session_id)
            force_new_chunk = not force
            latest = existing[-1] if existing else None
            if latest is not None and not force_new_chunk:
                chunk_path = self._merge_into_previous_chunk(
                    latest, messages_to_archive, level, topics, now
                )
                merged = chunk_path is not None
                if chunk_path is None:
                    chunk_path = self._write_chunk(
                        session_id,
                        self.next_chunk_index(session_id),
                        build_chunk_md(
                            messages_to_archive, session_id,
                            self.next_chunk_index(session_id), level, topics, now,
                        ),
                    )
                index_chunks = [c for c in existing if c.index != latest.index]
            else:
                chunk_path = self._write_chunk(
                    session_id,
                    self.next_chunk_index(session_id),
                    build_chunk_md(
                        messages_to_archive, session_id,
                        self.next_chunk_index(session_id), level, topics, now,
                    ),
                )
                index_chunks = existing
            previous_chunks_info = [
                {"basename": c.basename, "path": str(c.path), "topics": c.topics}
                for c in index_chunks
            ]

        summary = self._summarizer(
            messages_to_archive, settings.target_compressed_tokens, level
        )
        summary_text = getattr(summary, "text", None) or str(summary)
        summary_topics = topics or getattr(summary, "topics", None)

        rebuilt = rebuild_with_compression(
            summary_text,
            original_messages=original_messages,
            recent_messages=recent_messages,
            chunk_path=str(chunk_path) if chunk_path else None,
            topics=summary_topics,
            previous_chunks=previous_chunks_info,
        )

        tokens_after = estimate_messages_tokens(rebuilt)
        logger.info(
            "efficiency: compressed session %s level=%d tokens %d -> %d "
            "(archived %d msgs, kept %d recent, chunk=%s)",
            session_id,
            level,
            tokens_before,
            tokens_after,
            len(messages_to_archive),
            len(recent_messages),
            chunk_path.name if chunk_path else None,
        )
        return CompressionResult(
            compressed=True,
            messages=rebuilt,
            reason=(
                f"insert-then-compress level {level}: "
                f"{len(messages_to_archive)} messages archived, "
                f"{len(recent_messages)} recent kept"
            ),
            chunk_path=str(chunk_path) if chunk_path else None,
            topics=summary_topics,
            level=level,
            tokens_before=tokens_before,
            tokens_after=tokens_after,
            archived_messages=len(messages_to_archive),
            kept_recent=len(recent_messages),
            merged_into_previous_chunk=merged,
        )


def _safe_session_id(session_id: str) -> str:
    """Filesystem-safe session id (no path traversal out of archive root)."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id).strip())
    # Drop leading dots/dashes/underscores so no id can create a hidden or
    # traversal-look directory segment; separators never survive the regex.
    cleaned = cleaned.lstrip("._-")
    return cleaned or "session"


def clean_rebuilt_history(
    messages: list[dict[str, Any]], max_total_chars: int = 100_000
) -> list[dict[str, Any]]:
    """Run the gateway's real 7-stage context cleaner over a rebuilt history
    (composition with ``moa_gateway.capability.context_clean``). Useful after
    compression when tool pairs may have been clipped."""
    try:
        parsed = context_clean.from_openai_format(messages)
    except ValueError:
        return messages
    cleaned, _stats = context_clean.clean_messages(parsed, max_total_chars)
    return context_clean.to_openai_format(cleaned)
