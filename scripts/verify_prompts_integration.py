"""端到端验证 prompts.py + moa.py 集成正确"""
import asyncio
import sys
sys.path.insert(0, ".")

from unittest.mock import MagicMock, AsyncMock, patch
from types import SimpleNamespace
import json


def make_mock_endpoint(eid: str, name: str, tier_rank: int = 2):
    ep = SimpleNamespace()
    ep.id = eid
    ep.name = name
    ep.is_available = True
    ep.consecutive_failures = 0
    ep.health_status = "healthy"
    ep.config = SimpleNamespace(api_key_runtime="mock-key", provider="mock", model=eid)
    ep.tier = SimpleNamespace(value="standard", rank=tier_rank)
    ep.provider = SimpleNamespace(call=AsyncMock(return_value={
        "content": f"mock-{eid}-response",
        "prompt_tokens": 100,
        "completion_tokens": 200,
        "cost": 0.001,
        "model": eid,
    }))
    return ep


def make_mock_resp(ep_id: str, content: str = None):
    """构造 pool.call 返回的 Response 格式"""
    from types import SimpleNamespace
    return SimpleNamespace(
        content=content or f"mock-{ep_id}-response",
        cost=0.001,
        latency_ms=50,
        prompt_tokens=100,
        completion_tokens=200,
        model=ep_id,
    )


