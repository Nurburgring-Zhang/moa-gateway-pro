"""Natural-language skill creation — generate a real SKILL.md from a description.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/skill_loader.rb`` ``create_skill`` — slug validation, frontmatter
  assembly via ``build_skill_content`` and writing the skill directory to disk;
- the skill-authoring flow OpenClacky exposes to agents (a description in, a
  valid SKILL.md out).

Two generation paths, both producing genuinely usable files:
1. **LLM path** — the gateway's real ModelPool drafts the SKILL.md; the output
   is parsed and validated, and rejected if malformed.
2. **Deterministic template engine** — a pure-Python generator (no model
   required) that composes valid frontmatter + a structured prompt body from
   the description; it is also the fallback when the LLM path is unavailable
   or produces invalid output.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .discovery import SkillRegistry, slug_for_name_hint
from .errors import SkillValidationError
from .loader import FRONTMATTER_RE, parse_frontmatter, sanitize_frontmatter, slugify
from .models import Skill

logger = logging.getLogger(__name__)

#: stop words excluded from trigger extraction (EN + common ZH particles)
_STOP_WORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "onto",
    "over", "under", "about", "when", "what", "which", "where", "who",
    "how", "can", "could", "will", "would", "should", "please", "help",
    "want", "need", "make", "create", "generate", "write", "skill",
    "一个", "帮我", "需要", "能够", "可以", "请", "把", "将", "用于", "进行", "功能",
}

_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n(.*?)```", re.DOTALL)


#: CJK function characters used as phrase boundaries during trigger extraction
#: (no tokenizer dependency: 把/成/并/的/... split continuous runs into phrases)
_ZH_PARTICLES = "的了和与并或把被将要在到从给对为以及等而且但使让向"


def _zh_phrases(run: str) -> list[str]:
    """Split one continuous CJK run into candidate phrases."""
    phrases: list[str] = []
    for part in re.split(f"[{_ZH_PARTICLES}]", run):
        part = part.strip()
        if not part:
            continue
        if len(part) <= 6:
            phrases.append(part)
        else:
            # long segment: keep head and tail phrases (most informative ends)
            phrases.append(part[:4])
            if part[-4:] != part[:4]:
                phrases.append(part[-4:])
    return phrases


def extract_triggers(description: str, limit: int = 6) -> list[str]:
    """Heuristic keyword extraction for the ``triggers`` frontmatter field.

    Real extraction: ascii words of length >= 3 minus stop
    words, plus CJK phrases obtained by splitting continuous runs at common
    function characters (particle boundaries), deduplicated in appearance
    order. No external tokenizer required.
    """
    triggers: list[str] = []
    seen: set[str] = set()

    def _push(tok: str) -> None:
        t = tok.strip().lower()
        if len(t) >= 2 and t not in _STOP_WORDS and t not in seen:
            seen.add(t)
            triggers.append(t)

    for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", description):
        if len(triggers) >= limit:
            break
        _push(word)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", description):
        for phrase in _zh_phrases(run):
            if len(triggers) >= limit:
                break
            _push(phrase)
        if len(triggers) >= limit:
            break
    return triggers[:limit]


def _title_from_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-") if w)


def deterministic_skill(name_hint: str | None, description: str) -> tuple[str, dict[str, Any], str]:
    """Template-engine generator: returns ``(slug, meta, body)``.

    The body is a real, executable prompt scaffold: it embeds the user's
    description as the skill objective and defines workflow, constraints and
    output requirements — the same structure used by the bundled packs.
    """
    desc = description.strip()
    slug = slug_for_name_hint(name_hint, desc)
    triggers = extract_triggers(desc)

    meta: dict[str, Any] = {
        "name": slug,
        "description": desc[:340],
        "triggers": triggers,
        "argument-hint": "<describe the task input for this skill>",
    }

    if slug.startswith("skill-"):
        # crc fallback slug: use the description itself as the human title
        title = desc[:40]
    else:
        title = _title_from_slug(slug)
    body = f"""# {title}

{desc}

## Objective

Deliver exactly what the description above promises. Treat every user request
as an instance of this capability and stay strictly within its scope; when a
request falls outside the scope, say so and suggest the closest alternative.

## Workflow

1. **Understand the input.** Restate the user's concrete request in one line;
   list anything missing that blocks execution and ask for it concisely.
2. **Plan.** Break the work into 2-5 ordered steps appropriate to the request
   size; do not over-decompose trivial requests.
3. **Execute.** Perform each step carefully, keeping all facts traceable to
   the user's input — never invent data.
