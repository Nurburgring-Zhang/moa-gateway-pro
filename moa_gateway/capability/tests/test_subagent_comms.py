"""subagent_comms 真实测试 — 端到端验证(非 mock)"""
import sys
import json
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.subagent_comms import (
    Message, TaskCreate,
    SubagentHub, TaskBoard, AdvisoryLock,
    BACKOFF_BASE_SEC, MAX_RETRY,
    hub_to_json, task_to_json, message_to_json,
)


# ============ SubagentHub: send_message / inbox ============

def test_send_message_creates_message():
    """send_message 创建一条 Message 并投递给目标 session"""
    hub = SubagentHub(session_id="s_a")
    m = hub.send_message("s_b", "hello")
    assert isinstance(m, Message)
    assert m.from_session == "s_a"
    assert m.to_session == "s_b"
    assert m.content == "hello"
    assert m.kind == "send"
    assert m.parent_msg_id is None
    assert isinstance(m.msg_id, str) and m.msg_id.startswith("m_")
    assert m.timestamp > 0
    print("  ✓ test_send_message_creates_message")
    assert True


def test_inbox_receives_message():
    """目标 session 的 inbox 能收到这条消息;发送方 inbox 为空"""
    hub_a = SubagentHub(session_id="s_a")
    hub_a.send_message("s_b", "ping")
    # 收件人视角:用 session_id="s_b" 的 hub 直接读 inbox
    hub_b = SubagentHub(session_id="s_b")
    # 把 s_b 视角的内部注册表与 hub_a 共享(模拟同进程多 hub 实例)
    hub_b._inboxes.update(hub_a._inboxes)
    inbox_b = hub_b.inbox()
    assert len(inbox_b) == 1
    assert inbox_b[0].content == "ping"
    assert inbox_b[0].from_session == "s_a"
    # 发送方 s_a 的 inbox 不含自己发出的消息(to_session != s_a)
    inbox_a_self_sent = [m for m in hub_a._inboxes.get("s_a", []) if m.to_session == "s_a"]
    assert len(inbox_a_self_sent) == 0
    print("  ✓ test_inbox_receives_message")
    assert True


def test_inbox_empty_for_new_session():
    """新 hub 自己的 inbox 默认空"""
    hub = SubagentHub(session_id="lonely")
    assert hub.inbox() == []
    print("  ✓ test_inbox_empty_for_new_session")
    assert True


# ============ SubagentHub: broadcast ============

def test_broadcast_multiple_sessions():
    """broadcast 同时给 N 个 session 各发一条独立 Message"""
    hub = SubagentHub(session_id="s_root")
    targets = ["s_1", "s_2", "s_3"]
    msgs = hub.broadcast(targets, "announce")
    assert len(msgs) == 3
    recipients = {m.to_session for m in msgs}
    assert recipients == set(targets)
    for m in msgs:
        assert m.kind == "broadcast"
        assert m.content == "announce"
        assert m.from_session == "s_root"
    # 收件人各自 inbox 收到 1 条
    for t in targets:
        assert len([m for m in hub._inboxes[t] if m.to_session == t]) == 1
    print("  ✓ test_broadcast_multiple_sessions")
    assert True


def test_broadcast_empty_sessions_returns_empty():
    """broadcast 空列表返回空,不报错"""
    hub = SubagentHub(session_id="s_root")
    assert hub.broadcast([], "nobody") == []
    print("  ✓ test_broadcast_empty_sessions_returns_empty")
    assert True


# ============ SubagentHub: reply ============

def test_reply_uses_parent_msg_id():
    """reply 必须带 parent_msg_id,并把 to_session 设为原消息的 from_session"""
    hub = SubagentHub(session_id="s_b")
    # 构造:s_a 发了条 m1 给 s_b,s_b 现在回 s_a
    hub_other = SubagentHub(session_id="s_a")
    m1 = hub_other.send_message("s_b", "ask")
    # 把 m1 也注入 hub 的 inbox(s_a 视角的 hub 不会自动知道,手动同步)
    hub.deliver(m1)
    reply_msg = hub.reply(m1.msg_id, "answer")
    assert reply_msg.kind == "reply"
    assert reply_msg.parent_msg_id == m1.msg_id
    assert reply_msg.to_session == "s_a"  # 回寄给原发送方
    assert reply_msg.from_session == "s_b"
    assert reply_msg.content == "answer"
    print("  ✓ test_reply_uses_parent_msg_id")
    assert True


