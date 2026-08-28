"""O3 — Planner/Matcher: 任务画像 -> 能力组合执行计划(DAG)。

"匹配化/联合/复合"的核心: 依据 TaskAnalyzer 的画像, 在 CapabilityRegistry 中
*真实选择*存在的能力并组合成执行计划。绝不选择注册表中不存在的能力。

策略(确定性 + 可选 LLM 增强):
  - 依 profile.capability_hints 在 registry 中检索真实能力
  - needs_moa / 复杂度高 -> 追加 MoA 聚合步
  - is_composite -> 组合多能力 + 聚合步, 用 depends_on 表达 DAG
输出:
  {"steps":[{"step_id","capability_id","type","depends_on","input","title"}...],
   "rationale","selected_capabilities","plan_mode"}
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .registry import (
    CAP_API,
    CAP_CLI,
    CAP_GRAPH,
    CAP_LOOP,
    CAP_MCP,
    CAP_MOA,
    CAP_SKILL,
    CapabilityRegistry,
    get_registry,
)

logger = logging.getLogger(__name__)


class Planner:
    """把任务画像匹配为能力组合计划。

    接受可选 reinforcer 形成闭环强化(对抗复审 Fix): 多个候选能力命中同一 hint 时,
    优先选择历史评分高者, 并回避已知频繁失败(低分)的能力。
    """

    def __init__(self, registry: CapabilityRegistry | None = None, reinforcer=None) -> None:
        self._registry = registry or get_registry()
        self._reinforcer = reinforcer

    def _score(self, cap_id: str) -> float:
        if self._reinforcer is None:
            return 0.5
        try:
            return float(self._reinforcer.get_score(cap_id))
        except Exception:  # noqa: BLE001
            return 0.5

    def _pick_best(self, matched: list) -> object | None:
        """从候选能力中按强化评分择优; 回避评分过低(<=0.25, 反复失败)的能力。"""
        if not matched:
            return None
        scored = sorted(matched, key=lambda c: self._score(c.id), reverse=True)
        for c in scored:
            if self._score(c.id) > 0.25:
                return c
        # 全部低分时仍返回评分最高者(如实, 不静默丢弃)
        return scored[0]

    def plan(
        self,
        profile: dict[str, Any],
        task_input: dict[str, Any] | None = None,
        privileged: bool = True,
    ) -> dict[str, Any]:
        if profile.get("error"):
            return {"steps": [], "rationale": f"invalid profile: {profile['error']}", "selected_capabilities": [], "plan_mode": "none"}

        text = profile.get("text", "")
        hints = profile.get("capability_hints", [])
        needs_moa = bool(profile.get("needs_moa"))
        is_composite = bool(profile.get("is_composite"))
        tin = task_input or {}

        # v3.2.1 hardening: non-privileged callers (API-key / readonly roles)
        # must never reach sandboxed code execution — the same trust model as
        # routes/agent.py (builtin dangerous tools are admin/operator-only).
        # Custom skills run the SAME sandbox, so they are filtered too
        # (red-team: leaving them reachable would make the sandbox the only
        # boundary between a readonly key and code execution). Filtered at
        # planning time so the returned plan is what the caller can actually
        # run; the executor re-checks anyway.
        filtered_privileged: list[str] = []

        steps: list[dict[str, Any]] = []
        selected: list[str] = []
        rationale_bits: list[str] = []

        # 1) 依 hints 在注册表中检索真实能力并加入计划(并行步, 互不依赖)。
        #    允许同类多个不同能力(如多个 skill); 仅按 capability id 去重。
        seen_ids: set[str] = set()
        for hint in hints:
            cap_type = hint.get("type")
            names = hint.get("names") or []
            matched = self._registry.search(keywords=names or [cap_type], cap_types=[cap_type] if cap_type else None)
            if not matched:
                continue
            cap = self._pick_best(matched)
            if cap is None or cap.id in seen_ids:
                continue
            if not privileged and cap.type == CAP_SKILL:
                filtered_privileged.append(cap.name)
                continue
            seen_ids.add(cap.id)
            sid = f"s{len(steps) + 1}"
            steps.append(
                {
                    "step_id": sid,
                    "capability_id": cap.id,
                    "type": cap.type,
                    "title": f"调用能力 {cap.name}",
                    "depends_on": [],
                    "input": self._step_input(cap, text, tin),
                }
            )
            selected.append(cap.id)
            rationale_bits.append(f"命中 {cap.type}:{cap.name} (关键词 {hint.get('matched')})")

        # 1b) 具名技能自动调用: 任务文本中直接出现某个已注册 skill 的名字(含自定义/热部署
        #     skill)时, 自动纳入计划 — 使新开发的 skill 无需改代码即可被编排器调用。
        lowered = text.lower()
        for cap in self._registry.by_type(CAP_SKILL):
            if cap.id in seen_ids:
                continue
            token = cap.name.lower()
            if not privileged:
                filtered_privileged.append(cap.name)
                continue
            # 对抗复审 Fix: 用词边界匹配而非子串匹配, 避免短名 skill(run/get/api...)误触发
            if len(token) >= 3 and re.search(r"(?<![a-z0-9_])" + re.escape(token) + r"(?![a-z0-9_])", lowered):
                seen_ids.add(cap.id)
                steps.append(
                    {
                        "step_id": f"s{len(steps) + 1}",
                        "capability_id": cap.id,
                        "type": CAP_SKILL,
                        "title": f"按名调用技能 {cap.name}",
                        "depends_on": [],
                        "input": self._step_input(cap, text, tin),
                    }
                )
                selected.append(cap.id)
                rationale_bits.append(f"任务文本提及技能名 {cap.name} -> 自动调用")

        # 2) 需要 MoA 或复杂度高 -> 追加 MoA 聚合步(依赖前面所有步)
        if needs_moa or profile.get("complexity", 0) >= 7:
            moa_caps = self._registry.by_type(CAP_MOA)
            preset_cap = self._pick_moa_preset(moa_caps, profile)
            if preset_cap:
                dep_ids = [s["step_id"] for s in steps]
                steps.append(
                    {
                        "step_id": f"s{len(steps) + 1}",
                        "capability_id": preset_cap.id,
                        "type": CAP_MOA,
                        "title": "MoA 多模型聚合",
                        "depends_on": dep_ids,
                        "input": {"query": text, "aggregate_of": dep_ids},
                    }
                )
                selected.append(preset_cap.id)
                rationale_bits.append(f"复杂度高/需MoA -> 追加 {preset_cap.name}")

        # 3) 复合任务但尚无聚合步 -> 用 plan_execute loop 收口(依赖前面所有步)
        elif is_composite and len(steps) >= 2:
            loop_caps = self._registry.search(keywords=["plan_execute"], cap_types=[CAP_LOOP])
            if loop_caps:
                dep_ids = [s["step_id"] for s in steps]
                steps.append(
                    {
                        "step_id": f"s{len(steps) + 1}",
                        "capability_id": loop_caps[0].id,
                        "type": CAP_LOOP,
                        "title": "plan_execute 循环收口",
                        "depends_on": dep_ids,
                        "input": {"messages": [{"role": "user", "content": text}], "aggregate_of": dep_ids},
                    }
                )
                selected.append(loop_caps[0].id)
                rationale_bits.append("复合任务 -> plan_execute 收口")

        # 4) 兜底: 没有任何能力命中 -> 直接走 chat/MoA 单步
        if not steps:
            fallback = self._registry.search(keywords=["chat"], cap_types=[CAP_API]) or self._registry.by_type(CAP_MOA)
            if fallback:
                cap = fallback[0]
                steps.append(
                    {
                        "step_id": "s1",
                        "capability_id": cap.id,
                        "type": cap.type,
                        "title": f"直接调用 {cap.name}",
                        "depends_on": [],
                        "input": self._step_input(cap, text, tin),
                    }
                )
                selected.append(cap.id)
                rationale_bits.append("无能力命中 -> 直接 chat/MoA")
            else:
                rationale_bits.append("注册表为空, 无法规划")

        plan_mode = "moa_aggregate" if needs_moa else ("composite" if is_composite else ("single" if len(steps) == 1 else "multi"))
        plan = {
            "steps": steps,
            "rationale": "; ".join(rationale_bits),
            "selected_capabilities": selected,
            "plan_mode": plan_mode,
        }
        if filtered_privileged:
            # 诚实披露: 计划中明确记录因权限被排除的技能, 不静默丢弃
            plan["filtered_privileged_skills"] = sorted(set(filtered_privileged))
        return plan

    # ---- helpers ----
    def _pick_moa_preset(self, moa_caps: list, profile: dict[str, Any]):
        if not moa_caps:
            return None
        # 复杂度高选 quality, 否则 balanced; 都不在则取首个
        complexity = profile.get("complexity", 5)
        preferred = "moa.preset.quality" if complexity >= 8 else "moa.preset.balanced"
        for c in moa_caps:
            if c.id == preferred:
                return c
        return moa_caps[0]

    def _step_input(self, cap, text: str, tin: dict[str, Any]) -> dict[str, Any]:
        kind = (cap.invoke or {}).get("kind")
        if kind == "skill":
            return {"task": text, **tin}
        if kind == "loop":
            return {"messages": [{"role": "user", "content": text}], "max_iterations": tin.get("max_iterations", 3)}
        if kind == "graph":
            return {"context": {"user_input": text}}
        if kind == "mcp":
            return {"arguments": {"query": text}}
        if kind == "cli":
            return {"query": text}
        if kind == "moa":
            return {"query": text}
        if kind in ("api", "api_route"):
            return {"prompt": text}
        return {"input": text}
