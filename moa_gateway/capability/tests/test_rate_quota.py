"""rate_quota 真实测试(非 mock,全部 assert)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.rate_quota import (
    VALID_WINDOW_NAMES,
    WINDOW_5H_SECONDS,
    WINDOW_DURATIONS,
    WINDOW_MONTHLY_SECONDS,
    WINDOW_WEEKLY_SECONDS,
    QuotaState,
    QuotaWindow,
    check_available,
    eta_exhaustion,
    make_default_state,
    prune_all,
    record_usage,
    rolling_remaining,
    would_exceed_within,
)

# ============ Tests ============

def test_empty_quota_available():
    """空 quota(0 used)→ check 大请求也能通过(在 limit 内)"""
    state = make_default_state(limit_5h=10000, limit_weekly=100000, limit_monthly=500000, at=100.0)
    ok, reason = check_available(state, requested=500)
    assert ok is True
    assert "ok" in reason
    # 5h 窗口 10000,5h 窗口 free=10000 >= 500
    assert "5h" in reason
    print(f"  ✓ test_empty_quota_available (reason={reason!r})")


def test_record_usage_updates_used_tokens():
    """record_usage 推进 used_tokens"""
    state = make_default_state(limit_5h=10000, limit_weekly=100000, limit_monthly=500000, at=1000.0)
    record_usage(state, tokens=300, at=1000.0)
    assert state.windows["5h"].used_tokens == 300
    assert state.windows["weekly"].used_tokens == 300
    assert state.windows["monthly"].used_tokens == 300
    # history 也被追加
    assert len(state.windows["5h"].used_history) == 1
    assert state.windows["5h"].used_history[0] == (1000.0, 300)
    # last_updated 更新
    assert state.last_updated == 1000.0
    print("  ✓ test_record_usage_updates_used_tokens (all 3 windows = 300)")


def test_5h_sliding_window_prune():
    """5h 滑窗:超出 5h 的 history 会被 prune"""
    state = make_default_state(limit_5h=10000, limit_weekly=1000000, limit_monthly=4000000, at=0.0)
    # t=0 用 100
    record_usage(state, tokens=100, at=0.0)
    # t=4h 用 200 (still within 5h)
    record_usage(state, tokens=200, at=4 * 3600)
    # 此时 5h 窗口 used = 100 + 200 = 300
    assert state.windows["5h"].used_tokens == 300
    # 跳到 t=6h,5h 窗口外(只看 [1h, 6h]) → t=0 的 100 被 prune
    record_usage(state, tokens=50, at=6 * 3600)
    assert state.windows["5h"].used_tokens == 200 + 50  # 250
    # 5h history 应只含 2 条(4h 的 200 + 6h 的 50)
    assert len(state.windows["5h"].used_history) == 2
    # weekly 窗口全保留(6h << 7d)
    assert state.windows["weekly"].used_tokens == 350
    assert len(state.windows["weekly"].used_history) == 3
    print(f"  ✓ test_5h_sliding_window_prune (5h used={state.windows['5h'].used_tokens}, history len={len(state.windows['5h'].used_history)})")


def test_weekly_window_prune():
    """weekly(7d)滑窗:超出 7 天的 history 会被 prune"""
    state = make_default_state(limit_5h=10000, limit_weekly=1000000, limit_monthly=4000000, at=0.0)
    # t=0 用 100
    record_usage(state, tokens=100, at=0.0)
    # t=5d 用 200 (still within weekly)
    record_usage(state, tokens=200, at=5 * 86400)
    assert state.windows["weekly"].used_tokens == 300
    # t=6d 用 50
    record_usage(state, tokens=50, at=6 * 86400)
    assert state.windows["weekly"].used_tokens == 350
    # t=8d 用 80 → 0/5d/6d 三个都超过 7d 窗口(只看 [1d, 8d])
    # 5d 在 [1d, 8d] 内,6d 在 [1d, 8d] 内,0d 被 prune
    record_usage(state, tokens=80, at=8 * 86400)
    assert state.windows["weekly"].used_tokens == 200 + 50 + 80  # 330
    assert len(state.windows["weekly"].used_history) == 3
    # 5h 窗口(以 8d 算 now)→ 所有 4 条都超出 5h,used=80
    assert state.windows["5h"].used_tokens == 80
    assert len(state.windows["5h"].used_history) == 1
    print(f"  ✓ test_weekly_window_prune (weekly used={state.windows['weekly'].used_tokens}, 5h used={state.windows['5h'].used_tokens})")


def test_monthly_window_prune():
    """monthly(30d)滑窗:超出 30 天的 history 会被 prune"""
    state = make_default_state(limit_5h=10000, limit_weekly=1000000, limit_monthly=4000000, at=0.0)
    # t=0 用 500
    record_usage(state, tokens=500, at=0.0)
    # t=20d 用 300
    record_usage(state, tokens=300, at=20 * 86400)
    # t=29d 用 100
    record_usage(state, tokens=100, at=29 * 86400)
    # 此时 monthly used = 500 + 300 + 100 = 900
    assert state.windows["monthly"].used_tokens == 900
    # t=31d 用 200 → 0d 的 500 被 prune(now-cutoff = 31d-30d = 1d;0d < 1d 被剪)
    # 20d(在 [1d,31d]),29d(在 [1d,31d])保留
    record_usage(state, tokens=200, at=31 * 86400)
    assert state.windows["monthly"].used_tokens == 300 + 100 + 200  # 600
    assert len(state.windows["monthly"].used_history) == 3
    # weekly 窗口(now=31d,只看 [24d,31d])→ 只 29d 的 100
    assert state.windows["weekly"].used_tokens == 100 + 200  # 300(29d + 31d)
    # 5h 窗口(只看 [31d-5h, 31d])→ 0 条(差得远),但本步加的 200 在 now 处,会保留
    # 等等:5h 窗口 = 5*3600 = 18000s;31d = 31*86400s;cutoff = 31d - 5h
    # 29d(29*86400s)在 cutoff 之前 → prune
    # 31d 的 200 在 cutoff 之后 → 保留
    assert state.windows["5h"].used_tokens == 200
    print(f"  ✓ test_monthly_window_prune (monthly used={state.windows['monthly'].used_tokens}, weekly used={state.windows['weekly'].used_tokens})")


def test_check_available_multi_window_pass():
    """check_available: 多窗口时至少 1 个通过即 True"""
    # 5h 已用 9500/10000,但 weekly 只用 1000/100000
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=10000, used_tokens=9500,
                              used_history=[(100.0, 9500)]),
            "weekly": QuotaWindow(name="weekly", limit_tokens=100000, used_tokens=1000,
                                  used_history=[(100.0, 1000)]),
            "monthly": QuotaWindow(name="monthly", limit_tokens=500000, used_tokens=1000,
                                   used_history=[(100.0, 1000)]),
        },
        last_updated=100.0,
    )
    # 请求 2000 tokens:5h 只剩 500,不够;weekly 还有 99000,够
    ok, reason = check_available(state, requested=2000)
    assert ok is True, f"expected ok, got {reason}"
    assert "ok" in reason
    assert "weekly" in reason
    print(f"  ✓ test_check_available_multi_window_pass (reason={reason!r})")


def test_check_available_all_exceeded():
    """check_available: 全窗口超限 → False + reason 含 'insufficient'"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=10000, used_tokens=9500,
                              used_history=[(100.0, 9500)]),
            "weekly": QuotaWindow(name="weekly", limit_tokens=100000, used_tokens=99999,
                                  used_history=[(100.0, 99999)]),
            "monthly": QuotaWindow(name="monthly", limit_tokens=500000, used_tokens=499999,
                                   used_history=[(100.0, 499999)]),
        },
        last_updated=100.0,
    )
    # 请求 1000:5h 只剩 500,weekly 只剩 1,monthly 只剩 1 → 全 fail
    ok, reason = check_available(state, requested=1000)
    assert ok is False
    assert "insufficient" in reason
    # 最紧窗口是 weekly/monthly(gap = 1-1000 = -999),5h 是 -500
    assert "weekly" in reason or "monthly" in reason
    print(f"  ✓ test_check_available_all_exceeded (reason={reason!r})")


