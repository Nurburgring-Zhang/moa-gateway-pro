"""trace 真实测试 — 端到端验证 (非 mock)

覆盖:
- new_trace 生成 / UUID 格式
- new_span 继承 trace_id / parent_span_id
- format_traceparent 标准 W3C 格式
- parse_traceparent 正常 / 错误 / 全零
- TraceCollector start / end
- record_span 多层 span
- get_trace 完整 tree
- query by since_ts / min_duration / status
- cleanup 过期 / 不删 in_flight
- stats 准确
- 边界: 空 trace / 无 parent / 超长 attrs
- 性能: 1000 trace
- 并发: 10 线程
"""
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.trace import (
    TraceContext,
    TraceCollector,
    new_trace,
    new_span,
    format_traceparent,
    parse_traceparent,
)


# ============ new_trace ============

def test_new_trace_generates_uuid_hex():
    ctx = new_trace()
    assert isinstance(ctx, TraceContext)
    assert len(ctx.trace_id) == 32
    assert all(c in "0123456789abcdef" for c in ctx.trace_id)
    assert len(ctx.span_id) == 16
    assert all(c in "0123456789abcdef" for c in ctx.span_id)
    assert ctx.parent_span_id is None
    assert ctx.start_ts > 0
    assert ctx.tags == {}
    assert ctx.baggage == {}
    print("  ✓ test_new_trace_generates_uuid_hex")
    assert True


def test_new_trace_unique_ids():
    ids = {new_trace().trace_id for _ in range(50)}
    assert len(ids) == 50
    print("  ✓ test_new_trace_unique_ids")
    assert True


def test_new_trace_with_tags_baggage():
    ctx = new_trace(tags={"env": "prod"}, baggage={"user": "u1"})
    assert ctx.tags == {"env": "prod"}
    assert ctx.baggage == {"user": "u1"}
    print("  ✓ test_new_trace_with_tags_baggage")
    assert True


# ============ new_span ============

def test_new_span_inherits_trace_id():
    parent = new_trace()
    child = new_span(parent, "provider_call")
    assert child.trace_id == parent.trace_id
    assert child.parent_span_id == parent.span_id
    assert child.span_id != parent.span_id
    assert child.tags["span.name"] == "provider_call"
    print("  ✓ test_new_span_inherits_trace_id")
    assert True


def test_new_span_inherits_baggage():
    parent = new_trace(baggage={"k": "v"})
    child = new_span(parent, "x")
    assert child.baggage == {"k": "v"}
    print("  ✓ test_new_span_inherits_baggage")
    assert True


def test_new_span_extra_tags_merged():
    parent = new_trace(tags={"env": "prod"})
    child = new_span(parent, "x", tags={"phase": "aggregate"})
    assert child.tags["env"] == "prod"
    assert child.tags["phase"] == "aggregate"
    print("  ✓ test_new_span_extra_tags_merged")
    assert True


def test_new_span_three_levels():
    root = new_trace()
    mid = new_span(root, "moa_aggregate")
    leaf = new_span(mid, "provider_call")
    assert leaf.trace_id == root.trace_id == mid.trace_id
    assert mid.parent_span_id == root.span_id
    assert leaf.parent_span_id == mid.span_id
    print("  ✓ test_new_span_three_levels")
    assert True


# ============ format_traceparent ============

def test_format_traceparent_w3c_format():
    ctx = TraceContext(
        trace_id="0af7651916cd43dd8448eb211c80319c",
        parent_span_id=None,
        span_id="b7ad6b7169203331",
        start_ts=time.time(),
    )
    s = format_traceparent(ctx)
    parts = s.split("-")
    assert len(parts) == 4
    assert parts[0] == "00"
    assert parts[1] == "0af7651916cd43dd8448eb211c80319c"
    assert parts[2] == "b7ad6b7169203331"
    assert parts[3] == "01"
    print("  ✓ test_format_traceparent_w3c_format")
    assert True


def test_format_traceparent_custom_flags():
    ctx = new_trace()
    s = format_traceparent(ctx, flags="00")
    assert s.endswith("-00")
    print("  ✓ test_format_traceparent_custom_flags")
    assert True


