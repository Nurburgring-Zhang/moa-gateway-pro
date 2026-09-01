"""SkillHub HTTP routes (M7) — /v1/skills*.

Design ported from OpenClacky (https://github.com/clacky-ai/openclacky,
MIT License): the skill lifecycle its agent exposes (list / inspect / search /
invoke / create / update / delete) re-expressed as gateway REST endpoints,
with the evolution records (usage stats + improvement suggestions) surfaced
for the admin UI.

Auth model: reads require a valid API key; any write (create/update/delete)
requires admin. Every endpoint is gated by the ``skillhub`` capability toggle.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..auth import require_admin, require_api_key
from ..capability_toggles import require_capability
from ..skillhub import (
    SkillEvolutionManager,
    SkillHubError,
    SkillRegistry,
    create_skill_from_description,
    invoke_skill,
    search_skills,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["skillhub"])


# ---------- request models ----------


class SkillCreateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    content: str | None = None
    meta: dict[str, Any] | None = None
    #: force the deterministic template engine (skip the LLM generation path)
    force_template: bool = False


class SkillUpdateRequest(BaseModel):
    content: str | None = None
    description: str | None = None
    meta: dict[str, Any] | None = None


class SkillSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=400)
    top_k: int = Field(default=5, ge=1, le=50)


class SkillInvokeRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20_000)
    tier: str | None = Field(
        default=None, pattern="^(free|lite|standard|premium|flagship)$"
    )


# ---------- helpers ----------


def _registry() -> SkillRegistry:
    return SkillRegistry()


def _err(e: SkillHubError) -> JSONResponse:
    return JSONResponse(status_code=e.status_code, content={"error": e.message})


# ---------- read endpoints ----------


@router.get("/v1/skills")
async def list_skills(
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("skillhub")),
    source: str | None = Query(default=None, pattern="^(bundled|extra|user)$"),
):
    """List all discovered skills (optionally filtered by source)."""
    try:
        reg = _registry()
        skills = reg.list_skills()
        if source:
            skills = [s for s in skills if s.source == source]
        payload = {
            "skills": [s.to_dict() for s in skills],
            "count": len(skills),
            "sources": {
                "bundled": sum(1 for s in skills if s.source == "bundled"),
                "extra": sum(1 for s in skills if s.source == "extra"),
                "user": sum(1 for s in skills if s.source == "user"),
            },
        }
        return payload
    except SkillHubError as e:
        return _err(e)


@router.get("/v1/skills/evolution/suggestions")
async def list_evolution_suggestions(
    skill_name: str | None = Query(default=None),
    kind: str | None = Query(default=None, pattern="^(reflect|auto_create)$"),
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Improvement-suggestion records produced by the evolution hooks."""
    try:
        store = SkillEvolutionManager().store
        suggestions = store.list_suggestions(skill_name=skill_name, kind=kind)
        return {"suggestions": suggestions, "count": len(suggestions)}
    except SkillHubError as e:
        return _err(e)


