"""session_lock 真实测试 — 端到端验证(非 mock)

覆盖 SessionLockManager + MCPRegistry 全功能:
  - SessionLockState 3 值
  - try_acquire 成功 / 重复 / 释放后 / 多 session
  - acquire_with_wait 等待 + 获得 + retry_interval
  - cleanup_expired / 过期自动释放
  - 多 lock_id 独立
  - MCPTool dataclass / register / invoke / unregister / list / get / 不存在
  - JSON 序列化
"""
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.session_lock import (
    MCPRegistry,
    MCPTool,
    SessionLockManager,
    SessionLockState,
    lock_to_json,
    tool_to_json,
)

# ============ SessionLockState ============

def test_session_lock_state_values():
    """SessionLockState 有 FREE / ACQUIRED / WAITING 三个值"""
    assert SessionLockState.FREE.value == "free"
    assert SessionLockState.ACQUIRED.value == "acquired"
    assert SessionLockState.WAITING.value == "waiting"
    members = set(SessionLockState)
    assert len(members) == 3
    print("  ✓ test_session_lock_state_values")
    assert True


# ============ SessionLockManager.try_acquire ============

def test_try_acquire_succeeds_on_free_lock():
    """空锁槽上 try_acquire 返回 True,生成 SessionLock"""
    mgr = SessionLockManager(default_ttl=10.0)
    ok = mgr.try_acquire("L1", "sess_a")
    assert ok is True
    state = mgr.get_lock_state("L1")
    assert state is not None
    assert state.lock_id == "L1"
    assert state.session_id == "sess_a"
    assert state.state == SessionLockState.ACQUIRED
    assert state.waiters == []
    assert state.expires_at > state.acquired_at
    print("  ✓ test_try_acquire_succeeds_on_free_lock")
    assert True


def test_try_acquire_duplicate_returns_false():
    """同一 lock_id 被占后,另一 session 试拿返回 False"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True
    assert mgr.try_acquire("L1", "sess_b") is False
    state = mgr.get_lock_state("L1")
    assert state.session_id == "sess_a"  # holder 未变
    assert "sess_b" in state.waiters
    assert state.state == SessionLockState.WAITING
    print("  ✓ test_try_acquire_duplicate_returns_false")
    assert True


def test_try_acquire_reentrant_same_session():
    """同一 session 重入(未过期)→ 返回 True,刷新 TTL"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True
    first_state = mgr.get_lock_state("L1")
    time.sleep(0.05)
    assert mgr.try_acquire("L1", "sess_a") is True
    second_state = mgr.get_lock_state("L1")
    # 重入会刷新 expires_at
    assert second_state.expires_at > first_state.expires_at
    print("  ✓ test_try_acquire_reentrant_same_session")
    assert True


# ============ SessionLockManager.acquire_with_wait ============

def test_acquire_with_wait_blocks_then_succeeds():
    """acquire_with_wait 在锁释放前阻塞,释放后立刻拿到"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True

    # 启动一个延迟释放线程
    def releaser():
        time.sleep(0.10)
        mgr.release("L1", "sess_a")

    t = threading.Thread(target=releaser)
    t.start()

    t0 = time.time()
    ok = mgr.acquire_with_wait(
        "L1", "sess_b",
        timeout=2.0, retry_interval=0.01,
    )
    elapsed = time.time() - t0
    t.join()
    assert ok is True
    assert elapsed >= 0.08  # 至少等到释放
    assert elapsed < 1.0
    state = mgr.get_lock_state("L1")
    assert state.session_id == "sess_b"
    print("  ✓ test_acquire_with_wait_blocks_then_succeeds")
    assert True


def test_acquire_with_wait_times_out():
    """acquire_with_wait 在 timeout 内拿不到 → 返回 False"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True
    t0 = time.time()
    ok = mgr.acquire_with_wait(
        "L1", "sess_b",
        timeout=0.10, retry_interval=0.01,
    )
    elapsed = time.time() - t0
    assert ok is False
    assert 0.08 <= elapsed < 0.30  # 大致等于 timeout
    print("  ✓ test_acquire_with_wait_times_out")
    assert True


