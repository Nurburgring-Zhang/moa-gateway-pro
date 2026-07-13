"""bubble_mode 真实测试 — 端到端验证(非 mock)"""
import sys
import json
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.bubble_mode import (
    BubbleStatus, EscalationRequest, BubbleManager,
    EventType, Event, EventScheduler,
)


# ==================== A-06 Bubble Mode ====================

def test_bubble_status_three_values():
    """BubbleStatus 必须正好 3 个值"""
    assert len(BubbleStatus) == 3, f"expected 3, got {len(BubbleStatus)}"
    assert BubbleStatus.ALLOWED.value == "allowed"
    assert BubbleStatus.DENIED.value == "denied"
    assert BubbleStatus.ESCALATED.value == "escalated"
    print("  ✓ test_bubble_status_three_values")
    assert True


def test_escalation_request_default_status():
    """EscalationRequest 默认 status = ESCALATED"""
    req = EscalationRequest(
        request_id="r1",
        agent_id="child",
        parent_id="parent",
        action="act",
        reason="why",
        created_at=time.time(),
    )
    assert req.status == BubbleStatus.ESCALATED
    assert req.resolved_at is None
    assert req.resolver_note == ""
    print("  ✓ test_escalation_request_default_status")
    assert True


def test_bubble_manager_escalate_returns_id():
    """escalate 返回非空 string request_id,且 pending +1"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "write_file", "first write")
    assert isinstance(rid, str) and len(rid) > 0
    assert bm.get_request(rid) is not None
    assert bm.get_request(rid).status == BubbleStatus.ESCALATED
    assert len(bm.get_pending()) == 1
    assert bm.escalate_count == 1
    print("  ✓ test_bubble_manager_escalate_returns_id")
    assert True


def test_bubble_manager_resolve_allowed():
    """resolve ALLOWED 成功,resolved_at 被设置"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "delete_db", "cleanup")
    assert bm.resolve(rid, BubbleStatus.ALLOWED, "approved by admin")
    req = bm.get_request(rid)
    assert req.status == BubbleStatus.ALLOWED
    assert req.resolved_at is not None
    assert req.resolver_note == "approved by admin"
    assert len(bm.get_pending()) == 0
    print("  ✓ test_bubble_manager_resolve_allowed")
    assert True


def test_bubble_manager_resolve_denied():
    """resolve DENIED 成功,状态变为 DENIED"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "rm_rf", "rm -rf /tmp")
    assert bm.resolve(rid, BubbleStatus.DENIED, "too dangerous")
    req = bm.get_request(rid)
    assert req.status == BubbleStatus.DENIED
    assert req.resolver_note == "too dangerous"
    assert req.resolved_at is not None
    print("  ✓ test_bubble_manager_resolve_denied")
    assert True


def test_bubble_manager_resolve_invalid_status():
    """resolve 传 ESCALATED 应返回 False(只能传终态)"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "act", "why")
    assert bm.resolve(rid, BubbleStatus.ESCALATED) is False
    assert bm.get_request(rid).status == BubbleStatus.ESCALATED
    # 未知 request_id
    assert bm.resolve("esc_unknown", BubbleStatus.ALLOWED) is False
    print("  ✓ test_bubble_manager_resolve_invalid_status")
    assert True


