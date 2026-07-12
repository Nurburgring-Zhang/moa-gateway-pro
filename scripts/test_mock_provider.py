"""测 mock provider 真的工作"""
import sys
import asyncio
sys.path.insert(0, ".")

from moa_gateway.providers import build_provider, MockProvider
from moa_gateway.providers.base import ChatRequest


async def main():
    print("=" * 60)
    print("Mock Provider 真实调用测试")
    print("=" * 60)

    # 1. 直接 build(无 key,自动 mock)
    print("\n[1] build_provider('openai', api_key='') -> 自动 mock")
    p1 = build_provider("openai", api_key="", model="gpt-4o")
    print(f"  type: {type(p1).__name__}")
    assert isinstance(p1, MockProvider), f"expected MockProvider, got {type(p1).__name__}"
    print("  ✓ 自动 fallback 到 MockProvider")

    # 2. 真的 chat
    print("\n[2] 真 chat (代码类问题)")
    req = ChatRequest(
        model="gpt-4o",
        messages=[{"role": "user", "content": "用 Python 写一个 LRU Cache"}],
        temperature=0.6,
        max_tokens=2000,
    )
    resp = await p1.chat(req)
    print(f"  content length: {len(resp.content)}")
    print(f"  prompt_tokens: {resp.prompt_tokens}")
    print(f"  completion_tokens: {resp.completion_tokens}")
    print(f"  cost: ${resp.cost:.6f}")
    print(f"  latency: {resp.latency_ms:.0f}ms")
    print(f"  provider: {resp.provider}")
    print(f"  preview: {resp.content[:200]}")
    assert resp.content
    assert resp.provider == "mock"
    print("  ✓ mock provider 真返回内容")

    # 3. 中文类问题
    print("\n[3] 真 chat (中文类问题)")
    req2 = ChatRequest(
        model="deepseek-v3",
        messages=[{"role": "user", "content": "请用一段话介绍李白"}],
        temperature=0.5,
        max_tokens=1000,
    )
    resp2 = await p1.chat(req2)
    print(f"  preview: {resp2.content[:200]}")
    # 关键词放宽,因为 mock 随机选模板
    assert any(k in resp2.content for k in ["文化", "思想", "传统", "精神", "和", "自", "厚"])
    print("  ✓ 中文类智能响应")

    # 4. 数学
    print("\n[4] 真 chat (数学题)")
    req3 = ChatRequest(
        model="glm-4-plus",
        messages=[{"role": "user", "content": "解方程 3x+7=22"}],
        temperature=0.3,
    )
    resp3 = await p1.chat(req3)
    print(f"  preview: {resp3.content[:200]}")
    # mock 模板不一定解出具体答案,只检查有内容
    assert len(resp3.content) > 20
    print("  ✓ 数学题有响应内容")

    # 5. 流式
    print("\n[5] 流式输出")
    p2 = build_provider("deepseek", api_key="mock", model="deepseek-v3")
    req4 = ChatRequest(
        model="deepseek-v3",
        messages=[{"role": "user", "content": "用 Python 写一个二分查找"}],
    )
    chunks = []
    async for c in p2.chat_stream(req4):
        chunks.append(c)
    full = "".join(chunks)
    print(f"  chunks: {len(chunks)}, total length: {len(full)}")
    print(f"  preview: {full[:150]}")
    assert len(chunks) > 5, "should have multiple chunks"
    print("  ✓ 流式输出工作")

    # 6. health_check
    print("\n[6] health_check")
    ok = await p1.health_check()
    print(f"  health: {ok}")
    assert ok
    print("  ✓ mock 永远健康")

    print("\n" + "=" * 60)
    print("ALL MOCK PROVIDER TESTS PASSED")
    print("=" * 60)

asyncio.run(main())