def test_format_traceparent_invalid_flags_fallback():
    ctx = new_trace()
    s = format_traceparent(ctx, flags="zz")
    assert s.endswith("-01")
    print("  ✓ test_format_traceparent_invalid_flags_fallback")
    assert True


# ============ parse_traceparent ============

def test_parse_traceparent_valid():
    h = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    ctx = parse_traceparent(h)
    assert ctx is not None
    assert ctx.trace_id == "0af7651916cd43dd8448eb211c80319c"
    assert ctx.parent_span_id == "b7ad6b7169203331"
    assert ctx.span_id != "b7ad6b7169203331"
    assert len(ctx.span_id) == 16
    print("  ✓ test_parse_traceparent_valid")
    assert True


def test_parse_traceparent_invalid_returns_none():
    assert parse_traceparent("") is None
    assert parse_traceparent(None) is None
    assert parse_traceparent("garbage") is None
    assert parse_traceparent("00-XX-CD-01") is None
    assert parse_traceparent("01-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01") is None
    print("  ✓ test_parse_traceparent_invalid_returns_none")
    assert True


def test_parse_traceparent_all_zero_invalid():
    h = "00-00000000000000000000000000000000-b7ad6b7169203331-01"
    assert parse_traceparent(h) is None
    h2 = "00-0af7651916cd43dd8448eb211c80319c-0000000000000000-01"
    assert parse_traceparent(h2) is None
    print("  ✓ test_parse_traceparent_all_zero_invalid")
    assert True


def test_parse_traceparent_roundtrip():
    ctx0 = new_trace()
    h = format_traceparent(ctx0)
    ctx1 = parse_traceparent(h)
    assert ctx1 is not None
    assert ctx1.trace_id == ctx0.trace_id
    print("  ✓ test_parse_traceparent_roundtrip")
    assert True


# ============ TraceCollector start / end ============

def test_collector_start_trace_new():
    c = TraceCollector()
    ctx = c.start_trace()
    assert ctx in [c._traces[k]["ctx"] for k in c._traces]
    assert c.stats()["total"] == 1
    assert c.stats()["in_flight"] == 1
    print("  ✓ test_collector_start_trace_new")
    assert True


def test_collector_start_trace_from_header():
    c = TraceCollector()
    h = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    ctx = c.start_trace(h)
    assert ctx.trace_id == "0af7651916cd43dd8448eb211c80319c"
    print("  ✓ test_collector_start_trace_from_header")
    assert True


def test_collector_start_trace_invalid_header_falls_back_to_new():
    c = TraceCollector()
    ctx = c.start_trace("garbage")
    assert len(ctx.trace_id) == 32  # 新生成
    print("  ✓ test_collector_start_trace_invalid_header_falls_back_to_new")
    assert True


def test_collector_end_trace_ok():
    c = TraceCollector()
    ctx = c.start_trace()
    c.end_trace(ctx, status="ok")
    s = c.stats()
    assert s["ok"] == 1
    assert s["in_flight"] == 0
    print("  ✓ test_collector_end_trace_ok")
    assert True


def test_collector_end_trace_error():
    c = TraceCollector()
    ctx = c.start_trace()
    c.end_trace(ctx, status="error", error="boom")
    assert c.stats()["error"] == 1
    print("  ✓ test_collector_end_trace_error")
    assert True


# ============ record_span ============

def test_record_span_multi_layer():
    c = TraceCollector()
    root = c.start_trace()
    mid = new_span(root, "moa_aggregate")
    leaf1 = new_span(mid, "provider_a")
    leaf2 = new_span(mid, "provider_b")
    c.record_span(leaf1, "provider_a", 120.5, attrs={"model": "m1"})
    c.record_span(leaf2, "provider_b", 80.0, attrs={"model": "m2"})
    c.record_span(mid, "moa_aggregate", 250.0)
    c.end_trace(root)
    t = c.get_trace(root.trace_id)
    assert t is not None
    assert len(t["spans"]) == 3
    # tree 至少 1 root (moa_aggregate),其下 2 leaves
    assert len(t["tree"]) >= 1
    top = t["tree"][0]
    assert top["name"] == "moa_aggregate"
    assert len(top["children"]) == 2
    print("  ✓ test_record_span_multi_layer")
    assert True


