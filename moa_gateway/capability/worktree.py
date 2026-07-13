"""worktree — Worktree 隔离基元 + Snapshot/Diff (来自 06 moai-adk-multiagent)

核心能力:
  1. WorktreeInfo 数据模型: path / branch / commit_sha / is_main / created_at
  2. WorktreeSnapshot 数据模型: commit_sha / branch / tracked_files / porcelain_status / timestamp
  3. WorktreeManager: 基于真实 git 命令的 worktree 创建/列举/删除
  4. snapshot(): git rev-parse + status --porcelain + ls-files 真实快照
  5. diff_snapshots: 两份快照的文件级 diff (added / removed / modified)
  6. is_clean: 基于 porcelain_status 的空判断
  7. JSON 序列化 (to_dict / from_dict)

设计原则:
  - 全部走真实 git CLI (subprocess.run, 无 mock, 无 hardcoded)
  - 任何 git 失败 → 抛 GitCommandError, 不静默
  - porcelain 解析遵循 git status --porcelain 官方格式 (XY path)
  - diff 用 set diff O(n) 而非嵌套 list, 大 repo 也稳
  - WorktreeManager 默认 cwd 是 repo 根, 所有子命令显式 cwd 避免污染
"""
from __future__ import annotations
import json
import os
import subprocess
import time
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Set, Tuple


# ============ 异常 ============
class GitCommandError(RuntimeError):
    """git 命令执行失败时抛"""


