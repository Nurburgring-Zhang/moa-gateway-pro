"""A-23 Checkpoint 原子写能力

- atomic_write: 写临时文件 + fsync 落盘 + os.replace 原子重命名 (Windows/Linux/macOS 跨平台)
- CheckpointStore: 多 checkpoint 管理,save/load/list/cleanup/delete,线程安全,JSON 序列化

真实实现,非 mock。所有写操作走 atomic_write,失败时自动清理临时文件,
绝不留下半成品。save() 持有 threading.Lock 防止并发写竞争。
"""

from __future__ import annotations

import builtins
import contextlib
import json
import os
import threading
import time
from typing import Any, Union

# ============ A-23: atomic_write ============


def atomic_write(
    path: str,
    data: Union[str, bytes],
    encoding: str = "utf-8",
    mode: int = 0o644,
) -> None:
    """把 data 原子地写到 path (跨平台:Windows/Linux/macOS)

    步骤:
      1. 写到 ``path + ".tmp.<pid>.<ts>"`` 临时文件
      2. flush + os.fsync 落盘
      3. os.replace() 原子重命名覆盖目标
      4. 失败时清理临时文件 (不留下半成品)

    Args:
        path: 目标文件绝对/相对路径
        data: 文本 (str) 或 二进制 (bytes)
        encoding: str 编码方式,默认 utf-8
        mode: Unix 权限位,默认 0o644 (Windows 上忽略)

    Raises:
        TypeError: data 不是 str/bytes
        OSError: 写盘/重命名失败
    """
    if not isinstance(path, str) or not path:
        raise ValueError(f"path must be non-empty str, got {path!r}")
    if not isinstance(data, (str, bytes)):
        raise TypeError(f"data must be str or bytes, got {type(data).__name__}")

    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, exist_ok=True)

    tmp_path = f"{path}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
    fd: int | None = None
    try:
        if isinstance(data, str):
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            payload = data.encode(encoding)
        else:
            fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            payload = data

        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
            fd = None

        os.replace(tmp_path, path)
    except BaseException:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        raise


# ============ A-23: CheckpointStore ============