def test_record_span_attrs():
    c = TraceCollector()
    ctx = c.start_trace()
    c.record_span(ctx, "x", 10.0, attrs={"a": 1, "b": "two"})
    t = c.get_trace(ctx.trace_id)
    sp = t["spans"][0]
    assert sp["attrs"] == {"a": 1, "b": "two"}
    assert sp["duration_ms"] == 10.0
    print("  ✓ test_record_span_attrs")
    assert True


# ============ get_trace ============

def test_get_trace_missing_returns_none():
    c = TraceCollector()
    assert c.get_trace("nonexistent") is None
    print("  ✓ test_get_trace_missing_returns_none")
    assert True


def test_get_trace_empty():
    c = TraceCollector()
    ctx = c.start_trace()
    t = c.get_trace(ctx.trace_id)
    assert t is not None
    assert t["spans"] == []
    assert t["tree"] == []
    assert t["status"] == "ok"
    assert t["error"] is None
    print("  ✓ test_get_trace_empty")
    assert True


def test_get_trace_tree_structure():
    c = TraceCollector()
    root = c.start_trace()
    a = new_span(root, "a")
    b = new_span(a, "b")
    c1 = new_span(a, "c")
    c.record_span(b, "b", 5.0)
    c.record_span(c1, "c", 7.0)
    c.record_span(a, "a", 12.0)
    t = c.get_trace(root.trace_id)
    assert len(t["tree"]) == 1
    assert t["tree"][0]["name"] == "a"
    children = {ch["name"] for ch in t["tree"][0]["children"]}
    assert children == {"b", "c"}
    print("  ✓ test_get_trace_tree_structure")
    assert True


# ============ query ============

def test_query_by_since_ts():
    c = TraceCollector()
    ctx1 = c.start_trace()
    c.end_trace(ctx1)
    time.sleep(0.05)
    cutoff = time.time()
    time.sleep(0.05)
    ctx2 = c.start_trace()
    c.end_trace(ctx2)
    res = c.query(since_ts=cutoff)
    assert all(r["trace_id"] == ctx2.trace_id for r in res)
    print("  ✓ test_query_by_since_ts")
    assert True


def test_query_by_min_duration():
    c = TraceCollector()
    ctx_fast = c.start_trace()
    c.end_trace(ctx_fast)
    ctx_slow = c.start_trace()
    time.sleep(0.12)
    c.end_trace(ctx_slow)
    res = c.query(min_duration_ms=100.0)
    assert any(r["trace_id"] == ctx_slow.trace_id for r in res)
    assert all(r["trace_id"] != ctx_fast.trace_id for r in res)
    print("  ✓ test_query_by_min_duration")
    assert True


def test_query_by_status():
    c = TraceCollector()
    ctx_ok = c.start_trace()
    c.end_trace(ctx_ok, status="ok")
    ctx_err = c.start_trace()
    c.end_trace(ctx_err, status="error", error="x")
    ok_res = c.query(status="ok")
    err_res = c.query(status="error")
    assert all(r["status"] == "ok" for r in ok_res)
    assert all(r["status"] == "error" for r in err_res)
    print("  ✓ test_query_by_status")
    assert True


def test_query_limit():
    c = TraceCollector()
    for _ in range(20):
        ctx = c.start_trace()
        c.end_trace(ctx)
    res = c.query(limit=5)
    assert len(res) == 5
    print("  ✓ test_query_limit")
    assert True


# ============ cleanup / stats ============

def test_cleanup_removes_old_completed():
    c = TraceCollector()
    ctx = c.start_trace()
    c.end_trace(ctx)
    time.sleep(0.1)
    removed = c.cleanup(older_than_seconds=0.05)
    assert removed == 1
    assert c.stats()["total"] == 0
    print("  ✓ test_cleanup_removes_old_completed")
    assert True


