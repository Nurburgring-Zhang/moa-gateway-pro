"""Orchestrator authorization-consistency regression (v3.2.1 S2).

The orchestrator must honor the same trust model as /v1/agent: RCE-capable
skills (code_execute / file_* / api_verify) are admin/operator-only. A
non-privileged caller must not reach them via ANY orchestrator path —
including the planner's name-mention auto-inclusion (which previously
re-exposed them to readonly API keys).

These tests run the real planner / executor / engine; no auth machinery is
mocked (there is none at module level — the route layer passes `privileged`).
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("MOA_ADMIN_PASSWORD", "Audit#2026StrongPwd!")
os.environ.setdefault("MOA_GATEWAY_KEY", "gw-test-key-audit-20260814")
os.environ.setdefault("MOA_JWT_SECRET", "audit-jwt-secret-0123456789abcdef0123456789abcdef")


def _analyzer():
    from moa_gateway.orchestrator.analyzer import TaskAnalyzer

    return TaskAnalyzer()


# ---------------------------------------------------------------------------
# Planner filter
# ---------------------------------------------------------------------------

class TestPlannerFiltersDangerousSkills:
    def test_name_mention_excluded_for_non_privileged(self):
        from moa_gateway.orchestrator.planner import Planner
        from moa_gateway.orchestrator.registry import get_registry

        planner = Planner(get_registry().build())
        profile = _analyzer().analyze("please use code_execute to compute 6*7", {"code": "print(6*7)"})
        plan = planner.plan(profile, {"code": "print(6*7)"}, privileged=False)
        cap_ids = [s["capability_id"] for s in plan["steps"]]
        assert "skill.code_execute" not in cap_ids
        # 诚实披露: 被过滤的技能必须记录在计划中
        assert "code_execute" in plan.get("filtered_privileged_skills", [])

    def test_name_mention_included_for_admin(self):
        from moa_gateway.orchestrator.planner import Planner
        from moa_gateway.orchestrator.registry import get_registry

        planner = Planner(get_registry().build())
        profile = _analyzer().analyze("please use code_execute to compute 6*7", {"code": "print(6*7)"})
        plan = planner.plan(profile, {"code": "print(6*7)"}, privileged=True)
        assert "skill.code_execute" in [s["capability_id"] for s in plan["steps"]]
        assert "filtered_privileged_skills" not in plan

    def test_custom_skills_also_filtered_for_non_privileged(self):
        """Red-team consistency fix: custom skills run the SAME sandbox as
        code_execute, so a readonly caller must not auto-trigger them."""
        from moa_gateway.orchestrator.planner import Planner
        from moa_gateway.orchestrator.registry import get_registry

        planner = Planner(get_registry().build())
        # no custom skill named this exists — use a builtin SAFE skill name
        # (web_search) to prove the filter is type-wide, not name-based:
        # wait — web_search is a builtin safe skill; it must ALSO be filtered
        # for readonly since ALL skill execution is privileged.
        profile = _analyzer().analyze("please use web_search for the latest news", {})
        plan = planner.plan(profile, {}, privileged=False)
        assert "skill.web_search" not in [s["capability_id"] for s in plan["steps"]]
        assert "web_search" in plan.get("filtered_privileged_skills", [])

    def test_safe_builtin_skill_available_for_admin(self):
        from moa_gateway.orchestrator.planner import Planner
        from moa_gateway.orchestrator.registry import get_registry

        planner = Planner(get_registry().build())
        profile = _analyzer().analyze("please use web_search for the latest news", {})
        plan = planner.plan(profile, {}, privileged=True)
        assert "skill.web_search" in [s["capability_id"] for s in plan["steps"]]

    def test_mcp_user_role_not_collapsed(self):
        """Red-team fix: a 'user'-role caller keeps user-allowed MCP tools."""
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        executor = Executor(get_registry().build())
        cap = type("Cap", (), {"invoke": {"tool": "search_web"}, "name": "search_web"})()
        out = asyncio.run(
            executor._exec_mcp(cap, {"arguments": {"query": "hi"}}, privileged=True, role="user")
        )
        assert out["ok"] is True, f"user role should keep search_web: {out}"

    @pytest.mark.parametrize("tool", ["file_read", "file_write", "file_list", "api_verify"])
    def test_all_dangerous_skills_filtered(self, tool):
        from moa_gateway.orchestrator.planner import Planner
        from moa_gateway.orchestrator.registry import get_registry

        planner = Planner(get_registry().build())
        profile = _analyzer().analyze(f"run {tool} on the payload", {})
        plan = planner.plan(profile, {}, privileged=False)
        assert f"skill.{tool}" not in [s["capability_id"] for s in plan["steps"]]


# ---------------------------------------------------------------------------
# Executor defense-in-depth
# ---------------------------------------------------------------------------

class TestExecutorDefenseInDepth:
    def _plan_with_skill(self, skill: str) -> dict:
        return {
            "steps": [
                {
                    "step_id": "s1",
                    "capability_id": f"skill.{skill}",
                    "type": "skill",
                    "title": f"hand-built {skill}",
                    "depends_on": [],
                    "input": {"code": "print(6*7)", "path": "x", "url": "https://example.com", "directory": "."},
                }
            ]
        }

    @pytest.mark.parametrize(
        "skill", ["code_execute", "file_read", "file_write", "file_list", "api_verify"]
    )
    def test_hand_built_dangerous_step_denied(self, skill):
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        executor = Executor(get_registry().build())
        out = asyncio.run(
            executor.execute(self._plan_with_skill(skill), {}, privileged=False)
        )
        res = out["step_results"]["s1"]
        assert res["ok"] is False
        assert "admin/operator" in res["error"]

    def test_privileged_hand_built_step_runs(self):
        """Admin keeps full reach: the sandbox genuinely computes 42."""
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        executor = Executor(get_registry().build())
        out = asyncio.run(
            executor.execute(self._plan_with_skill("code_execute"), {"code": "print(6*7)"}, privileged=True)
        )
        res = out["step_results"]["s1"]
        assert res["ok"] is True
        assert "42" in str(res.get("value", ""))

    def test_non_privileged_loop_has_no_dangerous_tools(self):
        from moa_gateway.agent_loop.harness import AgentHarness
        from moa_gateway.agent_loop.skills import DANGEROUS_TOOLS, BUILTIN_TOOLS
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        captured: dict = {}

        class SpyHarness(AgentHarness):
            def register_tool(self, name, handler, description):
                captured.setdefault("tools", set()).add(name)
                super().register_tool(name, handler, description)

        import moa_gateway.agent_loop.harness as harness_mod
        import moa_gateway.orchestrator.executor as ex

        executor = Executor(get_registry().build())
        cap = type("Cap", (), {"invoke": {"loop_name": "react"}, "name": "loop.react"})()
        # SpyHarness monkeypatch: verify the tool-subset contract directly.
        # Patch the harness module attribute — _exec_loop imports it from
        # there at call time.
        original = harness_mod.AgentHarness
        harness_mod.AgentHarness = SpyHarness
        try:
            asyncio.run(
                executor._exec_loop(cap, {"messages": [{"role": "user", "content": "hi"}]}, privileged=False)
            )
        finally:
            harness_mod.AgentHarness = original
        registered = captured.get("tools", set())
        assert registered
        assert not (registered & DANGEROUS_TOOLS), f"dangerous tools registered: {registered & DANGEROUS_TOOLS}"
        assert "web_search" in registered  # safe tools remain available

    def test_non_privileged_mcp_role_check_enforced(self):
        from moa_gateway.orchestrator.executor import Executor
        from moa_gateway.orchestrator.registry import get_registry

        executor = Executor(get_registry().build())
        cap = type("Cap", (), {"invoke": {"tool": "run_agent_loop"}, "name": "run_agent_loop"})()
        out = asyncio.run(
            executor._exec_mcp(cap, {"arguments": {"query": "hi"}}, privileged=False)
        )
        assert out["ok"] is False
        assert "privileged" in out["error"]


# ---------------------------------------------------------------------------
# Engine end-to-end with unprivileged caller
# ---------------------------------------------------------------------------

class TestEngineUnprivilegedRun:
    def test_run_never_executes_dangerous_skill(self):
        from moa_gateway.orchestrator.engine import get_orchestrator

        orch = get_orchestrator()
        out = asyncio.run(
            orch.run("use code_execute to compute 6*7", {"code": "print(6*7)"}, privileged=False)
        )
        cap_ids = [s["capability_id"] for s in out["plan"]["steps"]]
        assert "skill.code_execute" not in cap_ids
        for step_result in out["execution"]["step_results"].values():
            if isinstance(step_result, dict) and step_result.get("ok"):
                assert step_result.get("skill") not in {
                    "code_execute", "file_read", "file_write", "file_list", "api_verify"
                }