def test_reply_with_unknown_parent_still_sends():
    """reply 找不到父消息时仍可发送(目标回退到 unknown:xxx 便于排查)"""
    hub = SubagentHub(session_id="s_b")
    reply_msg = hub.reply("m_does_not_exist", "still tries")
    assert reply_msg.parent_msg_id == "m_does_not_exist"
    assert reply_msg.to_session.startswith("unknown:")
    assert reply_msg.kind == "reply"
    print("  ✓ test_reply_with_unknown_parent_still_sends")
    assert True


# ============ TaskCreate / TaskBoard ============

def test_task_create_returns_id():
    """create_task 返回 task_id,且 TaskCreate 字段齐"""
    board = TaskBoard(session_id="s_a")
    tid = board.create_task("write doc", assignee="s_b")
    assert isinstance(tid, str) and tid.startswith("t_")
    t = board.get_task(tid)
    assert t is not None
    assert t.title == "write doc"
    assert t.assignee_session == "s_b"
    assert t.status == "pending"
    assert t.parent_task_id is None
    assert t.created_at > 0
    print("  ✓ test_task_create_returns_id")
    assert True


def test_task_create_with_parent():
    """create_task 支持 parent,且父不存在会抛错"""
    board = TaskBoard(session_id="s_a")
    parent_id = board.create_task("root task")
    child_id = board.create_task("child task", parent=parent_id)
    child = board.get_task(child_id)
    assert child is not None
    assert child.parent_task_id == parent_id
    # 父不存在 → KeyError
    try:
        board.create_task("orphan", parent="t_does_not_exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for missing parent")
    print("  ✓ test_task_create_with_parent")
    assert True


def test_update_status_changes_status():
    """update_status 修改 status,且必须合法"""
    board = TaskBoard(session_id="s_a")
    tid = board.create_task("do thing")
    for s in ["in_progress", "completed", "failed", "pending"]:
        board.update_status(tid, s)
        assert board.get_task(tid).status == s
    # 非法 status 抛错
    try:
        board.update_status(tid, "bogus")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for bogus status")
    # 不存在 task 抛错
    try:
        board.update_status("t_nope", "completed")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError for missing task")
    print("  ✓ test_update_status_changes_status")
    assert True


def test_get_task_returns_none_for_missing():
    """get_task 不存在时返回 None(不抛错)"""
    board = TaskBoard(session_id="s_a")
    assert board.get_task("t_missing") is None
    print("  ✓ test_get_task_returns_none_for_missing")
    assert True


def test_list_tasks_filter_by_status():
    """list_tasks 按 status 过滤"""
    board = TaskBoard(session_id="s_a")
    t1 = board.create_task("a")
    t2 = board.create_task("b")
    t3 = board.create_task("c")
    board.update_status(t1, "completed")
    board.update_status(t2, "in_progress")
    # status="completed"
    completed = board.list_tasks(status="completed")
    assert len(completed) == 1 and completed[0].task_id == t1
    # status="pending"
    pending = board.list_tasks(status="pending")
    assert {t.task_id for t in pending} == {t3}
    # 无过滤 → 全部
    all_tasks = board.list_tasks()
    assert len(all_tasks) == 3
    print("  ✓ test_list_tasks_filter_by_status")
    assert True


def test_list_tasks_filter_by_assignee():
    """list_tasks 按 assignee 过滤"""
    board = TaskBoard(session_id="s_a")
    board.create_task("t1", assignee="alice")
    board.create_task("t2", assignee="bob")
    board.create_task("t3")  # 无 assignee
    alice_tasks = board.list_tasks(assignee="alice")
    assert len(alice_tasks) == 1 and alice_tasks[0].assignee_session == "alice"
    bob_tasks = board.list_tasks(assignee="bob")
    assert len(bob_tasks) == 1
    # 过滤不存在的 assignee
    assert board.list_tasks(assignee="nobody") == []
    print("  ✓ test_list_tasks_filter_by_assignee")
    assert True


