"""测 call_async 是否能跨线程调 async 函数"""
import asyncio
import sys
import time
sys.path.insert(0, ".")

from moa_gateway.ui.server_runner import ServerRunner


def test_call_async():
    sr = ServerRunner()

    async def add(a, b):
        await asyncio.sleep(0.1)
        return a + b

    t0 = time.time()
    r = sr.call_async(add(2, 3), timeout=5)
    elapsed = time.time() - t0
    print(f"  add(2,3) = {r}, took {elapsed:.2f}s")
    assert r == 5, f"expected 5, got {r}"

    async def fail():
        await asyncio.sleep(0.05)
        raise ValueError("test error")

    r = sr.call_async(fail(), timeout=5)
    print(f"  fail() = {r} (should be None)")
    assert r is None

    async def slow():
        await asyncio.sleep(5)

    t0 = time.time()
    r = sr.call_async(slow(), timeout=0.5)
    elapsed = time.time() - t0
    print(f"  slow(5s, timeout=0.5) = {r} (should be None), took {elapsed:.2f}s")
    assert r is None

    print("All call_async tests passed.")


if __name__ == "__main__":
    test_call_async()