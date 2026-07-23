"""MoA Gateway Pro — 启动入口

7 个子命令:
  serve     默认: venv + 装依赖 + watchdog 父子进程 + 故障自动重启
  direct    直接启动(不创建 venv)— 开发用
  init-data 初始化数据目录(SQLite + admin 用户)
  venv      仅创建/复用 venv
  install   仅安装依赖
  test      跑 smoke test (perf/chaos.py)
  mcp       启动独立 MCP server (stdio / SSE)
  version   版本
  check     自检(环境 / 端口 / 端点 / 鉴权)
"""
from __future__ import annotations
import sys
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent


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
    print(f"  MoA Gateway Pro v{_version()} — DIRECT mode (no watchdog)")
    print(f"  Listening on http://{s.server.host}:{s.server.port}")
    print(f"  WebUI:        http://{s.server.host}:{s.server.port}/")
    print(f"  OpenAI API:   http://{s.server.host}:{s.server.port}/v1/")
    print(f"  MCP SSE:      http://{s.server.host}:{s.server.port}/v1/mcp/sse")
    print("=" * 60)
    uvicorn.run("moa_gateway.server:app",
                host=s.server.host, port=s.server.port,
                workers=s.server.workers, log_level=s.server.log_level.lower(),
                reload=args.reload)


def cmd_init_data(args):
    """初始化数据目录(自动 fallback 到 MOA_ADMIN_PASSWORD env)"""
    import os
    # 安全:admin_password 必须有值,优先 env,fallback 到 yaml 默认(空则报错)
    pwd = os.environ.get("MOA_ADMIN_PASSWORD", "")
    if not pwd:
        print("[ERROR] 必须设 MOA_ADMIN_PASSWORD 环境变量才能 init-data")
        print("        export MOA_ADMIN_PASSWORD='YourStrong#Pass1'")
        sys.exit(1)
    from moa_gateway.config import DATA_DIR, get_settings
    from moa_gateway.storage import get_storage
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    s = get_settings()
    storage = get_storage()
    print(f"OK  data dir: {DATA_DIR}")
    print(f"OK  db path:  {storage.db_path}")
    print(f"OK  admin:    {s.auth.admin_username} ({'*' * len(pwd) if pwd else '未设'})")
    print(f"OK  api keys (yaml): {s.auth.gateway_api_keys}")
    print(f"OK  endpoints: {len(s.models)} ({sum(1 for m in s.models if m.enabled)} enabled)")


def cmd_test(args):
    """跑 smoke test (perf/chaos.py — 19 故障注入场景)"""
    import os
    env = os.environ.copy()
    env.setdefault("MOA_ADMIN_PASSWORD", "TestPass#2024")
    env.setdefault("DEEPSEEK_API_KEY", "sk-mock")
    env.setdefault("OPENAI_API_KEY", "sk-mock")
    env.setdefault("ANTHROPIC_API_KEY", "sk-mock")
    r = subprocess.run(
        [sys.executable, str(ROOT / "perf" / "chaos.py")],
        env=env, cwd=ROOT,
    )
    return r.returncode


def cmd_version(args):
    print(f"MoA Gateway Pro v{_version()}")


def _version() -> str:
    from moa_gateway import __version__
    return __version__


def _detect_venv() -> Path | None:
    """检测任何常见位置的 venv(优先 .venv,fallback venv)"""
    import platform
    IS_WINDOWS = platform.system() == "Windows"
    bin_dir = "Scripts" if IS_WINDOWS else "bin"
    for venv_dir in [".venv", "venv"]:
        p = ROOT / venv_dir / bin_dir / ("python.exe" if IS_WINDOWS else "python")
        if p.exists():
            return p
    return None


def cmd_venv(args):
    """创建或复用 venv(支持 .venv 和 venv 两种目录)"""
    venv_py = _detect_venv()
    if venv_py:
        in_venv = Path(sys.executable).resolve() == venv_py.resolve()
        if in_venv:
            print(f"OK  已在 venv: {sys.executable}")
        else:
            print(f"OK  venv 存在(未用): {venv_py}")
        return
    # 没 venv,调 bootstrap 建
    from moa_gateway.bootstrap import repair_venv
    print(f"[*] 创建 venv: {ROOT / 'venv'}")
    ok = repair_venv()
    if not ok:
        print("[FAIL] venv 创建失败 — 看 data/heal.log")
        sys.exit(1)
    venv_py = _detect_venv()
    print(f"OK  venv 已建: {venv_py}")


