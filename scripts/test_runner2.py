"""直接调 ServerRunner.start() 看具体哪里卡"""
import sys
sys.path.insert(0, ".")
import time
import socket
import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from moa_gateway.ui.server_runner import ServerRunner, PROJECT_ROOT
print(f"PROJECT_ROOT: {PROJECT_ROOT}")

sr = ServerRunner()
print(f"\nCalling start() at {time.time():.2f}")
t0 = time.time()
ok, msg = sr.start()
elapsed = time.time() - t0
print(f"\nAt {time.time():.2f} (after {elapsed:.2f}s):")
print(f"  ok={ok}, msg={msg}")
print(f"  is_running={sr.is_running}, port={sr.port}")
print(f"  last_error={sr.last_error}")
print(f"  process: {sr._process}")
if sr._process:
    print(f"  pid: {sr._process.pid}")
    print(f"  poll: {sr._process.poll()}")
    sr._process.terminate()
    sr._process.wait(timeout=3)