"""Memory persistence layer (self-managed SQLite tables).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT) stores
memories in the external MemoraX service; this port keeps the same data model
(items scoped by ``effective_user_id``, idempotent adds keyed by content hash,
writeback dedupe keys, turn state) but persists locally in SQLite.

The gateway's shared ``storage.py`` is intentionally NOT modified: this module
creates its own database file (``<DATA_DIR>/memory.db``) and its own tables
via ``CREATE TABLE IF NOT EXISTS``.  ``DATA_DIR`` is read lazily so test
isolation (conftest monkeypatch) applies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from .. import config as _cfg

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    effective_user_id TEXT NOT NULL,
    base_user_id TEXT NOT NULL,
    repository_slug TEXT NOT NULL,
    memory_type TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'assistant',
    source TEXT NOT NULL DEFAULT 'writeback',
    session_id TEXT,
    group_id TEXT,
    chunk_index INTEGER,
    chunk_count INTEGER,
    embedding TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (effective_user_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_memory_items_user
    ON memory_items (effective_user_id);

CREATE TABLE IF NOT EXISTS memory_turn_state (
    client TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    prompt TEXT,
    cwd TEXT,
    created_at REAL NOT NULL,
    PRIMARY KEY (client, session_id, turn_id)
);

CREATE TABLE IF NOT EXISTS memory_buffer_state (
    buffer_key TEXT PRIMARY KEY,
    client TEXT NOT NULL,
    effective_user_id TEXT NOT NULL,
    session_key TEXT NOT NULL,
    turn_count INTEGER NOT NULL,
    content_chars INTEGER NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    idle_deadline REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_buffer_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    buffer_key TEXT NOT NULL,
    turn_idempotency_key TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_buffer_messages_key
    ON memory_buffer_messages (buffer_key);

CREATE TABLE IF NOT EXISTS memory_dedupe_keys (
    key TEXT PRIMARY KEY,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_skill_reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client TEXT NOT NULL,
    session_id TEXT NOT NULL,
    turn_id TEXT,
    content TEXT NOT NULL,
    triggers TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""


def content_hash(effective_user_id: str, content: str) -> str:
    """Idempotency hash for a stored memory (user-scoped content digest)."""
    normalized = "\n".join(line.rstrip() for line in content.strip().splitlines()).strip()
    return hashlib.sha256(f"{effective_user_id}\x00{normalized}".encode("utf-8")).hexdigest()


class MemoryStore:
    """SQLite-backed store for memory items, turn state and writeback buffers."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = Path(db_path) if db_path else _cfg.DATA_DIR / "memory.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), timeout=30, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ items
    def insert_item(
        self,
        *,
        effective_user_id: str,
        base_user_id: str,
        repository_slug: str,
        memory_type: str,
        content: str,
        role: str = "assistant",
        source: str = "writeback",
        session_id: str | None = None,
        group_id: str | None = None,
        chunk_index: int | None = None,
        chunk_count: int | None = None,
        embedding: list[float] | None = None,
        now: float | None = None,
    ) -> tuple[int | None, bool]:
        """Idempotent insert. Returns ``(item_id, created)``; duplicates by
        (effective_user_id, content_hash) return the existing id and
        ``created=False``."""
        ts = now if now is not None else time.time()
        digest = content_hash(effective_user_id, content)
        embedding_json = json.dumps(embedding) if embedding else None
        with self._lock:
            try:
                cursor = self._conn.execute(
                    """
                    INSERT INTO memory_items (
                        effective_user_id, base_user_id, repository_slug, memory_type,
                        content, content_hash, role, source, session_id,
                        group_id, chunk_index, chunk_count, embedding,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        effective_user_id,
                        base_user_id,
                        repository_slug,
                        memory_type,
                        content,
                        digest,
                        role,
                        source,
                        session_id,
                        group_id,
                        chunk_index,
                        chunk_count,
                        embedding_json,
                        ts,
                        ts,
                    ),
                )
                self._conn.commit()
                return int(cursor.lastrowid), True
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    "SELECT id FROM memory_items WHERE effective_user_id = ? AND content_hash = ?",
                    (effective_user_id, digest),
                ).fetchone()
                return (int(row["id"]) if row else None), False

    def list_items(
        self,
        effective_user_id: str,
        *,
        memory_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_items WHERE effective_user_id = ?"
        params: list[Any] = [effective_user_id]
        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)
        query += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([int(limit), int(offset)])
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_items WHERE id = ?", (int(item_id),)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def delete_item(self, item_id: int, effective_user_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM memory_items WHERE id = ? AND effective_user_id = ?",
                (int(item_id), effective_user_id),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def count_items(self, effective_user_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memory_items WHERE effective_user_id = ?",
                (effective_user_id,),
            ).fetchone()
        return int(row["n"])

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> dict[str, Any]:
        embedding = None
        if row["embedding"]:
            try:
                embedding = json.loads(row["embedding"])
            except (TypeError, ValueError):
                embedding = None
        return {
            "id": int(row["id"]),
            "effective_user_id": row["effective_user_id"],
            "base_user_id": row["base_user_id"],
            "repository_slug": row["repository_slug"],
            "memory_type": row["memory_type"],
            "content": row["content"],
            "content_hash": row["content_hash"],
            "role": row["role"],
            "source": row["source"],
            "session_id": row["session_id"],
            "group_id": row["group_id"],
            "chunk_index": row["chunk_index"],
            "chunk_count": row["chunk_count"],
            "embedding": embedding,
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    # ------------------------------------------------------------- turn state
    def record_turn(
        self,
        *,
        client: str,
        session_id: str,
        turn_id: str,
        prompt: str | None,
        cwd: str | None,
        now: float | None = None,
    ) -> None:
        ts = now if now is not None else time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_turn_state (client, session_id, turn_id, prompt, cwd, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(client, session_id, turn_id) DO UPDATE SET
                    prompt = excluded.prompt,
                    cwd = excluded.cwd,
                    created_at = excluded.created_at
                """,
                (client, session_id, turn_id, prompt, cwd, ts),
            )
            self._conn.commit()

    def consume_turn(self, *, client: str, session_id: str, turn_id: str) -> dict[str, Any] | None:
        """Fetch and delete the turn state (MemoraX "consumed" disposition)."""
        if not turn_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_turn_state WHERE client = ? AND session_id = ? AND turn_id = ?",
                (client, session_id, turn_id),
            ).fetchone()
            if row is None:
                return None
            self._conn.execute(
                "DELETE FROM memory_turn_state WHERE client = ? AND session_id = ? AND turn_id = ?",
                (client, session_id, turn_id),
            )
            self._conn.commit()
        return {
            "client": row["client"],
            "session_id": row["session_id"],
            "turn_id": row["turn_id"],
            "prompt": row["prompt"],
            "cwd": row["cwd"],
            "created_at": float(row["created_at"]),
        }

    # ----------------------------------------------------------------- buffer
    def get_buffer(self, buffer_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM memory_buffer_state WHERE buffer_key = ?", (buffer_key,)
            ).fetchone()
        if row is None:
            return None
        return {
            "buffer_key": row["buffer_key"],
            "client": row["client"],
            "effective_user_id": row["effective_user_id"],
            "session_key": row["session_key"],
            "turn_count": int(row["turn_count"]),
            "content_chars": int(row["content_chars"]),
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
            "idle_deadline": float(row["idle_deadline"]),
        }

    def upsert_buffer(
        self,
        *,
        buffer_key: str,
        client: str,
        effective_user_id: str,
        session_key: str,
        turn_count: int,
        content_chars: int,
        created_at: float,
        updated_at: float,
        idle_deadline: float,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO memory_buffer_state (
                    buffer_key, client, effective_user_id, session_key,
                    turn_count, content_chars, created_at, updated_at, idle_deadline
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(buffer_key) DO UPDATE SET
                    turn_count = excluded.turn_count,
                    content_chars = excluded.content_chars,
                    updated_at = excluded.updated_at,
                    idle_deadline = excluded.idle_deadline
                """,
                (
                    buffer_key,
                    client,
                    effective_user_id,
                    session_key,
                    turn_count,
                    content_chars,
                    created_at,
                    updated_at,
                    idle_deadline,
                ),
            )
            self._conn.commit()

    def delete_buffer(self, buffer_key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM memory_buffer_state WHERE buffer_key = ?", (buffer_key,))
            self._conn.execute("DELETE FROM memory_buffer_messages WHERE buffer_key = ?", (buffer_key,))
            self._conn.commit()

    def buffer_messages(self, buffer_key: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM memory_buffer_messages WHERE buffer_key = ? ORDER BY id",
                (buffer_key,),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "turn_idempotency_key": row["turn_idempotency_key"],
                "role": row["role"],
                "content": row["content"],
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def append_buffer_messages(
        self,
        buffer_key: str,
        turn_idempotency_key: str,
        messages: list[dict[str, str]],
        now: float,
    ) -> None:
        with self._lock:
            self._conn.executemany(
                """
                INSERT INTO memory_buffer_messages
                    (buffer_key, turn_idempotency_key, role, content, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (buffer_key, turn_idempotency_key, message["role"], message["content"], now)
                    for message in messages
                ],
            )
            self._conn.commit()

    def all_buffer_keys(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute("SELECT buffer_key FROM memory_buffer_state").fetchall()
        return [row["buffer_key"] for row in rows]

    # ---------------------------------------------------------------- dedupe
    def has_dedupe_key(self, key: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM memory_dedupe_keys WHERE key = ?", (key,)
            ).fetchone()
        return row is not None

    def reserve_dedupe_keys(self, keys: list[str], now: float | None = None) -> None:
        ts = now if now is not None else time.time()
        with self._lock:
            self._conn.executemany(
                "INSERT OR IGNORE INTO memory_dedupe_keys (key, created_at) VALUES (?, ?)",
                [(key, ts) for key in keys],
            )
            self._conn.commit()

    # ------------------------------------------------------------- reminders
    def record_skill_reminder(
        self,
        *,
        client: str,
        session_id: str,
        turn_id: str | None,
        content: str,
        triggers: list[str],
        now: float | None = None,
    ) -> int:
        ts = now if now is not None else time.time()
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_skill_reminders
                    (client, session_id, turn_id, content, triggers, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (client, session_id, turn_id, content, json.dumps(triggers), ts),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_skill_reminders(self, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        query = "SELECT * FROM memory_skill_reminders"
        params: list[Any] = []
        if session_id:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [
            {
                "id": int(row["id"]),
                "client": row["client"],
                "session_id": row["session_id"],
                "turn_id": row["turn_id"],
                "content": row["content"],
                "triggers": json.loads(row["triggers"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]


# ---------------------------------------------------------------------------
# Process-wide store cache keyed by resolved DB path (tests monkeypatch
# DATA_DIR per test, so each test transparently gets its own store).
# ---------------------------------------------------------------------------
_stores: dict[str, MemoryStore] = {}
_stores_lock = threading.Lock()


def get_memory_store(db_path: Path | None = None) -> MemoryStore:
    path = Path(db_path) if db_path else _cfg.DATA_DIR / "memory.db"
    key = str(path)
    with _stores_lock:
        store = _stores.get(key)
        if store is None:
            store = MemoryStore(path)
            _stores[key] = store
        return store


def reset_memory_store() -> None:
    """Close and drop all cached stores (test isolation helper)."""
    with _stores_lock:
        for store in _stores.values():
            try:
                store.close()
            except Exception:  # pragma: no cover - already closed
                pass
        _stores.clear()