def cmd_install(args):
    """装 requirements.txt(用 bootstrap 的 5 镜像 fallback 链路)"""
    from moa_gateway.bootstrap import _pip_install
    req = ROOT / "requirements.txt"
    if not req.exists():
        print(f"[ERROR] {req} 不存在")
        sys.exit(1)
    # 读 requirements.txt,过滤掉注释行和空行
    specs = []
    for line in req.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        specs.append(line)
    print(f"[*] 装 {len(specs)} 个包...")
    ok = _pip_install(specs)
    sys.exit(0 if ok else 1)


def cmd_mcp(args):
    """启动独立 MCP server(stdio 默认,SSE 通过 --transport sse)"""
    from moa_gateway.mcp_server import run_server
    run_server(transport=args.transport, host=args.host, port=args.port)


def cmd_check(args):
    """自检:环境 / 端口 / 端点 / 鉴权"""
    from moa_gateway.bootstrap import (
        diagnose_venv, diagnose_packages, diagnose_port, diagnose_data,
        diagnose_app,
    )
    from moa_gateway.config import get_settings
    s = get_settings()
    all_ok = True
    print("=" * 60)
    print("  MoA Gateway Pro v{} — Self Check".format(_version()))
    print("=" * 60)
    for name, fn in [
        ("venv", lambda: diagnose_venv()),
        ("packages", lambda: diagnose_packages()),
        ("port", lambda: diagnose_port(s.server.host, s.server.port)),
        ("data", lambda: diagnose_data()),
        ("app", lambda: diagnose_app()),
    ]:
        try:
            status, detail = fn()
        except Exception as e:
            status, detail = "error", str(e)
        icon = "OK" if status == "ok" else ("!!" if status in ("recoverable", "missing") else "FAIL")
        print(f"  [{icon}] {name:10} {status:12} {detail}")
        if status not in ("ok", "recoverable"):
            all_ok = False
    print("=" * 60)
    print("  RESULT:", "PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)



def cmd_discover(args):
    "Discover free model APIs (Task #35)"
    if args.list:
        from moa_gateway.discovery.free_model_catalog import get_all_platforms
        platforms = get_all_platforms()
        for p in platforms:
            print(f"  {p.platform_id:20s} {p.base_url:50s} auth={p.auth_type}")
    elif args.run:
        import asyncio
        from moa_gateway.discovery.discovery_engine import FreeModelDiscoveryEngine
        engine = FreeModelDiscoveryEngine()
        models = asyncio.run(engine.discover_all())
        print(f"Discovered {len(models)} free models")
    else:
        print("Use --list or --run. See: start.py discover --help")

def main():
    p = argparse.ArgumentParser(
        description="MoA Gateway Pro - 工业级多模型协作网关",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  start.py serve                  # 完整启动(venv + watchdog)
  start.py direct                 # 开发模式直接启动
  start.py mcp --transport stdio  # 启动 MCP stdio server
  start.py mcp --transport sse    # 启动 MCP SSE server (--port 8911)
  start.py check                  # 自检
  start.py install                # 装依赖
  start.py version
""",
    )
    sub = p.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="默认: venv+依赖+watchdog")
    p_serve.add_argument("--reload", action="store_true", help="开发模式(强制走 direct)")

    p_direct = sub.add_parser("direct", help="直接启动(不创建 venv)")
    p_direct.add_argument("--reload", action="store_true")

    p_mcp = sub.add_parser("mcp", help="启动独立 MCP server (被 Hermes/Claude Code/Cursor 当 MCP server 用)")
    p_mcp.add_argument("--transport", choices=["stdio", "sse"], default="stdio",
                       help="stdio (默认,本地) / sse (HTTP)")
    p_mcp.add_argument("--host", default="127.0.0.1")
    p_mcp.add_argument("--port", type=int, default=8911)

    sub.add_parser("init-data")
    sub.add_parser("venv")
    sub.add_parser("install")
    sub.add_parser("test")
    sub.add_parser("version")
    sub.add_parser("check")

    p_discover = sub.add_parser("discover", help="Discover free model APIs")
    p_discover.add_argument("--list", action="store_true", help="List known platforms")
    p_discover.add_argument("--run", action="store_true", help="Run discovery now")

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
    elif cmd == "mcp":
        return cmd_mcp(args)
    elif cmd == "check":
        return cmd_check(args)
    elif cmd == "discover":
        return cmd_discover(args)
    else:
        p.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
