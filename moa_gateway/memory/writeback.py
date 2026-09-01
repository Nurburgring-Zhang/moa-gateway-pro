"""Writeback pipeline — ported from MemoraX Code.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT):

- turn idempotency keys and per-session buffering semantics from
  ``packages/ts/memorax-code-backend/src/memory/writeback-buffer.ts``
  (a writeback is accepted at most once per ``(client, sessionId,
  clientTurnId)``; buffered turns flush when ANY of the turn-count, age or
  character limits is reached);
- chunking recipe from ``.../memory/writeback-chunk.ts``: the buffered
  transcript is split into chunks of at most ``chunk_chars`` with a trailing
  overlap of ``floor(chunk_chars * overlap)`` characters, and all chunks of
  one flush share a ``group_id`` of the form
  ``memory-writeback-chunk:v1:<digest>``;
- PII redaction is applied before buffering (see ``redaction.py``, ported
  from ``.../memory/payload-redaction.ts``) and gated by
  ``MemoryConfig.redact_pii``.

Persisted items remain idempotent at the storage layer too
(``memory_items.UNIQUE(effective_user_id, content_hash)`` in ``store.py``),
so replaying the same flush can never duplicate memories.
"""

from __future__ import annotations

import hashlib
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from ..config import MemoryConfig
from .classifier import classify_memory_type
from .redaction import has_meaningful_text, redact_text
from .scope import MemoryScope
from .store import MemoryStore
from .vectorizer import DenseVectorizer

logger = logging.getLogger(__name__)

#: MemoraX group-id prefix for chunked writebacks (writeback-chunk.ts).
CHUNK_GROUP_PREFIX = "memory-writeback-chunk:v1:"

_VALID_ROLES = ("user", "assistant", "system", "tool")


# ---------------------------------------------------------------------------
# Keys / message extraction
# ---------------------------------------------------------------------------
def turn_idempotency_key(client: str, session_id: str, correlation_id: str) -> str:
    """MemoraX turn identity: one writeback accepted per client/turn pair."""
    return f"{client}:{session_id}:{correlation_id or 'anonymous-turn'}"


def buffer_key_for(client: str, session_id: str, effective_user_id: str) -> str:
    """Buffer scope: per memory scope + client session."""
    return f"{effective_user_id}\x00{client}:{session_id}"


def extract_turn_messages(command: dict[str, Any]) -> list[dict[str, str]]:
    """Normalize a parsed writeback command into ``[{role, content}, ...]``.

    Per-client shapes follow MemoraX's WRITEBACK_KEYS contract:
    codex / claude-code carry ``lastAssistantMessage``; opencode carries a
    full ``messages`` array; dsh carries an ``events`` array from which user
    and assistant text events are extracted.
    """
    client = command.get("client")
    messages: list[dict[str, str]] = []
    if client in ("codex", "claude-code"):
        content = command.get("lastAssistantMessage")
        if isinstance(content, str) and content.strip():
            messages.append({"role": "assistant", "content": content.strip()})
    elif client == "opencode":
        for raw in command.get("messages") or []:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").strip().lower()
            content = raw.get("content")
            if role not in _VALID_ROLES or not isinstance(content, str):
                continue
            if content.strip():
                messages.append({"role": role, "content": content.strip()})
    elif client == "dsh":
        for raw in command.get("events") or []:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").strip().lower()
            content = raw.get("text")
            if content is None:
                content = raw.get("content")
            if role not in _VALID_ROLES or not isinstance(content, str):
                continue
            if content.strip():
                messages.append({"role": role, "content": content.strip()})
    return messages


def build_transcript(messages: list[dict[str, str]]) -> str:
    """Flatten messages into the chunkable transcript text."""
    return "\n".join(f"{message['role']}: {message['content']}" for message in messages)


