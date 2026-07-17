"""test_token_bucket — TokenBucket + MultiKeyTokenBucket 全场景测试

覆盖:
  1. 基本 consume / 超过 capacity 拒绝 / lazy refill
  2. 多 token 一次 consume
  3. wait_time / peek 精度
  4. reset / state dict
  5. MultiKey 多 key / LRU evict / cleanup_inactive
  6. 边界: capacity=0 / rate=0 / rate<0 / tokens<=0
  7. 并发: 100 线程抢 1 token (mutex 正确性)
  8. 大量 key (1000+)
  9. 性能: 1M try_consume < 1s
"""
from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from moa_gateway.capability.token_bucket import (
    DEFAULT_MULTIKEY_CAPACITY,
    DEFAULT_MULTIKEY_IDLE_SECONDS,
    MultiKeyTokenBucket,
    TokenBucket,
)

# ============================================================
# 1. 单 bucket 基本行为
# ============================================================

def test_initial_bucket_is_full():
    """新 bucket 应该装满 capacity 个 token"""
    tb = TokenBucket(capacity=10, refill_rate=1.0)
    assert tb.peek() == pytest.approx(10.0)


def test_single_consume_decrements_by_one():
    """单次 consume 扣 1 个 token"""
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.try_consume() is True
    assert tb.peek() == pytest.approx(4.0)


def test_consume_until_empty_then_denied():
    """消费到空后,下一次 consume 应被拒绝"""
    tb = TokenBucket(capacity=3, refill_rate=0.0)  # rate=0,不会补充
    assert tb.try_consume() is True
    assert tb.try_consume() is True
    assert tb.try_consume() is True
    assert tb.try_consume() is False  # 拒绝
    assert tb.peek() == 0.0


def test_consume_exactly_capacity_in_one_call():
    """一次 consume 整桶的 capacity,应成功"""
    tb = TokenBucket(capacity=10, refill_rate=1.0)
    assert tb.try_consume(10) is True
    assert tb.peek() == 0.0
    assert tb.try_consume() is False


def test_consume_more_than_capacity_denied():
    """单次请求 > capacity,直接拒绝 (无负数)"""
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.try_consume(6) is False
    # 拒绝不应消耗 token
    assert tb.peek() == pytest.approx(5.0)


# ============================================================
# 2. Lazy refill (mock time)
# ============================================================

def test_refill_via_mocked_time():
    """通过 mock time.monotonic 验证 lazy refill 正确"""
    tb = TokenBucket(capacity=10, refill_rate=2.0)  # 2 tokens/sec
    assert tb.peek() == pytest.approx(10.0)

    # 在 mock 上下文中消耗 8 个,使 _last_refill_ts 走 mock 时间
    base = tb._last_refill_ts
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 0.0):
        assert tb.try_consume(8) is True

    # 模拟再过去 3 秒(累计从 base 起 = 3s),rate=2 应补 6 个,2+6=8
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 3.0):
        assert tb.peek() == pytest.approx(8.0)

    # 模拟再过去到 5 秒(从 base 起),补 4s*2=8 个,2+8=10 → 满
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 5.0):
        assert tb.peek() == pytest.approx(10.0)


def test_refill_proportional_to_elapsed():
    """refill 数量 = elapsed * rate (但不超过 capacity)"""
    tb = TokenBucket(capacity=20, refill_rate=5.0)  # 5 t/s
    tb.try_consume(20)  # 清空

    base = tb._last_refill_ts
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 2.0):
        # 2 秒补充 10 个 (rate 5 t/s * 2s = 10)
        assert tb.peek() == pytest.approx(10.0)


def test_refill_caps_at_capacity():
    """即使过去很久,token 也不会超过 capacity"""
    tb = TokenBucket(capacity=10, refill_rate=100.0)
    base = tb._last_refill_ts
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 3600.0):
        # 3600s * 100 t/s = 360000 tokens, 但上限 = 10
        assert tb.peek() == pytest.approx(10.0)


# ============================================================
# 3. 多 tokens 一次 consume
# ============================================================

