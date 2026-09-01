"""Memory type classifier — ported from MemoraX Code semantics.

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT).
MemoraX tags every stored memory with a ``memory_type`` (rendered as
``<facts memory_type="...">`` buckets at recall time).  The upstream project
derives the type from its memory extraction pipeline; this port implements a
real, deterministic rule + keyword classifier with the same five categories:

- ``core``       — stable facts about the user (identity, role, durable
                   preferences).  The MemoraX "core memory" bucket.
- ``episodic``   — concrete events/experiences anchored in time ("yesterday
                   we hit the flaky test"; session happenings).
- ``semantic``   — general domain knowledge and definitions that are true
                   independent of the user ("SQLite is an embedded RDBMS").
- ``procedural`` — how-to knowledge: steps, commands, workflows, recipes.
- ``unclassified`` — the fallback bucket for anything that matches no rule.

The classifier is pure (no I/O, no model calls): each category has weighted
keyword/regex rules in English and Chinese; the highest scoring category
wins, and a minimum evidence threshold guards against weak matches so that
``unclassified`` remains the honest default.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

CORE = "core"
EPISODIC = "episodic"
SEMANTIC = "semantic"
PROCEDURAL = "procedural"
UNCLASSIFIED = "unclassified"

MEMORY_TYPES: tuple[str, ...] = (CORE, EPISODIC, SEMANTIC, PROCEDURAL, UNCLASSIFIED)

# Minimum accumulated evidence score required to classify into a real bucket.
_MIN_EVIDENCE = 1.0


class _Rule:
    __slots__ = ("pattern", "weight")

    def __init__(self, pattern: str, weight: float):
        self.pattern = re.compile(pattern, re.IGNORECASE)
        self.weight = weight


def _rules(pairs: Sequence[tuple[str, float]]) -> tuple[_Rule, ...]:
    return tuple(_Rule(p, w) for p, w in pairs)


# --- core: durable user facts / preferences ---------------------------------
_CORE_RULES = _rules(
    [
        (r"\bmy name is\b", 2.0),
        (r"\bi am\b|\bi'm\b", 1.0),
        (r"\bmy (?:email|e-mail)\b", 2.0),
        (r"\bcall me\b", 2.0),
        (r"\bi (?:prefer|like|love|hate|dislike|always use|never use)\b", 1.5),
        (r"\bmy (?:favorite|favourite|role|team|timezone|editor)\b", 1.5),
        (r"\buser profile\b|\bpersonal(?:ly)? (?:i|info)\b", 1.5),
        (r"\b(?:user|owner|operator) preferences?\b", 1.5),
        (r"\b(?:user|owner|operator|he|she|they) (?:prefers?|likes?|loves?|hates?|dislikes?|wants?|expects?)\b", 1.5),
        (r"我叫|我的名字|称呼我", 2.0),
        (r"我是(?!说|想|在)", 1.0),
        (r"我(?:喜欢|讨厌|偏好|习惯|总是用|从不用)", 1.5),
        (r"我的(?:邮箱|角色|团队|时区|偏好)", 1.5),
        (r"用户(?:偏好|喜好|喜欢|讨厌|希望|期望|要求|习惯)", 1.5),
    ]
)

# --- episodic: time-anchored events ------------------------------------------
_EPISODIC_DATE = (
    r"\b(?:19|20)\d{2}[-/](?:0?[1-9]|1[0-2])(?:[-/](?:0?[1-9]|[12]\d|3[01]))?\b"
    r"|(?:0?[1-9]|1[0-2])月(?:0?[1-9]|[12]\d|3[01])[日号]"
)
_EPISODIC_RULES = _rules(
    [
        (r"\byesterday\b|\blast (?:night|week|month|sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b", 1.5),
        (r"\btoday (?:we|i|the)\b|\bthis morning\b", 1.0),
        (r"\b(?:happened|occurred|encountered|ran into|we hit|we fixed|we deployed|we decided)\b", 1.5),
        (r"\bduring (?:the )?(?:meeting|standup|review|incident|outage)\b", 1.5),
        (_EPISODIC_DATE, 2.0),
        (r"昨天|今天(?:我们|我|早上)|昨晚|上周|上个月", 1.5),
        (r"当时|那次|有一次|发生了|遇到了|碰到了", 1.5),
        (_EPISODIC_DATE.replace(r"\b", ""), 2.0),
    ]
)

# --- semantic: definitions and domain facts ----------------------------------
_SEMANTIC_RULES = _rules(
    [
        (r"\bis a (?:type|kind|form|subset|part) of\b|\bare (?:a|an) (?:type|kind) of\b", 2.0),
        (r"\b(?:means|refers to|is defined as|stands for|consists of)\b", 2.0),
        (r"\baccording to (?:the )?(?:docs?|documentation|spec|rfc)\b", 1.5),
        (r"\b(?:invented|created|maintained|published) (?:by|in)\b", 1.0),
        (r"\bfact:?\b|\bdefinition\b", 1.0),
        (r"\bis (?:a|an) (?:[\w.-]+ )?(?:application|framework|library|language|service|platform|database|protocol|tool)\b", 1.5),
        (r"\b(?:primary|main|programming) language\b|\bwritten in\b|\bbuilt (?:with|in|on)\b|\bpowers?\b", 1.5),
        (r"是一种|是一个|是指|指的是|定义为|由.{0,12}组成", 2.0),
        (r"根据(?:官方)?(?:文档|规范|定义)", 1.5),
        (r"主要(?:语言|技术栈|框架)|使用.{0,12}(?:编写|开发|构建)", 1.5),
    ]
)

# --- procedural: steps, commands, how-tos ------------------------------------
_PROCEDURAL_RULES = _rules(
    [
        (r"\bhow (?:to|do i|can i)\b", 1.5),
        (r"\bsteps?(?:\s+\d)?\s*[:：]|\bstep \d\b", 1.5),
        (r"\bfirst(?:ly)?,? .{0,80}\bthen\b", 1.5),
        (r"\brun (?:the )?(?:following )?(?:command|script)\b|\bexecute\b", 1.5),
        (r"```", 1.0),
        (r"^\s*(?:\d+[.)]|[-*])\s+\S", 0.4),
        (r"\b(?:install|configure|deploy|set up|setup) (?:it|the|a|an)\b", 1.0),
        (r"步骤|首先.{0,60}(?:然后|接着|再)|第[一二三四五六七八九十\d]步", 1.5),
        (r"(?:执行|运行)(?:以下|如下)?(?:命令|脚本)|安装方法|操作步骤", 1.5),
    ]
)

_CATEGORY_ORDER: tuple[tuple[str, tuple[_Rule, ...]], ...] = (
    (PROCEDURAL, _PROCEDURAL_RULES),
    (CORE, _CORE_RULES),
    (EPISODIC, _EPISODIC_RULES),
    (SEMANTIC, _SEMANTIC_RULES),
)


def classify_memory_type(text: str) -> str:
    """Classify ``text`` into one of the five MemoraX memory types.

    Real rule evaluation: every category's weighted patterns are matched
    against the text; the category with the highest total weight wins.  A
    minimum evidence threshold keeps weak matches in ``unclassified`` (the
    honest default) instead of forcing a guess.  Ties resolve in the fixed
    ``_CATEGORY_ORDER`` priority (procedural > core > episodic > semantic),
    which mirrors how MemoraX's extraction prompt privileges actionable and
    identity facts over generic prose.
    """
    if not isinstance(text, str) or not text.strip():
        return UNCLASSIFIED

    best_type = UNCLASSIFIED
    best_score = _MIN_EVIDENCE
    for memory_type, rules in _CATEGORY_ORDER:
        score = 0.0
        for rule in rules:
            matches = rule.pattern.findall(text)
            if matches:
                # Diminishing returns: first match full weight, later ones half.
                score += rule.weight + 0.5 * rule.weight * (len(matches) - 1)
        if score > best_score:
            best_score = score
            best_type = memory_type
    return best_type


def normalize_memory_type(value: object) -> str:
    """Normalize an external memory_type value into the known set."""
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in MEMORY_TYPES:
            return lowered
    return UNCLASSIFIED