def test_eta_exhaustion_already_exhausted():
    """eta_exhaustion: 已耗尽(remaining<=0)→ None"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=1000,
                              used_history=[(100.0, 1000)]),
        },
        last_updated=100.0,
    )
    eta = eta_exhaustion(state, burn_rate_per_hour=100.0, window_name="5h")
    assert eta is None
    print("  ✓ test_eta_exhaustion_already_exhausted (eta=None)")


def test_eta_exhaustion_normal_burn_rate():
    """eta_exhaustion: 正常 burn_rate 算 hours = remaining / rate"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=400,
                              used_history=[(100.0, 400)]),
        },
        last_updated=100.0,
    )
    # remaining = 600, burn_rate = 100/h → eta = 6h
    eta = eta_exhaustion(state, burn_rate_per_hour=100.0, window_name="5h")
    assert eta is not None
    assert abs(eta - 6.0) < 1e-9, f"eta {eta} != 6.0"
    print(f"  ✓ test_eta_exhaustion_normal_burn_rate (eta={eta}h)")


def test_eta_exhaustion_zero_burn_rate():
    """eta_exhaustion: burn_rate=0 → None(无法估算)"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=400,
                              used_history=[(100.0, 400)]),
        },
        last_updated=100.0,
    )
    eta = eta_exhaustion(state, burn_rate_per_hour=0.0, window_name="5h")
    assert eta is None
    print("  ✓ test_eta_exhaustion_zero_burn_rate (eta=None)")


def test_would_exceed_within_accurate():
    """would_exceed_within: 预测 horizon 小时内是否超限"""
    state = make_default_state(limit_5h=1000, limit_weekly=10000, limit_monthly=100000, at=0.0)
    record_usage(state, tokens=500, at=0.0)  # 5h used=500
    # 不超:0 requested, 1h horizon, 100/h burn → 5h 预测 = 500 + 0 + 100 = 600 < 1000
    assert would_exceed_within(state, requested=0, horizon_hours=1.0, burn_rate=100.0) is False
    # 超:0 requested, 10h horizon, 100/h burn → 5h 预测 = 500 + 0 + 1000 = 1500 > 1000
    assert would_exceed_within(state, requested=0, horizon_hours=10.0, burn_rate=100.0) is True
    # 超:500 requested(立刻爆)
    assert would_exceed_within(state, requested=500, horizon_hours=0.0, burn_rate=0.0) is False  # 正好 1000 == limit,不超
    assert would_exceed_within(state, requested=501, horizon_hours=0.0, burn_rate=0.0) is True
    print("  ✓ test_would_exceed_within_accurate (False/True/False/True)")


def test_rolling_remaining_realtime():
    """rolling_remaining: 实时算 = limit - 窗口内 history sum"""
    state = make_default_state(limit_5h=1000, limit_weekly=10000, limit_monthly=100000, at=0.0)
    # t=0 用 300,t=1h 用 200,t=3h 用 100
    record_usage(state, tokens=300, at=0.0)
    record_usage(state, tokens=200, at=1 * 3600)
    record_usage(state, tokens=100, at=3 * 3600)
    # 在 t=3h,5h 窗口 used = 300+200+100 = 600 → remaining = 400
    assert rolling_remaining(state, "5h") == 400
    # weekly 只看 [3h-7d, 3h] → 全含 → used = 600, remaining = 9400
    assert rolling_remaining(state, "weekly") == 10000 - 600
    # 跳到 t=4h,5h 窗口 used = 300+200+100 = 600 仍全含,remaining=400
    record_usage(state, tokens=0, at=4 * 3600)  # 0 token,但更新 last_updated
    assert rolling_remaining(state, "5h") == 400
    # 跳到 t=6h,5h 窗口(只看 [1h, 6h])→ 1h 的 200 + 3h 的 100 + 4h 的 0 = 300
    # 0h 的 300 被 prune
    record_usage(state, tokens=0, at=6 * 3600)
    assert rolling_remaining(state, "5h") == 1000 - 300
    # weekly 在 t=6h,全含 → 600
    assert rolling_remaining(state, "weekly") == 10000 - 600
    print(f"  ✓ test_rolling_remaining_realtime (5h@6h: {rolling_remaining(state, '5h')}, weekly@6h: {rolling_remaining(state, 'weekly')})")


def test_boundary_equal_to_limit_ok():
    """边界:used 等于 limit,check_available(requested=0) → ok"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=1000,
                              used_history=[(100.0, 1000)]),
        },
        last_updated=100.0,
    )
    # requested=0,所有窗口都满足(0 >= 0)
    ok, reason = check_available(state, requested=0)
    assert ok is True
    # 但 requested=1,5h 0 free → fail
    ok2, reason2 = check_available(state, requested=1)
    assert ok2 is False
    assert "insufficient" in reason2
    print("  ✓ test_boundary_equal_to_limit_ok (0→ok, 1→fail)")


