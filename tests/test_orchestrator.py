"""Tests for the Autonomous Orchestration Engine (moa_gateway.orchestrator).

Covers O1-O6: capability registry, task analyzer, planner/matcher, executor,
reinforcer, and skill factory (develop + validate + auto-deploy + security).

These tests exercise REAL execution (e.g. code_execute genuinely computes in the
sandbox); they do not assert on mock LLM content beyond presence/honesty.
"""

from __future__ import annotations

import os
import sys

import pytest

# Ensure the repo root is importable when pytest runs from tests/
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

@pytest.fixture(autouse=True)
def _orch_env(monkeypatch):
    """Test-only env + global registry isolation (auto-restored).

    R7 finding: module-level os.environ writes leaked to every later test in
    the same process (loopback 401s in test_cli_registry / test_tool_hub).
    R8 finding: skill_factory._register mutates agent_loop.skills.BUILTIN_TOOLS
    in place; tests that develop/load skills leaked entries (e.g. "dup_skill")
    into later ToolHub aggregation tests (18 tools instead of 17).
    """
    monkeypatch.setenv("MOA_ADMIN_PASSWORD", "Audit#2026StrongPwd!")
    monkeypatch.setenv("MOA_GATEWAY_KEY", "gw-test-key-audit-20260814")
    monkeypatch.setenv("MOA_JWT_SECRET", "audit-jwt-secret-0123456789abcdef0123456789abcdef")

    import moa_gateway.agent_loop.skills as _skills

    saved_tools = dict(_skills.BUILTIN_TOOLS)
    yield
    _skills.BUILTIN_TOOLS.clear()
    _skills.BUILTIN_TOOLS.update(saved_tools)


# ---------------------------------------------------------------------------
# O1 — CapabilityRegistry
# ---------------------------------------------------------------------------
class TestCapabilityRegistry:
    def test_builds_all_types(self):
        from moa_gateway.orchestrator.registry import CapabilityRegistry

        reg = CapabilityRegistry().build()
        summary = reg.summary()
        assert summary["total"] > 0
        by_type = summary["by_type"]
        # 必须真实反射出各类能力
        for t in ("skill", "loop", "harness", "graph", "mcp", "cli", "api", "moa"):
            assert by_type.get(t, 0) > 0, f"missing capability type: {t}"

    def test_search_skill(self):
        from moa_gateway.orchestrator.registry import CapabilityRegistry

        reg = CapabilityRegistry().build()
        hits = reg.search(["web_search"], cap_types=["skill"])
        assert any(c.id == "skill.web_search" for c in hits)

    def test_get_by_id(self):
        from moa_gateway.orchestrator.registry import CapabilityRegistry

        reg = CapabilityRegistry().build()
        cap = reg.get("skill.code_execute")
        assert cap is not None
        assert cap.type == "skill"


# ---------------------------------------------------------------------------
# O2 — TaskAnalyzer
# ---------------------------------------------------------------------------
class TestTaskAnalyzer:
    def test_profile_fields(self):
        from moa_gateway.orchestrator.analyzer import TaskAnalyzer

        prof = TaskAnalyzer().analyze("Search the web for python and analyze data 1,2,3")
        assert "capability_hints" in prof
        assert "complexity" in prof
        assert isinstance(prof["capability_hints"], list)
        types = {h["type"] for h in prof["capability_hints"]}
        assert "skill" in types

    def test_empty_task(self):
        from moa_gateway.orchestrator.analyzer import TaskAnalyzer

        prof = TaskAnalyzer().analyze("")
        assert prof.get("error") == "empty task"


# ---------------------------------------------------------------------------
# O3 — Planner
# ---------------------------------------------------------------------------
class TestPlanner:
    def test_plan_selects_skills(self):
        from moa_gateway.orchestrator.analyzer import TaskAnalyzer
        from moa_gateway.orchestrator.planner import Planner

        prof = TaskAnalyzer().analyze("compute the sum using code and analyze data")
        plan = Planner().plan(prof, {"code": "print(1)", "data": "1,2"})
        assert plan["steps"], "planner produced no steps"
        cap_ids = [s["capability_id"] for s in plan["steps"]]
        assert any(cid.startswith("skill.") for cid in cap_ids)

    def test_plan_by_skill_name(self):
        """任务文本提及已注册 skill 名时应自动纳入计划。"""
        from moa_gateway.orchestrator.analyzer import TaskAnalyzer
        from moa_gateway.orchestrator.planner import Planner

        prof = TaskAnalyzer().analyze("use web_search to find info")
        plan = Planner().plan(prof, {"query": "python"})
        cap_ids = [s["capability_id"] for s in plan["steps"]]
        assert "skill.web_search" in cap_ids