def test_bubble_manager_resolve_twice_fails():
    """重复 resolve 同一请求返回 False"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "act", "why")
    assert bm.resolve(rid, BubbleStatus.ALLOWED) is True
    assert bm.resolve(rid, BubbleStatus.DENIED) is False
    assert bm.get_request(rid).status == BubbleStatus.ALLOWED
    print("  ✓ test_bubble_manager_resolve_twice_fails")
    assert True


def test_bubble_manager_get_pending_and_resolved():
    """get_pending / get_resolved 正确分类"""
    bm = BubbleManager(parent_id="p1")
    r1 = bm.escalate("c1", "a1", "r1")
    r2 = bm.escalate("c1", "a2", "r2")
    r3 = bm.escalate("c2", "a3", "r3")
    assert len(bm.get_pending()) == 3
    bm.resolve(r1, BubbleStatus.ALLOWED)
    bm.resolve(r3, BubbleStatus.DENIED)
    pending = bm.get_pending()
    resolved = bm.get_resolved()
    assert len(pending) == 1
    assert pending[0].request_id == r2
    assert len(resolved) == 2
    assert {r.request_id for r in resolved} == {r1, r3}
    print("  ✓ test_bubble_manager_get_pending_and_resolved")
    assert True


def test_bubble_manager_empty_pending():
    """边界:0 个 pending 时返回空列表"""
    bm = BubbleManager(parent_id="p1")
    assert bm.get_pending() == []
    assert bm.get_resolved() == []
    print("  ✓ test_bubble_manager_empty_pending")
    assert True


def test_bubble_manager_wait_for_resolution():
    """wait_for_resolution 能被 resolve 唤醒并返回终态"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "act", "why")

    def delayed_resolve():
        time.sleep(0.1)
        bm.resolve(rid, BubbleStatus.DENIED, "later")

    t = threading.Thread(target=delayed_resolve, daemon=True)
    t.start()
    req = bm.wait_for_resolution(rid, timeout=2.0)
    t.join(timeout=1.0)
    assert req.status == BubbleStatus.DENIED
    assert req.resolver_note == "later"
    assert req.resolved_at is not None
    print("  ✓ test_bubble_manager_wait_for_resolution")
    assert True


def test_bubble_manager_wait_timeout_returns_pending():
    """wait_for_resolution 超时时仍返回当前状态(ESCALATED)"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "act", "why")
    req = bm.wait_for_resolution(rid, timeout=0.2)
    assert req.status == BubbleStatus.ESCALATED
    assert req.resolved_at is None
    print("  ✓ test_bubble_manager_wait_timeout_returns_pending")
    assert True


def test_bubble_manager_wait_unknown_id_raises():
    """wait_for_resolution 未知 request_id 抛 KeyError"""
    bm = BubbleManager(parent_id="p1")
    raised = False
    try:
        bm.wait_for_resolution("esc_nonexistent", timeout=0.1)
    except KeyError:
        raised = True
    assert raised
    print("  ✓ test_bubble_manager_wait_unknown_id_raises")
    assert True


def test_multiple_agents_independent():
    """多个 agent 各自独立管理请求"""
    bm1 = BubbleManager(parent_id="p1")
    bm2 = BubbleManager(parent_id="p1")
    r1 = bm1.escalate("agent_a", "act", "r")
    r2 = bm2.escalate("agent_b", "act", "r")
    assert bm1.get_request(r1) is not None
    assert bm1.get_request(r2) is None  # bm1 看不到 bm2 的
    assert bm2.get_request(r2) is not None
    assert len(bm1.get_pending()) == 1
    assert len(bm2.get_pending()) == 1
    # 互不影响
    bm1.resolve(r1, BubbleStatus.ALLOWED)
    assert len(bm1.get_pending()) == 0
    assert len(bm2.get_pending()) == 1
    print("  ✓ test_multiple_agents_independent")
    assert True


def test_multiple_escalates_independent_ids():
    """多次 escalate 产生独立 request_id"""
    bm = BubbleManager(parent_id="p1")
    ids = [bm.escalate("c1", f"act{i}", f"r{i}") for i in range(5)]
    assert len(set(ids)) == 5, "request_ids must be unique"
    assert len(bm.get_pending()) == 5
    # resolve 第一个不影响其他
    bm.resolve(ids[0], BubbleStatus.ALLOWED)
    pending = bm.get_pending()
    assert len(pending) == 4
    assert ids[0] not in {p.request_id for p in pending}
    print("  ✓ test_multiple_escalates_independent_ids")
    assert True


# ==================== A-26 Event scheduling ====================

def test_event_type_three_values():
    """EventType 必须正好 3 个值"""
    assert len(EventType) == 3
    assert EventType.TRIGGER.value == "trigger"
    assert EventType.NEUTRAL.value == "neutral"
    assert EventType.TERMINAL.value == "terminal"
    print("  ✓ test_event_type_three_values")
    assert True


def test_event_scheduler_schedule():
    """schedule 返回 event_id 且事件被存储"""
    es = EventScheduler()
    eid = es.schedule(Event(
        event_id="",
        event_type=EventType.TRIGGER,
        agent_id="a1",
        payload={"x": 1},
    ))
    assert isinstance(eid, str) and eid != ""
    assert es.event_count("a1") == 1
    assert es.schedule_count == 1
    rec = es.recent_events("a1")
    assert len(rec) == 1
    assert rec[0].event_type == EventType.TRIGGER
    assert rec[0].payload == {"x": 1}
    print("  ✓ test_event_scheduler_schedule")
    assert True


def test_should_continue_no_events_returns_true():
    """无事件 → 继续(默认 True)"""
    es = EventScheduler()
    assert es.should_continue("a1") is True
    # 未知 agent 也是 True
    assert es.should_continue("never_scheduled") is True
    print("  ✓ test_should_continue_no_events_returns_true")
    assert True


def test_should_continue_trigger_returns_true():
    """末事件 TRIGGER → 继续"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.TRIGGER, agent_id="a1"))
    assert es.should_continue("a1") is True
    print("  ✓ test_should_continue_trigger_returns_true")
    assert True


