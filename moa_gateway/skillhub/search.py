"""Fuzzy skill search — weighted multi-field similarity.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/agent/skill_manager.rb`` ``suggest_similar_skills`` — substring
  containment gets the highest boost, character overlap is the fallback signal,
  results capped at top-3.

Extended for moa_gateway_pro: the score blends name, description and the
``triggers`` frontmatter field with tunable weights, stays CJK-friendly
(substring and character-set signals, no whitespace tokenization assumptions),
and always returns results ranked by score.
"""

from __future__ import annotations

import difflib
import logging
from dataclasses import dataclass

from .models import Skill

logger = logging.getLogger(__name__)

#: field weights (sum tuned so a strong single-field match still ranks high)
W_NAME = 0.40
W_DESC = 0.25
W_TRIGGER = 0.25
W_OVERLAP = 0.10

#: bonus when the query is a literal substring of the skill name
_SUBSTRING_BONUS = 0.15

MIN_SCORE = 0.05


def _ratio(a: str, b: str) -> float:
    """SequenceMatcher ratio on lowercased strings (0..1)."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _char_overlap(a: str, b: str) -> float:
    """Jaccard overlap of character sets — cheap CJK-friendly signal."""
    sa, sb = set(a.lower()), set(b.lower())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _trigger_score(query: str, triggers: list[str]) -> float:
    """Best trigger match: exact/containment -> 1.0, else fuzzy ratio."""
    q = query.strip().lower()
    if not q or not triggers:
        return 0.0
    best = 0.0
    for trig in triggers:
        t = trig.strip().lower()
        if not t:
            continue
        if t == q or t in q or q in t:
            return 1.0
        best = max(best, _ratio(q, t))
    return best


@dataclass
class SearchResult:
    skill: Skill
    score: float
    breakdown: dict[str, float]

    def to_dict(self) -> dict:
        return {
            "skill": self.skill.to_dict(),
            "score": round(self.score, 4),
            "breakdown": {k: round(v, 4) for k, v in self.breakdown.items()},
        }


def score_skill(query: str, skill: Skill) -> SearchResult:
    """Compute a weighted similarity score in [0, 1] between query and skill."""
    q = query.strip()
    name = skill.name.lower()
    ql = q.lower()

    if ql and ql == name:
        breakdown = {"name": 1.0, "description": 1.0, "trigger": 1.0, "overlap": 1.0}
        return SearchResult(skill=skill, score=1.0, breakdown=breakdown)

    name_score = _ratio(ql, name) * W_NAME
    if ql and (ql in name or name in ql):
        name_score = min(W_NAME + _SUBSTRING_BONUS, name_score + _SUBSTRING_BONUS)

    desc_pool = " ".join(
        x for x in (skill.description, skill.description_zh) if x
    )
    desc_score = _ratio(ql, desc_pool) * W_DESC
    if ql and len(ql) >= 2 and ql in desc_pool.lower():
        desc_score = max(desc_score, W_DESC * 0.9)

    trig_score = _trigger_score(q, skill.triggers) * W_TRIGGER

    overlap = _char_overlap(ql, name + " " + desc_pool) * W_OVERLAP

    total = min(1.0, name_score + desc_score + trig_score + overlap)
    return SearchResult(
        skill=skill,
        score=total,
        breakdown={
            "name": name_score,
            "description": desc_score,
            "trigger": trig_score,
            "overlap": overlap,
        },
    )


def search_skills(
    query: str,
    skills: list[Skill],
    top_k: int = 5,
    min_score: float = MIN_SCORE,
) -> list[SearchResult]:
    """Rank ``skills`` against ``query``; returns up to ``top_k`` results."""
    if not query or not query.strip():
        return []
    results = [score_skill(query, s) for s in skills]
    results = [r for r in results if r.score >= min_score]
    results.sort(key=lambda r: (-r.score, r.skill.name))
    return results[: max(1, top_k)]
