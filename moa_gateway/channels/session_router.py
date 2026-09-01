"""Channel session routing — channel keys, dedup and persisted bindings (M8).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/server/channel/channel_manager.rb`` — ``channel_key`` by
  binding mode (``:chat`` -> ``"platform:chat:CHAT_ID"``, ``:user``,
  ``:chat_user``), ``resolve_session`` / ``bind_key_to_session`` semantics,
  and ``DEDUP_WINDOW = 2.0`` identical-message suppression;
- ``restore_channel_bindings`` — bindings survive restarts. Here they persist
  in a self-created SQLite table (``CREATE TABLE IF NOT EXISTS``; storage.py
  untouched).

Session TTL comes from ``ChannelsConfig.session_ttl_s``: an idle binding older
than the TTL starts a fresh gateway session on the next message, matching the
"conversation went cold" expectation of IM users.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
import uuid

from .events import InboundEvent

logger = logging.getLogger(__name__)

#: OpenClacky channel_manager.rb DEDUP_WINDOW
DEDUP_WINDOW = 2.0

BINDING_MODES = (":chat", ":user", ":chat_user")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS channel_session_bindings (
        channel_key TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_csb_session ON channel_session_bindings(session_id)",
)


def channel_key(platform: str, event: InboundEvent, binding_mode: str = ":chat") -> str:
    """Port of OpenClacky ``channel_key(event)`` per binding mode."""
    if binding_mode == ":user":
        return f"{platform}:user:{event.user}"
    if binding_mode == ":chat_user":
        return f"{platform}:chat_user:{event.chat_id}:{event.user}"
    return f"{platform}:chat:{event.chat_id}"


class SessionRouter:
    """Maps channel conversations to gateway session ids (persisted)."""

    def __init__(self, binding_mode: str = ":chat"):
        if binding_mode not in BINDING_MODES:
            raise ValueError(f"binding_mode must be one of {BINDING_MODES}")
        self.binding_mode = binding_mode
        self._schema_ready = False
        self._dedup_lock = threading.Lock()
        #: channel_key -> (digest, monotonic time)
        self._last_message: dict[str, tuple[str, float]] = {}

    # ---------- storage ----------

    @property
    def _storage(self):
        from ..storage import get_storage

        return get_storage()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._storage.conn() as c:
            for stmt in _SCHEMA:
                c.execute(stmt)
        self._schema_ready = True

    @staticmethod
    def _ttl() -> float:
        from ..config import get_settings

        return get_settings().channels.session_ttl_s

    # ---------- dedup (OpenClacky DEDUP_WINDOW) ----------

    def is_duplicate(self, key: str, event: InboundEvent) -> bool:
        """True when the identical message repeats on the same channel key
        within DEDUP_WINDOW seconds (adapter-storm safety net)."""
        digest = hashlib.sha1(
            f"{event.text}|{event.user}".encode("utf-8")
        ).hexdigest()
        now = time.monotonic()
        with self._dedup_lock:
            prev = self._last_message.get(key)
            self._last_message[key] = (digest, now)
            return prev is not None and prev[0] == digest and (now - prev[1]) < DEDUP_WINDOW

    # ---------- binding resolution ----------

    def resolve_session(self, event: InboundEvent) -> str:
        """Return the gateway session id for this event's channel key.

        Creates (and persists) a new session when the key is unbound or the
        existing binding is stale beyond ``session_ttl_s``.
        """
        key = channel_key(event.channel, event, self.binding_mode)
        self._ensure_schema()
        now = time.time()
        with self._storage.conn() as c:
            row = c.execute(
                "SELECT session_id, updated_at FROM channel_session_bindings "
                "WHERE channel_key = ?",
                (key,),
            ).fetchone()
            ttl = self._ttl()
            if row is not None and ttl > 0 and (now - float(row["updated_at"])) > ttl:
                logger.info(
                    "channels: binding %s expired after %.0fs, starting fresh session",
                    key, now - float(row["updated_at"]),
                )
                row = None
            if row is not None:
                c.execute(
                    "UPDATE channel_session_bindings SET updated_at = ? WHERE channel_key = ?",
                    (now, key),
                )
                return str(row["session_id"])
            session_id = uuid.uuid4().hex
            c.execute(
                "INSERT OR REPLACE INTO channel_session_bindings "
                "(channel_key, session_id, platform, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, session_id, event.channel, now, now),
            )
            logger.info("channels: bound %s -> session %s", key, session_id)
            return session_id

    def bind(self, key: str, session_id: str) -> None:
        """Explicitly bind (or re-bind) a channel key to a gateway session."""
        self._ensure_schema()
        now = time.time()
        with self._storage.conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO channel_session_bindings "
                "(channel_key, session_id, platform, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, session_id, key.split(":", 1)[0], now, now),
            )

    def unbind(self, key: str) -> bool:
        self._ensure_schema()
        with self._storage.conn() as c:
            cur = c.execute(
                "DELETE FROM channel_session_bindings WHERE channel_key = ?", (key,)
            )
            return cur.rowcount > 0

    def list_bindings(self, platform: str | None = None) -> list[dict]:
        self._ensure_schema()
        sql = "SELECT * FROM channel_session_bindings"
        params: list = []
        if platform:
            sql += " WHERE platform = ?"
            params.append(platform)
        sql += " ORDER BY updated_at DESC"
        with self._storage.conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