def test_should_continue_neutral_returns_true():
    """末事件 NEUTRAL → 继续"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.NEUTRAL, agent_id="a1"))
    assert es.should_continue("a1") is True
    print("  ✓ test_should_continue_neutral_returns_true")
    assert True


def test_should_continue_terminal_returns_false():
    """末事件 TERMINAL → 停止"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.TERMINAL, agent_id="a1"))
    assert es.should_continue("a1") is False
    print("  ✓ test_should_continue_should_continue_terminal_returns_false")
    assert True


def test_should_continue_tail_scan_overrides():
    """反向 tail 扫描: 末事件决定,前面的事件被忽略"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.TERMINAL, agent_id="a1"))
    es.schedule(Event(event_id="e2", event_type=EventType.TRIGGER, agent_id="a1"))
    # 末事件是 TRIGGER → 应继续
    assert es.should_continue("a1") is True
    es.schedule(Event(event_id="e3", event_type=EventType.TERMINAL, agent_id="a1"))
    assert es.should_continue("a1") is False
    print("  ✓ test_should_continue_tail_scan_overrides")
    assert True


def test_recent_events_chronological():
    """recent_events 按时间顺序返回"""
    es = EventScheduler()
    types = [EventType.TRIGGER, EventType.NEUTRAL, EventType.TERMINAL, EventType.TRIGGER]
    for i, et in enumerate(types):
        es.schedule(Event(
            event_id=f"e{i}",
            event_type=et,
            agent_id="a1",
            payload={"i": i},
        ))
    rec = es.recent_events("a1")
    assert len(rec) == 4
    assert [r.payload["i"] for r in rec] == [0, 1, 2, 3]
    assert [r.event_type for r in rec] == types
    # n 截断
    assert len(es.recent_events("a1", n=2)) == 2
    assert es.recent_events("a1", n=2)[-1].payload["i"] == 3
    print("  ✓ test_recent_events_chronological")
    assert True


def test_recent_events_zero_or_negative():
    """recent_events n<=0 返回空"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.TRIGGER, agent_id="a1"))
    assert es.recent_events("a1", n=0) == []
    assert es.recent_events("a1", n=-3) == []
    print("  ✓ test_recent_events_zero_or_negative")
    assert True


