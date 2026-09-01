"""M7 SkillHub tests — loader, discovery, search, invoke, creator, evolution, routes.

Test double boundary: no protocol mocks are needed here; LLM calls go through
the gateway's real ModelPool, which under the default explicit-mock mode and
key-less endpoints exercises the gateway's own MockProvider (the documented
credential-less behavior). Route tests follow tests/conftest.py isolation and
the tests/test_assistant_api.py app/client pattern.
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from moa_gateway.skillhub import (
    Skill,
    SkillEvolutionManager,
    SkillEvolutionStore,
    SkillRegistry,
    build_skill_content,
    create_skill_from_description,
    deterministic_skill,
    extract_triggers,
    heuristic_review,
    invoke_skill,
    is_valid_slug,
    parse_frontmatter,
    parse_llm_skill_output,
    sanitize_frontmatter,
    score_skill,
    search_skills,
    skill_from_text,
    slugify,
)
from moa_gateway.skillhub.discovery import bundled_packs_dir
from moa_gateway.skillhub.errors import (
    SkillInvokeError,
    SkillNotFoundError,
    SkillProtectedError,
    SkillValidationError,
)
from moa_gateway.skillhub.loader import load_skill_file

BUNDLED_NAMES = {"summarize", "translate", "code-review", "data-analysis", "writing-polish"}


# ---------- fixtures ----------


@pytest.fixture
def storage(tmp_path, make_settings):
    """Real isolated Storage registered as the singleton (for evolution tables)."""
    from moa_gateway.storage import Storage

    settings = make_settings()
    with patch("moa_gateway.storage.get_settings", return_value=settings):
        Storage._instance = None
        s = Storage(db_path=tmp_path / "skillhub.db")
        Storage._instance = s
        yield s
        Storage._instance = None


@pytest.fixture
def mock_pool(monkeypatch):
    """Settings + ModelPool with one key-less (=> gateway MockProvider) endpoint."""
    import moa_gateway.config as cfg
    import moa_gateway.model_pool as mp
    from moa_gateway.config import ModelEndpointConfig, Settings

    settings = Settings(
        auth={
            "admin_username": "admin",
            "admin_password": "SkillHubP@ss!2024",
            "jwt_secret": "skill-hub-test-secret-long-enough-for-hs256-xyz",
            "jwt_expire_minutes": 60,
            "gateway_api_keys": [],
        },
        models=[
            ModelEndpointConfig(
                id="mock-standard",
                provider="deepseek",
                model="deepseek-chat",
                tier="standard",
                enabled=True,
            )
        ],
    )
    monkeypatch.setattr(cfg, "_settings", settings)
    monkeypatch.setattr(mp, "_pool", None)
    pool = mp.get_model_pool()
    yield pool
    monkeypatch.setattr(mp, "_pool", None)


@pytest.fixture
def user_dir(tmp_path) -> Path:
    d = tmp_path / "user_skills"
    d.mkdir()
    return d


@pytest.fixture
def registry(user_dir) -> SkillRegistry:
    return SkillRegistry(extra_dirs=[], user_dir=user_dir)


# ---------- loader ----------


def test_parse_frontmatter_valid():
    text = "---\nname: my-skill\ndescription: does things\ntriggers:\n  - foo\n---\n\n# Body\ncontent"
    meta, body, warnings = parse_frontmatter(text)
    assert meta["name"] == "my-skill"
    assert meta["triggers"] == ["foo"]
    assert body.lstrip().startswith("# Body")
    assert warnings == []


def test_parse_frontmatter_no_block():
    meta, body, warnings = parse_frontmatter("# Just a doc\nno frontmatter")
    assert meta == {}
    assert body.startswith("# Just a doc")
    assert warnings == []


def test_parse_frontmatter_invalid_yaml_lenient():
    text = "---\nname: [unclosed\nbad: {yaml\n---\nreal content"
    meta, body, warnings = parse_frontmatter(text)
    assert meta == {}
    assert "real content" in body  # whole file kept as content
    assert warnings and "YAML" in warnings[0]


def test_parse_frontmatter_non_mapping():
    meta, body, warnings = parse_frontmatter("---\n- a\n- b\n---\nx")
    assert meta == {}
    assert warnings


def test_sanitize_name_fallback_to_name_zh():
    meta, warnings = sanitize_frontmatter({"name": "Bad Name!", "name_zh": "润色文稿"}, "dir-slug")
    assert meta["name"] == "dir-slug" or is_valid_slug(meta["name"])
    assert warnings  # fallback recorded


def test_sanitize_name_fallback_to_dir_slug():
    meta, warnings = sanitize_frontmatter({"name": "!!!"}, "fallback-dir")
    assert meta["name"] == "fallback-dir"
    assert warnings


def test_description_truncation_340():
    long_desc = "x" * 500
    meta, warnings = sanitize_frontmatter({"name": "ok", "description": long_desc}, "d")
    assert len(meta["description"]) == 340
    assert any("truncated" in w for w in warnings)


def test_triggers_string_coerced():
    meta, _ = sanitize_frontmatter({"name": "ok", "triggers": "a, b，c"}, "d")
    assert meta["triggers"] == ["a", "b", "c"]


def test_slugify_and_validation():
    assert slugify("My Cool Skill!") == "my-cool-skill"
    assert slugify("  --Weird__case--  ") == "weird-case"
    assert slugify("纯中文") == ""
    assert is_valid_slug("abc-123")
    assert not is_valid_slug("-abc")
    assert not is_valid_slug("Abc")
    assert not is_valid_slug("")


def test_build_skill_content_roundtrip():
    meta = {"name": "round", "description": "d", "triggers": ["t1"]}
    text = build_skill_content(meta, "# Title\nbody here")
    meta2, body2, warnings = parse_frontmatter(text)
    assert meta2["name"] == "round"
    assert body2.strip().startswith("# Title")
    assert warnings == []


def test_load_real_bundled_pack():
    path = bundled_packs_dir() / "summarize" / "SKILL.md"
    skill = load_skill_file(path, "bundled", 0)
    assert skill is not None
    assert skill.name == "summarize"
    assert "Workflow" in skill.content
    assert "总结" in skill.triggers


# ---------- discovery ----------


def test_bundled_packs_discovered(registry):
    skills = registry.load_all()
    assert BUNDLED_NAMES <= set(skills)
    for name in BUNDLED_NAMES:
        assert skills[name].source == "bundled"
        assert skills[name].description  # real descriptions, not placeholders


def test_extra_dir_loading(tmp_path, user_dir):
    extra = tmp_path / "extra"
    (extra / "my-extra").mkdir(parents=True)
    (extra / "my-extra" / "SKILL.md").write_text(
        "---\nname: my-extra\ndescription: from extra\n---\nbody", encoding="utf-8"
    )
    reg = SkillRegistry(extra_dirs=[str(extra)], user_dir=user_dir)
    skill = reg.get("my-extra")
    assert skill is not None and skill.source == "extra"


def test_two_level_category_layout(tmp_path, user_dir):
    nested = tmp_path / "catroot" / "category-a" / "deep-skill"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: deep-skill\ndescription: nested\n---\nbody", encoding="utf-8"
    )
    reg = SkillRegistry(extra_dirs=[str(tmp_path / "catroot")], user_dir=user_dir)
    assert reg.get("deep-skill") is not None


def test_user_overrides_bundled(registry, user_dir):
    (user_dir / "summarize").mkdir()
    (user_dir / "summarize" / "SKILL.md").write_text(
        "---\nname: summarize\ndescription: my override\n---\ncustom body",
        encoding="utf-8",
    )
    skill = registry.load_all(force=True)["summarize"]
    assert skill.source == "user"
    assert skill.description == "my override"


def test_save_skill_creates_real_file(registry, user_dir):
    path = registry.save_skill("fresh-skill", {"description": "d"}, "# Fresh\nbody")
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "name: fresh-skill" in text
    assert registry.get("fresh-skill") is not None


def test_save_skill_invalid_slug(registry):
    with pytest.raises(SkillValidationError):
        registry.save_skill("Bad Name", {}, "body")


def test_save_skill_empty_body(registry):
    with pytest.raises(SkillValidationError):
        registry.save_skill("ok-name", {}, "   ")


def test_delete_user_skill(registry, user_dir):
    registry.save_skill("temp-skill", {"description": "d"}, "body")
    removed = registry.delete_skill("temp-skill")
    assert not Path(removed).exists()
    assert registry.get("temp-skill") is None


def test_delete_bundled_protected(registry):
    with pytest.raises(SkillProtectedError):
        registry.delete_skill("summarize")


def test_delete_missing(registry):
    with pytest.raises(SkillNotFoundError):
        registry.delete_skill("no-such-skill")


# ---------- search ----------


def test_search_exact_name(registry):
    skill = registry.require("summarize")
    result = score_skill("summarize", skill)
    assert result.score == 1.0


def test_search_zh_trigger(registry):
    results = search_skills("总结", registry.list_skills(), top_k=3)
    assert results and results[0].skill.name == "summarize"


def test_search_fuzzy_typo(registry):
    results = search_skills("summrize", registry.list_skills(), top_k=3)
    assert results and results[0].skill.name == "summarize"


def test_search_description_substring(registry):
    results = search_skills("code review security findings", registry.list_skills(), top_k=3)
    assert results and results[0].skill.name == "code-review"


def test_search_chinese_intent(registry):
    results = search_skills("帮我翻译这段英文", registry.list_skills(), top_k=3)
    assert results and results[0].skill.name == "translate"


def test_search_empty_query(registry):
    assert search_skills("   ", registry.list_skills()) == []


def test_search_topk_and_ranking(registry):
    results = search_skills("translate", registry.list_skills(), top_k=2)
    assert len(results) <= 2
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


# ---------- invoker (real pipeline via gateway MockProvider semantics) ----------


def test_build_skill_prompt_contains_content(registry):
    from moa_gateway.skillhub import build_skill_prompt

    skill = registry.require("summarize")
    prompt = build_skill_prompt(skill)
    assert "# Skill: summarize" in prompt
    assert "TL;DR" in prompt  # skill body really injected


async def test_invoke_skill_real_pipeline(registry, mock_pool, storage):
    result = await invoke_skill("summarize", "帮我总结一段会议纪要", registry=registry)
    assert result["skill"] == "summarize"
    assert result["provider"] == "mock"  # gateway's own Mock endpoint semantics
    assert result["content"].strip()
    assert result["endpoint_id"] == "mock-standard"
    assert result["total_tokens"] > 0
    # usage really persisted for evolution hooks
    assert SkillEvolutionStore().stats("summarize")["invocations"] == 1


async def test_invoke_unknown_skill(registry):
    with pytest.raises(SkillNotFoundError):
        await invoke_skill("ghost-skill", "task", registry=registry)


async def test_invoke_disabled_model_invocation(registry, mock_pool):
    registry.save_skill(
        "no-llm", {"description": "d", "disable-model-invocation": True}, "body"
    )
    with pytest.raises(SkillInvokeError) as ei:
        await invoke_skill("no-llm", "task", registry=registry)
    assert ei.value.status_code == 403


async def test_invoke_empty_task(registry, mock_pool):
    with pytest.raises(SkillInvokeError) as ei:
        await invoke_skill("summarize", "   ", registry=registry)
    assert ei.value.status_code == 422


async def test_invoke_no_endpoints_503(registry, monkeypatch):
    import moa_gateway.config as cfg
    import moa_gateway.model_pool as mp
    from moa_gateway.config import Settings

    monkeypatch.setattr(cfg, "_settings", Settings(auth={
        "admin_username": "admin",
        "admin_password": "SkillHubP@ss!2024",
        "jwt_secret": "skill-hub-test-secret-long-enough-for-hs256-xyz",
        "jwt_expire_minutes": 60,
    }))
    monkeypatch.setattr(mp, "_pool", None)
    with pytest.raises(SkillInvokeError) as ei:
        await invoke_skill("summarize", "task", registry=registry)
    assert ei.value.status_code == 503
    monkeypatch.setattr(mp, "_pool", None)


# ---------- creator ----------


def test_deterministic_skill_valid_and_parseable():
    slug, meta, body = deterministic_skill(
        None, "Generate weekly status reports from team git activity"
    )
    assert is_valid_slug(slug)
    text = build_skill_content(meta, body)
    meta2, body2, warnings = parse_frontmatter(text)
    assert meta2["name"] == slug
    assert warnings == []
    assert "Workflow" in body2 and "weekly status reports" in body2.lower()
    assert meta["triggers"]  # real extracted keywords


def test_extract_triggers_ascii_and_zh():
    trig = extract_triggers("帮我把会议录音转成待办清单并分派负责人")
    assert any("待办清单" in t for t in trig)
    trig_en = extract_triggers("Summarize meeting notes into action items")
    assert "meeting" in trig_en or "notes" in trig_en or "action" in trig_en


def test_parse_llm_output_fenced():
    text = (
        "Here you go:\n```markdown\n---\nname: gen-skill\n"
        "description: generated\n---\n\n# Gen\nBody text\n```\n"
    )
    parsed = parse_llm_skill_output(text, "fallback")
    assert parsed is not None
    meta, body = parsed
    assert meta["name"] == "gen-skill"
    assert "Body text" in body


def test_parse_llm_output_invalid():
    assert parse_llm_skill_output("no frontmatter at all", "fb") is None
    assert parse_llm_skill_output("", "fb") is None


async def test_create_skill_template_path(registry):
    created = await create_skill_from_description(
        description="把 CSV 销售数据画成图表并给出洞察",
        name_hint="csv-chart",
        registry=registry,
        force_template=True,
    )
    assert created["generated_by"] == "template"
    path = Path(created["path"])
    assert path.is_file()
    skill = registry.require(created["skill"]["name"])
    assert "CSV 销售数据" in skill.content or "csv" in skill.name


async def test_create_skill_llm_path_falls_back_gracefully(registry, monkeypatch):
    """LLM path attempted through the real pipeline; with zero endpoints it
    must honestly degrade to the template engine (still a valid SKILL.md)."""
    import moa_gateway.config as cfg
    import moa_gateway.model_pool as mp
    from moa_gateway.config import Settings

    monkeypatch.setattr(cfg, "_settings", Settings(auth={
        "admin_username": "admin",
        "admin_password": "SkillHubP@ss!2024",
        "jwt_secret": "skill-hub-test-secret-long-enough-for-hs256-xyz",
        "jwt_expire_minutes": 60,
    }))
    monkeypatch.setattr(mp, "_pool", None)
    created = await create_skill_from_description(
        description="Analyze log files and highlight errors", registry=registry
    )
    assert created["generated_by"] == "template"
    assert Path(created["path"]).is_file()
    monkeypatch.setattr(mp, "_pool", None)


# ---------- evolution ----------


def test_record_invocation_and_stats(storage):
    store = SkillEvolutionStore()
    assert store.record_invocation("s1", "task-a") == 1
    assert store.record_invocation("s1", "task-b", ok=False) == 2
    stats = store.stats("s1")
    assert stats["invocations"] == 2
    assert stats["successes"] == 1 and stats["failures"] == 1
    assert stats["last_task"] == "task-b"


def test_top_skills(storage):
    store = SkillEvolutionStore()
    store.record_invocation("a", "t")
    store.record_invocation("b", "t")
    store.record_invocation("b", "t")
    top = store.top_skills()
    assert top[0]["name"] == "b" and top[0]["invocations"] == 2


async def test_reflect_milestone_at_threshold(storage, registry):
    store = SkillEvolutionStore()
    manager = SkillEvolutionManager(store)
    skill = registry.require("translate")
    for i in range(5):  # default evolution_min_iterations == 5
        store.record_invocation(skill.name, f"task {i}")
    milestone = await manager.check_milestone(skill)
    assert milestone is not None
    assert milestone["iteration"] == 5
    suggestions = store.list_suggestions(skill_name=skill.name)
    assert len(suggestions) == 1
    assert suggestions[0]["kind"] == "reflect"
    assert len(suggestions[0]["suggestion"]) > 20  # real review text


async def test_no_suggestion_below_threshold(storage, registry):
    store = SkillEvolutionStore()
    manager = SkillEvolutionManager(store)
    skill = registry.require("translate")
    store.record_invocation(skill.name, "task")
    assert await manager.check_milestone(skill) is None
    assert store.list_suggestions() == []


async def test_milestone_dedup(storage, registry):
    store = SkillEvolutionStore()
    manager = SkillEvolutionManager(store)
    skill = registry.require("translate")
    for i in range(5):
        store.record_invocation(skill.name, f"task {i}")
    first = await manager.check_milestone(skill)
    second = await manager.check_milestone(skill)
    assert first is not None and second is None
    assert len(store.list_suggestions(skill_name=skill.name)) == 1


async def test_evolution_disabled_no_suggestions(storage, registry):
    import moa_gateway.config as cfg

    settings = cfg.get_settings()
    settings.skillhub.evolution_enabled = False
    store = SkillEvolutionStore()
    manager = SkillEvolutionManager(store)
    skill = registry.require("translate")
    for i in range(6):
        store.record_invocation(skill.name, f"t{i}")
    assert await manager.check_milestone(skill) is None
    settings.skillhub.evolution_enabled = True


def test_heuristic_review_flags_missing_pieces():
    skill = Skill(name="thin", description="short", content="just words")
    review = heuristic_review(skill, {"invocations": 3, "failures": 0, "last_task": "x"})
    assert "triggers" in review and "workflow" in review


def test_heuristic_review_solid_skill(registry):
    skill = registry.require("summarize")
    review = heuristic_review(skill, {"invocations": 7, "failures": 1, "last_task": "x"})
    assert "7 invocations" in review


async def test_auto_create_at_threshold(storage, registry, monkeypatch):
    import moa_gateway.config as cfg

    settings = cfg.get_settings()
    monkeypatch.setattr(settings.skillhub, "auto_create_min_iterations", 3)
    manager = SkillEvolutionManager(SkillEvolutionStore())
    task = "把周报汇总成管理层简报"
    assert await manager.maybe_auto_create(task) is None  # 1st
    assert await manager.maybe_auto_create(task) is None  # 2nd
    result = await manager.maybe_auto_create(task)        # 3rd -> threshold
    assert result is not None
    assert result["cluster_size"] == 3
    assert Path(result["path"]).is_file()
    suggestions = SkillEvolutionStore().list_suggestions(kind="auto_create")
    assert len(suggestions) == 1
    assert suggestions[0]["skill_name"] == result["skill"]


# ---------- HTTP routes ----------


@pytest.fixture
def app():
    from fastapi import FastAPI

    from moa_gateway.auth import require_admin, require_api_key
    from moa_gateway.routes.skillhub import router

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[require_api_key] = lambda: {"key": "test-key"}
    test_app.dependency_overrides[require_admin] = lambda: {
        "username": "admin",
        "role": "admin",
    }
    return test_app


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


async def test_route_list_skills(client):
    resp = await client.get("/v1/skills")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 5
    names = {s["name"] for s in data["skills"]}
    assert BUNDLED_NAMES <= names
    assert data["sources"]["bundled"] >= 5


async def test_route_list_skills_source_filter(client):
    resp = await client.get("/v1/skills", params={"source": "user"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


async def test_route_get_skill(client, storage):
    resp = await client.get("/v1/skills/summarize", params={"with_content": "true"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["skill"]["name"] == "summarize"
    assert "Workflow" in body["skill"]["content"]
    assert body["usage"]["invocations"] == 0


async def test_route_get_skill_404(client):
    resp = await client.get("/v1/skills/ghost")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]


async def test_route_create_explicit(client, tmp_path, storage):
    resp = await client.post(
        "/v1/skills",
        json={
            "name": "api-created",
            "content": "# Created via API\nReal body",
            "meta": {"description": "created through the route"},
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["generated_by"] == "explicit"
    assert Path(data["path"]).is_file()
    got = await client.get("/v1/skills/api-created")
    assert got.status_code == 200


async def test_route_create_generated(client):
    resp = await client.post(
        "/v1/skills",
        json={
            "description": "Turn raw SQL query results into plain-language insights",
            "name": "sql-insights",
            "force_template": True,
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["generated_by"] == "template"
    assert Path(data["path"]).is_file()


async def test_route_create_validation(client):
    resp = await client.post("/v1/skills", json={"name": "x"})
    assert resp.status_code == 422
    resp2 = await client.post("/v1/skills", json={"content": "body without name"})
    assert resp2.status_code == 422


async def test_route_update_skill(client, storage):
    await client.post(
        "/v1/skills", json={"name": "upd-skill", "content": "v1 body", "meta": {"description": "d"}}
    )
    resp = await client.put("/v1/skills/upd-skill", json={"content": "v2 body"})
    assert resp.status_code == 200
    got = await client.get("/v1/skills/upd-skill", params={"with_content": "true"})
    assert "v2 body" in got.json()["skill"]["content"]


async def test_route_update_bundled_forbidden(client):
    resp = await client.put("/v1/skills/summarize", json={"content": "hacked"})
    assert resp.status_code == 403


async def test_route_delete_skill(client):
    await client.post("/v1/skills", json={"name": "del-skill", "content": "body"})
    resp = await client.delete("/v1/skills/del-skill")
    assert resp.status_code == 200
    assert (await client.get("/v1/skills/del-skill")).status_code == 404


async def test_route_delete_bundled_forbidden(client):
    resp = await client.delete("/v1/skills/summarize")
    assert resp.status_code == 403


async def test_route_search(client):
    resp = await client.post("/v1/skills/search", json={"query": "总结", "top_k": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] >= 1
    assert data["results"][0]["skill"]["name"] == "summarize"
    assert 0 < data["results"][0]["score"] <= 1


async def test_route_invoke_real_pipeline(client, mock_pool, storage):
    resp = await client.post(
        "/v1/skills/summarize/invoke", json={"task": "总结一下这段会议纪要"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["provider"] == "mock"
    assert data["content"].strip()
    assert data["endpoint_id"] == "mock-standard"


async def test_route_invoke_unknown(client):
    resp = await client.post("/v1/skills/ghost/invoke", json={"task": "x"})
    assert resp.status_code == 404


async def test_route_stats_endpoint(client, storage, mock_pool):
    await client.post("/v1/skills/summarize/invoke", json={"task": "总结"})
    resp = await client.get("/v1/skills/summarize/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["usage"]["invocations"] == 1


async def test_route_evolution_suggestions_endpoint(client, storage, registry):
    store = SkillEvolutionStore()
    store.add_suggestion("summarize", "reflect", 5, "improve X")
    resp = await client.get("/v1/skills/evolution/suggestions")
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


async def test_route_capability_disabled_503(client, monkeypatch):
    import moa_gateway.capability_toggles as toggles

    state = dict(toggles.DEFAULT_CAPABILITIES)
    state["skillhub"] = False
    monkeypatch.setattr(toggles, "_cache", state)
    resp = await client.get("/v1/skills")
    assert resp.status_code == 503