def test_cleanup_keeps_in_flight():
    c = TraceCollector()
    c.start_trace()  # in_flight
    time.sleep(0.1)
    removed = c.cleanup(older_than_seconds=0.05)
    assert removed == 0
    assert c.stats()["total"] == 1
    print("  ✓ test_cleanup_keeps_in_flight")
    assert True


def test_stats_accuracy():
    c = TraceCollector()
    a = c.start_trace()
    c.end_trace(a, status="ok")
    b = c.start_trace()
    c.end_trace(b, status="error", error="e")
    c.start_trace()  # in_flight
    s = c.stats()
    assert s["total"] == 3
    assert s["error"] == 1
    assert s["in_flight"] == 1
    # in_flight 也算 ok(默认 status=ok),所以 ok+error == total
    assert s["ok"] + s["error"] == 3
    assert s["ok"] >= 2
    assert s["max_traces"] == 10000
    print("  ✓ test_stats_accuracy")
    assert True


# ============ 边界 ============

def test_edge_no_parent_format():
    ctx = new_trace()
    s = format_traceparent(ctx)
    # parent_span_id 为 None 时,format 用自己的 span_id 作为 parent-id
    parts = s.split("-")
    assert parts[2] == ctx.span_id
    print("  ✓ test_edge_no_parent_format")
    assert True


def test_edge_huge_attrs():
    c = TraceCollector()
    ctx = c.start_trace()
    big = {f"k{i}": "v" * 100 for i in range(200)}
    c.record_span(ctx, "huge", 1.0, attrs=big)
    t = c.get_trace(ctx.trace_id)
    assert t["spans"][0]["attrs"]["k199"].startswith("v" * 50)
    print("  ✓ test_edge_huge_attrs")
    assert True


def test_edge_invalid_status_falls_back():
    c = TraceCollector()
    ctx = c.start_trace()
    c.end_trace(ctx, status="weird")
    s = c.stats()
    assert s["ok"] == 1  # 兜底
    print("  ✓ test_edge_invalid_status_falls_back")
    assert True


# ============ 性能 / 并发 ============

def test_perf_1000_traces():
    c = TraceCollector(max_traces=2000)
    t0 = time.time()
    for i in range(1000):
        ctx = c.start_trace()
        c.record_span(ctx, "s", 1.0)
        c.end_trace(ctx)
    elapsed = time.time() - t0
    assert c.stats()["total"] == 1000
    assert elapsed < 30.0  # 性能兜底
    print(f"  ✓ test_perf_1000_traces (elapsed={elapsed:.2f}s)")
    assert True


def test_concurrent_10_threads():
    c = TraceCollector(max_traces=5000)
    errors: list = []

    def worker(idx: int) -> None:
        try:
            for _ in range(50):
                ctx = c.start_trace()
                c.record_span(ctx, f"t{idx}", 2.0)
                c.end_trace(ctx)
        except Exception as e:
            errors.append(str(e))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    assert not errors, f"errors: {errors}"
    assert c.stats()["total"] == 500
    assert elapsed < 30.0
    print(f"  ✓ test_concurrent_10_threads (elapsed={elapsed:.2f}s)")
    assert True


# ============ 集成 ============

def test_integration_full_flow():
    c = TraceCollector()
    incoming = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    root = c.start_trace(incoming)
    assert root.trace_id == "0af7651916cd43dd8448eb211c80319c"

    cache = new_span(root, "cache_lookup")
    c.record_span(cache, "cache_lookup", 0.5, attrs={"hit": False})

    agg = new_span(root, "moa_aggregate")
    c.record_span(agg, "moa_aggregate", 200.0)
    p1 = new_span(agg, "provider_a")
    p2 = new_span(agg, "provider_b")
    c.record_span(p1, "provider_a", 80.0, attrs={"model": "m1"})
    c.record_span(p2, "provider_b", 120.0, attrs={"model": "m2"})

    c.end_trace(root, status="ok")
    t = c.get_trace(root.trace_id)
    assert t["status"] == "ok"
    assert len(t["spans"]) == 4
    # 顶层: cache + aggregate
    top_names = {n["name"] for n in t["tree"]}
    assert "cache_lookup" in top_names
    assert "moa_aggregate" in top_names
    print("  ✓ test_integration_full_flow")
    assert True
