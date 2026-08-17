"""Code execution skill — hardened sandbox (v3.1.1 audit P0 fix).

Security layers (defense in depth):

1. **Authorization (route layer)** — routes/agent.py exposes this tool only
   to admin/operator callers. API-key users get 403 (AGENTS.md rule 8:
   never expose RCE-capable primitives to API-key users).
2. **Hardened AST static analysis** — rejects ALL dunder attribute access
   (``x.__class__``), dunder subscript keys (``x['__builtins__']``), and
   format-string attribute traversal (``"{0.__class__}".format(x)``).
   The v3.1.0 blacklist missed these three classes, which allowed a full
   sandbox escape (json.__dict__['__builtins__'] chain).
3. **Process isolation** — code executes in a separate Python process with
   a scrubbed environment (no inherited API keys), hard wall-clock timeout
   and output cap. See sandbox_exec.run_isolated.
4. **Restricted runtime** — the child applies restricted builtins and an
   import whitelist (pure-computation stdlib modules only).

Honest limitation: a malicious *admin* who defeats layers 1-2 can run OS
commands inside the child process (no containers on this deployment target).
Non-admin callers cannot reach this code path at all, and the gateway
process itself is never exposed to the snippet.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from ..sandbox_exec import DEFAULT_TIMEOUT, run_isolated

logger = logging.getLogger(__name__)

# Thread pool manages child-process waits without blocking the event loop
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sandbox")
_DEFAULT_TIMEOUT = DEFAULT_TIMEOUT

# ---------------------------------------------------------------------------
# Security: AST-level code sanitization (hardened in v3.1.1)
# ---------------------------------------------------------------------------

# Dangerous function/name references
FORBIDDEN_NAMES = frozenset({
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "dir",
    "open", "input", "breakpoint", "__builtins__",
})

# Import whitelist — only pure-computation modules are allowed.
# v3.1.1 second-round fix: `operator` (attrgetter/methodcaller) and `string`
# (Formatter) were REMOVED — both perform runtime attribute walks that defeat
# the AST-level dunder ban (adversarial review escaped via attrgetter).
ALLOWED_IMPORTS = frozenset({
    "math", "json", "datetime", "collections", "itertools",
    "functools", "re", "random",
    "statistics", "decimal", "fractions", "typing",
    "dataclasses", "enum", "copy", "textwrap",
})

# v3.1.1 second-round fix: any string literal carrying a dunder token is a
# sandbox-escape building block (attrgetter('__builtins__'), split
# '.__dict__' fragments later concatenated, etc.). Reject them statically.
_DUNDER_TOKEN_RE = re.compile(r"__\w+__")

# Format strings that traverse attributes ("{0.__class__}", "{obj.__mro__}")
# are a classic blacklist-bypass: the AST sees an innocent str constant and
# the attribute walk happens at str.format() runtime. Reject them statically.
_FORMAT_ATTR_RE = re.compile(r"\{[^{}]*\.[^{}]*\}")

# Dunder names that legitimate code may reference explicitly (rare). Every
# other __name__ attribute/subscript is rejected.
_ALLOWED_DUNDERS = frozenset({
    "__len__", "__str__", "__repr__", "__iter__", "__next__",
    "__contains__", "__getitem__", "__bool__", "__abs__", "__round__",
    "__eq__", "__ne__", "__lt__", "__gt__", "__le__", "__ge__",
    "__name__", "__doc__", "__version__",
})


class SandboxViolation(Exception):
    """Raised when user code attempts a forbidden operation."""


def _restricted_import(name: str, *args: object, **kwargs: object) -> object:
    """Import function that only allows whitelisted modules.

    The sandboxed child process applies the same restriction internally;
    this mirror exists so the policy is inspectable/testable in-process.
    """
    module_name = name.split(".", maxsplit=1)[0]
    if module_name not in ALLOWED_IMPORTS:
        raise SandboxViolation(f"Import of '{name}' is not allowed")
    import builtins
    return builtins.__import__(name, *args, **kwargs)  # type: ignore[arg-type]


# Runtime builtin restriction applied inside the sandboxed child (mirrored
# here for introspection and tests — keep in sync with the runner template
# in sandbox_exec.py).
_ALLOWED_BUILTINS: dict[str, object] = {
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


def _is_dunder(name: str) -> bool:
    return (
        isinstance(name, str)
        and len(name) > 4
        and name.startswith("__")
        and name.endswith("__")
    )


class _CodeSanitizer(ast.NodeVisitor):
    """AST visitor that rejects dangerous code patterns."""

    def visit_Attribute(self, node: ast.Attribute) -> None:
        attr = node.attr
        if isinstance(attr, str):
            # v3.1.1: blanket dunder-attribute ban (closes __dict__/__class__
            # style escapes that the old blacklist missed).
            if _is_dunder(attr) and attr not in _ALLOWED_DUNDERS:
                raise SandboxViolation(
                    f"Access to dunder attribute '{attr}' is forbidden in sandbox"
                )
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        # v3.1.1: block string-literal subscript keys that name dunders or
        # forbidden names, e.g. x["__builtins__"], vars["__globals__"].
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            key = sl.value
            if (_is_dunder(key) and key not in _ALLOWED_DUNDERS) or key in FORBIDDEN_NAMES:
                raise SandboxViolation(
                    f"Subscript access to '{key}' is forbidden in sandbox"
                )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # v3.1.1: reject format strings capable of attribute traversal —
        # "{0.__class__}".format(x) / f-string equivalents evaluated at
        # runtime by str.format, invisible to attribute visitors.
        if isinstance(node.value, str):
            if _FORMAT_ATTR_RE.search(node.value):
                raise SandboxViolation(
                    "Format strings with attribute access (e.g. '{0.__class__}') "
                    "are forbidden in sandbox"
                )
            # v3.1.1 second-round: any string literal carrying a dunder token
            # is an escape building block — attrgetter('__builtins__'), or a
            # '.__dict__' fragment concatenated at runtime. Reject statically.
            if _DUNDER_TOKEN_RE.search(node.value):
                raise SandboxViolation(
                    "String literals containing dunder names "
                    "(e.g. '__builtins__') are forbidden in sandbox"
                )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES:
            raise SandboxViolation(
                f"Use of '{node.id}' is forbidden in sandbox"
            )
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module_name = alias.name.split(".")[0]
            if module_name not in ALLOWED_IMPORTS:
                raise SandboxViolation(
                    f"Import of '{alias.name}' is not allowed. "
                    f"Allowed: {sorted(ALLOWED_IMPORTS)}"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            module_name = node.module.split(".")[0]
            if module_name not in ALLOWED_IMPORTS:
                raise SandboxViolation(
                    f"Import from '{node.module}' is not allowed. "
                    f"Allowed: {sorted(ALLOWED_IMPORTS)}"
                )
        self.generic_visit(node)


def sanitize_code(code: str) -> None:
    """Perform AST-level security analysis on user code.

    Raises:
        SandboxViolation: If the code contains forbidden patterns.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxViolation(f"Syntax error in code: {e}") from e

    _CodeSanitizer().visit(tree)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def code_execute(code: str, language: str = "python", timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Execute code in a sandboxed child process and return stdout output.

    Security model (v3.1.1):
    - Layer 1: route-level authorization (admin/operator only)
    - Layer 2: hardened AST static analysis (dunder ban + format traversal)
    - Layer 3: subprocess isolation with scrubbed env + timeout + output cap
    - Layer 4: restricted builtins + import whitelist inside the child

    Args:
        code: The source code to execute.
        language: Programming language (only ``python`` is supported).
        timeout: Maximum execution time in seconds (default 30s, capped 120s).

    Returns:
        Captured stdout output or error message.
    """
    if language.lower() != "python":
        return f"Language '{language}' is not supported. Only 'python' is available."

    logger.info("code_execute: %d chars of %s code", len(code), language)

    # Layer 2: hardened AST static analysis
    try:
        sanitize_code(code)
    except SandboxViolation as e:
        logger.warning("code_execute blocked by sanitizer: %s", e)
        return f"Security violation: {e}"

    timeout = max(1.0, min(float(timeout), 120.0))

    # Layer 3: run in a scrubbed child process (blocking work off the loop)
    loop = asyncio.get_running_loop()
    result = await asyncio.wait_for(
        loop.run_in_executor(
            _EXECUTOR,
            lambda: run_isolated(code, allowed_imports=ALLOWED_IMPORTS, timeout=timeout),
        ),
        timeout=timeout + 15.0,  # outer guard beyond the child's own timeout
    )

    if result.get("ok"):
        output = str(result.get("stdout", ""))
        return output if output else "(code executed successfully, no output)"
    return str(result.get("error", "unknown sandbox error"))