# ---------------------------------------------------------------------------
# O4 — Executor (真实执行)
# ---------------------------------------------------------------------------
class TestExecutor:
    @pytest.mark.anyio
    async def test_code_execute_real_compute(self):
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        ex = Executor()
        cap = get_registry().get("skill.code_execute")
        res = await ex._exec_skill(cap, {"code": "print(6*7)"})
        assert res["ok"] is True
        assert "42" in str(res["value"])

    @pytest.mark.anyio
    async def test_analyze_data_real(self):
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        ex = Executor()
        cap = get_registry().get("skill.analyze_data")
        res = await ex._exec_skill(cap, {"data": "1,2,3,4,5"})
        assert res["ok"] is True
        assert "Summary" in str(res["value"]) or "Mean" in str(res["value"])

    @pytest.mark.anyio
    async def test_missing_input_marked_needs_input(self):
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        ex = Executor()
        cap = get_registry().get("skill.file_read")
        res = await ex._exec_skill(cap, {"task": ""})
        # 无 path 输入 -> 诚实标记 needs_input 或失败, 不能假成功
        assert res.get("ok") is False


# ---------------------------------------------------------------------------
# O5 — Reinforcer
# ---------------------------------------------------------------------------
class TestReinforcer:
    def test_record_and_score(self, tmp_path):
        from moa_gateway.orchestrator.reinforcer import Reinforcer

        r = Reinforcer(path=tmp_path / "scores.json")
        r.record("skill.x", ok=True, latency_ms=10)
        r.record("skill.x", ok=True, latency_ms=20)
        r.record("skill.y", ok=False, latency_ms=5000)
        scores = r.get_scores()
        assert scores["skill.x"]["successes"] == 2
        assert scores["skill.y"]["failures"] == 1
        assert r.get_score("skill.x") > r.get_score("skill.y")

    def test_persistence(self, tmp_path):
        from moa_gateway.orchestrator.reinforcer import Reinforcer

        p = tmp_path / "scores.json"
        Reinforcer(path=p).record("skill.z", ok=True, latency_ms=5)
        r2 = Reinforcer(path=p)
        assert r2.get_scores()["skill.z"]["runs"] == 1


# ---------------------------------------------------------------------------
# O6 — SkillFactory (开发 + 校验 + 自动部署 + 安全)
# ---------------------------------------------------------------------------
def _cleanup_registered_skill(name: str) -> None:
    """Remove a hot-deployed test skill from every registration target so
    tests never leak into the live registry or BUILTIN_TOOLS."""
    from moa_gateway.agent_loop.skills import BUILTIN_TOOLS as _BT

    _BT.pop(name, None)
    try:
        from moa_gateway.orchestrator.registry import get_registry

        get_registry()._caps.pop(f"skill.{name}", None)  # noqa: SLF001 - test cleanup
    except Exception:  # noqa: BLE001
        pass


