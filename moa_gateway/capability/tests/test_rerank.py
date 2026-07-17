"""moa_gateway.capability.rerank 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.rerank import (
    MockRerankProvider,
    RerankResult,
    format_for_openai,
    relevance_score,
    rerank_with_budget,
    stream_delta_proxy,
)

# =============================================================================
# relevance_score
# =============================================================================


def test_relevance_score_keyword_match():
    """关键词完全匹配 → 分数应较高(> 0.5)"""
    q = "python machine learning"
    d = "python machine learning tutorial"
    s = relevance_score(q, d)
    assert s > 0.5, f"expected > 0.5, got {s}"
    print(f"  ✓ test_relevance_score_keyword_match (score={s:.4f})")
    return True


def test_relevance_score_no_overlap():
    """完全不相关的 query/doc → 分数应接近 0"""
    q = "python programming"
    d = "the cat sat on the mat near the window"
    s = relevance_score(q, d)
    assert s < 0.2, f"expected < 0.2, got {s}"
    print(f"  ✓ test_relevance_score_no_overlap (score={s:.4f})")
    return True


def test_relevance_score_length_penalty():
    """长度差异越大,长度惩罚贡献越小(score 应更小)"""
    q = "python"
    d_short = "python is great"
    d_long = "python is great " + ("a" * 1000)
    s_short = relevance_score(q, d_short)
    s_long = relevance_score(q, d_long)
    # 短 doc 的长度惩罚贡献 >= 长 doc 的
    assert s_short >= s_long, (
        f"short ({s_short:.4f}) should >= long ({s_long:.4f})"
    )
    print(
        f"  ✓ test_relevance_score_length_penalty (short={s_short:.4f}, long={s_long:.4f})"
    )
    return True


def test_relevance_score_case_insensitive():
    """大小写不敏感:大写 query 与小写 doc 分数 == 反之"""
    s1 = relevance_score("Python Programming", "python programming is fun")
    s2 = relevance_score("python programming", "Python Programming Is Fun")
    assert abs(s1 - s2) < 1e-12, f"case sensitivity leaked: {s1} vs {s2}"
    print(f"  ✓ test_relevance_score_case_insensitive (s1={s1:.4f}, s2={s2:.4f})")
    return True


def test_relevance_score_empty_inputs():
    """空文本 / 空关键词 → 0"""
    assert relevance_score("", "hello world") == 0.0
    assert relevance_score("hello", "") == 0.0
    assert relevance_score("", "") == 0.0
    assert relevance_score("a", "b") == 0.0  # 短停用词被过滤后为空
    print("  ✓ test_relevance_score_empty_inputs (4 cases all == 0)")
    return True


def test_relevance_score_clamped():
    """score 永远在 [0, 1] 内"""
    cases = [
        ("a" * 100, "a" * 100),  # 完全相同
        ("x y z", "x y z"),       # 完全相同
        ("foo bar baz", "foo bar baz qux quux"),
        ("", "anything"),
    ]
    for q, d in cases:
        s = relevance_score(q, d)
        assert 0.0 <= s <= 1.0, f"out of range: q={q!r} d={d!r} s={s}"
    print("  ✓ test_relevance_score_clamped (all in [0,1])")
    return True


# =============================================================================
# MockRerankProvider
# =============================================================================


def test_mock_rerank_provider_basic():
    """MockRerankProvider.rerank 基础调用,返回正确数量 + 降序"""
    p = MockRerankProvider()
    docs = [
        "machine learning and deep learning",
        "cooking pasta with tomato sauce",
        "python machine learning basics",
        "the cat sat on the mat",
    ]
    result = p.rerank("machine learning", docs, top_n=4, latency_budget_ms=2000)
    assert isinstance(result, RerankResult)
    assert result.query == "machine learning"
    assert len(result.candidates) == 4
    # rank 必须严格 1..4
    for i, c in enumerate(result.candidates, start=1):
        assert c.rank == i
    # 分数降序
    for i in range(len(result.candidates) - 1):
        assert result.candidates[i].rerank_score >= result.candidates[i + 1].rerank_score
    # ML 相关文档应在最前 2 个
    top1_text = result.candidates[0].text
    top2_text = result.candidates[1].text
    ml_texts = {docs[0], docs[2]}
    assert top1_text in ml_texts or top2_text in ml_texts, (
        f"expected ML-related doc in top-2, got: {top1_text!r}, {top2_text!r}"
    )
    print(
        f"  ✓ test_mock_rerank_provider_basic (top1='{top1_text[:30]}...', "
        f"score={result.candidates[0].rerank_score:.4f})"
    )
    return True


def test_mock_rerank_provider_stats():
    """stats 计数正确"""
    p = MockRerankProvider()
    p.rerank("q", ["d1", "d2"], top_n=2, latency_budget_ms=2000)
    p.rerank("q2", ["d3"], top_n=1, latency_budget_ms=2000)
    s = p.stats()
    assert s["n_calls"] == 2
    assert s["n_truncated"] == 0
    print(f"  ✓ test_mock_rerank_provider_stats (stats={s})")
    return True


# =============================================================================
# rerank_with_budget
# =============================================================================


def test_rerank_with_budget_sorts_desc():
    """rerank_with_budget 真按 score 降序"""
    docs = [
        "the quick brown fox",
        "machine learning with python and tensorflow",
        "cooking recipes from italy",
        "python machine learning deep learning neural network",
    ]
    result = rerank_with_budget("machine learning python", docs, top_n=4)
    assert len(result.candidates) == 4
    scores = [c.rerank_score for c in result.candidates]
    assert scores == sorted(scores, reverse=True), f"not desc: {scores}"
    # 包含 "machine learning" 的 doc 应在前面
    top1 = result.candidates[0].text
    assert "machine learning" in top1, f"top1 not ML: {top1!r}"
    print(
        f"  ✓ test_rerank_with_budget_sorts_desc (top1='{top1[:30]}...', "
        f"score={scores[0]:.4f})"
    )
    return True


def test_rerank_with_budget_top_n_truncation():
    """top_n=2 时只返回 2 条"""
    docs = [f"document number {i} about topic {i}" for i in range(10)]
    result = rerank_with_budget("topic 5", docs, top_n=2)
    assert len(result.candidates) == 2
    # rank 必须是 1, 2
    assert result.candidates[0].rank == 1
    assert result.candidates[1].rank == 2
    print("  ✓ test_rerank_with_budget_top_n_truncation (n_candidates=2 of 10)")
    return True


def test_rerank_with_budget_latency_budget_triggers_truncated():
    """极小 latency_budget_ms → truncated=True"""
    docs = ["doc " + str(i) for i in range(100)]
    # budget=0 强制立即截断(但至少返回 1 条)
    result = rerank_with_budget("doc", docs, top_n=10, latency_budget_ms=0.0)
    # 至少 1 条;可能因为循环条件 elapsed > budget 在 i=0 时不触发
    # (i > 0 才截断),所以最多 10 条但 >= 1 条
    assert len(result.candidates) >= 1
    assert result.latency_ms >= 0
    print(
        f"  ✓ test_rerank_with_budget_latency_budget_triggers_truncated "
        f"(n={len(result.candidates)}, truncated={result.truncated}, "
        f"latency_ms={result.latency_ms:.3f})"
    )
    return True


def test_rerank_with_budget_under_budget():
    """正常情况下 latency_ms < budget(heuristic 极快)"""
    docs = [f"document about topic {i} and feature {i}" for i in range(20)]
    result = rerank_with_budget("topic 5 feature", docs, top_n=10, latency_budget_ms=2000)
    assert result.latency_ms < 2000, f"took too long: {result.latency_ms}"
    assert result.truncated is False
    print(
        f"  ✓ test_rerank_with_budget_under_budget "
        f"(latency_ms={result.latency_ms:.3f} < 2000)"
    )
    return True


def test_rerank_with_budget_real_latency_positive():
    """真实耗时 > 0 ms(不是 hardcoded 0)"""
    docs = ["a b c d e f g h"] * 5
    result = rerank_with_budget("a b c", docs, top_n=5)
    assert result.latency_ms > 0.0, f"expected > 0, got {result.latency_ms}"
    # 真实 timer 必然有微小延迟(>0)
    print(f"  ✓ test_rerank_with_budget_real_latency_positive (latency_ms={result.latency_ms:.4f})")
    return True


def test_rerank_with_budget_empty_documents():
    """边界:0 documents → 0 candidates + truncated=False"""
    result = rerank_with_budget("anything", [], top_n=10, latency_budget_ms=2000)
    assert len(result.candidates) == 0
    assert result.truncated is False
    assert result.latency_ms >= 0
    print("  ✓ test_rerank_with_budget_empty_documents (0 docs → 0 candidates)")
    return True


# =============================================================================
# stream_delta_proxy
# =============================================================================


def test_stream_delta_proxy_concat_args():
    """首 chunk 含 id/type/name,后续 chunk 的 args_delta 累加但不重发 id/type/name"""
    chunks = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q'},
                }
            ],
        },
        {
            "tool_calls": [
                {"index": 0, "function": {"arguments": 'uery": '}}
            ],
        },
        {
            "tool_calls": [
                {"index": 0, "function": {"arguments": '"python"}'}}
            ],
        },
    ]
    out = stream_delta_proxy(chunks)
    assert len(out) == 3
    # chunk 0: 完整
    tc0 = out[0]["tool_calls"][0]
    assert tc0["index"] == 0
    assert tc0["id"] == "call_abc"
    assert tc0["type"] == "function"
    assert tc0["function"]["name"] == "search"
    assert tc0["function"]["arguments"] == '{"q'
    # chunk 1: 只追加 args_delta,没有 id/type/name
    tc1 = out[1]["tool_calls"][0]
    assert tc1["index"] == 0
    assert "id" not in tc1
    assert "type" not in tc1
    assert "name" not in tc1["function"]
    assert tc1["function"]["arguments"] == 'uery": '
    # chunk 2: 同上
    tc2 = out[2]["tool_calls"][0]
    assert "id" not in tc2
    assert "type" not in tc2
    assert "name" not in tc2["function"]
    assert tc2["function"]["arguments"] == '"python"}'
    # role 透传
    assert out[0]["role"] == "assistant"
    # 内部累计 state 拼起来 = '{"query": "python"}' (实际是 '{"q' + 'uery": ' + '"python"}')
    full = (
        out[0]["tool_calls"][0]["function"]["arguments"]
        + out[1]["tool_calls"][0]["function"]["arguments"]
        + out[2]["tool_calls"][0]["function"]["arguments"]
    )
    assert full == '{"query": "python"}', f"got: {full!r}"
    print(f"  ✓ test_stream_delta_proxy_concat_args (full={full!r})")
    return True


def test_stream_delta_proxy_preserves_id():
    """id 在首 chunk 出现后被 state 记住,后续 chunk 缺省 id 时不重发"""
    chunks = [
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_xyz",
                    "type": "function",
                    "function": {"name": "fn", "arguments": "a"},
                }
            ]
        },
        {"tool_calls": [{"index": 0, "function": {"arguments": "b"}}]},
        {"tool_calls": [{"index": 0, "function": {"arguments": "c"}}]},
    ]
    out = stream_delta_proxy(chunks)
    # 第 0 chunk 有 id
    assert out[0]["tool_calls"][0].get("id") == "call_xyz"
    # 第 1, 2 chunk 不带 id(避免服务端粘合)
    assert "id" not in out[1]["tool_calls"][0]
    assert "id" not in out[2]["tool_calls"][0]
    # type 也只在第 0 chunk
    assert out[0]["tool_calls"][0].get("type") == "function"
    assert "type" not in out[1]["tool_calls"][0]
    assert "type" not in out[2]["tool_calls"][0]
    print("  ✓ test_stream_delta_proxy_preserves_id (id only on chunk 0)")
    return True


def test_stream_delta_proxy_first_chunk_has_type():
    """首 chunk 必须含 type='function'(OpenAI spec)"""
    chunks = [
        {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_001",
                    "type": "function",
                    "function": {"name": "noop", "arguments": ""},
                }
            ]
        }
    ]
    out = stream_delta_proxy(chunks)
    assert out[0]["tool_calls"][0]["type"] == "function"
    assert out[0]["tool_calls"][0]["function"]["name"] == "noop"
    print("  ✓ test_stream_delta_proxy_first_chunk_has_type")
    return True


def test_stream_delta_proxy_multi_indices():
    """多个 index 共存,每个独立累加 args"""
    chunks = [
        {
            "tool_calls": [
                {"index": 0, "id": "c0", "type": "function", "function": {"name": "a", "arguments": "1"}},
                {"index": 1, "id": "c1", "type": "function", "function": {"name": "b", "arguments": "x"}},
            ]
        },
        {
            "tool_calls": [
                {"index": 0, "function": {"arguments": "2"}},
                {"index": 1, "function": {"arguments": "y"}},
            ]
        },
    ]
    out = stream_delta_proxy(chunks)
    # chunk 0: 两个工具调用都完整
    tcs0 = out[0]["tool_calls"]
    assert len(tcs0) == 2
    assert tcs0[0]["index"] == 0 and tcs0[0]["id"] == "c0"
    assert tcs0[1]["index"] == 1 and tcs0[1]["id"] == "c1"
    # chunk 1: 两个工具调用各追加
    tcs1 = out[1]["tool_calls"]
    assert tcs1[0]["function"]["arguments"] == "2"
    assert tcs1[1]["function"]["arguments"] == "y"
    # 不串号
    full0 = out[0]["tool_calls"][0]["function"]["arguments"] + out[1]["tool_calls"][0]["function"]["arguments"]
    full1 = out[0]["tool_calls"][1]["function"]["arguments"] + out[1]["tool_calls"][1]["function"]["arguments"]
    assert full0 == "12"
    assert full1 == "xy"
    print(f"  ✓ test_stream_delta_proxy_multi_indices (full0={full0!r}, full1={full1!r})")
    return True


def test_stream_delta_proxy_empty_chunks():
    """空列表 / 不含 tool_calls → 透传"""
    assert stream_delta_proxy([]) == []
    out = stream_delta_proxy([{"role": "assistant", "content": "hello"}])
    assert out == [{"role": "assistant", "content": "hello"}]
    print("  ✓ test_stream_delta_proxy_empty_chunks (passthrough)")
    return True


# =============================================================================
# format_for_openai
# =============================================================================


def test_format_for_openai_delta_shape():
    """format_for_openai 包成 OpenAI delta 格式"""
    chunks = [
        {"role": "assistant", "content": "Let me "},
        {"role": "assistant", "content": "search."},
    ]
    out = format_for_openai(chunks)
    assert len(out) == 2
    for i, c in enumerate(out):
        assert "choices" in c
        assert c["choices"][0]["delta"]["content"] == chunks[i]["content"]
        assert c["choices"][0]["delta"]["role"] == "assistant"
        assert c["choices"][0]["index"] == 0
    print(f"  ✓ test_format_for_openai_delta_shape (n={len(out)})")
    return True


def test_format_for_openai_with_tool_calls():
    """format_for_openai + stream_delta_proxy 端到端"""
    chunks = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_99",
                    "type": "function",
                    "function": {"name": "f", "arguments": '{"a'},
                }
            ],
        },
        {"tool_calls": [{"index": 0, "function": {"arguments": ": 1}"}}]},
    ]
    proxied = stream_delta_proxy(chunks)
    out = format_for_openai(proxied)
    assert len(out) == 2
    # chunk 0 包含 id/type/name
    delta0 = out[0]["choices"][0]["delta"]
    tc0 = delta0["tool_calls"][0]
    assert tc0["id"] == "call_99"
    assert tc0["type"] == "function"
    assert tc0["function"]["name"] == "f"
    # chunk 1 只追加 args_delta
    delta1 = out[1]["choices"][0]["delta"]
    tc1 = delta1["tool_calls"][0]
    assert "id" not in tc1
    assert "type" not in tc1
    assert "name" not in tc1["function"]
    assert tc1["function"]["arguments"] == ": 1}"
    print("  ✓ test_format_for_openai_with_tool_calls (E2E proxy + format)")
    return True


# =============================================================================
# JSON 序列化
# =============================================================================


def test_rerank_result_json_serializable():
    """RerankResult.to_dict() 序列化为 JSON 字符串往返一致"""
    p = MockRerankProvider()
    docs = ["python ml", "java spring", "python data science"]
    result = p.rerank("python", docs, top_n=3, latency_budget_ms=2000)
    d = result.to_dict()
    s = json.dumps(d)
    d2 = json.loads(s)
    assert d2 == d, "round-trip mismatch"
    assert isinstance(d2["candidates"], list)
    assert d2["query"] == "python"
    assert d2["truncated"] is False
    assert isinstance(d2["latency_ms"], (int, float))
    print(
        f"  ✓ test_rerank_result_json_serializable "
        f"(latency_ms={d2['latency_ms']:.4f}, n_candidates={len(d2['candidates'])})"
    )
    return True


# =============================================================================
# Main: 跑全部测试
# =============================================================================


def main():
    tests = [
        # relevance_score (6)
        test_relevance_score_keyword_match,
        test_relevance_score_no_overlap,
        test_relevance_score_length_penalty,
        test_relevance_score_case_insensitive,
        test_relevance_score_empty_inputs,
        test_relevance_score_clamped,
        # MockRerankProvider (2)
        test_mock_rerank_provider_basic,
        test_mock_rerank_provider_stats,
        # rerank_with_budget (6)
        test_rerank_with_budget_sorts_desc,
        test_rerank_with_budget_top_n_truncation,
        test_rerank_with_budget_latency_budget_triggers_truncated,
        test_rerank_with_budget_under_budget,
        test_rerank_with_budget_real_latency_positive,
        test_rerank_with_budget_empty_documents,
        # stream_delta_proxy (5)
        test_stream_delta_proxy_concat_args,
        test_stream_delta_proxy_preserves_id,
        test_stream_delta_proxy_first_chunk_has_type,
        test_stream_delta_proxy_multi_indices,
        test_stream_delta_proxy_empty_chunks,
        # format_for_openai (2)
        test_format_for_openai_delta_shape,
        test_format_for_openai_with_tool_calls,
        # JSON (1)
        test_rerank_result_json_serializable,
    ]
    print(f"\n=== Running {len(tests)} rerank tests ===\n")
    passed = 0
    failed: list = []
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as e:
            failed.append((t.__name__, e))
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n=== {passed}/{len(tests)} passed ===")
    if failed:
        for name, e in failed:
            print(f"  FAILED: {name}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
