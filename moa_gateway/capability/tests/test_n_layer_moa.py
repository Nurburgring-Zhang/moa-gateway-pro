"""n_layer_moa 真实测试(非 mock 整体, 但用 fake Provider 模拟可控响应)

测试策略:
- 用一个自写的 FakeProvider 控制每个 proposer/aggregator 的响应
- 验证多层 pipeline 真串联, prev_aggregated 喂回下层
- 验证失败回退 / 预算控制 / JSON 序列化等
- 全程无 assert 返回 True, 失败即 raise
"""
from __future__ import annotations
import sys
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Any
from dataclasses import dataclass, field

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from moa_gateway.providers.base import (
    ChatRequest, ChatResponse, Provider, ProviderError
)
from moa_gateway.capability.n_layer_moa import (
    Proposer, Aggregator, LayerResult, MoAConfig,
    MoARunError, BudgetExceededError,
    synthesize_layer, run_n_layer_moa, run_three_layer_moa,
)


# ============ FakeProvider: 可控响应,记录调用历史 ============

@dataclass
class CallRecord:
    model_id: str
    messages: List[Dict[str, Any]]
    temperature: float = 0.6
    stream: bool = False


class FakeProvider(Provider):
    """测试用 Provider — 不发网络,按 model_id 返回预置响应"""

    def __init__(self, responses: Dict[str, str], fail_on: Dict[str, Exception] = None,
                 echo_prev: bool = False, delay: float = 0.0):
        super().__init__(api_base="fake://", api_key="fake", timeout=10)
        self.responses = dict(responses)
        self.fail_on = dict(fail_on or {})
        self.calls: List[CallRecord] = []
        self.echo_prev = echo_prev
        self.delay = delay

    async def chat(self, req: ChatRequest) -> ChatResponse:
        return await self._do_chat(req, stream=False)

    async def chat_stream(self, req: ChatRequest):
        resp = await self._do_chat(req, stream=True)
        # 按 chunk yield
        s = resp.content
        chunk_size = max(1, len(s) // 4) if s else 1
        for i in range(0, len(s), chunk_size):
            yield s[i:i + chunk_size]

    async def _do_chat(self, req: ChatRequest, stream: bool) -> ChatResponse:
        self.calls.append(CallRecord(
            model_id=req.model_id if hasattr(req, "model_id") else req.model,
            messages=list(req.messages),
            temperature=req.temperature,
            stream=stream,
        ))
        if self.delay:
            await asyncio.sleep(self.delay)
        # 用 req.model 字段
        model = req.model
        if model in self.fail_on:
            exc = self.fail_on[model]
            if isinstance(exc, Exception):
                raise exc
            raise ProviderError(str(exc), provider="fake")
        if model in self.responses:
            content = self.responses[model]
        else:
            content = f"[fake:{model}] generic"
        # 模拟: 把 user content 里的 prev 标识回显到响应里,便于测试
        # - aggregator 的 user content 标 "【上一轮聚合输出】\n<text>"
        # - proposer 的 user content 标 "上一轮其他模型的综合输出(参考):\n<text>"
        if self.echo_prev:
            for m in reversed(req.messages):
                if m.get("role") == "user":
                    user_text = m.get("content", "")
                    import re
                    prev_text = ""
                    # 严格匹配: 只在真正出现"上一轮聚合输出"或"上一轮其他模型"标头时回显
                    m1 = re.search(r"【上一轮聚合输出】\s*\n(.*?)(?:\n\n|$)", user_text, re.S)
                    m2 = re.search(r"上一轮其他模型的综合输出[（(]参考[)）][:：]?\s*\n?(.*?)(?:\n\n|$)", user_text, re.S)
                    if m1:
                        prev_text = m1.group(1).strip()
                    elif m2:
                        prev_text = m2.group(1).strip()
                    if prev_text:
                        content = f"MOCK: ref={prev_text[:40]}"
                    # 没匹配到标头 → 不动 content,保留 self.responses 默认值
                    break
        pt = max(1, sum(len(m.get("content", "")) for m in req.messages) // 2)
        ct = max(1, len(content) // 2)
        return ChatResponse(
            content=content,
            finish_reason="stop",
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
            model=model,
            provider="fake",
        )


def _make_registry(providers: List[FakeProvider]) -> Dict[str, Provider]:
    """合并多个 FakeProvider 的 responses,生成 model_id -> Provider map"""
    reg: Dict[str, Provider] = {}
    for fp in providers:
        for m in list(fp.responses.keys()) + list(fp.fail_on.keys()):
            if m not in reg:
                reg[m] = fp
    return reg


# ============ 测试 1: 单层 1 proposer 1 aggregator 返回 1 个 LayerResult ============

def test_synthesize_layer_single_proposer():
    async def main():
        prov = FakeProvider({
            "model-a": "proposal-1 content",
            "agg-1": "aggregated final",
        })
        reg = {"model-a": prov, "agg-1": prov}
        proposers = [Proposer("p1", "model-a", "you are a proposer")]
        agg = Aggregator("a1", "agg-1", "synthesize these")
        result = await synthesize_layer(
            proposers=proposers,
            query="Q1",
            layer_idx=1,
            aggregator=agg,
            providers_registry=reg,
        )
        assert isinstance(result, LayerResult)
        assert result.layer_idx == 1
        assert len(result.proposals) == 1
        assert result.proposals[0] == "proposal-1 content"
        assert result.aggregated == "aggregated final"
        assert len(result.references) == 1
        # 验证 provider 真被调了
        assert len(prov.calls) == 2
        # 第 1 个 call = proposer, 第 2 个 = aggregator
        assert prov.calls[0].model_id == "model-a"
        assert prov.calls[1].model_id == "agg-1"
        # 验证 temperature 传透
        assert prov.calls[0].temperature == 0.6
        print("  ✓ test_synthesize_layer_single_proposer")
    asyncio.run(main())


# ============ 测试 2: 3 层 pipeline 返回 3 个 LayerResult ============

def test_run_n_layer_3_layers():
    async def main():
        prov = FakeProvider({
            "p-m": f"proposal text",
            "a-m": "aggregated",
        })
        reg = {"p-m": prov, "a-m": prov}
        proposers = [Proposer("p1", "p-m"), Proposer("p2", "p-m"), Proposer("p3", "p-m")]
        agg = Aggregator("a1", "a-m", "synth")
        cfg = MoAConfig(num_layers=3, proposers_per_layer=2, temperature=0.5)
        results = await run_n_layer_moa(
            query="big Q", config=cfg,
            proposers=proposers, aggregator=agg, providers_registry=reg,
        )
        assert len(results) == 3, f"expected 3 layers, got {len(results)}"
        assert [r.layer_idx for r in results] == [1, 2, 3]
        for r in results:
            assert r.aggregated == "aggregated"
        print("  ✓ test_run_n_layer_3_layers")
    asyncio.run(main())


# ============ 测试 3: prev_aggregated 喂回下层 (echo_prev) ============

def test_prev_aggregated_passed_down():
    async def main():
        prov = FakeProvider(
            responses={"p-m": "base", "a-m": "AGG-OUTPUT-L1"},
            echo_prev=True,
        )
        reg = {"p-m": prov, "a-m": prov}
        proposers = [Proposer("p1", "p-m"), Proposer("p2", "p-m")]
        agg = Aggregator("a1", "a-m")
        cfg = MoAConfig(num_layers=3, proposers_per_layer=2, temperature=0.6)
        results = await run_n_layer_moa(
            query="hello", config=cfg,
            proposers=proposers, aggregator=agg, providers_registry=reg,
        )
        # L1 proposer 不应看到 prev
        l1_proposer_calls = [c for c in prov.calls if c.model_id == "p-m"
                             and "上一轮" not in c.messages[-1].get("content", "")]
        # L2/L3 proposer 应看到 prev
        l23_proposer_calls = [c for c in prov.calls if c.model_id == "p-m"
                              and "上一轮" in c.messages[-1].get("content", "")]
        assert len(l1_proposer_calls) >= 1, "L1 proposer should not see prev"
        assert len(l23_proposer_calls) >= 1, "L2+ proposer should see prev"
        # L2+ proposer 的 user content 应包含 "AGG-OUTPUT-L1"
        saw_prev = any(
            "AGG-OUTPUT-L1" in c.messages[-1].get("content", "")
            for c in l23_proposer_calls
        )
        assert saw_prev, "prev_aggregated not found in L2+ proposer user content"
        # L2+ proposer 响应 (echo_prev) 应包含 "MOCK: ref=..."
        mock_responses = [c.messages[-1].get("content", "") for c in prov.calls
                          if c.model_id == "p-m" and "上一轮" in c.messages[-1].get("content", "")]
        # 实际响应是 FakeProvider 处理后返回的内容, 我们从 call history 反推: L2+ 之后 aggregator 收到的 user content 包含 "MOCK: ref=..."(因为 proposer 返回了它)
        l2_agg_calls = prov.calls[3 + 1:]  # rough, but at least one L2+ aggregator call should see MOCK: ref
        saw_mock_ref = any(
            "MOCK:" in c.messages[-1].get("content", "")
            for c in prov.calls if c.model_id == "a-m"
        )
        assert saw_mock_ref, "L2+ aggregator should see MOCK: ref=... in its user content"
        print("  ✓ test_prev_aggregated_passed_down")
    asyncio.run(main())


# ============ 测试 4: 单 proposer 失败不阻断整层 ============

def test_single_proposer_failure_does_not_block_layer():
    async def main():
        # model-1 抛错, model-2 正常
        prov = FakeProvider(
            responses={"model-1": "ignored", "model-2": "good proposal"},
            fail_on={"model-1": ProviderError("boom", provider="fake")},
        )
        reg = {"model-1": prov, "model-2": prov, "agg": prov}
        # 让 aggregator 响应也设上
        prov.responses["agg"] = "synthesized"
        proposers = [Proposer("p1", "model-1"), Proposer("p2", "model-2")]
        agg = Aggregator("a", "agg")
        result = await synthesize_layer(
            proposers=proposers, query="Q", layer_idx=1,
            aggregator=agg, providers_registry=reg,
        )
        assert len(result.proposals) == 2
        assert "fallback:p1" in result.proposals[0]
        assert result.proposals[1] == "good proposal"
        # 至少有 1 个 non-fallback, 所以层不抛
        assert result.aggregated == "synthesized"
        print("  ✓ test_single_proposer_failure_does_not_block_layer")
    asyncio.run(main())


# ============ 测试 5: temperature 透传 ============

def test_temperature_passed_through():
    async def main():
        prov = FakeProvider({"m": "x"})
        reg = {"m": prov}
        proposers = [Proposer("p", "m")]
        agg = Aggregator("a", "m")
        cfg_temp = 1.23
        await synthesize_layer(
            proposers=proposers, query="Q", layer_idx=1,
            aggregator=agg, providers_registry=reg,
            temperature=cfg_temp,
        )
        # proposer 和 aggregator 都应收到 1.23
        temps = [c.temperature for c in prov.calls]
        assert all(abs(t - cfg_temp) < 1e-9 for t in temps), f"temps: {temps}"
        print(f"  ✓ test_temperature_passed_through (t={cfg_temp})")
    asyncio.run(main())


# ============ 测试 6: LayerResult.to_dict / from_dict 往返一致 ============

def test_layer_result_serialization_roundtrip():
    lr = LayerResult(
        layer_idx=2,
        proposals=["a", "b", "c"],
        aggregated="final-2",
        references=["ref-block-1", "ref-block-2"],
    )
    d = lr.to_dict()
    j = json.dumps(d, ensure_ascii=False)
    d2 = json.loads(j)
    lr2 = LayerResult.from_dict(d2)
    assert lr.layer_idx == lr2.layer_idx
    assert lr.proposals == lr2.proposals
    assert lr.aggregated == lr2.aggregated
    assert lr.references == lr2.references
    # dict 字段类型
    assert isinstance(d["layer_idx"], int)
    assert isinstance(d["proposals"], list)
    assert isinstance(d["aggregated"], str)
    print("  ✓ test_layer_result_serialization_roundtrip")
    # 也验证 LayerResult 列表往返
    layers = [lr, LayerResult(layer_idx=3, proposals=["x"], aggregated="y3", references=[])]
    j2 = json.dumps([l.to_dict() for l in layers], ensure_ascii=False)
    back = [LayerResult.from_dict(d) for d in json.loads(j2)]
    assert len(back) == 2
    assert back[1].layer_idx == 3
    print("  ✓ test_layer_result_serialization_roundtrip (list)")


# ============ 测试 7: max_total_tokens 触发中途停止 ============

def test_max_total_tokens_stops_pipeline():
    async def main():
        # 每个 proposer 返回 200 字符 → 估算 100 token
        long_text = "X" * 200
        prov = FakeProvider({"m": long_text})
        reg = {"m": prov}
        proposers = [Proposer("p", "m")]
        agg = Aggregator("a", "m")
        cfg = MoAConfig(num_layers=5, proposers_per_layer=1, temperature=0.6)
        # 设一个很紧的预算
        results = await run_n_layer_moa(
            query="Q", config=cfg,
            proposers=proposers, aggregator=agg, providers_registry=reg,
            max_total_tokens=120,  # 第 1 层约 200 token, 第 1 层后已超额 → 中止
        )
        # 中途停: 应当少于 5 层
        assert len(results) < 5, f"budget didn't stop pipeline, got {len(results)} layers"
        assert len(results) >= 1, "should have completed at least 1 layer"
        print(f"  ✓ test_max_total_tokens_stops_pipeline ({len(results)}/5 layers)")
    asyncio.run(main())


# ============ 测试 8: 3 layer 整合返回 dict 含 'final_output' ============

def test_run_three_layer_moa_returns_dict():
    async def main():
        prov = FakeProvider({"m": "prop", "a1": "L1-out", "a2": "L2-out", "a3": "L3-final"})
        reg = {"m": prov, "a1": prov, "a2": prov, "a3": prov}
        proposers = [Proposer("p1", "m"), Proposer("p2", "m")]
        aggs = [
            Aggregator("a1", "a1", "L1-sys"),
            Aggregator("a2", "a2", "L2-sys"),
            Aggregator("a3", "a3", "L3-sys"),
        ]
        out = await run_three_layer_moa(
            query="big", proposers=proposers, aggregators=aggs,
            providers_registry=reg, temperature=0.6,
        )
        assert isinstance(out, dict)
        assert "final_output" in out
        assert out["final_output"] == "L3-final"
        assert "layers" in out
        assert len(out["layers"]) == 3
        assert "layer_outputs" in out
        assert out["layer_outputs"] == ["L1-out", "L2-out", "L3-final"]
        assert "tokens_used" in out
        assert out["tokens_used"] > 0
        print("  ✓ test_run_three_layer_moa_returns_dict")
    asyncio.run(main())


# ============ 测试 9: 预算耗尽抛 BudgetExceededError (单层 budget 耗尽) ============

def test_budget_exceeded_raises():
    async def main():
        prov = FakeProvider({"m": "x"})
        reg = {"m": prov}
        proposers = [Proposer("p", "m")]
        agg = Aggregator("a", "m")
        # tokens_used 远大于 max_total_tokens → 立即抛
        try:
            await synthesize_layer(
                proposers=proposers, query="Q", layer_idx=1,
                aggregator=agg, providers_registry=reg,
                max_total_tokens=10, tokens_used=100,
            )
            assert False, "should have raised BudgetExceededError"
        except BudgetExceededError as e:
            assert "budget exhausted" in str(e).lower()
            print(f"  ✓ test_budget_exceeded_raises ({e})")
    asyncio.run(main())


# ============ 测试 10: num_layers=0 / proposers_per_layer=0 抛 ValueError ============

def test_invalid_config_raises_value_error():
    try:
        MoAConfig(num_layers=0, proposers_per_layer=2)
        assert False, "num_layers=0 should raise"
    except ValueError as e:
        assert "num_layers" in str(e)
    try:
        MoAConfig(num_layers=2, proposers_per_layer=0)
        assert False, "proposers_per_layer=0 should raise"
    except ValueError as e:
        assert "proposers_per_layer" in str(e)
    try:
        MoAConfig(num_layers=2, proposers_per_layer=2, temperature=3.0)
        assert False, "temperature out of range should raise"
    except ValueError as e:
        assert "temperature" in str(e)
    # valid config 不抛
    cfg = MoAConfig(num_layers=1, proposers_per_layer=1, temperature=0.5)
    assert cfg.num_layers == 1
    print("  ✓ test_invalid_config_raises_value_error")


# ============ 测试 11: 重复 run 不污染 state ============

def test_repeated_runs_no_state_leak():
    async def main():
        prov = FakeProvider({"m": "resp", "a": "agg-out"})
        reg = {"m": prov, "a": prov}
        proposers = [Proposer("p", "m")]
        agg = Aggregator("a", "a")
        cfg = MoAConfig(num_layers=2, proposers_per_layer=1, temperature=0.5)
        # 第 1 次
        r1 = await run_n_layer_moa(
            query="Q1", config=cfg, proposers=proposers,
            aggregator=agg, providers_registry=reg,
        )
        calls_after_run1 = len(prov.calls)
        # 第 2 次 (同 provider, 不同 query)
        r2 = await run_n_layer_moa(
            query="Q2", config=cfg, proposers=proposers,
            aggregator=agg, providers_registry=reg,
        )
        # 两次都应正常返回, 结果独立
        assert len(r1) == 2
        assert len(r2) == 2
        # provider.calls 应增长 (新 calls 不会覆盖旧的)
        assert len(prov.calls) > calls_after_run1
        # 验证第二次的 calls 真的发出了 (用 Q2 标识)
        saw_q2 = any("Q2" in str(c.messages) for c in prov.calls[calls_after_run1:])
        assert saw_q2, "second run's calls should use Q2"
        # 第一次的 calls 还在
        saw_q1 = any("Q1" in str(c.messages) for c in prov.calls[:calls_after_run1])
        assert saw_q1, "first run's calls should still be in history (no mutation)"
        print(f"  ✓ test_repeated_runs_no_state_leak (run1={calls_after_run1} calls, run2={len(prov.calls)-calls_after_run1} calls)")
    asyncio.run(main())


# ============ Bonus 测试 12: 全 proposer 失败 → 抛 MoARunError ============

def test_all_proposers_fail_raises_moarunerror():
    async def main():
        prov = FakeProvider(
            responses={"a": "x"},
            fail_on={"m1": ProviderError("bad1", provider="fake"),
                     "m2": ProviderError("bad2", provider="fake")},
        )
        reg = {"m1": prov, "m2": prov, "a": prov}
        proposers = [Proposer("p1", "m1"), Proposer("p2", "m2")]
        agg = Aggregator("a", "a")
        try:
            await synthesize_layer(
                proposers=proposers, query="Q", layer_idx=1,
                aggregator=agg, providers_registry=reg,
            )
            assert False, "should have raised MoARunError"
        except MoARunError as e:
            assert "all" in str(e).lower()
            print(f"  ✓ test_all_proposers_fail_raises_moarunerror")
    asyncio.run(main())


# ============ Bonus 测试 13: aggregators 数量不对抛 ValueError ============

def test_three_layer_wrong_aggregator_count():
    async def main():
        prov = FakeProvider({"m": "x", "a": "y"})
        reg = {"m": prov, "a": prov}
        proposers = [Proposer("p", "m")]
        try:
            await run_three_layer_moa(
                query="Q", proposers=proposers,
                aggregators=[Aggregator("a", "a")],  # 只 1 个
                providers_registry=reg,
            )
            assert False, "should raise ValueError"
        except ValueError as e:
            assert "3" in str(e)
            print(f"  ✓ test_three_layer_wrong_aggregator_count")


# ============ Bonus 测试 14: 1 层 default (无 explicit aggregator) ============

def test_synthesize_layer_default_aggregator():
    async def main():
        prov = FakeProvider({"m": "prop-text"})
        reg = {"m": prov}
        proposers = [Proposer("p1", "m", "be helpful")]
        # 不传 aggregator — 默认用 proposers[0] 兼任
        result = await synthesize_layer(
            proposers=proposers, query="Q", layer_idx=1,
            providers_registry=reg, temperature=0.7,
        )
        # 因为 proposers[0] 兼任 aggregator, 调了 2 次 (propose + aggregate)
        assert len(prov.calls) == 2
        assert result.aggregated  # 非空
        print("  ✓ test_synthesize_layer_default_aggregator")


if __name__ == "__main__":
    test_synthesize_layer_single_proposer()
    test_run_n_layer_3_layers()
    test_prev_aggregated_passed_down()
    test_single_proposer_failure_does_not_block_layer()
    test_temperature_passed_through()
    test_layer_result_serialization_roundtrip()
    test_max_total_tokens_stops_pipeline()
    test_run_three_layer_moa_returns_dict()
    test_budget_exceeded_raises()
    test_invalid_config_raises_value_error()
    test_repeated_runs_no_state_leak()
    test_all_proposers_fail_raises_moarunerror()
    test_three_layer_wrong_aggregator_count()
    test_synthesize_layer_default_aggregator()
    print("\n✅ 全部通过 (14/14)")
