"""P8 主动任务分析闭环 — TaskAnalyzer + CapabilityRouter + TaskSupervisor.

用户给一句复合任务, 本模块完成:

1. **TaskAnalyzer** — 真 LLM 驱动分解: 调用 model_pool 的真实端点, 要求输出
   结构化 JSON 子任务图 (id/description/capability/depends_on/params)。
   无可用模型端点时显式抛错 (503), **不做关键词启发式兜底**——分解环节
   要么真 LLM, 要么诚实失败。
2. **CapabilityRouter** — 按子任务 capability 分发到真实执行器:
   moa(MOA 委员会) / chat(单模型) / multimodal(多路扇出) / workflow(YAML DAG) /
   cli(注册表外部 CLI) / agent_loop(工具循环)。每次分发写决策日志证据。
3. **TaskSupervisor** — 按依赖拓扑分波并发执行; 失败子任务自动重试一次,
   再失败走 self-heal 换路 (multimodal 剔除永久性不可用平台后对瞬时失败平台
   重扇出 / moa 降级 chat), 仍失败则带证据标记 failed; 全程 trace 可审计。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

#: 合法能力域 (路由目标)
CAPABILITIES = ("moa", "chat", "multimodal", "workflow", "cli", "agent_loop")

#: self-heal 换路映射: capability -> 降级/替换 capability
SELF_HEAL_ROUTE: dict[str, str] = {
    "moa": "chat",  # 委员会失败 → 单模型兜底
}

ANALYZE_SYSTEM_PROMPT = """你是任务分解引擎。把用户的复合任务分解为子任务图。
只输出 JSON 数组, 不要任何其他文字。每个元素:
{
  "id": "t1",                       // 子任务 ID (t1..tN)
  "description": "...",             // 子任务描述
  "capability": "moa|chat|multimodal|workflow|cli|agent_loop",
  "depends_on": ["t0"],             // 依赖的子任务 ID (可空数组)
  "params": {}                      // 执行参数 (multimodal 需 modality/platforms/prompt 等)
}
规则:
- capability 选择: 文本生成/复杂推理→moa; 简单问答→chat; 图像/语音等多媒体→multimodal;
  预定义流程→workflow; 需要外部命令行工具→cli; 需要多步工具调用→agent_loop
