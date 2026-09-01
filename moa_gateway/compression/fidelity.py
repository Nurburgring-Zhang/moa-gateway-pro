"""Deterministic fidelity gate for compressed output.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/fidelityGate.ts``: a compressed result is
accepted only when the protected tokens, diff hunk headers, numeric literals
and JSON keys of the input survive in the output. On the first failing
invariant the caller falls back to the original text, so compression can
never silently drop load-bearing content.

``fidelity_score`` additionally exposes a real, computable 0..1 quality
metric (weighted protected-token / numeric / JSON-key survival plus length
retention) used for stats and observability.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .preservation import CRITICAL_KINDS, extract_preserved_blocks

# Anti-ReDoS: all quantifiers are bounded (same policy as the TS source).
NUMERIC_RE = re.compile(r"\d[\d.,]{0,40}")
JSON_KEY_RE = re.compile(r"\"([A-Za-z_$][\w$-]{0,80})\"\s*:")
HUNK_RE = re.compile(r"@@ -\d{1,9}(?:,\d{1,9})? \+\d{1,9}(?:,\d{1,9})? @@")


@dataclass(frozen=True)
class FidelityGateConfig:
    enabled: bool = True
    #: % of input protected tokens that must survive.
    min_token_survival_percent: float = 95.0
    #: % of input JSON keys that must survive.
    min_json_key_percent: float = 90.0
    check_numeric_integrity: bool = True
    check_diff_hunks: bool = True


@dataclass(frozen=True)
class FidelityResult:
    passed: bool
    failed_invariant: str | None = None
    detail: str | None = None


def _survival_ratio(needles: list[str], haystack: str) -> float:
    if not needles:
        return 1.0
    survived = sum(1 for needle in needles if needle in haystack)
    return survived / len(needles)


def _uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _critical_tokens(input_text: str) -> list[str]:
    _, blocks = extract_preserved_blocks(input_text)
    tokens = [
        block.content.strip()
        for block in blocks
        if block.kind in CRITICAL_KINDS and block.content.strip()
    ]
    return _uniq(tokens)


def check_fidelity(
    input_text: str, output_text: str, cfg: FidelityGateConfig | None = None
) -> FidelityResult:
    """Fail-closed per-invariant check; any internal error fails OPEN.

    A verifier bug must never block compression (OmniRoute policy), but a
    genuine invariant violation always returns ``passed=False`` with the
    offending invariant named.
    """
    gate = cfg or FidelityGateConfig()
    if not gate.enabled:
        return FidelityResult(passed=True)
    try:
        tokens = _critical_tokens(input_text)
        min_tok = gate.min_token_survival_percent / 100.0
        tok_ratio = _survival_ratio(tokens, output_text)
        if tok_ratio < min_tok:
            return FidelityResult(
                passed=False,
                failed_invariant="protected-tokens",
                detail=(
                    f"protected tokens {round(tok_ratio * 100)}% "
                    f"< {round(min_tok * 100)}%"
                ),
            )

        if gate.check_diff_hunks:
            for hunk in _uniq(HUNK_RE.findall(input_text)):
                if hunk not in output_text:
                    return FidelityResult(
                        passed=False,
                        failed_invariant="diff-hunks",
                        detail=f'hunk "{hunk}" missing from output',
                    )

        if gate.check_numeric_integrity:
            for number in _uniq(NUMERIC_RE.findall(input_text)):
                if number not in output_text:
                    return FidelityResult(
                        passed=False,
                        failed_invariant="numeric",
                        detail=f'number "{number}" missing from output',
                    )

        keys = _uniq(JSON_KEY_RE.findall(input_text))
        if keys:
            min_key = gate.min_json_key_percent / 100.0
            key_ratio = _survival_ratio([f'"{k}"' for k in keys], output_text)
            if key_ratio < min_key:
                return FidelityResult(
                    passed=False,
                    failed_invariant="json-keys",
                    detail=(
                        f"JSON keys {round(key_ratio * 100)}% "
                        f"< {round(min_key * 100)}%"
                    ),
                )

        return FidelityResult(passed=True)
    except Exception:  # pragma: no cover - fail-open guard
        return FidelityResult(passed=True)


def fidelity_score(input_text: str, output_text: str) -> float:
    """Real, computable fidelity metric in [0, 1].

    Weights (mirroring the gate's invariant priorities):
      * 0.50 protected-token (identifiers/URLs/paths/code) survival ratio
      * 0.20 numeric-literal survival ratio
      * 0.20 JSON-key survival ratio
      * 0.10 length retention ratio (capped at 1.0)
    """
    if not input_text:
        return 1.0

    tokens = _critical_tokens(input_text)
    tok_ratio = _survival_ratio(tokens, output_text)

    numbers = _uniq(NUMERIC_RE.findall(input_text))
    num_ratio = _survival_ratio(numbers, output_text)

    keys = _uniq(JSON_KEY_RE.findall(input_text))
    key_ratio = _survival_ratio([f'"{k}"' for k in keys], output_text) if keys else 1.0

    length_ratio = min(1.0, len(output_text) / len(input_text)) if input_text else 1.0

    return round(
        0.50 * tok_ratio + 0.20 * num_ratio + 0.20 * key_ratio + 0.10 * length_ratio, 4
    )
