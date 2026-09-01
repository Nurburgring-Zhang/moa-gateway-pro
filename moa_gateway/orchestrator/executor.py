"""O4 — OrchestrationExecutor: 按计划真实联合执行并聚合。

对计划中每个步骤, 依据其能力类型*真实调用*既有引擎:
  skill -> agent_loop.skills 的真实 handler
  loop  -> AgentHarness + 真实 llm_call(model pool) 的 react/plan_execute
  graph -> WorkflowLoader 的 YAML DAG execute
  mcp   -> MCP builtin tool 的真实 handler
  cli   -> capability.channels 的 ChannelChain
  moa   -> moa.MoAOrchestrator.execute
  api   -> /v1/capability/* 对应 capability 函数(内部 loopback 或直调)

步骤按 depends_on 拓扑分波, 同波并行(asyncio.gather)。绝不假执行; 单步失败如实
记录 error 并继续(除非是关键聚合步)。产出执行 trace + 聚合结果。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .registry import (
    CAP_API,
    CAP_CLI,
    CAP_GRAPH,
    CAP_HARNESS,
    CAP_LOOP,
    CAP_MCP,
    CAP_MOA,
    CAP_SKILL,
    CapabilityRegistry,
    get_registry,
)

try:
    from ..agent_loop.skills import BUILTIN_TOOLS, DANGEROUS_TOOLS
except ImportError:  # v4.1: DANGEROUS_TOOLS lives in skill_factory instead
    from ..agent_loop.skills import BUILTIN_TOOLS
    from .skill_factory import DANGEROUS_TOOLS

logger = logging.getLogger(__name__)

# skill 的主参数名(用于把通用输入解析成 handler 需要的 kwargs)
_SKILL_PRIMARY_ARG = {
    "web_search": "query",
    "code_execute": "code",
    "file_read": "path",
    "file_write": "path",
    "file_list": "directory",
    "analyze_data": "data",
    "api_verify": "url",
}


class Executor:
    def __init__(self, registry: CapabilityRegistry | None = None) -> None:
        self._registry = registry or get_registry()

    async def execute(
        self,
        plan: dict[str, Any],
        task_input: dict[str, Any] | None = None,
        privileged: bool = True,
        role: str = "admin",
    ) -> dict[str, Any]:
        steps = plan.get("steps", [])
        tin = task_input or {}
        if not steps:
            return {"ok": False, "error": "empty plan", "trace": [], "result": None}

        results: dict[str, Any] = {}
        trace: list[dict[str, Any]] = []
        order, dangling = self._topo_waves(steps)

        # 悬空依赖步骤: 如实标记失败, 不执行(避免读到 None 的静默损坏)
        for s in dangling:
            results[s["step_id"]] = {"ok": False, "error": f"dangling depends_on in step {s['step_id']}"}
            trace.append({"step_id": s["step_id"], "capability_id": s.get("capability_id"), "ok": False, "error": "dangling dependency"})

        for wave in order:
            coros = [self._run_step(s, results, tin, privileged=privileged, role=role) for s in wave]
            wave_results = await asyncio.gather(*coros, return_exceptions=True)
            for s, wr in zip(wave, wave_results):
                if isinstance(wr, Exception):
                    results[s["step_id"]] = {"ok": False, "error": f"{type(wr).__name__}: {wr}"}
                    trace.append({"step_id": s["step_id"], "capability_id": s.get("capability_id"), "ok": False, "error": str(wr)})
                else:
                    results[s["step_id"]] = wr
                    trace.append({"step_id": s["step_id"], "capability_id": s.get("capability_id"), "ok": bool(wr.get("ok")), "summary": wr.get("summary")})

        # 对抗复审 Fix: 最终结果优先取聚合步(带 aggregate_of)的结果; 无聚合步时取最后一个
        # 成功步的结果, 而非盲目假设 steps[-1] 是聚合步。
        agg_step = next((s for s in reversed(steps) if (s.get("input") or {}).get("aggregate_of")), None)
        if agg_step is not None:
            final = results.get(agg_step["step_id"])
        else:
            ok_steps = [s for s in steps if isinstance(results.get(s["step_id"]), dict) and results.get(s["step_id"], {}).get("ok")]
            final = results.get((ok_steps[-1]["step_id"]) if ok_steps else steps[-1]["step_id"])
        ok_count = sum(1 for r in results.values() if isinstance(r, dict) and r.get("ok"))
        return {
            "ok": ok_count > 0,
            "steps_total": len(steps),
            "steps_ok": ok_count,
            "result": final,
            "trace": trace,
            "step_results": results,
        }

    # ---- 拓扑分波 ----
    def _topo_waves(self, steps: list[dict[str, Any]]) -> tuple[list[list[dict[str, Any]]], list[dict[str, Any]]]:
        """返回 (waves, dangling)。悬空依赖(引用不存在的 step)的步骤被剔除并标记,
        绝不与其未完成的依赖并行执行(防止聚合步读到 None 造成静默数据损坏)。"""
        by_id = {s["step_id"]: s for s in steps}
        dangling: list[dict[str, Any]] = []
        valid: dict[str, dict[str, Any]] = {}
        for s in steps:
            if any(d not in by_id for d in s.get("depends_on", [])):
                dangling.append(s)
            else:
                valid[s["step_id"]] = s
        remaining = dict(valid)
        waves: list[list[dict[str, Any]]] = []
        done: set[str] = set()
        guard = 0
        while remaining and guard < 100:
            guard += 1
            wave = [s for s in remaining.values() if all(d in done for d in s.get("depends_on", []))]
            if not wave:  # 依赖环 -> 余下全部一波执行(有向环无法拓扑排序)
                wave = list(remaining.values())
            waves.append(wave)
            for s in wave:
                done.add(s["step_id"])
                remaining.pop(s["step_id"], None)
        return waves, dangling

    # ---- 单步执行 ----
    async def _run_step(
        self,
        step: dict[str, Any],
        results: dict[str, Any],
        tin: dict[str, Any],
        privileged: bool = True,
        role: str = "admin",
    ) -> dict[str, Any]:
        cap_id = step.get("capability_id", "")
        cap = self._registry.get(cap_id)
        stype = step.get("type") or (cap.type if cap else "")
        merged_input = self._merge_input(step.get("input", {}), results, tin)
        t0 = time.time()
        try:
            if stype == CAP_SKILL:
                out = await self._exec_skill(cap, merged_input, privileged=privileged)
            elif stype == CAP_LOOP:
                out = await self._exec_loop(cap, merged_input, privileged=privileged)
            elif stype == CAP_HARNESS:
                # 对抗复审 Fix: harness 注册了就必须可执行 — harness 驱动默认 react loop
                out = await self._exec_loop(cap, {**merged_input, "loop_name": "react"}, privileged=privileged)
            elif stype == CAP_GRAPH:
                out = await self._exec_graph(cap, merged_input)
            elif stype == CAP_MCP:
                out = await self._exec_mcp(cap, merged_input, privileged=privileged, role=role)
            elif stype == CAP_CLI:
                out = await self._exec_cli(cap, merged_input)
            elif stype == CAP_MOA:
                out = await self._exec_moa(cap, merged_input)
            elif stype == CAP_API:
                out = await self._exec_api(cap, merged_input)
            else:
                out = {"ok": False, "error": f"unknown capability type: {stype}"}
            out = out if isinstance(out, dict) else {"ok": True, "value": out}
            out.setdefault("ok", True)
            out["latency_ms"] = round((time.time() - t0) * 1000, 1)
            out["summary"] = self._summarize(out)
            return out
        except Exception as e:  # noqa: BLE001
            logger.exception("step %s failed", step.get("step_id"))
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "latency_ms": round((time.time() - t0) * 1000, 1)}

    def _merge_input(self, step_input: dict[str, Any], results: dict[str, Any], tin: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        merged.update(tin or {})
        merged.update(step_input or {})
        # 聚合步: 把依赖步的结果注入 aggregate_results
        agg_of = step_input.get("aggregate_of")
        if agg_of:
            merged["aggregate_results"] = {sid: results.get(sid) for sid in agg_of}
        return merged

    def _summarize(self, out: dict[str, Any]) -> str:
        if not out.get("ok"):
            return f"error: {out.get('error', '')[:80]}"
        for key in ("value", "content", "result", "output", "final_content"):
            if key in out and out[key] is not None:
                return str(out[key])[:120]
        return "ok"

    # ---- 各类型真实执行 ----
    async def _exec_skill(self, cap, inp: dict[str, Any], privileged: bool = True) -> dict[str, Any]:
        from ..agent_loop.skills import BUILTIN_TOOLS

        name = (cap.invoke or {}).get("name") or cap.name
        # v3.2.1 hardening (defense-in-depth): even a hand-built plan cannot
        # make a non-privileged caller reach ANY skill execution. Custom
        # skills run the same sandbox as code_execute, so the sandbox must
        # not be the only boundary (red-team finding). Planner already
        # filters; this re-checks at the execution boundary.
        if not privileged:
            return {"ok": False, "error": f"skill '{name}' requires admin/operator role", "denied": True}
        entry = BUILTIN_TOOLS.get(name)
        if not entry:
            return {"ok": False, "error": f"skill not found: {name}"}
        handler = entry[0]
        kwargs = self._resolve_skill_kwargs(name, inp)
        if kwargs is None:
            return {"ok": False, "error": f"skill '{name}' 缺少必需输入(需提供 { _SKILL_PRIMARY_ARG.get(name) })", "needs_input": True}
        result = await handler(**kwargs)
        # 诚实性: skill 返回的错误/违规字符串应标记为失败步, 不冒充成功
        if isinstance(result, str) and self._looks_like_skill_error(result):
            return {"ok": False, "error": result[:200], "skill": name}
        return {"ok": True, "value": result, "skill": name}

    @staticmethod
    def _looks_like_skill_error(result: str) -> bool:
        lowered = result.lower()
        # 覆盖 run_isolated/沙箱的全部失败字符串, 防止把超时/过大/崩溃冒充为成功(对抗复审 Fix)
        markers = (
            "security violation",
            "sandbox",
            "traceback",
            "error:",
            "skill error",
            "is not supported",
            "unavailable",
            "timed out",
            "timeout",
            "too large",
            "child exited",
            "child killed",
            "unparseable",
            "execution error",
        )
        return any(m in lowered for m in markers)

    def _resolve_skill_kwargs(self, name: str, inp: dict[str, Any]):
        primary = _SKILL_PRIMARY_ARG.get(name)
        if not primary:
            return inp or None
        # 直接提供了主参数
        if primary in inp and inp[primary]:
            return {primary: inp[primary], **{k: v for k, v in inp.items() if k != primary and k in self._extra_kwargs(name)}}
        # 用通用 task/query/input/prompt 兜底填充主参数
        for alias in ("task", "query", "input", "prompt", "text", "content"):
            if inp.get(alias):
                return {primary: inp[alias]}
        return None

    def _extra_kwargs(self, name: str) -> set[str]:
        return {
            "web_search": {"max_results"},
            "code_execute": {"language", "timeout"},
            "analyze_data": {"analysis_type"},
            "api_verify": {"method", "expected_status"},
        }.get(name, set())

    async def _exec_loop(self, cap, inp: dict[str, Any], privileged: bool = True) -> dict[str, Any]:
        from ..agent_loop.harness import AgentHarness

        loop_name = (cap.invoke or {}).get("loop_name") or cap.name
        llm_call = self._make_llm_call()
        harness = AgentHarness(llm_call=llm_call)
        # v3.2.1 hardening: non-privileged loops get the same tool subset as
        # routes/agent.py defaults — RCE-capable tools never registered.
        for tname, (handler, desc) in BUILTIN_TOOLS.items():
            if not privileged and tname in DANGEROUS_TOOLS:
                continue
            harness.register_tool(tname, handler, desc)
        messages = inp.get("messages") or [{"role": "user", "content": inp.get("query") or inp.get("task") or ""}]
        result = await harness.run(messages=messages, loop_name=loop_name, max_iterations=int(inp.get("max_iterations", 2)))
        return {
            "ok": bool(result.success),
            "content": result.final_response,
            "iterations": result.iterations,
            "loop": loop_name,
        }

    async def _exec_graph(self, cap, inp: dict[str, Any]) -> dict[str, Any]:
        from ..workflows.workflow_loader import WorkflowLoader

        wf_name = (cap.invoke or {}).get("workflow") or cap.name
        loader = WorkflowLoader()
        wf = loader.get_workflow(wf_name)
        if wf is None:
            return {"ok": False, "error": f"workflow not found: {wf_name}"}
        context = inp.get("context") or {"user_input": inp.get("query") or inp.get("task") or ""}
        result = await wf.execute(context)
        # 对抗复审 Fix: 不能把缺失 success 键(未知步骤类型等)默认为成功
        if result.get("error") or result.get("success") is False:
            return {"ok": False, "error": str(result.get("error") or "workflow failed"), "value": result, "workflow": wf_name}
        return {"ok": bool(result.get("success", True)), "value": result, "workflow": wf_name}

    async def _exec_mcp(self, cap, inp: dict[str, Any], privileged: bool = True, role: str = "admin") -> dict[str, Any]:
        from ..mcp.builtin_tools import register_builtin_tools
        from ..mcp.registry import ToolRegistry

        tool_name = (cap.invoke or {}).get("tool") or cap.name
        reg = ToolRegistry()
        register_builtin_tools(reg)
        # v3.2.1 hardening: honor the registry's per-tool role restrictions
        # (e.g. run_agent_loop is admin/operator-only) instead of reaching
        # into _handlers directly, which bypassed them.
        # v3.2.1 (red-team): use the caller's real role so e.g. "user" role
        # callers keep access to user-allowed MCP tools; readonly stays denied.
        effective_role = role if privileged else "readonly"
        if not reg.check_access(tool_name, effective_role):
            return {"ok": False, "error": f"mcp tool '{tool_name}' requires a privileged role", "denied": True}
        handler = reg._handlers.get(tool_name)  # noqa: SLF001 - internal orchestration
        if handler is None:
            return {"ok": False, "error": f"mcp tool not found: {tool_name}"}
        args = inp.get("arguments") or {"query": inp.get("query") or inp.get("task") or ""}
        result = await handler(args)
        return {"ok": True, "value": result, "tool": tool_name}

    async def _exec_cli(self, cap, inp: dict[str, Any]) -> dict[str, Any]:
        from ..capability.channels import ChannelChain

        chain = ChannelChain()
        query = inp.get("query") or inp.get("task") or ""
        result = await chain.execute(query)
        return {
            "ok": bool(result.get("result") is not None),
            "value": result,
            "channel": (cap.invoke or {}).get("channel"),
            # v3.2.1 诚实性: 通道为模拟实现时如实透传 mock 标记
            "mock": bool(result.get("mock")),
        }

    async def _exec_moa(self, cap, inp: dict[str, Any]) -> dict[str, Any]:
        from ..moa import get_moa

        preset = (cap.invoke or {}).get("preset")
        if not preset and cap.id.startswith("moa.preset."):
            preset = cap.id.split("moa.preset.")[-1]
        query = inp.get("query") or inp.get("task") or ""
        moa = get_moa()
        result = await moa.execute(query=query, preset=preset, temperature=0.5, max_tokens=int(inp.get("max_tokens", 2048)))
        content = result.final_content or result.aggregated_content
        return {
            "ok": bool(content),
            "content": content,
            "preset": preset,
            "strategy": result.strategy,
            # 对抗复审 Fix: MoAResult 的字段是 mock_used(非 mock), 必须如实透传 mock 标记
            "mock": bool(getattr(result, "mock_used", False)),
        }

    async def _exec_api(self, cap, inp: dict[str, Any]) -> dict[str, Any]:
        # 真实调用 capability 函数(直调模块), 避免 HTTP 依赖
        module = (cap.invoke or {}).get("module")
        path = (cap.invoke or {}).get("path")
        prompt = inp.get("prompt") or inp.get("query") or inp.get("task") or ""
        if module == "embedding":
            from ..capability.embedding import MockEmbeddingProvider

            vecs = MockEmbeddingProvider(dim=64).embed([prompt])
            return {"ok": True, "value": {"dim": len(vecs[0]) if vecs else 0}, "api": "embedding", "mock": True}
        if path == "/v1/chat/completions":
            from ..model_pool import get_model_pool

            pool = get_model_pool()
            if not pool.endpoints:
                return {"ok": False, "error": "no model endpoints"}
            ep = list(pool.endpoints.keys())[0]
            resp = await pool.call(endpoint_id=ep, messages=[{"role": "user", "content": prompt}], max_tokens=1024)
            return {"ok": True, "content": resp.content, "api": path}
        if path == "/v1/moa/execute":
            # 对抗复审 Fix: 注册了就必须可执行 — 委托给 MoA 引擎
            from ..moa import get_moa

            result = await get_moa().execute(query=prompt, temperature=0.5, max_tokens=1024)
            content = result.final_content or result.aggregated_content
            return {"ok": bool(content), "content": content, "api": path, "mock": bool(getattr(result, "mock_used", False))}
        if path == "/v1/embeddings":
            from ..capability.embedding import MockEmbeddingProvider

            vecs = MockEmbeddingProvider(dim=64).embed([prompt])
            return {"ok": True, "value": {"dim": len(vecs[0]) if vecs else 0}, "api": path, "mock": True}
        return {"ok": False, "error": f"api capability not directly executable: {module or path}"}

    # ---- llm_call (真实 model pool) ----
    def _make_llm_call(self):
        from ..agent_loop.base import LlmOutcome, LlmUsage
        from ..model_pool import get_model_pool

        pool = get_model_pool()

        async def llm_call(messages, **params):
            endpoints = list(pool.endpoints.keys()) if pool.endpoints else []
            if not endpoints:
                return LlmOutcome(content="(no model endpoints configured)")
            ep_id = params.get("endpoint_id", endpoints[0])
            resp = await pool.call(
                endpoint_id=ep_id,
                messages=messages,
                temperature=params.get("temperature", 0.7),
                max_tokens=params.get("max_tokens", 2048),
            )
            return LlmOutcome(
                content=resp.content,
                usage=LlmUsage(
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    cost=resp.cost or 0.0,
                ),
            )

        return llm_call