- 依赖必须构成 DAG (无环)
- 子任务数量控制在 2-6 个"""


class TaskAnalysisError(RuntimeError):
    """分解失败 (无模型端点 / LLM 输出不可解析 / 依赖成环等)。"""


class MultimodalAllFailedError(RuntimeError):
    """multimodal 扇出全部路由失败。携带 result dict 供 supervisor self-heal
    剔除坏平台后重扇出 (不丢证据)。"""

    def __init__(self, message: str, result: dict[str, Any]) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class SubTask:
    id: str
    description: str
    capability: str
    depends_on: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    # 执行结果
    status: str = "pending"  # pending|running|success|failed
    attempts: int = 0
    executors: list[str] = field(default_factory=list)
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "capability": self.capability,
            "depends_on": list(self.depends_on),
            "params": self.params,
            "status": self.status,
            "attempts": self.attempts,
            "executors": list(self.executors),
            "output": self.output,
            "error": self.error,
            "latency_ms": round(self.latency_ms, 2),
        }


# ---------------------------------------------------------------------------
# 1. TaskAnalyzer — LLM 驱动分解
# ---------------------------------------------------------------------------

def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """从 LLM 回复中提取 JSON 数组 (容忍 ```json 代码围栏与前后杂文)。"""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise TaskAnalysisError(f"LLM output contains no JSON array: {text[:200]!r}")
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise TaskAnalysisError(f"LLM output is not valid JSON: {e}") from e
    if not isinstance(data, list) or not data:
        raise TaskAnalysisError("LLM output JSON array is empty")
    return data


def _validate_plan(raw: list[dict[str, Any]], max_subtasks: int) -> list[SubTask]:
    """结构校验: 字段齐全、capability 合法、依赖存在且无环。"""
    if len(raw) > max_subtasks:
        raise TaskAnalysisError(f"too many subtasks: {len(raw)} > {max_subtasks}")
    tasks: list[SubTask] = []
    ids: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise TaskAnalysisError(f"subtask #{i} is not an object")
        tid = str(item.get("id") or f"t{i + 1}")
        cap = str(item.get("capability") or "")
        if cap not in CAPABILITIES:
            raise TaskAnalysisError(f"subtask '{tid}': invalid capability '{cap}'")
        deps = item.get("depends_on") or []
        if not isinstance(deps, list):
            raise TaskAnalysisError(f"subtask '{tid}': depends_on must be a list")
        tasks.append(
            SubTask(
                id=tid,
                description=str(item.get("description") or ""),
                capability=cap,
                depends_on=[str(d) for d in deps],
                params=item.get("params") if isinstance(item.get("params"), dict) else {},
            )
        )
        if tid in ids:
            raise TaskAnalysisError(f"duplicate subtask id '{tid}'")
        ids.add(tid)
    for t in tasks:
        for d in t.depends_on:
            if d not in ids:
                raise TaskAnalysisError(f"subtask '{t.id}' depends on unknown '{d}'")
            if d == t.id:
                raise TaskAnalysisError(f"subtask '{t.id}' depends on itself")
    # 环检测 (Kahn)
    indeg = {t.id: len(t.depends_on) for t in tasks}
    queue = [tid for tid, n in indeg.items() if n == 0]
    seen = 0
    while queue:
        cur = queue.pop()
        seen += 1
        for t in tasks:
            if cur in t.depends_on:
                indeg[t.id] -= 1
                if indeg[t.id] == 0:
                    queue.append(t.id)
    if seen != len(tasks):
        raise TaskAnalysisError("dependency graph has a cycle")
    return tasks


class TaskAnalyzer:
    """LLM 驱动的任务分解器 (无启发式兜底)。"""

    def __init__(self, llm_call: Callable[..., Awaitable[Any]] | None = None) -> None:
        self._llm_call = llm_call

    def _resolve_llm_call(self) -> Callable[..., Awaitable[Any]]:
        if self._llm_call is not None:
            return self._llm_call
        from .model_pool import get_model_pool

        pool = get_model_pool()

        async def llm_call(messages: list[dict], **params: Any) -> Any:
            endpoints = list(pool.endpoints.keys()) if pool.endpoints else []
            if not endpoints:
                raise TaskAnalysisError("no model endpoints configured — cannot analyze task")
            return await pool.call(
                endpoint_id=params.get("endpoint_id", endpoints[0]),
                messages=messages,
                temperature=params.get("temperature", 0.2),
                max_tokens=params.get("max_tokens", 4096),
            )

        return llm_call

    async def analyze(self, task: str, max_subtasks: int = 6) -> list[SubTask]:
        if not task or not task.strip():
            raise TaskAnalysisError("task is empty")
        llm_call = self._resolve_llm_call()
        messages = [
            {"role": "system", "content": ANALYZE_SYSTEM_PROMPT},
            {"role": "user", "content": task},
        ]
        outcome = await llm_call(messages, temperature=0.2)
        content = getattr(outcome, "content", None) or str(outcome)
        raw = _extract_json_array(content)
        plan = _validate_plan(raw, max_subtasks)
        logger.info(
            "[task-analyzer] decomposed task into %d subtasks: %s",
            len(plan),
            [f"{t.id}:{t.capability}" for t in plan],
        )
        return plan


# ---------------------------------------------------------------------------
# 2. CapabilityRouter — 分发到真实执行器
# ---------------------------------------------------------------------------

class CapabilityRouter:
    """把子任务路由到真实子系统执行器。每次分发记录决策日志。"""

    async def execute(self, task: SubTask, upstream: dict[str, Any]) -> Any:
        cap = task.capability
        logger.info(
            "[capability-router] dispatch %s -> %s (params=%s, upstream=%s)",
            task.id, cap, sorted(task.params.keys()), sorted(upstream.keys()),
        )
        if cap == "moa":
            return await self._run_moa(task, upstream)
        if cap == "chat":
            return await self._run_chat(task, upstream)
        if cap == "multimodal":
            return await self._run_multimodal(task)
        if cap == "workflow":
            return await self._run_workflow(task, upstream)
        if cap == "cli":
            return await self._run_cli(task)
        if cap == "agent_loop":
            return await self._run_agent_loop(task, upstream)
        raise TaskAnalysisError(f"no executor for capability '{cap}'")

    # -- 各执行器 (全部真实组件) -------------------------------------------

    def _render_prompt(self, task: SubTask, upstream: dict[str, Any]) -> str:
        prompt = str(task.params.get("prompt") or task.description)
        for tid, out in upstream.items():
            prompt = prompt.replace("{{" + tid + "}}", str(out))
        return prompt

    async def _run_moa(self, task: SubTask, upstream: dict[str, Any]) -> Any:
        from .moa import get_moa

        result = await get_moa().execute(
            query=self._render_prompt(task, upstream),
            preset=str(task.params.get("preset", "balanced")),
            strategy=str(task.params.get("strategy", "layered")),
        )
        return result.get("final_content") or result.get("aggregated_content", "")

    async def _run_chat(self, task: SubTask, upstream: dict[str, Any]) -> Any:
        from .model_pool import get_model_pool

        pool = get_model_pool()
        endpoints = list(pool.endpoints.keys()) if pool.endpoints else []
        if not endpoints:
            raise RuntimeError("no model endpoints configured for chat subtask")
        resp = await pool.call(
            endpoint_id=endpoints[0],
            messages=[{"role": "user", "content": self._render_prompt(task, upstream)}],
            temperature=0.7,
            max_tokens=4096,
        )
        return resp.content

    async def _run_multimodal(self, task: SubTask) -> Any:
        from .multimodal_fanout import get_fanout

        p = task.params
        modality = str(p.get("modality") or "")
        platforms = p.get("platforms") or []
        if not modality or not platforms:
            raise RuntimeError("multimodal subtask requires params.modality and params.platforms")
        result = await get_fanout().execute(
            modality=modality,
            platforms=[str(x) for x in platforms],
            payload={k: v for k, v in p.items() if k not in ("modality", "platforms", "mode")},
            mode=str(p.get("mode", "all")),
            per_route_timeout_s=float(p.get("per_route_timeout_s", 60.0)),
        )
        if not result.successes:
            raise MultimodalAllFailedError(
                "all multimodal routes failed: "
                + "; ".join(f"{r.platform}:{r.status}:{r.error}" for r in result.routes),
                result=result.to_dict(),
            )
        return result.to_dict()

    async def _run_workflow(self, task: SubTask, upstream: dict[str, Any]) -> Any:
        from .workflows.workflow_loader import WorkflowLoader
        from .workflows.yaml_workflow import WorkflowYAML

        name = str(task.params.get("workflow") or "")
        if not name:
            raise RuntimeError("workflow subtask requires params.workflow")
        loader = WorkflowLoader()
        wf_path = loader.workflow_dir / f"{name}.yaml"
        if not wf_path.exists():
            wf_path = loader.workflow_dir / f"{name}.yml"
        if not wf_path.exists():
            raise RuntimeError(f"builtin workflow '{name}' not found")
        wf: WorkflowYAML = loader.load_from_file(wf_path)
        context = dict(task.params.get("context") or {})
        context.setdefault("task", self._render_prompt(task, upstream))
        context.update({f"upstream.{k}": v for k, v in upstream.items()})
        result = await wf.execute(context=context)
        if not result.get("success"):
            raise RuntimeError(f"workflow '{name}' failed: {result.get('error')}")
        return result.get("outputs", {})

    async def _run_cli(self, task: SubTask) -> Any:
        from .capability.cli_registry import get_cli_registry

        name = str(task.params.get("tool") or "")
        if not name:
            raise RuntimeError("cli subtask requires params.tool")
        params = task.params.get("params") or {}
        registry = get_cli_registry()
        result = await asyncio.to_thread(registry.execute, name, params)
        if not getattr(result, "success", False):
            raise RuntimeError(f"cli tool '{name}' failed: {getattr(result, 'error', '')}")
        return getattr(result, "output", "")

    async def _run_agent_loop(self, task: SubTask, upstream: dict[str, Any]) -> Any:
        from .agent_loop.harness import AgentHarness

        from .routes.agent import _make_llm_call

        harness = AgentHarness(llm_call=_make_llm_call())
        messages = [{"role": "user", "content": self._render_prompt(task, upstream)}]
        result = await harness.run(messages, loop_name="react", max_iterations=8)
        if not getattr(result, "success", False):
            raise RuntimeError(f"agent_loop failed: {getattr(result, 'error', '')}")
        return getattr(result, "final_response", "")


# ---------------------------------------------------------------------------
# 3. TaskSupervisor — 分波执行 + 重试 + self-heal 换路
# ---------------------------------------------------------------------------

class TaskSupervisor:
    """拓扑分波并发执行, 失败自动重试一次, 再失败 self-heal 换路。"""

    def __init__(
        self,
        router: CapabilityRouter | None = None,
        per_subtask_timeout_s: float = 300.0,
    ) -> None:
        self.router = router or CapabilityRouter()
        self.per_subtask_timeout_s = per_subtask_timeout_s

    def _waves(self, tasks: list[SubTask]) -> list[list[SubTask]]:
        done: set[str] = set()
        remaining = list(tasks)
        waves: list[list[SubTask]] = []
        while remaining:
            wave = [t for t in remaining if all(d in done for d in t.depends_on)]
            if not wave:
                raise TaskAnalysisError("dependency deadlock (cycle?) in plan")
            waves.append(wave)
            done.update(t.id for t in wave)
            remaining = [t for t in remaining if t.id not in done]
        return waves

    async def _heal_reroute(self, task: SubTask, upstream: dict[str, Any]) -> Any:
        """self-heal: 按 SELF_HEAL_ROUTE 换路重试; multimodal 剔除坏平台重扇出。

        multimodal 语义 (如实说明): 全路由失败时不存在"上次成功的幸存平台",
        因此自愈策略是——把**永久性不可用**平台 (no_key / skipped_mock_unavailable,
        重试也不可能成功) 剔除, 对**瞬时失败**平台 (timeout / failed / cancelled)
        再给一次重扇出机会。若剩下的重试集为空则如实失败。
        """
        if task.capability == "multimodal" and isinstance(task.output, dict):
            permanent_statuses = {"no_key", "skipped_mock_unavailable"}
            routes = task.output.get("routes") or []
            permanent_platforms = {
                r.get("platform") for r in routes if r.get("status") in permanent_statuses
            }
            retry_platforms = [
                p for p in (task.params.get("platforms") or []) if p not in permanent_platforms
            ]
            if retry_platforms:
                logger.warning(
                    "[supervisor] self-heal %s: multimodal re-fanout without permanently-bad %s",
                    task.id, sorted(permanent_platforms),
                )
                healed = SubTask(
                    id=task.id, description=task.description, capability="multimodal",
                    params={**task.params, "platforms": retry_platforms},
                )
                return await self.router.execute(healed, upstream)
        alt = SELF_HEAL_ROUTE.get(task.capability)
        if alt:
            logger.warning("[supervisor] self-heal %s: reroute %s -> %s", task.id, task.capability, alt)
            healed = SubTask(
                id=task.id, description=task.description, capability=alt,
                params=dict(task.params),
            )
            return await self.router.execute(healed, upstream)
        raise RuntimeError(f"no self-heal route for capability '{task.capability}'")

    async def _run_one(self, task: SubTask, upstream: dict[str, Any]) -> None:
        task.status = "running"
        start = time.perf_counter()
        last_error: str | None = None
        # 尝试序列: 原路 → 原路重试 → self-heal 换路
        for attempt in range(3):
            task.attempts = attempt + 1
            try:
                if attempt < 2:
                    executor = f"{task.capability}#{attempt + 1}"
                    task.executors.append(executor)
                    task.output = await asyncio.wait_for(
                        self.router.execute(task, upstream), timeout=self.per_subtask_timeout_s
                    )
                else:
                    task.executors.append("self-heal")
                    task.output = await asyncio.wait_for(
                        self._heal_reroute(task, upstream), timeout=self.per_subtask_timeout_s
                    )
                task.status = "success"
                task.latency_ms = (time.perf_counter() - start) * 1000
                logger.info("[supervisor] subtask %s success (attempt %d)", task.id, task.attempts)
                return
            except asyncio.TimeoutError:
                last_error = f"timeout after {self.per_subtask_timeout_s}s"
                logger.warning("[supervisor] subtask %s attempt %d timed out", task.id, task.attempts)
            except MultimodalAllFailedError as e:
                last_error = f"{type(e).__name__}: {e}"
                # 保留路由证据, 供 self-heal 剔除坏平台后重扇出
                task.output = e.result
                logger.warning(
                    "[supervisor] subtask %s attempt %d failed: %s", task.id, task.attempts, last_error
                )
            except Exception as e:  # noqa: BLE001 - 记录真实错误继续自愈
                last_error = f"{type(e).__name__}: {e}"
                logger.warning(
                    "[supervisor] subtask %s attempt %d failed: %s", task.id, task.attempts, last_error
                )
        task.status = "failed"
        task.error = last_error
        task.latency_ms = (time.perf_counter() - start) * 1000

    async def run(self, tasks: list[SubTask]) -> dict[str, Any]:
        started = time.perf_counter()
        outputs: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        for wave_idx, wave in enumerate(self._waves(tasks)):
            logger.info(
                "[supervisor] wave %d: %s", wave_idx, [t.id for t in wave],
            )
            await asyncio.gather(*(self._run_one(t, outputs) for t in wave))
            for t in wave:
                trace.append(t.to_dict())
                if t.status == "success":
                    outputs[t.id] = t.output
        succeeded = [t for t in tasks if t.status == "success"]
        return {
            "success": len(succeeded) == len(tasks),
            "subtasks": [t.to_dict() for t in tasks],
            "outputs": outputs,
            "trace": trace,
            "total_latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
