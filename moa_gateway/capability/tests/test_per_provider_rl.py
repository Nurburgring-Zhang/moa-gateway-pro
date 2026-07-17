"""per_provider_rl 真实测试(非 mock,全部 assert)"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.per_provider_rl import (
    MultiProviderLimiter,
    ProviderLimit,
    ProviderLimiter,
    decision_to_dict,
    decision_to_json,
    make_default_limits,
    snapshot_to_json,
)

# ============ Helpers ============

def _make_limit(
    provider: str = "openai",
    max_rpm: int = 10,
    max_ipm: int = 1000,
    max_conc: int = 3,
    cooldown: float = 60.0,
) -> ProviderLimit:
    return ProviderLimit(
        provider=provider,
        max_requests_per_minute=max_rpm,
        max_inputs_per_minute=max_ipm,
        max_concurrent=max_conc,
        cooldown_seconds_after_429=cooldown,
    )


# ============ Tests ============

def test_zero_usage_allowed():
    """0 usage → allowed=True"""
    lim = ProviderLimiter(_make_limit(max_rpm=10))
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is True
    assert dec.current_rpm == 0.0
    assert dec.current_ipm == 0.0
    assert dec.current_concurrent == 0
    assert dec.retry_after_seconds is None
    print("  ✓ test_zero_usage_allowed")


def test_one_request_current_rpm():
    """1 request → current_rpm=1"""
    lim = ProviderLimiter(_make_limit(max_rpm=10))
    lim.record_usage(request_count=1, at=1000.0)
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is True
    assert dec.current_rpm == 1.0
    print("  ✓ test_one_request_current_rpm")


def test_rpm_at_max_allowed():
    """达到 max_rpm-1 仍 allowed(>= 才拒绝)"""
    lim = ProviderLimiter(_make_limit(max_rpm=5))
    for i in range(4):
        lim.record_usage(request_count=1, at=1000.0 + i * 0.1)
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is True
    assert dec.current_rpm == 4.0
    print("  ✓ test_rpm_at_max_allowed")


def test_rpm_exceeded_denied():
    """max_rpm=10 11 个请求 → 11th denied"""
    lim = ProviderLimiter(_make_limit(max_rpm=10))
    for i in range(10):
        lim.record_usage(request_count=1, at=1000.0 + i * 0.01)
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is False
    assert dec.current_rpm == 10.0
    assert "rpm" in dec.reason.lower()
    print("  ✓ test_rpm_exceeded_denied")


def test_retry_after_rpm():
    """retry_after = 60/max_rpm"""
    lim = ProviderLimiter(_make_limit(max_rpm=10))
    for i in range(10):
        lim.record_usage(request_count=1, at=1000.0 + i * 0.01)
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is False
    assert dec.retry_after_seconds == 6.0  # 60/10
    print(f"  ✓ test_retry_after_rpm (retry={dec.retry_after_seconds})")


def test_retry_after_ipm():
    """retry_after = 60/max_ipm"""
    lim = ProviderLimiter(_make_limit(max_rpm=100, max_ipm=100))
    for i in range(10):
        lim.record_usage(request_count=0, input_tokens=10, at=1000.0 + i * 0.1)
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is False
    assert dec.retry_after_seconds == 0.6  # 60/100
    print(f"  ✓ test_retry_after_ipm (retry={dec.retry_after_seconds})")


def test_ipm_exceeded_denied():
    """current_ipm 超限 denied"""
    lim = ProviderLimiter(_make_limit(max_rpm=1000, max_ipm=50))
    lim.record_usage(request_count=0, input_tokens=60, at=1000.0)
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is False
    assert dec.current_ipm == 60.0
    assert "ipm" in dec.reason.lower()
    print("  ✓ test_ipm_exceeded_denied")


def test_concurrent_exceeded_denied():
    """current_concurrent 超限 denied,retry_after=0"""
    lim = ProviderLimiter(_make_limit(max_rpm=100, max_conc=2))
    dec = lim.check_rate_limit(concurrent_now=2, at=1000.0)
    assert dec.allowed is False
    assert dec.current_concurrent == 2
    assert dec.retry_after_seconds == 0.0
    assert "concurrent" in dec.reason.lower()
    print("  ✓ test_concurrent_exceeded_denied")


def test_acquire_slot_normal():
    """acquire_slot 正常获取"""
    lim = ProviderLimiter(_make_limit(max_conc=3))
    cm = lim.acquire_slot()
    assert cm is not None
    with cm:
        dec = lim.check_rate_limit(at=1000.0)
        assert dec.current_concurrent == 1
    # 释放后归 0
    assert lim._current_concurrent() == 0
    print("  ✓ test_acquire_slot_normal")


def test_acquire_slot_full_returns_none():
    """acquire_slot 满 → None"""
    lim = ProviderLimiter(_make_limit(max_conc=2))
    assert lim.acquire_slot() is not None
    assert lim.acquire_slot() is not None
    # 第三个:满
    assert lim.acquire_slot() is None
    print("  ✓ test_acquire_slot_full_returns_none")


def test_mark_429_is_in_cooldown_true():
    """mark_429 + is_in_cooldown True"""
    lim = ProviderLimiter(_make_limit())
    assert lim.is_in_cooldown(at=1000.0) is False
    lim.mark_429(duration_seconds=60.0, at=1000.0)
    assert lim.is_in_cooldown(at=1000.0) is True
    assert lim.is_in_cooldown(at=1030.0) is True
    print("  ✓ test_mark_429_is_in_cooldown_true")


def test_cooldown_expired_false():
    """cooldown 过期 → False"""
    lim = ProviderLimiter(_make_limit(cooldown=10.0))
    lim.mark_429(duration_seconds=10.0, at=1000.0)
    assert lim.is_in_cooldown(at=1005.0) is True
    assert lim.is_in_cooldown(at=1010.0) is False  # 边界:等于视为过期
    assert lim.is_in_cooldown(at=1020.0) is False
    print("  ✓ test_cooldown_expired_false")


def test_cooldown_blocks_all_requests():
    """cooldown 期内请求一律 denied"""
    lim = ProviderLimiter(_make_limit())
    lim.mark_429(duration_seconds=60.0, at=1000.0)
    dec = lim.check_rate_limit(at=1010.0)
    assert dec.allowed is False
    assert "cooldown" in dec.reason.lower()
    assert dec.retry_after_seconds is not None and dec.retry_after_seconds > 0
    print("  ✓ test_cooldown_blocks_all_requests")


def test_multi_provider_independent():
    """多 provider 独立限流"""
    multi = MultiProviderLimiter({
        "a": _make_limit(provider="a", max_rpm=2),
        "b": _make_limit(provider="b", max_rpm=5),
    })
    multi.record("a", request_count=2, at=1000.0)
    dec_a = multi.check("a", at=1000.0)
    dec_b = multi.check("b", at=1000.0)
    assert dec_a.allowed is False  # a 已满
    assert dec_b.allowed is True   # b 不受影响
    print("  ✓ test_multi_provider_independent")


def test_record_usage_appends():
    """record_usage 追加 history"""
    lim = ProviderLimiter(_make_limit())
    assert lim.history_size() == 0
    lim.record_usage(request_count=1, input_tokens=10, at=1000.0)
    lim.record_usage(request_count=1, input_tokens=20, at=1000.5)
    assert lim.history_size() == 2
    dec = lim.check_rate_limit(at=1000.5)
    assert dec.current_rpm == 2.0
    assert dec.current_ipm == 30.0
    print("  ✓ test_record_usage_appends")


def test_prune_outside_60s():
    """60s 外 prune"""
    lim = ProviderLimiter(_make_limit())
    lim.record_usage(request_count=1, at=1000.0)
    lim.record_usage(request_count=1, at=1030.0)
    lim.record_usage(request_count=1, at=1070.0)
    # 在 t=1100 查询:[1040, 1100] 窗口:只有 1070 那条
    dec = lim.check_rate_limit(at=1100.0)
    assert dec.current_rpm == 1.0
    assert lim.history_size() == 1
    print("  ✓ test_prune_outside_60s")


def test_empty_provider_no_error():
    """空 provider 不报错(用空字符串应被 dataclass 拒绝)"""
    try:
        ProviderLimit(provider="", max_requests_per_minute=10, max_inputs_per_minute=10, max_concurrent=1)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 合法空 usage:0 record 也能查
    lim = ProviderLimiter(_make_limit(provider="p1"))
    dec = lim.check_rate_limit(at=0.0)
    assert dec.allowed is True
    print("  ✓ test_empty_provider_no_error")


def test_zero_max_always_deny():
    """0 max → always deny"""
    lim = ProviderLimiter(_make_limit(max_rpm=0, max_ipm=0, max_conc=1))
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is False
    print("  ✓ test_zero_max_always_deny")


def test_json_serialization():
    """JSON 序列化(RateLimitDecision + MultiProviderLimiter snapshot)"""
    lim = ProviderLimiter(_make_limit())
    lim.record_usage(request_count=1, input_tokens=100, at=1000.0)
    dec = lim.check_rate_limit(at=1000.0)
    d = decision_to_dict(dec)
    assert d["allowed"] is True
    assert d["current_rpm"] == 1.0
    s = decision_to_json(dec)
    parsed = json.loads(s)
    assert parsed["current_ipm"] == 100.0

    multi = MultiProviderLimiter(make_default_limits())
    snap = snapshot_to_json(multi)
    snap_parsed = json.loads(snap)
    assert "openai" in snap_parsed
    assert "anthropic" in snap_parsed
    assert "together" in snap_parsed
    print("  ✓ test_json_serialization")


def test_at_none_uses_time():
    """边界:at=None → 用 time.time()"""
    lim = ProviderLimiter(_make_limit(max_rpm=100))
    # at=None 不应抛异常
    dec = lim.check_rate_limit()
    assert dec.allowed is True
    lim.record_usage()  # 全默认
    assert lim.history_size() == 1
    print("  ✓ test_at_none_uses_time")


def test_concurrency_safety_lock():
    """并发安全:多线程 acquire/release 不超 max_concurrent"""
    lim = ProviderLimiter(_make_limit(max_conc=5))
    held = []
    peak = {"v": 0}
    lock = threading.Lock()

    def worker():
        cm = lim.acquire_slot()
        if cm is None:
            return
        with cm:
            with lock:
                held.append(1)
                peak["v"] = max(peak["v"], lim._current_concurrent())
            # 模拟工作
            import time as _t
            _t.sleep(0.01)

    threads = [threading.Thread(target=worker) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # 峰值从未超过 max_concurrent=5
    assert peak["v"] <= 5
    assert lim._current_concurrent() == 0
    print(f"  ✓ test_concurrency_safety_lock (peak={peak['v']})")


def test_check_respects_concurrent_now():
    """check_rate_limit 用 caller 传入的 concurrent_now 决策"""
    lim = ProviderLimiter(_make_limit(max_conc=2))
    # 没传 → 用内部值(0)→ allowed
    dec = lim.check_rate_limit(at=1000.0)
    assert dec.allowed is True
    # 传入 2 → denied
    dec2 = lim.check_rate_limit(concurrent_now=2, at=1000.0)
    assert dec2.allowed is False
    # 传入 1 → 仍 allowed
    dec3 = lim.check_rate_limit(concurrent_now=1, at=1000.0)
    assert dec3.allowed is True
    print("  ✓ test_check_respects_concurrent_now")


def test_multi_provider_mark_429_isolated():
    """multi:一个 provider 进 cooldown 不影响其他"""
    multi = MultiProviderLimiter({
        "a": _make_limit(provider="a"),
        "b": _make_limit(provider="b"),
    })
    multi.mark_429("a", duration_seconds=60.0, at=1000.0)
    assert multi.is_in_cooldown("a", at=1010.0) is True
    assert multi.is_in_cooldown("b", at=1010.0) is False
    # b 的请求允许
    dec_b = multi.check("b", at=1010.0)
    assert dec_b.allowed is True
    print("  ✓ test_multi_provider_mark_429_isolated")


def test_multi_provider_acquire_slot():
    """multi.acquire_slot 路由到正确 provider"""
    multi = MultiProviderLimiter({
        "a": _make_limit(provider="a", max_conc=1),
        "b": _make_limit(provider="b", max_conc=2),
    })
    cm_a1 = multi.acquire_slot("a")
    assert cm_a1 is not None
    cm_a2 = multi.acquire_slot("a")
    assert cm_a2 is None  # a 已满
    cm_b1 = multi.acquire_slot("b")
    assert cm_b1 is not None
    cm_b2 = multi.acquire_slot("b")
    assert cm_b2 is not None
    cm_b3 = multi.acquire_slot("b")
    assert cm_b3 is None  # b 已满
    # 释放 a1 后 a 可再获
    with cm_a1:
        pass
    cm_a1b = multi.acquire_slot("a")
    assert cm_a1b is not None
    print("  ✓ test_multi_provider_acquire_slot")


def test_invalid_limit_raises():
    """ProviderLimit 参数校验"""
    # max_rpm 负数
    try:
        ProviderLimit(provider="x", max_requests_per_minute=-1, max_inputs_per_minute=10, max_concurrent=1)
        raise AssertionError()
    except ValueError:
        pass
    # max_concurrent 负数
    try:
        ProviderLimit(provider="x", max_requests_per_minute=10, max_inputs_per_minute=10, max_concurrent=-5)
        raise AssertionError()
    except ValueError:
        pass
    # cooldown 负数
    try:
        ProviderLimit(provider="x", max_requests_per_minute=10, max_inputs_per_minute=10, max_concurrent=1, cooldown_seconds_after_429=-1.0)
        raise AssertionError()
    except ValueError:
        pass
    print("  ✓ test_invalid_limit_raises")


def test_mark_429_extends_existing_cooldown():
    """后续 429 应延长 cooldown(monotonic)"""
    lim = ProviderLimiter(_make_limit())
    lim.mark_429(duration_seconds=10.0, at=1000.0)
    # 在 t=1005 再来一次 429,持续 20s → until 应是 1025
    lim.mark_429(duration_seconds=20.0, at=1005.0)
    # 1005+20=1025,比原 until=1010 大,被更新
    assert lim.is_in_cooldown(at=1020.0) is True
    # 1030 已过新 cooldown
    assert lim.is_in_cooldown(at=1030.0) is False
    print("  ✓ test_mark_429_extends_existing_cooldown")


def test_make_default_limits_has_three_providers():
    """make_default_limits 含 3 个 provider"""
    defaults = make_default_limits()
    assert set(defaults.keys()) == {"openai", "anthropic", "together"}
    for p, lim in defaults.items():
        assert lim.provider == p
        assert lim.max_requests_per_minute > 0
    print("  ✓ test_make_default_limits_has_three_providers")


def test_history_pruned_on_check():
    """check 时自动 prune"""
    lim = ProviderLimiter(_make_limit(max_rpm=100))
    # 注入 100s 前的记录
    lim.record_usage(request_count=5, at=900.0)
    # 30s 前的也在窗口外
    lim.record_usage(request_count=1, at=1060.0)
    # 当前的
    lim.record_usage(request_count=2, at=1080.0)
    # 在 t=1100 检查:1080 在窗口内,1060 也在窗口内(1100-60=1040),900 已外
    dec = lim.check_rate_limit(at=1100.0)
    assert dec.current_rpm == 3.0
    # history 应只剩 2 条
    assert lim.history_size() == 2
    print("  ✓ test_history_pruned_on_check")


# ============ Main ============

def main():
    tests = [
        test_zero_usage_allowed,
        test_one_request_current_rpm,
        test_rpm_at_max_allowed,
        test_rpm_exceeded_denied,
        test_retry_after_rpm,
        test_retry_after_ipm,
        test_ipm_exceeded_denied,
        test_concurrent_exceeded_denied,
        test_acquire_slot_normal,
        test_acquire_slot_full_returns_none,
        test_mark_429_is_in_cooldown_true,
        test_cooldown_expired_false,
        test_cooldown_blocks_all_requests,
        test_multi_provider_independent,
        test_record_usage_appends,
        test_prune_outside_60s,
        test_empty_provider_no_error,
        test_zero_max_always_deny,
        test_json_serialization,
        test_at_none_uses_time,
        test_concurrency_safety_lock,
        test_check_respects_concurrent_now,
        test_multi_provider_mark_429_isolated,
        test_multi_provider_acquire_slot,
        test_invalid_limit_raises,
        test_mark_429_extends_existing_cooldown,
        test_make_default_limits_has_three_providers,
        test_history_pruned_on_check,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"  ✗ {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n  total: {passed} passed, {failed} failed, {len(tests)} total")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