# ============ 工具函数 ============
def _run_git(repo_path: str, args: List[str], check: bool = True, timeout: float = 10.0) -> str:
    """统一封装 git 命令。

    - repo_path 必须存在(由调用方保证,这里不校验)
    - 默认 check=True: 非零退出码 → GitCommandError
    - 返回 stdout (stripped), stderr 在异常信息里携带
    - 修 P0-8: 加 timeout 防 subprocess.run 阻塞 event loop(慢盘/hang 的 fsmonitor)
    """
    cmd = ["git"] + args
    # 修 P0-8: 关 terminal prompt + 关 optional lock,防 hang
    env = {
        **__import__("os").environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as e:
        raise GitCommandError(f"git not found on PATH: {e}") from e
    except subprocess.TimeoutExpired as e:
        # 修 P0-8: 超时转 GitCommandError,不 crash
        raise GitCommandError(
            f"git {' '.join(args)} timeout after {timeout}s"
        ) from e

    if check and proc.returncode != 0:
        raise GitCommandError(
            f"git {' '.join(args)} failed (rc={proc.returncode}): "
            f"{proc.stderr.strip()}"
        )
    return proc.stdout


# ============ 数据模型 ============
@dataclass
class WorktreeInfo:
    """单个 worktree 的元信息。"""
    path: str
    branch: str
    commit_sha: str
    is_main: bool
    created_at: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorktreeInfo":
        return cls(**d)


@dataclass
class WorktreeSnapshot:
    """一个 worktree 在某个时刻的完整状态快照。"""
    commit_sha: str
    branch: str
    tracked_files: List[str] = field(default_factory=list)
    porcelain_status: List[Dict[str, str]] = field(default_factory=list)
    timestamp: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorktreeSnapshot":
        return cls(**d)


# ============ WorktreeManager ============
class WorktreeManager:
    """基于 git worktree 命令的隔离基元。

    使用方式:
        mgr = WorktreeManager("/path/to/repo")
        wt = mgr.create_worktree("feature-x", base="main")
        # ... 在 wt.path 上工作
        mgr.remove_worktree(wt.path, force=True)
    """

    def __init__(self, repo_path: str) -> None:
        self.repo_path = os.path.abspath(repo_path)
        # 验证是 git repo
        try:
            _run_git(self.repo_path, ["rev-parse", "--git-dir"])
        except GitCommandError as e:
            raise ValueError(f"not a git repo: {self.repo_path} ({e})") from e

    # ---- private helpers ----
    def _rev_parse_head(self) -> str:
        return _run_git(self.repo_path, ["rev-parse", "HEAD"]).strip()

    def _current_branch(self) -> str:
        try:
            return _run_git(self.repo_path, ["symbolic-ref", "--short", "HEAD"]).strip()
        except GitCommandError:
            # detached HEAD
            return "HEAD"

    def _worktree_root(self) -> str:
        return _run_git(self.repo_path, ["rev-parse", "--show-toplevel"]).strip()

    # ---- public API ----
    def create_worktree(
        self,
        branch: str,
        base: str = "main",
        path: Optional[str] = None,
    ) -> WorktreeInfo:
        """基于 base 创建一个新 branch + worktree。

        - branch 必须是新名字(不能已存在),否则 git 会拒绝
        - path 不传则用 ../<basename(repo)>.wt-<branch> 自动生成
        """
        if path is None:
            repo_basename = os.path.basename(self.repo_path.rstrip(os.sep)) or "repo"
            safe_branch = branch.replace("/", "-").replace("\\", "-")
            path = os.path.abspath(
                os.path.join(
                    os.path.dirname(self.repo_path),
                    f"{repo_basename}.wt-{safe_branch}",
                )
            )

        # -b: 新建分支; 后续是 base ref
        _run_git(self.repo_path, ["worktree", "add", "-b", branch, path, base])

        # 拿到新 worktree 的 HEAD sha(在 path 子目录里执行)
        try:
            sha = _run_git(path, ["rev-parse", "HEAD"]).strip()
        except GitCommandError:
            sha = ""

        is_main = (os.path.normpath(path) == os.path.normpath(self._worktree_root()))

        return WorktreeInfo(
            path=os.path.abspath(path),
            branch=branch,
            commit_sha=sha,
            is_main=is_main,
            created_at=time.time(),
        )

    def list_worktrees(self) -> List[WorktreeInfo]:
        """git worktree list --porcelain 解析。"""
        out = _run_git(self.repo_path, ["worktree", "list", "--porcelain"])
        return _parse_worktree_list(out, main_root=self._worktree_root())

    def remove_worktree(self, path: str, force: bool = False) -> bool:
        """删除指定 path 的 worktree。返回 True 成功, False 没找到/失败。

        注意: remove 不会删 branch, branch 留着供后续 prune。
        """
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(path)
        try:
            _run_git(self.repo_path, args)
            return True
        except GitCommandError:
            return False


def _parse_worktree_list(porcelain: str, main_root: str) -> List[WorktreeInfo]:
    """git worktree list --porcelain 输出解析。

    格式: 多段 record,以空行分隔,每段:
      worktree <绝对路径>
      HEAD <sha>
      branch <refs/heads/xxx>    (detached 时为 detached)
    """
    worktrees: List[WorktreeInfo] = []
    cur: Dict[str, str] = {}
    for raw_line in porcelain.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if cur:
                worktrees.append(_worktree_from_record(cur, main_root))
                cur = {}
            continue
        if line.startswith("worktree "):
            cur["path"] = line[len("worktree "):].strip()
        elif line.startswith("HEAD "):
            cur["sha"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            cur["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            cur["branch"] = "HEAD"
    # 收尾
    if cur:
        worktrees.append(_worktree_from_record(cur, main_root))
    return worktrees


def _worktree_from_record(rec: Dict[str, str], main_root: str) -> WorktreeInfo:
    path = rec.get("path", "")
    return WorktreeInfo(
        path=os.path.abspath(path),
        branch=rec.get("branch", "HEAD"),
        commit_sha=rec.get("sha", ""),
        is_main=(os.path.normpath(path) == os.path.normpath(main_root)),
        created_at=0.0,  # git worktree list 不返回时间,这里留 0 占位
    )


# ============ Snapshot / Diff 纯函数 ============
def _parse_porcelain(text: str) -> List[Dict[str, str]]:
    """git status --porcelain 单行解析为 dict 列表。

    官方格式: XY<space>path  (重命名时 XY<space>old -> new)
    XY 是两字符状态码,例如:
      "M " = staged modified
      " M" = unstaged modified
      "A " = added
      "??" = untracked
    """
    result: List[Dict[str, str]] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r")
        if len(line) < 3:
            continue
        xy = line[:2]
        rest = line[3:]  # skip XY + space
        # renamed/copied: "old -> new" 形式
        if " -> " in rest:
            old, new = rest.split(" -> ", 1)
            entry = {
                "status": xy,
                "path": new,
                "old_path": old,
            }
        else:
            entry = {
                "status": xy,
                "path": rest,
            }
        result.append(entry)
    return result


def snapshot(repo_path: str) -> WorktreeSnapshot:
    """对指定 repo 路径做一次完整快照。

    调 3 个真实 git 命令:
      - rev-parse HEAD     → commit_sha
      - status --porcelain → porcelain_status
      - ls-files           → tracked_files
    """
    repo_path = os.path.abspath(repo_path)
    # 验证是 git repo
    _run_git(repo_path, ["rev-parse", "--git-dir"])

    sha = _run_git(repo_path, ["rev-parse", "HEAD"]).strip()
    porcelain_text = _run_git(repo_path, ["status", "--porcelain"])
    ls_files_text = _run_git(repo_path, ["ls-files"])

    try:
        branch = _run_git(repo_path, ["symbolic-ref", "--short", "HEAD"]).strip()
    except GitCommandError:
        branch = "HEAD"

    tracked = [ln.rstrip("\r") for ln in ls_files_text.splitlines() if ln.strip()]

    return WorktreeSnapshot(
        commit_sha=sha,
        branch=branch,
        tracked_files=tracked,
        porcelain_status=_parse_porcelain(porcelain_text),
        timestamp=time.time(),
    )


def diff_snapshots(s1: WorktreeSnapshot, s2: WorktreeSnapshot) -> Dict[str, Any]:
    """对比两份 snapshot 的 tracked_files 集合。

    返回:
      {
        "added":    [在 s2 不在 s1 的文件],
        "removed":  [在 s1 不在 s2 的文件],
        "modified": [两边都在但 commit_sha 不同] (这里只看文件级,
                    因为同一文件在不同 commit 必然 modified),
        "s1_sha": s1.commit_sha,
        "s2_sha": s2.commit_sha,
      }
    """
    set1: Set[str] = set(s1.tracked_files)
    set2: Set[str] = set(s2.tracked_files)
    added = sorted(set2 - set1)
    removed = sorted(set1 - set2)
    # 共同文件: 单纯 set diff 体现不出 modified 语义, 这里把 commit 不同也视为 modified
    common = set1 & set2
    if s1.commit_sha != s2.commit_sha:
        modified = sorted(common)
    else:
        modified = []
    return {
        "added": added,
        "removed": removed,
        "modified": modified,
        "s1_sha": s1.commit_sha,
        "s2_sha": s2.commit_sha,
    }


def is_clean(snap: WorktreeSnapshot) -> bool:
    """空工作区 = porcelain_status 为空。"""
    return len(snap.porcelain_status) == 0


# ============ JSON 序列化 ============
def snapshot_to_json(snap: WorktreeSnapshot) -> str:
    return json.dumps(snap.to_dict(), ensure_ascii=False, sort_keys=True)


def snapshot_from_json(text: str) -> WorktreeSnapshot:
    return WorktreeSnapshot.from_dict(json.loads(text))


def worktree_info_to_json(info: WorktreeInfo) -> str:
    return json.dumps(info.to_dict(), ensure_ascii=False, sort_keys=True)


def worktree_info_from_json(text: str) -> WorktreeInfo:
    return WorktreeInfo.from_dict(json.loads(text))
