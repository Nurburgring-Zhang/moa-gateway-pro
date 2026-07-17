"""moa_gateway.bootstrap — 自愈式启动管理
一站式负责:
  1. 环境体检(venv / 关键包 / 关键文件 / 端口 / sqlite 写权限)
  2. 异常自动修复(多源重装 / 文件重建 / 路径修复)
  3. watchdog 父子进程(故障自动重启)
  4. 退出时清理全部子进程(跨平台)
"""
from __future__ import annotations

import atexit
import contextlib
import importlib
import logging
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = ROOT / "venv"
DATA_DIR = ROOT / "data"
LOG_DIR = DATA_DIR / "logs"
PID_FILE = DATA_DIR / "gateway.pid"
WATCHDOG_LOG = DATA_DIR / "watchdog.log"
HEAL_LOG = DATA_DIR / "heal.log"

IS_WINDOWS = platform.system() == "Windows"

# 关键包(必须能 import,否则视为环境异常)
CRITICAL_PACKAGES = [
    "fastapi", "uvicorn", "pydantic", "pydantic_settings",
    "httpx", "aiohttp", "sqlalchemy", "aiosqlite",
    "yaml", "rich", "tenacity", "jose", "passlib",
    "cryptography", "bcrypt", "psutil", "multipart",
]

# 国内 pip 镜像(按优先级)
PIP_MIRRORS = [
    ("https://pypi.tuna.tsinghua.edu.cn/simple", "清华"),
    ("https://mirrors.aliyun.com/pypi/simple/", "阿里云"),
    ("https://pypi.douban.com/simple/", "豆瓣"),
    ("https://mirrors.huaweicloud.com/repository/pypi/simple", "华为云"),
    ("https://pypi.org/simple", "PyPI官方(回退)"),
]

# 检查状态(用于最终汇报)
HEAL_REPORT: dict[str, dict] = {}


