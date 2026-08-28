"""OrchestratorEngine — 串联 O2-O5 的端到端自主编排。

run(task, task_input):
  分析(analyzer) -> 匹配规划(planner) -> 联合执行(executor) -> 强化(reinforcer)
全程真实; 无真实 LLM key 时语义部分为显式标注 mock, 执行一律真实引擎。

对外方法:
  run(task, task_input)          全自动编排
  analyze(task)                  仅任务分析
  plan(task, task_input)         仅出计划
  capabilities()                 能力目录
  develop_skill(spec)            开发+校验+自动部署新 skill
  skills()                       已部署 skill 列表
  scores()                       能力评分
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .analyzer import TaskAnalyzer
from .executor import Executor
from .planner import Planner
from .registry import get_registry
from .reinforcer import get_reinforcer
from .skill_factory import get_skill_factory

logger = logging.getLogger(__name__)


class OrchestratorEngine:
    def __init__(self) -> None:
        self._registry = get_registry()
        self._analyzer = TaskAnalyzer()
        self._reinforcer = get_reinforcer()
        # 对抗复审 Fix: 把 reinforcer 传给 planner, 闭合"强化反馈->更优匹配"回路
        self._planner = Planner(self._registry, reinforcer=self._reinforcer)
        self._executor = Executor(self._registry)
        self._factory = get_skill_factory()
        # 启动时自动部署已持久化的自定义 skill
        try:
            self._factory.load_persisted()
        except Exception as e:  # noqa: BLE001
            logger.warning("orchestrator load_persisted failed: %s", e)

    # ---- 端到端 ----
    async def run(
        self,
        task: str,
        task_input: dict[str, Any] | None = None,
        privileged: bool = True,
        role: str = "admin",
    ) -> dict[str, Any]:
        t0 = time.time()
        profile = self._analyzer.analyze(task, task_input)
        plan = self._planner.plan(profile, task_input, privileged=privileged)
        if not plan.get("steps"):
            return {
                "ok": False,
                "error": "planner produced no executable steps",
                "profile": profile,
                "plan": plan,
            }
        execution = await self._executor.execute(plan, task_input, privileged=privileged, role=role)
        reinforced = self._reinforcer.record_run(execution.get("trace", []), execution.get("step_results", {}))
        return {
            "ok": bool(execution.get("ok")),
            "profile": profile,
            "plan": plan,
            "execution": execution,
            "reinforced_capabilities": reinforced,
            "latency_ms": round((time.time() - t0) * 1000, 1),
        }

    # ---- 分步 ----
    def analyze(self, task: str, task_input: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._analyzer.analyze(task, task_input)

    def plan(
        self,
        task: str,
        task_input: dict[str, Any] | None = None,
        privileged: bool = True,
    ) -> dict[str, Any]:
        profile = self._analyzer.analyze(task, task_input)
        return {"profile": profile, "plan": self._planner.plan(profile, task_input, privileged=privileged)}

    def capabilities(self) -> dict[str, Any]:
        return self._registry.summary()

    async def develop_skill(self, spec: dict[str, Any]) -> dict[str, Any]:
        return await self._factory.develop(spec)

    def skills(self) -> dict[str, Any]:
        from ..agent_loop.skills import BUILTIN_TOOLS

        return {
            "builtin": sorted(BUILTIN_TOOLS.keys()),
            "custom_persisted": self._factory.list_persisted(),
        }

    def scores(self) -> dict[str, Any]:
        return self._reinforcer.get_scores()


_engine: OrchestratorEngine | None = None


def get_orchestrator() -> OrchestratorEngine:
    global _engine
    if _engine is None:
        _engine = OrchestratorEngine()
    return _engine
