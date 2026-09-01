"""Workspace memory service — orchestrates .moa_memory updates (M11).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``scripts/repo-memory/repo-memory-job-supervisor.mjs`` +
``scripts/repo-memory/repo-memory-update-policy.mjs`` +
``scripts/repo-memory/prepare_repo_memory.py``:

1. acquire the per-workspace supervisor lock (fail-closed);
2. compute the content fingerprint + git commit count;
3. let the update policy decide ``rebuild`` vs ``skip``;
4. on rebuild: materialize facet scripts, execute them as real
   subprocesses, collect markdown artifacts, write the consolidated
   ``index.md`` and persist ``state.json``;
5. always release the lock (token-checked).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import MemoryConfig, get_settings
from .facets import (
    BUILTIN_FACETS,
    artifact_filename,
    run_facet,
    script_filename,
    write_facet_scripts,
)
from .layout import STATE_SCHEMA_VERSION, WorkspaceLayout, ensure_layout, layout_for
from .policy import (
    compute_workspace_fingerprint,
    decide_update,
    load_state,
    save_state,
)
from .supervisor import acquire_lock, read_lock, release_lock

logger = logging.getLogger(__name__)

_INDEX_FACET_CHAR_CAP = 8000


def _memory_config() -> MemoryConfig:
    return get_settings().memory


def git_commit_count(workspace: Path) -> int | None:
    """Read-only ``git rev-list --count HEAD``; None when git is unusable."""
    if not (workspace / ".git").exists():
        return None
    git = shutil.which("git")
    if git is None:
        return None
    try:
        proc = subprocess.run(
            [git, "-C", str(workspace), "rev-list", "--count", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("git commit count failed for %s: %s", workspace, exc)
        return None
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


class WorkspaceMemoryService:
    """Status + update orchestration for one workspace at a time."""

    # ---------------------------------------------------------------- status
    def status(self, workspace: str | Path) -> dict[str, Any]:
        cfg = _memory_config()
        layout = layout_for(workspace)
        state = load_state(layout) if layout.state_path.exists() else None
        facets_status: list[dict[str, Any]] = []
        for spec in BUILTIN_FACETS:
            artifact = layout.facets_dir / artifact_filename(spec)
            script = layout.facets_dir / script_filename(spec)
            entry: dict[str, Any] = {
                "name": spec.name,
                "title": spec.title,
                "script_exists": script.is_file(),
                "artifact_exists": artifact.is_file(),
                "artifact_chars": 0,
            }
            if artifact.is_file():
                try:
                    entry["artifact_chars"] = len(artifact.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    entry["artifact_chars"] = -1
            facets_status.append(entry)
        return {
            "enabled": cfg.workspace_enabled,
            "workspace": str(layout.workspace),
            "memory_dir": str(layout.memory_dir),
            "memory_dir_exists": layout.memory_dir.is_dir(),
            "policy": cfg.workspace_update_policy,
            "commit_threshold": cfg.workspace_commit_threshold,
            "cooldown_hours": cfg.workspace_cooldown_hours,
            "lock": read_lock(layout.lock_path),
            "state": state,
            "facets": facets_status,
        }

    # ---------------------------------------------------------------- update
    def update(self, workspace: str | Path, *, force: bool = False) -> dict[str, Any]:
        cfg = _memory_config()
        layout = ensure_layout(layout_for(workspace))
        started = time.perf_counter()

        token = acquire_lock(layout.lock_path)
        if token is None:
            return {
                "status": "locked",
                "workspace": str(layout.workspace),
                "lock": read_lock(layout.lock_path),
            }
        try:
            return self._update_locked(layout, cfg, force=force, started=started)
        finally:
            release_lock(layout.lock_path, token)

    def _update_locked(
        self,
        layout: WorkspaceLayout,
        cfg: MemoryConfig,
        *,
        force: bool,
        started: float,
    ) -> dict[str, Any]:
        now = time.time()
        fingerprint = compute_workspace_fingerprint(layout.workspace)
        commit_count = git_commit_count(layout.workspace)
        state = load_state(layout)
        decision, reason = decide_update(
            state,
            policy=cfg.workspace_update_policy,
            fingerprint=fingerprint,
            commit_count=commit_count,
            force=force,
            now=now,
            commit_threshold=cfg.workspace_commit_threshold,
            cooldown_hours=cfg.workspace_cooldown_hours,
        )
        if decision == "skip":
            logger.info(
                "workspace memory update skipped: %s reason=%s", layout.workspace, reason
            )
            if state is not None:
                state["last_decision"] = decision
                state["last_reason"] = reason
                save_state(layout, state)
            return {
                "status": "skipped",
                "decision": decision,
                "reason": reason,
                "workspace": str(layout.workspace),
                "fingerprint": fingerprint,
                "commit_count": commit_count,
                "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            }

        # ---- rebuild -------------------------------------------------------
        write_facet_scripts(layout.facets_dir)
        facet_reports: list[dict[str, Any]] = []
        facet_contents: dict[str, str] = {}
        for spec in BUILTIN_FACETS:
            script_path = layout.facets_dir / script_filename(spec)
            artifact_path = layout.facets_dir / artifact_filename(spec)
            result = run_facet(spec, script_path, layout.workspace, artifact_path)
            facet_reports.append(result.to_dict())
            if result.ok:
                try:
                    facet_contents[spec.name] = artifact_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    facet_contents[spec.name] = ""
        succeeded = sum(1 for report in facet_reports if report["ok"])
        index_chars = self._write_index(layout, facet_contents, fingerprint, commit_count, now)

        new_state: dict[str, Any] = {
            "schema_version": STATE_SCHEMA_VERSION,
            "last_run_at": now,
            "last_decision": decision,
            "last_reason": reason,
            "fingerprint": fingerprint,
            "commit_count": commit_count,
            "policy": cfg.workspace_update_policy,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
            "facets": {
                report["name"]: {
                    "chars": report["artifact_chars"],
                    "sha256": report["artifact_sha256"],
                }
                for report in facet_reports
            },
        }
        save_state(layout, new_state)
        status = "rebuilt" if succeeded == len(facet_reports) else (
            "rebuilt_partial" if succeeded else "error"
        )
        logger.info(
            "workspace memory %s: %s facets_ok=%d/%d reason=%s",
            status,
            layout.workspace,
            succeeded,
            len(facet_reports),
            reason,
        )
        return {
            "status": status,
            "decision": decision,
            "reason": reason,
            "workspace": str(layout.workspace),
            "fingerprint": fingerprint,
            "commit_count": commit_count,
            "facets": facet_reports,
            "facets_ok": succeeded,
            "facets_total": len(facet_reports),
            "index_chars": index_chars,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 2),
        }

    # ----------------------------------------------------------------- index
    def _write_index(
        self,
        layout: WorkspaceLayout,
        facet_contents: dict[str, str],
        fingerprint: str,
        commit_count: int | None,
        now: float,
    ) -> int:
        """Write the consolidated index.md and return its char count."""
        lines = [
            "# Workspace Memory Index",
            "",
            f"- Workspace: `{layout.workspace}`",
            f"- Generated: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))}",
            f"- Content fingerprint: `{fingerprint[:16]}...`",
            f"- Git commits: {commit_count if commit_count is not None else 'n/a'}",
            f"- Facets: {', '.join(facet_contents) if facet_contents else 'none'}",
            "",
        ]
        for spec in BUILTIN_FACETS:
            content = facet_contents.get(spec.name)
            if not content:
                continue
            lines.append("---")
            lines.append("")
            lines.append(f"# Facet: {spec.title}")
            lines.append("")
            truncated = content[:_INDEX_FACET_CHAR_CAP]
            if len(content) > _INDEX_FACET_CHAR_CAP:
                truncated += f"\n\n...[truncated; full artifact in facets/{artifact_filename(spec)}]"
            lines.append(truncated.rstrip())
            lines.append("")
        text = "\n".join(lines)
        layout.index_path.write_text(text, encoding="utf-8")
        return len(text)


# ---------------------------------------------------------------------------
_service: WorkspaceMemoryService | None = None


def get_workspace_memory_service() -> WorkspaceMemoryService:
    global _service
    if _service is None:
        _service = WorkspaceMemoryService()
    return _service


def reset_workspace_memory_service() -> None:
    """Test isolation helper."""
    global _service
    _service = None
