"""moa_gateway.capability.cli_registry — 外部 CLI 工具注册表 + 沙箱执行器

把外部命令行程序当作受控工具接入网关 (v3.1.1 审计整改: 替代 channels.py 里
的 sleep+模板字符串模拟)。

安全模型 (白名单制,纵深防御):

1. **可执行文件白名单** — ``settings.cli.allowed_executables`` 列出唯一允许
   spawn 的程序名 (默认 python/python3/git/node/curl)。注册任何 argv[0]
   不在白名单里的工具直接被拒;执行前还会二次校验。白名单只能由 admin 通过
   config.yaml 扩充,任何请求体都改不了。
2. **argv 永远是列表** — 占位符替换只做"整段参数值替换",替换结果仍是单个
   argv 元素,绝不拼接 shell 字符串,绝不经过 shell 解释 (无 ``shell=True``)。
   占位符里塞 ``; rm -rf`` 只会被当成一个字面量参数。
3. **工作目录限制** — 默认 cwd 是 ``data/cli_sandbox`` (自动创建);工具想
   用别的目录必须命中 ``settings.cli.allowed_dirs`` 白名单 (commonpath 校验,
   防 ``..`` 穿越)。
4. **env 清洗** — 子进程环境是白名单重建 (只保留 PATH 等启动必需变量),
   网关机密 (MOA_ADMIN_PASSWORD / MOA_GATEWAY_KEY / JWT secret 等) 绝不继承;
   admin 可通过 env_extra 显式补变量 (密钥类变量名被拒)。
5. **超时 + 输出上限** — 每个工具注册时带 timeout_s 与 max_output_bytes,
   超时由 subprocess 杀掉子进程,输出按字节截断。

持久化走主 Storage (SQLite, WAL),表 ``cli_tools``。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CLIToolSpec",
    "CLIExecResult",
    "CLIRegistry",
    "CLIRegistryError",
    "render_argv",
    "executable_basename",
    "scrubbed_env",
    "get_cli_registry",
]

# 工具名: 字母/数字开头,允许 - _ ,1..64 字符
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
# argv 占位符: {name},name 是标识符
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")
# env_extra 的 key 必须是合法环境变量名
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# env_extra 里拒绝的变量名模式 (机密类)
_ENV_SECRET_RE = re.compile(
    r"(SECRET|PASSWORD|PASSWD|TOKEN|API_?KEY|CREDENTIAL|PRIVATE)", re.IGNORECASE
)
# Windows 可执行文件后缀 (basename 归一化时剥掉)
_EXE_SUFFIXES = (".exe", ".cmd", ".bat", ".com", ".sh")

# 子进程环境白名单: 只保留操作系统启动解释器/查找 PATH 必需的变量。
# 其余一律不继承 — 网关的 API key / admin 密码 / JWT secret 不会泄漏给子进程。
_ENV_KEEP = {
    "PATH",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "COMSPEC",
    "PATHEXT",
    "PROGRAMDATA",
    "LANG",
    "LC_ALL",
    "TZ",
    "TERM",
    "SHELL",
    "GIT_EXEC_PATH",  # git 子命令定位 libexec
    "PYTHONUTF8",
}


class CLIRegistryError(ValueError):
    """注册表策略违规 (白名单拒绝 / 重名 / cwd 越界等)。

    继承 ValueError 使 routes 层的 err_500 把它映射为 400 而不是 500。
    """


@dataclass
class CLIToolSpec:
    """一个已注册的外部 CLI 工具。"""

    name: str
    argv_template: list[str]
    description: str = ""
    cwd: str = ""  # 空 = 默认沙箱目录
    timeout_s: float = 30.0
    max_output_bytes: int = 1_000_000
    env_extra: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CLIExecResult:
    """一次真实子进程执行的完整证据。"""

    ok: bool
    exit_code: int  # 超时时为 -1
    stdout: str
    stderr: str
    latency_ms: int
    timed_out: bool = False
    truncated: bool = False
    error: str = ""
    error_kind: str = ""  # auth / timeout / cli / empty (R-24 四分类)
    argv: list[str] = field(default_factory=list)
    cwd: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============ argv 安全渲染 ============


def executable_basename(argv0: str) -> str:
    """归一化 argv[0]: 取 basename,剥可执行后缀,转小写。"""
    base = os.path.basename(str(argv0).replace("\\", "/"))
    low = base.lower()
    for suf in _EXE_SUFFIXES:
        if low.endswith(suf):
            return low[: -len(suf)]
    return low


def render_argv(template: list[str], params: dict[str, Any] | None) -> list[str]:
    """把 argv 模板里的 ``{key}`` 占位符替换为 params 里的值。

    安全保证:
    - 替换是纯字符串替换,结果仍是 argv 列表里的单个元素 — 值里的空格/分号/
      引号/``$()`` 都保持字面量,永远不会被 shell 解释 (执行走 argv 列表,
      无 shell=True)。
    - 模板里有占位符但 params 缺 key → ValueError (拒绝执行,而不是替换成空)。
    - params 的 key 必须是标识符,防止奇怪键名。
    """
    params = params or {}
    for key in params:
        if not isinstance(key, str) or not _PLACEHOLDER_RE.fullmatch("{" + key + "}"):
            raise CLIRegistryError(f"invalid param key: {key!r}")

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in params:
            raise CLIRegistryError(f"missing param for placeholder {{{key}}}")
        value = params[key]
        if isinstance(value, (dict, list, tuple, set)):
            raise CLIRegistryError(f"param {{{key}}} must be a scalar, got {type(value).__name__}")
        return str(value)

    out: list[str] = []
    for seg in template:
        if not isinstance(seg, str):
            raise CLIRegistryError(f"argv segment must be str, got {type(seg).__name__}")
        out.append(_PLACEHOLDER_RE.sub(_sub, seg))
    return out


def scrubbed_env(env_extra: dict[str, str] | None = None) -> dict[str, str]:
    """重建子进程环境: 白名单保留 + 机密剥离 + admin 显式补充。"""
    env = {k: v for k, v in os.environ.items() if k in _ENV_KEEP}
    # 双保险:任何 MOA_* 网关变量一律不带入子进程
    for k in list(env):
        if k.upper().startswith("MOA_"):
            env.pop(k, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for k, v in (env_extra or {}).items():
        if not _ENV_KEY_RE.match(k):
            raise CLIRegistryError(f"invalid env key: {k!r}")
        if _ENV_SECRET_RE.search(k):
            raise CLIRegistryError(f"env key {k!r} looks like a secret; refused")
        env[k] = str(v)
    return env


def _looks_like_auth_failure(text: str) -> bool:
    low = (text or "").lower()
    return any(
        k in low
        for k in ("unauthorized", "forbidden", "authentication", "permission denied", "401", "403")
    )


def classify_cli_failure(*, exit_code: int, stderr: str, timed_out: bool) -> str:
    """把子进程失败归入 R-24 四分类 (auth / timeout / cli / empty)。"""
    if timed_out:
        return "timeout"
    if _looks_like_auth_failure(stderr):
        return "auth"
    if exit_code == 0 and not stderr:
        return "empty"
    return "cli"


# ============ Registry ============


class CLIRegistry:
    """外部 CLI 工具注册表 (SQLite 持久化) + 沙箱执行器。"""

    def __init__(self, storage: Any | None = None) -> None:
        # 延迟绑定 storage,允许测试先替换单例
        self._storage = storage

    # ---------- 基础设施 ----------

    @property
    def storage(self):
        if self._storage is None:
            from ..storage import get_storage

            self._storage = get_storage()
        return self._storage

    @staticmethod
    def _cfg():
        from ..config import get_settings

        return get_settings().cli

    def sandbox_dir(self) -> Path:
        """默认沙箱工作目录 (data/cli_sandbox),不存在就创建。"""
        from ..config import ROOT_DIR

        cfg = self._cfg()
        p = Path(cfg.sandbox_dir)
        if not p.is_absolute():
            p = ROOT_DIR / p
        p = p.resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _allowed_roots(self) -> list[Path]:
        """cwd 白名单根: 沙箱目录 + settings.cli.allowed_dirs。"""
        from ..config import ROOT_DIR

        cfg = self._cfg()
        roots = [self.sandbox_dir()]
        for d in cfg.allowed_dirs:
            p = Path(os.path.expanduser(d))
            if not p.is_absolute():
                p = ROOT_DIR / p
            roots.append(p.resolve())
        return roots

    def _validate_cwd(self, cwd: str) -> str:
        """校验工具 cwd 在白名单内;空串表示用默认沙箱。返回规范化绝对路径。"""
        if not cwd:
            return ""
        p = Path(os.path.expanduser(cwd))
        if not p.is_absolute():
            from ..config import ROOT_DIR

            p = ROOT_DIR / p
        p = p.resolve()
        for root in self._allowed_roots():
            try:
                if os.path.commonpath([str(p), str(root)]) == str(root):
                    return str(p)
            except ValueError:
                continue  # 不同盘符 (Windows) 等不可比路径
        raise CLIRegistryError(f"cwd not in allowlist: {p}")

    def _validate_executable(self, argv0: str) -> None:
        """argv[0] 白名单校验: 必须是固定值 (无占位符) 且命中 allowed_executables。"""
        if _PLACEHOLDER_RE.search(argv0):
            raise CLIRegistryError("argv[0] must be a fixed executable (no placeholders)")
        cfg = self._cfg()
        allowed = {executable_basename(x) for x in cfg.allowed_executables}
        base = executable_basename(argv0)
        if base not in allowed:
            raise CLIRegistryError(
                f"executable {base!r} not in cli.allowed_executables {sorted(allowed)}"
            )
        # 带路径的 argv[0] 必须真实存在,防止拿白名单名字伪装任意路径
        if ("/" in argv0 or "\\" in argv0) and not Path(os.path.expanduser(argv0)).exists():
            raise CLIRegistryError(f"executable path does not exist: {argv0}")

    # ---------- CRUD ----------

    def register(
        self,
        name: str,
        argv_template: list[str],
        *,
        description: str = "",
        cwd: str = "",
        timeout_s: float | None = None,
        max_output_bytes: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """注册一个新工具。白名单/cwd/超时/输出上限都在这里强制。"""
        cfg = self._cfg()
        if not _NAME_RE.match(name or ""):
            raise CLIRegistryError(f"invalid tool name: {name!r} (need [a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}})")
        if not isinstance(argv_template, (list, tuple)) or not argv_template:
            raise CLIRegistryError("argv_template must be a non-empty list of strings")
        argv_template = [str(x) for x in argv_template]
        self._validate_executable(argv_template[0])
        cwd_norm = self._validate_cwd(cwd or "")
        t = cfg.default_timeout_s if timeout_s is None else float(timeout_s)
        if not (0 < t <= cfg.max_timeout_s):
            raise CLIRegistryError(f"timeout_s must be in (0, {cfg.max_timeout_s}]")
        m = cfg.max_output_bytes if max_output_bytes is None else int(max_output_bytes)
        if not (0 < m <= cfg.max_output_bytes_cap):
            raise CLIRegistryError(
                f"max_output_bytes must be in (0, {cfg.max_output_bytes_cap}]"
            )
        env_extra = dict(env_extra or {})
        scrubbed_env(env_extra)  # 复用校验: 拒绝机密键名/非法键名

        now = time.time()
        spec = CLIToolSpec(
            name=name,
            argv_template=argv_template,
            description=description or "",
            cwd=cwd_norm,
            timeout_s=t,
            max_output_bytes=m,
            env_extra=env_extra,
            created_at=now,
            updated_at=now,
        )
        with self.storage.conn() as c:
            try:
                c.execute(
                    "INSERT INTO cli_tools (name, description, argv_template, cwd, "
                    "timeout_s, max_output_bytes, env_extra, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        spec.name,
                        spec.description,
                        json.dumps(spec.argv_template, ensure_ascii=False),
                        spec.cwd,
                        spec.timeout_s,
                        spec.max_output_bytes,
                        json.dumps(spec.env_extra, ensure_ascii=False),
                        spec.created_at,
                        spec.updated_at,
                    ),
                )
            except Exception as e:
                if "UNIQUE" in str(e).upper():
                    raise CLIRegistryError(f"cli tool already exists: {name}") from e
                raise
        logger.info("cli tool registered: %s argv=%s", name, argv_template)
        return spec.to_dict()

    def get(self, name: str) -> dict[str, Any] | None:
        with self.storage.conn() as c:
            row = c.execute("SELECT * FROM cli_tools WHERE name = ?", (name,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self) -> list[dict[str, Any]]:
        with self.storage.conn() as c:
            rows = c.execute("SELECT * FROM cli_tools ORDER BY name").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def update(
        self,
        name: str,
        *,
        argv_template: list[str] | None = None,
        description: str | None = None,
        cwd: str | None = None,
        timeout_s: float | None = None,
        max_output_bytes: int | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """部分更新;每个提供的字段都重新走完整校验。"""
        cur = self.get(name)
        if cur is None:
            raise CLIRegistryError(f"unknown cli tool: {name}")
        merged = {
            "argv_template": argv_template if argv_template is not None else cur["argv_template"],
            "description": description if description is not None else cur["description"],
            "cwd": cwd if cwd is not None else cur["cwd"],
            "timeout_s": timeout_s if timeout_s is not None else cur["timeout_s"],
            "max_output_bytes": (
                max_output_bytes if max_output_bytes is not None else cur["max_output_bytes"]
            ),
            "env_extra": env_extra if env_extra is not None else cur["env_extra"],
        }
        # 删除旧行后用 register 重建 — 校验逻辑只有一份,不会漂移
        self.delete(name)
        try:
            return self.register(
                name,
                merged["argv_template"],
                description=merged["description"],
                cwd=merged["cwd"],
                timeout_s=merged["timeout_s"],
                max_output_bytes=merged["max_output_bytes"],
                env_extra=merged["env_extra"],
            )
        except CLIRegistryError:
            # 校验失败时恢复原记录,保证 update 原子性
            self.register(
                name,
                cur["argv_template"],
                description=cur["description"],
                cwd=cur["cwd"],
                timeout_s=cur["timeout_s"],
                max_output_bytes=cur["max_output_bytes"],
                env_extra=cur["env_extra"],
            )
            raise

    def delete(self, name: str) -> bool:
        with self.storage.conn() as c:
            cur = c.execute("DELETE FROM cli_tools WHERE name = ?", (name,))
            return cur.rowcount > 0

    @staticmethod
    def _row_to_dict(row) -> dict[str, Any]:
        return {
            "name": row["name"],
            "description": row["description"] or "",
            "argv_template": json.loads(row["argv_template"]),
            "cwd": row["cwd"] or "",
            "timeout_s": row["timeout_s"],
            "max_output_bytes": row["max_output_bytes"],
            "env_extra": json.loads(row["env_extra"] or "{}"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ---------- 执行 ----------

    def build_argv(self, name: str, params: dict[str, Any] | None = None) -> list[str]:
        """渲染 argv 并做执行前的二次白名单校验。"""
        spec = self.get(name)
        if spec is None:
            raise CLIRegistryError(f"unknown cli tool: {name}")
        argv = render_argv(spec["argv_template"], params)
        self._validate_executable(argv[0])
        return argv

    def resolve_cwd(self, spec: dict[str, Any]) -> str:
        """执行时解析 cwd: 空 = 沙箱默认;否则对当前白名单复检 (防配置收缩)。"""
        cwd = spec.get("cwd") or ""
        if not cwd:
            return str(self.sandbox_dir())
        return self._validate_cwd(cwd)

    def execute(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> CLIExecResult:
        """同步真实执行已注册工具 (subprocess.run, argv 列表, 无 shell)。

        子进程失败 (exit!=0 / 超时) 不抛异常,全部转译为 CLIExecResult;
        只有注册表策略违规 (未知工具/白名单拒绝) 才抛 CLIRegistryError。
        """
        spec = self.get(name)
        if spec is None:
            raise CLIRegistryError(f"unknown cli tool: {name}")
        argv = render_argv(spec["argv_template"], params)
        self._validate_executable(argv[0])  # 二次校验: 配置可能已收缩白名单
        cwd = self.resolve_cwd(spec)
        cfg = self._cfg()
        timeout = timeout_s if timeout_s is not None else spec["timeout_s"]
        timeout = max(0.05, min(float(timeout), cfg.max_timeout_s))
        return self._spawn(
            argv,
            cwd=cwd,
            timeout=timeout,
            max_output_bytes=int(spec["max_output_bytes"]),
            env_extra=spec.get("env_extra") or {},
        )

    def execute_argv(
        self,
        argv_template: list[str],
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
        cwd: str = "",
        max_output_bytes: int | None = None,
    ) -> CLIExecResult:
        """一次性执行内联 argv 模板 — 与注册工具同等的校验/沙箱/清洗。

        供 CLIChannel 的内联模式使用:argv[0] 同样必须命中可执行文件白名单,
        cwd 同样受白名单约束 (默认沙箱目录),env 同样清洗。
        """
        if not isinstance(argv_template, (list, tuple)) or not argv_template:
            raise CLIRegistryError("argv_template must be a non-empty list of strings")
        tmpl = [str(x) for x in argv_template]
        self._validate_executable(tmpl[0])
        argv = render_argv(tmpl, params)
        self._validate_executable(argv[0])
        resolved_cwd = self._validate_cwd(cwd) if cwd else str(self.sandbox_dir())
        cfg = self._cfg()
        timeout = float(timeout_s if timeout_s is not None else cfg.default_timeout_s)
        timeout = max(0.05, min(timeout, cfg.max_timeout_s))
        cap = int(max_output_bytes if max_output_bytes is not None else cfg.max_output_bytes)
        return self._spawn(argv, cwd=resolved_cwd, timeout=timeout, max_output_bytes=cap, env_extra={})

    def _spawn(
        self,
        argv: list[str],
        *,
        cwd: str,
        timeout: float,
        max_output_bytes: int,
        env_extra: dict[str, str],
    ) -> CLIExecResult:
        """真实 spawn:subprocess.run + argv 列表 + 清洗 env + 超时杀进程。"""
        env = scrubbed_env(env_extra)
        t0 = time.perf_counter()
        timed_out = False
        try:
            proc = subprocess.run(  # noqa: S603 — argv list, never shell
                argv,
                capture_output=True,
                timeout=timeout,
                cwd=cwd,
                env=env,
                check=False,
            )
            exit_code = proc.returncode
            raw_out = proc.stdout or b""
            raw_err = proc.stderr or b""
        except subprocess.TimeoutExpired as e:
            timed_out = True
            exit_code = -1
            raw_out = (e.stdout or b"") if isinstance(e.stdout, bytes) else b""
            raw_err = (e.stderr or b"") if isinstance(e.stderr, bytes) else b""
            # subprocess.run 超时时已 kill 子进程
        except FileNotFoundError as e:
            latency = int((time.perf_counter() - t0) * 1000)
            logger.warning("cli exec: executable not found: %s", e)
            return CLIExecResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                latency_ms=latency,
                error=f"executable not found: {argv[0]}",
                error_kind="cli",
                argv=argv,
                cwd=cwd,
            )
        except OSError as e:
            latency = int((time.perf_counter() - t0) * 1000)
            logger.error("cli exec: spawn failed: %s", e)
            return CLIExecResult(
                ok=False,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                latency_ms=latency,
                error=f"spawn failed: {e}",
                error_kind="cli",
                argv=argv,
                cwd=cwd,
            )
        latency = int((time.perf_counter() - t0) * 1000)

        truncated = len(raw_out) > max_output_bytes or len(raw_err) > max_output_bytes
        stdout = raw_out[:max_output_bytes].decode("utf-8", "replace")
        stderr = raw_err[:max_output_bytes].decode("utf-8", "replace")

        if timed_out:
            return CLIExecResult(
                ok=False,
                exit_code=-1,
                stdout=stdout,
                stderr=stderr,
                latency_ms=latency,
                timed_out=True,
                truncated=truncated,
                error=f"timed out after {timeout}s (child killed)",
                error_kind="timeout",
                argv=argv,
                cwd=cwd,
            )
        if exit_code != 0:
            kind = classify_cli_failure(exit_code=exit_code, stderr=stderr, timed_out=False)
            return CLIExecResult(
                ok=False,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                latency_ms=latency,
                truncated=truncated,
                error=f"exit code {exit_code}" + (f": {stderr.strip()[:300]}" if stderr.strip() else ""),
                error_kind=kind,
                argv=argv,
                cwd=cwd,
            )
        if not stdout.strip():
            return CLIExecResult(
                ok=False,
                exit_code=0,
                stdout=stdout,
                stderr=stderr,
                latency_ms=latency,
                truncated=truncated,
                error="empty output",
                error_kind="empty",
                argv=argv,
                cwd=cwd,
            )
        return CLIExecResult(
            ok=True,
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            latency_ms=latency,
            truncated=truncated,
            argv=argv,
            cwd=cwd,
        )

    async def aexecute(
        self,
        name: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> CLIExecResult:
        """异步包装: 线程池里跑同步 subprocess,不阻塞 event loop。"""
        return await asyncio.to_thread(self.execute, name, params, timeout_s=timeout_s)

    async def execute_batch(
        self, items: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """并发执行多个已注册工具,聚合逐路结果。

        每路独立超时 (各自 subprocess timeout),部分失败不影响整体:
        未知工具/策略违规被捕获为该路的失败结果,而不是让整个 batch 抛错。
        返回与输入同序的结果列表。
        """

        async def _one(item: dict[str, Any]) -> dict[str, Any]:
            name = str(item.get("name", ""))
            params = item.get("params") or {}
            timeout_s = item.get("timeout_s")
            try:
                res = await self.aexecute(name, params, timeout_s=timeout_s)
            except CLIRegistryError as e:
                res = CLIExecResult(
                    ok=False,
                    exit_code=-1,
                    stdout="",
                    stderr="",
                    latency_ms=0,
                    error=str(e),
                    error_kind="cli",
                )
            d = res.to_dict()
            d["name"] = name
            return d

        return list(await asyncio.gather(*[_one(it) for it in items]))


_registry: CLIRegistry | None = None


def get_cli_registry() -> CLIRegistry:
    """进程级单例 (storage 延迟绑定,测试可重置)。"""
    global _registry
    if _registry is None:
        _registry = CLIRegistry()
    return _registry
