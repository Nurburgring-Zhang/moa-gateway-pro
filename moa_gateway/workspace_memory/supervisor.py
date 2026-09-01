"""Workspace memory update supervisor — lock-file concurrency guard (M11).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``scripts/repo-memory/repo-memory-job-supervisor.mjs``: only one repo-memory
update job may run per workspace; a lock file with owner metadata prevents
concurrent rebuilds, and stale locks (crashed jobs) are reclaimed by age.

Port semantics:
- exclusive creation via ``os.open(O_CREAT | O_EXCL)`` (atomic on all
  platforms supported by CPython);
- lock payload is JSON: ``{"pid", "started_at", "token", "holder"}``;
- a lock older than ``stale_after_seconds`` is considered crashed and
  reclaimed (age-based only — no cross-platform pid probing, which keeps
  the reclaim decision deterministic);
- ``release_lock`` removes the lock only when the token matches, so a job
  that lost its lock to a reclaim never deletes the new owner's lock.
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STALE_AFTER_SECONDS = 1800.0  # 30 minutes


def _read_payload(lock_path: Path) -> dict[str, Any] | None:
    try:
        raw = lock_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def read_lock(lock_path: Path) -> dict[str, Any] | None:
    """Return the current lock payload (or None when unlocked)."""
    payload = _read_payload(lock_path)
    if payload is None:
        return None
    return {
        "pid": payload.get("pid"),
        "started_at": payload.get("started_at"),
        "holder": payload.get("holder"),
        "age_seconds": round(time.time() - float(payload.get("started_at") or time.time()), 2),
    }


def acquire_lock(
    lock_path: Path,
    *,
    holder: str = "moa-gateway",
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
    now: float | None = None,
) -> str | None:
    """Try to acquire the update lock; returns a release token or None.

    Fail-closed: any ambiguity (unreadable existing lock held by someone
    fresh, races during reclaim) yields ``None`` — the caller must refuse to
    run the update rather than risk a concurrent rebuild.
    """
    ts = now if now is not None else time.time()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = json.dumps(
        {"pid": os.getpid(), "started_at": ts, "token": token, "holder": holder}
    )

    for _attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = _read_payload(lock_path)
            started_at = existing.get("started_at") if existing else None
            try:
                age = ts - float(started_at) if started_at is not None else None
            except (TypeError, ValueError):
                age = None
            if age is not None and age >= stale_after_seconds:
                logger.warning(
                    "workspace memory lock stale (age=%.0fs >= %.0fs); reclaiming %s",
                    age,
                    stale_after_seconds,
                    lock_path,
                )
                try:
                    lock_path.unlink()
                except OSError:
                    return None
                continue  # retry exclusive create once
            logger.info("workspace memory update already in progress: %s", lock_path)
            return None
        except OSError as exc:
            logger.warning("workspace memory lock create failed: %s (%s)", lock_path, exc)
            return None
        try:
            os.write(fd, payload.encode("utf-8"))
        finally:
            os.close(fd)
        logger.debug("workspace memory lock acquired: %s", lock_path)
        return token
    return None


def release_lock(lock_path: Path, token: str) -> bool:
    """Release the lock iff it still carries our token."""
    payload = _read_payload(lock_path)
    if payload is None:
        return False
    if payload.get("token") != token:
        logger.warning("workspace memory lock token mismatch; not releasing %s", lock_path)
        return False
    try:
        lock_path.unlink()
    except OSError as exc:
        logger.warning("workspace memory lock release failed: %s (%s)", lock_path, exc)
        return False
    return True