# ---------------------------------------------------------------------------
# Chunking (writeback-chunk.ts recipe)
# ---------------------------------------------------------------------------
def chunk_overlap_chars(chunk_chars: int, overlap_ratio: float) -> int:
    """Overlap in characters: ``floor(chunk_chars * overlap_ratio)``.

    Capped at half the chunk size so chunking always makes forward progress.
    """
    overlap = int(math.floor(chunk_chars * max(0.0, overlap_ratio)))
    return min(overlap, chunk_chars // 2)


def chunk_text(text: str, chunk_chars: int, overlap_ratio: float) -> list[str]:
    """Split ``text`` into chunks of at most ``chunk_chars`` characters.

    Adjacent chunks overlap by ``chunk_overlap_chars(...)`` trailing
    characters (MemoraX overlap semantics).  When possible the cut point is
    moved back to the last newline inside the second half of the window so
    transcript lines are not torn apart; content is never lost or reordered.
    """
    if chunk_chars <= 0:
        raise ValueError("chunk_chars must be > 0")
    text = text or ""
    if not text.strip():
        return []
    overlap = chunk_overlap_chars(chunk_chars, overlap_ratio)
    chunks: list[str] = []
    start = 0
    total = len(text)
    while start < total:
        end = min(start + chunk_chars, total)
        if end < total:
            window_start = start + chunk_chars // 2
            cut = text.rfind("\n", window_start, end)
            if cut > window_start:
                end = cut + 1
        chunk = text[start:end]
        if chunk.strip():
            chunks.append(chunk)
        if end >= total:
            break
        start = max(end - overlap, start + 1)
    return chunks


def chunk_group_id(buffer_key: str, created_at: float, first_turn_key: str) -> str:
    """Deterministic ``group_id`` shared by all chunks of one flush."""
    digest = hashlib.sha256(
        f"{buffer_key}\x00{created_at!r}\x00{first_turn_key}".encode("utf-8")
    ).hexdigest()[:40]
    return f"{CHUNK_GROUP_PREFIX}{digest}"


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
@dataclass
class WritebackReceipt:
    accepted: bool
    duplicate: bool = False
    buffered: bool = False
    flushed: bool = False
    buffer_key: str = ""
    turn_key: str = ""
    message_count: int = 0
    redactions: dict[str, int] = field(default_factory=dict)
    redacted_total: int = 0
    chunks: int = 0
    items_created: int = 0
    items_duplicate: int = 0
    group_id: str | None = None
    flush_reason: str | None = None
    skip_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "accepted": self.accepted,
            "duplicate": self.duplicate,
            "buffered": self.buffered,
            "flushed": self.flushed,
            "message_count": self.message_count,
            "redactions": dict(self.redactions),
            "redacted_total": self.redacted_total,
            "chunks": self.chunks,
            "items_created": self.items_created,
            "items_duplicate": self.items_duplicate,
        }
        if self.group_id:
            payload["group_id"] = self.group_id
        if self.flush_reason:
            payload["flush_reason"] = self.flush_reason
        if self.skip_reason:
            payload["skip_reason"] = self.skip_reason
        return payload


@dataclass
class FlushReport:
    buffer_key: str
    flushed: bool
    reason: str
    chunks: int = 0
    items_created: int = 0
    items_duplicate: int = 0
    group_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "buffer_key": self.buffer_key,
            "flushed": self.flushed,
            "reason": self.reason,
            "chunks": self.chunks,
            "items_created": self.items_created,
            "items_duplicate": self.items_duplicate,
            "group_id": self.group_id,
        }


# ---------------------------------------------------------------------------
# Flush path
# ---------------------------------------------------------------------------
def flush_buffer(
    store: MemoryStore,
    vectorizer: DenseVectorizer,
    cfg: MemoryConfig,
    buffer_key: str,
    *,
    reason: str,
    now: float,
) -> FlushReport:
    """Flush one buffer: transcript -> chunks -> idempotent item inserts.

    The buffer is removed even when every chunk turns out to be a duplicate
    (idempotency lives in the item store), so a flush never loops.
    """
    state = store.get_buffer(buffer_key)
    messages = store.buffer_messages(buffer_key)
    if state is None or not messages:
        store.delete_buffer(buffer_key)
        return FlushReport(buffer_key=buffer_key, flushed=False, reason="empty_buffer")

    transcript = build_transcript(messages)
    chunks = chunk_text(transcript, cfg.chunk_chars, cfg.chunk_overlap)
    if not chunks:
        store.delete_buffer(buffer_key)
        return FlushReport(buffer_key=buffer_key, flushed=False, reason="no_meaningful_content")

    first_turn_key = messages[0]["turn_idempotency_key"]
    group_id = chunk_group_id(buffer_key, state["created_at"], first_turn_key)
    chunk_count = len(chunks)
    created = duplicates = 0
    # effective_user_id = "<base>@<slug>"; the slug is slugified (never
    # contains '@'), so rsplit keeps base ids with a literal '@' intact.
    base_user, _, repo_slug = state["effective_user_id"].rpartition("@")
    for index, chunk in enumerate(chunks):
        embedding = vectorizer.embed(chunk)
        item_id, was_created = store.insert_item(
            effective_user_id=state["effective_user_id"],
            base_user_id=base_user or state["effective_user_id"],
            repository_slug=repo_slug,
            memory_type=classify_memory_type(chunk),
            content=chunk,
            role="conversation",
            source="writeback",
            session_id=state["session_key"],
            group_id=group_id,
            chunk_index=index,
            chunk_count=chunk_count,
            embedding=embedding,
            now=now,
        )
        if was_created and item_id is not None:
            created += 1
        else:
            duplicates += 1
    store.delete_buffer(buffer_key)
    logger.info(
        "memory writeback flush: buffer=%s reason=%s chunks=%d created=%d duplicate=%d",
        buffer_key,
        reason,
        chunk_count,
        created,
        duplicates,
    )
    return FlushReport(
        buffer_key=buffer_key,
        flushed=True,
        reason=reason,
        chunks=chunk_count,
        items_created=created,
        items_duplicate=duplicates,
        group_id=group_id,
    )


