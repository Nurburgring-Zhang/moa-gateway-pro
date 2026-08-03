"""Code execution skill — safe Python execution in a restricted namespace.

Security layers:
1. AST-level static analysis blocks dangerous patterns before execution
2. Restricted builtins prevent access to dangerous functions at runtime
3. Import whitelist restricts available modules
4. Thread pool isolation + timeout (prevents event loop blocking & DoS)
"""

from __future__ import annotations

import ast
import asyncio
import io
import logging
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout

logger = logging.getLogger(__name__)

# Thread pool for sandboxed code execution (isolated from event loop)
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sandbox")
_DEFAULT_TIMEOUT = 10.0  # seconds

# ---------------------------------------------------------------------------
# Security: AST-level code sanitization
# ---------------------------------------------------------------------------

# Dangerous attribute names that enable sandbox escape
FORBIDDEN_ATTRS = frozenset({
    "__subclasses__", "__mro__", "__globals__", "__code__",
    "__import__", "__builtins__", "__class__", "__bases__",
    "__getattr__", "__setattr__", "__delattr__",
    "__init_subclass__", "__set_name__", "__reduce__",
    "__reduce_ex__", "__spec__", "__loader__",
})

# Dangerous function/name references
FORBIDDEN_NAMES = frozenset({
    "exec", "eval", "compile", "__import__", "globals", "locals",
    "getattr", "setattr", "delattr", "vars", "dir",
    "open", "input", "breakpoint", "__builtins__",
})

# Import whitelist — only these top-level modules are allowed
ALLOWED_IMPORTS = frozenset({
    "math", "json", "datetime", "collections", "itertools",
    "functools", "operator", "string", "re", "random",
    "statistics", "decimal", "fractions", "typing",
    "dataclasses", "enum", "copy", "textwrap",
})


class SandboxViolation(Exception):
    """Raised when user code attempts a forbidden operation."""


class _CodeSanitizer(ast.NodeVisitor):
    """AST visitor that rejects dangerous code patterns."""

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.attr, str) and node.attr in FORBIDDEN_ATTRS:
            raise SandboxViolation(
                f"Access to '{node.attr}' is forbidden in sandbox"
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

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            if node.func.attr in FORBIDDEN_ATTRS:
                raise SandboxViolation(
                    f"Call to '.{node.func.attr}()' is forbidden"
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
# Restricted import function
# ---------------------------------------------------------------------------

def _restricted_import(name: str, *args: object, **kwargs: object) -> object:
    """Import function that only allows whitelisted modules."""
    module_name = name.split(".")[0]
    if module_name not in ALLOWED_IMPORTS:
        raise SandboxViolation(f"Import of '{name}' is not allowed")
    import builtins
    return builtins.__import__(name, *args, **kwargs)


# ---------------------------------------------------------------------------
# Allowed builtins (runtime layer)
# ---------------------------------------------------------------------------

_ALLOWED_BUILTINS = {
    "abs": abs,
    "all": all,
    "any": any,
    "ascii": ascii,
    "bin": bin,
    "bool": bool,
    "bytearray": bytearray,
    "bytes": bytes,
    "callable": callable,
    "chr": chr,
    "complex": complex,
    "dict": dict,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "hasattr": hasattr,
    "hash": hash,
    "hex": hex,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
    "__import__": _restricted_import,
}

_SAFE_IMPORTS = {
    "math": __import__("math"),
    "json": __import__("json"),
    "re": __import__("re"),
    "statistics": __import__("statistics"),
    "collections": __import__("collections"),
    "itertools": __import__("itertools"),
    "functools": __import__("functools"),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def code_execute(code: str, language: str = "python", timeout: float = _DEFAULT_TIMEOUT) -> str:
    """Execute code and return stdout output.

    Security model:
    - Layer 1: AST static analysis rejects dangerous patterns
    - Layer 2: Restricted builtins prevent runtime escapes
    - Layer 3: Import whitelist blocks unauthorized modules
    - Layer 4: Thread pool isolation + timeout (prevents DoS)

    Args:
        code: The source code to execute.
        language: Programming language (only ``python`` is supported).
        timeout: Maximum execution time in seconds (default 10s).

    Returns:
        Captured stdout output or error message.
    """
    if language.lower() != "python":
        return f"Language '{language}' is not supported. Only 'python' is available."

    logger.info("code_execute: %d chars of %s code", len(code), language)

    # Layer 1: AST-level static analysis
    try:
        sanitize_code(code)
    except SandboxViolation as e:
        return f"Security violation: {e}"

    # Layer 4: Execute in isolated thread pool with timeout
    def _run_in_sandbox() -> str:
        """Synchronous sandbox execution (runs in thread pool)."""
        safe_globals: dict = {"__builtins__": _ALLOWED_BUILTINS}
        safe_globals.update(_SAFE_IMPORTS)

        stdout_buf = io.StringIO()

        try:
            with redirect_stdout(stdout_buf):
                exec(compile(code, "<agent_code>", "exec"), safe_globals)  # noqa: S102
            output = stdout_buf.getvalue()
            if not output:
                output = "(code executed successfully, no output)"
            return output
        except SandboxViolation as e:
            return f"Security violation: {e}"
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            return f"Execution error:\n{tb}"

    try:
        loop = asyncio.get_running_loop()
        result = await asyncio.wait_for(
            loop.run_in_executor(_EXECUTOR, _run_in_sandbox),
            timeout=timeout,
        )
        return result
    except asyncio.TimeoutError:
        return f"Execution timed out after {timeout}s"