def test_boundary_exceed_by_one_token():
    """边界:超过 1 token → check 不通过"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=999,
                              used_history=[(100.0, 999)]),
            "weekly": QuotaWindow(name="weekly", limit_tokens=10000, used_tokens=9999,
                                  used_history=[(100.0, 9999)]),
            "monthly": QuotaWindow(name="monthly", limit_tokens=100000, used_tokens=99999,
                                   used_history=[(100.0, 99999)]),
        },
        last_updated=100.0,
    )
    # 5h 剩 1, weekly 剩 1, monthly 剩 1
    # 请求 1:所有窗口 free >= 1 → ok
    ok, _ = check_available(state, requested=1)
    assert ok is True
    # 请求 2:全 fail
    ok2, reason2 = check_available(state, requested=2)
    assert ok2 is False
    assert "insufficient" in reason2
    # 3 个窗口 gap 相同(-1),取第一个(按 dict 顺序)
    assert "5h" in reason2
    print(f"  ✓ test_boundary_exceed_by_one_token (1→ok, 2→fail, reason={reason2!r})")


def test_quota_window_validation():
    """QuotaWindow 验证:name/limit/used/history 范围"""
    # 非法 name
    try:
        QuotaWindow(name="hourly", limit_tokens=1000)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 非法 limit
    try:
        QuotaWindow(name="5h", limit_tokens=0)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 负 used
    try:
        QuotaWindow(name="5h", limit_tokens=1000, used_tokens=-1)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 负 history tokens
    try:
        QuotaWindow(name="5h", limit_tokens=1000, used_history=[(100.0, -1)])
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 负 timestamp
    try:
        QuotaWindow(name="5h", limit_tokens=1000, used_history=[(-1.0, 100)])
        raise AssertionError("should have raised")
    except ValueError:
        pass
    print("  ✓ test_quota_window_validation (5 invalid inputs rejected)")


def test_quota_state_validation():
    """QuotaState 验证:windows 非空 + 名字合法 + last_updated >= 0"""
    # 空 windows
    try:
        QuotaState(windows={}, last_updated=0.0)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 未知 name
    try:
        QuotaState(
            windows={"badname": QuotaWindow(name="5h", limit_tokens=1000)},
            last_updated=0.0,
        )
        raise AssertionError("should have raised")
    except ValueError:
        pass
    # 负 last_updated
    try:
        state_test = make_default_state()
        QuotaState(windows=state_test.windows, last_updated=-1.0)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    print("  ✓ test_quota_state_validation (3 invalid inputs rejected)")


def test_record_usage_negative_tokens_rejected():
    """record_usage 拒绝负 tokens"""
    state = make_default_state(at=100.0)
    try:
        record_usage(state, tokens=-1, at=100.0)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    try:
        record_usage(state, tokens=100, at=-1.0)
        raise AssertionError("should have raised")
    except ValueError:
        pass
    print("  ✓ test_record_usage_negative_tokens_rejected")


def test_would_exceed_within_sliding_window():
    """would_exceed_within:考虑 history 滑窗(只看窗口内)"""
    state = make_default_state(limit_5h=1000, limit_weekly=10000, limit_monthly=100000, at=0.0)
    # t=0 用 900(5h 几乎用尽)
    record_usage(state, tokens=900, at=0.0)
    # t=6h 5h 窗口外(只看 [1h,6h])→ used=0
    # 但 monthly 还含
    # 在 t=6h,horizon=0, requested=50, burn=0
    # 5h used_in_window(now=6h) = 0, 预测 = 0+50+0 = 50 < 1000 → not exceed
    record_usage(state, tokens=50, at=6 * 3600)
    assert would_exceed_within(state, requested=0, horizon_hours=0.0, burn_rate=0.0) is False
    # 但 monthly 窗口(now=6h)→ 5h-30d 区间,0h 的 900 + 6h 的 50 = 950
    # horizon=0, requested=100 → 950+100 = 1050 > 100000? no,远小于 100000
    # 改 limit: monthly = 1000
    state2 = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=0),
            "weekly": QuotaWindow(name="weekly", limit_tokens=10000, used_tokens=0),
            "monthly": QuotaWindow(name="monthly", limit_tokens=1000, used_tokens=0),
        },
        last_updated=0.0,
    )
    record_usage(state2, tokens=900, at=0.0)
    # 在 t=6h, monthly now=6h,cutoff=6h-30d,used_in_window=900
    record_usage(state2, tokens=50, at=6 * 3600)
    # monthly used = 900+50=950;horizon=0, requested=100 → 950+100=1050 > 1000
    assert would_exceed_within(state2, requested=100, horizon_hours=0.0, burn_rate=0.0) is True
    # 但 5h now=6h, used=50(0h 被剪);horizon=0, requested=100 → 50+100=150 < 1000 → not exceed
    # (但 monthly 决定 True)
    print("  ✓ test_would_exceed_within_sliding_window (monthly catches it)")


def test_eta_exhaustion_with_sliding_history():
    """eta_exhaustion 用 rolling_remaining(基于滑窗)"""
    state = make_default_state(limit_5h=1000, limit_weekly=10000, limit_monthly=100000, at=0.0)
    # t=0 用 800(5h)
    record_usage(state, tokens=800, at=0.0)
    # 在 t=0,5h remaining=200,eta = 200/100 = 2h
    assert abs(eta_exhaustion(state, 100.0, "5h") - 2.0) < 1e-9
    # 跳到 t=4h,5h 窗口仍有 800,remaining=200,eta=2h
    record_usage(state, tokens=0, at=4 * 3600)
    assert abs(eta_exhaustion(state, 100.0, "5h") - 2.0) < 1e-9
    # 跳到 t=6h,5h 窗口(只看 [1h,6h])→ 4h 的 0 used → remaining=1000,eta=10h
    record_usage(state, tokens=0, at=6 * 3600)
    assert abs(eta_exhaustion(state, 100.0, "5h") - 10.0) < 1e-9
    print("  ✓ test_eta_exhaustion_with_sliding_history (2h → 2h → 10h)")


def test_make_default_state_constants():
    """make_default_state 用正确默认值 + 窗口 duration 常量正确"""
    assert WINDOW_5H_SECONDS == 18000
    assert WINDOW_WEEKLY_SECONDS == 604800
    assert WINDOW_MONTHLY_SECONDS == 2592000
    assert WINDOW_DURATIONS["5h"] == 18000
    assert WINDOW_DURATIONS["weekly"] == 604800
    assert WINDOW_DURATIONS["monthly"] == 2592000
    assert VALID_WINDOW_NAMES == ("5h", "weekly", "monthly")
    state = make_default_state()
    assert state.windows["5h"].limit_tokens == 100_000
    assert state.windows["weekly"].limit_tokens == 1_000_000
    assert state.windows["monthly"].limit_tokens == 4_000_000
    assert state.last_updated == 0.0
    print("  ✓ test_make_default_state_constants (durations + defaults)")


def test_prune_all_standalone():
    """prune_all 独立可调用"""
    state = QuotaState(
        windows={
            "5h": QuotaWindow(name="5h", limit_tokens=1000, used_tokens=500,
                              used_history=[(100.0, 100), (200.0, 200), (20000.0, 200)]),
        },
        last_updated=200.0,
    )
    # 现在 used=500,history 3 条
    # prune_all(at=10h+5h=41400s 即 now=41400)→ 100s 在 [41400-18000, 41400]=[23400, 41400]外,prune
    prune_all(state, at=11 * 3600)  # 11h = 39600s;cutoff=39600-18000=21600s
    # 100 < 21600, 200 < 21600 → prune;20000 < 21600? 否,20000 < 21600 → prune
    # 等等,20000 < 21600,也对
    # 但 last_updated=200 时 history 都是 [(100,100),(200,200),(20000,200)]
    # prune_all(at=39600): cutoff=21600;全 prune
    # 但 prune_all 不写 history(只 prune + recompute),也不写 used 增量
    # 所以 used 应重算为 0
    assert state.windows["5h"].used_tokens == 0
    assert len(state.windows["5h"].used_history) == 0
    assert state.last_updated == 11 * 3600
    print("  ✓ test_prune_all_standalone (all 3 entries pruned, used=0)")


# ============ runner ============

def main() -> int:
    tests = [
        test_empty_quota_available,
        test_record_usage_updates_used_tokens,
        test_5h_sliding_window_prune,
        test_weekly_window_prune,
        test_monthly_window_prune,
        test_check_available_multi_window_pass,
        test_check_available_all_exceeded,
        test_eta_exhaustion_already_exhausted,
        test_eta_exhaustion_normal_burn_rate,
        test_eta_exhaustion_zero_burn_rate,
        test_would_exceed_within_accurate,
        test_rolling_remaining_realtime,
        test_boundary_equal_to_limit_ok,
        test_boundary_exceed_by_one_token,
        test_quota_window_validation,
        test_quota_state_validation,
        test_record_usage_negative_tokens_rejected,
        test_would_exceed_within_sliding_window,
        test_eta_exhaustion_with_sliding_history,
        test_make_default_state_constants,
        test_prune_all_standalone,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  ✗ {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}: EXCEPTION {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
