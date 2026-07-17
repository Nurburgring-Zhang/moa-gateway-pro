"""Tool Input 9-segment Risk Screening (reference S-37 / A-37).

Scans a tool-call's ``arguments`` dict for 9 categories of risky payloads
and emits a list of :class:`Finding` objects with JSON-path location.

Categories
----------
1. SQL injection         (SELECT/UPDATE/DELETE/DROP/UNION/EXEC)
2. Shell command         (``rm -rf``, ``curl ... | sh``, ``eval``)
3. Path traversal        (``../``, ``..\\``, absolute /etc/passwd, C:\\Windows\\System32)
4. Code injection        (``exec()``, ``eval()``, ``__import__``, ``subprocess``)
5. Prompt injection      ("ignore previous", ``system:``, "disregard")
6. URL fetch (SSRF-ish)  (RFC1918, loopback, metadata 169.254.169.254, ``file://``, ``gopher://``)
7. File write            (overwrite known system path)
8. Network exfiltration  (network tool + payload > 1 MiB)
9. Privilege escalation  (``sudo``, ``chmod 777``, ``chown root``)

Public API
----------
- :class:`RiskLevel`     - 5-level enum (SAFE / LOW / MEDIUM / HIGH / BLOCKED)
- :class:`Finding`       - one matched pattern with location and risk
- :func:`screen_input`   - functional one-shot scanner
- :class:`ToolScreener`  - stateful wrapper supporting custom patterns
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

__all__ = [
    "RiskLevel",
    "Finding",
    "CATEGORY_NAMES",
    "DEFAULT_PATTERNS",
    "screen_input",
    "ToolScreener",
]


# --------------------------------------------------------------------------- #
#  Risk levels                                                                #
# --------------------------------------------------------------------------- #


class RiskLevel(str, Enum):
    """Five-step risk ladder for a single tool call's input.

    The ordering is meaningful: ``BLOCKED > HIGH > MEDIUM > LOW > SAFE``.
    """

    SAFE = "safe"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"

    @classmethod
    def order(cls) -> dict[RiskLevel, int]:
        return {
            cls.SAFE: 0,
            cls.LOW: 1,
            cls.MEDIUM: 2,
            cls.HIGH: 3,
            cls.BLOCKED: 4,
        }

    def __ge__(self, other: RiskLevel) -> bool:  # type: ignore[override]
        return self.order()[self] >= self.order()[other]

    def __gt__(self, other: RiskLevel) -> bool:  # type: ignore[override]
        return self.order()[self] > self.order()[other]

    def __le__(self, other: RiskLevel) -> bool:  # type: ignore[override]
        return self.order()[self] <= self.order()[other]

    def __lt__(self, other: RiskLevel) -> bool:  # type: ignore[override]
        return self.order()[self] < self.order()[other]


CATEGORY_NAMES: dict[int, str] = {
    1: "sql_injection",
    2: "shell_command",
    3: "path_traversal",
    4: "code_injection",
    5: "prompt_injection",
    6: "url_fetch_ssrf",
    7: "file_write",
    8: "network_exfiltration",
    9: "privilege_escalation",
}


# --------------------------------------------------------------------------- #
#  Default patterns                                                           #
# --------------------------------------------------------------------------- #
#
#  Each entry: (category, pattern_id, compiled-regex, risk)
#
#  Every category ships with >= 5 patterns; regexes are frozen at import.
# --------------------------------------------------------------------------- #

_RAW_PATTERNS: tuple[tuple[int, str, str, RiskLevel], ...] = (
    # ---- 1) SQL injection ----------------------------------------------------
    (1, "SQL_UNION_SELECT",
     r"(?i)\bunion\b\s+(?:all\s+)?\bselect\b", RiskLevel.HIGH),
    (1, "SQL_DROP_TABLE",
     r"(?i)\bdrop\s+(?:table|database|schema|index|view)\b", RiskLevel.BLOCKED),
    (1, "SQL_DELETE_WHERE",
     r"(?i)\bdelete\s+from\s+\w+\s*(?:where)?", RiskLevel.HIGH),
    (1, "SQL_UPDATE_SET",
     r"(?i)\bupdate\s+\w+\s+set\s+\w+\s*=", RiskLevel.HIGH),
    (1, "SQL_EXEC_SP",
     r"(?i)\bexec(?:ute)?\s+(?:sp_|xp_)\w+", RiskLevel.BLOCKED),
    (1, "SQL_TAUTOLOGY",
     r"(?i)'\s*or\s+['\d]+\s*=\s*['\d]+", RiskLevel.MEDIUM),
    (1, "SQL_COMMENT_TRAIL",
     r"(?i)--\s*$.*|/\*.*?\*/", RiskLevel.LOW),

    # ---- 2) Shell command ----------------------------------------------------
    (2, "SHELL_RM_RF",
     r"\brm\s+(?:-[a-zA-Z]*\s+)*-?[a-zA-Z]*[rR][fF]\b", RiskLevel.BLOCKED),
    (2, "SHELL_CURL_PIPE_SH",
     r"\bcurl\b[^\n|]*\|\s*(?:ba)?sh\b", RiskLevel.BLOCKED),
    (2, "SHELL_WGET_PIPE_SH",
     r"\bwget\b[^\n|]*\|\s*(?:ba)?sh\b", RiskLevel.BLOCKED),
    (2, "SHELL_EVAL",
     r"\beval\s*[\(\s]", RiskLevel.HIGH),
    (2, "SHELL_DD",
     r"\bdd\s+if=\S+\s+of=\S+", RiskLevel.HIGH),
    (2, "SHELL_FORK_BOMB",
     r":\(\)\s*\{\s*:\|:&\s*\};\s*:", RiskLevel.BLOCKED),
    (2, "SHELL_NC_REVERSE",
     r"\bnc(?:at)?\s+-[a-zA-Z]*e\b[^\n]*\d+\.\d+\.\d+\.\d+", RiskLevel.BLOCKED),

    # ---- 3) Path traversal ---------------------------------------------------
    (3, "PATH_DOTDOT_SLASH",
     r"(?:\.\./){2,}", RiskLevel.HIGH),
    (3, "PATH_DOTDOT_BACKSLASH",
     r"(?:\.\.\\){2,}", RiskLevel.HIGH),
    (3, "PATH_ABS_ETC_PASSWD",
     r"/etc/(?:passwd|shadow|hosts|sudoers)\b", RiskLevel.BLOCKED),
    (3, "PATH_ABS_WINDOWS_SYSTEM",
     r"[A-Za-z]:\\Windows\\System32\\", RiskLevel.BLOCKED),
    (3, "PATH_PROC_SELF",
     r"/proc/self/(?:environ|cmdline|maps)\b", RiskLevel.HIGH),
    (3, "PATH_DEV_NULL",
     r"/dev/(?:null|zero|random)\b", RiskLevel.LOW),

    # ---- 4) Code injection ---------------------------------------------------
    (4, "PY_EXEC_CALL",
     r"\bexec\s*\(", RiskLevel.HIGH),
    (4, "PY_EVAL_CALL",
     r"\beval\s*\(", RiskLevel.HIGH),
    (4, "PY_DUNDER_IMPORT",
     r"__import__\s*\(", RiskLevel.HIGH),
    (4, "PY_SUBPROCESS",
     r"\bsubprocess\.(?:Popen|run|call|check_output)\s*\(", RiskLevel.HIGH),
    (4, "PY_OS_SYSTEM",
     r"\bos\.system\s*\(", RiskLevel.HIGH),
    (4, "JS_FUNCTION_CONSTRUCTOR",
     r"\bnew\s+Function\s*\(", RiskLevel.HIGH),
    (4, "PY_PICKLE_LOADS",
     r"\bpickle\.loads?\s*\(", RiskLevel.MEDIUM),

    # ---- 5) Prompt injection -------------------------------------------------
    (5, "PI_IGNORE_PREVIOUS",
     r"(?i)ignore\s+(?:all\s+)?(?:the\s+)?previous", RiskLevel.HIGH),
    (5, "PI_DISREGARD",
     r"(?i)\bdisregard\s+(?:the\s+)?(?:above|prior|previous|instructions)", RiskLevel.HIGH),
    (5, "PI_SYSTEM_TAG",
     r"(?im)^\s*system\s*:\s*", RiskLevel.MEDIUM),
    (5, "PI_ASSISTANT_TAG",
     r"(?im)^\s*assistant\s*:\s*", RiskLevel.LOW),
    (5, "PI_JAILBREAK",
     r"(?i)\bDAN\s+mode\b|\bjailbreak\b", RiskLevel.HIGH),
    (5, "PI_REVEAL_PROMPT",
     r"(?i)reveal\s+(?:the\s+)?system\s+prompt", RiskLevel.MEDIUM),

    # ---- 6) URL fetch (SSRF) -------------------------------------------------
    (6, "URL_LOOPBACK",
     r"\bhttps?://(?:127\.\d{1,3}\.\d{1,3}\.\d{1,3}|localhost)(?::\d+)?(?:/\S*)?",
     RiskLevel.HIGH),
    (6, "URL_RFC1918",
     r"\bhttps?://(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})(?::\d+)?(?:/\S*)?",
     RiskLevel.HIGH),
    (6, "URL_172_16",
     r"\bhttps?://172\.(?:1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}(?::\d+)?(?:/\S*)?",
     RiskLevel.HIGH),
    (6, "URL_AWS_METADATA",
     r"\bhttps?://169\.254\.169\.254(?:/\S*)?", RiskLevel.BLOCKED),
    (6, "URL_FILE_SCHEME",
     r"\bfile:///\S+", RiskLevel.BLOCKED),
    (6, "URL_GOPHER_SCHEME",
     r"\bgopher://\S+", RiskLevel.BLOCKED),
    (6, "URL_DICT_SCHEME",
     r"\bdict://\S+", RiskLevel.HIGH),

    # ---- 7) File write (system path overwrite) -------------------------------
    (7, "WRITE_ETC",
     r"\b(?:write|save|create|put|fs\.write|open)\b[^\n]{0,40}/etc/\S+",
     RiskLevel.BLOCKED),
    (7, "WRITE_PASSWD",
     r"/etc/(?:passwd|shadow|sudoers|hosts)\b", RiskLevel.BLOCKED),
    (7, "WRITE_WINDOWS_SYSTEM32",
     r"[A-Za-z]:\\Windows\\(?:System32|SysWOW64)\\", RiskLevel.BLOCKED),
    (7, "WRITE_SSH_AUTHORIZED",
     r"\.ssh/authorized_keys\b", RiskLevel.BLOCKED),
    (7, "WRITE_BASHRC",
     r"(?:\.bashrc|\.bash_profile|\.zshrc|\.profile)\b", RiskLevel.HIGH),
    (7, "WRITE_CRON",
     r"/etc/cron\.\S+|/var/spool/cron/\S+", RiskLevel.HIGH),

    # ---- 8) Network exfiltration --------------------------------------------
    # 8 is special: a network tool name (heuristic) + payload > 1 MiB.
    # Detection is performed structurally in ``_check_exfiltration``;
    # the regex below flags explicit "POST" + secret-shape patterns
    # and a few other exfil-style constructions.
    (8, "NET_BIG_BASE64",
     r"[A-Za-z0-9+/]{2048,}={0,2}", RiskLevel.HIGH),
    (8, "NET_POST_SENSITIVE_KEY",
     r"(?i)POST[^\n]{0,80}(?:api[_-]?key|token|password|secret)\s*[=:]",
     RiskLevel.HIGH),
    (8, "NET_HTTPS_TO_SUSPICIOUS",
     r"\bhttps?://(?:pastebin\.com|transfer\.sh|requestbin\.\S+|webhook\.site)\S*",
     RiskLevel.HIGH),
    (8, "NET_BEARER_HEADER",
     r"(?i)\bAuthorization\s*:\s*Bearer\s+[A-Za-z0-9_\-\.=]{20,}",
     RiskLevel.MEDIUM),
    (8, "NET_AWS_KEY_PAIR",
     r"(?i)aws_access_key_id\s*=\s*AKIA[0-9A-Z]{16}",
     RiskLevel.BLOCKED),

    # ---- 9) Privilege escalation --------------------------------------------
    (9, "PRIV_SUDO",
     r"\bsudo\s+(?!-?[lnVHEh])\S+", RiskLevel.HIGH),
    (9, "PRIV_SUDO_SU",
     r"\bsudo\s+su\b", RiskLevel.BLOCKED),
    (9, "PRIV_CHMOD_777",
     r"\bchmod\s+(-[a-zA-Z]+\s+)?777\b", RiskLevel.HIGH),
    (9, "PRIV_CHOWN_ROOT",
     r"\bchown\s+(-[a-zA-Z]+\s+)?root(?::root)?\b", RiskLevel.BLOCKED),
    (9, "PRIV_SETUID_BIT",
     r"\bchmod\s+[0-7]*[4-7][0-7]{2}\b", RiskLevel.HIGH),
    (9, "PRIV_SU_PLAIN",
     r"(?<![\w/])su\s+-\s*\w+", RiskLevel.MEDIUM),
)


@dataclass(frozen=True)
class _Compiled:
    category: int
    pattern_id: str
    regex: re.Pattern  # type: ignore[type-arg]
    risk: RiskLevel


def _build(entries: Sequence[tuple[int, str, str, RiskLevel]]) -> tuple[_Compiled, ...]:
    out: list[_Compiled] = []
    for cat, pid, raw, risk in entries:
        try:
            out.append(_Compiled(category=cat, pattern_id=pid,
                                 regex=re.compile(raw), risk=risk))
        except re.error as exc:  # pragma: no cover - hard build failure
            raise RuntimeError(f"bad pattern {pid}: {exc}") from exc
    return tuple(out)


DEFAULT_PATTERNS: tuple[_Compiled, ...] = _build(_RAW_PATTERNS)


# Network tool heuristic for category 8 size-based rule
_NETWORK_TOOL_HINTS = (
    "http", "https", "fetch", "curl", "wget", "request", "post", "put",
    "send", "upload", "transmit", "exfil", "webhook", "api",
)
_EXFIL_BYTES_THRESHOLD = 1 * 1024 * 1024  # 1 MiB


# --------------------------------------------------------------------------- #
#  Finding                                                                    #
# --------------------------------------------------------------------------- #


@dataclass
class Finding:
    """One matched risky pattern inside a tool-call argument tree."""

    category: int                       # 1-9
    pattern_id: str                     # stable identifier
    matched: str                        # the actual matched substring
    risk: RiskLevel
    location: str                       # JSON path, e.g. "args.cmd" or "args.query.0"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["risk"] = self.risk.value
        d["category_name"] = CATEGORY_NAMES.get(self.category, "unknown")
        return d


# --------------------------------------------------------------------------- #
#  Walker                                                                     #
# --------------------------------------------------------------------------- #


def _walk(value: Any, path: str) -> list[tuple[str, str]]:
    """Recursively yield ``(jsonpath, str_value)`` pairs from a JSON-like tree.

    - dict   -> recurse into values, appending ``.key``
    - list   -> recurse into items, appending ``.index``
    - tuple  -> treat as list
    - str    -> yield as-is
    - other  -> stringify via ``repr`` so non-strings still get scanned

    The walker never raises; it swallows exceptions and yields nothing for
    un-walkable subtrees.
    """
    out: list[tuple[str, str]] = []
    try:
        if isinstance(value, str):
            out.append((path, value))
        elif isinstance(value, Mapping):
            for k, v in value.items():
                key = str(k)
                child = f"{path}.{key}" if path else key
                out.extend(_walk(v, child))
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                child = f"{path}.{i}"
                out.extend(_walk(item, child))
        else:
            out.append((path, repr(value)))
    except Exception:  # pragma: no cover - defensive
        return out
    return out


# --------------------------------------------------------------------------- #
#  Functional API                                                             #
# --------------------------------------------------------------------------- #


def screen_input(
    tool_name: str,
    arguments: Mapping[str, Any],
    patterns: Sequence[_Compiled] | None = None,
) -> list[Finding]:
    """Scan ``arguments`` and return every risky pattern match.

    Parameters
    ----------
    tool_name:
        Free-form tool name. Currently used to detect category-8
        exfiltration when paired with a large payload.
    arguments:
        JSON-like argument tree (dicts / lists / scalars). Every string
        is matched against all 9 categories of regex.
    patterns:
        Optional override list of compiled patterns. Defaults to
        :data:`DEFAULT_PATTERNS`.

    Returns
    -------
    list[Finding]
        Empty when safe. The function never raises; pathological inputs
        are skipped silently.
    """
    findings: list[Finding] = []
    if arguments is None:
        return findings
    pset = DEFAULT_PATTERNS if patterns is None else patterns
    for location, text in _walk(arguments, "args"):
        if not isinstance(text, str) or not text:
            continue
        for p in pset:
            try:
                m = p.regex.search(text)
            except Exception:  # pragma: no cover - regex bugs only
                continue
            if m is None:
                continue
            findings.append(Finding(
                category=p.category,
                pattern_id=p.pattern_id,
                matched=m.group(0),
                risk=p.risk,
                location=location,
            ))
    # Category 8 size-based rule (network tool + >1 MiB string anywhere)
    _check_exfiltration(tool_name, arguments, pset, findings)
    return findings


def _check_exfiltration(
    tool_name: str,
    arguments: Mapping[str, Any],
    patterns: Sequence[_Compiled],
    findings: list[Finding],
) -> None:
    """Append a BLOCKED finding when a network tool carries a >1 MiB payload."""
    try:
        if not tool_name:
            return
        name = tool_name.lower()
        if not any(hint in name for hint in _NETWORK_TOOL_HINTS):
            return
        for location, text in _walk(arguments, "args"):
            if isinstance(text, str) and len(text.encode("utf-8")) > _EXFIL_BYTES_THRESHOLD:
                findings.append(Finding(
                    category=8,
                    pattern_id="NET_LARGE_PAYLOAD",
                    matched=f"<{len(text)} chars>",
                    risk=RiskLevel.BLOCKED,
                    location=location,
                ))
                return  # one exfil signal is enough
    except Exception:  # pragma: no cover
        return


# --------------------------------------------------------------------------- #
#  ToolScreener (stateful wrapper)                                            #
# --------------------------------------------------------------------------- #


class ToolScreener:
    """Reusable screener with optional custom pattern overrides.

    Parameters
    ----------
    custom_patterns:
        Optional override mapping. Supported shapes:

        * ``{category_int: [(pattern_id, regex_str, risk), ...]}``
        * ``{category_int: [regex_str, ...]}``  (auto-id ``"CUSTOM_C{n}_{i}"``)
        * ``[(pattern_id, regex_str, risk), ...]``  (apply across all cats)

        When provided, the override **replaces** :data:`DEFAULT_PATTERNS`
        for the affected categories; unspecified categories keep the
        default. Pass an empty dict to disable every category.
    """

    def __init__(self, custom_patterns: dict[Any, Any] | None = None) -> None:
        self._patterns: tuple[_Compiled, ...] = self._compile(custom_patterns)
        self._stats: dict[str, int] = {
            "scanned": 0,
            "blocked": 0,
            "by_category": {str(i): 0 for i in range(1, 10)},
        }

    # ----- public API ------------------------------------------------------- #

    def screen(self, tool_name: str, arguments: Mapping[str, Any]) -> list[Finding]:
        """Run :func:`screen_input` and update internal counters."""
        try:
            findings = screen_input(tool_name, arguments, self._patterns)
        except Exception:  # pragma: no cover - never crash
            return []
        self._stats["scanned"] += 1
        for f in findings:
            self._stats["by_category"][str(f.category)] = (
                self._stats["by_category"].get(str(f.category), 0) + 1
            )
        if self.should_block(findings):
            self._stats["blocked"] += 1
        return findings

    def should_block(self, findings: list[Finding]) -> bool:
        """Block when there is >=1 BLOCKED or >=2 HIGH finding."""
        if not findings:
            return False
        if any(f.risk == RiskLevel.BLOCKED for f in findings):
            return True
        high = sum(1 for f in findings if f.risk == RiskLevel.HIGH)
        return high >= 2

    def classify(self, findings: list[Finding]) -> RiskLevel:
        """Roll a list of findings up into a single :class:`RiskLevel`."""
        if not findings:
            return RiskLevel.SAFE
        worst = RiskLevel.SAFE
        for f in findings:
            worst = max(worst, f.risk)
        return worst

    @property
    def stats(self) -> dict[str, Any]:
        """Live counters (copy)."""
        out = dict(self._stats)
        out["by_category"] = dict(self._stats["by_category"])
        return out

    @property
    def pattern_count(self) -> int:
        """Number of compiled patterns currently in use."""
        return len(self._patterns)

    # ----- internals -------------------------------------------------------- #

    def _compile(self, custom: dict[Any, Any] | None) -> tuple[_Compiled, ...]:
        if custom is None:
            return DEFAULT_PATTERNS
        by_cat: dict[int, list[_Compiled]] = {i: [] for i in range(1, 10)}
        # split defaults per category, skip ones being overridden
        overridden_cats: set[int] = set()
        entries: list[tuple[int, str, str, RiskLevel]] = []
        if isinstance(custom, dict):
            for key, value in custom.items():
                cat = int(key)
                if cat not in range(1, 10):
                    continue
                # Mark this category as overridden even if value is empty,
                # so callers can explicitly disable a category by passing [].
                overridden_cats.add(cat)
                if isinstance(value, dict):
                    items = list(value.items())
                else:
                    items = list(value)  # type: ignore[arg-type]
                for idx, item in enumerate(items):
                    if isinstance(item, tuple) and len(item) >= 3:
                        pid, raw, risk = item[0], item[1], item[2]
                        if not isinstance(risk, RiskLevel):
                            risk = RiskLevel(str(risk))
                    else:
                        pid = f"CUSTOM_C{cat}_{idx}"
                        raw = str(item)
                        risk = RiskLevel.MEDIUM
                    entries.append((cat, str(pid), str(raw), risk))
        elif isinstance(custom, (list, tuple)):
            for idx, item in enumerate(custom):  # type: ignore[union-attr]
                if not (isinstance(item, tuple) and len(item) >= 3):
                    continue
                pid, raw, risk = item[0], item[1], item[2]
                cat = int(item[3]) if len(item) >= 4 else 1
                overridden_cats.add(cat)
                if not isinstance(risk, RiskLevel):
                    risk = RiskLevel(str(risk))
                entries.append((cat, str(pid), str(raw), risk))
        # defaults for non-overridden cats
        for p in DEFAULT_PATTERNS:
            if p.category not in overridden_cats:
                by_cat[p.category].append(p)
        for cat, pid, raw, risk in entries:
            try:
                by_cat[cat].append(
                    _Compiled(category=cat, pattern_id=pid,
                              regex=re.compile(raw), risk=risk)
                )
            except re.error as exc:
                raise RuntimeError(f"bad custom pattern {pid}: {exc}") from exc
        merged: list[_Compiled] = []
        for i in range(1, 10):
            merged.extend(by_cat[i])
        return tuple(merged)


# --------------------------------------------------------------------------- #
#  CLI (optional)                                                             #
# --------------------------------------------------------------------------- #


def _cli(argv: list[str] | None = None) -> int:  # pragma: no cover
    import argparse
    import json as _json
    parser = argparse.ArgumentParser(description="Tool-input 9-segment screener")
    parser.add_argument("--tool", default="unknown")
    parser.add_argument("--args", default="{}",
                        help="JSON-encoded arguments object")
    args = parser.parse_args(argv)
    try:
        parsed = __import__("json").loads(args.args)
    except Exception as exc:
        print(f"bad --args JSON: {exc}")
        return 2
    scr = ToolScreener()
    findings = scr.screen(args.tool, parsed)
    out = {
        "tool": args.tool,
        "findings": [f.to_dict() for f in findings],
        "classify": scr.classify(findings).value,
        "block": scr.should_block(findings),
        "stats": scr.stats,
    }
    print(_json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    _sys.exit(_cli())
