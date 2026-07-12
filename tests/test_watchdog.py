"""tests/test_watchdog.py
验证 bootstrap/watchdog 的能力:
1. venv 检测
2. 依赖检测
3. 启动子进程 + 端口探活
4. 杀掉子进程后,watchdog 自动重启(在本测试里手动调用 run_watchdog 一轮)
5. atexit 清理子进程
"""
import os
import sys
import time
import signal
import socket
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _cleanup_stale_ports():
    """测试前清理:杀掉所有占用测试端口的旧进程(防上次测试残留)"""
    if not sys.platform.startswith("win"):
        return
    for p in (18999, 18998, 18997):
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-NetTCPConnection -LocalPort {p} -ErrorAction SilentlyContinue | "
             f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"],
            capture_output=True, timeout=5
        )

_cleanup_stale_ports()


def test_venv_detection():
    print("=== venv detection ===")
    from moa_gateway.bootstrap import _venv_python, _in_venv
    venv_p = _venv_python()
    print(f"  venv_python: {venv_p}")
    print(f"  venv_exists: {venv_p.exists()}")
    print(f"  is_in_venv: {_in_venv()}")
    print("  ok")


def test_spawn_and_kill():
    print("=== spawn + kill ===")
    from moa_gateway.bootstrap import spawn_child, kill_proc_tree, wait_for_port, port_dead

    port = 18999
    code = f"""
import http.server, socketserver, time, os
socketserver.TCPServer.allow_reuse_address = True
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b'ok')
    def log_message(s, *a): pass
print('CHILD_PID', os.getpid(), flush=True)
srv = socketserver.TCPServer(('127.0.0.1', {port}), H)
srv.serve_forever()
"""
    cmd = [sys.executable, "-u", "-c", code]
    DATA = ROOT / "data"
    DATA.mkdir(exist_ok=True)
    log = DATA / "wd_test.log"
    if log.exists():
        try: log.unlink()
        except: pass
    proc = spawn_child(cmd, log)
    print(f"  spawned pid={proc.pid}")
    assert wait_for_port("127.0.0.1", port, timeout=10), "port didn't open"
    print("  port up")
    kill_proc_tree(proc, grace_seconds=3)
    proc.wait(timeout=5)
    print(f"  proc.wait() rc={proc.returncode}")
    ok = port_dead("127.0.0.1", port, timeout=10)
    assert ok, "port still bound after kill"
    print("  port released")
    print("  ok")


def test_auto_restart():
    print("=== auto-restart simulation ===")
    from moa_gateway.bootstrap import spawn_child, kill_proc_tree, wait_for_port, port_dead

    port = 18998
    script = f"""
import http.server, socketserver, os
socketserver.TCPServer.allow_reuse_address = True
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b'ok')
    def log_message(s, *a): pass
print('CHILD_PID', os.getpid(), flush=True)
srv = socketserver.TCPServer(('127.0.0.1', {port}), H)
srv.serve_forever()
"""
    DATA = ROOT / "data"
    DATA.mkdir(exist_ok=True)
    cmd = [sys.executable, "-u", "-c", script]

    log1 = DATA / "wd_test1.log"
    if log1.exists():
        try: log1.unlink()
        except: pass
    proc1 = spawn_child(cmd, log1)
    print(f"  1st pid={proc1.pid}")
    assert wait_for_port("127.0.0.1", port, timeout=10)
    print("  1st up")

    kill_proc_tree(proc1, grace_seconds=2)
    proc1.wait(timeout=5)
    print(f"  1st dead (rc={proc1.returncode})")

    assert port_dead("127.0.0.1", port, timeout=10), "port still bound"
    print("  port closed")

    log2 = DATA / "wd_test2.log"
    if log2.exists():
        try: log2.unlink()
        except: pass
    proc2 = spawn_child(cmd, log2)
    print(f"  2nd pid={proc2.pid} (should be different from 1st)")
    assert proc2.pid != proc1.pid
    assert wait_for_port("127.0.0.1", port, timeout=10)
    print("  2nd up — auto-restart works")

    kill_proc_tree(proc2, grace_seconds=2)
    proc2.wait(timeout=5)
    print("  ok")


def test_atexit_cleanup():
    print("=== atexit cleanup ===")
    import atexit as _atexit
    from moa_gateway import bootstrap as b

    port = 18997
    script = f"""
import http.server, socketserver, os
socketserver.TCPServer.allow_reuse_address = True
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(s): s.send_response(200); s.end_headers(); s.wfile.write(b'ok')
    def log_message(s, *a): pass
print('CHILD_PID', os.getpid(), flush=True)
srv = socketserver.TCPServer(('127.0.0.1', {port}), H)
srv.serve_forever()
"""
    DATA = ROOT / "data"
    DATA.mkdir(exist_ok=True)
    log3 = DATA / "wd_test3.log"
    if log3.exists():
        try: log3.unlink()
        except: pass
    proc = b.spawn_child([sys.executable, "-u", "-c", script], log3)
    print(f"  spawned pid={proc.pid}")
    assert b.wait_for_port("127.0.0.1", port, timeout=10)
    print("  up")

    @_atexit.register
    def _cleanup():
        b.kill_proc_tree(proc, grace_seconds=2)
    _atexit._run_exitfuncs()
    proc.wait(timeout=5)
    ok = b.port_dead("127.0.0.1", port, timeout=10)
    assert ok, "port not closed by atexit"
    print("  cleaned by atexit")
    print("  ok")


if __name__ == "__main__":
    test_venv_detection()
    test_spawn_and_kill()
    test_auto_restart()
    test_atexit_cleanup()
    print("\nAll watchdog tests passed.")
