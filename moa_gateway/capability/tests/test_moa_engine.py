"""moa_engine tests — M-01 基础引擎 + M-05 3 proposer + 1 aggregator 协同"""
import sys
import asyncio
import time
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from moa_gateway.capability.moa_engine import (
    Proposer,
    Aggregator,
    ProposerResult,
    MoAResult,
    MoAEngineError,
    ProposerCallError,
    call_proposer,
    call_aggregator,
    run_moa,
    validate_moa,
    default_three_proposers,
    default_aggregator,
)


# ============ 测试夹具 / 工具 ============

def _sync_provider_factory(model_text: str, model_tokens: int):
    """造 1 个 sync provider_fn: 返回固定 (text, tokens)"""
    def _fn(actor, prompt):
        return model_text, model_tokens
    return _fn


def _async_slow_provider(delay_ms: float, text: str = "ok", tokens: int = 10):
    """造 1 个 sleep 的 sync provider — 用来验证并行确实并行"""
    def _fn(actor, prompt):
        time.sleep(delay_ms / 1000.0)
        return text, tokens
    return _fn


def _counting_provider(call_log: list, default_text: str = "ok", default_tokens: int = 10):
    """provider 会把每次调用记到 call_log, 用来验证调用次数 / 顺序"""
    counter = {"n": 0}
    def _fn(actor, prompt):
        counter["n"] += 1
        call_log.append({
            "n": counter["n"],
            "model_id": actor.model_id,
            "prompt_len": len(prompt),
        })
        return f"{default_text}#{counter['n']} from {actor.model_id}", default_tokens
    return _fn