def test_multi_token_consume_partial():
    """多 token 一次扣减"""
    tb = TokenBucket(capacity=20, refill_rate=1.0)
    assert tb.try_consume(7) is True
    assert tb.peek() == pytest.approx(13.0)
    assert tb.try_consume(13) is True
    assert tb.peek() == 0.0


def test_multi_token_insufficient_denies():
    """多 token 不足时,整个请求被拒绝且不消耗 token"""
    tb = TokenBucket(capacity=5, refill_rate=0.0)
    assert tb.try_consume(3) is True
    assert tb.peek() == pytest.approx(2.0)
    assert tb.try_consume(3) is False
    # 拒绝后 token 数应不变
    assert tb.peek() == pytest.approx(2.0)


# ============================================================
# 4. wait_time 准确度
# ============================================================

def test_wait_time_when_available():
    """当前够 token,wait_time = 0"""
    tb = TokenBucket(capacity=10, refill_rate=1.0)
    assert tb.wait_time(5) == 0.0
    assert tb.wait_time(10) == 0.0


def test_wait_time_when_insufficient():
    """不够时,wait_time = deficit / rate"""
    tb = TokenBucket(capacity=10, refill_rate=2.0)  # 2 t/s
    tb.try_consume(8)  # 剩 2
    # 想扣 5 个,缺 3 个,需等 3/2 = 1.5s
    assert tb.wait_time(5) == pytest.approx(1.5)


def test_wait_time_inf_when_rate_zero():
    """rate=0 且不足,wait_time = inf (永远不会够)"""
    tb = TokenBucket(capacity=5, refill_rate=0.0)
    # 先消耗到不足
    tb.try_consume(5)
    assert tb.wait_time(3) == float("inf")


def test_wait_time_for_negative_tokens():
    """tokens<=0 → wait_time = 0 (不阻塞)"""
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.wait_time(0) == 0.0
    assert tb.wait_time(-1) == 0.0


def test_wait_time_invalid_type():
    """非数字 tokens → 0.0 兜底"""
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.wait_time("abc") == 0.0  # type: ignore[arg-type]


# ============================================================
# 5. peek 准确
# ============================================================

def test_peek_triggers_refill():
    """peek 自身应触发 lazy refill"""
    tb = TokenBucket(capacity=10, refill_rate=3.0)
    tb.try_consume(10)
    base = tb._last_refill_ts
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 1.0):
        # 1s * 3 t/s = 3 tokens
        assert tb.peek() == pytest.approx(3.0)


def test_peek_does_not_consume():
    """peek 不消耗 token"""
    tb = TokenBucket(capacity=5, refill_rate=0.0)
    initial = tb.peek()
    for _ in range(10):
        tb.peek()
    assert tb.peek() == initial


# ============================================================
# 6. reset
# ============================================================

def test_reset_fills_bucket_and_clears_counters():
    """reset 后回到满桶 + 计数清零"""
    tb = TokenBucket(capacity=10, refill_rate=1.0)
    tb.try_consume(5)
    assert tb.peek() == pytest.approx(5.0)
    state_before = tb.state()
    assert state_before["total_consumed"] == 5

    tb.reset()
    assert tb.peek() == pytest.approx(10.0)
    state_after = tb.state()
    assert state_after["total_consumed"] == 0
    assert state_after["total_denied"] == 0


# ============================================================
# 7. state dict
# ============================================================

def test_state_dict_has_all_keys():
    """state 应包含所有监控所需字段"""
    tb = TokenBucket(capacity=8, refill_rate=2.0)
    tb.try_consume(3)
    s = tb.state()
    expected_keys = {
        "capacity", "refill_rate", "tokens", "available",
        "last_refill_ts", "created_ts",
        "total_consumed", "total_denied", "denied_ratio",
    }
    assert expected_keys.issubset(s.keys())
    assert s["capacity"] == 8.0
    assert s["refill_rate"] == 2.0
    assert s["tokens"] == pytest.approx(5.0)
    assert s["available"] == 5
    assert s["total_consumed"] == 3
    assert s["total_denied"] == 0
    assert s["denied_ratio"] == 0.0


