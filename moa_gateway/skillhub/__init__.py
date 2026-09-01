"""SkillHub (M7) — OpenClacky-style skill ecosystem for moa_gateway_pro.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License);
per-module attribution headers document exactly what was ported from where.

Capabilities:
- SKILL.md loading (YAML frontmatter + markdown body, lenient parsing)
- multi-source discovery: bundled packs / settings.skillhub.extra_dirs /
  user skills under data/skills (priority eviction)
- weighted fuzzy search over name / description / triggers
- ``invoke_skill`` meta-tool running through the gateway's real model pipeline
- natural-language skill creation (LLM path + deterministic template engine)
- evolution hooks: usage accounting, reflection suggestions at
  ``evolution_min_iterations``, auto-creation at ``auto_create_min_iterations``
"""

from .creator import (
    create_skill_from_description,
    deterministic_skill,
    extract_triggers,
    parse_llm_skill_output,
)
from .discovery import SkillRegistry, bundled_packs_dir, default_user_skills_dir
from .errors import (
    SkillHubError,
    SkillInvokeError,
    SkillNotFoundError,
    SkillProtectedError,
    SkillValidationError,
)
from .evolution import SkillEvolutionManager, SkillEvolutionStore, heuristic_review
from .invoker import build_skill_prompt, call_model_pipeline, invoke_skill
from .loader import (
    build_skill_content,
    is_valid_slug,
    load_skill_file,
    parse_frontmatter,
    sanitize_frontmatter,
    skill_from_text,
    slugify,
)
from .models import DESCRIPTION_MAX_CHARS, FRONTMATTER_FIELDS, Skill
from .search import SearchResult, score_skill, search_skills

__all__ = [
    "DESCRIPTION_MAX_CHARS",
    "FRONTMATTER_FIELDS",
    "Skill",
    "SkillRegistry",
    "SkillHubError",
    "SkillNotFoundError",
    "SkillValidationError",
    "SkillProtectedError",
    "SkillInvokeError",
    "SearchResult",
    "SkillEvolutionStore",
    "SkillEvolutionManager",
    "bundled_packs_dir",
    "default_user_skills_dir",
    "parse_frontmatter",
    "sanitize_frontmatter",
    "skill_from_text",
    "load_skill_file",
    "build_skill_content",
    "is_valid_slug",
    "slugify",
    "score_skill",
    "search_skills",
    "build_skill_prompt",
    "call_model_pipeline",
    "invoke_skill",
    "create_skill_from_description",
    "deterministic_skill",
    "extract_triggers",
    "parse_llm_skill_output",
    "heuristic_review",
]
