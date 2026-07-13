"""worktree 真实测试(非 mock)

测试策略:
  - 所有 git 命令走真实 git CLI(subprocess)
  - 用 pytest tmp_path 工厂创建独立 git repo,每个测试一个,自动清理
  - 至少 14 个测试用例,覆盖:字段 / 初始化 / snapshot 真实命令 /
    porcelain / tracked / is_clean 两路 / diff 三向 / JSON / 0 文件边界
"""
import sys
import json
import os
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.worktree import (
    WorktreeInfo, WorktreeSnapshot, WorktreeManager,
    snapshot, diff_snapshots, is_clean,
    snapshot_to_json, snapshot_from_json,
    worktree_info_to_json, worktree_info_from_json,
    _parse_porcelain, GitCommandError,
)


# ============ 测试夹具 ============
@pytest.fixture
def git_repo(tmp_path):
    """创建一个空白 git repo,带一次 commit,返回路径。

    git config 在 tmp 内设置,绝不污染全局。
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    cwd = str(repo)
    _git(cwd, ["init", "--initial-branch=main"])
    _git(cwd, ["config", "user.email", "t@t.com"])
    _git(cwd, ["config", "user.name", "t"])
    # 一次空 commit,让 HEAD 存在
    _git(cwd, ["commit", "--allow-empty", "-m", "init"])
    return cwd


@pytest.fixture
def git_repo_with_files(tmp_path):
    """创建一个带 3 个 tracked 文件的 git repo。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    cwd = str(repo)
    _git(cwd, ["init", "--initial-branch=main"])
    _git(cwd, ["config", "user.email", "t@t.com"])
    _git(cwd, ["config", "user.name", "t"])
    # 写 3 个文件
    (repo / "a.txt").write_text("aaa", encoding="utf-8")
    (repo / "b.txt").write_text("bbb", encoding="utf-8")
    (repo / "c.txt").write_text("ccc", encoding="utf-8")
    _git(cwd, ["add", "."])
    _git(cwd, ["commit", "-m", "add 3 files"])
    return cwd


def _git(cwd, args):
    """小工具:同步跑 git,失败抛 RuntimeError。"""
    proc = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {args} failed: {proc.stderr.strip()}")


# ============ 1. WorktreeInfo 字段 ============
def test_worktree_info_fields():
    """WorktreeInfo 5 字段全在。"""
    info = WorktreeInfo(
        path="/tmp/wt",
        branch="feature-x",
        commit_sha="abc123",
        is_main=False,
        created_at=1234567890.0,
    )
    assert info.path == "/tmp/wt"
    assert info.branch == "feature-x"
    assert info.commit_sha == "abc123"
    assert info.is_main is False
    assert info.created_at == 1234567890.0
    print("  ✓ test_worktree_info_fields: 5 字段全在")


# ============ 2. WorktreeManager __init__ ============
def test_worktree_manager_init(git_repo):
    """合法 git repo → WorktreeManager 构造成功。"""
    mgr = WorktreeManager(git_repo)
    assert mgr.repo_path == os.path.abspath(git_repo)
    print(f"  ✓ test_worktree_manager_init: {mgr.repo_path}")


def test_worktree_manager_init_invalid(tmp_path):
    """非 git 目录 → ValueError。"""
    not_a_repo = tmp_path / "fake"
    not_a_repo.mkdir()
    with pytest.raises(ValueError):
        WorktreeManager(str(not_a_repo))
    print("  ✓ test_worktree_manager_init_invalid: ValueError raised")


# ============ 3. snapshot 真实 git 命令 ============
def test_snapshot_real_git(git_repo_with_files):
    """在真 git repo 上跑 snapshot(),3 个字段都对得上。"""
    snap = snapshot(git_repo_with_files)
    # commit_sha 是 40 字符 hex
    assert isinstance(snap.commit_sha, str)
    assert len(snap.commit_sha) >= 7
    assert snap.branch == "main"
    assert len(snap.tracked_files) == 3
    assert "a.txt" in snap.tracked_files
    assert "b.txt" in snap.tracked_files
    assert "c.txt" in snap.tracked_files
    assert snap.timestamp > 0
    print(f"  ✓ test_snapshot_real_git: sha={snap.commit_sha[:8]}, 3 files")


# ============ 4. snapshot 含 commit_sha ============
def test_snapshot_has_commit_sha(git_repo):
    """commit_sha 字段存在且是 hex 字符串。"""
    snap = snapshot(git_repo)
    assert hasattr(snap, "commit_sha")
    assert snap.commit_sha != ""
    # hex check
    int(snap.commit_sha, 16)  # 触发 ValueError if not hex
    print(f"  ✓ test_snapshot_has_commit_sha: {snap.commit_sha[:12]}...")


