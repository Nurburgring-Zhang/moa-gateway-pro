"""直接测 _is_responsive"""
import sys
sys.path.insert(0, ".")
import time
import socket
import subprocess
import os
import logging
import urllib.request
from pathlib import Path

logging.basicConfig(level=logging.INFO)

PROJECT_ROOT = Path(r"D:\MoA Gateway Pro")
port = 8850

# 启动 server (跟 ServerRunner 一样)
cmd = [
    sys.executable, "-m", "uvicorn",
    "moa_gateway.server:app",
    "--host", "127.0.0.1", "--port", str(port),
    "--log-level", "warning", "--no-access-log",
]
env = os.environ.copy()
env["PYTHONPATH"] = str(PROJECT_ROOT) + os.pathsep + "."

print(f"Popen...")
proc = subprocess.Popen(
    cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    cwd=str(PROJECT_ROOT), env=env,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
)
print(f"pid={proc.pid}")

# 等 socket
for i in range(20):
    time.sleep(0.2)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
        print(f"[{time.time():.2f}] socket OK")
        break
    except Exception:
        pass

# 现在测 _is_responsive 逻辑
def is_responsive(port):
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            s.connect(("127.0.0.1", port))
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as r:
            return r.status_code == 200
    except Exception as e:
        print(f"  exception: {type(e).__name__}: {e}")
        return False

print(f"\nTesting _is_responsive() at port={port}:")
for i in range(3):
    t0 = time.time()
    r = is_responsive(port)
    print(f"  [{i}] result={r}, elapsed={time.time()-t0:.3f}s")

proc.terminate()
proc.wait(timeout=3)
print("done")