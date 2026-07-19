"""perf/bench.py — MoA Gateway Pro v1.8.1 性能压测 (httpx + connection pool)

httpx 内置 connection pool + retry,适合高并发压测
"""
import asyncio
import os
import sys
import time
import httpx

BASE = "http://127.0.0.1:8088"


def login_sync():
    with httpx.Client(timeout=5) as c:
        r = c.post(f"{BASE}/api/auth/login",
                    json={"username": "admin", "password": os.environ.get("MOA_ADMIN_PASSWORD", "TestPass#2024")})
        if r.status_code == 200:
            return r.json().get("token", "")
    return ""


def get_or_create_api_key(token):
    """从 list 拿 key,没就 create"""
    with httpx.Client(timeout=5) as c:
        r = c.get(f"{BASE}/api/api-keys", headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            d = r.json()
            items = d if isinstance(d, list) else d.get("items", [])
            if items and isinstance(items[0], dict):
                # list 只返回 key_id + key_hash,没有真 key
                # 真 key 只在 create 时返回一次
                pass
        # 创建新 key
        r2 = c.post(f"{BASE}/api/api-keys",
                     json={"name": "perf-bench", "quota_rpm": 100000, "quota_daily_tokens": 999999999},
                     headers={"Authorization": f"Bearer {token}"})
        if r2.status_code == 200:
            return r2.json().get("key", "")
    return ""


async def bench_async(path, total, concurrency, body=None, headers=None, label=""):
    """Async benchmark with httpx"""
    sem = asyncio.Semaphore(concurrency)
    results = []
    latencies = []
    error_count = 0
    error_lock = asyncio.Lock()

    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=concurrency + 20, max_keepalive_connections=concurrency + 20)) as c:
        async def one_req(_i):
            nonlocal error_count
            async with sem:
                t = time.time()
                try:
                    if body:
                        r = await c.post(f"{BASE}{path}", json=body, headers=headers or {})
                    else:
                        r = await c.get(f"{BASE}{path}", headers=headers or {})
                    lat = (time.time() - t) * 1000
                    latencies.append(lat)
                    results.append(r.status_code)
                except Exception as e:
                    error_count += 1
                    results.append(-1)

        t0 = time.time()
        await asyncio.gather(*[one_req(i) for i in range(total)])
        elapsed = time.time() - t0

    latencies.sort()
    n = len(latencies)
    p50 = latencies[n // 2] if n else 0
    p95 = latencies[int(n * 0.95)] if n else 0
    p99 = latencies[int(n * 0.99)] if n else 0
    ok = sum(1 for s in results if s == 200)
    rps = total / elapsed
    errs = total - ok
    print(f"  {label}: RPS={rps:.0f} | p50={p50:.1f}ms | p95={p95:.1f}ms | p99={p99:.1f}ms | errs={errs}/{total}")
    return {"label": label, "rps": rps, "p50": p50, "p95": p95, "p99": p99, "errs": errs, "total": total, "elapsed": elapsed}


def bench_sync(path, total, body=None, headers=None, label=""):
    """Sync bench (单线程顺序)"""
    latencies = []
    error_count = 0
    with httpx.Client(timeout=30) as c:
        t0 = time.time()
        for i in range(total):
            t = time.time()
            try:
                if body:
                    r = c.post(f"{BASE}{path}", json=body, headers=headers or {})
                else:
                    r = c.get(f"{BASE}{path}", headers=headers or {})
                if r.status_code != 200:
                    error_count += 1
            except Exception:
                error_count += 1
            latencies.append((time.time() - t) * 1000)
        elapsed = time.time() - t0
    latencies.sort()
    n = len(latencies)
    p50 = latencies[n // 2] if n else 0
    p95 = latencies[int(n * 0.95)] if n else 0
    p99 = latencies[int(n * 0.99)] if n else 0
    rps = total / elapsed
    print(f"  {label}: RPS={rps:.0f} | p50={p50:.1f}ms | p95={p95:.1f}ms | p99={p99:.1f}ms | errs={error_count}/{total}")
    return {"label": label, "rps": rps, "p50": p50, "p95": p95, "p99": p99, "errs": error_count, "total": total, "elapsed": elapsed}


def main():
    print("=" * 70)
    print(" MoA Gateway Pro v1.8.1 性能压测 (httpx + async pool)")
    print("=" * 70)

    # health check
    with httpx.Client(timeout=3) as c:
        try:
            r = c.get(f"{BASE}/health")
            if r.status_code != 200:
                print(f"server not responding: {r.status_code} {r.text[:100]}")
                sys.exit(1)
            print(f"server OK: {r.text[:100]}")
        except Exception as e:
            print(f"ERROR: {e}")
            sys.exit(1)

    token = login_sync()
    api_key = get_or_create_api_key(token)
    auth = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    print(f"api_key: {'set (' + api_key[:18] + '...)' if api_key else 'none (public only)'}")

    # 跑场景
    results = []
    print("\n[同步顺序 — baseline]")
    results.append(bench_sync("/health", 1000, label="sync_health_1000"))

    print("\n[异步并发 — httpx connection pool, 低并发避开 Windows 1000 端口池限制]")
    # Windows ephemeral port 1000 上限,TIME_WAIT 累积,高并发会 WinError 10048
    # 商用 Linux 上无此限制
    results.append(asyncio.run(bench_async("/health", 200, 20, label="async_health_200x20")))
    results.append(asyncio.run(bench_async("/health", 500, 30, label="async_health_500x30")))
    if auth:
        chat_body = {"model": "auto", "messages": [{"role": "user", "content": "Please analyze the impact of multi-model orchestration on AI system performance with 5 examples and trade-offs"}]}
        results.append(asyncio.run(bench_async("/v1/chat/completions", 100, 10,
                                              body=chat_body, headers=auth, label="async_chat_complex_100x10")))
    else:
        results.append(asyncio.run(bench_async("/v1/models", 200, 20, label="async_models_200x20")))

    print("\n" + "=" * 70)
    print(" 综合")
    print("=" * 70)
    for r in results:
        print(f"  {r['label']}: RPS={r['rps']:.0f} | p50={r['p50']:.1f}ms | p95={r['p95']:.1f}ms | p99={r['p99']:.1f}ms | errs={r['errs']}/{r['total']}")


if __name__ == "__main__":
    main()
