"""直接测 4 mock 并行"""
import asyncio
from moa_gateway.providers import build_provider
from moa_gateway.providers.base import ChatRequest


async def main():
    providers = [
        build_provider("deepseek", api_key="", model="deepseek-v3"),
        build_provider("zhipu", api_key="", model="glm-4-plus"),
        build_provider("moonshot", api_key="", model="moonshot-v1-8k"),
        build_provider("qwen", api_key="", model="qwen-plus"),
    ]
    print(f"All MockProvider: {all(type(p).__name__ == 'MockProvider' for p in providers)}")

    import time
    t0 = time.time()
    req = ChatRequest(
        model="deepseek-v3",
        messages=[{"role": "user", "content": "用 Python 写一个 LRU Cache"}],
    )
    results = await asyncio.gather(*[p.chat(req) for p in providers], return_exceptions=True)
    elapsed = time.time() - t0
    print(f"\nParallel 4 calls: {elapsed:.2f}s")
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            print(f"  [{i}] EXCEPTION: {type(r).__name__}: {r}")
        else:
            print(f"  [{i}] len={len(r.content)}, cost=${r.cost:.4f}, latency={r.latency_ms:.0f}ms")

asyncio.run(main())