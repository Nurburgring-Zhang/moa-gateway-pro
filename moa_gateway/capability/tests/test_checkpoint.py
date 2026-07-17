"""A-23 Checkpoint 原子写 — 测试

覆盖:
- atomic_write 创建/覆盖/二进制/失败回滚/临时文件清理
- CheckpointStore save/load/list/cleanup/delete
- 复杂 payload / 中文 Unicode / 线程并发
- fsync mock 验证
"""
from __future__ import annotations

import os
import sys
import threading
import time
import unittest
from unittest import mock

# 让测试可独立跑
_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(_HERE)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from moa_gateway.capability.checkpoint import CheckpointStore, atomic_write  # noqa: E402


def _tmp_dir(prefix: str = "ckpt_test_") -> str:
    import tempfile
    return tempfile.mkdtemp(prefix=prefix)


def _list_tmp_leftover(dirpath: str) -> list:
    if not os.path.isdir(dirpath):
        return []
    return [f for f in os.listdir(dirpath) if ".tmp." in f]


class TestAtomicWriteBasic(unittest.TestCase):
    def setUp(self) -> None:
        self.td = _tmp_dir()

    def tearDown(self) -> None:
        import shutil
        if os.path.isdir(self.td):
            shutil.rmtree(self.td, ignore_errors=True)

    def test_01_create_text_file(self) -> None:
        p = os.path.join(self.td, "a.txt")
        atomic_write(p, "hello world")
        self.assertTrue(os.path.isfile(p))
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "hello world")

    def test_02_overwrite_existing(self) -> None:
        p = os.path.join(self.td, "a.txt")
        atomic_write(p, "first")
        atomic_write(p, "second")
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "second")

    def test_03_bytes_payload(self) -> None:
        p = os.path.join(self.td, "b.bin")
        payload = b"\x00\x01\x02\xff\xfe"
        atomic_write(p, payload)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_04_unicode_text(self) -> None:
        p = os.path.join(self.td, "u.txt")
        atomic_write(p, "你好世界 🌍 — checkpoint")
        with open(p, encoding="utf-8") as f:
            self.assertEqual(f.read(), "你好世界 🌍 — checkpoint")

    def test_05_creates_parent_dir(self) -> None:
        p = os.path.join(self.td, "sub", "deep", "x.txt")
        atomic_write(p, "ok")
        self.assertTrue(os.path.isfile(p))

    def test_06_no_tmp_leftover_after_success(self) -> None:
        p = os.path.join(self.td, "c.txt")
        for i in range(5):
            atomic_write(p, f"v{i}")
        leftovers = _list_tmp_leftover(self.td)
        self.assertEqual(leftovers, [], f"tmp leftovers: {leftovers}")

    def test_07_failure_rolls_back_tmp(self) -> None:
        p = os.path.join(self.td, "f.txt")
        # 模拟 os.replace 抛 OSError
        with mock.patch("moa_gateway.capability.checkpoint.os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                atomic_write(p, "data")
        # 目标文件不应存在
        self.assertFalse(os.path.isfile(p))
        # 临时文件应被清理
        leftovers = _list_tmp_leftover(self.td)
        self.assertEqual(leftovers, [], f"tmp leftovers: {leftovers}")

    def test_08_failure_before_replace_rolls_back(self) -> None:
        p = os.path.join(self.td, "g.txt")
        with mock.patch("moa_gateway.capability.checkpoint.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                atomic_write(p, "data")
        self.assertFalse(os.path.isfile(p))
        leftovers = _list_tmp_leftover(self.td)
        self.assertEqual(leftovers, [], f"tmp leftovers: {leftovers}")

    def test_09_rejects_bad_data_type(self) -> None:
        p = os.path.join(self.td, "h.txt")
        with self.assertRaises(TypeError):
            atomic_write(p, 12345)  # type: ignore[arg-type]

    def test_10_rejects_empty_path(self) -> None:
        with self.assertRaises(ValueError):
            atomic_write("", "x")


class TestAtomicWriteFsync(unittest.TestCase):
    def setUp(self) -> None:
        self.td = _tmp_dir()

    def tearDown(self) -> None:
        import shutil
        if os.path.isdir(self.td):
            shutil.rmtree(self.td, ignore_errors=True)

    def test_11_fsync_called(self) -> None:
        p = os.path.join(self.td, "fsync.txt")
        with mock.patch("moa_gateway.capability.checkpoint.os.fsync") as fsync_mock:
            atomic_write(p, "data")
            self.assertEqual(fsync_mock.call_count, 1, "fsync should be called exactly once")

    def test_12_replace_called(self) -> None:
        p = os.path.join(self.td, "rep.txt")
        with mock.patch("moa_gateway.capability.checkpoint.os.replace") as replace_mock:
            atomic_write(p, "data")
            self.assertEqual(replace_mock.call_count, 1)
            args, _ = replace_mock.call_args
            # args = (src, dst)
            self.assertEqual(args[1], p)


class TestCheckpointStoreBasic(unittest.TestCase):
    def setUp(self) -> None:
        self.td = _tmp_dir()
        self.store = CheckpointStore(self.td, max_keep=3)

    def tearDown(self) -> None:
        import shutil
        if os.path.isdir(self.td):
            shutil.rmtree(self.td, ignore_errors=True)

    def test_13_save_load_roundtrip_dict(self) -> None:
        payload = {"step": 1, "score": 0.95, "tags": ["a", "b"]}
        p = self.store.save("run1", payload)
        self.assertTrue(os.path.isfile(p))
        self.assertEqual(self.store.load("run1"), payload)

    def test_14_save_load_nested(self) -> None:
        payload = {
            "level1": {
                "level2": {
                    "level3": [1, 2, {"x": "y", "z": [None, True, False]}],
                }
            },
            "unicode": "中文 🚀",
        }
        self.store.save("nested", payload)
        self.assertEqual(self.store.load("nested"), payload)

    def test_15_save_load_list(self) -> None:
        payload = [1, 2, 3, "four", {"five": 5}]
        self.store.save("lst", payload)
        self.assertEqual(self.store.load("lst"), payload)

    def test_16_load_missing_returns_none(self) -> None:
        self.assertIsNone(self.store.load("nope"))

    def test_17_delete_success(self) -> None:
        self.store.save("d1", {"k": 1})
        self.assertTrue(self.store.delete("d1"))
        self.assertIsNone(self.store.load("d1"))
        self.assertFalse(self.store.delete("d1"))  # 第二次不存在

    def test_18_delete_invalid_name(self) -> None:
        self.assertFalse(self.store.delete("../escape"))
        self.assertFalse(self.store.delete("a/b"))
        self.assertFalse(self.store.delete(""))

    def test_19_list_metadata(self) -> None:
        self.store.save("a", {"i": 1})
        time.sleep(0.02)
        self.store.save("b", {"i": 2})
        time.sleep(0.02)
        self.store.save("c", {"i": 3})
        items = self.store.list()
        self.assertEqual(len(items), 3)
        # 倒序:最新 c 在前
        self.assertEqual(items[0]["name"], "c")
        self.assertEqual(items[1]["name"], "b")
        self.assertEqual(items[2]["name"], "a")
        for it in items:
            self.assertIn("size", it)
            self.assertIn("mtime", it)
            self.assertIn("path", it)
            self.assertTrue(os.path.isfile(it["path"]))
            self.assertGreater(it["size"], 0)

    def test_20_cleanup_keeps_max_keep(self) -> None:
        for i in range(7):
            self.store.save(f"x{i}", {"i": i})
            time.sleep(0.005)
        deleted = self.store.cleanup()
        self.assertEqual(deleted, 7 - 3)  # 7 - max_keep=3
        items = self.store.list()
        self.assertEqual(len(items), 3)
        # 应保留最新 3 个: x4, x5, x6
        names = {it["name"] for it in items}
        self.assertEqual(names, {"x4", "x5", "x6"})

    def test_21_cleanup_older_than_seconds(self) -> None:
        self.store.save("old", {"v": 1})
        time.sleep(0.05)
        self.store.save("new", {"v": 2})
        # 把 "old" 的 mtime 拨到 1 小时前
        old_path = self.store._path_for("old")
        old_time = time.time() - 3600
        os.utime(old_path, (old_time, old_time))
        deleted = self.store.cleanup(older_than_seconds=60)
        self.assertGreaterEqual(deleted, 1)
        self.assertIsNone(self.store.load("old"))
        self.assertIsNotNone(self.store.load("new"))

    def test_22_save_invalid_payload_raises(self) -> None:
        with self.assertRaises(TypeError):
            self.store.save("bad", {"fn": lambda x: x})

    def test_23_unicode_payload(self) -> None:
        payload = {"zh": "中文测试", "emoji": "🎉🔥", "jp": "こんにちは", "mix": "中文+English+🌍"}
        self.store.save("uni", payload)
        loaded = self.store.load("uni")
        self.assertEqual(loaded, payload)


class TestCheckpointStoreConcurrency(unittest.TestCase):
    def setUp(self) -> None:
        self.td = _tmp_dir()
        self.store = CheckpointStore(self.td, max_keep=100)

    def tearDown(self) -> None:
        import shutil
        if os.path.isdir(self.td):
            shutil.rmtree(self.td, ignore_errors=True)

    def test_24_concurrent_save_10x10(self) -> None:
        threads_n = 10
        files_n = 10
        errors: list = []

        def worker(tid: int) -> None:
            try:
                for i in range(files_n):
                    name = f"t{tid}_f{i}"
                    self.store.save(name, {"tid": tid, "i": i, "ts": time.time()})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        ts = [threading.Thread(target=worker, args=(t,)) for t in range(threads_n)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30)

        self.assertEqual(errors, [], f"thread errors: {errors}")
        items = self.store.list()
        self.assertEqual(len(items), threads_n * files_n)
        # 无临时文件残留
        leftovers = _list_tmp_leftover(self.td)
        self.assertEqual(leftovers, [], f"tmp leftovers: {leftovers}")
        # 每个文件可正确读回
        for tid in range(threads_n):
            for i in range(files_n):
                loaded = self.store.load(f"t{tid}_f{i}")
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["tid"], tid)
                self.assertEqual(loaded["i"], i)


class TestCheckpointStoreEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self.td = _tmp_dir()

    def tearDown(self) -> None:
        import shutil
        if os.path.isdir(self.td):
            shutil.rmtree(self.td, ignore_errors=True)

    def test_25_corrupt_json_load_returns_none(self) -> None:
        store = CheckpointStore(self.td, max_keep=5)
        p = store._path_for("corrupt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{ not valid json")
        self.assertIsNone(store.load("corrupt"))

    def test_26_save_overwrites_existing(self) -> None:
        store = CheckpointStore(self.td, max_keep=5)
        store.save("k", {"v": 1})
        store.save("k", {"v": 2})
        self.assertEqual(store.load("k"), {"v": 2})
        # 只有一份
        items = [it for it in store.list() if it["name"] == "k"]
        self.assertEqual(len(items), 1)

    def test_27_max_keep_zero_deletes_all(self) -> None:
        store = CheckpointStore(self.td, max_keep=0)
        store.save("a", {"v": 1})
        store.save("b", {"v": 2})
        deleted = store.cleanup()
        self.assertEqual(deleted, 2)
        self.assertEqual(len(store.list()), 0)

    def test_28_root_dir_created_automatically(self) -> None:
        nested = os.path.join(self.td, "a", "b", "c")
        store = CheckpointStore(nested, max_keep=5)
        store.save("x", {"v": 1})
        self.assertTrue(os.path.isdir(nested))
        self.assertEqual(store.load("x"), {"v": 1})

    def test_29_list_empty(self) -> None:
        store = CheckpointStore(self.td, max_keep=5)
        self.assertEqual(store.list(), [])

    def test_30_init_invalid_max_keep(self) -> None:
        with self.assertRaises(ValueError):
            CheckpointStore(self.td, max_keep=-1)

    def test_31_init_invalid_root_dir(self) -> None:
        with self.assertRaises(ValueError):
            CheckpointStore("", max_keep=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
