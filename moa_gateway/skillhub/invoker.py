"""invoke_skill meta-tool — skill prompt assembly + real gateway LLM call.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License):
- ``lib/clacky/tools/invoke_skill.rb`` — tool_name ``invoke_skill`` with
  parameters ``{skill_name, task}`` (both required), resolving the skill and
  injecting it into the model context;
- ``lib/clacky/agent/skill_manager.rb`` — skill prompt assembly
  (AVAILABLE SKILLS / skill-content injection, MAX_CONTEXT_SKILLS cap).

Every execution goes through ``ModelPool`` — the same pipeline that serves
``/v1/chat/completions``. Endpoint selection is real (``select_one`` + tier
descent); when an endpoint carries no production credential, the gateway's
own credential-less provider semantics apply (``providers.build_provider``),
which is the documented no-key behavior of this gateway.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .discovery import SkillRegistry
from .errors import SkillInvokeError
from .models import Skill

logger = logging.getLogger(__name__)

#: Prompt cap, same spirit as OpenClacky MAX_CONTEXT_SKILLS: keep injected
#: skill content bounded so huge SKILL.md files cannot blow up the context.
MAX_SKILL_PROMPT_CHARS = 24_000

DEFAULT_TIER_VALUE = "standard"


def build_skill_prompt(skill: Skill) -> str:
    """Assemble the system prompt that executes a skill (prompt injection)."""
    parts = [
        "You are executing a skill inside the MOA gateway. Follow the skill's "
        "instructions exactly and produce the output format it specifies.",
        "",
        f"# Skill: {skill.name}" + (f" ({skill.display_name()})" if skill.name_zh else ""),
    ]
    if skill.description:
        parts.append(f"Description: {skill.description}")
    if skill.argument_hint:
        parts.append(f"Argument hint: {skill.argument_hint}")
    if skill.allowed_tools:
        parts.append(f"Allowed tools: {', '.join(skill.allowed_tools)}")
    if skill.forbidden_tools:
        parts.append(f"Forbidden tools: {', '.join(skill.forbidden_tools)}")
    parts.append("")
    body = skill.content or ""
    if len(body) > MAX_SKILL_PROMPT_CHARS:
        body = body[:MAX_SKILL_PROMPT_CHARS] + "\n\n[skill content truncated]"
    parts.append(body)
    return "\n".join(parts)


def build_messages(skill: Skill, task: str) -> list[dict[str, str]]:
    """OpenAI-style messages for the pipeline call."""
    return [
        {"role": "system", "content": build_skill_prompt(skill)},
        {"role": "user", "content": task.strip()},
    ]


async def call_model_pipeline(
    messages: list[dict[str, str]],
    tier_value: str = DEFAULT_TIER_VALUE,
    temperature: float = 0.6,
    max_tokens: int = 4096,
) -> tuple[Any, Any]:
    """Run messages through the gateway's real ModelPool.

    Returns ``(ChatResponse, ModelEndpoint)``. Descends tiers when the
    requested tier has no available endpoint; raises SkillInvokeError(503)
    when nothing is available, and SkillInvokeError(502) on call failure.
    """
    from ..model_pool import ModelTier, get_model_pool

    try:
        tier = ModelTier(tier_value)
    except ValueError:
        tier = ModelTier.STANDARD

    pool = get_model_pool()
    ep = pool.select_one(tier)
    while ep is None and tier.rank > 0:
        tier = tier.previous()
        ep = pool.select_one(tier)
    if ep is None:
        raise SkillInvokeError(
            "no model endpoint available for skill execution "
            "(configure at least one endpoint in config.yaml)",
            status_code=503,
        )
    try:
        resp = await pool.call(
            ep.id, messages, temperature=temperature, max_tokens=max_tokens
        )
    except SkillInvokeError:
        raise
    except Exception as e:  # ProviderError / network / auth failures
        logger.error("skillhub: pipeline call failed on endpoint %s: %s", ep.id, e)
        raise SkillInvokeError(f"model pipeline call failed: {e}") from e
    return resp, ep


async def invoke_skill(
    name: str,
    task: str,
    registry: SkillRegistry | None = None,
    tier_value: str = DEFAULT_TIER_VALUE,
) -> dict[str, Any]:
    """Execute ``task`` under skill ``name`` through the real LLM pipeline.

    Mirrors OpenClacky invoke_skill semantics: parameters are exactly
    ``skill_name`` + ``task``; the skill content is injected into the model
    context and the produced answer is returned together with execution
    metadata (endpoint, provider, tokens, latency).
    """
    from ..config import get_settings

    if not task or not task.strip():
        raise SkillInvokeError("task must not be empty", status_code=422)

    settings = get_settings()
    if settings.skillhub.max_skills_in_prompt < 1:
        raise SkillInvokeError("skillhub.max_skills_in_prompt must be >= 1", status_code=500)

    reg = registry or SkillRegistry()
    skill = reg.require(name)
    if skill.disable_model_invocation:
        raise SkillInvokeError(
            f"skill '{name}' has model invocation disabled "
            "(disable-model-invocation: true)",
            status_code=403,
        )

    started = time.perf_counter()
    resp, ep = await call_model_pipeline(
        build_messages(skill, task), tier_value=tier_value
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    # Usage accounting for evolution hooks (best-effort, never breaks invoke).
    try:
        from .evolution import SkillEvolutionStore

        SkillEvolutionStore().record_invocation(skill.name, task, ok=True)
    except Exception as e:  # pragma: no cover - storage unavailable edge
        logger.warning("skillhub: failed to record usage for %s: %s", skill.name, e)

    logger.info(
        "skillhub: invoked skill=%s endpoint=%s provider=%s tokens=%d in %.1fms",
        skill.name, ep.id, getattr(resp, "provider", ""), getattr(resp, "total_tokens", 0),
        elapsed_ms,
    )
    return {
        "skill": skill.name,
        "task": task.strip(),
        "content": resp.content,
        "endpoint_id": ep.id,
        "model": getattr(resp, "model", "") or ep.config.model,
        "provider": getattr(resp, "provider", ""),
        "finish_reason": getattr(resp, "finish_reason", ""),
        "prompt_tokens": getattr(resp, "prompt_tokens", 0),
        "completion_tokens": getattr(resp, "completion_tokens", 0),
        "total_tokens": getattr(resp, "total_tokens", 0),
        "latency_ms": round(elapsed_ms, 2),
    }