class CheckpointStore:
    """多 checkpoint 文件管理 (save/load/list/cleanup/delete)

    - save: JSON 序列化 + atomic_write,失败时回滚
    - load: 不存在返回 None;JSON 损坏返回 None (不抛)
    - list: 返回 name/size/mtime/path
    - cleanup: 按 mtime 删,留下最新 max_keep 个;可选 older_than_seconds
    - delete: 按 name 删

    线程安全: save/load/list/cleanup/delete 全部持有 self._lock。
    """

    def __init__(self, root_dir: str, max_keep: int = 10) -> None:
        """初始化

        Args:
            root_dir: checkpoint 文件根目录 (不存在则创建)
            max_keep: cleanup 默认保留的最新文件数
        """
        if not isinstance(root_dir, str) or not root_dir:
            raise ValueError(f"root_dir must be non-empty str, got {root_dir!r}")
        if not isinstance(max_keep, int) or max_keep < 0:
            raise ValueError(f"max_keep must be non-negative int, got {max_keep!r}")
        self.root_dir = os.path.abspath(root_dir)
        if not os.path.isdir(self.root_dir):
            os.makedirs(self.root_dir, exist_ok=True)
        self.max_keep = max_keep
        self._lock = threading.Lock()

    # ---------- 路径工具 ----------

    def _safe_name(self, name: str) -> str:
        """校验并返回 name:必须非空、不能含路径分隔符或 '..'"""
        if not isinstance(name, str) or not name:
            raise ValueError(f"name must be non-empty str, got {name!r}")
        if os.path.basename(name) != name:
            raise ValueError(f"name must not contain path separators: {name!r}")
        if name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError(f"invalid name: {name!r}")
        return name

    def _path_for(self, name: str) -> str:
        safe = self._safe_name(name)
        return os.path.join(self.root_dir, f"{safe}.json")

    # ---------- save / load ----------

    def save(self, name: str, payload: Any) -> str:
        """序列化 payload 到 JSON,atomic_write 落盘

        Args:
            name: checkpoint 名 (不能含路径分隔符)
            payload: 可 JSON 序列化的对象 (dict/list/str/int/float/bool/None)

        Returns:
            落盘文件的绝对路径

        Raises:
            ValueError: name 非法
            TypeError: payload 不可 JSON 序列化
            OSError: 写盘失败
        """
        target = self._path_for(name)
        try:
            blob = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"payload not JSON-serializable: {exc}") from exc

        with self._lock:
            atomic_write(target, blob, encoding="utf-8", mode=0o644)
        return target

    def load(self, name: str) -> Any | None:
        """按 name 加载 checkpoint;不存在或损坏返回 None

        Args:
            name: checkpoint 名

        Returns:
            反序列化后的对象;不存在/JSON 损坏/IO 失败 → None
        """
        try:
            target = self._path_for(name)
        except ValueError:
            return None

        with self._lock:
            if not os.path.isfile(target):
                return None
            try:
                with open(target, "rb") as f:
                    raw = f.read()
            except OSError:
                return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None

    # ---------- list ----------

    def list(self) -> builtins.list[dict[str, Any]]:
        """列出所有 checkpoint 元数据,按 mtime 倒序 (最新在前)

        Returns:
            [{"name": str, "size": int, "mtime": float, "path": str}, ...]
        """
        with self._lock:
            return self._list_locked()

    def _list_locked(self) -> builtins.list[dict[str, Any]]:
        """list() 的锁内实现,供 cleanup 复用 (避免重入死锁)"""
        entries: list[dict[str, Any]] = []
        if not os.path.isdir(self.root_dir):
            return entries
        try:
            filenames = os.listdir(self.root_dir)
        except OSError:
            return entries
        for fn in filenames:
            if not fn.endswith(".json"):
                continue
            full = os.path.join(self.root_dir, fn)
            if not os.path.isfile(full):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            entries.append(
                {
                    "name": fn[: -len(".json")],
                    "size": int(st.st_size),
                    "mtime": float(st.st_mtime),
                    "path": full,
                }
            )
        entries.sort(key=lambda e: e["mtime"], reverse=True)
        return entries

    # ---------- cleanup ----------

    def cleanup(self, older_than_seconds: int | None = None) -> int:
        """清理 checkpoint

        规则:
          1. 若 older_than_seconds 给定,删除 mtime 早于 (now - older_than_seconds) 的全部
          2. 否则按 mtime 倒序,保留最新 max_keep 个,删除其余
          3. 两规则叠加:先按 max_keep 限数量,再按 older_than_seconds 删旧

        Returns:
            实际删除的文件数
        """
        with self._lock:
            entries = self._list_locked()
            if not entries:
                return 0

            # 第一步:按 max_keep 限数量 (倒序,保留前 max_keep)
            keep = entries[: self.max_keep] if self.max_keep > 0 else []
            to_delete = entries[self.max_keep :] if self.max_keep > 0 else entries

            # 第二步:按 older_than_seconds 进一步删旧
            if older_than_seconds is not None and older_than_seconds >= 0:
                threshold = time.time() - float(older_than_seconds)
                # 从 keep 里也挑出超过阈值的删
                for e in keep:
                    if e["mtime"] < threshold:
                        to_delete.append(e)
            # 去重
            seen = set()
            unique: list[dict[str, Any]] = []
            for e in to_delete:
                p = e["path"]
                if p in seen:
                    continue
                seen.add(p)
                unique.append(e)

            deleted = 0
            for e in unique:
                try:
                    os.remove(e["path"])
                    deleted += 1
                except OSError:
                    pass
            return deleted

    # ---------- delete ----------

    def delete(self, name: str) -> bool:
        """按 name 删 checkpoint;不存在返回 False

        Args:
            name: checkpoint 名

        Returns:
            True=删了;False=不存在或 IO 失败
        """
        try:
            target = self._path_for(name)
        except ValueError:
            return False
        with self._lock:
            try:
                if not os.path.isfile(target):
                    return False
                os.remove(target)
                return True
            except OSError:
                return False