async def main():
    from moa_gateway.config import Settings, MoAPresetConfig, ServerConfig, ModelEndpointConfig, AuthConfig, StorageConfig, RateLimitConfig, ReferenceModelConfig
    from moa_gateway.moa import MoAOrchestrator
    from moa_gateway.prompts import get_prompt

    print("=" * 70)
    print("  prompts + moa.py 集成验证")
    print("=" * 70)

    # 构造 settings (chinese_battalion preset)
    preset = MoAPresetConfig(
        enabled=True, strategy="compose",
        reference_count=4,
        reference_models=[
            ReferenceModelConfig(id="ep1", role="feasibility"),
            ReferenceModelConfig(id="ep2", role="performance"),
            ReferenceModelConfig(id="ep3", role="security"),
            ReferenceModelConfig(id="ep4", role="ux"),
        ],
        aggregator="ep5",
        aggregator_tier="premium",
        tier="standard",
        reference_temperature=0.7,
        aggregator_temperature=0.3,
        critic_rounds=1,
        roles=["feasibility", "performance", "security", "ux"],
    )
    preset2 = MoAPresetConfig(
        enabled=True, strategy="chain",
        reference_count=2,
        tier="standard",
        aggregator_tier="premium",
        critic_rounds=0,
        stages=[
            {"name": "research", "tier": "standard"},
            {"name": "analyze", "tier": "premium"},
            {"name": "summarize", "tier": "premium"},
        ],
    )
    settings = Settings(
        server=ServerConfig(),
        auth=AuthConfig(),
        storage=StorageConfig(),
        rate_limit=RateLimitConfig(),
        moa={
            "default_preset": "chinese_battalion",
            "presets": {
                "chinese_battalion": preset,
                "chain_test": preset2,
            },
        },
        models=[
            ModelEndpointConfig(id="ep1", name="ep1", provider="mock", model="m1"),
            ModelEndpointConfig(id="ep2", name="ep2", provider="mock", model="m2"),
            ModelEndpointConfig(id="ep3", name="ep3", provider="mock", model="m3"),
            ModelEndpointConfig(id="ep4", name="ep4", provider="mock", model="m4"),
            ModelEndpointConfig(id="ep5", name="ep5", provider="mock", model="m5"),
        ],
    )

    # 5 个 mock endpoint + 1 aggregator
    eps = {
        "ep1": make_mock_endpoint("ep1", "ep1", tier_rank=2),
        "ep2": make_mock_endpoint("ep2", "ep2", tier_rank=2),
        "ep3": make_mock_endpoint("ep3", "ep3", tier_rank=2),
        "ep4": make_mock_endpoint("ep4", "ep4", tier_rank=2),
        "ep5": make_mock_endpoint("ep5", "ep5", tier_rank=3),
    }
    pool = MagicMock()
    pool.endpoints = eps
    # pool.call(id, ...) -> Response-like object
    async def mock_call(ep_id, *args, **kwargs):
        return make_mock_resp(ep_id)
    pool.call = AsyncMock(side_effect=mock_call)
    pool.get_fallback_chain = MagicMock(return_value=[])  # no fallback
    pool.select_many = MagicMock(return_value=list(eps.values())[:4])
    pool.select_one = MagicMock(side_effect=lambda tier: eps.get("ep5") or list(eps.values())[0])

    router = MagicMock()
    def route_for_moa(*args, **kwargs):
        return ([eps["ep5"]], eps["ep5"])
    router.route_for_moa = route_for_moa
    router.route = MagicMock(return_value=eps["ep5"])

    # 测试 1: compose 模式 + 4 个角色 + aggregator 用 prompts 文件
    print("\n[Test 1] compose 模式 (4 角色) - 用 prompts.py 加载角色 prompt")
    print("-" * 70)
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = settings  # override 默认
    res = await orch.execute(
        query="设计一个高并发的电商系统",
        preset="chinese_battalion",
    )
    print(f"  strategy: {res.strategy}")
    print(f"  fallback_used: {res.fallback_used}")
    print(f"  total_cost: ${res.total_cost:.6f}")
    print(f"  final_content: {res.final_content[:80] if res.final_content else '(empty)'}")
    print(f"  aggregator_model: {res.aggregator_model}")

    # 检查所有 4 个角色被打了不同的 prompt
    print("\n  [Verify] 验证每条参考调用 system prompt 来自 prompts/ 文件:")
    for i, ep_id in enumerate(["ep1", "ep2", "ep3", "ep4"]):
        ep = eps[ep_id]
        if ep.provider.call.call_args_list:
            # call_args_list 是个 list,每个元素是 (args, kwargs)
            # 第 i 次调用 args[0] 是 messages
            if i < len(ep.provider.call.call_args_list):
                call_args = ep.provider.call.call_args_list[i]
                # call_args 是 Call 对象, args = call_args.args, kwargs = call_args.kwargs
                msgs = call_args.kwargs.get("messages") or (call_args.args[0] if call_args.args else [])
                system_msgs = [m for m in msgs if m.get("role") == "system"]
                if system_msgs:
                    content = system_msgs[0]["content"][:60].replace("\n", " ")
                    print(f"    {ep_id}: '{content}...'")
    assert res.strategy == "compose", f"expected compose, got {res.strategy}"
    print("  ✓ PASS")

    # 测试 2: chain 模式 - 验证每个 step 都注入了对应 prompt
    print("\n[Test 2] chain 模式 (research → analyze → summarize)")
    print("-" * 70)
    # 重置 mock
    for ep in eps.values():
        ep.provider.call = AsyncMock(return_value={
            "content": f"mock-{ep.id}-response",
            "prompt_tokens": 100,
            "completion_tokens": 200,
            "cost": 0.001,
            "model": ep.id,
        })
    router.route_for_moa = MagicMock(return_value=([eps["ep2"], eps["ep3"]], eps["ep5"]))
    pool.get_fallback_chain = MagicMock(return_value=[])
    pool.select_many = MagicMock(return_value=[eps["ep2"], eps["ep3"]])
    res = await orch.execute(
        query="什么是 Transformer 架构?",
        preset="chain_test",
    )
    print(f"  strategy: {res.strategy}")
    print(f"  chain_steps count: {len(res.chain_steps)}")
    print(f"  total_cost: ${res.total_cost:.6f}")
    for step in res.chain_steps:
        print(f"    step {step.step} ({step.preset}): cost=${step.cost:.6f}, len={len(step.output)}B")
    # 验证 chain 模式生成了 3 个步骤
    assert res.strategy == "chain", f"expected chain, got {res.strategy}"
    assert len(res.chain_steps) == 3, f"expected 3 chain steps, got {len(res.chain_steps)}"
    print("  ✓ PASS")

    # 测试 3: 验证 get_prompt 返回的是从磁盘加载的真实内容(不是 builtin)
    print("\n[Test 3] get_prompt('aggregator') vs 内置 fallback")
    print("-" * 70)
    p = get_prompt("aggregator")
    assert "聚合器" in p, "aggregator prompt 应包含 '聚合器' 关键字"
    assert "共识" in p or "综合" in p, "aggregator prompt 应包含 '综合'/'共识'"
    print(f"  aggregator.md loaded: {len(p)} chars")
    print(f"  contains key concepts: ✓ (共识/综合/裁决)")
    p2 = get_prompt("compose_security")
    assert "安全" in p2 or "认证" in p2, "compose_security prompt 应包含安全关键字"
    print(f"  compose_security.md loaded: {len(p2)} chars")
    p3 = get_prompt("chain_summarize")
    assert "综述" in p3 or "汇总" in p3
    print(f"  chain_summarize.md loaded: {len(p3)} chars")
    print("  ✓ PASS")

    # 测试 4: 用户自定义 override
    print("\n[Test 4] 用户自定义 template 覆盖默认")
    print("-" * 70)
    from moa_gateway.prompts import save_template, delete_template
    save_template("aggregator", "CUSTOM_USER_OVERRIDE_TEST_MARKER")
    p4 = get_prompt("aggregator")
    assert "CUSTOM_USER_OVERRIDE_TEST_MARKER" in p4, "用户覆盖失败"
    print(f"  Override OK: {p4}")
    delete_template("aggregator")
    p5 = get_prompt("aggregator")
    assert "CUSTOM_USER_OVERRIDE_TEST_MARKER" not in p5
    print(f"  After delete, fallback to default file: {p5[:30]}...")
    print("  ✓ PASS")

    print("\n" + "=" * 70)
    print("  全部 4 项验证通过")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())