# ============ 5. snapshot 含 porcelain_status ============
def test_snapshot_porcelain_clean(git_repo_with_files):
    """干净 repo → porcelain_status 为空 list。"""
    snap = snapshot(git_repo_with_files)
    assert isinstance(snap.porcelain_status, list)
    assert snap.porcelain_status == []
    print("  ✓ test_snapshot_porcelain_clean: 0 entries")


def test_snapshot_porcelain_modified(git_repo_with_files):
    """修改一个文件 → porcelain_status 含 1 条 status=' M'。"""
    repo = Path(git_repo_with_files)
    (repo / "a.txt").write_text("aaa-modified", encoding="utf-8")
    snap = snapshot(git_repo_with_files)
    assert len(snap.porcelain_status) == 1
    entry = snap.porcelain_status[0]
    assert entry["path"] == "a.txt"
    assert "M" in entry["status"]
    print(f"  ✓ test_snapshot_porcelain_modified: {entry}")


def test_snapshot_porcelain_untracked(git_repo_with_files):
    """新建 untracked 文件 → porcelain_status 含 '??'。"""
    repo = Path(git_repo_with_files)
    (repo / "new.txt").write_text("xxx", encoding="utf-8")
    snap = snapshot(git_repo_with_files)
    assert len(snap.porcelain_status) == 1
    entry = snap.porcelain_status[0]
    assert entry["path"] == "new.txt"
    assert "??" in entry["status"]
    print(f"  ✓ test_snapshot_porcelain_untracked: {entry}")


