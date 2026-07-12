"""moa_gateway.ui.server_runner — 后端 server 进程管理(用独立 subprocess)

不用线程内嵌 uvicorn(那样跟 flet 主线程 asyncio 冲突),改用独立 Python 子进程。
这样:
- 启动器能用 .venv 里的 Python(独立依赖)
- 异常能用 subprocess 状态码捕获
- 启动/停止/日志 都独立干净
"""
from __future__ import annotations
import os
import sys
import socket
import time
import subprocess
import threading
import logging
import urllib.request
import asyncio
from pathlib import Path
import flet as ft

logger = logging.getLogger(__name__)

# 项目根目录: moa_gateway/ui/server_runner.py → 上 3 层
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ServerRunner:
    """子进程方式跑后端 server"""
    def __init__(self):
        self._process: subprocess.Popen = None
        self._port: int = None
        self._log_thread: threading.Thread = None
        self.is_running = False
        self.last_error: str = None
        # flet page(可选,用于显示状态)
        self._page: ft.Page = None
        # 临时 event loop 线程(供 UI 线程同步调 async 函数)
        self._tmp_loop: asyncio.AbstractEventLoop = None
        self._tmp_loop_thread: threading.Thread = None
        self._tmp_loop_ready: threading.Event = None
        # 启动回调列表:server 起来时自动调,让 page 重新加载数据
        # (因为 page 启动时 sr.is_running=False → 早 return 不加载,需要这个钩子)
        self.on_started_callbacks: list = []
        # 修25: admin JWT(自动 login 拿,所有 /api/* 端点都用这个)
        self.admin_token: str = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def loop(self):  # 兼容旧接口:返回临时 loop(永远 running)
        return self._ensure_tmp_loop()

    def _ensure_tmp_loop(self) -> asyncio.AbstractEventLoop:
        """确保后台有一个 running event loop,供 UI 线程跨线程跑 async coroutine。
        flet 主 loop 已经在跑(UI 渲染),不能直接用,所以建一个独立 loop 在新 thread。"""
        if self._tmp_loop and self._tmp_loop_thread and self._tmp_loop_thread.is_alive():
            return self._tmp_loop
        self._tmp_loop_ready = threading.Event()
        def _runner():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._tmp_loop = loop
            self._tmp_loop_ready.set()
            loop.run_forever()
        self._tmp_loop_thread = threading.Thread(target=_runner, daemon=True, name="ui-tmp-loop")
        self._tmp_loop_thread.start()
        self._tmp_loop_ready.wait(timeout=5)
        return self._tmp_loop


    def _admin_login(self):
        """修25: server 启动后自动用默认 admin 登录,拿 JWT 存 self.admin_token。
        UI 所有 /api/* 调用都用这个 token(后端 auth.py 修24 也支持 admin JWT 鉴权)。"""
        import json as _json
        try:
            url = f"http://127.0.0.1:{self._port}/api/auth/login"
            payload = _json.dumps({"username": "admin", "password": "admin"}).encode()
            req = urllib.request.Request(
                url, data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read().decode("utf-8"))
                self.admin_token = data.get("token") or data.get("access_token")
                if self.admin_token:
                    logger.info("admin login OK, token len=%d", len(self.admin_token))
                else:
                    logger.warning("admin login returned no access_token: %s", data)
        except Exception as e:
            logger.warning("admin login failed (using default admin/admin): %s", e)
            self.admin_token = None
    def call_async(self, coro, timeout: float = 30):
        """从 UI 线程同步调一个 async coroutine(内部跨线程到临时 loop)。
        异常时返回 None(调用方需自行处理 None)。"""
        loop = self._ensure_tmp_loop()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return fut.result(timeout=timeout)
        except Exception as ex:
            logger.error("call_async failed: %s", ex)
            return None

    def set_page(self, page: ft.Page):
        self._page = page

    def start(self, host: str = "127.0.0.1", port: int = 0) -> tuple:
        """启动 server。返回 (ok, message)"""
        if self.is_running:
            return False, "已在运行"
        if port == 0:
            port = self._find_free_port(8765)
        self._port = port
        self.last_error = None
        # 用 -m uvicorn 模式(更稳,被验证可用)
        cmd = [
            sys.executable,
            "-m", "uvicorn",
            "moa_gateway.server:app",
            "--host", host,
            "--port", str(port),
            "--log-level", "warning",
            "--no-access-log",
        ]
        env = os.environ.copy()
        # 确保 PYTHONPATH 含项目根目录
        root = str(PROJECT_ROOT)
        if root not in env.get("PYTHONPATH", ""):
            env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
        try:
            # 用 DEVNULL 而不是 PIPE(Windows 下 PIPE 会让子进程 stdout 满而阻塞)
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=root,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
        except Exception as e:
            self.last_error = f"Popen 失败: {e}"
            logger.exception("Popen failed")
            return False, self.last_error

        # 不开 log thread(子进程输出丢弃,日志由 server 自己写到文件)

        # 等启动(最多 30s,因为 model_pool 健康检查要时间)
        for i in range(300):  # 30s
            time.sleep(0.1)
            if self._process.poll() is not None:
                # 进程已退出(失败)
                code = self._process.returncode
                self.last_error = f"进程退出 (code={code})"
                # 拿剩余 stdout
                try:
                    out, _ = self._process.communicate(timeout=2)
                    logger.error("[server-exit] %s", out.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
                return False, self.last_error
            if self._is_responsive():
                self.is_running = True
                # 修25: 启动后自动 admin login 拿 token,UI 所有调用都用这个
                self._admin_login()
                # 触发所有注册的 on_started 回调
                for cb in self.on_started_callbacks:
                    try:
                        cb()
                    except Exception as e:
                        logger.error("on_started callback failed: %s", e)
                return True, f"运行在 :{port}"
            if i % 50 == 0 and i > 0:
                # 每 5s 输出状态
                logger.info("[server] 启动中(已等 %ds)...", i // 10)

        # 超时但进程还在跑 — 杀掉
        try:
            self._process.terminate()
            out, _ = self._process.communicate(timeout=3)
            logger.error("[server-timeout] %s", out.decode("utf-8", errors="ignore")[:1000])
        except Exception:
            pass
        self.last_error = "启动超时(30s)"
        return False, self.last_error

    def stop(self) -> bool:
        if not self._process:
            self.is_running = False
            return True
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        except Exception as e:
            logger.warning("stop: %s", e)
        self._process = None
        self.is_running = False
        self._port = None
        return True

    def _read_log(self, proc: subprocess.Popen):
        """(保留以兼容旧调用)用 DEVNULL 后此方法无效"""
        pass

    def _is_responsive(self) -> bool:
        try:
            import socket
            # 先用 socket 测试端口(快,不被 urllib 配置影响)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                s.connect(("127.0.0.1", self._port))
            # 端口通,再 http 请求
            req = urllib.request.Request(f"http://127.0.0.1:{self._port}/health")
            with urllib.request.urlopen(req, timeout=2) as r:
                # urllib 返回 http.client.HTTPResponse,属性是 .status 不是 .status_code
                return r.status == 200
        except socket.timeout:
            return False
        except ConnectionRefusedError:
            return False
        except Exception as e:
            logger.debug("_is_responsive: %s", e)
            return False

    @staticmethod
    def _find_free_port(start: int = 8765) -> int:
        for p in range(start, start + 100):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        return start
