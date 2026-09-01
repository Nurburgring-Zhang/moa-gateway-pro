"""moa_gateway.workspace_memory — repository/workspace memory layer (M11).

Ported from MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``scripts/repo-memory/*``: repository knowledge is maintained in a
``.moa_memory`` directory of per-facet markdown artifacts, kept fresh by an
adaptive update policy and protected by a supervisor lock.

Modules:
- ``layout``     : ``.moa_memory`` directory structure + path resolution;
- ``facets``     : facet script mechanism — built-in facet scripts are real,
                   self-contained python programs executed as subprocesses;
- ``policy``     : content fingerprint + adaptive/every_commit/commit_count/
                   daily update decisions;
- ``supervisor`` : lock-file concurrency guard with stale reclaim;
- ``service``    : status/update orchestration used by ``routes/memory.py``.

Workspace memory is OFF by default (``MemoryConfig.workspace_enabled``).
"""

from .facets import (
    BUILTIN_FACETS,
    FacetRunResult,
    FacetSpec,
    artifact_filename,
    run_facet,
    script_filename,
    write_facet_scripts,
)
from .layout import (
    FACETS_DIR_NAME,
    INDEX_FILE_NAME,
    LOCK_FILE_NAME,
    MEMORY_DIR_NAME,
    STATE_FILE_NAME,
    STATE_SCHEMA_VERSION,
    WorkspaceLayout,
    ensure_layout,
    layout_for,
    resolve_workspace,
)
from .policy import (
    EXCLUDED_DIRS,
    KNOWN_POLICIES,
    POLICY_ADAPTIVE,
    POLICY_COMMIT_COUNT,
    POLICY_DAILY,
    POLICY_EVERY_COMMIT,
    compute_workspace_fingerprint,
    decide_update,
    load_state,
    save_state,
)
from .service import (
    WorkspaceMemoryService,
    get_workspace_memory_service,
    git_commit_count,
    reset_workspace_memory_service,
)
from .supervisor import acquire_lock, read_lock, release_lock

__all__ = [
    "BUILTIN_FACETS",
    "EXCLUDED_DIRS",
    "FACETS_DIR_NAME",
    "INDEX_FILE_NAME",
    "KNOWN_POLICIES",
    "LOCK_FILE_NAME",
    "MEMORY_DIR_NAME",
    "POLICY_ADAPTIVE",
    "POLICY_COMMIT_COUNT",
    "POLICY_DAILY",
    "POLICY_EVERY_COMMIT",
    "STATE_FILE_NAME",
    "STATE_SCHEMA_VERSION",
    "FacetRunResult",
    "FacetSpec",
    "WorkspaceLayout",
    "WorkspaceMemoryService",
    "acquire_lock",
    "artifact_filename",
    "compute_workspace_fingerprint",
    "decide_update",
    "ensure_layout",
    "get_workspace_memory_service",
    "git_commit_count",
    "layout_for",
    "load_state",
    "read_lock",
    "release_lock",
    "reset_workspace_memory_service",
    "resolve_workspace",
    "run_facet",
    "save_state",
    "script_filename",
    "write_facet_scripts",
]
