"""start_ui.py — MoA Gateway Pro 桌面 UI 启动器(自检 + 自愈 + 启动)

执行流程:
  1. 找到项目根目录(start_ui.py 所在)
  2. 检测/创建 .venv 虚拟环境
  3. 检测/修复依赖(flet / PySide6 / fastapi / 等)
  4. 用 venv Python 重新启动本脚本(如果刚创建或升级了依赖)
  5. 启动 flet 桌面 UI

用法:
  python start_ui.py                  # 当前 Python 启动(自动建 venv)
  start_ui.bat                        # Windows 双击
  start_ui.sh                         # macOS / Linux
"""
from __future__ import annotations
import os
import sys
import subprocess
import platform
from pathlib import Path


# ========== 路径与常量 ==========
ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
VENV_PYTHON_WIN = VENV_DIR / "Scripts" / "python.exe"
VENV_PYTHON_UNIX = VENV_DIR / "bin" / "python"
IS_WINDOWS = platform.system() == "Windows"


def venv_python() -> Path:
    return VENV_PYTHON_WIN if IS_WINDOWS else VENV_PYTHON_UNIX


REQUIREMENTS = ROOT / "requirements.txt"


# ========== UI 颜色(给控制台输出用) ==========
class C:
    H = "\033[1;36m"   # cyan
    G = "\033[1;32m"   # green
    Y = "\033[1;33m"   # yellow
    R = "\033[1;31m"   # red
    D = "\033[2;37m"   # dim
    E = "\033[0m"       # end


def info(msg): print(f"{C.H}ℹ{C.E} {msg}", flush=True)
def ok(msg):   print(f"{C.G}✓{C.E} {msg}", flush=True)
def warn(msg): print(f"{C.Y}⚠{C.E} {msg}", flush=True)
def err(msg):  print(f"{C.R}✗{C.E} {msg}", flush=True)
def step(n, total, msg): print(f"\n{C.D}[{n}/{total}]{C.E} {C.H}{msg}{C.E}", flush=True)


# ========== 步骤 1: 检测 venv ==========
def check_venv() -> bool:
    py = venv_python()
    if py.exists():
        return True
    return False


# ========== 步骤 2: 创建 venv ==========
def create_venv() -> bool:
    info(f"创建虚拟环境: {VENV_DIR}")
    try:
        import venv
        builder = venv.EnvBuilder(
            system_site_packages=False,
            clear=True,
            symlinks=not IS_WINDOWS,
            with_pip=True,
            upgrade_deps=True,
        )
        builder.create(str(VENV_DIR))
        ok(f"虚拟环境已创建: {VENV_DIR}")
        return True
    except Exception as e:
        err(f"创建 venv 失败: {e}")
        return False


# ========== 步骤 3: 检查/修复依赖 ==========
def check_deps(python: Path) -> tuple:
    """返回 (ok, missing)"""
    # 关键依赖列表
    critical = [
        "flet", "fastapi", "uvicorn", "httpx", "pydantic",
        "aiosqlite", "bcrypt", "jose", "cryptography",
        "yaml", "psutil",
    ]
    try:
        result = subprocess.run(
            [str(python), "-c",
             "import flet, fastapi, uvicorn, httpx, pydantic, "
             "aiosqlite, bcrypt, jose, cryptography, yaml, psutil; "
             "print('ok')"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and "ok" in result.stdout:
            return True, []
        return False, critical
    except Exception as e:
        return False, critical


def install_deps(python: Path) -> bool:
    info(f"安装依赖(可能需要 1-3 分钟)...")
    # 用清华源加速
    mirror_args = [
        "-i", "https://pypi.tuna.tsinghua.edu.cn/simple",
        "--trusted-host", "pypi.tuna.tsinghua.edu.cn",
        "--timeout", "120",
    ]
    # 升级 pip
    subprocess.run(
        [str(python), "-m", "pip", "install", "--upgrade", "pip"] + mirror_args,
        capture_output=True, timeout=120,
    )
    # 装 requirements
    if REQUIREMENTS.exists():
        result = subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)] + mirror_args,
            capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            err(f"依赖安装失败:\n{result.stdout[-500:]}\n{result.stderr[-500:]}")
            return False
    else:
        # fallback 关键依赖
        deps = [
            "flet>=0.27", "fastapi>=0.115", "uvicorn[standard]>=0.30",
            "httpx>=0.27", "pydantic>=2.9", "aiosqlite",
            "bcrypt", "python-jose[cryptography]", "pyyaml", "psutil",
        ]
        subprocess.run(
            [str(python), "-m", "pip", "install"] + mirror_args + deps,
            capture_output=True, timeout=600,
        )
    ok("依赖安装完成")
    return True


