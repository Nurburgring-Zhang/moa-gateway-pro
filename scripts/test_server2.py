"""用 Python subprocess 而不是 PowerShell 测 server_runner 启动"""
import sys
sys.path.insert(0, ".")
import time
import subprocess
from moa_gateway.ui.server_runner import ServerRunner

print("Test: ServerRunner.start()")
sr = ServerRunner()
t0 = time.time()
ok, msg = sr.start()
print(f"  elapsed: {time.time()-t0:.2f}s, ok={ok}, msg={msg}")
if ok:
    import urllib.request
    r = urllib.request.urlopen(f"http://127.0.0.1:{sr.port}/health", timeout=2)
    print(f"  /health: {r.status}")
    r2 = urllib.request.urlopen(f"http://127.0.0.1:{sr.port}/v1/models", timeout=5)
    body = r2.read()[:200]
    print(f"  /v1/models: {r2.status}, body[:200]: {body}")
    sr.stop()
    print(f"  stopped")
else:
    print(f"  last_error: {sr.last_error}")
    # 读进程残留输出
    if sr._process and sr._process.stdout:
        try:
            sr._process.stdout.close()
        except Exception:
            pass