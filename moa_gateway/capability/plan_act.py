"""Plan/Act mode classifier for MoA Gateway Pro.

Resolves a user query into one of three execution modes (plan / act / chat)
using deterministic, local-only signal matching. No LLM calls are made.

Modes
-----
- ``plan`` : user is asking for design, strategy, or evaluation — the system
  should think, draft, or recommend before doing anything.
- ``act``  : user is asking the system to perform a concrete action now
  (build, run, send, click, etc.).
- ``chat`` : fallback when no Plan/Act signal fires; conversational mode.

This module is referenced by capability R-10 in the MoA Gateway spec.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["classify_mode"]


_PLAN_KEYWORDS: frozenset = frozenset(
    {
        "plan_strategy",
        "refactor",
        "design",
        "propose",
        "outline",
        "design_pattern",
        "simulate",
        "model",
        "evaluate",
        "draft",
        "design_doc",
        "what_if",
        "recommend",
        "should_we",
        "consider",
        "suggest",
        "brainstorm",
        "ideate",
        "think_about",
        "plan_out",
        "strategic",
        "roadmap",
        "blueprint",
        "architect",
        "envision",
        "explore",
    }
)

_ACT_KEYWORDS: frozenset = frozenset(
    {
        "execute",
        "run",
        "build",
        "deploy",
        "install",
        "click",
        "type",
        "paste",
        "search",
        "fetch",
        "download",
        "edit",
        "fix",
        "create",
        "make",
        "send",
        "write",
        "open",
    }
)

_ACT_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        r"act_run_command",
        r"^\s*(?:please\s+)?(?:run|execute|build|deploy|install|click|type|paste|search|fetch|download)\b",
    ),
    (r"act_do_it", r"\bdo\s+(?:it|this|that)\b"),
    (
        r"act_can_you",
        r"\bcan\s+you\s+(?:please\s+)?(?:run|execute|build|deploy|install|click|type|paste|search|fetch|download|edit|fix|create|make|send|write|open)\b",
    ),
    (
        r"act_lets",
        r"\b(?:let'?s|lets)\s+(?:run|execute|build|deploy|install|click|type|paste|search|fetch|download|edit|fix|create|make|send|write|open|do)\b",
    ),
    (r"act_go_ahead", r"\bgo\s+ahead\s+(?:and|to)\b"),
    (
        r"act_please_verb",
        r"^\s*please\s+(?:run|execute|build|deploy|install|click|type|paste|search|fetch|download|edit|fix|create|make|send|write|open)\b",
    ),
    (r"act_now", r"\b(?:now|immediately|right\s+away|asap)\b"),
    (r"act_perform", r"\bperform\s+(?:the|this|that|a)\b"),
    (r"act_trigger", r"\btrigger\s+(?:the|this|that|a)\b"),
    (r"act_apply", r"\bapply\s+(?:the|this|that|a)\b"),
    (r"act_commit_push", r"\bcommit\s+(?:and|&)\s+push\b"),
)

_PLAN_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"plan_how_should", r"\bhow\s+(?:should|would|can|do)\s+(?:we|i|you)\b"),
    (r"plan_what_if", r"\bwhat\s+if\b"),
    (r"plan_should_we", r"\bshould\s+(?:we|i)\b"),
    (r"plan_think_about", r"\bthink\s+about\b"),
    (r"plan_come_up", r"\bcome\s+up\s+with\b"),
    (r"plan_compare", r"\bcompare\s+(?:the|these|those|options|alternatives)\b"),
    (r"plan_pros_cons", r"\bpros\s+and\s+cons\b"),
    (r"plan_evaluate", r"\bevaluate\s+(?:the|this|that|our|options|alternatives)\b"),
)


def _normalise(query: str) -> str:
    """Lower-case and collapse whitespace; leaves punctuation intact for regexes."""
    return re.sub(r"\s+", " ", (query or "").lower()).strip()


def _scan_keywords(text: str, keywords: frozenset) -> list[str]:
    """Return the list of keyword tokens that appear as whole-word substrings."""
    hits: list[str] = []
    for kw in keywords:
        pattern = r"\b" + re.escape(kw) + r"\b"
        if re.search(pattern, text):
            hits.append(kw)
    return hits


def _scan_patterns(text: str, patterns: tuple[tuple[str, str], ...]) -> list[str]:
    """Return the list of pattern names that fire against the normalised text."""
    return [name for name, regex in patterns if re.search(regex, text)]


def classify_mode(query: Any) -> dict[str, Any]:
    """Classify a user ``query`` into plan / act / chat mode.

    Parameters
    ----------
    query:
        Raw user input. Anything non-string is stringified first. The function
        never raises — invalid input falls back to ``chat`` with confidence 0.

    Returns
    -------
    dict
        ``{"mode": "plan"|"act"|"chat", "confidence": float, "signals": list[str]}``

        - ``mode`` is the winning mode (``chat`` is the fallback).
        - ``confidence`` is a float in ``[0.0, 1.0]`` derived from the number of
          keyword hits (×0.2) and pattern hits (×0.3) for the winning side,
          clamped. The losing side's score is ignored.
        - ``signals`` lists every keyword and pattern name that contributed,
          prefixed with ``"plan:"`` or ``"act:"`` so callers can audit the
          decision.

    Examples
    --------
    >>> classify_mode("please run the tests")
    {'mode': 'act', 'confidence': 0.5, 'signals': ['act:run', 'act:act_run_command']}
    >>> classify_mode("how should we refactor the auth module?")
    {'mode': 'plan', 'confidence': 0.5, 'signals': ['plan:refactor', 'plan:plan_how_should']}
    >>> classify_mode("hi there")
    {'mode': 'chat', 'confidence': 0.0, 'signals': []}
    """
    fallback: dict[str, Any] = {"mode": "chat", "confidence": 0.0, "signals": []}
    try:
        if query is None:
            return dict(fallback)
        text = _normalise(str(query))
        if not text:
            return dict(fallback)

        plan_kw_hits = _scan_keywords(text, _PLAN_KEYWORDS)
        act_kw_hits = _scan_keywords(text, _ACT_KEYWORDS)
        plan_pat_hits = _scan_patterns(text, _PLAN_PATTERNS)
        act_pat_hits = _scan_patterns(text, _ACT_PATTERNS)

        plan_score = len(plan_kw_hits) * 0.2 + len(plan_pat_hits) * 0.3
        act_score = len(act_kw_hits) * 0.2 + len(act_pat_hits) * 0.3

        if plan_score == 0.0 and act_score == 0.0:
            return dict(fallback)

        if plan_score > act_score:
            mode = "plan"
            confidence = plan_score
            signals = [f"plan:{s}" for s in plan_kw_hits] + [f"plan:{s}" for s in plan_pat_hits]
        elif act_score > plan_score:
            mode = "act"
            confidence = act_score
            signals = [f"act:{s}" for s in act_kw_hits] + [f"act:{s}" for s in act_pat_hits]
        # exact tie: prefer the side that produced any keyword hit, else act (more concrete)
        elif plan_kw_hits and not act_kw_hits:
            mode = "plan"
            confidence = plan_score
            signals = [f"plan:{s}" for s in plan_kw_hits] + [f"plan:{s}" for s in plan_pat_hits]
        elif act_kw_hits and not plan_kw_hits:
            mode = "act"
            confidence = act_score
            signals = [f"act:{s}" for s in act_kw_hits] + [f"act:{s}" for s in act_pat_hits]
        else:
            mode = "act"
            confidence = act_score
            signals = [f"act:{s}" for s in act_kw_hits] + [f"act:{s}" for s in act_pat_hits]

        confidence = max(0.0, min(1.0, confidence))
        return {"mode": mode, "confidence": confidence, "signals": signals}
    except Exception:
        return dict(fallback)