# ========== 步骤 4: 重启到 venv Python(如果需要) ==========
def relaunch_in_venv() -> bool:
    """如果当前 Python 不是 venv 的,重新用 venv 启动"""
    vpy = venv_python()
    if not vpy.exists():
        return False
    # 检查当前 Python 路径
    current = Path(sys.executable).resolve()
    target = vpy.resolve()
    if current == target:
        return False  # 已经在 venv 里
    info(f"切到 venv: {target}")
    # 重新 exec
    args = [str(target), str(Path(__file__).resolve())] + sys.argv[1:]
    # 把当前 PYTHONPATH / 工作目录保留
    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(VENV_DIR)
    try:
        result = subprocess.run(args, env=env, cwd=str(ROOT))
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        sys.exit(0)


# ========== 主流程 ==========
def main():
    print(f"\n{C.H}╔════════════════════════════════════════╗{C.E}", flush=True)
    print(f"{C.H}║  MoA Gateway Pro — 启动器 (自检 + 自愈) ║{C.E}", flush=True)
    print(f"{C.H}╚════════════════════════════════════════╝{C.E}\n", flush=True)
    info(f"项目根目录: {ROOT}")
    info(f"Python: {sys.executable}  ({platform.python_version()})")

    total_steps = 5

    # Step 1: 检测 venv
    step(1, total_steps, "检测虚拟环境")
    if check_venv():
        ok(f"venv 已存在: {VENV_DIR}")
    else:
        warn(f"venv 不存在,准备创建")
        if not create_venv():
            err("venv 创建失败,无法继续")
            sys.exit(1)

    # Step 2: 切到 venv(重新 exec)
    step(2, total_steps, "切换到 venv Python")
    relaunch_in_venv()  # 如果已经在 venv 内,什么也不做

    # Step 3: 检查依赖
    step(3, total_steps, "检查依赖")
    vpy = venv_python()
    dep_ok, missing = check_deps(vpy)
    if dep_ok:
        ok("所有依赖齐全")
    else:
        warn(f"缺少依赖,准备自动安装")
        if not install_deps(vpy):
            err("依赖安装失败")
            sys.exit(1)
        # 装完再检查一次
        dep_ok, missing = check_deps(vpy)
        if not dep_ok:
            err(f"依赖仍不完整: {missing}")
            sys.exit(1)
        ok("依赖验证通过")

    # Step 4: 加载 UI 模块检查
    step(4, total_steps, "验证 UI 模块可加载")
    try:
        result = subprocess.run(
            [str(vpy), "-c",
             "import sys; sys.path.insert(0, r'" + str(ROOT) + "'); "
             "import flet; "
             "import moa_gateway.ui.theme; "
             "import moa_gateway.ui.pages; "
             "import moa_gateway.ui.pages2; "
             "import moa_gateway.ui.main; "
             "print('ui ok')"],
            capture_output=True, text=True, timeout=20,
            cwd=str(ROOT),
        )
        if "ui ok" in result.stdout:
            ok("UI 模块加载成功")
        else:
            err(f"UI 模块加载失败:\n{result.stdout[-400:]}\n{result.stderr[-400:]}")
            sys.exit(1)
    except Exception as e:
        err(f"UI 模块验证异常: {e}")
        sys.exit(1)

    # Step 5: 启动 flet 桌面 UI
    step(5, total_steps, "启动桌面 UI")
    info(f"用 venv Python: {vpy}")
    print()
    # 用 subprocess 启动 flet UI(Windows 上 os.execv 路径含空格会出错)
    code = (
        "import sys; "
        f"sys.path.insert(0, r'{ROOT}'); "
        "from moa_gateway.ui.main import run; "
        "run()"
    )
    args = [str(vpy), "-c", code]
    try:
        result = subprocess.run(args, cwd=str(ROOT))
        sys.exit(result.returncode)
    except KeyboardInterrupt:
        print()
        info("用户中断")
        sys.exit(0)
    except Exception as e:
        err(f"启动 UI 失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{C.Y}用户中断{C.D} 退出{C.E}")
        sys.exit(0)