def _should_flush(state: dict[str, Any], cfg: MemoryConfig, now: float) -> str | None:
    """Return the first triggered flush reason, or None (any-trigger policy)."""
    if state["turn_count"] >= cfg.buffer_turns:
        return "turn_count"
    if state["content_chars"] >= cfg.buffer_chars:
        return "char_count"
    if now - state["created_at"] >= cfg.buffer_seconds:
        return "buffer_age"
    return None


def sweep_expired_buffers(
    store: MemoryStore,
    vectorizer: DenseVectorizer,
    cfg: MemoryConfig,
    *,
    now: float,
) -> list[FlushReport]:
    """Flush buffers whose age/idle deadline has passed (background path)."""
    reports: list[FlushReport] = []
    for buffer_key in store.all_buffer_keys():
        state = store.get_buffer(buffer_key)
        if state is None:
            continue
        expired = now >= state["idle_deadline"] or now - state["created_at"] >= cfg.buffer_seconds
        if expired:
            reports.append(
                flush_buffer(store, vectorizer, cfg, buffer_key, reason="expired", now=now)
            )
    return reports


# ---------------------------------------------------------------------------
# Enqueue path (turn association -> redaction -> buffering -> maybe flush)
# ---------------------------------------------------------------------------
def enqueue_writeback(
    store: MemoryStore,
    vectorizer: DenseVectorizer,
    cfg: MemoryConfig,
    *,
    client: str,
    session_id: str,
    correlation_id: str,
    scope: MemoryScope,
    messages: list[dict[str, str]],
    now: float,
) -> WritebackReceipt:
    """Run the full writeback pipeline for one turn.

    Idempotent at two layers: the turn idempotency key rejects replays
    before any side effect, and the item store deduplicates by content hash.
    """
    turn_key = turn_idempotency_key(client, session_id, correlation_id)
    buffer_key = buffer_key_for(client, session_id, scope.effective_user_id)

    if store.has_dedupe_key(turn_key):
        return WritebackReceipt(accepted=False, duplicate=True, turn_key=turn_key, buffer_key=buffer_key)

    meaningful = [m for m in messages if has_meaningful_text(m.get("content", ""))]
    if not meaningful:
        # Reserve the key so a replay of the same empty turn stays a no-op.
        store.reserve_dedupe_keys([turn_key], now=now)
        return WritebackReceipt(
            accepted=False,
            turn_key=turn_key,
            buffer_key=buffer_key,
            skip_reason="no_meaningful_content",
            message_count=len(messages),
        )

    # --- PII redaction (before anything is persisted) -----------------------
    redactions: dict[str, int] = {}
    redacted_total = 0
    prepared: list[dict[str, str]] = []
    for message in meaningful:
        content = message["content"]
        if cfg.redact_pii:
            content, counts, _was_redacted = redact_text(content)
            for kind, count in counts.items():
                redactions[kind] = redactions.get(kind, 0) + count
                redacted_total += count
        prepared.append({"role": message["role"], "content": content})

    # Reserve the turn key BEFORE mutating the buffer: concurrent or replayed
    # writebacks for the same turn cannot double-buffer.
    store.reserve_dedupe_keys([turn_key], now=now)
    store.append_buffer_messages(buffer_key, turn_key, prepared, now=now)

    existing = store.get_buffer(buffer_key)
    added_chars = sum(len(message["content"]) for message in prepared)
    if existing is None:
        store.upsert_buffer(
            buffer_key=buffer_key,
            client=client,
            effective_user_id=scope.effective_user_id,
            session_key=session_id,
            turn_count=1,
            content_chars=added_chars,
            created_at=now,
            updated_at=now,
            idle_deadline=now + cfg.buffer_seconds,
        )
        state = store.get_buffer(buffer_key)
    else:
        store.upsert_buffer(
            buffer_key=buffer_key,
            client=client,
            effective_user_id=scope.effective_user_id,
            session_key=session_id,
            turn_count=existing["turn_count"] + 1,
            content_chars=existing["content_chars"] + added_chars,
            created_at=existing["created_at"],
            updated_at=now,
            idle_deadline=now + cfg.buffer_seconds,
        )
        state = store.get_buffer(buffer_key)

    receipt = WritebackReceipt(
        accepted=True,
        buffered=True,
        buffer_key=buffer_key,
        turn_key=turn_key,
        message_count=len(prepared),
        redactions=redactions,
        redacted_total=redacted_total,
    )
    if state is None:  # pragma: no cover - upsert just succeeded above
        return receipt

    reason = _should_flush(state, cfg, now)
    if reason is not None:
        report = flush_buffer(store, vectorizer, cfg, buffer_key, reason=reason, now=now)
        receipt.flushed = report.flushed
        receipt.chunks = report.chunks
        receipt.items_created = report.items_created
        receipt.items_duplicate = report.items_duplicate
        receipt.group_id = report.group_id
        receipt.flush_reason = report.reason
        receipt.buffered = not report.flushed
    return receipt
