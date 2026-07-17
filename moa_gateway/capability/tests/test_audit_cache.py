"""24h Audit Cache 测试 — 覆盖 LRU / TTL / 线程安全 / 序列化等关键路径。"""
from __future__ import annotations

import json
import threading
import time

import pytest

from moa_gateway.capability.audit_cache import (
    VALID_OUTCOMES,
    AuditCache,
    AuditEvent,
)

# ============ Fixtures ============

@pytest.fixture
def make_event(fixed_clock):
    """工厂:构造 AuditEvent,默认用 fixed_clock 的当前时间。"""
    clock, _ = fixed_clock

    def _make(
        event_type: str = "tool.invoke",
        actor: str = "user:alice",
        resource: str = "fs:/tmp/a.txt",
        action: str = "read",
        outcome: str = "allow",
        timestamp: float | None = None,
        metadata: dict | None = None,
        event_id: str | None = None,
    ) -> AuditEvent:
        kwargs = {
            "event_type": event_type,
            "actor": actor,
            "resource": resource,
            "action": action,
            "outcome": outcome,
        }
        kwargs["timestamp"] = timestamp if timestamp is not None else clock()
        if metadata is not None:
            kwargs["metadata"] = metadata
        if event_id is not None:
            kwargs["event_id"] = event_id
        return AuditEvent(**kwargs)

    return _make


@pytest.fixture
def fixed_clock():
    """可手动推进的伪时钟,返回 (clock, advance)。所有 cache 默认使用它。"""
    state = {"now": 1_000_000.0}

    def clock() -> float:
        return state["now"]

    def advance(seconds: float) -> None:
        state["now"] += seconds

    return clock, advance


@pytest.fixture
def make_cache(fixed_clock):
    """工厂:构造使用 fixed_clock 的 cache(避免真实时间漂移影响 TTL 测试)。"""
    clock, _ = fixed_clock

    def _make(max_size: int = 10000, ttl_seconds: int = 86400) -> AuditCache:
        return AuditCache(max_size=max_size, ttl_seconds=ttl_seconds, time_func=clock)

    return _make


# ============ record + get ============

def test_record_returns_event_id(make_event, make_cache):
    cache = make_cache()
    ev = make_event()
    eid = cache.record(ev)
    assert eid == ev.event_id
    assert isinstance(eid, str) and len(eid) > 0


def test_get_existing_event(make_event, make_cache):
    cache = make_cache()
    ev = make_event()
    cache.record(ev)
    got = cache.get(ev.event_id)
    assert got is not None
    assert got.event_id == ev.event_id
    assert got.event_type == ev.event_type
    assert got.actor == ev.actor
    assert got.resource == ev.resource
    assert got.action == ev.action
    assert got.outcome == ev.outcome


def test_get_missing_returns_none(make_cache):
    assert make_cache().get("nonexistent-id") is None


def test_record_auto_uuid_when_id_empty(make_cache):
    """event_id 为空串时,record 应自动生成 uuid 并返回。"""
    cache = make_cache()
    ev = AuditEvent(
        event_type="x", actor="a", resource="r", action="act", event_id=""
    )
    eid = cache.record(ev)
    assert eid  # 非空
    assert cache.get(eid) is not None


# ============ query ============

def test_query_by_event_type(make_event, make_cache):
    cache = make_cache()
    cache.record(make_event(event_type="tool.invoke"))
    cache.record(make_event(event_type="policy.deny"))
    cache.record(make_event(event_type="tool.invoke"))
    out = cache.query(event_type="tool.invoke")
    assert len(out) == 2
    assert all(e.event_type == "tool.invoke" for e in out)


def test_query_by_actor(make_event, make_cache):
    cache = make_cache()
    cache.record(make_event(actor="user:alice"))
    cache.record(make_event(actor="user:bob"))
    cache.record(make_event(actor="user:alice"))
    out = cache.query(actor="user:alice")
    assert len(out) == 2
    assert {e.actor for e in out} == {"user:alice"}