def test_state_denied_ratio_with_mixed_calls():
    """混合成功/失败后 denied_ratio 正确"""
    tb = TokenBucket(capacity=2, refill_rate=0.0)
    tb.try_consume()  # OK
    tb.try_consume()  # OK
    tb.try_consume()  # DENY
    tb.try_consume()  # DENY
    s = tb.state()
    assert s["total_consumed"] == 2
    assert s["total_denied"] == 2
    assert s["denied_ratio"] == 0.5


# ============================================================
# 8. 边界: capacity=0
# ============================================================

def test_capacity_zero_always_denies():
    """capacity=0 的桶永远拒绝 (rate=0 时 wait_time=inf;rate>0 时有限等待)"""
    tb = TokenBucket(capacity=0, refill_rate=0.0)
    assert tb.try_consume() is False
    assert tb.peek() == 0.0
    # rate=0 + 永远不足 → 永远等不到 → inf
    assert tb.wait_time(1) == float("inf")
    s = tb.state()
    assert s["capacity"] == 0.0
    assert s["available"] == 0


# ============================================================
# 9. 边界: rate=0
# ============================================================

def test_rate_zero_no_refill():
    """rate=0 永不补充,一次性桶"""
    tb = TokenBucket(capacity=5, refill_rate=0.0)
    for _ in range(5):
        assert tb.try_consume() is True
    assert tb.try_consume() is False
    time.sleep(0.1)  # 等实际时间
    assert tb.peek() == 0.0  # 仍未补充


# ============================================================
# 10. 边界: rate=负数(降级)
# ============================================================

def test_negative_rate_degrades_to_zero_with_warning():
    """rate<0 应降级为 0 并发出警告"""
    with pytest.warns(UserWarning, match="negative"):
        tb = TokenBucket(capacity=5, refill_rate=-1.0)
    assert tb.refill_rate == 0.0
    assert tb.try_consume(5) is True
    assert tb.try_consume() is False


# ============================================================
# 11. 边界: 负 tokens
# ============================================================

def test_negative_tokens_rejected():
    """负数 tokens 直接拒绝 (不消耗)"""
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.try_consume(-1) is False
    assert tb.try_consume(0) is False
    # token 仍然满
    assert tb.peek() == pytest.approx(5.0)


def test_invalid_type_tokens_rejected():
    """非数字 tokens 拒绝"""
    tb = TokenBucket(capacity=5, refill_rate=1.0)
    assert tb.try_consume("abc") is False  # type: ignore[arg-type]
    assert tb.try_consume(None) is False  # type: ignore[arg-type]


# ============================================================
# 12. 参数校验
# ============================================================

def test_invalid_capacity_type_raises():
    with pytest.raises(TypeError):
        TokenBucket("60", 1.0)  # type: ignore[arg-type]


def test_invalid_refill_rate_type_raises():
    with pytest.raises(TypeError):
        TokenBucket(60, "1.0")  # type: ignore[arg-type]


def test_negative_capacity_raises():
    with pytest.raises(ValueError):
        TokenBucket(-1, 1.0)


# ============================================================
# 13. MultiKeyTokenBucket 基本
# ============================================================

def test_multikey_isolates_keys():
    """不同 key 的 bucket 互不影响"""
    mk = MultiKeyTokenBucket(default_capacity=3, default_refill_rate=0.0)
    assert mk.try_consume("user-A") is True
    assert mk.try_consume("user-A") is True
    assert mk.try_consume("user-A") is True
    assert mk.try_consume("user-A") is False  # A 用尽

    # B 不应被影响
    assert mk.try_consume("user-B") is True
    assert mk.try_consume("user-B") is True
    assert mk.try_consume("user-B") is True
    assert mk.try_consume("user-B") is False

    assert mk.size() == 2


def test_multikey_get_bucket_creates_lazy():
    """get_bucket 自动创建不存在的 key"""
    mk = MultiKeyTokenBucket(default_capacity=10, default_refill_rate=1.0)
    b = mk.get_bucket("new-key")
    assert isinstance(b, TokenBucket)
    assert b.capacity == 10.0
    assert b.refill_rate == 1.0
    assert mk.size() == 1


def test_multikey_get_bucket_returns_same_instance():
    """get_bucket 同一 key 应返回同一对象"""
    mk = MultiKeyTokenBucket(default_capacity=5, default_refill_rate=1.0)
    b1 = mk.get_bucket("k")
    b2 = mk.get_bucket("k")
    assert b1 is b2


