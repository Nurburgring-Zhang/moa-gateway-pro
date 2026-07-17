"""llm_merge tests — L-32 LLM 响应合并 (multi-source) + L-33 LLM 降级 chain"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from moa_gateway.capability.llm_merge import (
    AllProvidersFailedError,
    FallbackChain,
    LLMResponse,
    MergedResult,
    MergeStrategy,
    merge_responses,
    response_to_json,
    result_to_json,
)


# ============ LLMResponse 字段 ============
class TestLLMResponse:
    def test_fields(self):
        r = LLMResponse(
            source="gpt-4o",
            text="hello",
            tokens=10,
            latency_ms=120.5,
            cost_usd=0.002,
            confidence=0.9,
        )
        assert r.source == "gpt-4o"
        assert r.text == "hello"
        assert r.tokens == 10
        assert r.latency_ms == 120.5
        assert r.cost_usd == 0.002
        assert r.confidence == 0.9

    def test_to_dict(self):
        r = LLMResponse("m1", "t", 1, 1.0, 0.1, 0.5)
        d = r.to_dict()
        assert d == {
            "source": "m1",
            "text": "t",
            "tokens": 1,
            "latency_ms": 1.0,
            "cost_usd": 0.1,
            "confidence": 0.5,
        }


# ============ MergeStrategy 枚举 ============
class TestMergeStrategy:
    def test_5_strategies(self):
        assert len(MergeStrategy) == 5
        assert {s.value for s in MergeStrategy} == {
            "CONCAT", "DEDUP", "VOTE", "WEIGHTED", "FIRST_SUCCESS"
        }

    def test_str_inherits(self):
        # 继承 str,可直接当字符串使用
        assert MergeStrategy.CONCAT == "CONCAT"
        assert MergeStrategy.VOTE.value == "VOTE"


# ============ MergedResult 字段 ============
class TestMergedResult:
    def test_to_dict(self):
        r = MergedResult(
            text="hi",
            sources=["a", "b"],
            strategy=MergeStrategy.CONCAT,
            total_tokens=20,
            total_cost_usd=0.01,
            confidence=0.8,
        )
        d = r.to_dict()
        assert d["text"] == "hi"
        assert d["sources"] == ["a", "b"]
        assert d["strategy"] == "CONCAT"
        assert d["total_tokens"] == 20
        assert d["total_cost_usd"] == 0.01
        assert d["confidence"] == 0.8


# ============ CONCAT ============
class TestConcat:
    def test_concat_joins_with_dashes(self):
        responses = [
            LLMResponse("a", "first", 10, 100.0, 0.001, 0.9),
            LLMResponse("b", "second", 20, 200.0, 0.002, 0.8),
            LLMResponse("c", "third", 30, 300.0, 0.003, 0.7),
        ]
        result = merge_responses(responses, MergeStrategy.CONCAT)
        assert result.text == "first---second---third"
        assert result.strategy == MergeStrategy.CONCAT
        assert result.sources == ["a", "b", "c"]


# ============ DEDUP ============
class TestDedup:
    def test_removes_exact_duplicates(self):
        responses = [
            LLMResponse("a", "hello", 10, 100.0, 0.001, 0.9),
            LLMResponse("b", "hello", 10, 100.0, 0.001, 0.8),
            LLMResponse("c", "world", 20, 200.0, 0.002, 0.7),
        ]
        result = merge_responses(responses, MergeStrategy.DEDUP)
        assert result.text == "hello---world"
        assert result.sources == ["a", "c"]

    def test_normalizes_whitespace(self):
        responses = [
            LLMResponse("a", "hello", 10, 100.0, 0.001, 0.9),
            LLMResponse("b", "  HELLO\n", 10, 100.0, 0.001, 0.8),
        ]
        result = merge_responses(responses, MergeStrategy.DEDUP)
        # 规范化后相等,只保留 a
        assert result.sources == ["a"]


# ============ VOTE ============
class TestVote:
    def test_majority_wins(self):
        responses = [
            LLMResponse("a", "yes", 10, 100.0, 0.001, 0.5),
            LLMResponse("b", "yes", 10, 100.0, 0.001, 0.5),
            LLMResponse("c", "yes", 10, 100.0, 0.001, 0.5),
            LLMResponse("d", "no", 10, 100.0, 0.001, 0.5),
        ]
        result = merge_responses(responses, MergeStrategy.VOTE)
        assert result.text == "yes"
        # 3 票占比 0.75, 平均 confidence 0.5
        assert abs(result.confidence - 0.75 * 0.5) < 1e-9
        assert "a" in result.sources and "b" in result.sources and "c" in result.sources

    def test_tie_breaks_by_confidence(self):
        responses = [
            LLMResponse("a", "alpha", 10, 100.0, 0.001, 0.9),
            LLMResponse("b", "alpha", 10, 100.0, 0.001, 0.9),
            LLMResponse("c", "beta", 10, 100.0, 0.001, 0.4),
            LLMResponse("d", "beta", 10, 100.0, 0.001, 0.4),
        ]
        result = merge_responses(responses, MergeStrategy.VOTE)
        # 平局 2:2,但 alpha 置信度总和高(0.9+0.9=1.8 > 0.4+0.4=0.8)
        assert result.text == "alpha"


# ============ WEIGHTED ============
class TestWeighted:
    def test_picks_highest_confidence(self):
        responses = [
            LLMResponse("a", "low", 10, 100.0, 0.001, 0.3),
            LLMResponse("b", "mid", 10, 100.0, 0.001, 0.6),
            LLMResponse("c", "high", 10, 100.0, 0.001, 0.95),
        ]
        result = merge_responses(responses, MergeStrategy.WEIGHTED)
        assert result.text == "high"
        assert result.sources == ["c"]
        assert result.confidence == 0.95


# ============ FIRST_SUCCESS ============
class TestFirstSuccess:
    def test_picks_first_nonempty(self):
        responses = [
            LLMResponse("a", "", 10, 100.0, 0.001, 0.5),
            LLMResponse("b", "real answer", 10, 100.0, 0.001, 0.8),
            LLMResponse("c", "another", 10, 100.0, 0.001, 0.9),
        ]
        result = merge_responses(responses, MergeStrategy.FIRST_SUCCESS)
        assert result.text == "real answer"
        assert result.sources == ["b"]

    def test_all_empty_falls_back_to_first(self):
        responses = [
            LLMResponse("a", "", 10, 100.0, 0.001, 0.5),
            LLMResponse("b", "   ", 10, 100.0, 0.001, 0.8),
        ]
        result = merge_responses(responses, MergeStrategy.FIRST_SUCCESS)
        # 全部空白,降级到第一个
        assert result.sources == ["a"]


# ============ Edge cases ============
class TestEdgeCases:
    def test_zero_responses(self):
        result = merge_responses([], MergeStrategy.CONCAT)
        assert result.text == ""
        assert result.sources == []
        assert result.total_tokens == 0
        assert result.total_cost_usd == 0.0
        assert result.confidence == 0.0

    def test_one_response(self):
        r = LLMResponse("solo", "only one", 5, 50.0, 0.0005, 0.7)
        result = merge_responses([r], MergeStrategy.VOTE)
        assert result.text == "only one"
        assert result.sources == ["solo"]

    def test_total_tokens_accumulated(self):
        responses = [
            LLMResponse("a", "x", 10, 100.0, 0.001, 0.5),
            LLMResponse("b", "y", 20, 200.0, 0.001, 0.5),
            LLMResponse("c", "z", 30, 300.0, 0.001, 0.5),
        ]
        result = merge_responses(responses, MergeStrategy.CONCAT)
        assert result.total_tokens == 60

    def test_total_cost_accumulated(self):
        responses = [
            LLMResponse("a", "x", 10, 100.0, 0.001, 0.5),
            LLMResponse("b", "y", 20, 200.0, 0.002, 0.5),
            LLMResponse("c", "z", 30, 300.0, 0.003, 0.5),
        ]
        result = merge_responses(responses, MergeStrategy.CONCAT)
        assert abs(result.total_cost_usd - 0.006) < 1e-9


# ============ FallbackChain ============
class TestFallbackChain:
    def test_init(self):
        chain = FallbackChain(["a", "b", "c"])
        assert chain.providers == ["a", "b", "c"]

    def test_init_empty(self):
        chain = FallbackChain()
        assert chain.providers == []

    def test_add_fallback(self):
        chain = FallbackChain()
        chain.add_fallback("primary", priority=0)
        chain.add_fallback("secondary", priority=1)
        assert chain.providers == ["primary", "secondary"]

    def test_priority_ordering(self):
        chain = FallbackChain()
        chain.add_fallback("low_prio", priority=10)
        chain.add_fallback("high_prio", priority=1)
        chain.add_fallback("mid_prio", priority=5)
        assert chain.providers == ["high_prio", "mid_prio", "low_prio"]

    def test_execute_success(self):
        chain = FallbackChain(["a", "b"])
        calls = []
        def call_fn(provider):
            calls.append(provider)
            return LLMResponse(provider, "ok", 10, 50.0, 0.001, 0.9)
        result = chain.execute(call_fn)
        # 第一个 provider 成功,不调用第二个
        assert result.source == "a"
        assert result.text == "ok"
        assert calls == ["a"]

    def test_execute_fallback_success(self):
        chain = FallbackChain(["a", "b", "c"])
        calls = []
        def call_fn(provider):
            calls.append(provider)
            if provider == "a":
                raise RuntimeError("a down")
            return LLMResponse(provider, f"from-{provider}", 10, 50.0, 0.001, 0.8)
        result = chain.execute(call_fn)
        # a 失败,b 成功
        assert result.source == "b"
        assert result.text == "from-b"
        assert calls == ["a", "b"]

    def test_execute_all_failed_raises(self):
        chain = FallbackChain(["a", "b"])
        def call_fn(provider):
            raise ValueError(f"{provider} failed")
        with pytest.raises(AllProvidersFailedError) as exc_info:
            chain.execute(call_fn)
        err = exc_info.value
        assert "a" in err.providers
        assert "b" in err.providers
        assert len(err.errors) == 2
        assert all(isinstance(e, ValueError) for e in err.errors)

    def test_execute_empty_chain_raises(self):
        chain = FallbackChain()
        with pytest.raises(AllProvidersFailedError):
            chain.execute(lambda p: LLMResponse(p, "x", 1, 1.0, 0.0, 0.5))

    def test_execute_priority_routing(self):
        chain = FallbackChain()
        chain.add_fallback("cheapest", priority=2)
        chain.add_fallback("best", priority=0)
        chain.add_fallback("middle", priority=1)
        calls = []
        def call_fn(provider):
            calls.append(provider)
            if provider == "best":
                raise RuntimeError("best down")
            return LLMResponse(provider, "ok", 10, 50.0, 0.001, 0.8)
        result = chain.execute(call_fn)
        # best 失败 → middle 成功
        assert result.source == "middle"
        assert calls == ["best", "middle"]


# ============ AllProvidersFailedError ============
class TestAllProvidersFailedError:
    def test_is_exception(self):
        err = AllProvidersFailedError(["x"])
        assert isinstance(err, Exception)

    def test_message_contains_providers(self):
        err = AllProvidersFailedError(["p1", "p2"])
        assert "p1" in str(err)
        assert "p2" in str(err)

    def test_with_errors_includes_detail(self):
        e1 = ValueError("v1")
        e2 = RuntimeError("r2")
        err = AllProvidersFailedError(["p1", "p2"], [e1, e2])
        msg = str(err)
        assert "ValueError" in msg
        assert "RuntimeError" in msg
        assert "v1" in msg
        assert "r2" in msg


# ============ JSON 序列化 ============
class TestJSON:
    def test_merged_result_to_json(self):
        r = MergedResult(
            text="hi",
            sources=["a", "b"],
            strategy=MergeStrategy.CONCAT,
            total_tokens=20,
            total_cost_usd=0.01,
            confidence=0.8,
        )
        j = result_to_json(r)
        data = json.loads(j)
        assert data["text"] == "hi"
        assert data["sources"] == ["a", "b"]
        assert data["strategy"] == "CONCAT"
        assert data["total_tokens"] == 20
        assert data["total_cost_usd"] == 0.01
        assert data["confidence"] == 0.8

    def test_response_to_json(self):
        r = LLMResponse("gpt-4o", "hello", 10, 100.0, 0.001, 0.9)
        j = response_to_json(r)
        data = json.loads(j)
        assert data["source"] == "gpt-4o"
        assert data["text"] == "hello"
        assert data["tokens"] == 10
        assert data["confidence"] == 0.9

    def test_json_roundtrip_chinese(self):
        r = MergedResult(
            text="你好世界",
            sources=["模型A", "模型B"],
            strategy=MergeStrategy.WEIGHTED,
            total_tokens=100,
            total_cost_usd=0.5,
            confidence=0.95,
        )
        j = result_to_json(r)
        data = json.loads(j)
        assert data["text"] == "你好世界"
        assert data["sources"] == ["模型A", "模型B"]
        assert data["strategy"] == "WEIGHTED"
