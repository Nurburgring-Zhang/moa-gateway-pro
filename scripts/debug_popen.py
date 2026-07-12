"""完全手动复现 server_runner 的 Popen 启动,看 stdout"""
import sys
import os
sys.path.insert(0, ".")
import subprocess
import time

# 跟 server_runner 一模一样
root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
env = os.environ.copy()
if root not in env.get("PYTHONPATH", ""):
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")

port = 8810
host = "127.0.0.1"
cmd = [
    sys.executable,
    "-c",
    f"import sys, uvicorn; "
    f"from moa_gateway.server import app; "
    f"print('[server] starting on {host}:{port}', flush=True); "
    f"uvicorn.run(app, host='{host}', port={port}, log_level='warning', access_log=False)",
]
print("cmd:", cmd)
print("cwd:", root)
print("env PYTHONPATH:", env.get("PYTHONPATH", ""))

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

# 等启动
for i in range(60):
    time.sleep(0.5)
    if proc.poll() is not None:
        print(f"[{time.time()-t0:.1f}s] process exited code={proc.returncode}")
        # 读输出
        try:
            out, _ = proc.communicate(timeout=2)
            print(f"output:\n{out.decode('utf-8', errors='ignore')[:1000]}")
        except Exception as e:
            print(f"communicate err: {e}")
        break
    # 试 health
    try:
        import urllib.request
        r = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
        print(f"[{time.time()-t0:.1f}s] /health {r.status}")
        break
    except Exception as e:
        if i % 4 == 0:
            print(f"[{time.time()-t0:.1f}s] waiting... ({e})")

# 读部分输出
try:
    proc.terminate()
    out, _ = proc.communicate(timeout=3)
    print(f"--- output ({len(out)} bytes) ---")
    print(out.decode("utf-8", errors="ignore")[:2000])
except Exception as e:
    print(f"terminate err: {e}")
    proc.kill()