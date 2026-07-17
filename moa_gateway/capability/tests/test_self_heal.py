"""self_heal 真实测试(非 mock,全部 assert)"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.self_heal import (
    DEFAULT_COOLDOWN_SECONDS,
    auto_balance,
    check_recovery,
    demote,
    enter_cooldown,
    get_available_endpoints,
    make_default_state,
    promote,
    record_failure,
    record_success,
    state_from_dict,
    state_to_dict,
)

# ============ Tests ============

def test_record_success_clears_consecutive_failures():
    """record_success 把 consecutive_failures 清零"""
    state = make_default_state(["ep1"])
    # 先记 2 次失败
    record_failure(state, "ep1", at=100.0)
    record_failure(state, "ep1", at=101.0)
    assert state.get("ep1").consecutive_failures == 2
    # 一次成功归零
    action = record_success(state, "ep1", at=102.0)
    assert action.action == "no_op"
    assert state.get("ep1").consecutive_failures == 0
    assert state.get("ep1").last_success_at == 102.0
    assert state.get("ep1").total_calls == 3  # 2 failure + 1 success
    print("  ✓ test_record_success_clears_consecutive_failures")


def test_record_success_during_cooldown_no_immediate_recover():
    """record_success 在 cooldown 中未到期 → no_op,不立即 recover"""
    state = make_default_state(["ep1"])
    # 3 次失败触发 cooldown(默认 5 min)
    for i in range(3):
        record_failure(state, "ep1", at=100.0 + i)
    ep = state.get("ep1")
    assert ep.in_cooldown is True
    assert ep.tier == "secondary"  # demoted
    assert ep.cooldown_until is not None
    # 在 cooldown 中(未到 5 min 后)记一次成功
    action = record_success(state, "ep1", at=101.0)
    assert action.action == "no_op"
    assert "cooldown" in action.reason
    assert ep.in_cooldown is True  # 仍在 cooldown
    assert ep.consecutive_failures == 0  # 失败计数还是归零
    print("  ✓ test_record_success_during_cooldown_no_immediate_recover")


def test_record_failure_single_no_cooldown():
    """单次 failure < 阈值 → 不进 cooldown"""
    state = make_default_state(["ep1"])
    action = record_failure(state, "ep1", at=100.0)
    assert action.action == "no_op"
    ep = state.get("ep1")
    assert ep.in_cooldown is False
    assert ep.cooldown_until is None
    assert ep.consecutive_failures == 1
    assert ep.total_failures == 1
    assert ep.total_calls == 1
    print("  ✓ test_record_failure_single_no_cooldown")


def test_record_failure_three_consecutive_triggers_cooldown():
    """连续 3 次失败 → 触发 cooldown + demote"""
    state = make_default_state(["ep1"], failure_threshold=3)
    actions = []
    for i in range(3):
        actions.append(record_failure(state, "ep1", at=100.0 + i))
    ep = state.get("ep1")
    assert ep.in_cooldown is True
    assert ep.tier == "secondary"  # primary → secondary
    assert ep.cooldown_until == 100.0 + 2 + DEFAULT_COOLDOWN_SECONDS  # 末次失败 at + 300
    assert ep.consecutive_failures == 3
    # 第 1, 2 次都是 no_op(1/3, 2/3)
    assert actions[0].action == "no_op"
    assert actions[1].action == "no_op"
    # 第 3 次是 demote
    assert actions[2].action == "demote"
    assert "primary → secondary" in actions[2].reason
    print("  ✓ test_record_failure_three_consecutive_triggers_cooldown")


def test_record_failure_cooldown_sets_cooldown_until():
    """record_failure cooldown 设置 cooldown_until = now + duration"""
    state = make_default_state(["ep1"], cooldown_seconds=600.0)
    for i in range(3):
        record_failure(state, "ep1", at=1000.0 + i)
    ep = state.get("ep1")
    assert ep.cooldown_until == 1000.0 + 2 + 600.0  # 1602.0
    assert ep.in_cooldown is True
    print(f"  ✓ test_record_failure_cooldown_sets_cooldown_until (until={ep.cooldown_until})")


def test_check_recovery_not_expired():
    """check_recovery:未到期 → no_op"""
    state = make_default_state(["ep1"])
    for i in range(3):
        record_failure(state, "ep1", at=100.0 + i)
    # cooldown_until = 100+2+300 = 402
    ep = state.get("ep1")
    assert ep.in_cooldown is True
    # 在 cooldown 中(在 402 之前)检查
    action = check_recovery(state, "ep1", at=300.0)
    assert action.action == "no_op"
    assert ep.in_cooldown is True  # 仍 cooldown
    print("  ✓ test_check_recovery_not_expired")


def test_check_recovery_expired_recovers_and_promotes():
    """check_recovery:到期 → recover + promote 回 original_tier"""
    state = make_default_state(["ep1"])
    for i in range(3):
        record_failure(state, "ep1", at=100.0 + i)
    ep = state.get("ep1")
    # 此时 ep.tier == "secondary",in_cooldown,cooldown_until=402
    # 检查在 402 之后
    action = check_recovery(state, "ep1", at=500.0)
    assert action.action == "recover"
    assert ep.in_cooldown is False
    assert ep.cooldown_until is None
    assert ep.tier == "primary"  # promote 回 original
    assert ep.consecutive_failures == 0
    print("  ✓ test_check_recovery_expired_recovers_and_promotes")


def test_promote_secondary_to_primary():
    """promote secondary → primary"""
    state = make_default_state(["ep1"])
    state.get("ep1").tier = "secondary"
    action = promote(state, "ep1", reason="manual", at=100.0)
    assert action.action == "promote"
    assert "secondary → primary" in action.reason
    assert state.get("ep1").tier == "primary"
    print("  ✓ test_promote_secondary_to_primary")


def test_promote_primary_is_no_op():
    """promote primary → primary(no_op)"""
    state = make_default_state(["ep1"])
    action = promote(state, "ep1", reason="redundant", at=100.0)
    assert action.action == "no_op"
    assert "already primary" in action.reason
    assert state.get("ep1").tier == "primary"
    print("  ✓ test_promote_primary_is_no_op")


def test_demote_primary_to_secondary():
    """demote primary → secondary"""
    state = make_default_state(["ep1"])
    action = demote(state, "ep1", reason="manual", at=100.0)
    assert action.action == "demote"
    assert "primary → secondary" in action.reason
    assert state.get("ep1").tier == "secondary"
    print("  ✓ test_demote_primary_to_secondary")


def test_demote_fallback_stays_fallback():
    """demote fallback → fallback(no_op)"""
    state = make_default_state(["ep1"])
    state.get("ep1").tier = "fallback"
    action = demote(state, "ep1", reason="redundant", at=100.0)
    assert action.action == "no_op"
    assert "already fallback" in action.reason
    assert state.get("ep1").tier == "fallback"
    print("  ✓ test_demote_fallback_stays_fallback")


def test_enter_cooldown_state_correct():
    """enter_cooldown 状态正确"""
    state = make_default_state(["ep1"])
    action = enter_cooldown(state, "ep1", duration_seconds=120.0, reason="manual", at=100.0)
    assert action.action == "cooldown"
    assert "120" in action.reason
    ep = state.get("ep1")
    assert ep.in_cooldown is True
    assert ep.cooldown_until == 100.0 + 120.0  # 220.0
    print("  ✓ test_enter_cooldown_state_correct (until=220.0)")


def test_get_available_excludes_cooldown():
    """get_available_endpoints 排除 cooldown 中的"""
    state = make_default_state(["ep1", "ep2", "ep3"])
    # ep1 进 cooldown
    for i in range(3):
        record_failure(state, "ep1", at=100.0 + i)
    avail = get_available_endpoints(state)
    assert "ep1" not in avail
    assert "ep2" in avail
    assert "ep3" in avail
    print("  ✓ test_get_available_excludes_cooldown")


def test_get_available_excludes_disabled():
    """get_available_endpoints 排除 disabled"""
    state = make_default_state(["ep1", "ep2"])
    state.get("ep2").enabled = False
    avail = get_available_endpoints(state)
    assert "ep1" in avail
    assert "ep2" not in avail
    print("  ✓ test_get_available_excludes_disabled")


def test_auto_balance_triggers_check_recovery_multiple():
    """auto_balance 触发 check_recovery 多个"""
    state = make_default_state(["ep1", "ep2", "ep3"])
    # 3 个 endpoint 全进 cooldown(at=100, 200, 300)
    for i in range(3):
        record_failure(state, "ep1", at=100.0 + i)
    for i in range(3):
        record_failure(state, "ep2", at=200.0 + i)
    for i in range(3):
        record_failure(state, "ep3", at=300.0 + i)
    # ep1 cooldown_until = 402
    # ep2 cooldown_until = 502
    # ep3 cooldown_until = 602
    # 在 t=700 跑 auto_balance
    actions = auto_balance(state, at=700.0)
    # 3 个都应 recover
    assert len(actions) == 3
    recovered_ids = {a.endpoint_id for a in actions}
    assert recovered_ids == {"ep1", "ep2", "ep3"}
    for a in actions:
        assert a.action == "recover"
    # 状态都清 cooldown,tier 回到 primary
    for eid in ["ep1", "ep2", "ep3"]:
        ep = state.get(eid)
        assert ep.in_cooldown is False
        assert ep.tier == "primary"
    # last_auto_balance_at 更新
    assert state.last_auto_balance_at == 700.0
    print("  ✓ test_auto_balance_triggers_check_recovery_multiple (3 recovered)")


def test_full_flow_failure_cooldown_recovery():
    """完整流程:failure → cooldown → 时间旅行 → recovery"""
    state = make_default_state(["ep1"], failure_threshold=3, cooldown_seconds=60.0)
    # t=0..2 三次失败
    for i in range(3):
        record_failure(state, "ep1", at=float(i))
    ep = state.get("ep1")
    # 触发 cooldown,tier=secondary,cooldown_until=60+2=62
    assert ep.in_cooldown is True
    assert ep.tier == "secondary"
    assert ep.cooldown_until == 62.0
    assert ep.consecutive_failures == 3
    # 路由视角:cooldown 中,不可用
    assert "ep1" not in get_available_endpoints(state)
    # 时间旅行到 t=100
    action = auto_balance(state, at=100.0)
    assert len(action) == 1
    assert action[0].action == "recover"
    assert ep.in_cooldown is False
    assert ep.tier == "primary"
    # 路由视角:恢复可用
    assert "ep1" in get_available_endpoints(state)
    print("  ✓ test_full_flow_failure_cooldown_recovery (3 fails → cooldown → recover)")


def test_state_serialization_roundtrip():
    """state 序列化往返"""
    state = make_default_state(["ep1", "ep2"], cooldown_seconds=120.0)
    for i in range(3):
        record_failure(state, "ep1", at=100.0 + i)
    record_success(state, "ep2", at=200.0)
    # 序列化
    data = state_to_dict(state)
    assert "endpoints" in data
    assert "log" in data
    assert "cooldown_seconds" in data
    assert data["endpoints"]["ep1"]["in_cooldown"] is True
    assert data["endpoints"]["ep1"]["tier"] == "secondary"
    # 反序列化
    state2 = state_from_dict(data)
    ep1 = state2.get("ep1")
    assert ep1.in_cooldown is True
    assert ep1.cooldown_until == 100.0 + 2 + 120.0
    assert ep1.tier == "secondary"
    assert ep1.consecutive_failures == 3
    ep2 = state2.get("ep2")
    assert ep2.consecutive_failures == 0
    assert ep2.last_success_at == 200.0
    # log 也保留
    assert len(state2.log.actions) == len(state.log.actions)
    assert state2.cooldown_seconds == 120.0
    print(f"  ✓ test_state_serialization_roundtrip (log len={len(state2.log.actions)})")


def test_multiple_endpoints_independent_state():
    """多 endpoint 独立状态"""
    state = make_default_state(["ep1", "ep2", "ep3"])
    # ep1 失败 2 次(不触发 cooldown)
    record_failure(state, "ep1", at=100.0)
    record_failure(state, "ep1", at=101.0)
    # ep2 失败 3 次(触发 cooldown)
    for i in range(3):
        record_failure(state, "ep2", at=200.0 + i)
    # ep3 全成功
    record_success(state, "ep3", at=300.0)
    record_success(state, "ep3", at=301.0)
    # 检查独立
    assert state.get("ep1").in_cooldown is False
    assert state.get("ep1").consecutive_failures == 2
    assert state.get("ep2").in_cooldown is True
    assert state.get("ep2").tier == "secondary"
    assert state.get("ep3").in_cooldown is False
    assert state.get("ep3").consecutive_failures == 0
    assert state.get("ep3").last_success_at == 301.0
    # available: ep1, ep3
    avail = get_available_endpoints(state)
    assert "ep1" in avail
    assert "ep2" not in avail
    assert "ep3" in avail
    print("  ✓ test_multiple_endpoints_independent_state")


def test_heal_log_history_recorded():
    """HealLog 历史记录:真实 demote/cooldown/recover 事件被记录"""
    state = make_default_state(["ep1"], failure_threshold=2, cooldown_seconds=60.0)
    # 1st failure(1/2):no_op,不进 log
    record_failure(state, "ep1", at=100.0)
    assert len(state.log.actions) == 0
    # 2nd failure(2/2):demote + cooldown 都进 log
    record_failure(state, "ep1", at=101.0)
    log = state.log
    assert len(log.actions) == 2
    assert log.actions[0].action == "demote"
    assert log.actions[0].endpoint_id == "ep1"
    assert log.actions[1].action == "cooldown"
    # 计数
    assert log.count_by_action("demote") == 1
    assert log.count_by_action("cooldown") == 1
    assert log.count_by_action("recover") == 0
    # for_endpoint
    ep1_actions = log.for_endpoint("ep1")
    assert len(ep1_actions) == 2
    # 时间旅行,trigger recover
    recover_action = check_recovery(state, "ep1", at=200.0)
    assert recover_action.action == "recover"
    assert log.count_by_action("recover") == 1
    # 序列化
    d = log.to_dict()
    assert "actions" in d
    assert len(d["actions"]) == 3
    print(f"  ✓ test_heal_log_history_recorded (log len={len(log.actions)}, demote+cooldown+recover)")


def test_boundary_zero_duration_immediate_expiry():
    """边界:duration=0 → 立即到期"""
    state = make_default_state(["ep1"])
    # 直接 enter_cooldown duration=0
    enter_cooldown(state, "ep1", duration_seconds=0, reason="zero", at=100.0)
    ep = state.get("ep1")
    assert ep.in_cooldown is True
    assert ep.cooldown_until == 100.0
    # 在同一时刻(now=100)检查:cooldown_until <= now → recover
    action = check_recovery(state, "ep1", at=100.0)
    assert action.action == "recover"
    assert ep.in_cooldown is False
    print("  ✓ test_boundary_zero_duration_immediate_expiry")


# ============ runner ============

def main() -> int:
    tests = [
        test_record_success_clears_consecutive_failures,
        test_record_success_during_cooldown_no_immediate_recover,
        test_record_failure_single_no_cooldown,
        test_record_failure_three_consecutive_triggers_cooldown,
        test_record_failure_cooldown_sets_cooldown_until,
        test_check_recovery_not_expired,
        test_check_recovery_expired_recovers_and_promotes,
        test_promote_secondary_to_primary,
        test_promote_primary_is_no_op,
        test_demote_primary_to_secondary,
        test_demote_fallback_stays_fallback,
        test_enter_cooldown_state_correct,
        test_get_available_excludes_cooldown,
        test_get_available_excludes_disabled,
        test_auto_balance_triggers_check_recovery_multiple,
        test_full_flow_failure_cooldown_recovery,
        test_state_serialization_roundtrip,
        test_multiple_endpoints_independent_state,
        test_heal_log_history_recorded,
        test_boundary_zero_duration_immediate_expiry,
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
