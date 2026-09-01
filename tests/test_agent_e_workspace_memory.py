"""tests/test_agent_e_workspace_memory.py — M11 workspace/repository memory.

Covers moa_gateway/workspace_memory/* and the M11 endpoints of
moa_gateway/routes/memory.py (ported from MemoraX Code,
https://github.com/memorax-ai/memorax-code, MIT license).

Zero test doubles: facet scripts are real python programs executed as real
subprocesses against real temporary workspaces; the supervisor lock, the
content fingerprint and the policy decisions all run production code.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from moa_gateway.config import Settings
from moa_gateway.workspace_memory.facets import (
    BUILTIN_FACETS,
    artifact_filename,
    run_facet,
    script_filename,
    write_facet_scripts,
)
from moa_gateway.workspace_memory.layout import (
    MEMORY_DIR_NAME,
    ensure_layout,
    layout_for,
)
from moa_gateway.workspace_memory.policy import (
    POLICY_ADAPTIVE,
    POLICY_COMMIT_COUNT,
    POLICY_DAILY,
    POLICY_EVERY_COMMIT,
    compute_workspace_fingerprint,
    decide_update,
    load_state,
    save_state,
)
from moa_gateway.workspace_memory.service import (
    get_workspace_memory_service,
    git_commit_count,
    reset_workspace_memory_service,
)
from moa_gateway.workspace_memory.supervisor import (
    acquire_lock,
    read_lock,
    release_lock,
)

API_KEY = "wsmem-test-key-001"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


# ============ fixtures ============


@pytest.fixture(autouse=True)
def _reset_wsmem_singleton():
    reset_workspace_memory_service()
    yield
    reset_workspace_memory_service()


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "demo-repo"
    ws.mkdir()
    (ws / "README.md").write_text(
        "# Demo Project\n\nA demo used for workspace memory tests.\nTODO: document API\n",
        encoding="utf-8",
    )
    (ws / "app.py").write_text(
        "# TODO: refactor this module\nVALUE = 1  # FIXME: naming\n", encoding="utf-8"
    )
    (ws / "pyproject.toml").write_text(
        "[tool.ruff]\nline-length = 110\n", encoding="utf-8"
    )
    return ws


@pytest.fixture
def ws_settings(monkeypatch):
    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        },
        memory={"workspace_enabled": True},
    )
    monkeypatch.setattr("moa_gateway.config._settings", settings)
    return settings


@pytest.fixture
def app(ws_settings):
    from moa_gateway.routes.memory import router

    application = FastAPI()
    application.include_router(router)
    return application


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://wsmem.test") as ac:
        yield ac


# ============ layout ============


def test_layout_resolves_and_ensures(workspace):
    layout = layout_for(workspace)
    assert layout.memory_dir == workspace / MEMORY_DIR_NAME
    assert not layout.memory_dir.exists()
    ensure_layout(layout)
    assert layout.memory_dir.is_dir()
    assert layout.facets_dir.is_dir()


def test_layout_rejects_bad_paths(tmp_path):
    with pytest.raises(ValueError):
        layout_for(tmp_path / "does-not-exist")
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        layout_for(file_path)


# ============ fingerprint ============


def test_fingerprint_deterministic_and_content_sensitive(workspace):
    fp1 = compute_workspace_fingerprint(workspace)
    fp2 = compute_workspace_fingerprint(workspace)
    assert fp1 == fp2
    (workspace / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    fp3 = compute_workspace_fingerprint(workspace)
    assert fp3 != fp1


def test_fingerprint_ignores_moa_memory_artifacts(workspace):
    layout = ensure_layout(layout_for(workspace))
    fp1 = compute_workspace_fingerprint(workspace)
    (layout.facets_dir / "project-overview.md").write_text("# artifact\n", encoding="utf-8")
    (layout.index_path).write_text("index\n", encoding="utf-8")
    fp2 = compute_workspace_fingerprint(workspace)
    assert fp1 == fp2  # rebuilds never invalidate their own fingerprint


# ============ policy ============


def _state(fingerprint="fp-1", commit_count=10, last_run_at=0.0):
    return {
        "schema_version": "moa-workspace-memory.v1",
        "fingerprint": fingerprint,
        "commit_count": commit_count,
        "last_run_at": last_run_at,
    }


def test_policy_rebuilds_without_prior_state():
    decision, reason = decide_update(
        None, policy=POLICY_ADAPTIVE, fingerprint="fp", commit_count=None,
        force=False, now=1000.0, commit_threshold=5, cooldown_hours=24.0,
    )
    assert decision == "rebuild" and reason == "no_prior_state"


def test_policy_force_overrides_skip():
    decision, reason = decide_update(
        _state(), policy=POLICY_ADAPTIVE, fingerprint="fp-1", commit_count=10,
        force=True, now=1000.0, commit_threshold=5, cooldown_hours=24.0,
    )
    assert decision == "rebuild" and reason == "forced"


def test_policy_adaptive_skip_and_rebuild():
    kwargs = dict(policy=POLICY_ADAPTIVE, commit_count=None, force=False,
                  now=1000.0, commit_threshold=5, cooldown_hours=24.0)
    decision, reason = decide_update(_state(fingerprint="fp-1"), fingerprint="fp-1", **kwargs)
    assert decision == "skip" and reason == "no_content_change"
    decision2, reason2 = decide_update(_state(fingerprint="fp-1"), fingerprint="fp-2", **kwargs)
    assert decision2 == "rebuild" and reason2 == "content_changed"


def test_policy_every_commit_always_rebuilds():
    decision, reason = decide_update(
        _state(), policy=POLICY_EVERY_COMMIT, fingerprint="fp-1", commit_count=10,
        force=False, now=1000.0, commit_threshold=5, cooldown_hours=24.0,
    )
    assert decision == "rebuild" and reason == "every_commit_policy"


def test_policy_commit_count_threshold():
    kwargs = dict(policy=POLICY_COMMIT_COUNT, force=False, now=1000.0,
                  commit_threshold=5, cooldown_hours=24.0)
    decision, reason = decide_update(
        _state(commit_count=10), fingerprint="fp-1", commit_count=14, **kwargs
    )
    assert decision == "skip" and reason == "below_commit_threshold"
    decision2, reason2 = decide_update(
        _state(commit_count=10), fingerprint="fp-1", commit_count=15, **kwargs
    )
    assert decision2 == "rebuild" and reason2 == "commit_threshold_reached"
    # no git history -> fingerprint fallback
    decision3, reason3 = decide_update(
        _state(commit_count=None), fingerprint="fp-2", commit_count=None, **kwargs
    )
    assert decision3 == "rebuild" and reason3 == "fingerprint_changed_no_git"


def test_policy_daily_cooldown():
    kwargs = dict(policy=POLICY_DAILY, fingerprint="fp-1", commit_count=None,
                  force=False, commit_threshold=5, cooldown_hours=24.0)
    decision, reason = decide_update(
        _state(last_run_at=1000.0), now=1000.0 + 3600, **kwargs
    )
    assert decision == "skip" and reason == "within_cooldown"
    decision2, reason2 = decide_update(
        _state(last_run_at=1000.0), now=1000.0 + 25 * 3600, **kwargs
    )
    assert decision2 == "rebuild" and reason2 == "cooldown_elapsed"


def test_state_save_and_load_roundtrip(workspace):
    layout = ensure_layout(layout_for(workspace))
    state = _state(fingerprint="abc")
    save_state(layout, state)
    loaded = load_state(layout)
    assert loaded is not None
    assert loaded["fingerprint"] == "abc"


def test_state_load_rejects_corrupt_or_unknown_schema(workspace):
    layout = ensure_layout(layout_for(workspace))
    layout.state_path.write_text("{not json", encoding="utf-8")
    assert load_state(layout) is None
    layout.state_path.write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
    assert load_state(layout) is None


# ============ supervisor lock ============


def test_lock_acquire_blocks_second_and_release(workspace):
    layout = ensure_layout(layout_for(workspace))
    token = acquire_lock(layout.lock_path)
    assert token is not None
    assert read_lock(layout.lock_path) is not None
    # second acquire fails while held
    assert acquire_lock(layout.lock_path) is None
    assert release_lock(layout.lock_path, token) is True
    assert read_lock(layout.lock_path) is None
    # after release, acquire works again
    token2 = acquire_lock(layout.lock_path)
    assert token2 is not None
    release_lock(layout.lock_path, token2)


def test_lock_release_with_wrong_token_refused(workspace):
    layout = ensure_layout(layout_for(workspace))
    token = acquire_lock(layout.lock_path)
    assert release_lock(layout.lock_path, "someone-elses-token") is False
    assert read_lock(layout.lock_path) is not None  # still held
    release_lock(layout.lock_path, token)


def test_lock_stale_reclaim(workspace):
    layout = ensure_layout(layout_for(workspace))
    token = acquire_lock(layout.lock_path)
    assert token is not None
    # fresh contender cannot take a fresh lock...
    assert acquire_lock(layout.lock_path, stale_after_seconds=1800) is None
    # simulate a crashed job: backdate the lock's started_at by 2 hours
    payload = json.loads(layout.lock_path.read_text(encoding="utf-8"))
    payload["started_at"] = time.time() - 7200
    layout.lock_path.write_text(json.dumps(payload), encoding="utf-8")
    # ...but a 2h-old lock is stale and gets reclaimed
    token2 = acquire_lock(layout.lock_path, stale_after_seconds=1800)
    assert token2 is not None
    assert token2 != token
    release_lock(layout.lock_path, token2)


# ============ facet mechanism ============


def test_facet_scripts_materialize_and_compile(workspace):
    import py_compile

    layout = ensure_layout(layout_for(workspace))
    written = write_facet_scripts(layout.facets_dir)
    assert len(written) == len(BUILTIN_FACETS) == 4
    for script_path in written:
        assert script_path.is_file()
        py_compile.compile(str(script_path), doraise=True)  # valid python


def test_run_facet_project_overview_produces_real_artifact(workspace):
    layout = ensure_layout(layout_for(workspace))
    write_facet_scripts(layout.facets_dir)
    spec = next(s for s in BUILTIN_FACETS if s.name == "project-overview")
    result = run_facet(
        spec,
        layout.facets_dir / script_filename(spec),
        workspace,
        layout.facets_dir / artifact_filename(spec),
    )
    assert result.ok is True and result.exit_code == 0
    content = (layout.facets_dir / artifact_filename(spec)).read_text(encoding="utf-8")
    assert "# Project Overview" in content
    assert "README excerpt" in content
    assert "Demo Project" in content  # real README text, not boilerplate
    assert result.artifact_chars == len(content)


def test_run_facet_open_questions_finds_real_markers(workspace):
    layout = ensure_layout(layout_for(workspace))
    write_facet_scripts(layout.facets_dir)
    spec = next(s for s in BUILTIN_FACETS if s.name == "open-questions")
    result = run_facet(
        spec,
        layout.facets_dir / script_filename(spec),
        workspace,
        layout.facets_dir / artifact_filename(spec),
    )
    assert result.ok is True
    content = (layout.facets_dir / artifact_filename(spec)).read_text(encoding="utf-8")
    assert "TODO" in content
    assert "app.py" in content
    assert "FIXME" in content


# ============ service ============


def test_service_full_rebuild_cycle(ws_settings, workspace):
    service = get_workspace_memory_service()
    report = service.update(workspace)
    assert report["status"] == "rebuilt"
    assert report["decision"] == "rebuild"
    assert report["reason"] == "no_prior_state"
    assert report["facets_ok"] == 4 and report["facets_total"] == 4
    assert report["index_chars"] > 0
    layout = layout_for(workspace)
    assert layout.index_path.is_file()
    index = layout.index_path.read_text(encoding="utf-8")
    assert "Workspace Memory Index" in index
    assert "Facet: Conventions" in index
    state = load_state(layout)
    assert state is not None
    assert state["fingerprint"] == report["fingerprint"]
    assert state["last_decision"] == "rebuild"
    for facet in report["facets"]:
        assert facet["artifact_sha256"] != ""


def test_service_second_update_skips_adaptive(ws_settings, workspace):
    service = get_workspace_memory_service()
    assert service.update(workspace)["status"] == "rebuilt"
    report2 = service.update(workspace)
    assert report2["status"] == "skipped"
    assert report2["reason"] == "no_content_change"
    # changing content re-enables rebuild
    (workspace / "new_module.py").write_text("NEW = True\n", encoding="utf-8")
    report3 = service.update(workspace)
    assert report3["status"] == "rebuilt"
    assert report3["reason"] == "content_changed"


def test_service_force_rebuilds_even_when_unchanged(ws_settings, workspace):
    service = get_workspace_memory_service()
    service.update(workspace)
    report = service.update(workspace, force=True)
    assert report["status"] == "rebuilt"
    assert report["reason"] == "forced"


def test_service_update_blocked_while_locked(ws_settings, workspace):
    layout = ensure_layout(layout_for(workspace))
    token = acquire_lock(layout.lock_path)
    assert token is not None
    report = get_workspace_memory_service().update(workspace)
    assert report["status"] == "locked"
    assert report["lock"] is not None
    release_lock(layout.lock_path, token)


def test_service_status_reports_facets_state_and_lock(ws_settings, workspace):
    service = get_workspace_memory_service()
    pre = service.status(workspace)
    assert pre["enabled"] is True
    assert pre["memory_dir_exists"] is False
    service.update(workspace)
    post = service.status(workspace)
    assert post["memory_dir_exists"] is True
    assert post["lock"] is None  # released after update
    assert post["state"]["last_decision"] == "rebuild"
    assert len(post["facets"]) == 4
    assert all(f["artifact_exists"] for f in post["facets"])
    assert all(f["script_exists"] for f in post["facets"])


def test_git_commit_count_none_for_non_git(workspace):
    assert git_commit_count(workspace) is None


# ============ HTTP endpoints (M11) ============


async def test_workspace_status_disabled_by_default(client, workspace, monkeypatch):
    import moa_gateway.config as _cfgmod

    _cfgmod._settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SuperStr0ng!Pass#2024",
            "jwt_secret": "test-secret-long-enough-for-hs256-signing-key-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [API_KEY],
        }
    )
    resp = await client.get(
        "/v1/workspace-memory/status", params={"path": str(workspace)}, headers=AUTH
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["enabled"] is False
    assert payload["status"] == "disabled"


async def test_workspace_update_and_status_cycle_via_http(client, workspace):
    resp = await client.post(
        "/v1/workspace-memory/update",
        json={"path": str(workspace), "force": False},
        headers=AUTH,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["status"] == "rebuilt"
    assert payload["facets_ok"] == 4

    resp2 = await client.post(
        "/v1/workspace-memory/update", json={"path": str(workspace)}, headers=AUTH
    )
    assert resp2.json()["status"] == "skipped"

    resp3 = await client.get(
        "/v1/workspace-memory/status", params={"path": str(workspace)}, headers=AUTH
    )
    status = resp3.json()
    assert status["enabled"] is True
    assert status["state"]["last_decision"] == "skip"
    assert len(status["facets"]) == 4


async def test_workspace_update_requires_auth(client, workspace):
    resp = await client.post(
        "/v1/workspace-memory/update", json={"path": str(workspace)}
    )
    assert resp.status_code == 401


async def test_workspace_update_bad_path_400(client, tmp_path):
    resp = await client.post(
        "/v1/workspace-memory/update",
        json={"path": str(tmp_path / "ghost")},
        headers=AUTH,
    )
    assert resp.status_code == 400


async def test_workspace_status_bad_path_400(client, tmp_path):
    resp = await client.get(
        "/v1/workspace-memory/status",
        params={"path": str(tmp_path / "ghost")},
        headers=AUTH,
    )
    assert resp.status_code == 400
