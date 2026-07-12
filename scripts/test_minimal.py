"""最简 ServerRunner 复现 — 不读 stdout/stderr"""
import sys
sys.path.insert(0, ".")
import time
import os
import socket
import subprocess
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(r"D:\MoA Gateway Pro")
port = 8840

cmd = [
    sys.executable, "-m", "uvicorn",
    "moa_gateway.server:app",
    "--host", "127.0.0.1", "--port", str(port),
    "--log-level", "warning", "--no-access-log",
]
env = os.environ.copy()
root = str(PROJECT_ROOT)
env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")

print(f"[{time.time():.2f}] Popen start")
t0 = time.time()
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    cwd=root,
    env=env,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
)
print(f"[{time.time():.2f}] Popen done, pid={proc.pid}, elapsed={time.time()-t0:.3f}s")

# 等
for i in range(50):
    time.sleep(0.2)
    # 检查进程
    rc = proc.poll()
    if rc is not None:
        print(f"[{time.time():.2f}] process exited code={rc}")
        sys.exit(1)
    # 测 socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            print(f"[{time.time():.2f}] socket connect OK after {(time.time()-t0):.2f}s")
            break
    except Exception as e:
        if i % 5 == 0:
            print(f"[{time.time():.2f}] waiting (i={i}, err={type(e).__name__})")
else:
    print(f"[{time.time():.2f}] TIMEOUT after 10s")
    proc.terminate()
    proc.wait(timeout=3)
    sys.exit(1)

# 测试 http
import urllib.request
r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2)
print(f"[{time.time():.2f}] /health: {r.status}, body={r.read()[:80]}")
r2 = urllib.request.urlopen(f"http://127.0.0.1:{port}/v1/models", timeout=5)
print(f"[{time.time():.2f}] /v1/models: {r2.status}, body[:200]={r2.read()[:200]}")

proc.terminate()
proc.wait(timeout=3)
print("DONE")