# ========== 0. 通用工具 ==========
def _log(msg: str, also_stdout: bool = True):
    """统一日志(写到 heal.log + 可选 stdout)"""
    HEAL_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(HEAL_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    if also_stdout:
        print(line, flush=True)


def _run(cmd: list[str], timeout: int = 600, **kw) -> tuple[int, str, str]:
    """subprocess.run 包装,返回 (rc, stdout, stderr)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout after {timeout}s: {e}"
    except Exception as e:
        return 1, "", f"exec error: {e}"


def _venv_python() -> Path:
    if IS_WINDOWS:
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def _in_venv() -> bool:
    return Path(sys.executable).resolve() == _venv_python().resolve() \
        or Path(sys.prefix).resolve() == VENV_DIR.resolve()


def _pip() -> list[str]:
    """返回当前应该用的 pip 命令"""
    if _in_venv():
        return [sys.executable, "-m", "pip"]
    if (VENV_DIR / ("Scripts" if IS_WINDOWS else "bin")).exists():
        return [str(_venv_python()), "-m", "pip"]
    return [sys.executable, "-m", "pip"]


# ========== 1. 体检:venv ==========
def diagnose_venv() -> tuple[str, str]:
    """检查 venv。返回 (状态, 详情)"""
    if _in_venv():
        return ("ok", f"已在 venv 里: {sys.executable}")
    if _venv_python().exists():
        return ("recoverable", f"venv 存在但当前进程未使用: {_venv_python()}")
    return ("missing", "venv 不存在")


def repair_venv() -> bool:
    """创建 venv(若已存在则跳过)"""
    if _venv_python().exists():
        return True
    _log(f"[fix] 创建 venv: {VENV_DIR}")
    rc, out, err = _run([sys.executable, "-m", "venv", "--upgrade-deps", str(VENV_DIR)],
                        timeout=180)
    if rc != 0:
        _log(f"[fail] venv 创建失败 rc={rc}: {err[-300:]}")
        return False
    _log(f"[ok] venv 创建成功: {_venv_python()}")
    return True


# ========== 2. 体检:关键包 ==========
def diagnose_packages() -> tuple[str, list[str]]:
    """检查关键包能否 import。返回 (状态, 缺失列表)"""
    missing = []
    for mod in CRITICAL_PACKAGES:
        try:
            importlib.import_module(mod)
        except Exception as e:
            missing.append((mod, str(e)[:100]))
    if not missing:
        return ("ok", [])
    return ("missing", missing)


def _pip_install(specs: list[str], mirrors: list[tuple[str, str]] = PIP_MIRRORS,
                 retries_per_mirror: int = 2) -> bool:
    """尝试用多个镜像源安装,每个源 2 次重试。specs 是 pip 接受的包列表。"""
    base = _pip() + ["install", "--disable-pip-version-check", "--no-input"]
    last_err = "unknown"
    for url, name in mirrors:
        for attempt in range(retries_per_mirror):
            cmd = base + ["-i", url] + specs
            _log(f"[pip] 试源 {name} ({url}) 第 {attempt+1}/{retries_per_mirror} 次: {' '.join(specs)}")
            rc, out, err = _run(cmd, timeout=600)
            if rc == 0:
                _log(f"[ok] 装包成功(源 {name})")
                return True
            err_short = (err or out)[-300:]
            last_err = f"rc={rc} {err_short}"
            _log(f"[warn] 源 {name} 失败: {last_err}")
            time.sleep(1)
    _log(f"[fail] 所有源都失败,最后错误: {last_err}")
    return False


def repair_packages() -> bool:
    """修复关键包:先重装 requirements,再补单独缺失的包"""
    req = ROOT / "requirements.txt"
    # 1) 重装 requirements(多源)
    if req.exists():
        if _pip_install(["-r", str(req)]):
            pass
        else:
            _log("[warn] requirements 重装失败,继续尝试单包")
    # 2) 检测还有哪些缺
    state, missing = diagnose_packages()
    if state == "ok":
        return True
    # 3) 装单独的
    pkgs = [m[0] for m in missing]
    _log(f"[fix] 单独装缺失的包: {pkgs}")
    return _pip_install(pkgs)


# ========== 3. 体检:数据目录 / 关键文件 ==========
def diagnose_data() -> tuple[str, list[str]]:
    """检查 data 目录、关键文件、sqlite 写权限"""
    issues = []
    if not DATA_DIR.exists():
        issues.append("data 目录不存在")
    else:
        try:
            test_file = DATA_DIR / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink()
        except Exception as e:
            issues.append(f"data 目录不可写: {e}")
    # JWT secret
    if not (DATA_DIR / ".jwt_secret").exists():
        issues.append("JWT secret 缺失")
    # Fernet key
    if not (DATA_DIR / ".fernet_key").exists():
        issues.append("Fernet key 缺失")
    return ("ok" if not issues else "missing", issues)


def repair_data() -> bool:
    """创建缺失的目录和关键文件"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # 1) JWT secret — 直接写文件,不依赖 singleton
    secret_path = DATA_DIR / ".jwt_secret"
    if not secret_path.exists():
        try:
            import secrets as _secrets
            new_secret = _secrets.token_urlsafe(48)
            secret_path.write_text(new_secret, encoding="utf-8")
            _log(f"[fix] 生成 JWT secret → {secret_path}")
        except Exception as e:
            _log(f"[fail] JWT secret 写入失败: {e}")
            return False
    # 2) Fernet key — 同样直接写文件
    fernet_path = DATA_DIR / ".fernet_key"
    if not fernet_path.exists():
        try:
            from cryptography.fernet import Fernet
            fernet_path.write_bytes(Fernet.generate_key())
            _log(f"[fix] 生成 Fernet key → {fernet_path}")
        except Exception as e:
            _log(f"[fail] Fernet key 写入失败: {e}")
            return False
    # 3) 触发 config + storage 加载,确保 settings 和 admin 都到位
    try:
        from .config import reload_settings
        s = reload_settings()  # 重新读盘
        if not s.auth.jwt_secret:
            _log("[fail] reload 后 JWT secret 仍为空")
            return False
    except Exception as e:
        _log(f"[fail] config reload 失败: {e}")
        return False
    try:
        from .storage import get_storage
        get_storage()
    except Exception as e:
        _log(f"[fail] storage 初始化失败: {e}")
        return False
    return True


# ========== 4. 体检:端口 ==========
def diagnose_port(host: str, port: int) -> tuple[str, str]:
    """检查目标端口是否空闲"""
    if _port_listen(host, port):
        return ("occupied", f"{host}:{port} 已被占用")
    return ("ok", f"{host}:{port} 空闲")


def _port_listen(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except Exception:
        return False


# ========== 5. 体检:app 能实例化 ==========
def diagnose_app() -> tuple[str, str]:
    """尝试 import app,确保 FastAPI 能构建"""
    try:
        from .server import app
        # 检查路由
        n = len(app.routes)
        return ("ok", f"app 正常, {n} 个路由")
    except Exception as e:
        tb = traceback.format_exc()[-500:]
        return ("error", f"app 加载失败: {e}\n{tb}")


# ========== 6. 自愈主循环 ==========
def heal_environment(max_total_seconds: int = 600) -> bool:
    """完整环境体检 + 修复。每步失败就尝试修复,再不行就放弃。"""
    _log("=" * 60)
    _log("开始环境自愈(health check + auto repair)")
    _log("=" * 60)
    t0 = time.time()

    # Step 1: venv
    _log("\n[1/5] 检查虚拟环境…")
    state, detail = diagnose_venv()
    HEAL_REPORT["venv"] = {"state": state, "detail": detail}
    if state == "missing":
        _log("  → 缺失,创建中…")
        if not repair_venv():
            _log("  ✗ venv 创建失败,请手动: python -m venv venv")
            return False
        # 创建完需要切到 venv 重新跑
        vpy = str(_venv_python())
        _log(f"  → 切到 venv 重启: {vpy}")
        os.execv(vpy, [vpy] + sys.argv)
        # execv 不会返回
    elif state == "recoverable":
        vpy = str(_venv_python())
        _log(f"  → 切到 venv 重启: {vpy}")
        os.execv(vpy, [vpy] + sys.argv)
    _log("  ✓ venv OK")

    # Step 2: 关键包
    _log("\n[2/5] 检查关键 Python 包…")
    state, missing = diagnose_packages()
    if state != "ok":
        miss_str = ", ".join(f"{m[0]}" for m in missing)
        _log(f"  → 缺失/损坏: {miss_str}")
        if not repair_packages():
            _log("  ✗ 关键包修复失败,请手动: pip install -r requirements.txt")
            return False
        # 再确认
        state2, missing2 = diagnose_packages()
        if state2 != "ok":
            _log(f"  ✗ 修复后仍有缺失: {[m[0] for m in missing2]}")
            return False
    _log(f"  ✓ 全部 {len(CRITICAL_PACKAGES)} 个关键包 OK")

    # Step 3: 数据目录与关键文件
    _log("\n[3/5] 检查数据目录与关键文件…")
    state, issues = diagnose_data()
    if state != "ok":
        _log(f"  → 异常: {issues},修复中…")
        if not repair_data():
            return False
    _log("  ✓ data 目录与关键文件 OK")

    # Step 4: app 加载
    _log("\n[4/5] 检查 FastAPI app 能加载…")
    state, detail = diagnose_app()
    HEAL_REPORT["app"] = {"state": state, "detail": detail}
    if state != "ok":
        _log(f"  ✗ {detail}")
        return False
    _log(f"  ✓ {detail}")

    # Step 5: 端口
    _log("\n[5/5] 检查端口…")
    try:
        from .config import get_settings
        s = get_settings()
        port = s.server.port
    except Exception:
        port = 8910
    state, detail = diagnose_port("127.0.0.1", port)
    HEAL_REPORT["port"] = {"state": state, "detail": detail, "port": port}
    if state == "occupied":
        _log(f"  ✗ {detail}")
        _log("  解决: 改 config.yaml 的 server.port,或杀掉占用进程")
        return False
    _log(f"  ✓ {detail}")

    _log("=" * 60)
    _log(f"环境自愈完成,耗时 {time.time()-t0:.1f}s")
    _log("=" * 60)
    return True


# ========== 7. 跨平台子进程管理 ==========
def _set_pdeathsig_preexec() -> None:
    if hasattr(signal, "SIGHUP"):
        PR_SET_PDEATHSIG = 1
        try:
            import ctypes
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(PR_SET_PDEATHSIG, signal.SIGHUP, 0, 0, 0)
        except Exception:
            pass


def spawn_child(cmd: list[str], log_file: Path | None = None) -> subprocess.Popen:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_path = log_file or (DATA_DIR / "child.log")
    # 修12: 用文件路径而非已打开的句柄,避免父进程持 fd 跨重启累积
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        kwargs = {"stdin": subprocess.DEVNULL,
                      "stdout": open(log_path, "ab", buffering=0),
                      "stderr": subprocess.STDOUT,
                      "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    else:
        kwargs = {"stdin": subprocess.DEVNULL,
                      "stdout": open(log_path, "ab", buffering=0),
                      "stderr": subprocess.STDOUT,
                      "preexec_fn": os.setsid}
    proc = subprocess.Popen(cmd, **kwargs)
    # 让子进程继承 fd 句柄,父进程不再保留引用 — OS 在子进程退出时自动关闭
    _log(f"[watchdog] 子进程已启动: pid={proc.pid} cmd={' '.join(cmd[:3])}…")
    return proc


def check_existing_instance() -> bool:
    """修18: 启动时检查是否已有实例在跑(防端口冲突和数据竞争)"""
    PID_FILE = DATA_DIR / "gateway.pid"
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        # 检查进程是否真活着
        if IS_WINDOWS:
            r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                               capture_output=True, text=True, timeout=5)
            return pid in r.stdout
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    except Exception:
        return False


def kill_proc_tree(proc: subprocess.Popen | None, grace_seconds: float = 5.0) -> None:
    if proc is None or proc.poll() is not None:
        return
    pid = proc.pid
    _log(f"[watchdog] 清理子进程树: pid={pid}")
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                           capture_output=True, timeout=10)
        else:
            import signal as _sig
            try:
                os.killpg(os.getpgid(pid), _sig.SIGTERM)
            except ProcessLookupError:
                return
            t0 = time.time()
            while time.time() - t0 < grace_seconds:
                if proc.poll() is not None:
                    return
                time.sleep(0.2)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(os.getpgid(pid), _sig.SIGKILL)
    except Exception as e:
        _log(f"[watchdog] kill 出错: {e}")


def wait_for_port(host: str, port: int, timeout: float = 30.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if _port_listen(host, port):
            return True
        time.sleep(0.5)
    return False


def port_dead(host: str, port: int, timeout: float = 10.0) -> bool:
    """尝试 bind,看端口真的释放了没(避免 TIME_WAIT 假阳性)"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            s.close()
            return True
        except OSError:
            s.close()
            time.sleep(0.3)
    return False


# ========== 8. Watchdog ==========
def run_watchdog(child_cmd: list[str], host: str = "127.0.0.1",
                 port: int = 8910,
                 max_restarts: int = 0,
                 restart_cooldown: float = 3.0,
                 stop_event: threading.Event | None = None) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    _log("=" * 60)
    _log(f"watchdog 启动: pid={os.getpid()}")
    _log(f"监听: http://{host}:{port}")
    _log(f"子进程命令: {' '.join(child_cmd)}")
    _log("=" * 60)

    child_ref: list[subprocess.Popen | None] = [None]

    def _cleanup_all():
        if child_ref[0] is not None:
            kill_proc_tree(child_ref[0])
    atexit.register(_cleanup_all)

    # 信号处理
    def _sig_handler(signum, frame):
        _log(f"watchdog 收到信号 {signum},准备退出")
        _cleanup_all()
        if IS_WINDOWS:
            sys.exit(0)
        else:
            os._exit(0)
    if not IS_WINDOWS:
        signal.signal(signal.SIGTERM, _sig_handler)
        signal.signal(signal.SIGHUP, _sig_handler)
    else:
        try:
            signal.signal(signal.SIGINT, _sig_handler)
            signal.signal(signal.SIGBREAK, _sig_handler)
        except Exception:
            pass

    restarts = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            _log("watchdog 收到停止事件")
            break
        proc = spawn_child(child_cmd)
        child_ref[0] = proc

        if wait_for_port(host, port, timeout=60):
            _log(f"✓ 服务已就绪(http://{host}:{port})")

        rc = proc.wait()
        child_ref[0] = None
        _log(f"⚠ 子进程退出: pid={proc.pid}, rc={rc}, restarts={restarts}")

        if max_restarts > 0 and restarts >= max_restarts:
            _log(f"已达最大重启次数 {max_restarts},watchdog 退出")
            return rc

        restarts += 1
        if stop_event is None:
            _log(f"{restart_cooldown}s 后自动重启(第 {restarts} 次)…")
            time.sleep(restart_cooldown)
    return 0


# ========== 9. 顶层入口 ==========
def bootstrap_and_serve(args) -> int:
    """完整自愈启动:
    1. 自愈环境(venv/包/数据/app/端口)
    2. 启 watchdog
    3. 拉起 uvicorn 子进程
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    # heal.log 初始化
    HEAL_LOG.write_text("", encoding="utf-8") if HEAL_LOG.exists() else HEAL_LOG.touch()
    _log("=" * 60)
    _log("MoA Gateway Pro 启动自愈流程")
    _log("=" * 60)

    # 自愈环境
    if not heal_environment():
        _log("\n✗ 环境自愈失败 — 请检查上面的错误并手动修复")
        _log("  常见解决:")
        _log("    1) 手动 pip install -r requirements.txt")
        _log("    2) 检查 8910 端口是否被占用")
        _log("    3) 检查杀毒软件是否拦截")
        return 1

    # 拿配置
    try:
        from .config import get_settings
        s = get_settings()
        port = s.server.port
        log_level = s.server.log_level.lower()
    except Exception:
        port = 8910
        log_level = "info"

    # 切到项目根目录,方便 -m 找包
    os.chdir(ROOT)

    # 起 watchdog
    child_cmd = [
        sys.executable, "-m", "uvicorn",
        "moa_gateway.server:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--log-level", log_level,
    ]
    _log("\n启动 watchdog + uvicorn…")
    _log(f"  WebUI:  http://127.0.0.1:{port}/")
    _log(f"  API:    http://127.0.0.1:{port}/v1/")
    return run_watchdog(child_cmd, host="127.0.0.1", port=port)