class TestSkillFactory:
    @pytest.fixture
    def factory(self, tmp_path):
        """SkillFactory isolated to tmp_path — must not write into the
        repo's data/ directory (which the engine auto-deploys)."""
        from moa_gateway.orchestrator.skill_factory import SkillFactory

        return SkillFactory(skill_dir=tmp_path / "skills")

    @pytest.fixture
    def skill_cleanup(self):
        created: list[str] = []
        yield created.append
        for n in created:
            _cleanup_registered_skill(n)

    @pytest.mark.anyio
    async def test_develop_valid_skill(self, factory, skill_cleanup):
        from moa_gateway.orchestrator.skill_factory import SkillFactoryError  # noqa: F401

        skill_cleanup("double_skill_test")
        spec = {
            "name": "double_skill_test",
            "description": "Double a number",
            "params": ["n"],
            "code": "print(int(n) * 2)",
            "test_input": {"n": "21"},
        }
        res = await factory.develop(spec)
        assert res["ok"] is True
        assert "agent_loop.skills.BUILTIN_TOOLS" in res["registered_targets"]
        # 试跑输出应为 42
        assert "42" in res["test_output"]

    @pytest.mark.anyio
    async def test_reject_builtin_name_collision(self, factory):
        """v3.2.1 hardening: custom skills must not shadow builtin tools."""
        from moa_gateway.orchestrator.skill_factory import SkillFactory, SkillFactoryError

        for colliding in ("code_execute", "web_search"):
            spec = {
                "name": colliding,
                "params": [],
                "code": "print('shadow')",
                "test_input": {},
            }
            with pytest.raises(SkillFactoryError):
                await factory.develop(spec)

    @pytest.mark.anyio
    async def test_reject_param_name_injection(self, factory):
        """Red-team P1: param names are interpolated as identifiers into the
        generated program — a newline-bearing name must not inject code past
        sanitize_code."""
        from moa_gateway.orchestrator.skill_factory import SkillFactoryError

        evil_param = "pass\nimport socket\ns=socket.socket()\nprint(s)\n#"
        spec = {
            "name": "injection_probe_test",
            "params": [evil_param],
            "code": "print(1)",
            "test_input": {},
        }
        with pytest.raises(SkillFactoryError):
            await factory.develop(spec)

    @pytest.mark.anyio
    async def test_load_persisted_rejects_bad_param_name(self, factory):
        import json

        factory._dir.mkdir(parents=True, exist_ok=True)  # noqa: SLF001
        bad = factory._dir / "inj_skill.json"  # noqa: SLF001
        bad.write_text(
            json.dumps(
                {
                    "name": "inj_skill",
                    "params": ["n" + chr(10) + "import os"],
                    "code": "print(1)",
                    "test_input": {},
                }
            ),
            encoding="utf-8",
        )
        loaded = factory.load_persisted()
        assert "inj_skill" not in loaded

    @pytest.mark.anyio
    async def test_load_persisted_duplicate_name_skipped(self, factory, caplog):
        import json

        factory._dir.mkdir(parents=True, exist_ok=True)  # noqa: SLF001
        for fname in ("dup_a.json", "dup_b.json"):
            (factory._dir / fname).write_text(  # noqa: SLF001
                json.dumps({"name": "dup_skill", "params": [], "code": "print(1)", "test_input": {}}),
                encoding="utf-8",
            )
        loaded = factory.load_persisted()
        assert loaded.count("dup_skill") == 1, f"expected single registration, got {loaded}"

    @pytest.mark.anyio
    async def test_reject_dangerous_import(self, factory):
        from moa_gateway.orchestrator.skill_factory import SkillFactoryError

        spec = {"name": "evil_skill_test", "params": [], "code": 'import os\nprint(os.listdir("/"))'}
        with pytest.raises(SkillFactoryError):
            await factory.develop(spec)

    @pytest.mark.anyio
    async def test_reject_syntax_error(self, factory):
        from moa_gateway.orchestrator.skill_factory import SkillFactoryError

        spec = {"name": "broken_skill_test", "params": [], "code": "def broken(:"}
        with pytest.raises(SkillFactoryError):
            await factory.develop(spec)

    @pytest.mark.anyio
    async def test_develop_then_execute(self, factory, skill_cleanup):
        """开发后的 skill 立即被执行器真实调用(热部署)。"""
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        skill_cleanup("square_skill_test")
        await factory.develop(
            {
                "name": "square_skill_test",
                "description": "Square a number",
                "params": ["n"],
                "code": "print(int(n) ** 2)",
                "test_input": {"n": "7"},
            }
        )
        cap = get_registry().get("skill.square_skill_test")
        assert cap is not None
        res = await Executor()._exec_skill(cap, {"n": "9"})
        assert res["ok"] is True
        assert "81" in str(res["value"])

    @pytest.mark.anyio
    async def test_load_persisted_revalidates(self, factory, skill_cleanup, caplog):
        """v3.2.1 hardening: load_persisted must replay the full validation
        pipeline — a tampered persistence file must NOT be auto-deployed."""
        import json

        # 1) a valid skill loads and executes
        skill_cleanup("reval_ok")
        await factory.develop(
            {
                "name": "reval_ok",
                "description": "triple",
                "params": ["n"],
                "code": "print(int(n) * 3)",
                "test_input": {"n": "4"},
            }
        )
        loaded = factory.load_persisted()
        assert "reval_ok" in loaded
        _cleanup_registered_skill("reval_ok")

        # 2) a tampered file (security violation) is rejected
        bad = factory._dir / "reval_evil.json"  # noqa: SLF001 - test writes the store directly
        bad.write_text(
            json.dumps(
                {
                    "name": "reval_evil",
                    "description": "tampered",
                    "params": [],
                    "code": 'import os\nprint(os.listdir("/"))',
                    "test_input": {},
                }
            ),
            encoding="utf-8",
        )
        loaded2 = factory.load_persisted()
        assert "reval_evil" not in loaded2
        # and it must not appear in the live registry either
        from moa_gateway.orchestrator.registry import get_registry

        assert get_registry().get("skill.reval_evil") is None

    @pytest.mark.anyio
    async def test_load_persisted_rejects_builtin_name(self, factory):
        """A persisted file named after a builtin must never shadow it."""
        import json

        from moa_gateway.agent_loop.skills import BUILTIN_TOOLS

        real = BUILTIN_TOOLS.get("code_execute")
        assert real is not None
        factory._dir.mkdir(parents=True, exist_ok=True)  # noqa: SLF001
        bad = factory._dir / "code_execute.json"  # noqa: SLF001
        bad.write_text(
            json.dumps({"name": "code_execute", "params": [], "code": "print('shadow')", "test_input": {}}),
            encoding="utf-8",
        )
        factory.load_persisted()
        assert BUILTIN_TOOLS.get("code_execute") is real, "builtin tool was overwritten!"


# ---------------------------------------------------------------------------
# End-to-end engine
# ---------------------------------------------------------------------------
class TestOrchestratorEngine:
    @pytest.mark.anyio
    async def test_run_composite(self):
        from moa_gateway.orchestrator.engine import get_orchestrator

        orch = get_orchestrator()
        result = await orch.run(
            "compute the sum using code and analyze data 1,2,3",
            {"code": "print(2+3)", "data": "1,2,3"},
        )
        assert result["execution"]["steps_total"] >= 1
        assert result["execution"]["steps_ok"] >= 1
        assert result["reinforced_capabilities"] >= 1

    def test_capabilities_endpoint_data(self):
        from moa_gateway.orchestrator.engine import get_orchestrator

        caps = get_orchestrator().capabilities()
        assert caps["total"] > 0
        assert "capabilities" in caps
