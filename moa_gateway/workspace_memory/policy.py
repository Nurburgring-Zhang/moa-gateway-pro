"""Workspace memory update policy — adaptive rebuild decisions (M11).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``scripts/repo-memory/repo-memory-update-policy.mjs``: the update policy
decides whether repository memory must be rebuilt or can be skipped.
Supported policies (config ``settings.memory.workspace_update_policy``):

- ``adaptive``     : rebuild only when the workspace content fingerprint
                     changed since the last successful rebuild;
- ``every_commit`` : every trigger rebuilds (commit-hook semantics);
- ``commit_count`` : rebuild when at least ``workspace_commit_threshold``
                     new commits landed (falls back to the fingerprint when
                     the workspace has no git history);
- ``daily``        : rebuild at most once per ``workspace_cooldown_hours``.

The fingerprint is a deterministic content digest over the workspace files
(``.moa_memory`` itself is excluded so a rebuild never invalidates its own
fingerprint — that would defeat the adaptive skip).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .layout import STATE_SCHEMA_VERSION, WorkspaceLayout

logger = logging.getLogger(__name__)

POLICY_ADAPTIVE = "adaptive"
POLICY_EVERY_COMMIT = "every_commit"
POLICY_COMMIT_COUNT = "commit_count"
POLICY_DAILY = "daily"
KNOWN_POLICIES = (POLICY_ADAPTIVE, POLICY_EVERY_COMMIT, POLICY_COMMIT_COUNT, POLICY_DAILY)

# Directories never scanned for the fingerprint / by facet scripts.
EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".moa_memory",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".idea",
        ".vscode",
        ".hg",
        ".svn",
    }
)

_MAX_FINGERPRINT_FILES = 2000
_MAX_HASHED_FILE_BYTES = 131_072


def iter_workspace_files(root: Path, max_files: int = _MAX_FINGERPRINT_FILES):
    """Yield workspace files in deterministic (sorted) order."""
    collected: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in EXCLUDED_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            collected.append(Path(dirpath) / name)
            if len(collected) >= max_files:
                return collected
    return collected


def compute_workspace_fingerprint(root: Path) -> str:
    """Deterministic content fingerprint of a workspace.

    Files up to ``_MAX_HASHED_FILE_BYTES`` are content-hashed; larger files
    contribute path + size only.  Unreadable files contribute path + size so
    a permission glitch changes nothing silently.
    """
    digest = hashlib.sha256()
    for path in iter_workspace_files(root):
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:  # pragma: no cover - walked under root
            rel = path.name
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        if 0 <= size <= _MAX_HASHED_FILE_BYTES:
            try:
                content_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                content_hash = "-"
            digest.update(f"{rel}\x00{size}\x00{content_hash}\n".encode("utf-8"))
        else:
            digest.update(f"{rel}\x00{size}\x00large\n".encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------
def load_state(layout: WorkspaceLayout) -> dict[str, Any] | None:
    """Load the persisted update state (None when absent/corrupt)."""
    try:
        raw = layout.state_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        state = json.loads(raw)
    except ValueError:
        logger.warning("workspace memory state corrupt at %s; treating as absent", layout.state_path)
        return None
    if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA_VERSION:
        return None
    return state


def save_state(layout: WorkspaceLayout, state: dict[str, Any]) -> None:
    """Atomically persist update state (write-temp + rename)."""
    layout.memory_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = layout.state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp_path, layout.state_path)


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------
def decide_update(
    state: dict[str, Any] | None,
    *,
    policy: str,
    fingerprint: str,
    commit_count: int | None,
    force: bool,
    now: float,
    commit_threshold: int,
    cooldown_hours: float,
) -> tuple[str, str]:
    """Return ``(decision, reason)`` — ``rebuild`` or ``skip``.

    Mirrors MemoraX's policy resolver: explicit force wins; absent state
    always rebuilds; otherwise the configured policy applies.
    """
    if force:
        return "rebuild", "forced"
    if state is None:
        return "rebuild", "no_prior_state"
    if policy not in KNOWN_POLICIES:
        logger.warning("unknown workspace_update_policy %r; falling back to adaptive", policy)
        policy = POLICY_ADAPTIVE

    if policy == POLICY_EVERY_COMMIT:
        return "rebuild", "every_commit_policy"

    if policy == POLICY_COMMIT_COUNT:
        previous_commits = state.get("commit_count")
        if commit_count is not None and isinstance(previous_commits, int):
            if commit_count - previous_commits >= commit_threshold:
                return "rebuild", "commit_threshold_reached"
            return "skip", "below_commit_threshold"
        # No usable git history: fall back to the content fingerprint.
        if fingerprint != state.get("fingerprint"):
            return "rebuild", "fingerprint_changed_no_git"
        return "skip", "no_content_change"

    if policy == POLICY_DAILY:
        last_run = state.get("last_run_at") or 0.0
        if now - float(last_run) >= cooldown_hours * 3600.0:
            return "rebuild", "cooldown_elapsed"
        return "skip", "within_cooldown"

    # adaptive
    if fingerprint != state.get("fingerprint"):
        return "rebuild", "content_changed"
    return "skip", "no_content_change"
