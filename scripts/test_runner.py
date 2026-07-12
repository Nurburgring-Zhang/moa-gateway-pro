"""直接测 ServerRunner"""
import sys
sys.path.insert(0, ".")
import time
from moa_gateway.ui.server_runner import ServerRunner

sr = ServerRunner()
print("start()...")
t0 = time.time()
ok, msg = sr.start()
print(f"  ok={ok}, msg={msg}, elapsed={time.time()-t0:.2f}s")
if ok:
    print(f"  port={sr.port}, is_running={sr.is_running}")
    sr.stop()
    print("  stopped")
else:
    print(f"  last_error: {sr.last_error}")
    if sr._process:
        sr._process.kill()