def test_acquire_with_wait_retry_interval():
    """retry_interval 生效:releaser 在 50ms 后释放,等待方能在此期间醒来"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True

    release_at = [0.05]

    def releaser():
        time.sleep(release_at[0])
        mgr.release("L1", "sess_a")

    t = threading.Thread(target=releaser)
    t.start()

    t0 = time.time()
    ok = mgr.acquire_with_wait(
        "L1", "sess_b",
        timeout=2.0, retry_interval=0.01,
    )
    elapsed = time.time() - t0
    t.join()
    assert ok is True
    # retry_interval=0.01 意味着最坏 10ms 一次重试;50ms 内一定能拿到
    assert elapsed < 0.20
    print(f"  ✓ test_acquire_with_wait_retry_interval (elapsed={elapsed:.3f}s)")
    assert True


# ============ SessionLockManager.release ============

def test_release_only_holder_succeeds():
    """只有 holder 能 release;其他人 release 返回 False"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True
    # 非 holder 释放
    assert mgr.release("L1", "sess_b") is False
    state = mgr.get_lock_state("L1")
    assert state is not None
    assert state.session_id == "sess_a"
    # holder 释放
    assert mgr.release("L1", "sess_a") is True
    assert mgr.get_lock_state("L1") is None
    # 重复释放 → False
    assert mgr.release("L1", "sess_a") is False
    print("  ✓ test_release_only_holder_succeeds")
    assert True


def test_release_unknown_lock_returns_false():
    """不存在的 lock_id release 返回 False"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.release("never_existed", "sess_a") is False
    print("  ✓ test_release_unknown_lock_returns_false")
    assert True


# ============ SessionLockManager.get_lock_state ============

def test_get_lock_state_free():
    """未占用的 lock_id 返回 None"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.get_lock_state("ghost") is None
    print("  ✓ test_get_lock_state_free")
    assert True


def test_get_lock_state_snapshot_isolates_waiters():
    """get_lock_state 返回的 waiters 是拷贝,改它不影响内部状态"""
    mgr = SessionLockManager(default_ttl=10.0)
    mgr.try_acquire("L1", "sess_a")
    mgr.try_acquire("L1", "sess_b")
    snap = mgr.get_lock_state("L1")
    snap.waiters.append("mutated")
    snap.waiters.clear()
    fresh = mgr.get_lock_state("L1")
    assert fresh.waiters == ["sess_b"]
    print("  ✓ test_get_lock_state_snapshot_isolates_waiters")
    assert True


# ============ SessionLockManager.cleanup_expired ============

def test_cleanup_expired_removes_expired_locks():
    """cleanup_expired 清理所有过期锁,返回数量"""
    mgr = SessionLockManager(default_ttl=0.05)
    mgr.try_acquire("L1", "sess_a")
    mgr.try_acquire("L2", "sess_b")
    mgr.try_acquire("L3", "sess_c")
    time.sleep(0.10)
    cleaned = mgr.cleanup_expired()
    assert cleaned == 3
    assert mgr.get_lock_state("L1") is None
    assert mgr.get_lock_state("L2") is None
    assert mgr.get_lock_state("L3") is None
    print("  ✓ test_cleanup_expired_removes_expired_locks")
    assert True


def test_cleanup_expired_keeps_fresh_locks():
    """cleanup_expired 不动未过期的锁"""
    mgr = SessionLockManager(default_ttl=10.0)
    mgr.try_acquire("L1", "sess_a")
    mgr.try_acquire("L2", "sess_b")
    cleaned = mgr.cleanup_expired()
    assert cleaned == 0
    assert mgr.get_lock_state("L1") is not None
    assert mgr.get_lock_state("L2") is not None
    print("  ✓ test_cleanup_expired_keeps_fresh_locks")
    assert True


def test_expired_lock_auto_released_on_acquire():
    """过期锁被新 session 抢占(等于自动释放)"""
    mgr = SessionLockManager(default_ttl=0.05)
    assert mgr.try_acquire("L1", "sess_a") is True
    time.sleep(0.10)
    # 过期后,新 session 应该能拿到
    assert mgr.try_acquire("L1", "sess_b") is True
    state = mgr.get_lock_state("L1")
    assert state.session_id == "sess_b"
    assert state.waiters == []
    print("  ✓ test_expired_lock_auto_released_on_acquire")
    assert True


