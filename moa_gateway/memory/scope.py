"""Memory scope model — ported from MemoraX Code.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``packages/ts/memorax-code-backend/src/repository/scope.ts``.

Ported concept: a *workspace memory scope* isolates memory per
(base user, repository/workspace) pair.  The effective user id that keys
all storage and retrieval is ``f"{base_user_id}@{repository_slug}"`` so
that the same human working in two different repositories keeps two fully
isolated memory namespaces.

The original resolves Git identity by reading Git metadata *without*
executing Git.  This port keeps that read-only discipline: it inspects the
``.git`` marker file/directory directly and never shells out, so scope
derivation is deterministic and safe in restricted environments.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "workspace-memory-scope.v1"

# Scope kinds (MemoraX ``RepositoryMemoryScopeKind``).
SCOPE_GIT = "git-repository"
SCOPE_LOCAL = "local-directory"

# Identity sources (MemoraX ``RepositoryMemoryIdentitySource`` subset that
# are meaningful without executing Git).
IDENTITY_GIT_MARKER = "git-common-dir"
IDENTITY_WORKSPACE_DIR = "workspace-directory"


@dataclass(frozen=True)
class MemoryScope:
    """Resolved workspace memory scope.

    Mirrors MemoraX ``RepositoryMemoryScope`` but flattened for the gateway.
    """

    schema_version: str
    base_user_id: str
    effective_user_id: str
    repository_key: str
    repository_slug: str
    scope_kind: str
    identity_source: str
    bound_workspace_root: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "base_user_id": self.base_user_id,
            "effective_user_id": self.effective_user_id,
            "repository_key": self.repository_key,
            "repository_slug": self.repository_slug,
            "scope_kind": self.scope_kind,
            "identity_source": self.identity_source,
            "bound_workspace_root": self.bound_workspace_root,
        }


def _slugify(value: str) -> str:
    """Produce a filesystem/identity-safe slug from a workspace name."""
    cleaned = "".join(ch if (ch.isalnum() or ch in "-_.") else "-" for ch in value.strip())
    cleaned = cleaned.strip("-_.")
    return cleaned or "workspace"


def _identity_key(source: str, identifier: str) -> str:
    """Stable identity key = hash(source + canonical identifier).

    Mirrors MemoraX ``identityKey`` which binds the identity *source* to the
    canonical identifier so two different sources never collide.
    """
    digest = hashlib.sha256(f"{source}:{identifier}".encode("utf-8")).hexdigest()
    return f"{source}:{digest[:32]}"


def _detect_git_root(workspace: Path) -> Path | None:
    """Walk upward looking for a ``.git`` marker (file or dir), read-only.

    Returns the directory that *contains* the ``.git`` marker, i.e. the repo
    top-level.  Does not execute Git.
    """
    try:
        current = workspace.resolve()
    except OSError:
        return None
    for _ in range(64):  # bounded ascent
        marker = current / ".git"
        if marker.exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def resolve_memory_scope(
    workspace_root: str | Path | None,
    base_user_id: str,
) -> MemoryScope | None:
    """Resolve the memory scope for a workspace + base user.

    Returns ``None`` (fail-closed) when the base user id is empty or the
    workspace cannot be resolved, mirroring MemoraX's fail-closed scope
    resolution.
    """
    base = (base_user_id or "").strip()
    if not base:
        logger.warning("memory scope: empty base_user_id rejected (fail-closed)")
        return None

    if workspace_root is None or str(workspace_root).strip() == "":
        # Project-less scope: isolate under a stable pseudo-repository.
        slug = _slugify(base) + "-general"
        return MemoryScope(
            schema_version=SCHEMA_VERSION,
            base_user_id=base,
            effective_user_id=f"{base}@{slug}",
            repository_key=_identity_key("projectless", slug),
            repository_slug=slug,
            scope_kind=SCOPE_LOCAL,
            identity_source="projectless",
            bound_workspace_root=None,
        )

    try:
        workspace = Path(workspace_root).expanduser().resolve()
    except OSError:
        logger.warning("memory scope: unresolvable workspace %r (fail-closed)", workspace_root)
        return None

    git_root = _detect_git_root(workspace) if workspace.is_dir() else None
    if git_root is not None:
        slug = _slugify(git_root.name)
        return MemoryScope(
            schema_version=SCHEMA_VERSION,
            base_user_id=base,
            effective_user_id=f"{base}@{slug}",
            repository_key=_identity_key(IDENTITY_GIT_MARKER, str(git_root)),
            repository_slug=slug,
            scope_kind=SCOPE_GIT,
            identity_source=IDENTITY_GIT_MARKER,
            bound_workspace_root=str(git_root),
        )

    # Local-directory scope (no Git marker).
    slug = _slugify(workspace.name)
    return MemoryScope(
        schema_version=SCHEMA_VERSION,
        base_user_id=base,
        effective_user_id=f"{base}@{slug}",
        repository_key=_identity_key(IDENTITY_WORKSPACE_DIR, str(workspace)),
        repository_slug=slug,
        scope_kind=SCOPE_LOCAL,
        identity_source=IDENTITY_WORKSPACE_DIR,
        bound_workspace_root=str(workspace),
    )


def effective_user_id(base_user_id: str, repository_slug: str) -> str:
    """Compose the effective user id exactly as MemoraX does.

    ``effectiveUserId = baseUserId + "@" + repositorySlug``.
    """
    return f"{base_user_id}@{repository_slug}"