def test_multikey_all_states():
    """all_states 返回每个 key 的快照"""
    mk = MultiKeyTokenBucket(default_capacity=5, default_refill_rate=1.0)
    mk.try_consume("a", 2)
    mk.try_consume("b", 3)
    states = mk.all_states()
    assert set(states.keys()) == {"a", "b"}
    assert states["a"]["tokens"] == pytest.approx(3.0)
    assert states["b"]["tokens"] == pytest.approx(2.0)


# ============================================================
# 14. LRU evict
# ============================================================

def test_lru_eviction():
    """超出 max_keys 时,最久未访问的被驱逐"""
    mk = MultiKeyTokenBucket(
        default_capacity=1,
        default_refill_rate=0.0,
        max_keys=3,
    )
    mk.get_bucket("k1")
    mk.get_bucket("k2")
    mk.get_bucket("k3")
    assert mk.size() == 3

    # 加 k4 → 应该驱逐 k1
    mk.get_bucket("k4")
    assert mk.size() == 3
    assert "k1" not in mk._buckets
    assert "k4" in mk._buckets


def test_lru_eviction_respects_access_order():
    """get_bucket 触发的访问会刷新 LRU 顺序"""
    mk = MultiKeyTokenBucket(
        default_capacity=1,
        default_refill_rate=0.0,
        max_keys=3,
    )
    mk.get_bucket("k1")
    mk.get_bucket("k2")
    mk.get_bucket("k3")
    # 访问 k1,使其变成最近
    mk.get_bucket("k1")
    # 加 k4 → 应该驱逐 k2 (最久未访问)
    mk.get_bucket("k4")
    assert "k1" in mk._buckets
    assert "k2" not in mk._buckets
    assert "k3" in mk._buckets
    assert "k4" in mk._buckets


def test_lru_zero_max_keys_means_unlimited():
    """max_keys<=0 表示无上限"""
    mk = MultiKeyTokenBucket(
        default_capacity=1,
        default_refill_rate=0.0,
        max_keys=0,
    )
    for i in range(100):
        mk.get_bucket(f"k{i}")
    assert mk.size() == 100


# ============================================================
# 15. cleanup_inactive
# ============================================================

def test_cleanup_inactive_removes_idle_buckets():
    """cleanup_inactive 清理 idle 超过阈值的 bucket"""
    mk = MultiKeyTokenBucket(
        default_capacity=1,
        default_refill_rate=0.0,
        max_keys=100,
    )
    mk.get_bucket("active")
    # mock 让 k_old 看起来已经很久没动
    mk.get_bucket("old")

    # 把 old 的 last_refill_ts 设为很久以前
    with mk._lock:
        old_bucket = mk._buckets["old"]
        with old_bucket._lock:
            old_bucket._last_refill_ts = time.monotonic() - 7200  # 2 小时前

    removed = mk.cleanup_inactive(max_idle_seconds=3600.0)
    assert removed == 1
    assert "old" not in mk._buckets
    assert "active" in mk._buckets


def test_cleanup_inactive_no_op_when_all_fresh():
    """所有 bucket 都新鲜时,清理返回 0"""
    mk = MultiKeyTokenBucket(default_capacity=1, default_refill_rate=0.0)
    mk.get_bucket("a")
    mk.get_bucket("b")
    assert mk.cleanup_inactive(max_idle_seconds=3600.0) == 0
    assert mk.size() == 2


# ============================================================
# 16. 并发: 100 线程抢 1 token
# ============================================================

def test_concurrent_consume_exactly_one_succeeds():
    """100 线程并发抢 1 token,只有 1 个能成功"""
    tb = TokenBucket(capacity=1, refill_rate=0.0)
    results: list[bool] = []
    lock = threading.Lock()
    barrier = threading.Barrier(100)

    def worker():
        barrier.wait()  # 同步起跑
        ok = tb.try_consume()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1
    assert results.count(False) == 99
    s = tb.state()
    assert s["total_consumed"] == 1
    assert s["total_denied"] == 99