# ============ 多 session 排队 ============

def test_multiple_sessions_queue_for_lock():
    """多个 session 在同一 lock_id 上排队,waiters 列表按到达顺序"""
    mgr = SessionLockManager(default_ttl=10.0)
    mgr.try_acquire("L1", "sess_a")
    for s in ("sess_b", "sess_c", "sess_d"):
        assert mgr.try_acquire("L1", s) is False
    state = mgr.get_lock_state("L1")
    assert state.waiters == ["sess_b", "sess_c", "sess_d"]
    assert state.state == SessionLockState.WAITING
    print("  ✓ test_multiple_sessions_queue_for_lock")
    assert True


# ============ 多 lock_id 独立 ============

def test_multiple_lock_ids_independent():
    """不同 lock_id 互不影响,可同时被不同 session 占用"""
    mgr = SessionLockManager(default_ttl=10.0)
    assert mgr.try_acquire("L1", "sess_a") is True
    assert mgr.try_acquire("L2", "sess_b") is True
    assert mgr.try_acquire("L3", "sess_c") is True
    assert mgr.get_lock_state("L1").session_id == "sess_a"
    assert mgr.get_lock_state("L2").session_id == "sess_b"
    assert mgr.get_lock_state("L3").session_id == "sess_c"
    # 释放 L2 不影响 L1 / L3
    assert mgr.release("L2", "sess_b") is True
    assert mgr.get_lock_state("L1") is not None
    assert mgr.get_lock_state("L2") is None
    assert mgr.get_lock_state("L3") is not None
    print("  ✓ test_multiple_lock_ids_independent")
    assert True


# ============ MCPTool dataclass ============

def test_mcp_tool_dataclass_fields():
    """MCPTool 字段齐全,handler 可调用"""
    def add(a: int, b: int) -> int:
        return a + b

    tool = MCPTool(
        name="add",
        description="add two ints",
        parameters={"a": "int", "b": "int"},
        handler=add,
    )
    assert tool.name == "add"
    assert tool.description == "add two ints"
    assert tool.parameters == {"a": "int", "b": "int"}
    assert tool.handler is add
    assert tool.handler(2, 3) == 5
    d = tool.to_dict()
    assert d == {"name": "add", "description": "add two ints", "parameters": {"a": "int", "b": "int"}}
    print("  ✓ test_mcp_tool_dataclass_fields")
    assert True


# ============ MCPRegistry.register / invoke ============

def test_mcp_registry_register_and_invoke():
    """register + invoke:handler 被正确调用并返回值"""
    reg = MCPRegistry()

    def greet(who: str) -> str:
        return f"hello, {who}"

    tool = MCPTool(
        name="greet",
        description="greet someone",
        parameters={"who": "str"},
        handler=greet,
    )
    reg.register(tool)
    result = reg.invoke("greet", who="world")
    assert result == "hello, world"
    print("  ✓ test_mcp_registry_register_and_invoke")
    assert True


def test_mcp_registry_register_duplicate_raises():
    """重复 register 抛 ValueError"""
    reg = MCPRegistry()
    reg.register(MCPTool(name="t", description="d", parameters={}, handler=lambda: None))
    try:
        reg.register(MCPTool(name="t", description="d2", parameters={}, handler=lambda: None))
    except ValueError as e:
        assert "already registered" in str(e)
    else:
        raise AssertionError("expected ValueError on duplicate register")
    print("  ✓ test_mcp_registry_register_duplicate_raises")
    assert True


# ============ MCPRegistry.unregister ============

def test_mcp_registry_unregister():
    """unregister 存在的工具返回 True,不存在返回 False"""
    reg = MCPRegistry()
    reg.register(MCPTool(name="t1", description="d", parameters={}, handler=lambda: 1))
    assert reg.unregister("t1") is True
    assert reg.unregister("t1") is False  # 第二次返回 False
    # 注销后 invoke 抛 KeyError
    try:
        reg.invoke("t1")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError after unregister")
    print("  ✓ test_mcp_registry_unregister")
    assert True