def test_get_subtasks_returns_children():
    """get_subtasks 返回所有 parent_task_id 匹配的子任务"""
    board = TaskBoard(session_id="s_a")
    parent = board.create_task("root")
    c1 = board.create_task("c1", parent=parent)
    c2 = board.create_task("c2", parent=parent)
    board.create_task("orphan")  # 无父
    subs = board.get_subtasks(parent)
    assert {t.task_id for t in subs} == {c1, c2}
    # 不存在的 parent → 空列表
    assert board.get_subtasks("t_no_parent") == []
    print("  ✓ test_get_subtasks_returns_children")
    assert True


# ============ AdvisoryLock ============

def test_advisory_lock_acquire_succeeds_when_free():
    """无竞争时 acquire 直接成功"""
    lock = AdvisoryLock("lk_1", "holder_a", timeout=5.0)
    assert lock.acquire() is True
    assert lock.is_held() is True
    assert lock.held_by() == "holder_a"
    lock.release()
    print("  ✓ test_advisory_lock_acquire_succeeds_when_free")
    assert True


def test_advisory_lock_second_acquire_fails():
    """已被 holder_a 持有后,holder_b 重试 3 次后仍失败"""
    lock_a = AdvisoryLock("lk_2", "holder_a", timeout=5.0)
    assert lock_a.acquire() is True
    # 同一 lock_id 同一进程不允许第二个实例 —— 直接用别的 holder 名
    # 测试逻辑:同 lock_id,不同 holder 抢锁 —— 我们需要绕过单实例注册
    # 解决: 模拟"holder_b"的视角(直接用相同 lock_id,绕开 __init__ 注册)
    fake_holder = AdvisoryLock.__new__(AdvisoryLock)
    fake_holder.lock_id = "lk_2"
    fake_holder.holder = "holder_b"
    fake_holder.timeout = 5.0
    fake_holder._acquired_at = None
    start = time.time()
    assert fake_holder.acquire() is False
    elapsed = time.time() - start
    # 3-retry 退避:10ms + 20ms = 30ms 起步;允许较大误差
    assert elapsed >= (BACKOFF_BASE_SEC + BACKOFF_BASE_SEC * 2) * 0.8
    # 真正的锁还应被 holder_a 持有
    assert lock_a.is_held() is True
    assert lock_a.held_by() == "holder_a"
    lock_a.release()
    print("  ✓ test_advisory_lock_second_acquire_fails")
    assert True


def test_advisory_lock_release_only_by_holder():
    """release 只在当前 holder 匹配时返回 True"""
    lock = AdvisoryLock("lk_3", "holder_a", timeout=5.0)
    assert lock.acquire() is True
    # 当前 holder 是 holder_a,直接 release 成功
    assert lock.release() is True
    # 已释放,再 release 失败
    assert lock.release() is False
    # 不存在的 lock_id 视角不会 release 成功
    fake = AdvisoryLock.__new__(AdvisoryLock)
    fake.lock_id = "lk_3"
    fake.holder = "holder_x"
    fake.timeout = 5.0
    fake._acquired_at = None
    assert fake.release() is False
    print("  ✓ test_advisory_lock_release_only_by_holder")
    assert True


def test_advisory_lock_is_held_snapshot():
    """is_held 返回当前是否被任何人持有"""
    lock = AdvisoryLock("lk_4", "holder_a", timeout=5.0)
    assert lock.is_held() is False
    assert lock.acquire() is True
    assert lock.is_held() is True
    assert lock.is_held_by_me() is True
    lock.release()
    assert lock.is_held() is False
    assert lock.is_held_by_me() is False
    print("  ✓ test_advisory_lock_is_held_snapshot")
    assert True


def test_advisory_lock_releases_after_timeout_force_claim():
    """原 holder 持锁超过 timeout 后,新 holder 可强制夺锁(force)"""
    lock_a = AdvisoryLock("lk_5", "holder_a", timeout=0.05)  # 50ms
    assert lock_a.acquire() is True
    # 等到超时
    time.sleep(0.08)
    fake = AdvisoryLock.__new__(AdvisoryLock)
    fake.lock_id = "lk_5"
    fake.holder = "holder_b"
    fake.timeout = 5.0
    fake._acquired_at = None
    assert fake.acquire() is True  # 强制夺锁
    assert fake.held_by() == "holder_b"
    # holder_a 已不能再 release(它不是当前 holder)
    assert lock_a.release() is False
    # 当前 holder_b(fake)绕过 __init__ 注册,直接调 _set_holder 清理槽位
    # —— 验证 fake 是真 holder(fake.held_by() == fake.holder)
    assert fake.held_by() == fake.holder
    # 释放方式:fake.release() 因未在 registry 中会返回 False,
    # 实际清理用 lock_a(同 lock_id 同 holder_id 的真实实例):
    # 把 lock_a 重新 acquire 让其成为 holder,然后正常 release
    lock_a._set_holder("holder_a", time.time())  # 让 lock_a 当回 holder
    assert lock_a.release() is True
    assert lock_a.is_held() is False
    print("  ✓ test_advisory_lock_releases_after_timeout_force_claim")
    assert True


