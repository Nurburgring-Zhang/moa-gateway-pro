"""Protected-block extraction for the compression pipeline.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/preservation.ts``: signal-carrying spans
(code fences, inline code, URLs, identifiers, versions, file paths, error
lines, math, tables, headings) are tombstoned with sentinel placeholders
before any prose rewriting runs, then stitched back verbatim afterwards.
This guarantees structured content is never mangled by the text engines.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

SENTINEL_PREFIX = "\x00OMNI_CAVEMAN"

_FENCED_RE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~", re.MULTILINE)
_FRONTMATTER_RE = re.compile(r"\A---\n[\s\S]*?\n---\n")


@dataclass(frozen=True)
class PreservedBlock:
    placeholder: str
    content: str
    kind: str


def _seed() -> str:
    return "r" + secrets.token_hex(8)


def _ensure_global_flags(flags: int) -> int:
    return flags  # Python re has no per-call lastIndex; patterns are stateless here.


def compile_user_patterns(patterns: list[str] | None) -> list[tuple[re.Pattern, str]]:
    """Compile caller-supplied preserve regexes; invalid ones are ignored."""
    compiled: list[tuple[re.Pattern, str]] = []
    for pattern in patterns or []:
        try:
            compiled.append((re.compile(pattern), "custom"))
        except re.error:
            continue
    return compiled


def _built_in_patterns() -> list[tuple[re.Pattern, str]]:
    return [
        (re.compile(r"\$\$[\s\S]*?\$\$"), "math_block"),
        (re.compile(r"\\\[[\s\S]*?\\\]"), "math_block"),
        (re.compile(r"\$(?![\s\d])(?:\\.|[^$\n\\]){1,160}?(?<!\s)\$(?!\$)"), "math_inline"),
        (re.compile(r"\\begin\{[A-Za-z*]+\}[\s\S]*?\\end\{[A-Za-z*]+\}"), "latex_block"),
        (re.compile(r"^#{1,6}\s+.+$", re.MULTILINE), "markdown_heading"),
        (re.compile(r"^\s*\|.*\|\s*$", re.MULTILINE), "markdown_table"),
        (re.compile(r"`[^`\n]+`"), "inline_code"),
        (re.compile(r"\[[^\]\n]+\]\([^) \n]+(?:\s+\"[^\"]*\")?\)"), "markdown_link"),
        (re.compile(r"\bhttps?://[^\s)\]\"'>]+", re.IGNORECASE), "url"),
        (re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b"), "const_case"),
        (re.compile(r"\bprocess\.env\.[A-Za-z_][A-Za-z0-9_]*\b"), "env_var"),
        (re.compile(r"\$[A-Z_][A-Z0-9_]*\b"), "env_var"),
        (re.compile(r"\b\d+(?:\.\d+){1,3}(?:[-+][A-Za-z0-9.-]+)?\b"), "version"),
        (re.compile(r"\b[a-zA-Z_$][\w$]*(?:\.[a-zA-Z_$][\w$]*)+\(\)?"), "dotted_identifier"),
        (re.compile(r"\b[A-Za-z_$][\w$]*\s*\([^()\n]*\)"), "function_call"),
        (
            re.compile(r"(?:^|\s)(?:\.{0,2}/[A-Za-z0-9_@./-]+|[A-Za-z]:\\[A-Za-z0-9_.\\/-]+)"),
            "file_path",
        ),
        (
            re.compile(
                r"\b(?:TypeError|ReferenceError|SyntaxError|RangeError|URIError|EvalError"
                r"|Error|Exception):[^\n]+"
            ),
            "error_message",
        ),
    ]


def extract_preserved_blocks(
    text: str, extra_patterns: list[str] | None = None
) -> tuple[str, list[PreservedBlock]]:
    """Replace every protected span with a unique sentinel placeholder.

    Returns ``(text_with_placeholders, blocks)``. Restoring via
    :func:`restore_preserved_blocks` reproduces the protected spans
    byte-for-byte.
    """
    blocks: list[PreservedBlock] = []
    seed = _seed()
    counter = 0

    def add_block(content: str, kind: str) -> str:
        nonlocal counter
        placeholder = f"{SENTINEL_PREFIX}_{seed}_{counter}\x00"
        blocks.append(PreservedBlock(placeholder=placeholder, content=content, kind=kind))
        counter += 1
        return placeholder

    result = text

    fm = _FRONTMATTER_RE.match(result)
    if fm:
        result = add_block(fm.group(0), "frontmatter") + result[fm.end():]

    result = _FENCED_RE.sub(lambda m: add_block(m.group(0), "fenced_code"), result)

    for pattern, kind in [*_built_in_patterns(), *compile_user_patterns(extra_patterns)]:
        def _sub(m: re.Match, _kind: str = kind) -> str:
            matched = m.group(0)
            if not matched or SENTINEL_PREFIX in matched:
                return matched
            return add_block(matched, _kind)

        result = pattern.sub(_sub, result)

    return result, blocks


def restore_preserved_blocks(text: str, blocks: list[PreservedBlock]) -> str:
    """Re-stitch tombstoned blocks verbatim (placeholder -> original content)."""
    result = text
    for block in blocks:
        result = result.replace(block.placeholder, block.content)
    return result


def find_fenced_code_blocks(text: str) -> list[str]:
    return [m.group(0) for m in _FENCED_RE.finditer(text)]


#: Kinds the fidelity gate treats as critical (must survive compression).
CRITICAL_KINDS = frozenset(
    {
        "url",
        "const_case",
        "env_var",
        "version",
        "dotted_identifier",
        "function_call",
        "file_path",
        "inline_code",
    }
)
