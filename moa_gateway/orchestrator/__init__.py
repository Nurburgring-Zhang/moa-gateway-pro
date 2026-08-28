"""moa_gateway.orchestrator — 自主编排引擎 (Autonomous Orchestration Engine).

在多种 API 联合/复合使用时, 全自动智能化地调用并组合:
  skill / harness engineering / loop engineering / graph engineering / MCP / CLI
等各种能力, 完成 MOA 任务; 并提供能力强化(reinforcement)与新能力的开发+自动部署。

组件:
  registry       O1 能力注册表 — 真实枚举全部可用能力 + 元数据
  analyzer       O2 任务分析器 — 任务 -> 结构化任务画像(能力需求)
  planner        O3 规划/匹配器 — 任务画像 -> 能力组合执行计划(DAG)
  executor       O4 编排执行器 — 按计划真实联合执行并聚合
  reinforcer     O5 强化器     — 结果 -> 能力评分反馈(持久化)
  skill_factory  O6 Skill工厂  — 新能力开发 + 校验 + 自动注册(热部署)
  engine         编排引擎      — 串联 O2-O5 的端到端 run()

诚实政策(零虚假): 无真实 LLM key 时, 语义分析/规划中的 LLM 部分使用显式标注的
mock; 执行一律复用真实引擎(agent run-loop / workflow / MCP / channels / dispatch),
绝不假执行。
"""

from .engine import OrchestratorEngine, get_orchestrator  # noqa: F401
from .registry import Capability, CapabilityRegistry, get_registry  # noqa: F401

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "get_registry",
    "OrchestratorEngine",
    "get_orchestrator",
]
