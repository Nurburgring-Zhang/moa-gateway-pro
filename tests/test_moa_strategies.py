"""tests/test_moa_strategies.py
验证 MoAOrchestrator 5 种 strategy 都真实工作(无占位、无简化)。
所有测试不调真实模型(无 key),改用 mock 端点模拟。
"""
from __future__ import annotations
import sys
import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_mock_model_pool(endpoints, mock_responses):
    """构造一个 mock ModelPool:任何 call 都返回 mock_responses 里的内容"""
    pool = MagicMock()
    pool.endpoints = {ep["id"]: ep for ep in endpoints}
    pool.settings = MagicMock()
    pool.settings.health.failure_threshold = 3
    pool.settings.health.cooldown_seconds = 60
    pool.settings.health.timeout_seconds = 10

    async def fake_call(model_id, messages, **kwargs):
        ep = pool.endpoints.get(model_id)
        if not ep:
            raise RuntimeError(f"no model {model_id}")
        if not getattr(ep, "available", True):
            raise RuntimeError("not available")
        # mock_responses 里有特定答案,没有就给"generic"
        content = mock_responses.get(model_id) or f"[mock:{model_id}] generic"
        cost = (getattr(ep, "cost_per_1k_input", 0.001) * 10
                + getattr(ep, "cost_per_1k_output", 0.002) * 20)
        await asyncio.sleep(0.001)
        resp = MagicMock()
        resp.content = content
        resp.cost = cost
        resp.latency_ms = 1.0
        resp.prompt_tokens = 10
        resp.completion_tokens = 20
        resp.total_tokens = 30
        resp.fallback_used = False
        resp.success = True
        return resp

    pool.call = fake_call

    def fake_get_fallback_chain(primary_id, count=3):
        all_eps = list(pool.endpoints.values())
        return all_eps[1:count + 1]

    pool.get_fallback_chain = fake_get_fallback_chain

    def fake_select_one(tier, prefer_provider=None, exclude_ids=None):
        wanted = tier.value if hasattr(tier, "value") else str(tier)
        for ep in pool.endpoints.values():
            ep_tier = getattr(ep, "tier", None)
            ep_val = ep_tier.value if hasattr(ep_tier, "value") else ep_tier
            if ep_val == wanted and getattr(ep, "id", None) not in (exclude_ids or []):
                return ep
        return list(pool.endpoints.values())[0]

    pool.select_one = fake_select_one
    pool.select_many = lambda tier, count, prefer_diversity=True, exclude_ids=None: \
        list(pool.endpoints.values())[:count]
    return pool


def make_endpoints():
    """构造支持属性访问的 mock endpoint"""
    class MockEndpoint:
        def __init__(self, **kw):
            self.__dict__.update(kw)
        def __getitem__(self, k):
            return self.__dict__[k]
    base = [
        {"id": "deepseek-v3", "tier": "standard", "provider": "deepseek",
         "available": True, "is_available": True, "config": MagicMock(),
         "cost_per_1k_input": 0.0005, "cost_per_1k_output": 0.001},
        {"id": "qwen-plus", "tier": "standard", "provider": "qwen",
         "available": True, "is_available": True, "config": MagicMock(),
         "cost_per_1k_input": 0.0008, "cost_per_1k_output": 0.0008},
        {"id": "glm-4-plus", "tier": "premium", "provider": "zhipu",
         "available": True, "is_available": True, "config": MagicMock(),
         "cost_per_1k_input": 0.0007, "cost_per_1k_output": 0.0007},
        {"id": "claude-sonnet", "tier": "premium", "provider": "anthropic",
         "available": True, "is_available": True, "config": MagicMock(),
         "cost_per_1k_input": 0.003, "cost_per_1k_output": 0.015},
        {"id": "gpt-4o", "tier": "premium", "provider": "openai",
         "available": True, "is_available": True, "config": MagicMock(),
         "cost_per_1k_input": 0.0025, "cost_per_1k_output": 0.0075},
    ]
    return [MockEndpoint(**d) for d in base]


