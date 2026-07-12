"""MoA Gateway Pro — 启动入口
推荐使用 bootstrap 模式(默认):
  python start.py
  → 自动创建 venv → 自动装依赖 → watchdog 监控 → 故障自动重启

高级用法:
  python start.py serve        # 等同于 python start.py(bootstrap + watchdog)
  python start.py direct       # 不创建 venv、不 watchdog,直接当前 python 启动(开发用)
  python start.py init-data    # 仅初始化数据目录
  python start.py test         # 跑烟雾测试
  python start.py version      # 版本
  python start.py venv         # 仅创建/复用 venv
  python start.py install      # 仅安装依赖
"""
from __future__ import annotations
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def cmd_bootstrap(args):
    """默认入口:venv + 依赖 + watchdog + 服务"""
    from moa_gateway.bootstrap import bootstrap_and_serve
    return bootstrap_and_serve(args)


def cmd_direct(args):
    """直接启动(不创建 venv、不 watchdog)— 开发用"""
    from moa_gateway.config import get_settings
    import uvicorn
    s = get_settings()
    print("=" * 60)
    print(f"  MoA Gateway Pro v1.0.0 — DIRECT mode (no watchdog)")
    print(f"  Listening on http://{s.server.host}:{s.server.port}")
    print(f"  WebUI:        http://{s.server.host}:{s.server.port}/")
    print(f"  OpenAI API:   http://{s.server.host}:{s.server.port}/v1/")
    print("=" * 60)
    uvicorn.run("moa_gateway.server:app",
                host=s.server.host, port=s.server.port,
                workers=s.server.workers, log_level=s.server.log_level.lower(),
                reload=args.reload)


def cmd_init_data(args):
    from moa_gateway.config import DATA_DIR, get_settings
    from moa_gateway.storage import get_storage
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    s = get_settings()
    storage = get_storage()
    print(f"OK data dir: {DATA_DIR}")
    print(f"OK db path: {storage.db_path}")
    print(f"OK admin user: {s.auth.admin_username}")
    print(f"OK default api key (yaml): {s.auth.gateway_api_keys}")
    print(f"OK configured endpoints: {len(s.models)}")


def cmd_test(args):
    import subprocess
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_smoke.py")])
    return r.returncode


def cmd_version(args):
    from moa_gateway import __version__
    print(f"MoA Gateway Pro v{__version__}")


def cmd_venv(args):
    from moa_gateway.bootstrap import create_venv, is_in_venv, venv_python, venv_exists
    if is_in_venv():
        print(f"Already in venv: {sys.executable}")
    if not venv_exists():
        create_venv()
        print(f"OK created venv: {venv_python()}")
    else:
        print(f"OK venv exists: {venv_python()}")


def cmd_install(args):
    from moa_gateway.bootstrap import ensure_deps
    ok = ensure_deps(ROOT / "requirements.txt")
    sys.exit(0 if ok else 1)


def main():
    p = argparse.ArgumentParser(
        description="MoA Gateway Pro - 工业级多模型协作网关",
    )
    sub = p.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="默认: venv+依赖+watchdog")
    p_serve.add_argument("--reload", action="store_true", help="开发模式(强制走 direct)")

    p_direct = sub.add_parser("direct", help="直接启动(不创建 venv)")
    p_direct.add_argument("--reload", action="store_true")

    sub.add_parser("init-data")
    sub.add_parser("venv")
    sub.add_parser("install")
    sub.add_parser("test")
    sub.add_parser("version")

    args = p.parse_args()
    cmd = args.cmd or "serve"

    if cmd == "serve":
        if getattr(args, "reload", False):
            return cmd_direct(args)
        return cmd_bootstrap(args)
    elif cmd == "direct":
        return cmd_direct(args)
    elif cmd == "init-data":
        return cmd_init_data(args)
    elif cmd == "test":
        return cmd_test(args)
    elif cmd == "version":
        return cmd_version(args)
    elif cmd == "venv":
        return cmd_venv(args)
    elif cmd == "install":
        return cmd_install(args)
    else:
        p.print_help()


if __name__ == "__main__":
    sys.exit(main() or 0)