# ============ MCPRegistry.list_tools ============

def test_mcp_registry_list_tools():
    """list_tools 返回元数据列表(按 name 排序,不含 handler)"""
    reg = MCPRegistry()
    reg.register(MCPTool(name="beta", description="b", parameters={"x": "int"}, handler=lambda x: x))
    reg.register(MCPTool(name="alpha", description="a", parameters={}, handler=lambda: None))
    tools = reg.list_tools()
    assert isinstance(tools, list)
    assert len(tools) == 2
    names = [t["name"] for t in tools]
    assert names == ["alpha", "beta"]  # 已排序
    for t in tools:
        assert "handler" not in t  # 不暴露 handler
    assert tools[0]["parameters"] == {}
    assert tools[1]["parameters"] == {"x": "int"}
    print("  ✓ test_mcp_registry_list_tools")
    assert True


# ============ MCPRegistry.get_tool ============

def test_mcp_registry_get_tool():
    """get_tool 返回 MCPTool 或 None"""
    reg = MCPRegistry()
    reg.register(MCPTool(name="t", description="d", parameters={}, handler=lambda: 42))
    tool = reg.get_tool("t")
    assert tool is not None
    assert tool.name == "t"
    assert tool.handler() == 42
    assert reg.get_tool("missing") is None
    print("  ✓ test_mcp_registry_get_tool")
    assert True


# ============ invoke 不存在 → raise ============

def test_mcp_registry_invoke_missing_raises():
    """invoke 不存在的工具 → 抛 KeyError"""
    reg = MCPRegistry()
    try:
        reg.invoke("nonexistent", x=1)
    except KeyError as e:
        assert "nonexistent" in str(e)
    else:
        raise AssertionError("expected KeyError on missing tool invoke")
    print("  ✓ test_mcp_registry_invoke_missing_raises")
    assert True


# ============ JSON 序列化 ============

def test_session_lock_json_serialization():
    """SessionLock / SessionLockManager.to_json() 是合法 JSON 字符串"""
    mgr = SessionLockManager(default_ttl=10.0)
    mgr.try_acquire("L1", "sess_a")
    mgr.try_acquire("L1", "sess_b")
    raw = mgr.to_json()
    obj = json.loads(raw)
    assert obj["default_ttl"] == 10.0
    assert obj["lock_count"] == 1
    lock_dict = obj["locks"][0]
    assert lock_dict["lock_id"] == "L1"
    assert lock_dict["session_id"] == "sess_a"
    assert lock_dict["state"] == "waiting"
    assert lock_dict["waiters"] == ["sess_b"]
    # 顶层辅助函数同样工作
    state = mgr.get_lock_state("L1")
    assert json.loads(lock_to_json(state))["lock_id"] == "L1"
    print("  ✓ test_session_lock_json_serialization")
    assert True


def test_mcp_registry_json_serialization():
    """MCPRegistry.to_json() 是合法 JSON 字符串(不含 handler)"""
    reg = MCPRegistry()
    reg.register(MCPTool(name="add", description="sum", parameters={"a": "int", "b": "int"}, handler=lambda a, b: a + b))
    raw = reg.to_json()
    obj = json.loads(raw)
    assert obj["tool_count"] == 1
    assert obj["tools"][0]["name"] == "add"
    assert "handler" not in obj["tools"][0]
    # 顶层辅助函数
    tool = reg.get_tool("add")
    assert json.loads(tool_to_json(tool))["name"] == "add"
    print("  ✓ test_mcp_registry_json_serialization")
    assert True


# ============ 线程安全 sanity ============

def test_mcp_registry_invoke_is_thread_safe_basic():
    """基础线程安全:多线程并发 invoke 同一工具,结果正确且不抛异常"""
    reg = MCPRegistry()
    reg.register(MCPTool(
        name="inc",
        description="increment",
        parameters={"x": "int"},
        handler=lambda x: x + 1,
    ))
    results = []
    errors = []

    def worker(x):
        try:
            r = reg.invoke("inc", x=x)
            results.append(r)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(results) == list(range(1, 51))
    print("  ✓ test_mcp_registry_invoke_is_thread_safe_basic")
    assert True