def _echo_provider():
    """provider 把 prompt 中的 user 段回声, 用来验证 prompt 拼装"""
    def _fn(actor, prompt):
        # 从 [USER] 段后开始取
        if "[USER]" in prompt:
            text = prompt.split("[USER]", 1)[1].strip()
        else:
            text = prompt
        # tokens = prompt 字符数 // 4
        return f"echo:{text}", max(1, len(prompt) // 4)
    return _fn


# ============ Proposer / Aggregator dataclass ============

class TestDataclasses:
    def test_proposer_defaults(self):
        p = Proposer(model_id="m1")
        assert p.model_id == "m1"
        assert p.system_prompt == ""
        assert p.temperature == 0.7  # 默认

    def test_aggregator_defaults(self):
        a = Aggregator(model_id="a1")
        assert a.model_id == "a1"
        assert a.synthesis_prompt == ""

    def test_proposer_result_construction(self):
        r = ProposerResult(model_id="m1", text="hi", latency_ms=12.5, tokens=5)
        assert r.model_id == "m1"
        assert r.text == "hi"
        assert r.latency_ms == 12.5
        assert r.tokens == 5

    def test_default_three_proposers_count(self):
        ps = default_three_proposers()
        assert len(ps) == 3
        assert all(isinstance(p, Proposer) for p in ps)
        # 3 个 model_id 各不相同
        ids = {p.model_id for p in ps}
        assert len(ids) == 3

    def test_default_aggregator_model(self):
        a = default_aggregator()
        assert a.model_id != ""
        assert a.synthesis_prompt != ""


# ============ validate_moa ============

class TestValidateMoA:
    def test_0_proposer_error(self):
        errs = validate_moa([], default_aggregator())
        assert len(errs) >= 1
        assert any("proposer" in e.lower() for e in errs)

    def test_1_proposer_1_agg_ok(self):
        ps = [Proposer(model_id="m1", system_prompt="x")]
        errs = validate_moa(ps, default_aggregator())
        assert errs == []

    def test_3_proposer_1_agg_ok(self):
        ps = default_three_proposers()
        errs = validate_moa(ps, default_aggregator())
        assert errs == []

    def test_missing_aggregator_error(self):
        ps = [Proposer(model_id="m1")]
        errs = validate_moa(ps, None)
        assert len(errs) >= 1
        assert any("aggregator" in e.lower() for e in errs)

    def test_bad_temperature_error(self):
        ps = [Proposer(model_id="m1", temperature=2.5)]
        errs = validate_moa(ps, default_aggregator())
        assert len(errs) >= 1
        assert any("temperature" in e.lower() for e in errs)


# ============ call_proposer 单个 ============

class TestCallProposer:
    def test_call_proposer_single(self):
        p = Proposer(model_id="m1", system_prompt="be brief")
        fn = _sync_provider_factory("answer1", 7)
        result = asyncio.run(call_proposer(p, "Q?", fn))
        assert result.model_id == "m1"
        assert result.text == "answer1"
        assert result.tokens == 7
        assert result.latency_ms >= 0.0

    def test_call_proposer_latency_positive(self):
        p = Proposer(model_id="m1")
        fn = _async_slow_provider(20, "x", 3)
        result = asyncio.run(call_proposer(p, "Q", fn))
        assert result.latency_ms >= 15.0  # sleep 20ms

    def test_call_proposer_error_propagates(self):
        p = Proposer(model_id="m1")
        def bad_fn(actor, prompt):
            raise RuntimeError("provider down")
        with pytest.raises(ProposerCallError):
            asyncio.run(call_proposer(p, "Q", bad_fn))


# ============ call_proposer 并行 ============

class TestParallelCallProposer:
    def test_parallel_speedup(self):
        """3 个 proposer 各 sleep 50ms — 并行应 ≈ 50ms 而非 150ms"""
        ps = default_three_proposers()
        fn = _async_slow_provider(50, "p", 5)

        async def _go():
            t0 = time.perf_counter()
            results = await asyncio.gather(*[call_proposer(p, "Q", fn) for p in ps])
            return results, (time.perf_counter() - t0) * 1000.0

        results, elapsed_ms = asyncio.run(_go())
        assert len(results) == 3
        # 并行应 < 120ms (50ms × 3 sequential would be ≥ 150ms)
        assert elapsed_ms < 120.0, f"expected parallel < 120ms, got {elapsed_ms:.1f}"

    def test_parallel_all_invoked(self):
        ps = default_three_proposers()
        log = []
        fn = _counting_provider(log)

        async def _go():
            return await asyncio.gather(*[call_proposer(p, "Q", fn) for p in ps])

        results = asyncio.run(_go())
        assert len(results) == 3
        assert len(log) == 3
        # 3 个 model_id 都被调
        invoked = {e["model_id"] for e in log}
        assert invoked == {p.model_id for p in ps}


# ============ call_aggregator ============

class TestCallAggregator:
    def test_call_aggregator_basic(self):
        agg = default_aggregator()
        proposals = [
            ProposerResult("a", "ans A", 10.0, 5),
            ProposerResult("b", "ans B", 12.0, 7),
        ]
        text, tokens, lat = asyncio.run(call_aggregator(agg, "Q", proposals, _echo_provider()))
        assert "echo:" in text
        assert tokens > 0
        assert lat >= 0.0

    def test_call_aggregator_empty_proposals(self):
        agg = default_aggregator()
        text, tokens, lat = asyncio.run(call_aggregator(agg, "Q", [], _echo_provider()))
        assert text == ""
        assert tokens == 0
        assert lat == 0.0

    def test_call_aggregator_prompt_contains_all_proposals(self):
        agg = default_aggregator()
        proposals = [
            ProposerResult("m1", "UNIQUE_ALPHA_TEXT", 1.0, 1),
            ProposerResult("m2", "UNIQUE_BETA_TEXT", 1.0, 1),
            ProposerResult("m3", "UNIQUE_GAMMA_TEXT", 1.0, 1),
        ]
        captured = {}
        def capture_fn(actor, prompt):
            captured["prompt"] = prompt
            return "ok", 10
        asyncio.run(call_aggregator(agg, "ORIG_QUERY", proposals, capture_fn))
        p = captured["prompt"]
        assert "UNIQUE_ALPHA_TEXT" in p
        assert "UNIQUE_BETA_TEXT" in p
        assert "UNIQUE_GAMMA_TEXT" in p
        assert "ORIG_QUERY" in p


# ============ run_moa 完整 pipeline ============

class TestRunMoA:
    def test_run_moa_full_pipeline_3plus1(self):
        ps = default_three_proposers()
        agg = default_aggregator()
        log = []
        fn = _counting_provider(log, default_text="proposal", default_tokens=10)
        result = asyncio.run(run_moa("test query", ps, agg, fn))
        assert isinstance(result, MoAResult)
        assert result.query == "test query"
        assert len(result.proposals) == 3
        # aggregator 被调 1 次
        assert len(log) == 3 + 1
        # 最后 1 次是 aggregator
        assert log[-1]["model_id"] == agg.model_id

    def test_run_moa_total_tokens_sum(self):
        """total_tokens = sum(proposals.tokens) + aggregator.tokens"""
        ps = default_three_proposers()
        agg = default_aggregator()

        def var_fn(actor, prompt):
            if actor.model_id == agg.model_id:
                return ("agg_text", 100)
            # proposers: m1=10, m2=20, m3=30
            mapping = {"proposer-A": 10, "proposer-B": 20, "proposer-C": 30}
            return ("p", mapping.get(actor.model_id, 5))
        result = asyncio.run(run_moa("Q", ps, agg, var_fn))
        expected = 10 + 20 + 30 + 100
        assert result.total_tokens == expected

    def test_run_moa_total_latency_max_plus_agg(self):
        """total_latency_ms = max(proposer latencies) + aggregator latency"""
        ps = [
            Proposer(model_id="slow", system_prompt=""),  # sleep 长
            Proposer(model_id="fast", system_prompt=""),  # sleep 短
        ]
        agg = default_aggregator()
        # "slow" sleep 60ms, "fast" sleep 5ms, aggregator 20ms
        def fn(actor, prompt):
            if actor.model_id == "slow":
                time.sleep(0.060)
                return "x", 5
            if actor.model_id == "fast":
                time.sleep(0.005)
                return "x", 5
            # aggregator
            time.sleep(0.020)
            return "agg", 5
        t0 = time.perf_counter()
        result = asyncio.run(run_moa("Q", ps, agg, fn))
        wall_ms = (time.perf_counter() - t0) * 1000.0
        # 期望: max(60, 5) + 20 = 80ms 左右; 整体 wall < 110ms (因为 proposers 并行)
        assert result.total_latency_ms >= 75.0
        assert wall_ms < 110.0, f"parallel should be < 110ms, got {wall_ms:.1f}"

    def test_run_moa_invalid_config_raises(self):
        """0 proposer → MoAEngineError"""
        with pytest.raises(MoAEngineError):
            asyncio.run(run_moa("Q", [], default_aggregator(), _echo_provider()))

    def test_run_moa_missing_aggregator_raises(self):
        ps = [Proposer(model_id="m1")]
        with pytest.raises(MoAEngineError):
            asyncio.run(run_moa("Q", ps, None, _echo_provider()))

    def test_run_moa_boundary_empty_query(self):
        """空 query 也能跑通 (但结果 query 字段为空)"""
        ps = [Proposer(model_id="m1", system_prompt="s")]
        result = asyncio.run(run_moa("", ps, default_aggregator(), _echo_provider()))
        assert result.query == ""
        assert len(result.proposals) == 1
        assert result.aggregated != ""


# ============ MoAResult JSON 序列化 ============

class TestMoAResultJSON:
    def test_moa_result_to_json(self):
        r = MoAResult(
            query="Q",
            proposals=[ProposerResult("m1", "ans", 10.0, 5)],
            aggregated="final",
            total_tokens=15,
            total_latency_ms=20.0,
        )
        j = r.to_json()
        assert "Q" in j
        assert "final" in j
        assert "15" in j
        assert "20" in j

    def test_moa_result_from_json(self):
        r = MoAResult(
            query="Q2",
            proposals=[ProposerResult("m1", "ans2", 12.0, 8)],
            aggregated="final2",
            total_tokens=20,
            total_latency_ms=30.0,
        )
        j = r.to_json()
        r2 = MoAResult.from_json(j)
        assert r2.query == "Q2"
        assert r2.aggregated == "final2"
        assert r2.total_tokens == 20
        assert r2.total_latency_ms == 30.0
        assert len(r2.proposals) == 1
        assert r2.proposals[0].model_id == "m1"
        assert r2.proposals[0].tokens == 8

    def test_moa_result_to_dict(self):
        r = MoAResult(
            query="Q",
            proposals=[],
            aggregated="x",
            total_tokens=0,
            total_latency_ms=0.0,
        )
        d = r.to_dict()
        assert d["query"] == "Q"
        assert d["proposals"] == []
        assert d["aggregated"] == "x"
        assert d["total_tokens"] == 0
        assert d["total_latency_ms"] == 0.0


# ============ provider_fn 注入 / 默认 temperature ============

class TestProviderInjection:
    def test_provider_fn_called_with_proposer(self):
        """provider 收到 actor.model_id == proposer.model_id"""
        p = Proposer(model_id="INJECTED_MODEL", system_prompt="x")
        captured = {}
        def fn(actor, prompt):
            captured["model_id"] = actor.model_id
            return "ok", 1
        asyncio.run(call_proposer(p, "Q", fn))
        assert captured["model_id"] == "INJECTED_MODEL"

    def test_default_temperature_in_proposer(self):
        """Proposer() 不传 temperature → 默认 0.7"""
        p = Proposer(model_id="x")
        assert p.temperature == 0.7

    def test_custom_temperature_passes_through(self):
        """自定义 temperature 保留"""
        p = Proposer(model_id="x", temperature=0.3)
        assert p.temperature == 0.3
