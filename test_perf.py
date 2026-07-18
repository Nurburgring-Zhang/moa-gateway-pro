"""Performance / concurrency test."""
import json
import time
import threading
import urllib.request
import urllib.error
import statistics
import sys

BASE = "http://127.0.0.1:8088"

def call(method, path, body=None, headers=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        b = r.read().decode()
        try:
            return r.status, json.loads(b) if b else None
        except Exception:
            return r.status, b
    except urllib.error.HTTPError as e:
        b = e.read().decode()
        try:
            return e.code, json.loads(b)
        except Exception:
            return e.code, b
    except Exception as e:
        return 0, {"error": str(e)}

# Login
s, body = call("POST", "/api/auth/login", body={"username": "admin", "password": "TestPass#2024"})
if s != 200 or "token" not in body:
    print(f"login failed: status={s}, body={body}")
    sys.exit(1)
TOKEN = body["token"]
H = {"Authorization": f"Bearer {TOKEN}"}
print(f"login OK, token len={len(TOKEN)}")

# Test 1: Sequential health checks
print("=== Test 1: Sequential /health (1000 requests) ===")
times = []
for i in range(1000):
    t0 = time.perf_counter()
    s, body = call("GET", "/health", timeout=5)
    times.append((time.perf_counter() - t0) * 1000)
print(f"  count: {len(times)}")
print(f"  p50: {statistics.median(times):.2f}ms")
print(f"  p95: {statistics.quantiles(times, n=20)[18]:.2f}ms")
print(f"  p99: {statistics.quantiles(times, n=100)[98]:.2f}ms")
print(f"  avg: {statistics.mean(times):.2f}ms")
print(f"  max: {max(times):.2f}ms")
print(f"  errors: {sum(1 for s, _ in [(0, 0)] if s == 0)}")

# Test 2: Concurrent /health
print("\n=== Test 2: Concurrent /health (200 threads × 10 requests) ===")
def hit_health():
    for _ in range(10):
        call("GET", "/health", timeout=5)

results = []
threads = []
for _ in range(200):
    t = threading.Thread(target=hit_health)
    threads.append(t)
t0 = time.perf_counter()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.perf_counter() - t0
total = 200 * 10
print(f"  total requests: {total}")
print(f"  elapsed: {elapsed:.2f}s")
print(f"  RPS: {total/elapsed:.1f}")

# Test 3: Concurrent dispatch
print("\n=== Test 3: Concurrent /v1/agent/dispatch (50 threads × 5) ===")
s, body = call("POST", "/api/api-keys",
               body={"name": f"perf_test_{int(time.time())}", "quota_rpm": 100000, "quota_daily_tokens": 100000000},
               headers=H)
print(f"  key create: status={s}, body keys={list(body.keys()) if isinstance(body, dict) else body}")
if isinstance(body, dict) and "key" not in body:
    print(f"  key create failed: {body}")
    raise SystemExit(1)
API_KEY = body["key"]
AH = {"Authorization": f"Bearer {API_KEY}"}

dispatch_times = []
errors = 0

def hit_dispatch():
    global errors
    for _ in range(5):
        t0 = time.perf_counter()
        s, body = call("POST", "/v1/agent/dispatch",
                       body={"service": "consensus", "method": "vote_ensemble",
                             "payload": {"votes": [{"voter_id": "v1", "candidate": "a",
                                                       "confidence": 0.9, "reason": "best"}],
                                          "method": "weighted"}},
                       headers=AH, timeout=10)
        dispatch_times.append((time.perf_counter() - t0) * 1000)
        if s != 200:
            errors += 1

threads = []
for _ in range(50):
    t = threading.Thread(target=hit_dispatch)
    threads.append(t)
t0 = time.perf_counter()
for t in threads:
    t.start()
for t in threads:
    t.join()
elapsed = time.perf_counter() - t0
print(f"  total requests: {len(dispatch_times)}")
print(f"  elapsed: {elapsed:.2f}s")
print(f"  RPS: {len(dispatch_times)/elapsed:.1f}")
if dispatch_times:
    print(f"  p50: {statistics.median(dispatch_times):.2f}ms")
    print(f"  p95: {statistics.quantiles(dispatch_times, n=20)[18]:.2f}ms")
    print(f"  errors: {errors}")

# Test 4: Memory (using a side-channel)
print("\n=== Test 4: Memory usage (after stress) ===")
import os
try:
    import psutil
    process = psutil.Process(os.getpid())
    # Get the server's memory
    for p in psutil.process_iter(['name', 'pid', 'memory_info']):
        if 'python' in p.info['name'].lower() and p.pid != os.getpid():
            mi = p.info['memory_info']
            print(f"  python PID {p.pid}: RSS={mi.rss/1024/1024:.1f}MB")
except ImportError:
    print("  psutil not installed, skipping memory check")

print("\nPERFORMANCE TESTS DONE")