def test_query_by_since_timestamp(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    base = clock()
    cache = make_cache()
    cache.record(make_event(timestamp=base + 0))
    cache.record(make_event(timestamp=base + 100))
    cache.record(make_event(timestamp=base + 200))
    out = cache.query(since=base + 100)
    assert len(out) == 2
    assert all(e.timestamp >= base + 100 for e in out)


def test_query_combined_filters(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    base = clock()
    cache = make_cache()
    cache.record(make_event(event_type="tool.invoke", actor="user:alice", timestamp=base + 0))
    cache.record(make_event(event_type="tool.invoke", actor="user:bob", timestamp=base + 50))
    cache.record(make_event(event_type="policy.deny", actor="user:alice", timestamp=base + 100))
    cache.record(make_event(event_type="tool.invoke", actor="user:alice", timestamp=base + 200))
    out = cache.query(event_type="tool.invoke", actor="user:alice", since=base + 50)
    assert len(out) == 1
    assert out[0].timestamp == base + 200


def test_query_empty(make_event, make_cache):
    cache = make_cache()
    assert cache.query() == []
    assert cache.query(event_type="nope") == []


def test_query_limit(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    base = clock()
    cache = make_cache()
    for i in range(10):
        cache.record(make_event(timestamp=base + float(i)))
    out = cache.query(limit=3)
    assert len(out) == 3
    # 升序,前 3 个
    assert [e.timestamp for e in out] == [base, base + 1, base + 2]


def test_query_limit_zero_or_negative(make_event, make_cache):
    cache = make_cache()
    cache.record(make_event())
    assert cache.query(limit=0) == []
    assert cache.query(limit=-5) == []


# ============ LRU 淘汰 ============

def test_lru_eviction_when_over_max_size(make_event, make_cache):
    cache = make_cache(max_size=3)
    cache.record(make_event(event_id="e1"))
    cache.record(make_event(event_id="e2"))
    cache.record(make_event(event_id="e3"))
    cache.record(make_event(event_id="e4"))
    # e1 应被淘汰
    assert cache.get("e1") is None
    assert cache.get("e2") is not None
    assert cache.get("e3") is not None
    assert cache.get("e4") is not None


def test_lru_get_refreshes_order(make_event, make_cache):
    cache = make_cache(max_size=3)
    cache.record(make_event(event_id="e1"))
    cache.record(make_event(event_id="e2"))
    cache.record(make_event(event_id="e3"))
    # 访问 e1,使其变为最新
    assert cache.get("e1") is not None
    # 插入 e4,e2 应被淘汰
    cache.record(make_event(event_id="e4"))
    assert cache.get("e1") is not None  # 刚访问,存活
    assert cache.get("e2") is None        # 最久未访问,被淘汰


def test_duplicate_event_id_updates(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    base = clock()
    cache = make_cache(max_size=3)
    ev1 = make_event(event_id="dup", actor="alice", timestamp=base)
    ev2 = make_event(event_id="dup", actor="bob", timestamp=base + 1, outcome="deny")
    cache.record(ev1)
    cache.record(ev2)
    assert cache.count() == 1
    got = cache.get("dup")
    assert got is not None
    assert got.actor == "bob"
    assert got.outcome == "deny"
    assert got.timestamp == base + 1


# ============ TTL 过期 ============

def test_ttl_expiry_lazy_via_get(make_event, make_cache, fixed_clock):
    clock, advance = fixed_clock
    cache = make_cache(ttl_seconds=60)
    ev = make_event(event_id="t1")
    cache.record(ev)
    # 30s 后未过期
    advance(30)
    assert cache.get("t1") is not None
    # 再过 31s,累计 61s,过期
    advance(31)
    assert cache.get("t1") is None
    # 过期项已被 get lazy 删除
    assert cache.count() == 0


def test_ttl_expiry_count_keeps_until_cleanup(make_event, make_cache, fixed_clock):
    """count() 不主动清理,过期项仍计入,直到 cleanup() 或下次 get/query。"""
    clock, advance = fixed_clock
    cache = make_cache(ttl_seconds=10)
    cache.record(make_event())
    advance(20)
    # 未触发任何 lazy 清理,count 仍为 1
    assert cache.count() == 1


def test_ttl_expiry_lazy_via_query(make_event, make_cache, fixed_clock):
    clock, advance = fixed_clock
    cache = make_cache(ttl_seconds=10)
    cache.record(make_event(event_id="q1"))
    advance(11)
    assert cache.query() == []
    # 过期项已被 query lazy 删除
    assert cache.count() == 0


def test_cleanup_removes_expired(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    base = clock()
    cache = make_cache(ttl_seconds=100)
    cache.record(make_event(event_id="c1", timestamp=base - 200))  # 已过期
    cache.record(make_event(event_id="c2", timestamp=base - 50))   # 鲜活
    cache.record(make_event(event_id="c3", timestamp=base - 150))  # 过期
    assert cache.count() == 3
    removed = cache.cleanup()
    assert removed == 2
    assert cache.count() == 1
    assert cache.get("c2") is not None


def test_cleanup_returns_zero_when_nothing_expired(make_event, make_cache):
    cache = make_cache()
    cache.record(make_event())
    assert cache.cleanup() == 0


# ============ count / stats ============

def test_count_includes_expired_not_yet_cleaned(make_event, make_cache, fixed_clock):
    clock, advance = fixed_clock
    cache = make_cache(ttl_seconds=10)
    cache.record(make_event())
    advance(20)
    # 未触发任何 lazy 清理,count 仍含
    assert cache.count() == 1
    # cleanup 后清空
    assert cache.cleanup() == 1
    assert cache.count() == 0


def test_stats_by_event_type(make_event, make_cache):
    cache = make_cache()
    cache.record(make_event(event_type="tool.invoke"))
    cache.record(make_event(event_type="tool.invoke"))
    cache.record(make_event(event_type="policy.deny"))
    cache.record(make_event(event_type="auth.login"))
    s = cache.stats()
    assert s == {"tool.invoke": 2, "policy.deny": 1, "auth.login": 1}


def test_stats_excludes_expired(make_event, make_cache, fixed_clock):
    clock, advance = fixed_clock
    cache = make_cache(ttl_seconds=10)
    cache.record(make_event(event_type="tool.invoke"))
    advance(20)
    s = cache.stats()
    assert s == {}


def test_stats_empty_cache():
    # 真实时钟,空 cache
    assert AuditCache().stats() == {}


# ============ 序列化 ============

def test_serialization_roundtrip(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    cache = make_cache(max_size=50, ttl_seconds=3600)
    cache.record(make_event(event_type="tool.invoke", metadata={"k": "v", "n": 1}))
    cache.record(make_event(event_type="policy.deny", outcome="deny"))
    payload = cache.to_dict()
    assert payload["max_size"] == 50
    assert payload["ttl_seconds"] == 3600
    assert payload["count"] == 2

    # 用同一 clock 反序列化,避免真实时间漂移把事件算成过期
    restored = AuditCache.from_dict(payload, time_func=clock)
    assert restored.count() == 2
    e1 = next(e for e in restored.query(event_type="tool.invoke"))
    assert e1.metadata == {"k": "v", "n": 1}


def test_json_roundtrip(make_event, make_cache, fixed_clock):
    clock, _ = fixed_clock
    cache = make_cache()
    cache.record(make_event(metadata={"中文": "值", "list": [1, 2, 3]}))
    raw = cache.to_json()
    obj = json.loads(raw)
    assert "events" in obj
    # 替换默认 time_func 为 mock clock,让 from_json 不丢事件
    restored = AuditCache.from_json(raw, time_func=clock)
    out = restored.query()
    assert len(out) == 1
    assert out[0].metadata == {"中文": "值", "list": [1, 2, 3]}


def test_from_dict_skips_expired(make_event, fixed_clock):
    clock, advance = fixed_clock
    cache = AuditCache(ttl_seconds=10, time_func=clock)
    base = clock()
    cache.record(make_event(event_id="fresh", timestamp=base))
    advance(5)
    cache.record(make_event(event_id="stale", timestamp=base - 100))  # 一定过期
    payload = cache.to_dict()
    assert payload["count"] == 1  # stale 被过滤
    assert payload["events"][0]["event_id"] == "fresh"
    restored = AuditCache.from_dict(payload, time_func=clock)
    assert restored.count() == 1


def test_from_dict_skips_malformed_entries():
    bad = {
        "max_size": 10,
        "ttl_seconds": 100,
        "count": 0,
        "events": [
            {"event_type": "ok", "actor": "a", "resource": "r", "action": "act", "outcome": "allow"},
            {"not_a_valid_field": True},  # 缺字段,会被 from_dict 拒绝
        ],
    }
    restored = AuditCache.from_dict(bad)
    assert restored.count() == 1


# ============ outcome / metadata ============

def test_outcome_accepts_valid_values():
    for o in VALID_OUTCOMES:
        ev = AuditEvent(event_type="t", actor="a", resource="r", action="act", outcome=o)
        assert ev.outcome == o


def test_outcome_rejects_invalid():
    with pytest.raises(ValueError):
        AuditEvent(event_type="t", actor="a", resource="r", action="act", outcome="maybe")


def test_metadata_preserved(make_event, make_cache):
    meta = {"trace_id": "abc-123", "duration_ms": 42, "tags": ["fast", "prod"]}
    ev = make_event(metadata=meta)
    cache = make_cache()
    cache.record(ev)
    got = cache.get(ev.event_id)
    assert got is not None
    assert got.metadata == meta


def test_metadata_default_empty():
    ev = AuditEvent(event_type="t", actor="a", resource="r", action="act")
    assert ev.metadata == {}


# ============ 线程并发 ============

def test_concurrent_record_and_get(fixed_clock):
    """10 线程 × 100 events + 3 reader 线程,验证线程安全。"""
    clock, _ = fixed_clock
    cache = AuditCache(max_size=20_000, time_func=clock)

    def writer(thread_id: int) -> None:
        for i in range(100):
            ev = AuditEvent(
                event_id=f"t{thread_id}-e{i}",
                event_type="tool.invoke" if i % 2 == 0 else "policy.deny",
                actor=f"user:{thread_id}",
                resource="r",
                action="a",
                timestamp=clock(),
            )
            cache.record(ev)

    def reader() -> None:
        for _ in range(50):
            cache.query(limit=20)
            cache.stats()
            cache.count()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(10)]
    readers = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads + readers:
        t.start()
    for t in threads + readers:
        t.join()

    # 10 * 100 = 1000,每线程独立 event_id,无重复
    assert cache.count() == 1000
    # 每线程的 actor 都应能查到 100 条
    for i in range(10):
        out = cache.query(actor=f"user:{i}")
        assert len(out) == 100


# ============ 大数量性能 ============

def test_large_volume_performance(fixed_clock):
    clock, _ = fixed_clock
    cache = AuditCache(max_size=2000, ttl_seconds=10_000, time_func=clock)
    start = time.time()
    for i in range(1500):
        ev = AuditEvent(
            event_id=f"big-{i}",
            event_type="tool.invoke",
            actor="a",
            resource="r",
            action="act",
            timestamp=clock(),
        )
        cache.record(ev)
    elapsed = time.time() - start
    # 1500 次写入应在 2s 内完成(留足余量,CI 上较慢)
    assert elapsed < 2.0, f"写入过慢: {elapsed:.2f}s"
    assert cache.count() == 1500

    # 查询性能
    start = time.time()
    out = cache.query(limit=500)
    elapsed = time.time() - start
    assert len(out) == 500
    assert elapsed < 1.0, f"查询过慢: {elapsed:.2f}s"


# ============ 构造参数校验 ============

def test_invalid_max_size_raises():
    with pytest.raises(ValueError):
        AuditCache(max_size=0)
    with pytest.raises(ValueError):
        AuditCache(max_size=-1)


def test_invalid_ttl_raises():
    with pytest.raises(ValueError):
        AuditCache(ttl_seconds=0)
    with pytest.raises(ValueError):
        AuditCache(ttl_seconds=-100)


def test_record_none_event_raises():
    cache = AuditCache()
    with pytest.raises(ValueError):
        cache.record(None)  # type: ignore[arg-type]