4. **Verify.** Re-check the output against the request: completeness,
   correctness, format. Fix any gap before answering.

## Constraints

- Keep the user's language (Chinese input -> Chinese output, etc.).
- Preserve technical terms, names and numbers exactly as given.
- Prefer concrete, actionable output over generic commentary.
- If the request is ambiguous in a way that changes the result, state the
  assumption you picked and proceed, flagging it at the end.

## Output Format

Return Markdown: a short headline result first, then the details/rationale,
then (only when relevant) follow-up suggestions. No filler, no apologies.
"""
    return slug, meta, body


def parse_llm_skill_output(text: str, fallback_name: str) -> tuple[dict[str, Any], str] | None:
    """Parse model output into ``(meta, body)``; None when unusable.

    Tolerates output wrapped in code fences and requires a frontmatter block.
    """
    if not text or not text.strip():
        return None
    candidate = text.strip()
    fence = _CODE_FENCE_RE.search(candidate)
    if fence and "name" in fence.group(1):
        candidate = fence.group(1).strip()
    if not FRONTMATTER_RE.match(candidate):
        return None
    meta, body, warnings = parse_frontmatter(candidate)
    if warnings:
        logger.info("skillhub: LLM-generated frontmatter warnings: %s", warnings)
    meta, _ = sanitize_frontmatter(meta, fallback_name)
    if not body.strip():
        return None
    return meta, body


async def llm_generate_skill(name_hint: str | None, description: str) -> tuple[str, dict[str, Any], str] | None:
    """LLM path: ask the real pipeline to draft a SKILL.md.

    Returns ``(slug, meta, body)`` or None when the pipeline is unavailable or
    the model output fails validation (caller then uses the template engine).
    """
    from .invoker import call_model_pipeline

    slug_hint = slug_for_name_hint(name_hint, description)
    prompt = (
        "You author SKILL.md files for an AI-agent skill ecosystem.\n"
        "Write a complete SKILL.md for the capability described below.\n\n"
        "Hard requirements:\n"
        "1. Start with a YAML frontmatter block delimited by '---' lines containing "
        f"at least: name (a slug, suggested: {slug_hint}), description (one sentence), "
        "triggers (3-6 short keywords in the user's language).\n"
        "2. After the frontmatter, write a markdown body with: a title heading, "
        "Objective, Workflow (numbered concrete steps), Constraints, Output Format.\n"
        "3. The body must be directly usable as an LLM system prompt — specific, "
        "imperative, with every section fully written out.\n\n"
        f"Capability description:\n{description.strip()}\n\n"
        "Respond with ONLY the SKILL.md content."
    )
    messages = [
        {"role": "system", "content": "You are a precise technical writer."},
        {"role": "user", "content": prompt},
    ]
    try:
        resp, ep = await call_model_pipeline(messages, max_tokens=2048)
    except Exception as e:
        logger.info("skillhub: LLM skill generation unavailable (%s), using template engine", e)
        return None
    parsed = parse_llm_skill_output(resp.content, slug_hint)
    if parsed is None:
        logger.info("skillhub: LLM skill output failed validation, using template engine")
        return None
    meta, body = parsed
    raw_name = meta.get("name") or slug_hint
    slug = raw_name if slugify(raw_name) == raw_name else slug_hint
    meta["name"] = slug
    logger.info("skillhub: LLM drafted skill '%s' via endpoint %s", slug, ep.id)
    return slug, meta, body


async def create_skill_from_description(
    description: str,
    name_hint: str | None = None,
    registry: SkillRegistry | None = None,
    force_template: bool = False,
) -> dict[str, Any]:
    """Generate a SKILL.md on disk from a natural-language description.

    Returns ``{"skill": Skill.to_dict(), "path": str, "generated_by": str}``.
    """
    if not description or not description.strip():
        raise SkillValidationError("description must not be empty")

    reg = registry or SkillRegistry()
    generated_by = "template"
    result: tuple[str, dict[str, Any], str] | None = None
    if not force_template:
        result = await llm_generate_skill(name_hint, description)
        if result is not None:
            generated_by = "llm"
    if result is None:
        result = deterministic_skill(name_hint, description)
    slug, meta, body = result

    path = reg.save_skill(slug, meta, body, overwrite=True)
    skill = reg.require(slug)
    logger.info("skillhub: created skill '%s' via %s path -> %s", slug, generated_by, path)
    return {
        "skill": skill.to_dict(),
        "path": str(path),
        "generated_by": generated_by,
    }
