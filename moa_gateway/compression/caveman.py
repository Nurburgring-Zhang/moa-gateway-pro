"""Caveman semantic-condensation engine (rule packs + intensity levels).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/caveman.ts`` + ``cavemanRules.ts``:

* Rule packs live in ``rules/en/*.json`` (filler / dedup / structural /
  context / ultra). Each rule declares a ``context`` (all|user|assistant|
  system) and a ``minIntensity`` (lite|full|ultra); a mode's intensity
  selects the matching rule subset.
* Protected spans (code fences, URLs, identifiers, ...) are tombstoned via
  :mod:`moa_gateway.compression.preservation` before rules run and stitched
  back verbatim afterwards, so structured content is never mangled.
* ``RULE_KEYWORDS`` (from ``port_data.json``) provides the cheap pre-filter:
  a rule is only attempted when one of its trigger keywords is present.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .preservation import extract_preserved_blocks, restore_preserved_blocks

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).resolve().parent / "rules" / "en"
_PORT_DATA_PATH = Path(__file__).resolve().parent / "port_data.json"

INTENSITY_RANK = {"lite": 0, "full": 1, "ultra": 2}

_ARTICLE_HINT_RE = re.compile(r"\b(?:a|an|the)\b")

_CODE_LINE_HINTS = re.compile(
    r"(?:^|\s)(?:import|from|def |class |return|if\s|else|elif|for |while "
    r"|const |let |var |function |=>|[{};]|\w+\.\w+\()"
)


@dataclass(frozen=True)
class CavemanRule:
    name: str
    pattern: re.Pattern
    replacement: str
    replacement_fn: Callable[[str], str] | None
    context: str
    category: str
    min_intensity: str


@dataclass
class CavemanConfig:
    enabled: bool = True
    compress_roles: tuple[str, ...] = ("user", "assistant")
    skip_rules: frozenset[str] = frozenset()
    min_message_length: int = 50
    preserve_patterns: tuple[str, ...] = ()
    intensity: str = "lite"


@dataclass
class CavemanResult:
    messages: list[dict[str, Any]]
    rules_applied: list[str] = field(default_factory=list)
    preserved_block_count: int = 0
    fallback_applied: bool = False


_lock = threading.Lock()
_rule_cache: dict[str, list[CavemanRule]] = {}
_keyword_cache: dict[str, list[str]] | None = None


def _compile_replacement_fn(
    rule: dict[str, Any],
) -> Callable[[str], str] | None:
    """Build the case-insensitive replacement-map function (TS ``__fn`` maps)."""
    mapping = rule.get("replacementMap")
    if not isinstance(mapping, dict) or not mapping:
        return None
    lowered = {str(key).lower(): str(value) for key, value in mapping.items()}
    fallback = str(rule.get("replacement", ""))

    def _fn(match: str) -> str:
        return lowered.get(match.strip().lower(), fallback)

    return _fn


def _load_pack(path: Path) -> list[CavemanRule]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules: list[CavemanRule] = []
    for raw in data.get("rules", []):
        try:
            pattern = re.compile(raw["pattern"], re.IGNORECASE)
        except (re.error, KeyError) as exc:
            logger.warning("skipping caveman rule %s: %s", raw.get("name"), exc)
            continue
        rules.append(
            CavemanRule(
                name=str(raw.get("name", "")),
                pattern=pattern,
                replacement=str(raw.get("replacement", "")),
                replacement_fn=_compile_replacement_fn(raw),
                context=str(raw.get("context", "all")),
                category=str(raw.get("category", "")),
                min_intensity=str(raw.get("minIntensity", "lite")),
            )
        )
    return rules


def load_rule_packs(language: str = "en") -> list[CavemanRule]:
    """Load (and cache) every rule pack for a language."""
    with _lock:
        cached = _rule_cache.get(language)
        if cached is not None:
            return cached
        pack_dir = Path(__file__).resolve().parent / "rules" / language
        rules: list[CavemanRule] = []
        if pack_dir.is_dir():
            for path in sorted(pack_dir.glob("*.json")):
                try:
                    rules.extend(_load_pack(path))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("failed to load caveman pack %s: %s", path, exc)
        _rule_cache[language] = rules
        return rules


def rule_keywords() -> dict[str, list[str]]:
    """Cheap keyword pre-filter table (ported ``RULE_KEYWORDS``)."""
    global _keyword_cache
    if _keyword_cache is not None:
        return _keyword_cache
    try:
        data = json.loads(_PORT_DATA_PATH.read_text(encoding="utf-8"))
        keywords = data.get("ruleKeywords", {})
        _keyword_cache = {
            str(name): [str(k) for k in values]
            for name, values in keywords.items()
            if isinstance(values, list)
        }
    except (OSError, json.JSONDecodeError):
        logger.warning("port_data.json unavailable; rule pre-filter disabled")
        _keyword_cache = {}
    return _keyword_cache


def get_rules_for_context(
    role: str, intensity: str = "full", language: str = "en"
) -> list[CavemanRule]:
    """Rules applicable to ``role`` at the given intensity level."""
    rank = INTENSITY_RANK.get(intensity, 0)
    out: list[CavemanRule] = []
    for rule in load_rule_packs(language):
        if rule.context not in ("all", role):
            continue
        if INTENSITY_RANK.get(rule.min_intensity, 0) > rank:
            continue
        out.append(rule)
    return out


def _should_attempt_rule(rule_name: str, lower_text: str) -> bool:
    if rule_name == "articles":
        return bool(_ARTICLE_HINT_RE.search(lower_text))
    keywords = rule_keywords().get(rule_name)
    if not keywords:
        return True
    return any(keyword in lower_text for keyword in keywords)


def apply_rules_to_text(
    text: str, rules: list[CavemanRule]
) -> tuple[str, list[str]]:
    result = text
    lower_result = text.lower()
    applied: list[str] = []
    for rule in rules:
        if not _should_attempt_rule(rule.name, lower_result):
            continue
        before = result
        if rule.replacement_fn is not None:
            fn = rule.replacement_fn
            result = rule.pattern.sub(lambda m: fn(m.group(0)), result)
        else:
            result = rule.pattern.sub(rule.replacement, result)
        if result != before:
            applied.append(rule.name)
    return result, applied


def cleanup_artifacts(text: str) -> str:
    """Post-rule whitespace/punctuation cleanup (TS ``cleanupArtifacts``)."""
    if not text:
        return ""
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([,.;:!?])", r"\1", text)
    text = re.sub(r"([.!?]){2,}", lambda m: m.group(1)[-1], text)
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def is_code_dominant_text(text: str) -> bool:
    """True when >=30% of non-empty lines look like code (skip prose fixes)."""
    lines = [line for line in text.split("\n") if line.strip()]
    if len(lines) < 3:
        return False
    code_like = sum(1 for line in lines if _CODE_LINE_HINTS.search(line))
    return code_like / len(lines) >= 0.3


def recapitalize_sentences(text: str) -> str:
    return re.sub(
        r"(^|[.!?][ \t]|\n[ \t]*)([a-z])",
        lambda m: f"{m.group(1)}{m.group(2).upper()}",
        text,
        flags=re.MULTILINE,
    )


def caveman_compress_text(
    text: str,
    role: str,
    config: CavemanConfig | None = None,
) -> tuple[str, list[str], int]:
    """Compress one text; returns (text, applied_rules, preserved_blocks)."""
    cfg = config or CavemanConfig()
    if not text or len(text) < cfg.min_message_length:
        return text, [], 0

    rules = [
        rule
        for rule in get_rules_for_context(role, cfg.intensity)
        if rule.name not in cfg.skip_rules
    ]
    if not rules:
        return text, [], 0

    extracted_text, blocks = extract_preserved_blocks(text, list(cfg.preserve_patterns))
    result, applied = apply_rules_to_text(extracted_text, rules)

    if not is_code_dominant_text(result):
        result = recapitalize_sentences(cleanup_artifacts(result))
    if blocks:
        result = cleanup_artifacts(restore_preserved_blocks(result, blocks))

    # Compression must never grow the text; fall back on rule malfunction.
    if len(result) > len(text):
        return text, [], len(blocks)
    return result, applied, len(blocks)


def caveman_compress_messages(
    messages: list[dict[str, Any]],
    config: CavemanConfig | None = None,
) -> CavemanResult:
    """Apply the caveman engine to every compressible message."""
    cfg = config or CavemanConfig()
    all_rules: list[str] = []
    preserved_total = 0
    out: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", ""))
        content = msg.get("content")
        if not cfg.enabled or role not in cfg.compress_roles:
            out.append(msg)
            continue
        if isinstance(content, str):
            new_text, applied, blocks = caveman_compress_text(content, role, cfg)
            all_rules.extend(applied)
            preserved_total += blocks
            out.append({**msg, "content": new_text} if new_text != content else msg)
        elif isinstance(content, list):
            new_content = []
            changed = False
            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    new_text, applied, blocks = caveman_compress_text(
                        part["text"], role, cfg
                    )
                    all_rules.extend(applied)
                    preserved_total += blocks
                    if new_text != part["text"]:
                        changed = True
                        new_content.append({**part, "text": new_text})
                        continue
                new_content.append(part)
            out.append({**msg, "content": new_content} if changed else msg)
        else:
            out.append(msg)

    return CavemanResult(
        messages=out,
        rules_applied=list(dict.fromkeys(all_rules)),
        preserved_block_count=preserved_total,
    )
