"""SKILL.md loader — YAML frontmatter parsing, sanitization and slug rules.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/skill.rb`` — ``parse_frontmatter`` (regex ``\\A---\\n(.*?)\\n---[ \\t]*\\n?``
  + ``YAML.safe_load``, lenient: bad YAML degrades to a warning with the whole
  file treated as content) and ``sanitize_frontmatter`` (invalid ``name`` falls
  back to ``name_zh`` then to the directory slug; description capped at 340).
- ``lib/clacky/skill_loader.rb`` — ``create_skill`` slug validation
  (``/^[a-z0-9][a-z0-9-]*$/``) and ``build_skill_content``
  (``"---\\n{yaml}---\\n\\n{content}"``).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from .errors import SkillValidationError
from .models import DESCRIPTION_MAX_CHARS, Skill

logger = logging.getLogger(__name__)

#: OpenClacky skill.rb FRONTMATTER_RE (extended to tolerate CRLF endings).
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)

#: OpenClacky skill_loader.rb SLUG_REGEX.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")

_SKILL_FILENAME = "SKILL.md"


def is_valid_slug(name: str) -> bool:
    """True when ``name`` satisfies ``^[a-z0-9][a-z0-9-]*$``."""
    return bool(name) and bool(SLUG_RE.match(name))


def slugify(raw: str) -> str:
    """Best-effort conversion of arbitrary text into a valid skill slug.

    Keeps ASCII alphanumerics, maps runs of other characters to single hyphens.
    Returns "" when nothing usable remains (callers must fall back).
    """
    if not raw:
        return ""
    out: list[str] = []
    for ch in raw.strip().lower():
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        else:
            if out and out[-1] != "-":
                out.append("-")
    slug = "".join(out).strip("-")
    # slug must start with alphanumeric; strip leading hyphens defensively
    return slug.lstrip("-")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str, list[str]]:
    """Split a SKILL.md document into (meta, body, warnings).

    Faithful port of OpenClacky ``Skill.parse_frontmatter``:
    - no frontmatter block -> ``({}, whole_text, [])``
    - YAML parse failure or non-mapping YAML -> warning, whole file as content
    """
    warnings: list[str] = []
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text, warnings
    raw_yaml = m.group(1)
    try:
        data = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as e:
        warnings.append(f"invalid YAML frontmatter, using whole file as content: {e}")
        logger.warning("SKILL.md frontmatter YAML error: %s", e)
        return {}, text, warnings
    if data is None:
        data = {}
    if not isinstance(data, dict):
        warnings.append("frontmatter is not a mapping, using whole file as content")
        return {}, text, warnings
    return data, text[m.end():], warnings


def sanitize_frontmatter(
    meta: dict[str, Any], fallback_name: str
) -> tuple[dict[str, Any], list[str]]:
    """Normalize raw frontmatter, ported from OpenClacky ``sanitize_frontmatter``.

    - invalid/missing ``name`` -> try ``name_zh`` -> finally the fallback slug
      (derived from the skill directory name);
    - descriptions truncated to DESCRIPTION_MAX_CHARS;
    - scalar fields coerced, ``triggers`` normalized to a list of strings.
    """
    warnings: list[str] = []
    meta = dict(meta)

    name = meta.get("name")
    if not isinstance(name, str) or not is_valid_slug(name.strip()):
        zh = meta.get("name_zh")
        zh_slug = slugify(zh) if isinstance(zh, str) else ""
        if zh_slug:
            warnings.append(f"invalid skill name {name!r}, using slugified name_zh {zh_slug!r}")
            meta["name"] = zh_slug
        else:
            warnings.append(f"invalid skill name {name!r}, using directory slug {fallback_name!r}")
            meta["name"] = fallback_name
    else:
        meta["name"] = name.strip()

    for key in ("description", "description_zh"):
        val = meta.get(key)
        if isinstance(val, str) and len(val) > DESCRIPTION_MAX_CHARS:
            meta[key] = val[:DESCRIPTION_MAX_CHARS]
            warnings.append(f"{key} truncated to {DESCRIPTION_MAX_CHARS} chars")

    trig = meta.get("triggers")
    if trig is None:
        meta["triggers"] = []
    elif isinstance(trig, str):
        meta["triggers"] = [t.strip() for t in re.split(r"[,，;；]", trig) if t.strip()]
    elif isinstance(trig, list):
        meta["triggers"] = [str(t).strip() for t in trig if str(t).strip()]
    else:
        warnings.append("triggers must be a list, ignored")
        meta["triggers"] = []

    for list_key in ("allowed-tools", "forbidden_tools"):
        val = meta.get(list_key)
        if isinstance(val, str):
            meta[list_key] = [val]
        elif isinstance(val, list):
            meta[list_key] = [str(v) for v in val]
        elif val is not None:
            meta[list_key] = []

    hooks = meta.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        warnings.append("hooks must be a mapping, ignored")
        meta["hooks"] = {}

    return meta, warnings


def skill_from_text(
    text: str, fallback_name: str, source: str, priority: int, dir_path: str
) -> Skill:
    """Build a Skill from raw SKILL.md text (never raises on bad content)."""
    meta, body, warnings = parse_frontmatter(text)
    meta, san_warnings = sanitize_frontmatter(meta, fallback_name)
    warnings.extend(san_warnings)

    def _bool(key: str, default: bool = False) -> bool:
        val = meta.get(key)
        return bool(val) if val is not None else default

    return Skill(
        name=str(meta.get("name", fallback_name)),
        content=body.strip(),
        description=str(meta.get("description") or ""),
        name_zh=str(meta.get("name_zh") or ""),
        description_zh=str(meta.get("description_zh") or ""),
        triggers=list(meta.get("triggers") or []),
        context=meta.get("context"),
        agent=meta.get("agent"),
        argument_hint=meta.get("argument-hint"),
        allowed_tools=list(meta.get("allowed-tools") or []),
        forbidden_tools=list(meta.get("forbidden_tools") or []),
        model=meta.get("model"),
        user_invocable=_bool("user-invocable", True),
        disable_model_invocation=_bool("disable-model-invocation", False),
        auto_summarize=_bool("auto_summarize", False),
        always_show=_bool("always-show", False),
        fork_agent=_bool("fork_agent", False),
        hooks=meta.get("hooks") or {},
        source=source,
        priority=priority,
        dir_path=dir_path,
        warnings=warnings,
    )


def load_skill_file(
    path: Path, source: str, priority: int
) -> Skill | None:
    """Load ``path`` (a SKILL.md file). Returns None on unreadable files."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("skillhub: cannot read %s: %s", path, e)
        return None
    fallback = slugify(path.parent.name) or "unnamed-skill"
    skill = skill_from_text(text, fallback, source, priority, str(path.parent))
    if skill.warnings:
        logger.info("skillhub: loaded %s with warnings: %s", path, skill.warnings)
    return skill


def build_skill_content(meta: dict[str, Any], body: str) -> str:
    """Render SKILL.md text from frontmatter dict + body.

    Port of OpenClacky skill_loader.rb ``build_skill_content``:
    ``"---\\n{yaml}---\\n\\n{content}"``.
    """
    yaml_text = yaml.safe_dump(
        meta, allow_unicode=True, sort_keys=False, default_flow_style=False
    ).rstrip("\n")
    body = body.strip()
    if body:
        return f"---\n{yaml_text}\n---\n\n{body}\n"
    return f"---\n{yaml_text}\n---\n"


def validate_skill_payload(name: str, body: str) -> None:
    """Raise SkillValidationError for payloads that must not hit the disk."""
    if not is_valid_slug(name):
        raise SkillValidationError(
            f"invalid skill name {name!r}: must match ^[a-z0-9][a-z0-9-]*$"
        )
    if not body or not body.strip():
        raise SkillValidationError("skill content must not be empty")