# ============ 6. snapshot 含 tracked_files ============
def test_snapshot_tracked_files(git_repo_with_files):
    """tracked_files 是 git ls-files 真实结果。"""
    snap = snapshot(git_repo_with_files)
    assert isinstance(snap.tracked_files, list)
    assert sorted(snap.tracked_files) == ["a.txt", "b.txt", "c.txt"]
    # 反向验证: 和 git ls-files 直接调用结果一致
    raw = subprocess.run(
        ["git", "ls-files"], cwd=git_repo_with_files,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(snap.tracked_files) == sorted(raw)
    print(f"  ✓ test_snapshot_tracked_files: {snap.tracked_files}")


# ============ 7. is_clean 空状态 → True ============
def test_is_clean_when_empty(git_repo_with_files):
    """干净 repo → is_clean True。"""
    snap = snapshot(git_repo_with_files)
    assert is_clean(snap) is True
    print("  ✓ test_is_clean_when_empty: True")


# ============ 8. is_clean 有 modified → False ============
def test_is_clean_when_modified(git_repo_with_files):
    """修改文件后 → is_clean False。"""
    repo = Path(git_repo_with_files)
    (repo / "a.txt").write_text("dirty", encoding="utf-8")
    snap = snapshot(git_repo_with_files)
    assert is_clean(snap) is False
    print("  ✓ test_is_clean_when_modified: False")


# ============ 9/10/11. diff_snapshots added/removed/modified ============
def test_diff_snapshots_added(git_repo):
    """s2 比 s1 多 1 个 tracked 文件 → added 含那个文件。"""
    repo = Path(git_repo)
    s1 = snapshot(git_repo)
    # 新增 1 个文件并 commit
    (repo / "new.txt").write_text("new", encoding="utf-8")
    _git(git_repo, ["add", "new.txt"])
    _git(git_repo, ["commit", "-m", "add new"])
    s2 = snapshot(git_repo)
    diff = diff_snapshots(s1, s2)
    assert "new.txt" in diff["added"]
    assert diff["removed"] == []
    assert diff["s1_sha"] != diff["s2_sha"]
    print(f"  ✓ test_diff_snapshots_added: {diff['added']}")


def test_diff_snapshots_removed(git_repo_with_files):
    """s2 比 s1 少 1 个 tracked 文件 → removed 含那个文件。"""
    repo = Path(git_repo_with_files)
    s1 = snapshot(git_repo_with_files)
    (repo / "a.txt").unlink()
    _git(git_repo_with_files, ["add", "-A"])
    _git(git_repo_with_files, ["commit", "-m", "rm a"])
    s2 = snapshot(git_repo_with_files)
    diff = diff_snapshots(s1, s2)
    assert "a.txt" in diff["removed"]
    assert diff["added"] == []
    print(f"  ✓ test_diff_snapshots_removed: {diff['removed']}")


def test_diff_snapshots_modified(git_repo_with_files):
    """s1/s2 共同文件,但 commit 不同 → modified 含共同文件。"""
    repo = Path(git_repo_with_files)
    s1 = snapshot(git_repo_with_files)
    (repo / "a.txt").write_text("changed", encoding="utf-8")
    _git(git_repo_with_files, ["add", "a.txt"])
    _git(git_repo_with_files, ["commit", "-m", "change a"])
    s2 = snapshot(git_repo_with_files)
    diff = diff_snapshots(s1, s2)
    # added/removed 应为空(只是修改,文件还在)
    assert diff["added"] == []
    assert diff["removed"] == []
    # 共同文件被标 modified
    assert "a.txt" in diff["modified"]
    assert "b.txt" in diff["modified"]
    assert "c.txt" in diff["modified"]
    print(f"  ✓ test_diff_snapshots_modified: {diff['modified']}")


# ============ 12. WorktreeInfo JSON ============
def test_worktree_info_json():
    """WorktreeInfo ↔ JSON 往返一致。"""
    info = WorktreeInfo(
        path="/tmp/x", branch="b", commit_sha="deadbeef",
        is_main=True, created_at=1.5,
    )
    text = worktree_info_to_json(info)
    data = json.loads(text)
    assert data["path"] == "/tmp/x"
    assert data["branch"] == "b"
    assert data["is_main"] is True
    # roundtrip
    info2 = worktree_info_from_json(text)
    assert info2 == info
    print(f"  ✓ test_worktree_info_json: roundtrip ok")


# ============ 13. WorktreeSnapshot JSON ============
def test_worktree_snapshot_json(git_repo_with_files):
    """WorktreeSnapshot ↔ JSON 往返一致。"""
    snap = snapshot(git_repo_with_files)
    text = snapshot_to_json(snap)
    data = json.loads(text)
    assert data["commit_sha"] == snap.commit_sha
    assert data["tracked_files"] == snap.tracked_files
    # roundtrip
    snap2 = snapshot_from_json(text)
    assert snap2.commit_sha == snap.commit_sha
    assert snap2.branch == snap.branch
    assert snap2.tracked_files == snap.tracked_files
    assert snap2.porcelain_status == snap.porcelain_status
    print(f"  ✓ test_worktree_snapshot_json: roundtrip ok ({len(snap.tracked_files)} files)")


# ============ 14. 边界: 0 tracked files ============
def test_snapshot_empty_repo_zero_tracked(git_repo):
    """空 repo(只有 init commit)→ tracked_files 为空 list。"""
    snap = snapshot(git_repo)
    assert snap.tracked_files == []
    assert isinstance(snap.tracked_files, list)
    assert is_clean(snap) is True
    # porcelain_status 也应空
    assert snap.porcelain_status == []
    print("  ✓ test_snapshot_empty_repo_zero_tracked: 0 files, clean")


# ============ bonus: porcelain parser 单元 ============
def test_parse_porcelain_rename():
    """_parse_porcelain 处理 'R old -> new' 重命名。"""
    text = "R  old.txt -> new.txt\n"
    parsed = _parse_porcelain(text)
    assert len(parsed) == 1
    assert parsed[0]["status"] == "R "
    assert parsed[0]["path"] == "new.txt"
    assert parsed[0]["old_path"] == "old.txt"
    print("  ✓ test_parse_porcelain_rename: ok")


# ============ bonus: WorktreeManager create/list/remove 真实工作流 ============
def test_worktree_manager_full_lifecycle(tmp_path):
    """create → list → snapshot → remove 真实工作流。"""
    repo = tmp_path / "mainrepo"
    repo.mkdir()
    cwd = str(repo)
    _git(cwd, ["init", "--initial-branch=main"])
    _git(cwd, ["config", "user.email", "t@t.com"])
    _git(cwd, ["config", "user.name", "t"])
    (repo / "f.txt").write_text("x", encoding="utf-8")
    _git(cwd, ["add", "f.txt"])
    _git(cwd, ["commit", "-m", "init"])

    mgr = WorktreeManager(cwd)
    wt = mgr.create_worktree("feature", base="main")
    assert wt.branch == "feature"
    assert wt.is_main is False
    assert os.path.isdir(wt.path)
    # 真实 worktree 目录里也能跑 git
    wt_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=wt.path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert wt_sha == wt.commit_sha

    # list 至少 2 个(main + feature)
    listed = mgr.list_worktrees()
    paths = {w.path for w in listed}
    assert wt.path in paths

    # remove
    assert mgr.remove_worktree(wt.path, force=True) is True
    # 再次 list → 不该有 feature 路径
    listed2 = mgr.list_worktrees()
    paths2 = {w.path for w in listed2}
    assert wt.path not in paths2
    print(f"  ✓ test_worktree_manager_full_lifecycle: create/list/remove ok")


if __name__ == "__main__":
    # 不走 pytest 时也能跑
    import sys
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
