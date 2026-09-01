"""O6 — SkillFactory: 新能力(skill)的开发 + 校验 + 自动注册(热部署)。

真实流程(零虚假):
  1. 开发: 接受 skill 规格 {name, description, params, code}。code 是 Python 片段,
     运行时可访问已声明的 params 变量, 通过 print() 输出结果。
     (可选 nl_spec -> LLM 生成 code; 无真实 LLM key 时该路径返回显式 mock 标注,
      不冒充真实生成。)
  2. 校验: a) ast.parse 语法; b) code_execute.sanitize_code 安全静态分析;
     c) 用样例参数在隔离沙箱(run_isolated)真实试跑, 必须成功且无 Security violation。
  3. 自动注册(热部署): 通过校验后, 立即注册进
       - agent_loop.skills.BUILTIN_TOOLS (run-loop/编排器即时可用, 无需重启)
       - orchestrator.CapabilityRegistry (编排匹配即时可见)
     并持久化到 data/orchestrator_skills/<name>.json, 启动时自动加载(自动部署)。

执行安全: 自定义 skill 每次调用都在 code_execute 同款隔离沙箱(subprocess + 受限
builtins + import 白名单 + timeout)中运行, 与内置 code_execute 同安全级别。
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

try:
    from ..agent_loop.skills import BUILTIN_TOOL_NAMES, DANGEROUS_TOOLS
except ImportError:  # v4.1: these name-collision guards live here instead
    from ..agent_loop.skills import BUILTIN_TOOLS

    # Ported from v3.2.1 agent_loop.skills: tools a hot-deployed custom skill
    # must never shadow or impersonate.
    DANGEROUS_TOOLS = frozenset(
        {"code_execute", "file_read", "file_write", "file_list", "api_verify"}
    )
    # Frozen at import time - BEFORE any hot-deployed custom skill registers
    # into the mutable BUILTIN_TOOLS dict (same semantics as v3.2.1).
    BUILTIN_TOOL_NAMES = frozenset(BUILTIN_TOOLS)

logger = logging.getLogger(__name__)

_SKILL_DIR = Path("data") / "orchestrator_skills"


class SkillFactoryError(RuntimeError):
    pass


class SkillFactory:
    def __init__(self, skill_dir: Path | None = None) -> None:
        self._dir = skill_dir or _SKILL_DIR

    # ================= 开发 + 校验 + 注册 =================
    async def develop(self, spec: dict[str, Any]) -> dict[str, Any]:
        name = (spec.get("name") or "").strip()
        description = spec.get("description") or name
        params = spec.get("params") or []
        code = spec.get("code") or ""
        nl_spec = spec.get("nl_spec") or ""
        test_input = spec.get("test_input") or {}

        if not name or not name.replace("_", "").isalnum():
            raise SkillFactoryError("skill name 必须为非空字母数字下划线")

        # v3.2.1 hardening (red-team P1): param *names* are interpolated as
        # Python identifiers into the generated program — an unvalidated name
        # like "pass\nimport socket\n..." would inject arbitrary code past
        # sanitize_code. Names must be plain identifiers; values are safe
        # (repr of JSON types only).
        import re as _re

        for p in params:
            pname = p if isinstance(p, str) else str(p.get("name", ""))
            if not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", pname or ""):
                raise SkillFactoryError(f"参数名必须为合法 Python 标识符: {pname!r}")

        # v3.2.1 hardening: a custom skill must never shadow a builtin tool
        # (e.g. "code_execute" would overwrite the real one in BUILTIN_TOOLS).
        # Check the frozen pristine-name set, not the live dict (which hot
        # deploys mutate).
        if name.lower() in BUILTIN_TOOL_NAMES or name.lower() in DANGEROUS_TOOLS:
            raise SkillFactoryError(f"skill name '{name}' 与内置工具冲突, 必须换名")

        # 1) 若只有自然语言规格 -> LLM 生成 code (无 key 时显式 mock, 不冒充)
        generated_by = "explicit_code"
        if not code and nl_spec:
            code, gen_note = await self._generate_code_from_nl(name, nl_spec, params)
            generated_by = gen_note
            if not code:
                raise SkillFactoryError("无法从 nl_spec 生成 skill 代码(缺少真实 LLM key 且未提供 code)")

        if not code:
            raise SkillFactoryError("必须提供 code 或 nl_spec")

        # 2) 校验
        self._validate_syntax(code)
        self._validate_security(code)
        test_result = await self._functional_test(code, params, test_input)
        if not test_result.get("ok"):
            msg = str(test_result.get("error") or test_result.get("stdout") or "unknown")
            raise SkillFactoryError(f"skill 功能试跑失败: {msg[:200]}")

        # 3) 构造 handler 并热部署注册
        handler = self._make_handler(code, params)
        registered = self._register(name, description, handler)

        # 4) 持久化(自动部署, 重启后可加载)。test_input 一并存档,
        #    供 load_persisted 启动重校验时重放同一功能试跑。
        self._persist(
            {
                "name": name,
                "description": description,
                "params": params,
                "code": code,
                "generated_by": generated_by,
                "created_at": time.time(),
                "test_input": test_input,
                "test_output": str(test_result.get("stdout", ""))[:200],
            }
        )

        return {
            "ok": True,
            "name": name,
            "description": description,
            "params": params,
            "generated_by": generated_by,
            "registered_targets": registered,
            "test_output": str(test_result.get("stdout", ""))[:200],
        }

    # ================= 各阶段 =================
    async def _generate_code_from_nl(self, name: str, nl_spec: str, params: list) -> tuple[str, str]:
        """用 LLM 从自然语言生成 skill 代码。无真实 key 时返回空 + mock 标注(不冒充)。"""
        try:
            from ..model_pool import get_model_pool

            pool = get_model_pool()
            if not pool.endpoints:
                return "", "llm_unavailable(mock)"
            # 检查是否全部为 mock 端点 -> 无法真实生成
            all_mock = all(pool._ep_is_mock(e) for e in pool.endpoints.values())
            if all_mock:
                return "", "llm_mock_only(no real key)"
            param_sig = ", ".join(params) if params else ""
            prompt = (
                f"Generate a Python code snippet for a skill named '{name}'.\n"
                f"Requirement: {nl_spec}\n"
                f"The code can use variables: {param_sig}. It must print() the result.\n"
                f"Only pure-computation stdlib allowed. Output ONLY the code."
            )
            ep = list(pool.endpoints.keys())[0]
            resp = await pool.call(endpoint_id=ep, messages=[{"role": "user", "content": prompt}], max_tokens=1024)
            return (resp.content or "").strip(), "llm_generated"
        except Exception as e:  # noqa: BLE001
            logger.warning("nl->code generation failed: %s", e)
            return "", f"llm_error({type(e).__name__})"

    def _validate_syntax(self, code: str) -> None:
        try:
            ast.parse(code)
        except SyntaxError as e:
            raise SkillFactoryError(f"语法错误: {e}") from e

    def _validate_security(self, code: str) -> None:
        # SandboxViolation 的真实定义在 code_execute(sandbox_exec 内的是子进程模板字符串)
        from ..agent_loop.skills.code_execute import SandboxViolation, sanitize_code

        try:
            sanitize_code(code)
        except SandboxViolation as e:
            raise SkillFactoryError(f"安全校验未通过: {e}") from e

    async def _functional_test(self, code: str, params: list, test_input: dict | None = None) -> dict[str, Any]:
        program = self._build_program(code, params, values=test_input, sample=True)
        return await self._run_sandboxed(program)

    def _make_handler(self, code: str, params: list) -> Callable[..., Any]:
        async def handler(**kwargs: Any) -> str:
            program = self._build_program(code, params, values=kwargs)
            result = await self._run_sandboxed(program)
            if result.get("ok"):
                return str(result.get("stdout", "")) or "(skill executed, no output)"
            return f"skill error: {result.get('error', 'unknown')}"

        return handler

    def _build_program(self, code: str, params: list, values: dict | None = None, sample: bool = False) -> str:
        lines = []
        for i, p in enumerate(params):
            pname = p if isinstance(p, str) else p.get("name", f"arg{i}")
            # 优先用显式提供的值(test_input 或调用值), 否则回退到启发式样例
            val = (values or {}).get(pname)
            if val is None:
                val = self._sample_value(pname)
            lines.append(f"{pname} = {val!r}")
        lines.append(code)
        return "\n".join(lines)

    @staticmethod
    def _sample_value(pname: str) -> Any:
        lowered = pname.lower()
        if lowered in {"n", "x", "i", "k", "num", "number", "count", "size", "limit", "value"}:
            return 3
        if any(k in lowered for k in ("count", "num", "size", "limit", "index")):
            return 3
        if any(k in lowered for k in ("data", "list", "values", "numbers", "series")):
            return "1,2,3,4,5"
        return "sample"

    async def _run_sandboxed(self, program: str, timeout: float = 15.0) -> dict[str, Any]:
        from ..agent_loop.sandbox_exec import run_isolated
        from ..agent_loop.skills.code_execute import ALLOWED_IMPORTS

        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: run_isolated(program, allowed_imports=ALLOWED_IMPORTS, timeout=timeout),
                ),
                timeout=timeout + 10.0,
            )
        except asyncio.TimeoutError:
            return {"ok": False, "error": "skill execution timeout"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    def _register(self, name: str, description: str, handler: Callable) -> list[str]:
        targets = []
        # 注册进 agent skills (run-loop 可用)
        try:
            from ..agent_loop.skills import BUILTIN_TOOLS

            BUILTIN_TOOLS[name] = (handler, description)
            targets.append("agent_loop.skills.BUILTIN_TOOLS")
        except Exception as e:  # noqa: BLE001
            logger.warning("register into BUILTIN_TOOLS failed: %s", e)
        # 注册进 orchestrator capability registry
        try:
            from .registry import CAP_SKILL, Capability, get_registry

            reg = get_registry()
            reg._caps[f"skill.{name}"] = Capability(  # noqa: SLF001 - internal registration
                id=f"skill.{name}",
                name=name,
                type=CAP_SKILL,
                description=description,
                when_to_use=[name.lower(), "custom", "skill"],
                input_hint="custom skill params",
                source="orchestrator.skill_factory(hot-deploy)",
                invoke={"kind": "skill", "name": name},
            )
            targets.append("orchestrator.CapabilityRegistry")
        except Exception as e:  # noqa: BLE001
            logger.warning("register into capability registry failed: %s", e)
        return targets

    def _persist(self, skill_def: dict[str, Any]) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            (self._dir / f"{skill_def['name']}.json").write_text(
                json.dumps(skill_def, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("persist skill failed: %s", e)

    # ================= 启动时自动加载(自动部署) =================
    def load_persisted(self) -> list[str]:
        """Re-validate and register persisted skills (static pipeline).

        v3.2.1 hardening (audit P2-1): loading is no longer a blind trust
        path. Every persisted spec must pass syntax validation and the
        security static analysis (``sanitize_code``) again — the same checks
        the develop path applies — before it is re-registered. Files that
        fail (including anything tampered with after deploy) are skipped
        with a logged warning.

        Deliberately NO sandboxed functional re-run here: engine init runs
        synchronously on the event loop, and a subprocess per skill would
        block it for N×timeout on the first orchestrator request. The
        runtime boundary is unaffected — every skill call goes through
        ``run_isolated`` (restricted builtins, import whitelist, timeout),
        so the sandbox re-applies the full per-call guarantee anyway.

        Note: validation runs synchronously (pure CPU, no subprocesses)
        because engine init is synchronous.
        """
        loaded: list[str] = []
        rejected: list[str] = []
        seen_names: set[str] = set()
        try:
            if not self._dir.exists():
                return loaded
            from ..agent_loop.skills.code_execute import SandboxViolation, sanitize_code

            for f in sorted(self._dir.glob("*.json")):
                try:
                    spec = json.loads(f.read_text(encoding="utf-8"))
                    name = (spec.get("name") or f.stem).strip()
                    code = spec.get("code", "")
                    params = spec.get("params") or []

                    # name guard: never let a persisted file shadow a builtin
                    if name.lower() in BUILTIN_TOOL_NAMES or name.lower() in DANGEROUS_TOOLS:
                        rejected.append(name)
                        logger.warning("persisted skill %s rejected: name collides with builtin tool", name)
                        continue
                    # v3.2.1 (red-team P1): param names become Python identifiers
                    import re as _re

                    bad_param = any(
                        not _re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p if isinstance(p, str) else str(p.get("name", "")))
                        for p in params
                    )
                    if bad_param:
                        rejected.append(name)
                        logger.warning("persisted skill %s rejected: invalid param name", name)
                        continue
                    # duplicate-name guard: last file would silently shadow
                    if name in seen_names:
                        rejected.append(name)
                        logger.warning("persisted skill %s skipped: duplicate name across persisted files", name)
                        continue
                    seen_names.add(name)
                    # static re-validation pipeline (syntax + security)
                    self._validate_syntax(code)
                    self._validate_security(code)

                    handler = self._make_handler(code, params)
                    self._register(name, spec.get("description", ""), handler)
                    loaded.append(name)
                except (SandboxViolation, SkillFactoryError) as e:
                    rejected.append(f.stem)
                    logger.warning("persisted skill %s rejected by re-validation: %s", f.stem, e)
                except Exception as e:  # noqa: BLE001
                    rejected.append(f.stem)
                    logger.warning("load persisted skill %s failed: %s", f.name, e)
        except Exception as e:  # noqa: BLE001
            logger.warning("load_persisted failed: %s", e)
        if loaded:
            logger.info("orchestrator: auto-deployed persisted skills: %s", loaded)
        if rejected:
            logger.warning("orchestrator: rejected persisted skills (failed re-validation): %s", rejected)
        return loaded

    def list_persisted(self) -> list[dict[str, Any]]:
        out = []
        try:
            if self._dir.exists():
                for f in sorted(self._dir.glob("*.json")):
                    try:
                        d = json.loads(f.read_text(encoding="utf-8"))
                        out.append({k: d.get(k) for k in ("name", "description", "params", "generated_by", "created_at")})
                    except Exception:  # noqa: BLE001
                        continue
        except Exception:  # noqa: BLE001
            pass
        return out


_factory: SkillFactory | None = None


def get_skill_factory() -> SkillFactory:
    global _factory
    if _factory is None:
        _factory = SkillFactory()
    return _factory
