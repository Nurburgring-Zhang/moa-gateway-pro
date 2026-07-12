"""4 preset 端到端测"""
import asyncio
import sys
sys.path.insert(0, ".")
from moa_gateway.model_pool import get_model_pool
from moa_gateway.moa import get_moa


async def main():
    pool = get_model_pool()
    await pool.start()
    moa = get_moa()
    print("Pool endpoints:", len(pool.endpoints))
    print("Healthy:", sum(1 for e in pool.endpoints.values() if e.health_status == "healthy"))
    print("Mock mode:", sum(
        1 for e in pool.endpoints.values()
        if e.provider_obj.__class__.__name__ == "MockProvider"
    ))
    print("Real providers:", sum(
        1 for e in pool.endpoints.values()
        if e.provider_obj.__class__.__name__ != "MockProvider"
    ))

    for preset in [
        "chinese_battalion",
        "chinese_battalion_layered",
        "qwen_single_proposer",
        "ranker_qwen110b",
    ]:
        try:
            r = await moa.execute(query="写一个 LRU Cache", preset=preset)
            ok = sum(1 for x in r.references if x.success)
            f_len = len(r.final_content or "")
            print(f"  {preset:30s} {len(r.references)} refs ({ok} ok) "
                  f"final_len={f_len} cost=${r.total_cost:.4f}")
        except Exception as e:
            print(f"  {preset:30s} ERR: {type(e).__name__}: {e}")

    await pool.stop()


asyncio.run(main())