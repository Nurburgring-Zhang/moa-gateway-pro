"""Subprocess-isolated execution runner for the code_execute skill.

Security model (v3.1.1, audit P0 fix):

The previous implementation ran user code via ``exec()`` inside a gateway
thread. A blacklist-style AST sanitizer was escapable (``json.__dict__``
subscript access, ``str.format`` attribute traversal), which yielded full
RCE to any API-key holder.

The v3.1.1 model is defense in depth:

1. **Authorization** — routes/agent.py only exposes ``code_execute`` to
   admin/operator callers (AGENTS.md rule 8: never expose RCE-capable
   primitives to API-key users).
2. **Hardened static analysis** — code_execute.sanitize_code rejects ALL
   dunder attribute access, dunder subscript keys, and format-string
   attribute traversal before anything runs.
3. **Process isolation** — the code runs in a *separate Python process*
   with a scrubbed environment (no inherited API keys), a hard wall-clock
   timeout, and an output size cap. The gateway's event loop, memory and
   credentials are never shared with the child.
4. **Restricted runtime** — the child re-applies restricted builtins and the
   import whitelist, so even inside the child the attack surface is minimal.

Honest limitation: on a host without containers, a malicious *admin* who
bypasses layers 1-2 can still execute OS commands inside the child process.
The guarantee this module provides is: non-admin callers cannot reach this
path at all, and accidental/hostile snippets cannot touch the gateway
process itself (no shared memory, no inherited secrets, bounded time and
output).
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from string import Template

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0  # seconds (hard wall-clock cap for the child)
MAX_OUTPUT_BYTES = 1_000_000  # 1 MB stdout cap
_MAX_CODE_CHARS = 200_000  # reject absurdly large submissions early

# Runner script template — written to a temp file and executed as a child
# process. Self-contained on purpose: it must not import moa_gateway (that
# would drag config/storage into the sandboxed child). Uses string.Template
# ($ placeholders) so the generated Python needs no brace escaping.
_RUNNER_TEMPLATE = Template('''
import io
import json
import sys
import traceback
from contextlib import redirect_stdout

ALLOWED_IMPORTS = $allowed_imports

class SandboxViolation(Exception):
    pass

def _restricted_import(name, *args, **kwargs):
    module_name = name.split(".", maxsplit=1)[0]
    if module_name not in ALLOWED_IMPORTS:
        raise SandboxViolation(f"Import of '{name}' is not allowed")
    # v3.1.1 second-round: hand back the dunder-blocking proxy, never the raw
    # module object (raw modules expose __builtins__/__dict__/__loader__).
    if module_name in _SAFE_IMPORTS:
        return _SAFE_IMPORTS[module_name]
    import builtins
    return _ModuleProxy(builtins.__import__(name, *args, **kwargs))

_ALLOWED_BUILTINS = {
    "abs": abs, "all": all, "any": any, "ascii": ascii, "bin": bin,
    "bool": bool, "bytearray": bytearray, "bytes": bytes, "callable": callable,
    "chr": chr, "complex": complex, "dict": dict, "divmod": divmod,
    "enumerate": enumerate, "filter": filter, "float": float, "format": format,
    "frozenset": frozenset, "hasattr": hasattr, "hash": hash, "hex": hex,
    "int": int, "isinstance": isinstance, "issubclass": issubclass,
    "iter": iter, "len": len, "list": list, "map": map, "max": max,
    "min": min, "next": next, "oct": oct, "ord": ord, "pow": pow,
    "print": print, "range": range, "repr": repr, "reversed": reversed,
    "round": round, "set": set, "slice": slice, "sorted": sorted,
    "str": str, "sum": sum, "tuple": tuple, "type": type, "zip": zip,
    "True": True, "False": False, "None": None,
    "__import__": _restricted_import,
}

_SAFE_IMPORTS = {}

class _ModuleProxy:
    """Runtime backstop: forward normal attribute access to the wrapped module
    but block every dunder attribute. Closes dynamic escape routes the static
    AST layer cannot see (e.g. dunder names assembled with chr() at runtime
    and applied through any attribute-walking primitive)."""

    __slots__ = ("_mod",)

    def __init__(self, mod):
        object.__setattr__(self, "_mod", mod)

    def __getattr__(self, name):
        if isinstance(name, str) and name.startswith("__") and name.endswith("__"):
            raise SandboxViolation(
                f"access to dunder attribute '{name}' is forbidden in sandbox"
            )
        return getattr(object.__getattribute__(self, "_mod"), name)

    def __repr__(self):
        return repr(object.__getattribute__(self, "_mod"))

for _m in ("math", "json", "re", "statistics", "collections",
           "itertools", "functools", "datetime", "decimal", "fractions",
           "textwrap", "enum", "dataclasses", "random", "typing", "copy"):
    if _m in ALLOWED_IMPORTS:
        _SAFE_IMPORTS[_m] = _ModuleProxy(__import__(_m))

def main():
    code_path = sys.argv[1]
    with open(code_path, encoding="utf-8") as f:
        code = f.read()
    safe_globals = {"__builtins__": _ALLOWED_BUILTINS}
    safe_globals.update(_SAFE_IMPORTS)
    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            exec(compile(code, "<agent_code>", "exec"), safe_globals)
        print(json.dumps({"ok": True, "stdout": buf.getvalue()[:$max_out]}))
    except SandboxViolation as e:
        print(json.dumps({"ok": False, "error": f"Security violation: {e}"}))
    except Exception:
        tb = traceback.format_exc()
        print(json.dumps({"ok": False, "error": f"Execution error:\\n{tb}"}))

main()
''')


def _scrubbed_env() -> dict[str, str]:
    """Build a minimal environment for the child process.

    Strips every inherited variable except the handful Python needs to
    start on this OS. No API keys, no tokens, no admin passwords leak
    into the sandboxed child.
    """
    keep = {
        "PATH", "SystemRoot", "SYSTEMROOT", "TEMP", "TMP", "HOME",
        "USERPROFILE", "COMSPEC", "PATHEXT", "LANG", "LC_ALL",
        "PROGRAMDATA", "WINDIR",
    }
    env = {k: v for k, v in os.environ.items() if k in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Never let the child read the parent's .env or config
    env.pop("MOA_ADMIN_PASSWORD", None)
    env.pop("MOA_GATEWAY_KEY", None)
    env.pop("MOA_JWT_SECRET", None)
    return env


def run_isolated(
    code: str,
    *,
    allowed_imports: frozenset[str],
    timeout: float = DEFAULT_TIMEOUT,
    cwd: str | os.PathLike[str] | None = None,
) -> dict[str, object]:
    """Execute *code* in a scrubbed child process.

    Returns a dict: {"ok": bool, "stdout": str} or {"ok": False, "error": str}.
    Never raises for runtime failures — errors are captured in the result so
    the agent loop can feed them back to the LLM.
    """
    if len(code) > _MAX_CODE_CHARS:
        return {"ok": False, "error": f"code too large ({len(code)} chars > {_MAX_CODE_CHARS})"}

    workdir = Path(cwd) if cwd else Path(tempfile.gettempdir()) / "moa-sandbox"
    workdir.mkdir(parents=True, exist_ok=True)

    run_id = uuid.uuid4().hex
    code_file = workdir / f"code_{run_id}.py"
    runner_file = workdir / f"runner_{run_id}.py"

    runner_src = _RUNNER_TEMPLATE.substitute(
        allowed_imports=repr(sorted(allowed_imports)),
        max_out=MAX_OUTPUT_BYTES,
    )
    code_file.write_text(code, encoding="utf-8")
    runner_file.write_text(runner_src, encoding="utf-8")

    try:
        proc = subprocess.run(  # noqa: S603 — argv list, no shell
            [sys.executable, str(runner_file), str(code_file)],
            capture_output=True,
            timeout=timeout,
            env=_scrubbed_env(),
            cwd=str(workdir),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Execution timed out after {timeout:.0f}s (child killed)"}
    except OSError as e:
        logger.error("sandbox child spawn failed: %s", e)
        return {"ok": False, "error": f"sandbox unavailable: {e}"}
    finally:
        for f in (code_file, runner_file):
            try:
                f.unlink(missing_ok=True)
            except OSError:
                pass

    raw_out = (proc.stdout or b"")[:MAX_OUTPUT_BYTES].decode("utf-8", "replace").strip()
    # The runner prints exactly one JSON line on success; take the last line
    # to tolerate any stray output.
    last_line = raw_out.splitlines()[-1] if raw_out else ""
    try:
        payload = json.loads(last_line)
        if isinstance(payload, dict) and "ok" in payload:
            return payload
    except (json.JSONDecodeError, ValueError):
        pass

    stderr_tail = (proc.stderr or b"")[-2000:].decode("utf-8", "replace")
    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"child exited with code {proc.returncode}"
            + (f"\n{stderr_tail}" if stderr_tail else ""),
        }
    return {"ok": False, "error": f"unparseable child output: {raw_out[:300]}"}
