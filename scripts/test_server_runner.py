"""测试 server_runner subprocess 模式"""
import sys
import time
sys.path.insert(0, ".")

from moa_gateway.ui.server_runner import ServerRunner

print("=" * 60)
print("测试 ServerRunner (subprocess 模式)")
print("=" * 60)

sr = ServerRunner()
print(f"初始: is_running={sr.is_running}, port={sr.port}")

# 启动
print("\n[1] start()")
t0 = time.time()
ok, msg = sr.start()
elapsed = time.time() - t0
print(f"  耗时: {elapsed:.2f}s")
print(f"  ok={ok}, msg={msg}")
print(f"  is_running={sr.is_running}, port={sr.port}")
print(f"  pid={sr._process.pid if sr._process else None}")

if ok:
    # 测 /health
    print("\n[2] GET /health")
    import urllib.request
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{sr.port}/health", timeout=2)
        print(f"  status={r.status}, body={r.read()[:100]}")
    except Exception as e:
        print(f"  ERR: {e}")

    # 测 /v1/models
    print("\n[3] GET /v1/models")
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{sr.port}/v1/models", timeout=5)
        body = r.read()[:200]
        print(f"  status={r.status}, body={body}")
    except Exception as e:
        print(f"  ERR: {e}")

    # 停止
    print("\n[4] stop()")
    sr.stop()
    print(f"  is_running={sr.is_running}")

# 再启动一次
print("\n[5] 再 start()")
ok, msg = sr.start()
print(f"  ok={ok}, msg={msg}")
if ok:
    import urllib.request
    r = urllib.request.urlopen(f"http://127.0.0.1:{sr.port}/health", timeout=2)
    print(f"  /health status={r.status}")
    sr.stop()

print("\n[6] DONE")