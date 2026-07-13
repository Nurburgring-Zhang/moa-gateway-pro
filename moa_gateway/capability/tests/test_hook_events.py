"""hook_events 真实测试 — 端到端验证(非 mock)"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.hook_events import (
    HookEvent, HookHandler, HookContext, HookRegistry,
    ralph_loop, RALPH_CYCLE,
    RALPH_STAGE_ANALYZE, RALPH_STAGE_IMPLEMENT, RALPH_STAGE_TEST, RALPH_STAGE_REVIEW,
    RALPH_STAGES,
)


def test_27_hook_events_defined():
    """HookEvent 完整定义 27 个事件"""
    assert len(HookEvent) == 27, f"expected 27 events, got {len(HookEvent)}"
    # 抽查几个关键事件
    assert HookEvent.SessionStart.value == "SessionStart"
    assert HookEvent.PreToolUse.value == "PreToolUse"
    assert HookEvent.PostToolUseFailure.value == "PostToolUseFailure"
    assert HookEvent.PreCompact.value == "PreCompact"
    assert HookEvent.AgentSpawn.value == "AgentSpawn"
    assert HookEvent.AgentExit.value == "AgentExit"
    print("  ✓ test_27_hook_events_defined")
    assert True


def test_hook_registry_register_and_unregister():
    """register 返回 handler_id,unregister 能移除"""
    reg = HookRegistry()
    hid = reg.register(HookEvent.SessionStart, lambda ctx: "ok")
    assert isinstance(hid, str)
    assert len(reg.list_handlers()) == 1
    assert reg.unregister(hid) is True
    assert len(reg.list_handlers()) == 0
    # 重复 unregister 返回 False
    assert reg.unregister(hid) is False
    print("  ✓ test_hook_registry_register_and_unregister")
    assert True


def test_hook_registry_trigger_sync():
    """trigger 同步调用,callback 收到 HookContext"""
    received = []

    def cb(ctx: HookContext):
        received.append(ctx)
        return ctx.data.get("x", 0) * 2

    reg = HookRegistry()
    reg.register(HookEvent.PreToolUse, cb)
    results = reg.trigger(HookEvent.PreToolUse, {"x": 5})
    assert results == [10], f"expected [10], got {results}"
    assert len(received) == 1
    assert received[0].event == HookEvent.PreToolUse
    assert received[0].data == {"x": 5}
    print("  ✓ test_hook_registry_trigger_sync")
    assert True


def test_hook_registry_priority_ordering():
    """高 priority 先执行(降序)"""
    order = []
    reg = HookRegistry()
    reg.register(HookEvent.PostToolUse, lambda ctx: order.append("low"), priority=1)
    reg.register(HookEvent.PostToolUse, lambda ctx: order.append("high"), priority=10)
    reg.register(HookEvent.PostToolUse, lambda ctx: order.append("mid"), priority=5)
    reg.trigger(HookEvent.PostToolUse, {})
    assert order == ["high", "mid", "low"], f"got {order}"
    print("  ✓ test_hook_registry_priority_ordering")
    assert True


def test_hook_registry_disabled_skipped():
    """enabled=False 的 handler 不被调用"""
    called = []
    reg = HookRegistry()
    h1 = reg.register(HookEvent.Stop, lambda ctx: called.append("a"))
    h2 = reg.register(HookEvent.Stop, lambda ctx: called.append("b"))
    reg.disable(h2)
    reg.trigger(HookEvent.Stop, {})
    assert called == ["a"], f"got {called}"
    # 重新启用 h2 — 再次 trigger,两个都应被调用
    reg.enable(h2)
    reg.trigger(HookEvent.Stop, {})
    assert "a" in called and "b" in called, f"got {called}"
    assert called.count("a") == 2 and called.count("b") == 1, f"got {called}"
    print("  ✓ test_hook_registry_disabled_skipped")
    assert True


def test_all_27_events_registerable():
    """27 个事件全部能注册并触发"""
    reg = HookRegistry()
    seen = []
    for ev in HookEvent:
        reg.register(ev, lambda ctx, e=ev: seen.append(e))
    for ev in HookEvent:
        reg.trigger(ev, {})
    assert len(seen) == 27, f"expected 27 callbacks fired, got {len(seen)}"
    assert set(seen) == set(HookEvent), "mismatch in fired events"
    print("  ✓ test_all_27_events_registerable")
    assert True


def test_list_handlers_filter_by_event():
    """list_handlers(event=) 按 event 过滤"""
    reg = HookRegistry()
    reg.register(HookEvent.PreToolUse, lambda ctx: None)
    reg.register(HookEvent.PostToolUse, lambda ctx: None)
    reg.register(HookEvent.PreToolUse, lambda ctx: None, priority=5)
    all_h = reg.list_handlers()
    pre = reg.list_handlers(HookEvent.PreToolUse)
    post = reg.list_handlers(HookEvent.PostToolUse)
    assert len(all_h) == 3
    assert len(pre) == 2
    assert len(post) == 1
    assert all(h.event == HookEvent.PreToolUse for h in pre)
    print("  ✓ test_list_handlers_filter_by_event")
    assert True


def test_hook_context_serialization():
    """HookContext 可序列化为 dict + JSON"""
    ctx = HookContext(
        event=HookEvent.PreCommit,
        session_id="sess-1",
        data={"files": ["a.py", "b.py"]},
    )
    d = ctx.to_dict()
    assert d["event"] == "PreCommit"
    assert d["session_id"] == "sess-1"
    assert d["data"]["files"] == ["a.py", "b.py"]
    # JSON 序列化往返
    j = ctx.to_json()
    parsed = json.loads(j)
    assert parsed["event"] == "PreCommit"
    assert parsed["session_id"] == "sess-1"
    # timestamp 是 float
    assert isinstance(d["timestamp"], float)
    print("  ✓ test_hook_context_serialization")
    assert True


def test_ralph_loop_four_stages():
    """ralph_loop 包含 4 个阶段常量"""
    assert len(RALPH_STAGES) == 4
    assert RALPH_STAGES == ["analyze", "implement", "test", "review"]
    # 4 个阶段名都能跑
    for s in RALPH_STAGES:
        r = ralph_loop(s, {})
        assert r["stage"] == s
        assert r["next_stage"] is not None
        assert r["status"] == "ok"
    print("  ✓ test_ralph_loop_four_stages")
    assert True


def test_ralph_analyze_to_implement():
    """analyze → implement"""
    r = ralph_loop(RALPH_STAGE_ANALYZE, {"plan": "do X"})
    assert r["stage"] == "analyze"
    assert r["next_stage"] == "implement"
    assert r["status"] == "ok"
    assert r["data"]["plan"] == "do X"
    print("  ✓ test_ralph_analyze_to_implement")
    assert True


def test_ralph_test_to_review():
    """test → review"""
    r = ralph_loop(RALPH_STAGE_TEST, {"passed": True, "coverage": 0.85})
    assert r["stage"] == "test"
    assert r["next_stage"] == "review"
    assert r["status"] == "ok"
    print("  ✓ test_ralph_test_to_review")
    assert True


def test_ralph_review_failed_returns_to_analyze():
    """review 失败 → 回到 analyze"""
    r = ralph_loop(RALPH_STAGE_REVIEW, {"passed": False, "issues": ["bug"]})
    assert r["stage"] == "review"
    assert r["next_stage"] == "analyze", f"expected analyze, got {r['next_stage']}"
    assert r["status"] == "failed"
    print("  ✓ test_ralph_review_failed_returns_to_analyze")
    assert True


def test_ralph_review_passed_returns_to_analyze_via_cycle():
    """review 通过 → 下一阶段回到 analyze(完整循环)"""
    r = ralph_loop(RALPH_STAGE_REVIEW, {"passed": True})
    assert r["stage"] == "review"
    assert r["next_stage"] == "analyze"
    assert r["status"] == "ok"
    print("  ✓ test_ralph_review_passed_returns_to_analyze_via_cycle")
    assert True


def test_ralph_cycle_iteration_count():
    """RALPH_CYCLE 完整循环后 iteration 计数 +1"""
    rc = RALPH_CYCLE(max_iter=10)
    assert rc.iteration == 0
    assert rc.current_stage == "analyze"
    # analyze → implement
    rc.advance({"plan": "p"})
    assert rc.current_stage == "implement"
    assert rc.iteration == 0
    # implement → test
    rc.advance({"code": "c"})
    assert rc.current_stage == "test"
    # test → review
    rc.advance({"passed": True})
    assert rc.current_stage == "review"
    # review 通过 → iteration 增 1,下一阶段 analyze
    rc.advance({"passed": True})
    assert rc.iteration == 1
    assert rc.current_stage == "analyze"
    print("  ✓ test_ralph_cycle_iteration_count")
    assert True


def test_ralph_cycle_complete_loop_with_repeat():
    """完整循环 + review 失败重置"""
    rc = RALPH_CYCLE(max_iter=10)
    # 第一次循环
    rc.advance({})  # analyze → implement
    rc.advance({})  # implement → test
    rc.advance({"passed": True})  # test → review
    rc.advance({"passed": True})  # review(ok) → iteration=1, analyze
    assert rc.iteration == 1
    # 第二次循环,review 失败
    rc.advance({})  # analyze → implement
    rc.advance({})  # implement → test
    rc.advance({"passed": True})  # test → review
    assert rc.should_repeat() is False
    rc.advance({"passed": False})  # review(failed) → analyze
    assert rc.current_stage == "analyze"
    assert rc.iteration == 1  # 失败不计数
    assert rc.should_repeat() is True  # history 最后一项是 failed review
    print("  ✓ test_ralph_cycle_complete_loop_with_repeat")
    assert True


def test_ralph_cycle_max_iter():
    """达到 max_iter 后 terminated"""
    rc = RALPH_CYCLE(max_iter=2)
    for _ in range(2):
        rc.advance({})  # analyze
        rc.advance({})  # implement
        rc.advance({"passed": True})  # test
        rc.advance({"passed": True})  # review → iteration +1
    assert rc.iteration == 2
    # 再 advance 应终止
    nxt = rc.advance({})
    assert nxt == "terminated"
    assert rc.terminated is True
    assert rc.terminate_reason == "max_iter_reached"
    # 再次 advance 仍返回 terminated
    assert rc.advance({}) == "terminated"
    print("  ✓ test_ralph_cycle_max_iter")
    assert True


def test_multiple_triggers_independent():
    """多次 trigger 互不干扰,callback 独立调用"""
    counter = [0]
    reg = HookRegistry()
    reg.register(HookEvent.Notification, lambda ctx: counter.__setitem__(0, counter[0] + 1))
    reg.trigger(HookEvent.Notification, {"i": 1})
    reg.trigger(HookEvent.Notification, {"i": 2})
    reg.trigger(HookEvent.Notification, {"i": 3})
    assert counter[0] == 3
    assert reg.trigger_count == 3
    # 触发其他事件不应增加 counter
    reg.trigger(HookEvent.Stop, {})
    assert counter[0] == 3
    print("  ✓ test_multiple_triggers_independent")
    assert True


def test_trigger_zero_handlers_returns_empty():
    """无 handler 时 trigger 返回 []"""
    reg = HookRegistry()
    assert reg.trigger(HookEvent.PreToolUse, {}) == []
    assert reg.trigger(HookEvent.SessionEnd, {}) == []
    # 无 trigger_count 增加
    assert reg.trigger_count == 2
    print("  ✓ test_trigger_zero_handlers_returns_empty")
    assert True


def test_callback_error_isolated():
    """单个 callback 抛错不应中断其他 handler"""
    def bad(ctx):
        raise ValueError("boom")

    def good(ctx):
        return "ok"

    reg = HookRegistry()
    reg.register(HookEvent.PostToolUse, good, priority=10)
    reg.register(HookEvent.PostToolUse, bad, priority=5)
    results = reg.trigger(HookEvent.PostToolUse, {})
    assert len(results) == 2
    # high priority 先 → "ok";然后 bad → 错误 dict
    assert results[0] == "ok"
    assert isinstance(results[1], dict)
    assert results[1]["error"] == "ValueError"
    assert "boom" in results[1]["message"]
    print("  ✓ test_callback_error_isolated")
    assert True


def test_registry_json_serialization():
    """Registry / Handler / RALPH_CYCLE 均可 JSON 序列化"""
    reg = HookRegistry()
    reg.register(HookEvent.PreToolUse, lambda ctx: 1, priority=5)
    reg.register(HookEvent.PostToolUse, lambda ctx: 2)
    reg.trigger(HookEvent.PreToolUse, {"x": 1})
    d = reg.to_dict()
    j = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(j)
    assert parsed["handler_count"] == 2
    assert parsed["trigger_count"] == 1
    assert len(parsed["handlers"]) == 2
    # RALPH_CYCLE
    rc = RALPH_CYCLE(max_iter=3, session_id="s-1")
    rc.advance({})
    rcj = rc.to_json()
    rcparsed = json.loads(rcj)
    assert rcparsed["session_id"] == "s-1"
    assert rcparsed["max_iter"] == 3
    assert len(rcparsed["history"]) == 1
    print("  ✓ test_registry_json_serialization")
    assert True


def test_ralph_loop_unknown_stage():
    """未知阶段返回 failed"""
    r = ralph_loop("unknown_stage", {})
    assert r["status"] == "failed"
    assert r["next_stage"] is None
    print("  ✓ test_ralph_loop_unknown_stage")
    assert True


def test_hook_handler_dataclass_defaults():
    """HookHandler 默认值正确"""
    h = HookHandler(event=HookEvent.SessionStart)
    assert h.event == HookEvent.SessionStart
    assert h.callback is None
    assert h.priority == 0
    assert h.enabled is True
    assert h.handler_id.startswith("h_")
    d = h.to_dict()
    assert d["event"] == "SessionStart"
    assert d["has_callback"] is False
    print("  ✓ test_hook_handler_dataclass_defaults")
    assert True


def test_ralph_cycle_reset():
    """reset 回到初始状态"""
    rc = RALPH_CYCLE(max_iter=2)
    rc.advance({})
    rc.advance({})
    rc.advance({})
    rc.advance({"passed": True})  # iteration=1
    assert rc.iteration == 1
    rc.reset()
    assert rc.iteration == 0
    assert rc.current_stage == "analyze"
    assert len(rc.history) == 0
    assert rc.terminated is False
    print("  ✓ test_ralph_cycle_reset")
    assert True
