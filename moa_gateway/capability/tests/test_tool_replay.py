"""moa_gateway.capability.tool_replay 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from moa_gateway.capability.tool_replay import (
    DEFAULT_LOOP_THRESHOLD,
    DEFAULT_LOOP_WINDOW,
    ReplayResult,
    ToolCall,
    detect_tool_loop,
    extract_tool_calls,
    format_tool_calls_for_aggregator,
    hash_arguments,
    replay_tool_calls,
    should_disable_tool_choice,
)


# =============================================================================
# extract_tool_calls
# =============================================================================


def test_extract_single_tool_use():
    """单 tool_use 提取 → 1 个 ToolCall"""
    text = '<tool_use name="search" id="c1">{"q": "weather"}</tool_use>'
    tcs = extract_tool_calls(text, proposal_idx=0)
    assert len(tcs) == 1
    assert isinstance(tcs[0], ToolCall)
    assert tcs[0].id == "c1"
    assert tcs[0].name == "search"
    assert tcs[0].arguments == {"q": "weather"}
    assert tcs[0].source_proposal_idx == 0
    print(f"  ✓ test_extract_single_tool_use (name={tcs[0].name}, args={tcs[0].arguments})")
    return True


def test_extract_multiple_tool_uses():
    """多 tool_use 提取 → 多个 ToolCall,顺序保留"""
    text = (
        'I will call tools.\n'
        '<tool_use name="search" id="a">{"q": "x"}</tool_use>\n'
        '<tool_use name="calc" id="b">{"expr": "1+1"}</tool_use>\n'
        '<tool_use name="search" id="c">{"q": "y"}</tool_use>\n'
    )
    tcs = extract_tool_calls(text, proposal_idx=3)
    assert len(tcs) == 3
    assert [t.id for t in tcs] == ["a", "b", "c"]
    assert [t.name for t in tcs] == ["search", "calc", "search"]
    assert tcs[0].source_proposal_idx == 3
    print(f"  ✓ test_extract_multiple_tool_uses (n={len(tcs)})")
    return True


def test_extract_json_args_parsed():
    """JSON arguments 正确解析成 dict"""
    text = (
        '<tool_use name="http" id="h1">'
        '{"url": "https://example.com", "method": "POST", "headers": {"X": "1"}}'
        '</tool_use>'
    )
    tcs = extract_tool_calls(text)
    assert len(tcs) == 1
    args = tcs[0].arguments
    assert args["url"] == "https://example.com"
    assert args["method"] == "POST"
    assert args["headers"] == {"X": "1"}
    print(f"  ✓ test_extract_json_args_parsed (args={args})")
    return True


def test_extract_no_tool_use_returns_empty():
    """无 tool_use → []"""
    assert extract_tool_calls("no tools here") == []
    assert extract_tool_calls("") == []
    print("  ✓ test_extract_no_tool_use_returns_empty")
    return True


def test_extract_missing_id_auto_generates():
    """缺省 id → 自动 call_<idx>"""
    text = (
        '<tool_use name="a">{}</tool_use>'
        '<tool_use name="b">{}</tool_use>'
    )
    tcs = extract_tool_calls(text)
    assert tcs[0].id == "call_0"
    assert tcs[1].id == "call_1"
    print(f"  ✓ test_extract_missing_id_auto_generates (ids={tcs[0].id},{tcs[1].id})")
    return True


def test_extract_invalid_json_fallsback_to_empty_args():
    """JSON 解析失败 → arguments={} 不抛"""
    text = '<tool_use name="bad" id="b1">not json {{</tool_use>'
    tcs = extract_tool_calls(text)
    assert len(tcs) == 1
    assert tcs[0].name == "bad"
    assert tcs[0].arguments == {}
    print("  ✓ test_extract_invalid_json_fallsback_to_empty_args")
    return True


# =============================================================================
# replay_tool_calls
# =============================================================================


def test_replay_same_tool_merged():
    """同 name + 同 args → 合并成 1 条"""
    proposals = [
        '<tool_use name="search" id="a">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="b">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="c">{"q": "x"}</tool_use>',
    ]
    r = replay_tool_calls(proposals)
    assert isinstance(r, ReplayResult)
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "search"
    assert r.tool_calls[0].arguments == {"q": "x"}
    assert r.deduplicated_count == 2
    assert r.conflicts_resolved == 0
    print(f"  ✓ test_replay_same_tool_merged (dedup={r.deduplicated_count})")
    return True


def test_replay_different_tools_preserved():
    """不同 name 全部保留"""
    proposals = [
        '<tool_use name="search" id="a">{"q": "x"}</tool_use>',
        '<tool_use name="calc" id="b">{"expr": "1+1"}</tool_use>',
        '<tool_use name="http" id="c">{"url": "u"}</tool_use>',
    ]
    r = replay_tool_calls(proposals)
    assert len(r.tool_calls) == 3
    names = sorted(t.name for t in r.tool_calls)
    assert names == ["calc", "http", "search"]
    assert r.deduplicated_count == 0
    assert r.conflicts_resolved == 0
    print(f"  ✓ test_replay_different_tools_preserved (n={len(r.tool_calls)})")
    return True


def test_replay_conflict_picks_most_frequent():
    """同 name 不同 args → 选最频繁的"""
    proposals = [
        # search with "x" appears 3 times, with "y" appears 1 time
        '<tool_use name="search" id="a">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="b">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="c">{"q": "x"}</tool_use>',
        '<tool_use name="search" id="d">{"q": "y"}</tool_use>',
    ]
    r = replay_tool_calls(proposals)
    assert len(r.tool_calls) == 1
    assert r.tool_calls[0].name == "search"
    assert r.tool_calls[0].arguments == {"q": "x"}
    assert r.conflicts_resolved == 1
    print(f"  ✓ test_replay_conflict_picks_most_frequent "
          f"(resolved={r.conflicts_resolved}, winner={r.tool_calls[0].arguments})")
    return True


def test_replay_dedup_count_correct():
    """deduplicated_count = 输入总数 - 输出数"""
    proposals = [
        '<tool_use name="a" id="x">{}</tool_use>',
        '<tool_use name="a" id="y">{}</tool_use>',  # dup of x
        '<tool_use name="b" id="z">{}</tool_use>',
    ]
    r = replay_tool_calls(proposals)
    # 3 input, 2 output (a and b) → dedup = 1
    assert len(r.tool_calls) == 2
    assert r.deduplicated_count == 1
    print(f"  ✓ test_replay_dedup_count_correct (dedup={r.deduplicated_count})")
    return True


def test_replay_aggregated_arguments_merged():
    """aggregated_arguments = {tool_name: args_dict}"""
    proposals = [
        '<tool_use name="search" id="a">{"q": "x"}</tool_use>',
        '<tool_use name="calc" id="b">{"expr": "1+1"}</tool_use>',
    ]
    r = replay_tool_calls(proposals)
    assert "search" in r.aggregated_arguments
    assert "calc" in r.aggregated_arguments
    assert r.aggregated_arguments["search"] == {"q": "x"}
    assert r.aggregated_arguments["calc"] == {"expr": "1+1"}
    print(f"  ✓ test_replay_aggregated_arguments_merged (agg={r.aggregated_arguments})")
    return True


def test_replay_empty_proposals():
    """空 proposals → 空 ReplayResult"""
    r = replay_tool_calls([])
    assert r.tool_calls == []
    assert r.aggregated_arguments == {}
    assert r.deduplicated_count == 0
    assert r.conflicts_resolved == 0
    print("  ✓ test_replay_empty_proposals")
    return True


def test_replay_source_indices_propagated():
    """source_indices 自定义 idx 时,ToolCall.source_proposal_idx 用之"""
    proposals = [
        '<tool_use name="a" id="x">{}</tool_use>',
        '<tool_use name="a" id="y">{}</tool_use>',  # dup
    ]
    r = replay_tool_calls(proposals, source_indices=[10, 20])
    assert len(r.tool_calls) == 1
    # 取首次出现:idx=10
    assert r.tool_calls[0].source_proposal_idx == 10
    print(f"  ✓ test_replay_source_indices_propagated (idx={r.tool_calls[0].source_proposal_idx})")
    return True


# =============================================================================
# should_disable_tool_choice
# =============================================================================


def test_should_disable_3_consecutive_same_tool_true():
    """连续 3 次调用 tool → True"""
    assert should_disable_tool_choice(call_count=3, last_n_calls=3) is True
    assert should_disable_tool_choice(call_count=5, last_n_calls=5) is True
    print("  ✓ test_should_disable_3_consecutive_same_tool_true")
    return True


def test_should_disable_2_calls_false():
    """2 次调用 → False(未到 threshold)"""
    assert should_disable_tool_choice(call_count=2, last_n_calls=2) is False
    assert should_disable_tool_choice(call_count=0, last_n_calls=0) is False
    print("  ✓ test_should_disable_2_calls_false")
    return True


def test_should_disable_mixed_calls_false():
    """last_n_calls != call_count(中间有非 tool 调用)→ False"""
    # call_count=5 但 last_n_calls=2,说明最近 N 次并非全是 tool
    assert should_disable_tool_choice(call_count=5, last_n_calls=2) is False
    # call_count=10 但 last_n_calls=4(中间有 text 间隔)
    assert should_disable_tool_choice(call_count=10, last_n_calls=4) is False
    print("  ✓ test_should_disable_mixed_calls_false")
    return True


def test_should_disable_custom_threshold():
    """自定义 threshold=5"""
    # 4 次 → False(未到 5)
    assert should_disable_tool_choice(call_count=4, last_n_calls=4, threshold=5) is False
    # 5 次 → True
    assert should_disable_tool_choice(call_count=5, last_n_calls=5, threshold=5) is True
    print(f"  ✓ test_should_disable_custom_threshold (default={DEFAULT_LOOP_THRESHOLD})")
    return True


# =============================================================================
# detect_tool_loop
# =============================================================================


def test_detect_loop_same_tool_repeated_returns():
    """同 tool 重复出现 → 返回首次出现那条"""
    calls = [
        ToolCall(id="1", name="search", arguments={"q": "x"}),
        ToolCall(id="2", name="search", arguments={"q": "x"}),
        ToolCall(id="3", name="search", arguments={"q": "x"}),
    ]
    loop = detect_tool_loop(calls, window=5)
    assert loop is not None
    assert loop.id == "1"
    assert loop.name == "search"
    print(f"  ✓ test_detect_loop_same_tool_repeated_returns (loop={loop.name})")
    return True


def test_detect_loop_different_tools_returns_none():
    """不同 tool → None"""
    calls = [
        ToolCall(id="1", name="search", arguments={"q": "x"}),
        ToolCall(id="2", name="calc", arguments={"expr": "1+1"}),
        ToolCall(id="3", name="http", arguments={"url": "u"}),
    ]
    assert detect_tool_loop(calls, window=5) is None
    print("  ✓ test_detect_loop_different_tools_returns_none")
    return True


def test_detect_loop_outside_window_not_triggered():
    """窗口外重复不触发"""
    # 在 window=3 内只有 search 出现 1 次;重复在 6 步前
    calls = [
        ToolCall(id="1", name="search", arguments={"q": "x"}),  # out of window
        ToolCall(id="2", name="calc", arguments={"expr": "1+1"}),
        ToolCall(id="3", name="http", arguments={"url": "u"}),
        ToolCall(id="4", name="search", arguments={"q": "x"}),  # in window
    ]
    loop = detect_tool_loop(calls, window=3)
    # window=3 → tail = [2, 3, 4] = [calc, http, search]
    # 没有重复 → None
    assert loop is None
    print("  ✓ test_detect_loop_outside_window_not_triggered")
    return True


def test_detect_loop_empty_returns_none():
    """空 list → None"""
    assert detect_tool_loop([]) is None
    assert detect_tool_loop([], window=5) is None
    print("  ✓ test_detect_loop_empty_returns_none")
    return True


def test_detect_loop_different_args_same_name_not_loop():
    """同 name 不同 args 不算 loop(只看 name+args 一起)"""
    calls = [
        ToolCall(id="1", name="search", arguments={"q": "x"}),
        ToolCall(id="2", name="search", arguments={"q": "y"}),
        ToolCall(id="3", name="search", arguments={"q": "z"}),
    ]
    assert detect_tool_loop(calls, window=5) is None
    print("  ✓ test_detect_loop_different_args_same_name_not_loop")
    return True


# =============================================================================
# format_tool_calls_for_aggregator
# =============================================================================


def test_format_tool_calls_for_aggregator():
    """format 输出包含 tool/id/args/total/unique_args"""
    calls = [
        ToolCall(id="c1", name="search", arguments={"q": "weather"}),
        ToolCall(id="c2", name="calc", arguments={"expr": "1+1"}),
    ]
    out = format_tool_calls_for_aggregator(calls)
    assert "[1]" in out
    assert "[2]" in out
    assert "tool=search" in out
    assert "tool=calc" in out
    assert "id=c1" in out
    assert "id=c2" in out
    assert '"q": "weather"' in out
    assert "total=2" in out
    assert "unique_args=2" in out
    print(f"  ✓ test_format_tool_calls_for_aggregator")
    return True


def test_format_tool_calls_empty():
    """空列表 → total=0"""
    out = format_tool_calls_for_aggregator([])
    assert "total=0" in out
    assert "unique_args=0" in out
    print("  ✓ test_format_tool_calls_empty")
    return True


def test_format_tool_calls_duplicate_args_count():
    """重复 args 合并到 unique_args"""
    calls = [
        ToolCall(id="c1", name="search", arguments={"q": "x"}),
        ToolCall(id="c2", name="search", arguments={"q": "x"}),  # same args
    ]
    out = format_tool_calls_for_aggregator(calls)
    assert "total=2" in out
    assert "unique_args=1" in out
    print(f"  ✓ test_format_tool_calls_duplicate_args_count")
    return True


# =============================================================================
# JSON 序列化
# =============================================================================


def test_tool_call_json_serialization():
    """ToolCall.to_dict() → OpenAI 风格 JSON"""
    tc = ToolCall(id="c1", name="search", arguments={"q": "weather"})
    d = tc.to_dict()
    # 必须可 json.dumps
    s = json.dumps(d, ensure_ascii=False)
    parsed = json.loads(s)
    assert parsed["id"] == "c1"
    assert parsed["type"] == "function"
    assert parsed["function"]["name"] == "search"
    # arguments 是 JSON 字符串
    args = json.loads(parsed["function"]["arguments"])
    assert args == {"q": "weather"}
    print(f"  ✓ test_tool_call_json_serialization (d={d})")
    return True


def test_replay_result_json_serialization():
    """ReplayResult 全字段可 JSON 序列化"""
    proposals = [
        '<tool_use name="a" id="x">{"k": "v"}</tool_use>',
        '<tool_use name="a" id="y">{"k": "v"}</tool_use>',  # dup
    ]
    r = replay_tool_calls(proposals)
    payload = {
        "tool_calls": [t.to_dict() for t in r.tool_calls],
        "aggregated_arguments": r.aggregated_arguments,
        "deduplicated_count": r.deduplicated_count,
        "conflicts_resolved": r.conflicts_resolved,
    }
    s = json.dumps(payload, ensure_ascii=False)
    parsed = json.loads(s)
    assert len(parsed["tool_calls"]) == 1
    assert parsed["deduplicated_count"] == 1
    assert parsed["aggregated_arguments"]["a"] == {"k": "v"}
    print(f"  ✓ test_replay_result_json_serialization (n_calls={len(parsed['tool_calls'])})")
    return True


# =============================================================================
# 边界
# =============================================================================


def test_edge_zero_tool_calls():
    """边界:0 tool_calls 全链路"""
    r = replay_tool_calls(["hello", "world", ""])
    assert len(r.tool_calls) == 0
    assert r.aggregated_arguments == {}
    assert r.deduplicated_count == 0
    assert r.conflicts_resolved == 0
    assert should_disable_tool_choice(call_count=0, last_n_calls=0) is False
    assert detect_tool_loop([]) is None
    assert "total=0" in format_tool_calls_for_aggregator([])
    print("  ✓ test_edge_zero_tool_calls (all 4 funcs return safe defaults)")
    return True


def test_hash_arguments_stable():
    """hash_arguments 对等价 dict 产生相同 hash"""
    a = hash_arguments({"q": "x", "n": 1})
    b = hash_arguments({"n": 1, "q": "x"})  # 顺序不同
    assert a == b, f"sort_keys should normalize: {a} != {b}"
    c = hash_arguments({"q": "y", "n": 1})  # 值不同
    assert a != c
    assert len(a) == 16
    print(f"  ✓ test_hash_arguments_stable (hash={a})")
    return True


# =============================================================================
# Runner
# =============================================================================


def _run_all() -> int:
    """执行本文件所有 test_* 函数,返回失败数"""
    import inspect
    tests = sorted(
        [(n, f) for n, f in globals().items()
         if n.startswith("test_") and callable(f)],
        key=lambda x: x[0],
    )
    failed = 0
    for name, fn in tests:
        try:
            ok = fn()
            if ok is False:
                print(f"  ✗ {name} returned False")
                failed += 1
        except AssertionError as e:
            print(f"  ✗ {name} ASSERTION FAILED: {e}")
            failed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            print(f"  ✗ {name} EXCEPTION: {type(e).__name__}: {e}")
            traceback.print_exc()
            failed += 1
    return failed


if __name__ == "__main__":
    n_tests = sum(1 for n in globals() if n.startswith("test_") and callable(globals()[n]))
    print(f"\n=== Running {n_tests} tool_replay tests ===\n")
    failed = _run_all()
    print(f"\n=== {n_tests - failed}/{n_tests} passed ===\n")
    sys.exit(0 if failed == 0 else 1)
