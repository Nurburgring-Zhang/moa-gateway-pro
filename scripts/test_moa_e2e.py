"""用 chinese_battalion 实际流程测"""
import asyncio
import sys
sys.path.insert(0, ".")
import time
from moa_gateway.config import get_settings
from moa_gateway.model_pool import get_model_pool
from moa_gateway.moa import get_moa


async def main():
    print("Initializing...")
    pool = get_model_pool()
    await pool.start()
    moa = get_moa()

    # 模拟 chinese_battalion preset
    query = "用 Python 写一个 LRU Cache"

    print(f"\nTesting chinese_battalion preset (query='{query}')")
    t0 = time.time()
    res = await moa.execute(query=query, preset="chinese_battalion")
    elapsed = time.time() - t0
    print(f"  strategy: {res.strategy}")
    print(f"  references: {len(res.references)} ({sum(1 for r in res.references if r.success)} ok)")
    print(f"  final_content length: {len(res.final_content or '')}")
    print(f"  total_cost: ${res.total_cost:.4f}")
    print(f"  elapsed: {elapsed:.2f}s")
    print(f"\nReferences:")
    for r in res.references:
        ok = "✓" if r.success else "✗"
        err = f"  err: {r.error[:60]}" if r.error else ""
        print(f"  {ok} {r.model_id:20s} {r.content[:80] if r.content else ''}{err}")
    if res.final_content:
        print(f"\nFinal answer (first 400):\n  {res.final_content[:400]}")

    await pool.stop()


asyncio.run(main())