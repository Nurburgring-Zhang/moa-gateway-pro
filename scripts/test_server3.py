"""直接 import server_runner 测"""
import sys
sys.path.insert(0, ".")
import time
import os
import subprocess
from pathlib import Path
from moa_gateway.ui.server_runner import ServerRunner, PROJECT_ROOT

print(f"PROJECT_ROOT: {PROJECT_ROOT}")
print(f"PROJECT_ROOT exists: {PROJECT_ROOT.exists()}")

# 直接用 subprocess 复现 Popen 行为,跟 server_runner 完全一致
port = 8820
env = os.environ.copy()
root = str(PROJECT_ROOT)
if root not in env.get("PYTHONPATH", ""):
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")

cmd = [
    sys.executable,
    "-m", "uvicorn",
    "moa_gateway.server:app",
    "--host", "127.0.0.1",
    "--port", str(port),
    "--log-level", "warning",
    "--no-access-log",
]
print(f"\ncmd: {cmd}")
print(f"cwd: {root}")
print(f"PYTHONPATH: {env.get('PYTHONPATH', '')}")

t0 = time.time()
proc = subprocess.Popen(
    cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    cwd=root,
    env=env,
    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
)
print(f"started pid={proc.pid}, elapsed={time.time()-t0:.2f}s")

# 试 health 5s
for i in range(50):
    time.sleep(0.1)
    if proc.poll() is not None:
        out, _ = proc.communicate(timeout=2)
        print(f"exited code={proc.returncode}")
        print(f"output: {out.decode('utf-8', errors='ignore')[:1000]}")
        sys.exit(1)
    try:
        import urllib.request
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
        print(f"[{time.time()-t0:.1f}s] /health {r.status}")
        proc.terminate()
        break
    except Exception as e:
        if i % 10 == 0:
            print(f"[{time.time()-t0:.1f}s] waiting...")
print("done")