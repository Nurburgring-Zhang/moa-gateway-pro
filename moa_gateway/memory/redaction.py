"""PII redaction for memory payloads — ported from MemoraX Code.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``packages/ts/memorax-code-backend/src/memory/payload-redaction.ts``.

Ported design (real regex + rule redactor, no stubs):

- every rule is a (pattern, kind, priority, optional capture-group, optional
  accept-guard) tuple;
- all rule spans are collected, overlapping spans are merged by priority, and
  each selected span is replaced with ``[REDACTED:<KIND>]``;
- ``has_meaningful_text`` detects payloads that contain nothing but redaction
  placeholders / labels, so empty-after-redaction turns are skipped upstream.

Rule families (MemoraX set + the PII classes required by the gateway spec):
private keys, Authorization/Bearer/JWT tokens, cookies, credential
assignments (env/CLI/query-string/URL-password), vendor API keys, e-mail,
phone numbers (CN mobile + generic long numbers), CN national ID (with real
checksum validation), credit cards (with real Luhn validation), opaque ids
(UUID/hex hashes/high-entropy strings).

All patterns use bounded repetition to avoid catastrophic backtracking.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

REDACTED_PLACEHOLDER = re.compile(r"\[REDACTED:[A-Z_]+\]")
_PLACEHOLDER_ONLY = re.compile(r"^\[REDACTED:[A-Z_]+\]$")

_NON_CONTENT_LABELS = re.compile(
    r"\b(?:proxy-authorization|authorization|bearer|basic|cookie|set-cookie|password|passwd|pwd|"
    r"client[_-]?secret|api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|"
    r"private[_-]?key|credential|secret|token|jwt|email|contact|card|key|export)\b",
    re.IGNORECASE,
)
_PUNCTUATION = re.compile(r'[\s.,;:!?"\'`(){}\[\]<>/\\|=_-]+')

_SENSITIVE_KEY = (
    r"(?:[A-Za-z_$][A-Za-z0-9_$-]*)?(?:password|passwd|pwd|client[-_$]?secret|consumer[-_$]?secret|"
    r"api[-_$]?key|access[-_$]?token|refresh[-_$]?token|auth[-_$]?token|id[-_$]?token|"
    r"private[-_$]?key|secret[-_$]?access[-_$]?key|secret[-_$]?key|credential|secret|token)"
)
_CREDENTIAL_VALUE = (
    r"(?:\[REDACTED:[A-Z_]+\]|\$\{\{[^\r\n]*?\}\}|\{\{[^\r\n]*?\}\}|\$\{[^}\r\n]+\}|\$[A-Za-z_][A-Za-z0-9_]*|"
    r'"(?:\\.|[^"\\\r\n])*"|\'(?:\\.|[^\'\\\r\n])*\'|[^,;#}\]\)\r\n]+)'
)
_SAFE_CREDENTIAL_VALUE = re.compile(
    r"^(?:\[REDACTED:[A-Z_]+\]|\$\{\{[^\r\n]*\}\}|\{\{[^\r\n]*\}\}|\$\{[A-Z_][A-Z0-9_]*\}|\$[A-Z_][A-Z0-9_]*|"
    r"(?:process\.)?env\.[A-Z_][A-Z0-9_]*|<[^>]+>|--\S+|x{2,}|\*{2,}|\.{3}|sk[_-]x+|example|sample|"
    r"change-?me|(?:your|replace)[-_][a-z0-9_-]+|string|str|number|boolean|unknown|any|null|undefined|none)$",
    re.IGNORECASE,
)


class _Rule(NamedTuple):
    pattern: re.Pattern[str]
    kind: str
    priority: int
    group: int | None = None
    accept: Callable[[str], bool] | None = None


class _Span(NamedTuple):
    start: int
    end: int
    kind: str
    priority: int


def _is_safe_credential_value(raw: str) -> bool:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return not value or bool(_SAFE_CREDENTIAL_VALUE.match(value))


def _not_safe_credential(value: str) -> bool:
    return not _is_safe_credential_value(value)


def _looks_like_high_entropy_id(value: str) -> bool:
    lowercase = uppercase = digits = transitions = 0
    letter_run = longest_run = 0
    previous_digit: bool | None = None
    unique: set[str] = set()
    for ch in value:
        is_digit = ch.isdigit()
        if is_digit:
            digits += 1
            letter_run = 0
        else:
            if ch.islower():
                lowercase += 1
            else:
                uppercase += 1
            letter_run += 1
            longest_run = max(longest_run, letter_run)
        if previous_digit is not None and previous_digit != is_digit:
            transitions += 1
        previous_digit = is_digit
        unique.add(ch)
    return (
        lowercase >= 2
        and uppercase >= 2
        and digits >= 2
        and transitions >= 4
        and longest_run <= 5
        and len(unique) >= 12
    )


def _phone_accept(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if len(digits) < 9:
        return False
    # 13-19 digits (plain or space/hyphen-grouped) is the credit-card domain:
    # the Luhn-validated CREDIT_CARD rule owns it, and the generic phone
    # sweep defers so the checksum decision is never overridden (an
    # order-number-shaped run that fails Luhn stays untouched).
    if 13 <= len(digits) <= 19 and re.fullmatch(r"\d+(?:[ -]\d+)*", value.strip()):
        return False
    normalized = value.strip().replace("(", "").replace(")", "")
    if re.match(r"^(?:19|20)\d{2}-\d{1,2}-\d{1,2}(?:[ T-]\d{1,2})?$", normalized):
        return False
    return not re.match(r"^(?:19|20)\d{2}\s+\d{1,2}\s+\d{1,2}(?:\s+\d{1,2})?$", normalized)


_CN_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_CN_ID_CHECK_CHARS = "10X98765432"


def _cn_id_card_accept(value: str) -> bool:
    """Real GB 11643-1999 checksum validation for 18-digit CN national IDs."""
    if len(value) != 18:
        return False
    body, check = value[:17], value[17].upper()
    total = sum(int(d) * w for d, w in zip(body, _CN_ID_WEIGHTS, strict=True))
    return _CN_ID_CHECK_CHARS[total % 11] == check


def _luhn_valid(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def _credit_card_accept(value: str) -> bool:
    digits = re.sub(r"[ -]", "", value)
    if not (13 <= len(digits) <= 19):
        return False
    return _luhn_valid(digits)


def _email_accept(value: str) -> bool:
    local, _, _domain = value.partition("@")
    return 1 <= len(local) <= 64


RULES: tuple[_Rule, ...] = (
    _Rule(
        re.compile(
            r"-----BEGIN ((?:(?:RSA|EC|DSA|OPENSSH|ENCRYPTED) )?PRIVATE KEY|PGP PRIVATE KEY BLOCK)-----"
            r"[\s\S]*?(?:-----END \1-----|$)",
            re.IGNORECASE,
        ),
        "PRIVATE_KEY",
        100,
    ),
    _Rule(
        re.compile(r"(?:^|[\r\n])[ \t]*(?:Proxy-)?Authorization[ \t]*:[ \t]*([^ \t\r\n][^\r\n]*)", re.IGNORECASE | re.MULTILINE),
        "AUTH_TOKEN",
        95,
        group=1,
    ),
    _Rule(
        re.compile(r"(?:^|[^A-Za-z0-9_-])(?:Bearer|Basic)\s+[\"']?([A-Za-z0-9._~+/=-]{16,})(?![A-Za-z0-9._~+/=-])", re.IGNORECASE),
        "AUTH_TOKEN",
        94,
        group=1,
    ),
    _Rule(
        re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{16,}\b"),
        "AUTH_TOKEN",
        94,
    ),
    _Rule(
        re.compile(r"(?:^|[\r\n])[ \t]*(?:Set-Cookie|Cookie)\s*:\s*([^\r\n]+)", re.IGNORECASE | re.MULTILINE),
        "COOKIE",
        92,
        group=1,
    ),
    _Rule(
        re.compile(
            rf"(?:^|[\r\n;,{{}}])[ \t]*(?:-[ \t]+)?(?:(?:export|const|let|var)[ \t]+)*[\"']?{_SENSITIVE_KEY}[\"']?"
            rf"[ \t]*[:=][ \t]*({_CREDENTIAL_VALUE})",
            re.IGNORECASE | re.MULTILINE,
        ),
        "CREDENTIAL",
        85,
        group=1,
        accept=_not_safe_credential,
    ),
    _Rule(
        re.compile(rf"--{_SENSITIVE_KEY}(?![A-Za-z0-9_$-])(?:[ \t]+|=[ \t]*)({_CREDENTIAL_VALUE})", re.IGNORECASE),
        "CREDENTIAL",
        85,
        group=1,
        accept=_not_safe_credential,
    ),
    _Rule(
        re.compile(rf"[?&]{_SENSITIVE_KEY}(?![A-Za-z0-9_$-])=([^&#\s\"'<>]+)", re.IGNORECASE),
        "CREDENTIAL",
        85,
        group=1,
        accept=_not_safe_credential,
    ),
    _Rule(
        re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s/:@]*:([^\s/@]+)@"),
        "CREDENTIAL",
        85,
        group=1,
        accept=_not_safe_credential,
    ),
    _Rule(re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}[A-Za-z0-9]\b"), "API_KEY", 80),
    _Rule(re.compile(r"\bsk_[A-Za-z0-9_-]{8,}\b"), "API_KEY", 80),
    _Rule(re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "API_KEY", 80),
    _Rule(re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "API_KEY", 80),
    _Rule(re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}[A-Za-z0-9]\b"), "API_KEY", 80),
    _Rule(re.compile(r"\b(?:(?:sk|rk)_live_[A-Za-z0-9]{16,}|whsec_[A-Za-z0-9]{16,})\b"), "API_KEY", 80),
    _Rule(re.compile(r"\bglpat-[A-Za-z0-9_-]{16,}[A-Za-z0-9]\b"), "API_KEY", 80),
    _Rule(re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "API_KEY", 80),
    _Rule(re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"), "API_KEY", 80),
    _Rule(
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,63}(?![A-Za-z0-9-]|\.[A-Za-z0-9])"
        ),
        "EMAIL",
        70,
        accept=_email_accept,
    ),
    # CN national ID (18 digits incl. X check char), real checksum validated.
    _Rule(
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "ID_CARD",
        68,
        accept=_cn_id_card_accept,
    ),
    # CN mobile number (1[3-9]xxxxxxxxx).
    _Rule(
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "PHONE",
        55,
    ),
    # Credit cards: 13-19 digits with optional space/hyphen groups, Luhn-checked.
    _Rule(
        re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
        "CREDIT_CARD",
        52,
        accept=_credit_card_accept,
    ),
    _Rule(
        re.compile(r"(?<![A-Za-z0-9])[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}(?![A-Za-z0-9])"),
        "OPAQUE_ID",
        60,
    ),
    _Rule(
        re.compile(r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{64}|[0-9A-Fa-f]{40}|[0-9A-Fa-f]{32})(?![A-Za-z0-9])"),
        "OPAQUE_ID",
        60,
        accept=lambda v: bool(re.search(r"[A-Fa-f]", v)),
    ),
    _Rule(
        re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{24,}(?![A-Za-z0-9])"),
        "OPAQUE_ID",
        60,
        accept=_looks_like_high_entropy_id,
    ),
    _Rule(
        re.compile(r"(?<![A-Za-z0-9.])\+?(?:\d|\(\d)[\d ()-]{7,22}\d[Xx]?\)?(?![A-Za-z0-9]|\.\d)"),
        "LONG_NUMBER",
        50,
        accept=_phone_accept,
    ),
)


def redact_text(text: str) -> tuple[str, dict[str, int], bool]:
    """Redact PII in ``text``.

    Returns ``(redacted_text, counts_by_kind, redacted_flag)``.  Spans are
    merged by (start, then longer end, then higher priority) exactly like
    MemoraX's ``mergeOverlappingSpans``.
    """
    if not text:
        return text, {}, False

    spans: list[_Span] = []
    for rule in RULES:
        for match in rule.pattern.finditer(text):
            group_index = rule.group if rule.group is not None else 0
            value = match.group(group_index)
            if value is None or not value:
                continue
            if _PLACEHOLDER_ONLY.match(value.strip()):
                continue
            if rule.accept is not None and not rule.accept(value):
                continue
            relative_start = match.group(0).rfind(value)
            if relative_start < 0:
                continue
            start = match.start() + relative_start
            spans.append(_Span(start, start + len(value), rule.kind, rule.priority))

    if not spans:
        return text, {}, False

    spans.sort(key=lambda s: (s.start, -s.end, -s.priority))
    merged: list[_Span] = []
    for span in spans:
        if not merged or span.start >= merged[-1].end:
            merged.append(span)
        else:
            previous = merged[-1]
            merged[-1] = _Span(previous.start, max(previous.end, span.end), previous.kind, previous.priority)

    counts: dict[str, int] = {}
    output: list[str] = []
    offset = 0
    for span in merged:
        output.append(text[offset:span.start])
        output.append(f"[REDACTED:{span.kind}]")
        counts[span.kind] = counts.get(span.kind, 0) + 1
        offset = span.end
    output.append(text[offset:])
    return "".join(output), counts, True


def has_meaningful_text(text: str) -> bool:
    """True when the text still carries real content after stripping
    redaction placeholders, sensitive-key labels and punctuation.

    Ported from MemoraX ``hasMeaningfulMemoryPayloadText``.
    """
    if not text or not text.strip():
        return False
    remainder = REDACTED_PLACEHOLDER.sub(" ", text)
    remainder = _NON_CONTENT_LABELS.sub(" ", remainder)
    remainder = _PUNCTUATION.sub("", remainder)
    return bool(re.search(r"[^\W_]", remainder, re.UNICODE))