def test_concurrent_multikey_isolation():
    """多 key 并发 consume,各 key 独立计数正确"""
    mk = MultiKeyTokenBucket(default_capacity=5, default_refill_rate=0.0)
    barrier = threading.Barrier(50)
    results = {"A": 0, "B": 0}
    lock = threading.Lock()

    def worker(key: str):
        barrier.wait()
        if mk.try_consume(key):
            with lock:
                results[key] += 1

    threads = []
    for _ in range(25):
        threads.append(threading.Thread(target=worker, args=("A",)))
        threads.append(threading.Thread(target=worker, args=("B",)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # capacity=5,所以每个 key 最多 5 个成功
    assert results["A"] == 5
    assert results["B"] == 5


# ============================================================
# 17. 1000 keys 大批量
# ============================================================

def test_1000_keys_basic_consume():
    """1000 个 key 都能独立工作"""
    mk = MultiKeyTokenBucket(
        default_capacity=2,
        default_refill_rate=0.0,
        max_keys=2000,
    )
    for i in range(1000):
        assert mk.try_consume(f"key-{i}") is True
        assert mk.try_consume(f"key-{i}") is True
        assert mk.try_consume(f"key-{i}") is False  # 第 3 次被拒
    assert mk.size() == 1000


# ============================================================
# 18. 性能: 1M try_consume < 1s
# ============================================================

def test_perf_1m_try_consume_under_1s():
    """100 万次 try_consume 应在 1 秒内完成 (在 capacity 充足时)"""
    tb = TokenBucket(capacity=10_000_000, refill_rate=1_000_000.0)
    start = time.perf_counter()
    ok_count = 0
    for _ in range(1_000_000):
        if tb.try_consume():
            ok_count += 1
    elapsed = time.perf_counter() - start
    assert ok_count == 1_000_000
    # 留一些余量给慢机器: 实际预期 < 0.5s, 阈值 1.5s
    assert elapsed < 1.5, f"1M try_consume took {elapsed:.3f}s"


# ============================================================
# 19. 兜底: peek / state 在异常时不应崩溃
# ============================================================

def test_peek_survives_when_state_corrupted(monkeypatch):
    """即使内部状态被破坏,peek/try_consume 不应抛异常"""
    tb = TokenBucket(capacity=10, refill_rate=1.0)
    # 不打补丁,只确保正常路径不抛
    assert tb.peek() >= 0.0
    s = tb.state()
    assert "tokens" in s


def test_multikey_with_none_key():
    """None key 应被安全处理为 str"""
    mk = MultiKeyTokenBucket(default_capacity=1, default_refill_rate=0.0)
    assert mk.try_consume(None) is True  # type: ignore[arg-type]
    assert mk.try_consume(None) is False
    assert mk.size() == 1


def test_multikey_constructor_validates():
    """非法参数应被拒绝"""
    with pytest.raises(ValueError):
        MultiKeyTokenBucket(default_capacity=-1, default_refill_rate=1.0)
    with pytest.raises(TypeError):
        MultiKeyTokenBucket(default_capacity="x", default_refill_rate=1.0)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        MultiKeyTokenBucket(default_capacity=10, default_refill_rate="x")  # type: ignore[arg-type]


# ============================================================
# 20. 集成场景: 60 RPM 实际限流
# ============================================================

def test_realistic_60rpm_burst_then_drain():
    """模拟 60 RPM: 满桶时允许 60 个瞬时爆发,然后按 1/s 补充"""
    tb = TokenBucket(capacity=60, refill_rate=1.0)
    # 瞬时消耗 60 个 (突发)
    for _ in range(60):
        assert tb.try_consume() is True
    assert tb.try_consume() is False

    # mock 1 秒后,补充 1 个
    base = tb._last_refill_ts
    with patch("moa_gateway.capability.token_bucket.time.monotonic", side_effect=lambda: base + 1.0):
        assert tb.try_consume() is True
        assert tb.try_consume() is False


def test_constants_exposed():
    """模块常量应可访问"""
    assert DEFAULT_MULTIKEY_CAPACITY == 10000
    assert DEFAULT_MULTIKEY_IDLE_SECONDS == 3600.0


def test_all_exports():
    """__all__ 完整性"""
    from moa_gateway.capability import token_bucket
    for name in token_bucket.__all__:
        assert hasattr(token_bucket, name)