def test_advisory_lock_three_retry_backoff():
    """3-retry / 10ms 指数退避 —— acquire 失败耗时 ≈ 10 + 20 = 30ms(去掉最后那次 sleep)"""
    lock_a = AdvisoryLock("lk_6", "holder_a", timeout=5.0)
    assert lock_a.acquire() is True
    fake = AdvisoryLock.__new__(AdvisoryLock)
    fake.lock_id = "lk_6"
    fake.holder = "holder_b"
    fake.timeout = 5.0
    fake._acquired_at = None
    start = time.time()
    result = fake.acquire()
    elapsed = time.time() - start
    assert result is False
    # 期望 2 次 sleep(第 3 次前不再 sleep): 10ms + 20ms = 30ms
    expected_min = (BACKOFF_BASE_SEC + BACKOFF_BASE_SEC * 2) * 0.8
    assert elapsed >= expected_min, f"backoff too short: {elapsed}s"
    # 也应小于 1s(防死循环)
    assert elapsed < 1.0, f"backoff too long: {elapsed}s"
    # 验证正好 3 次尝试
    assert MAX_RETRY == 3
    lock_a.release()
    print("  ✓ test_advisory_lock_three_retry_backoff")
    assert True


# ============ JSON 序列化 ============

def test_json_serialization_for_message_task_hub():
    """Message / TaskCreate / Hub / Board 全部能 JSON 序列化往返"""
    # Message
    m = Message(
        msg_id="m_x", from_session="a", to_session="b",
        content="hi", timestamp=1.0, kind="send", parent_msg_id=None,
    )
    s = message_to_json(m)
    obj = json.loads(s)
    assert obj["msg_id"] == "m_x" and obj["kind"] == "send"
    # TaskCreate
    t = TaskCreate(
        task_id="t_x", title="hello", assignee_session="b",
        parent_task_id=None, status="pending", created_at=2.0,
    )
    s = task_to_json(t)
    obj = json.loads(s)
    assert obj["task_id"] == "t_x" and obj["status"] == "pending"
    # Hub
    hub = SubagentHub(session_id="s_a")
    hub.send_message("s_b", "yo")
    s = hub_to_json(hub)
    obj = json.loads(s)
    assert obj["session_id"] == "s_a"
    assert "s_a" in obj["sessions"] and "s_b" in obj["sessions"]
    # Board
    board = TaskBoard(session_id="s_a")
    board.create_task("x")
    s = board.to_json()
    obj = json.loads(s)
    assert obj["task_count"] == 1
    assert obj["tasks"][0]["title"] == "x"
    print("  ✓ test_json_serialization_for_message_task_hub")
    assert True


# ============ 边界 ============

def test_empty_inbox_and_empty_board():
    """空 inbox / 空 board 不报错"""
    hub = SubagentHub(session_id="s_empty")
    assert hub.inbox() == []
    board = TaskBoard(session_id="s_empty")
    assert board.list_tasks() == []
    assert board.get_subtasks("t_none") == []
    print("  ✓ test_empty_inbox_and_empty_board")
    assert True


# ============ 线程安全(轻量烟雾测试) ============

def test_concurrent_send_message_thread_safe():
    """多线程并发 send_message 不丢消息,数量正确"""
    hub = SubagentHub(session_id="s_root")
    N_THREADS = 8
    N_PER_THREAD = 25

    def worker():
        for i in range(N_PER_THREAD):
            hub.send_message("s_target", f"msg-{threading.current_thread().name}-{i}")

    threads = [threading.Thread(target=worker, name=f"w{i}") for i in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 收件人应该收到 N_THREADS * N_PER_THREAD 条
    count = sum(1 for m in hub._inboxes["s_target"] if m.to_session == "s_target")
    assert count == N_THREADS * N_PER_THREAD, f"got {count}"
    print("  ✓ test_concurrent_send_message_thread_safe")
    assert True
