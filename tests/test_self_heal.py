"""tests/test_self_heal.py
验证自愈启动流程(只测诊断函数,不实际拉 watchdog,避免长时间占用):
- diagnose_venv
- diagnose_packages (mock 缺包场景)
- diagnose_data
- diagnose_app
- diagnose_port
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_diagnose_venv():
    print("=== diagnose_venv ===")
    from moa_gateway.bootstrap import diagnose_venv, _in_venv
    state, detail = diagnose_venv()
    print(f"  state={state} detail={detail}")
    assert state in ("ok", "recoverable", "missing")
    if _in_venv():
        assert state == "ok"
    print("  ok")


def test_diagnose_packages():
    print("=== diagnose_packages ===")
    from moa_gateway.bootstrap import diagnose_packages, CRITICAL_PACKAGES
    state, missing = diagnose_packages()
    print(f"  state={state} missing={len(missing)}")
    if state == "ok":
        assert len(missing) == 0
        print(f"  all {len(CRITICAL_PACKAGES)} packages OK")
    else:
        print(f"  missing: {[m[0] for m in missing]}")
    print("  ok")


def test_diagnose_data():
    print("=== diagnose_data ===")
    from moa_gateway.bootstrap import diagnose_data
    state, issues = diagnose_data()
    print(f"  state={state} issues={issues}")
    assert state in ("ok", "missing")
    print("  ok")


def test_diagnose_app():
    print("=== diagnose_app ===")
    from moa_gateway.bootstrap import diagnose_app
    state, detail = diagnose_app()
    print(f"  state={state} detail={detail}")
    if state == "ok":
        assert "路由" in detail
    print("  ok")


def test_diagnose_port():
    print("=== diagnose_port ===")
    from moa_gateway.bootstrap import diagnose_port, _port_listen
    # 测一个肯定空闲的端口
    state, detail = diagnose_port("127.0.0.1", 29999)
    print(f"  port 29999: state={state} detail={detail}")
    assert state == "ok"
    # 测一个被占用的(如果 watchdog 在跑就是 8910)
    import socket
    test_port = 29998
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", test_port))
    s.listen(1)
    try:
        state, detail = diagnose_port("127.0.0.1", test_port)
        print(f"  port {test_port} (occupied): state={state} detail={detail}")
        assert state == "occupied"
    finally:
        s.close()
    print("  ok")


def test_repair_data_when_missing():
    """模拟 JWT secret / Fernet key 缺失后,repair_data 能恢复"""
    print("=== repair_data ===")
    from moa_gateway import bootstrap as b
    from moa_gateway.config import DATA_DIR as _DD
    jwt = _DD / ".jwt_secret"
    fernet = _DD / ".fernet_key"
    # 备份
    jwt_bak = jwt.read_bytes() if jwt.exists() else None
    fern_bak = fernet.read_bytes() if fernet.exists() else None
    # 删
    if jwt.exists(): jwt.unlink()
    if fernet.exists(): fernet.unlink()
    try:
        # 体检 → 应有 issues
        state, issues = b.diagnose_data()
        assert state == "missing", f"expected missing, got {state}: {issues}"
        print(f"  before: {issues}")
        # 修复
        ok = b.repair_data()
        assert ok
        # 再检
        state2, issues2 = b.diagnose_data()
        assert state2 == "ok", f"after repair, still issues: {issues2}"
        print(f"  after: all ok")
    finally:
        # 还原
        if jwt_bak is not None:
            jwt.write_bytes(jwt_bak)
        else:
            if jwt.exists(): jwt.unlink()
        if fern_bak is not None:
            fernet.write_bytes(fern_bak)
        else:
            if fernet.exists(): fernet.unlink()
    print("  ok")


if __name__ == "__main__":
    test_diagnose_venv()
    test_diagnose_packages()
    test_diagnose_data()
    test_diagnose_app()
    test_diagnose_port()
    test_repair_data_when_missing()
    print("\nAll self-heal tests passed.")