@router.post("/v1/skills/search")
async def search_skills_route(
    req: SkillSearchRequest,
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Fuzzy search across name / description / triggers."""
    try:
        reg = _registry()
        results = search_skills(req.query, reg.list_skills(), top_k=req.top_k)
        return {
            "query": req.query,
            "results": [r.to_dict() for r in results],
            "count": len(results),
        }
    except SkillHubError as e:
        return _err(e)


@router.get("/v1/skills/{name}")
async def get_skill(
    name: str,
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("skillhub")),
    with_content: bool = Query(default=False),
):
    """Fetch one skill by name."""
    try:
        skill = _registry().require(name)
        payload: dict[str, Any] = {"skill": skill.to_dict()}
        if with_content:
            payload["skill"]["content"] = skill.content
        payload["usage"] = SkillEvolutionManager().store.stats(name)
        return payload
    except SkillHubError as e:
        return _err(e)


@router.get("/v1/skills/{name}/stats")
async def get_skill_stats(
    name: str,
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Usage accounting + evolution suggestions for one skill."""
    try:
        reg = _registry()
        skill = reg.require(name)
        store = SkillEvolutionManager().store
        return {
            "skill": skill.name,
            "usage": store.stats(skill.name),
            "suggestions": store.list_suggestions(skill_name=skill.name),
        }
    except SkillHubError as e:
        return _err(e)


# ---------- write endpoints (admin) ----------


@router.post("/v1/skills", status_code=201)
async def create_skill(
    req: SkillCreateRequest,
    _admin: dict = Depends(require_admin),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Create a skill.

    Two modes:
    - explicit: provide ``name`` + ``content`` (+ optional ``meta``) -> the
      SKILL.md is written exactly as given;
    - generated: provide ``description`` (name optional) -> a valid SKILL.md
      is generated (LLM pipeline when available, deterministic template
      engine otherwise) and written to the user skill dir.
    """
    try:
        reg = _registry()
        if req.content is not None:
            if not req.name:
                return JSONResponse(
                    status_code=422,
                    content={"error": "name is required when content is provided"},
                )
            meta = dict(req.meta or {})
            if req.description:
                meta.setdefault("description", req.description)
            path = reg.save_skill(req.name, meta, req.content, overwrite=True)
            skill = reg.require(req.name)
            return {
                "skill": skill.to_dict(),
                "path": str(path),
                "generated_by": "explicit",
            }
        if req.description:
            created = await create_skill_from_description(
                description=req.description,
                name_hint=req.name,
                registry=reg,
                force_template=req.force_template,
            )
            return created
        return JSONResponse(
            status_code=422,
            content={"error": "provide either 'content' or 'description'"},
        )
    except SkillHubError as e:
        return _err(e)


@router.put("/v1/skills/{name}")
async def update_skill(
    name: str,
    req: SkillUpdateRequest,
    _admin: dict = Depends(require_admin),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Update a user skill's body and/or frontmatter fields."""
    try:
        reg = _registry()
        skill = reg.require(name)
        if skill.source != "user":
            return JSONResponse(
                status_code=403,
                content={
                    "error": f"skill '{name}' comes from the read-only "
                    f"'{skill.source}' source"
                },
            )
        meta = skill.to_frontmatter_dict()
        if req.meta:
            for k, v in req.meta.items():
                if k == "name":
                    continue  # renaming = delete + create; keep identity stable
                meta[k] = v
        if req.description is not None:
            meta["description"] = req.description
        body = req.content if req.content is not None else skill.content
        path = reg.save_skill(name, meta, body, overwrite=True)
        return {"skill": reg.require(name).to_dict(), "path": str(path)}
    except SkillHubError as e:
        return _err(e)


@router.delete("/v1/skills/{name}")
async def delete_skill(
    name: str,
    _admin: dict = Depends(require_admin),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Delete a user-created skill (bundled/extra sources are protected)."""
    try:
        removed_dir = _registry().delete_skill(name)
        return {"deleted": name, "dir": removed_dir}
    except SkillHubError as e:
        return _err(e)


# ---------- invoke ----------


@router.post("/v1/skills/{name}/invoke")
async def invoke_skill_route(
    name: str,
    req: SkillInvokeRequest,
    _user: dict = Depends(require_api_key),
    _cap: None = Depends(require_capability("skillhub")),
):
    """Execute a task under a skill through the gateway's real model pipeline."""
    try:
        reg = _registry()
        result = await invoke_skill(
            name,
            req.task,
            registry=reg,
            tier_value=req.tier or "standard",
        )
        # Evolution milestone check (usage already recorded by invoke_skill).
        try:
            milestone = await SkillEvolutionManager().check_milestone(reg.require(name))
            if milestone:
                result["evolution"] = milestone
        except Exception as e:  # pragma: no cover - never break invoke
            logger.warning("skillhub: milestone check failed for %s: %s", name, e)
        return result
    except SkillHubError as e:
        return _err(e)
