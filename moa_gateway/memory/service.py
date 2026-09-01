"""Memory service orchestration — MemoraX Code turn coordinator port.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT):

- turn lifecycle (turn-start records turn state, writeback consumes it and
  pairs the assistant answer with the original prompt) from
  ``packages/ts/memorax-code-backend/src/memory/turn-coordinator.ts``;
- automatic retrieval / writeback enablement checks and skip reasons from
  ``.../memory/automatic-retrieval.ts`` and ``.../memory/automatic-writeback.ts``;
- skill-reminder recording from ``.../memory/reminder-trace-recorder.ts``.

This module wires the pure pipeline modules (hook_protocol, scope, retrieval,
writeback, store, vectorizer) together and exposes:

- the three hook-protocol handlers used by ``routes/memory.py``;
- REST-facing helpers (recall / list / delete);
- a clean assistant integration API (``recall_for_turn`` /
  ``queue_writeback``): pure-function-style entry points that never raise
  into the caller and never mutate any assistant module.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

from ..config import MemoryConfig, get_settings
from .classifier import MEMORY_TYPES, normalize_memory_type
from .hook_protocol import turn_correlation_id
from .retrieval import RecallResult, hybrid_recall
from .scope import MemoryScope, effective_user_id, resolve_memory_scope
from .store import MemoryStore, get_memory_store
from .vectorizer import DenseVectorizer
from .writeback import (
    WritebackReceipt,
    enqueue_writeback,
    extract_turn_messages,
    sweep_expired_buffers,
    turn_idempotency_key,
)

logger = logging.getLogger(__name__)


def _memory_config() -> MemoryConfig:
    return get_settings().memory


class MemoryService:
    """Orchestrates recall, writeback and skill reminders for one process."""

    def __init__(self, store: MemoryStore | None = None, vectorizer: DenseVectorizer | None = None):
        self._explicit_store = store
        self._explicit_vectorizer = vectorizer
        self._vectorizer = vectorizer
        self._lock = threading.Lock()

    # ------------------------------------------------------------ components
    @property
    def store(self) -> MemoryStore:
        return self._explicit_store if self._explicit_store is not None else get_memory_store()

    @property
    def vectorizer(self) -> DenseVectorizer:
        if self._vectorizer is None:
            with self._lock:
                if self._vectorizer is None:
                    self._vectorizer = DenseVectorizer()
        return self._vectorizer

    # ------------------------------------------------------------ scope helpers
    def scope_for(
        self, base_user_id: str, *, cwd: str | None = None, repository_slug: str | None = None
    ) -> MemoryScope | None:
        """Resolve a scope either from a workspace path or an explicit slug."""
        if repository_slug:
            slug = repository_slug.strip()
            base = (base_user_id or "").strip()
            if not slug or not base:
                return None
            return MemoryScope(
                schema_version="workspace-memory-scope.v1",
                base_user_id=base,
                effective_user_id=effective_user_id(base, slug),
                repository_key=hashlib.sha256(f"slug:{slug}".encode("utf-8")).hexdigest(),
                repository_slug=slug,
                scope_kind="explicit-slug",
                identity_source="api-argument",
                bound_workspace_root=None,
            )
        return resolve_memory_scope(cwd, base_user_id)

    # ============================================================ HOOK PATH
    def handle_turn_start(self, command: dict[str, Any], base_user_id: str) -> dict[str, Any]:
        """MemoraX turn-start hook: record turn state + recall injection."""
        cfg = _memory_config()
        client = command["client"]
        session_id = command["sessionId"]
        correlation = turn_correlation_id(command)
        prompt = command.get("prompt") or ""

        if not cfg.retrieval_enabled and not cfg.writeback_enabled:
            return {"status": "skipped", "reason": "disabled", "retrieved": False}

        scope = self.scope_for(base_user_id, cwd=command.get("cwd"))
        if scope is None:
            return {"status": "skipped", "reason": "scope_unresolved", "retrieved": False}

        # Turn state enables writeback association even when retrieval itself
        # is disabled (MemoraX turn-coordinator semantics).
        if cfg.writeback_enabled:
            self.store.record_turn(
                client=client,
                session_id=session_id,
                turn_id=correlation,
                prompt=prompt,
                cwd=command.get("cwd"),
            )

        if not cfg.retrieval_enabled:
            return {"status": "skipped", "reason": "retrieval_disabled", "retrieved": False}

        result = hybrid_recall(
            self.store,
            self.vectorizer,
            query=prompt,
            effective_user_id=scope.effective_user_id,
            cfg=cfg,
            retrieval_enabled=True,
        )
        payload = result.to_dict()
        payload["status"] = "ok"
        payload["scope"] = {
            "effective_user_id": scope.effective_user_id,
            "repository_slug": scope.repository_slug,
            "scope_kind": scope.scope_kind,
        }
        return payload

    def handle_writeback(self, command: dict[str, Any], base_user_id: str) -> dict[str, Any]:
        """MemoraX writeback hook: redact -> buffer -> maybe flush."""
        cfg = _memory_config()
        if not cfg.writeback_enabled:
            return {"status": "skipped", "reason": "writeback_disabled", "accepted": False}

        client = command["client"]
        session_id = command["sessionId"]
        correlation = turn_correlation_id(command)

        scope = self.scope_for(base_user_id, cwd=command.get("cwd"))
        if scope is None:
            return {"status": "skipped", "reason": "scope_unresolved", "accepted": False}

        messages = extract_turn_messages(command)
        # Turn association: pair the assistant answer with the turn-start
        # prompt consumed from turn state (MemoraX turn-coordinator).
        turn_state = self.store.consume_turn(
            client=client, session_id=session_id, turn_id=correlation
        )
        turn_associated = turn_state is not None
        if turn_state and turn_state.get("prompt"):
            messages = [{"role": "user", "content": turn_state["prompt"]}, *messages]

        receipt = enqueue_writeback(
            self.store,
            self.vectorizer,
            cfg,
            client=client,
            session_id=session_id,
            correlation_id=correlation,
            scope=scope,
            messages=messages,
            now=time.time(),
        )
        payload = receipt.to_dict()
        payload["status"] = "ok"
        payload["turn_associated"] = turn_associated
        payload["scope"] = {
            "effective_user_id": scope.effective_user_id,
            "repository_slug": scope.repository_slug,
        }
        return payload

    def handle_skill_reminder(self, command: dict[str, Any], base_user_id: str) -> dict[str, Any]:
        """MemoraX skill-reminder hook: persist the reminder trace."""
        reminder_id = self.store.record_skill_reminder(
            client=command["client"],
            session_id=command["sessionId"],
            turn_id=turn_correlation_id(command) or None,
            content=command["content"],
            triggers=list(command["triggers"]),
        )
        logger.info(
            "memory skill reminder recorded: client=%s session=%s triggers=%s base_user=%s",
            command["client"],
            command["sessionId"],
            command["triggers"],
            base_user_id,
        )
        return {
            "status": "ok",
            "recorded": True,
            "id": reminder_id,
            "triggers": list(command["triggers"]),
        }

    # ============================================================ REST PATH
    def recall(
        self,
        *,
        query: str,
        base_user_id: str,
        repository_slug: str | None = None,
        cwd: str | None = None,
    ) -> RecallResult:
        """Run the recall recipe for an explicit scope (GET /v1/memory/recall)."""
        cfg = _memory_config()
        scope = self.scope_for(base_user_id, cwd=cwd, repository_slug=repository_slug)
        if scope is None:
            return RecallResult(retrieved=False, skip_reason="scope_unresolved", backend=self.vectorizer.backend)
        return hybrid_recall(
            self.store,
            self.vectorizer,
            query=query,
            effective_user_id=scope.effective_user_id,
            cfg=cfg,
            retrieval_enabled=True,  # explicit REST recall is an operator action
        )

    def list_items(
        self,
        *,
        base_user_id: str,
        repository_slug: str,
        memory_type: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        if memory_type:
            memory_type = normalize_memory_type(memory_type)
        items = self.store.list_items(
            effective_user_id(base_user_id, repository_slug),
            memory_type=memory_type,
            limit=limit,
            offset=offset,
        )
        # Embeddings are internal state; keep API payloads lean.
        for item in items:
            item.pop("embedding", None)
        return items

    def delete_item(self, *, item_id: int, base_user_id: str, repository_slug: str) -> bool:
        return self.store.delete_item(
            item_id, effective_user_id(base_user_id, repository_slug)
        )

    def count_items(self, *, base_user_id: str, repository_slug: str) -> int:
        return self.store.count_items(effective_user_id(base_user_id, repository_slug))

    def list_skill_reminders(self, session_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_skill_reminders(session_id=session_id, limit=limit)

    def sweep(self) -> list[dict[str, Any]]:
        """Flush expired writeback buffers (called opportunistically)."""
        cfg = _memory_config()
        if not cfg.writeback_enabled:
            return []
        reports = sweep_expired_buffers(self.store, self.vectorizer, cfg, now=time.time())
        return [report.to_dict() for report in reports]

    # ================================================ ASSISTANT INTEGRATION
    def recall_for_turn(
        self,
        prompt: str,
        *,
        base_user_id: str,
        repository_slug: str | None = None,
        cwd: str | None = None,
    ) -> str | None:
        """Clean assistant-facing recall API.

        Returns the ``<memories>`` XML context string to prepend to the turn,
        or ``None`` when retrieval is disabled, the scope cannot be resolved
        or nothing relevant was found.  Never raises: any internal failure is
        logged and swallowed so the conversational path is never disturbed.
        """
        try:
            cfg = _memory_config()
            if not cfg.retrieval_enabled:
                return None
            result = self.recall(
                query=prompt, base_user_id=base_user_id, repository_slug=repository_slug, cwd=cwd
            )
            if result.retrieved and result.context:
                return result.context
            return None
        except Exception:  # integration API contract: never break the caller
            logger.exception("memory recall_for_turn failed; returning None")
            return None

    def queue_writeback(
        self,
        *,
        base_user_id: str,
        repository_slug: str,
        session_id: str,
        messages: list[dict[str, str]],
        correlation_id: str | None = None,
        client: str = "gateway",
    ) -> dict[str, Any]:
        """Clean assistant-facing writeback API.

        Accepts plain ``[{role, content}, ...]`` messages, runs them through
        the same redact -> buffer -> chunk -> store pipeline as the hook
        path, and returns a JSON-safe receipt dict.  Never raises.
        """
        try:
            cfg = _memory_config()
            if not cfg.writeback_enabled:
                return {"status": "skipped", "reason": "writeback_disabled", "accepted": False}
            scope = self.scope_for(base_user_id, repository_slug=repository_slug)
            if scope is None:
                return {"status": "skipped", "reason": "scope_unresolved", "accepted": False}

            normalized: list[dict[str, str]] = []
            for raw in messages or []:
                if not isinstance(raw, dict):
                    continue
                role = str(raw.get("role") or "").strip().lower()
                content = raw.get("content")
                if role in ("user", "assistant", "system", "tool") and isinstance(content, str):
                    normalized.append({"role": role, "content": content})
            if not normalized:
                return {"status": "skipped", "reason": "no_messages", "accepted": False}

            correlation = correlation_id or hashlib.sha256(
                "\x00".join(f"{m['role']}:{m['content']}" for m in normalized).encode("utf-8")
            ).hexdigest()[:32]
            receipt = enqueue_writeback(
                self.store,
                self.vectorizer,
                cfg,
                client=client,
                session_id=session_id,
                correlation_id=correlation,
                scope=scope,
                messages=normalized,
                now=time.time(),
            )
            payload = receipt.to_dict()
            payload["status"] = "ok"
            payload["turn_key"] = turn_idempotency_key(client, session_id, correlation)
            return payload
        except Exception:  # integration API contract: never break the caller
            logger.exception("memory queue_writeback failed; returning error receipt")
            return {"status": "error", "accepted": False}


# ---------------------------------------------------------------------------
# Process-wide singleton (reset by tests via reset_memory_service()).
# ---------------------------------------------------------------------------
_service: MemoryService | None = None
_service_lock = threading.Lock()


def get_memory_service() -> MemoryService:
    global _service
    with _service_lock:
        if _service is None:
            _service = MemoryService()
        return _service


def reset_memory_service() -> None:
    """Drop the cached singleton (test isolation helper)."""
    global _service
    with _service_lock:
        _service = None


__all__ = [
    "MEMORY_TYPES",
    "MemoryService",
    "WritebackReceipt",
    "get_memory_service",
    "reset_memory_service",
]
