"""quota_snapshots persistence with change detection (M2).

Self-created table (shared gateway files are never modified):

    quota_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    connection_id TEXT NOT NULL DEFAULT '',
                    captured_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    values_json TEXT NOT NULL,
                    change_key TEXT NOT NULL)

Ported semantics from OmniRoute (MIT) ``src/domain/quotaCache.ts`` —
``quotaSnapshotChanged``: persist only the FIRST observation of a state and
subsequent REAL changes; identical repeats are skipped. The row cap
(``settings.quota.max_snapshots``) is enforced by deleting the oldest rows
after each insert.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any

from .models import QuotaState

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS quota_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    connection_id TEXT NOT NULL DEFAULT '',
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL,
    values_json TEXT NOT NULL,
    change_key TEXT NOT NULL
)
"""
_CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_quota_snapshots_endpoint "
    "ON quota_snapshots(endpoint_id)"
)


def compute_change_key(state: QuotaState) -> str:
    """Normalized fingerprint of the observable state (status + values).

    Only fields a routing decision could care about participate: dimension,
    limit, used, remaining, reset_at, source. Timestamps deliberately do NOT
    — a refetch with identical numbers is not a change (upstream contract).
    """
    payload = [
        state.status,
        sorted(
            [
                [v.dimension, v.limit, v.used, v.remaining, v.reset_at, v.source]
                for v in state.values
            ],
            key=lambda row: row[0],
        ),
    ]
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]


class SnapshotStore:
    """Durable quota-snapshot history with change detection + row cap."""

    def __init__(self, max_snapshots: int | None = None) -> None:
        self._max_snapshots = max_snapshots  # None → read settings lazily
        self._last_change_key: dict[str, str] = {}
        self._table_ready = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------- settings

    def _max_rows(self) -> int:
        if self._max_snapshots is not None:
            return self._max_snapshots
        try:
            from ..config import get_settings

            return get_settings().quota.max_snapshots
        except Exception:
            return 5000

    def _storage(self):
        from ..storage import get_storage

        return get_storage()

    def _ensure_table(self) -> bool:
        if self._table_ready:
            return True
        try:
            with self._storage().conn() as conn:
                conn.execute(_CREATE_TABLE_SQL)
                conn.execute(_CREATE_INDEX_SQL)
                conn.commit()
            self._table_ready = True
            return True
        except Exception:
            logger.warning("quota_snapshots: storage unavailable", exc_info=True)
            return False

    # ----------------------------------------------------------------- api

    def record(self, state: QuotaState) -> bool:
        """Persist the state when it is the first observation or changed.

        Returns True when a row was written. Unchanged states are skipped
        (``quotaSnapshotChanged``); storage outages are logged, not raised.
        """
        endpoint_id = state.endpoint_id or state.connection_id or state.provider_id
        change_key = compute_change_key(state)
        with self._lock:
            last = self._last_change_key.get(endpoint_id)
            if last is None:
                last = self._load_last_change_key(endpoint_id)
            if last == change_key:
                return False
            self._last_change_key[endpoint_id] = change_key

        values_json = json.dumps(
            [v.model_dump(exclude_none=True) for v in state.values], ensure_ascii=False
        )
        if not self._ensure_table():
            return False
        try:
            with self._storage().conn() as conn:
                conn.execute(
                    "INSERT INTO quota_snapshots "
                    "(endpoint_id, provider_id, connection_id, captured_at, status, values_json, change_key) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        endpoint_id,
                        state.provider_id,
                        state.connection_id,
                        state.fetched_at,
                        state.status,
                        values_json,
                        change_key,
                    ),
                )
                max_rows = self._max_rows()
                conn.execute(
                    "DELETE FROM quota_snapshots WHERE id NOT IN "
                    "(SELECT id FROM quota_snapshots ORDER BY id DESC LIMIT ?)",
                    (max_rows,),
                )
                conn.commit()
            return True
        except Exception:
            logger.warning("quota_snapshots: insert failed for %s", endpoint_id, exc_info=True)
            return False

    def list(
        self, endpoint_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Newest-first snapshot rows (optionally filtered by endpoint)."""
        if not self._ensure_table():
            return []
        limit = max(1, min(int(limit), 1000))
        try:
            with self._storage().conn() as conn:
                if endpoint_id:
                    rows = conn.execute(
                        "SELECT id, endpoint_id, provider_id, connection_id, captured_at, "
                        "status, values_json, change_key FROM quota_snapshots "
                        "WHERE endpoint_id = ? ORDER BY id DESC LIMIT ?",
                        (endpoint_id, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT id, endpoint_id, provider_id, connection_id, captured_at, "
                        "status, values_json, change_key FROM quota_snapshots "
                        "ORDER BY id DESC LIMIT ?",
                        (limit,),
                    ).fetchall()
        except Exception:
            logger.warning("quota_snapshots: query failed", exc_info=True)
            return []
        result = []
        for row in rows:
            try:
                values = json.loads(row["values_json"])
            except (ValueError, TypeError):
                values = []
            result.append(
                {
                    "id": row["id"],
                    "endpoint_id": row["endpoint_id"],
                    "provider_id": row["provider_id"],
                    "connection_id": row["connection_id"],
                    "captured_at": row["captured_at"],
                    "status": row["status"],
                    "values": values,
                    "change_key": row["change_key"],
                }
            )
        return result

    def count(self) -> int:
        if not self._ensure_table():
            return 0
        try:
            with self._storage().conn() as conn:
                row = conn.execute("SELECT COUNT(*) AS n FROM quota_snapshots").fetchone()
            return int(row["n"]) if row else 0
        except Exception:
            return 0

    # ------------------------------------------------------------ internals

    def _load_last_change_key(self, endpoint_id: str) -> str | None:
        """Recover the latest change key from storage (restart safety)."""
        if not self._ensure_table():
            return None
        try:
            with self._storage().conn() as conn:
                row = conn.execute(
                    "SELECT change_key FROM quota_snapshots WHERE endpoint_id = ? "
                    "ORDER BY id DESC LIMIT 1",
                    (endpoint_id,),
                ).fetchone()
            return row["change_key"] if row else None
        except Exception:
            return None


_store: SnapshotStore | None = None
_store_lock = threading.Lock()


def get_snapshot_store() -> SnapshotStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = SnapshotStore()
        return _store


def reset_snapshot_store_for_tests() -> None:
    global _store
    with _store_lock:
        _store = None
