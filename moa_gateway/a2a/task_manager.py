"""moa_gateway.a2a.task_manager — A2A task lifecycle + persistence.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT license):
  - source: src/lib/a2a/taskManager.ts (state machine, TTL, owner scoping)
  - source: src/lib/a2a/authenticate.ts (owner = hashed API-key identity,
    GHSA-jcm5-6wpp-wjj8 IDOR hardening)

Ported semantics:
  - State machine: submitted -> working -> completed | failed | cancelled
    (terminal states have no outgoing transitions).
  - UUID4 task ids, ISO-8601 timestamps, TTL expiration (default 5 minutes),
    event history for every state transition.
  - Owner scoping: a task carrying an owner is only visible/cancellable by the
    same owner; ownerless tasks stay visible to everyone. Not-found errors are
    deliberately indistinguishable from "exists but not yours" so an IDOR probe
    cannot enumerate task ids.

Divergence from OmniRoute (real persistence instead of in-memory Map):
  tasks are persisted to the gateway storage database in a self-created
  ``a2a_tasks`` table (CREATE TABLE IF NOT EXISTS — moa_gateway/storage.py is
  intentionally left untouched), so ``tasks/get`` works across process
  restarts as required by the M5 plan.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ============ Valid state transitions (OmniRoute taskManager.ts) ============

TaskState = str  # "submitted" | "working" | "completed" | "failed" | "cancelled"

VALID_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "submitted": ("working", "failed", "cancelled"),
    "working": ("completed", "failed", "cancelled"),
    "completed": (),
    "failed": (),
    "cancelled": (),
}

TERMINAL_STATES = ("completed", "failed", "cancelled")

# Self-owned persistence table. Basic SQL types only so the same DDL works on
# both backends supported by moa_gateway.storage (SQLite and PostgreSQL).
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS a2a_tasks (
    id TEXT PRIMARY KEY,
    skill TEXT NOT NULL,
    state TEXT NOT NULL,
    owner TEXT,
    input_json TEXT NOT NULL,
    artifacts_json TEXT NOT NULL,
    events_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
"""


class TaskTransitionError(Exception):
    """Raised when a task id cannot be found (or is not visible to caller)."""


class InvalidTransitionError(TaskTransitionError):
    """Raised when a state transition violates the state machine."""


@dataclass
class A2ATask:
    """One A2A task (OmniRoute A2ATask port)."""

    id: str
    skill: str
    state: TaskState
    input: dict[str, Any]
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    owner: str | None = None
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "skill": self.skill,
            "state": self.state,
            "input": self.input,
            "artifacts": self.artifacts,
            "events": self.events,
            "metadata": self.metadata,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "expiresAt": self.expires_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


