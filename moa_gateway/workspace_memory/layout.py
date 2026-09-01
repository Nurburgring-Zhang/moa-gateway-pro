"""Workspace memory layout — the ``.moa_memory`` knowledge directory (M11).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``scripts/repo-memory/prepare_repo_memory.py`` and
``scripts/repo-memory/git_commit_facets.py``: repository knowledge lives in
a ``.repo_memory`` directory with one markdown artifact per *facet* and a
consolidated index.  This port uses the same layout under ``.moa_memory``:

    <workspace>/.moa_memory/
        index.md            consolidated memory document
        state.json          update state (fingerprint, commit count, ...)
        .update.lock        supervisor lock file (see supervisor.py)
        facets/
            <facet>.py      facet script (real, self-contained python)
            <facet>.md      markdown artifact produced by the script
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MEMORY_DIR_NAME = ".moa_memory"
FACETS_DIR_NAME = "facets"
INDEX_FILE_NAME = "index.md"
STATE_FILE_NAME = "state.json"
LOCK_FILE_NAME = ".update.lock"

STATE_SCHEMA_VERSION = "moa-workspace-memory.v1"


@dataclass(frozen=True)
class WorkspaceLayout:
    """Resolved paths of one workspace's ``.moa_memory`` directory."""

    workspace: Path
    memory_dir: Path
    facets_dir: Path
    index_path: Path
    state_path: Path
    lock_path: Path


def resolve_workspace(path: str | Path) -> Path:
    """Resolve and validate a workspace path (must be an existing directory).

    Raises ``ValueError`` with an operator-readable message otherwise.
    """
    try:
        workspace = Path(path).expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"unresolvable workspace path: {path!r} ({exc})") from exc
    if not workspace.exists():
        raise ValueError(f"workspace does not exist: {workspace}")
    if not workspace.is_dir():
        raise ValueError(f"workspace is not a directory: {workspace}")
    return workspace


def layout_for(workspace: str | Path) -> WorkspaceLayout:
    """Compute the layout for a workspace (does NOT create anything)."""
    root = resolve_workspace(workspace)
    memory_dir = root / MEMORY_DIR_NAME
    return WorkspaceLayout(
        workspace=root,
        memory_dir=memory_dir,
        facets_dir=memory_dir / FACETS_DIR_NAME,
        index_path=memory_dir / INDEX_FILE_NAME,
        state_path=memory_dir / STATE_FILE_NAME,
        lock_path=memory_dir / LOCK_FILE_NAME,
    )


def ensure_layout(layout: WorkspaceLayout) -> WorkspaceLayout:
    """Create the ``.moa_memory`` directory tree (idempotent)."""
    layout.memory_dir.mkdir(parents=True, exist_ok=True)
    layout.facets_dir.mkdir(parents=True, exist_ok=True)
    return layout