def test_clear_removes_agent_events():
    """clear 清空该 agent 的事件"""
    es = EventScheduler()
    for i in range(3):
        es.schedule(Event(event_id=f"e{i}", event_type=EventType.NEUTRAL, agent_id="a1"))
    es.schedule(Event(event_id="x", event_type=EventType.TRIGGER, agent_id="a2"))
    assert es.event_count("a1") == 3
    removed = es.clear("a1")
    assert removed == 3
    assert es.event_count("a1") == 0
    assert es.event_count("a2") == 1
    # 再次 clear 返回 0
    assert es.clear("a1") == 0
    # clear 未知 agent
    assert es.clear("never_seen") == 0
    print("  ✓ test_clear_removes_agent_events")
    assert True


def test_clear_then_should_continue_true():
    """clear 后无事件 → should_continue 返回 True(恢复)"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.TERMINAL, agent_id="a1"))
    assert es.should_continue("a1") is False
    es.clear("a1")
    assert es.should_continue("a1") is True
    print("  ✓ test_clear_then_should_continue_true")
    assert True


def test_event_scheduler_multiple_agents_independent():
    """EventScheduler 多个 agent 流相互独立"""
    es = EventScheduler()
    es.schedule(Event(event_id="e1", event_type=EventType.TERMINAL, agent_id="a1"))
    es.schedule(Event(event_id="e2", event_type=EventType.TRIGGER, agent_id="a2"))
    assert es.should_continue("a1") is False
    assert es.should_continue("a2") is True
    assert es.recent_events("a1")[0].event_id == "e1"
    assert es.recent_events("a2")[0].event_id == "e2"
    print("  ✓ test_event_scheduler_multiple_agents_independent")
    assert True


# ==================== JSON 序列化 ====================

def test_json_serialization_bubble():
    """EscalationRequest + BubbleManager 都能 JSON 序列化"""
    bm = BubbleManager(parent_id="p1")
    rid = bm.escalate("c1", "act", "r")
    req = bm.get_request(rid)
    s = req.to_json()
    parsed = json.loads(s)
    assert parsed["request_id"] == rid
    assert parsed["status"] == "escalated"
    assert parsed["agent_id"] == "c1"
    assert parsed["parent_id"] == "p1"
    # BubbleManager 概览
    bm_dict = bm.to_dict()
    assert bm_dict["parent_id"] == "p1"
    assert bm_dict["pending"] == 1
    assert bm_dict["escalate_count"] == 1
    # round-trip 完整 JSON
    bm.resolve(rid, BubbleStatus.ALLOWED, "go")
    req2 = bm.get_request(rid)
    p2 = json.loads(req2.to_json())
    assert p2["status"] == "allowed"
    assert p2["resolver_note"] == "go"
    assert p2["resolved_at"] is not None
    print("  ✓ test_json_serialization_bubble")
    assert True


def test_json_serialization_event():
    """Event / EventScheduler 都能 JSON 序列化"""
    es = EventScheduler()
    eid = es.schedule(Event(
        event_id="",
        event_type=EventType.NEUTRAL,
        agent_id="a1",
        payload={"k": "v", "n": 42},
    ))
    rec = es.recent_events("a1")
    s = rec[0].to_json()
    parsed = json.loads(s)
    assert parsed["event_type"] == "neutral"
    assert parsed["agent_id"] == "a1"
    assert parsed["payload"] == {"k": "v", "n": 42}
    assert parsed["event_id"] == eid
    # EventScheduler 概览
    d = es.to_dict()
    assert "a1" in d["agents"]
    assert d["schedule_count"] == 1
    assert d["per_agent"]["a1"] == 1
    print("  ✓ test_json_serialization_event")
    assert True


# ==================== main ====================

if __name__ == "__main__":
    import inspect
    funcs = [f for f in globals() if f.startswith("test_")]
    passed = 0
    failed = 0
    for name in sorted(funcs):
        try:
            globals()[name]()
            passed += 1
        except AssertionError as e:
            failed += 1
            print(f"  ✗ {name}: {e}")
        except Exception as e:
            failed += 1
            print(f"  ✗ {name}: EXC {type(e).__name__}: {e}")
    print(f"\n{passed} passed, {failed} failed, {len(funcs)} total")