class A2ATaskManager:
    """Task lifecycle manager persisted to the gateway storage database."""

    def __init__(self, ttl_minutes: float = 5.0, storage: Any | None = None):
        self.ttl = timedelta(minutes=ttl_minutes)
        self._storage = storage
        self._lock = threading.Lock()
        self._table_ready = False

    # ---------- storage plumbing ----------

    def _get_storage(self):
        if self._storage is not None:
            return self._storage
        # Lazy import: keeps this module importable before storage is up and
        # lets tests inject an isolated Storage instance.
        from ..storage import get_storage

        return get_storage()

    def _ensure_table(self) -> None:
        if self._table_ready:
            return
        with self._lock:
            if self._table_ready:
                return
            with self._get_storage().conn() as c:
                c.execute(_CREATE_TABLE_SQL)
            self._table_ready = True

    # ---------- CRUD ----------

    def create_task(
        self,
        skill: str,
        messages: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
        owner: str | None = None,
    ) -> A2ATask:
        self._ensure_table()
        now = datetime.now(timezone.utc)
        task = A2ATask(
            id=str(uuid.uuid4()),
            skill=skill,
            state="submitted",
            input={"skill": skill, "messages": messages, "metadata": metadata or {}},
            artifacts=[],
            events=[{"timestamp": now.isoformat(), "state": "submitted"}],
            metadata=dict(metadata or {}),
            owner=owner,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            expires_at=(now + self.ttl).isoformat(),
        )
        with self._get_storage().conn() as c:
            c.execute(
                "INSERT INTO a2a_tasks (id, skill, state, owner, input_json, "
                "artifacts_json, events_json, metadata_json, created_at, updated_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.id,
                    task.skill,
                    task.state,
                    task.owner,
                    json.dumps(task.input, ensure_ascii=False),
                    json.dumps(task.artifacts, ensure_ascii=False),
                    json.dumps(task.events, ensure_ascii=False),
                    json.dumps(task.metadata, ensure_ascii=False),
                    task.created_at,
                    task.updated_at,
                    task.expires_at,
                ),
            )
        return task

    def update_task(
        self,
        task_id: str,
        state: TaskState,
        artifacts: list[dict[str, Any]] | None = None,
        message: str | None = None,
    ) -> A2ATask:
        """Transition a task to a new state (validated state machine)."""
        self._ensure_table()
        with self._lock:
            task = self._load(task_id)
            if task is None:
                raise TaskTransitionError(f"Task {task_id} not found")
            valid = VALID_TRANSITIONS.get(task.state, ())
            if state not in valid:
                raise InvalidTransitionError(
                    f"Invalid transition: {task.state} -> {state}"
                )
            now = _now_iso()
            task.state = state
            task.updated_at = now
            event: dict[str, Any] = {"timestamp": now, "state": state}
            if message:
                event["message"] = message
            task.events.append(event)
            if artifacts:
                task.artifacts.extend(artifacts)
            with self._get_storage().conn() as c:
                c.execute(
                    "UPDATE a2a_tasks SET state = ?, artifacts_json = ?, "
                    "events_json = ?, updated_at = ? WHERE id = ?",
                    (
                        task.state,
                        json.dumps(task.artifacts, ensure_ascii=False),
                        json.dumps(task.events, ensure_ascii=False),
                        task.updated_at,
                        task.id,
                    ),
                )
            return task

    def cancel_task(self, task_id: str, owner: str | None = None) -> A2ATask:
        # Owner check BEFORE the mutation (OmniRoute GHSA-jcm5-6wpp-wjj8):
        # same not-found error as a missing task so IDOR probes learn nothing.
        task = self.get_task(task_id, owner)
        if task is None:
            raise TaskTransitionError(f"Task {task_id} not found")
        return self.update_task(task_id, "cancelled", message="Cancelled by client")

    # ---------- reads ----------

    @staticmethod
    def _visible(task: A2ATask, owner: str | None) -> bool:
        return task.owner is None or task.owner == owner

    def _expire_if_needed(self, task: A2ATask) -> None:
        """Mark an expired non-terminal task failed (lazy TTL, OmniRoute)."""
        if task.state in TERMINAL_STATES:
            return
        try:
            if _parse_iso(task.expires_at) >= datetime.now(timezone.utc):
                return
        except ValueError:
            return
        try:
            self.update_task(task.id, "failed", message="Task expired")
        except TaskTransitionError:
            pass

    def _load(self, task_id: str) -> A2ATask | None:
        with self._get_storage().conn() as c:
            row = c.execute(
                "SELECT id, skill, state, owner, input_json, artifacts_json, "
                "events_json, metadata_json, created_at, updated_at, expires_at "
                "FROM a2a_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return A2ATask(
            id=row["id"],
            skill=row["skill"],
            state=row["state"],
            owner=row["owner"],
            input=json.loads(row["input_json"] or "{}"),
            artifacts=json.loads(row["artifacts_json"] or "[]"),
            events=json.loads(row["events_json"] or "[]"),
            metadata=json.loads(row["metadata_json"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )

    def get_task(self, task_id: str, owner: str | None = None) -> A2ATask | None:
        self._ensure_table()
        task = self._load(task_id)
        if task is None or not self._visible(task, owner):
            return None
        self._expire_if_needed(task)
        current = self._load(task_id)
        if current is None or not self._visible(current, owner):
            return None
        return current

    def list_tasks(
        self,
        state: str | None = None,
        skill: str | None = None,
        owner: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[A2ATask]:
        self._ensure_table()
        query = "SELECT id FROM a2a_tasks"
        clauses: list[str] = []
        params: list[Any] = []
        if state:
            clauses.append("state = ?")
            params.append(state)
        if skill:
            clauses.append("skill = ?")
            params.append(skill)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, int(limit)), max(0, int(offset))])
        with self._get_storage().conn() as c:
            rows = c.execute(query, tuple(params)).fetchall()
        tasks: list[A2ATask] = []
        for row in rows:
            t = self._load(row["id"])
            if t is not None and self._visible(t, owner):
                tasks.append(t)
        return tasks

    def get_stats(self) -> dict[str, Any]:
        self._ensure_table()
        counts = {s: 0 for s in VALID_TRANSITIONS}
        with self._get_storage().conn() as c:
            rows = c.execute(
                "SELECT state, COUNT(*) AS n FROM a2a_tasks GROUP BY state"
            ).fetchall()
            last_row = c.execute(
                "SELECT MAX(updated_at) AS last_task_at FROM a2a_tasks"
            ).fetchone()
        total = 0
        for row in rows:
            if row["state"] in counts:
                counts[row["state"]] = int(row["n"])
                total += int(row["n"])
        return {
            "counts": counts,
            "total": total,
            "lastTaskAt": (last_row["last_task_at"] if last_row else None),
        }


# ============ Singleton ============

_manager: A2ATaskManager | None = None
_manager_lock = threading.Lock()


def get_task_manager() -> A2ATaskManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = A2ATaskManager()
        return _manager


def reset_task_manager() -> None:
    """Reset the singleton (test isolation)."""
    global _manager
    with _manager_lock:
        _manager = None
