"""Code execution skill — safe Python execution in a restricted namespace."""
from __future__ import annotations

import io
import logging
import traceback
from contextlib import redirect_stdout

logger = logging.getLogger(__name__)

# Modules available to executed code
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
    "getattr": getattr,
    "hasattr": hasattr,
    "hash": hash,
    "hex": hex,
    "id": id,
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
    "setattr": setattr,
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


async def code_execute(code: str, language: str = "python") -> str:
    """Execute code and return stdout output.

    Args:
        code: The source code to execute.
        language: Programming language (only ``python`` is supported).

    Returns:
        Captured stdout output or error message.
    """
    if language.lower() != "python":
        return f"Language '{language}' is not supported. Only 'python' is available."

    logger.info("code_execute: %d chars of %s code", len(code), language)

    # Build a restricted namespace
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
    except Exception:  # noqa: BLE001
        tb = traceback.format_exc()
        return f"Execution error:\n{tb}"
