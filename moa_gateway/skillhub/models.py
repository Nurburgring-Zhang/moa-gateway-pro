"""Skill data model for SkillHub (M7).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/skill.rb`` — the ``Skill`` class: ``FRONTMATTER_FIELDS`` schema,
  ``DESCRIPTION_MAX_CHARS`` (340) truncation, ``identifier`` semantics and the
  ``to_h`` serialization shape.

Adapted to moa_gateway_pro v4.1.0: Python dataclass instead of a Ruby class,
plus a ``triggers`` list (moa_gateway extension used by fuzzy search) and
``source``/``priority`` bookkeeping for multi-source discovery.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

#: Maximum description length, identical to OpenClacky skill.rb.
DESCRIPTION_MAX_CHARS = 340

#: Recognized YAML frontmatter fields, ported from OpenClacky skill.rb
#: FRONTMATTER_FIELDS (kebab-case keys as they appear in SKILL.md files).
FRONTMATTER_FIELDS: tuple[str, ...] = (
    "name",
    "name_zh",
    "description",
    "description_zh",
    "disable-model-invocation",
    "user-invocable",
    "allowed-tools",
    "context",
    "agent",
    "argument-hint",
    "hooks",
    "fork_agent",
    "model",
    "forbidden_tools",
    "auto_summarize",
    "always-show",
    # moa_gateway extension: search triggers (list of keywords/phrases)
    "triggers",
)


@dataclass
class Skill:
    """A loaded, ready-to-use skill.

    ``content`` holds the markdown body of SKILL.md (everything after the
    frontmatter) — this is what gets injected into the model prompt on invoke.
    """

    name: str
    content: str = ""
    description: str = ""
    name_zh: str = ""
    description_zh: str = ""
    triggers: list[str] = field(default_factory=list)
    context: str | None = None
    agent: str | None = None
    argument_hint: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    model: str | None = None
    user_invocable: bool = True
    disable_model_invocation: bool = False
    auto_summarize: bool = False
    always_show: bool = False
    fork_agent: bool = False
    hooks: dict[str, Any] = field(default_factory=dict)
    #: discovery source label: "bundled" | "extra" | "user"
    source: str = "bundled"
    #: eviction priority; higher wins when two sources define the same name
    priority: int = 0
    #: absolute path of the skill directory (contains SKILL.md)
    dir_path: str = ""
    #: non-fatal parse warnings (lenient frontmatter handling)
    warnings: list[str] = field(default_factory=list)
    loaded_at: float = field(default_factory=time.time)

    # ---------- OpenClacky-compatible accessors ----------

    def identifier(self) -> str:
        """Canonical identifier — the slug name (skill.rb ``identifier``)."""
        return self.name

    def display_name(self) -> str:
        """Human-facing name, preferring the localized variant."""
        return self.name_zh or self.name

    def display_description(self) -> str:
        return self.description_zh or self.description

    def to_dict(self) -> dict[str, Any]:
        """Serialize for API responses (shape inspired by skill.rb ``to_h``)."""
        return {
            "name": self.name,
            "name_zh": self.name_zh,
            "description": self.description,
            "description_zh": self.description_zh,
            "triggers": list(self.triggers),
            "context": self.context,
            "agent": self.agent,
            "argument_hint": self.argument_hint,
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "model": self.model,
            "user_invocable": self.user_invocable,
            "disable_model_invocation": self.disable_model_invocation,
            "auto_summarize": self.auto_summarize,
            "always_show": self.always_show,
            "fork_agent": self.fork_agent,
            "hooks": dict(self.hooks) if isinstance(self.hooks, dict) else {},
            "source": self.source,
            "priority": self.priority,
            "dir_path": self.dir_path,
            "content_chars": len(self.content),
        }

    def to_frontmatter_dict(self) -> dict[str, Any]:
        """Frontmatter fields in on-disk (kebab-case) representation."""
        meta: dict[str, Any] = {"name": self.name}
        if self.name_zh:
            meta["name_zh"] = self.name_zh
        if self.description:
            meta["description"] = self.description
        if self.description_zh:
            meta["description_zh"] = self.description_zh
        if self.triggers:
            meta["triggers"] = list(self.triggers)
        if self.context:
            meta["context"] = self.context
        if self.agent:
            meta["agent"] = self.agent
        if self.argument_hint:
            meta["argument-hint"] = self.argument_hint
        if self.allowed_tools:
            meta["allowed-tools"] = list(self.allowed_tools)
        if self.forbidden_tools:
            meta["forbidden_tools"] = list(self.forbidden_tools)
        if self.model:
            meta["model"] = self.model
        if not self.user_invocable:
            meta["user-invocable"] = False
        if self.disable_model_invocation:
            meta["disable-model-invocation"] = True
        if self.auto_summarize:
            meta["auto_summarize"] = True
        if self.always_show:
            meta["always-show"] = True
        if self.fork_agent:
            meta["fork_agent"] = True
        if self.hooks:
            meta["hooks"] = self.hooks
        return meta
