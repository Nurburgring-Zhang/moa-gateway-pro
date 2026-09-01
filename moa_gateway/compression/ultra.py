"""Ultra engine — heuristic information-density token pruning (Tier A).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/ultraHeuristic.ts`` + ``ultra.ts``:

* Every prose token is scored 0.0..1.0 (digits/URLs/paths/errors = must keep,
  stopwords = 0.1, short tokens = 0.2, capitalized identifiers = 0.8, ...).
* Lowest-scored tokens are dropped until the target keep-rate is reached;
  whitespace and line structure are preserved.
* Structured spans are tombstoned by the shared preservation layer first
  (``pruneProseOnly``), so code blocks / inline code / URLs are NEVER pruned.

The stopword list and the force-preserve regex come from ``port_data.json``
(OmniRoute source) with an identical built-in fallback.
"""

from __future__ import annotations

import json
import logging
import math
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .preservation import extract_preserved_blocks, restore_preserved_blocks

logger = logging.getLogger(__name__)

_PORT_DATA_PATH = Path(__file__).resolve().parent / "port_data.json"

_BUILTIN_STOPWORDS = frozenset(
    """a an the is are was were be been being have has had do does did will
    would could should may might shall can need dare ought used i we you he
    she it they me us him her them my our your his its their this that these
    those and but or nor for yet so as at by in of on to up via with from
    into onto upon about just very really quite rather also too even still
    already always never often usually sometimes here there""".split()
)
_BUILTIN_FORCE_PRESERVE = r"\d|https?://|[._/\\]|Error:|Exception:|```"

DEFAULT_KEEP_RATE = 0.5
DEFAULT_MIN_SCORE = 0.3

_lock = threading.Lock()
_stopwords: frozenset[str] | None = None
_force_preserve_re: re.Pattern | None = None


def _load_port_data() -> tuple[frozenset[str], re.Pattern]:
    global _stopwords, _force_preserve_re
    with _lock:
        if _stopwords is not None and _force_preserve_re is not None:
            return _stopwords, _force_preserve_re
        stopwords = _BUILTIN_STOPWORDS
        force_source = _BUILTIN_FORCE_PRESERVE
        try:
            data = json.loads(_PORT_DATA_PATH.read_text(encoding="utf-8"))
            raw_words = data.get("stopwords")
            if isinstance(raw_words, list) and raw_words:
                stopwords = frozenset(str(w).lower() for w in raw_words)
            raw_force = data.get("forcePreserveRe")
            if isinstance(raw_force, dict) and raw_force.get("source"):
                force_source = str(raw_force["source"])
        except (OSError, json.JSONDecodeError):
            logger.warning("port_data.json unavailable; using built-in ultra tables")
        try:
            force_re = re.compile(force_source, re.IGNORECASE)
        except re.error:
            force_re = re.compile(_BUILTIN_FORCE_PRESERVE, re.IGNORECASE)
        _stopwords, _force_preserve_re = stopwords, force_re
        return stopwords, force_re


def stopwords() -> frozenset[str]:
    return _load_port_data()[0]


def score_token(token: str) -> float:
    """Information-value score for one token (0.0 prune .. 1.0 keep)."""
    _, force_re = _load_port_data()
    if force_re.search(token):
        return 1.0
    lowered = token.lower()
    if lowered in stopwords():
        return 0.1
    if len(token) <= 2:
        return 0.2
    if token[:1].isupper():
        return 0.8  # proper nouns / identifiers
    if len(token) >= 6:
        return 0.7
    return 0.5


def prune_by_score(text: str, keep_rate: float = DEFAULT_KEEP_RATE,
                   min_score: float = DEFAULT_MIN_SCORE) -> str:
    """Drop lowest-value tokens until the keep-rate target is met."""
    if not text or keep_rate >= 1:
        return text
    tokens = re.split(r"(\s+)", text)  # keep whitespace tokens
    word_positions = [i for i, t in enumerate(tokens) if not t.isspace() and t]
    target_keep = math.ceil(len(word_positions) * max(0.0, min(1.0, keep_rate)))
    if target_keep >= len(word_positions):
        return text

    scored = sorted(
        (
            (score_token(tokens[pos]), index, pos)
            for index, pos in enumerate(word_positions)
        ),
        key=lambda item: (item[0], -item[1]),
    )
    drop_count = len(word_positions) - target_keep
    drop_positions: set[int] = set()
    for score, _, pos in scored:
        if len(drop_positions) >= drop_count:
            break
        if score >= 1.0:  # force-preserved tokens are never dropped
            break
        drop_positions.add(pos)

    out: list[str] = []
    for i, token in enumerate(tokens):
        if i in drop_positions:
            continue
        out.append(token)
    result = "".join(out)
    result = re.sub(r"[ \t]{2,}", " ", result)
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n[ \t]+", "\n", result)
    return result.strip() if result.strip() else text


def prune_prose_only(text: str, keep_rate: float = DEFAULT_KEEP_RATE,
                     min_score: float = DEFAULT_MIN_SCORE) -> str:
    """Prune prose segments only; preserved blocks are stitched back verbatim."""
    extracted, blocks = extract_preserved_blocks(text)
    if not blocks:
        return prune_by_score(text, keep_rate, min_score)
    placeholder_map = {b.placeholder: b.content for b in blocks}
    parts = re.split(
        "(" + "|".join(re.escape(b.placeholder) for b in blocks) + ")", extracted
    )
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        preserved = placeholder_map.get(part)
        if preserved is not None:
            out.append(preserved)
        else:
            out.append(prune_by_score(part, keep_rate, min_score))
    return restore_preserved_blocks("".join(out), [])


@dataclass
class UltraResult:
    messages: list[dict[str, Any]]
    techniques_used: list[str]
    ultra_tier: str = "heuristic"


def ultra_compress_messages(
    messages: list[dict[str, Any]],
    keep_rate: float = DEFAULT_KEEP_RATE,
    min_score: float = DEFAULT_MIN_SCORE,
    compress_roles: tuple[str, ...] = ("user", "assistant"),
    preserve_system_prompt: bool = True,
    protect_last_user: bool = True,
) -> UltraResult:
    """Ultra pass: caveman-ultra style pruning over prose only.

    The most recent user message is left untouched (it carries the current
    intent); system prompts are preserved by default.
    """
    last_user_index = -1
    if protect_last_user:
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "user":
                last_user_index = i
                break

    applied = False
    out: list[dict[str, Any]] = []
    for i, msg in enumerate(messages):
        role = str(msg.get("role", ""))
        content = msg.get("content")
        if (
            role not in compress_roles
            or (preserve_system_prompt and role == "system")
            or i == last_user_index
            or not isinstance(content, str)
            or len(content) < 80
        ):
            out.append(msg)
            continue
        pruned = prune_prose_only(content, keep_rate, min_score)
        if len(pruned) < len(content):
            applied = True
            out.append({**msg, "content": pruned})
        else:
            out.append(msg)

    return UltraResult(
        messages=out,
        techniques_used=["ultra-heuristic-prune"] if applied else [],
        ultra_tier="heuristic",
    )
