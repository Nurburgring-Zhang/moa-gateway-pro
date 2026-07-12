"""最简 Popen 复现,排查"""
import sys
import time
import os
import socket
import subprocess
from pathlib import Path

ROOT = Path(r"D:\MoA Gateway Pro")
port = 8830

cmd = [
    sys.executable, "-m", "uvicorn",
    "moa_gateway.server:app",
    "--host", "127.0.0.1", "--port", str(port),
    "--log-level", "warning", "--no-access-log",
]
env = os.environ.copy()
env["PYTHONPATH"] = str(ROOT) + os.pathsep + "."

print(f"Popen...")
t0 = time.time()
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    cwd=str(ROOT),
    env=env,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
)
print(f"started pid={proc.pid}, elapsed={time.time()-t0:.3f}s")

for i in range(100):
    time.sleep(0.2)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            print(f"[{time.time()-t0:.2f}s] socket connect OK")
            break
    except Exception:
        if i % 5 == 0:
            print(f"[{time.time()-t0:.2f}s] waiting...")
    if proc.poll() is not None:
        print(f"process exited code={proc.returncode}")
        break

print(f"final: is alive = {proc.poll() is None}")
proc.terminate()
proc.wait(timeout=5)
print("done")