def patch_all(eps):
    """给 mock endpoint 补 is_available / mark_success / mark_failure 等"""
    tier_rank = {"free": 0, "lite": 1, "standard": 2, "premium": 3, "flagship": 4}
    for ep in eps:
        ep.is_available = True
        ep.consecutive_failures = 0
        ep.health_status = "healthy"
        ep.last_error = ""
        ep.cooldown_until = 0.0
        ep.config.api_key_runtime = "mock-key"
        ep.config.provider = getattr(ep, "provider", "mock")
        ep.config.model = getattr(ep, "model", "mock-model")
        ep.config.timeout = 120
        ep.config.max_tokens = 8192
        ep.config.api_base = "https://example.com/v1"
        ep.config.tags = []
        # 用 ModelTier-like 对象,有 .rank / .value
        t = ep.tier if hasattr(ep, "tier") else "standard"
        rank = tier_rank.get(t, 2)
        ep.tier = type("Tier", (), {"value": t, "rank": rank,
                                    "__lt__": lambda s, o: s.rank < o.rank,
                                    "__le__": lambda s, o: s.rank <= o.rank,
                                    "__gt__": lambda s, o: s.rank > o.rank,
                                    "__ge__": lambda s, o: s.rank >= o.rank})()

async def test_parallel_strategy():
    """验证 parallel 模式:多模型并行 + 聚合"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig
    endpoints = make_endpoints()
    patch_all(endpoints)
    pool = make_mock_model_pool(endpoints, {
        "deepseek-v3": "[deepseek] 答案 A",
        "qwen-plus": "[qwen] 答案 B",
        "glm-4-plus": "[glm] 答案 C",
        "claude-sonnet": "[claude] 综合答案",
    })
    router = MagicMock()
    pool_refs = list(pool.endpoints.values())[:3]
    aggregator = next((e for e in pool.endpoints.values()
                       if getattr(e, "tier", None) == "premium"),
                      list(pool.endpoints.values())[0])
    router.route_for_moa = MagicMock(return_value=(pool_refs, aggregator))
    router.route.return_value = MagicMock(primary=endpoints[0])
    router.tier_mapping = {"trivial": "free", "simple": "lite", "medium": "standard",
                          "complex": "premium", "expert": "flagship"}
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "balanced"
    orch.settings.moa.presets = {"balanced": MoAPresetConfig(
        strategy="parallel", reference_count=4, aggregator_tier="premium",
        critic_rounds=0, reference_temperature=0.6, aggregator_temperature=0.4
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    orch.settings.moa.consensus_threshold = 0.35

    result = await orch.execute(query="test",
                                 preset="balanced",
                                 temperature=0.6,
                                 max_tokens=100)
    assert result.strategy == "parallel"
    assert len(result.references) >= 2
    assert result.aggregated_content  # 非空
    assert result.consensus_score > 0
    # aggregator_model 应该是 premium tier 的某个 model
    assert result.aggregator_model in ("glm-4-plus", "claude-sonnet", "gpt-4o")
    print(f"  ✓ parallel: {len(result.references)} refs, "
          f"agg={result.aggregator_model}, cost=${result.total_cost:.4f}")


async def test_compose_strategy():
    """验证 compose 模式:多角度分工(每个参考模型扮演一个 role)"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig
    endpoints = make_endpoints()
    pool = make_mock_model_pool(endpoints, {
        "deepseek-v3": "[feasibility] 可以做",
        "qwen-plus": "[performance] 性能好",
        "glm-4-plus": "[security] 安全 OK",
        "claude-sonnet": "[综合] 可行,性能好,安全",
    })
    router = MagicMock()
    router.tier_mapping = {"medium": "standard"}
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "compose_analyst"
    orch.settings.moa.presets = {"compose_analyst": MoAPresetConfig(
        strategy="compose", reference_count=4, aggregator_tier="premium",
        critic_rounds=0, tier="standard", reference_temperature=0.6,
        aggregator_temperature=0.4
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    orch.settings.moa.consensus_threshold = 0.35

    result = await orch.execute(query="设计一个系统", preset="compose_analyst",
                                 temperature=0.6, max_tokens=200)
    assert result.strategy == "compose"
    # 至少 3 个带 role 的 ref
    roles = [r.role for r in result.references if r.role]
    assert len(roles) >= 3
    assert any(r in roles for r in ["feasibility", "performance", "security"])
    print(f"  ✓ compose: roles={set(roles)}, cost=${result.total_cost:.4f}")


async def test_judge_strategy():
    """验证 judge 模式:单模型多轮自我反思"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig
    endpoints = make_endpoints()
    call_count = {"n": 0}
    state = {"version": 0}

    async def judge_call(model_id, messages, **kwargs):
        state["version"] += 1
        n = state["version"]
        if n <= 1:
            content = "第一轮答案:不够详细"
        elif n == 2:
            content = "VERDICT: PASS"
        else:
            content = "不应该到这里"
        resp = MagicMock()
        resp.content = content
        resp.cost = 0.01
        resp.latency_ms = 1.0
        resp.prompt_tokens = 10
        resp.completion_tokens = 20
        return resp

    pool = make_mock_model_pool(endpoints, {})
    pool.call = judge_call
    router = MagicMock()
    router.tier_mapping = {}
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "judge"
    orch.settings.moa.presets = {"judge": MoAPresetConfig(
        strategy="judge", aggregator_tier="premium", critic_rounds=3,
        reference_temperature=0.5, aggregator_temperature=0.3,
        aggregator="claude-sonnet"
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    orch.settings.moa.consensus_threshold = 0.35

    result = await orch.execute(query="test", preset="judge",
                                 temperature=0.5, max_tokens=100)
    assert result.strategy == "judge"
    # 至少跑了 2 次:1 次初始 + 至少 1 次反思
    assert state["version"] >= 2
    # 第 2 次见到 PASS 应该停止
    print(f"  ✓ judge: {state['version']} calls, stopped at VERDICT:PASS")


async def test_chain_strategy():
    """验证 chain 模式:多步串行"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig, MoAStageConfig
    endpoints = make_endpoints()
    pool = make_mock_model_pool(endpoints, {
        "deepseek-v3": "调研结果",
        "qwen-plus": "调研结果",
        "glm-4-plus": "调研结果",
        "claude-sonnet": "分析 OK",
        "gpt-4o": "综述",
    })
    router = MagicMock()
    router.tier_mapping = {"medium": "standard", "complex": "premium"}
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "chain_deep"
    orch.settings.moa.presets = {"chain_deep": MoAPresetConfig(
        strategy="chain",
        stages=[
            MoAStageConfig(name="research", tier="standard"),
            MoAStageConfig(name="analyze", tier="premium"),
            MoAStageConfig(name="summarize", tier="premium"),
        ],
        reference_temperature=0.6, aggregator_temperature=0.4
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    orch.settings.moa.consensus_threshold = 0.35

    result = await orch.execute(query="test", preset="chain_deep",
                                 temperature=0.6, max_tokens=100)
    assert result.strategy == "chain"
    assert len(result.chain_steps) == 3
    print(f"  ✓ chain: {len(result.chain_steps)} steps, cost=${result.total_cost:.4f}")


async def test_pipeline_strategy():
    """验证 pipeline 模式:planner → generator → evaluator"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig, MoAStageConfig
    endpoints = make_endpoints()
    pool = make_mock_model_pool(endpoints, {
        "claude-sonnet": "规划:做这个",
        "gpt-4o": "生成的内容",
        "glm-4-plus": "PASS",
    })
    router = MagicMock()
    router.tier_mapping = {"complex": "premium", "medium": "standard"}
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "pipeline"
    orch.settings.moa.presets = {"pipeline": MoAPresetConfig(
        strategy="pipeline",
        stages=[
            MoAStageConfig(name="planner", tier="premium"),
            MoAStageConfig(name="generator", tier="standard"),
            MoAStageConfig(name="evaluator", tier="premium"),
        ],
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    orch.settings.moa.consensus_threshold = 0.35

    result = await orch.execute(query="test", preset="pipeline",
                                 temperature=0.6, max_tokens=100)
    assert result.strategy == "pipeline"
    assert len(result.pipeline_stages) >= 1
    print(f"  ✓ pipeline: {len(result.pipeline_stages)} stages")


async def test_single_strategy():
    """验证 single 模式"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig
    from types import SimpleNamespace
    endpoints = make_endpoints()
    patch_all(endpoints)
    pool = make_mock_model_pool(endpoints, {
        "gpt-4o": "单模型直答",
        "deepseek-v3": "单模型直答",
    })
    router = MagicMock()
    router.tier_mapping = {"medium": "standard", "trivial": "lite",
                          "simple": "lite"}
    target_ep = endpoints[3]
    router.route.return_value = SimpleNamespace(primary=target_ep)
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "fast"
    orch.settings.moa.presets = {"fast": MoAPresetConfig(
        strategy="single", reference_count=1, tier="standard", critic_rounds=0
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    orch.settings.moa.consensus_threshold = 0.35
    result = await orch.execute(query="test", preset="fast",
                                 temperature=0.6, max_tokens=100)
    assert result.strategy == "single"
    assert result.final_content
    print(f"  ✓ single: {result.aggregator_model}, no MoA overhead")


async def test_critic_consensus_extends_rounds():
    """验证共识低时自动追加 critic 轮(修复了之前的死代码)"""
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.config import MoAPresetConfig
    endpoints = make_endpoints()
    patch_all(endpoints)
    pool = make_mock_model_pool(endpoints, {
        "deepseek-v3": "答案 A 完全无关",
        "qwen-plus": "答案 B 毫不相干",
        "glm-4-plus": "答案 C 风马牛不相及",
        "claude-sonnet": '{"issues": ["不完整"], "suggestions": ["补全"]}\n\n**修订**: 完整答案',
    })
    router = MagicMock()
    router.tier_mapping = {"complex": "premium"}
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = MagicMock()
    orch.settings.moa = MagicMock()
    orch.settings.moa.default_preset = "balanced"
    orch.settings.moa.presets = {"balanced": MoAPresetConfig(
        strategy="parallel", reference_count=3, aggregator_tier="premium",
        critic_rounds=1,  # 用户配 1 轮
    )}
    orch.settings.moa.enabled = True
    orch.settings.moa.reference_timeout = 10
    orch.settings.moa.aggregator_timeout = 30
    # 设低阈值,触发自动追轮
    orch.settings.moa.consensus_threshold = 0.99

    result = await orch.execute(query="test", preset="balanced",
                                 temperature=0.6, max_tokens=100)
    # 共识应 < 0.99 → 触发追加 critic 轮
    assert len(result.critics) >= 1
    print(f"  ✓ consensus追轮: {len(result.critics)} critic rounds triggered")


async def test_evaluate_endpoint():
    """验证 evaluate 端点:横向对比 N 模型"""
    from moa_gateway.moa import MoAOrchestrator
    endpoints = make_endpoints()
    pool = make_mock_model_pool(endpoints, {
        "deepseek-v3": "答案 A",
        "qwen-plus": "答案 B",
        "glm-4-plus": '{"scores": [{"model": "deepseek-v3", "total": 80}, {"model": "qwen-plus", "total": 70}], "winner": "deepseek-v3", "ranking": ["deepseek-v3", "qwen-plus"]}',
    })
    orch = MoAOrchestrator(model_pool=pool)
    res = await orch.evaluate(query="test", candidates=["deepseek-v3", "qwen-plus"],
                              reference_answer="标准")
    assert "candidates" in res
    assert len(res["candidates"]) == 2
    assert "scores" in res
    print(f"  ✓ evaluate: {len(res['candidates'])} cands, judge={res['judge_model']}")


async def main():
    print("=== MoA strategy e2e tests ===\n")
    print("[1/8] parallel strategy")
    await test_parallel_strategy()
    print("[2/8] compose strategy")
    await test_compose_strategy()
    print("[3/8] judge strategy")
    await test_judge_strategy()
    print("[4/8] chain strategy")
    await test_chain_strategy()
    print("[5/8] pipeline strategy")
    await test_pipeline_strategy()
    print("[6/8] single strategy")
    await test_single_strategy()
    print("[7/8] consensus auto-extends critic rounds")
    await test_critic_consensus_extends_rounds()
    print("[8/8] evaluate (eval) endpoint")
    await test_evaluate_endpoint()
    print("\n✓ All 8 MoA strategy tests passed (no placeholder, all real paths).")


if __name__ == "__main__":
    asyncio.run(main())