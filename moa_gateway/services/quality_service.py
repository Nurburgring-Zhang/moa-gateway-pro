"""QualityService — wraps flask_score, elo_ranking, gate_l0, score_panel, brainstorm, plan_act, meta_prompt.

Exposes:
  - score_flask(query, response, tasks)  # FLASK 多维度评分
  - rank_elo(action, model_ids, matches)  # ELO 排名
  - gate_l0(query, content)  # L0 安全门禁
  - score_panel(query, answer, criteria)  # 评分面板
  - brainstorm(topic, action)  # 头脑风暴
  - plan_act(query)  # plan/act 模式分类
  - meta_prompt(action, query)  # meta prompt 模板
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


def _load_flask():
    from ..capability.flask_score import score_flask

    return score_flask


# Audit fix: elo_ranking exposes the EloLeaderboard / WorkerPool classes —
# there are no module-level record_match / get_ranked / submit_workers
# functions. A process-wide leaderboard keeps "record" and "ranked" coherent.
_elo_leaderboard = None
_elo_leaderboard_k = None


def _get_elo_leaderboard(k_factor: float = 4.0):
    global _elo_leaderboard, _elo_leaderboard_k
    from ..capability.elo_ranking import EloLeaderboard

    if _elo_leaderboard is None or _elo_leaderboard_k != float(k_factor):
        _elo_leaderboard = EloLeaderboard(k_factor=float(k_factor))
        _elo_leaderboard_k = float(k_factor)
    return _elo_leaderboard


def _load_elo():
    from ..capability.elo_ranking import EloLeaderboard, WorkerPool

    return EloLeaderboard, WorkerPool


def _load_gate():
    from ..capability.gate_l0 import gate as gate_l0_check

    return gate_l0_check


def _load_score_panel():
    from ..capability.score_panel import score_panel

    return score_panel


def _load_brainstorm():
    from ..capability.brainstorm import BrainstormSession, DecideMode

    return BrainstormSession, DecideMode


def _load_plan_act():
    # Audit fix: plan_act only exports classify_mode (plan_and_act never
    # existed).
    from ..capability.plan_act import classify_mode

    return classify_mode


def _load_meta_prompt():
    # Audit fix: real API is get_stage_prompts / cognitively_clash /
    # fuse_decision (get_stages / clash / fuse do not exist).
    from ..capability.meta_prompt import (
        cognitively_clash,
        fuse_decision,
        get_stage_prompts,
    )

    return get_stage_prompts, cognitively_clash, fuse_decision


class QualityService(ServiceBase):
    name = "quality"
    description = "质量保证: FLASK / ELO / gate / score panel / brainstorm / plan-act / meta-prompt"

    def _register_methods(self):
        self._methods["score_flask"] = ServiceMethod(
            name="score_flask",
            description="FLASK 多维度评分 (Truthfulness/Logic/etc)",
            func=self.score_flask,
            input_required=["query", "response"],
            input_optional=["tasks"],
        )
        self._methods["rank_elo"] = ServiceMethod(
            name="rank_elo",
            description=(
                "ELO 排名 (EloLeaderboard): record 记录对局并重算 rating / ranked 返回当前榜单 / "
                "submit 用 WorkerPool 分配 worker 负载 (lottery|shortest_queue)"
            ),
            func=self.rank_elo,
            input_required=["action"],
            input_optional=["model_ids", "matches", "k_factor", "workers", "strategy", "max_jobs_per_worker"],
        )
        self._methods["gate_l0"] = ServiceMethod(
            name="gate_l0",
            description="L0 安全门禁: 检测有害 query",
            func=self.gate_l0,
            input_required=["query"],
        )
        self._methods["score_panel"] = ServiceMethod(
            name="score_panel",
            description="多维度评分面板 (tq/co/ap/se/in 五维, criteria 作为 rubric 权重)",
            func=self.score_panel,
            input_required=["query", "answer"],
            input_optional=["criteria"],
        )
        self._methods["brainstorm"] = ServiceMethod(
            name="brainstorm",
            description="头脑风暴 (ideas / decide)",
            func=self.brainstorm,
            input_required=["topic", "action"],
            input_optional=["detailed", "options"],
        )
        self._methods["plan_act"] = ServiceMethod(
            name="plan_act",
            description="plan/act 模式分类: classify_mode(query) 判定模式与置信度 (纯分类, 不执行)",
            func=self.plan_act,
            input_required=["query"],
        )
        self._methods["meta_prompt"] = ServiceMethod(
            name="meta_prompt",
            description="meta prompt 模板 (get_stages / clash / fuse)",
            func=self.meta_prompt,
            input_required=["action"],
            input_optional=["query", "role_a", "role_b", "options", "context", "roles"],
        )

    def score_flask(self, query, response, tasks=None):
        score_flask = _load_flask()
        # flask takes (answer, query), not (query, response, tasks). It's a sync function.
        score = score_flask(answer=response, query=query)
        if hasattr(score, "to_dict"):
            return score.to_dict()
        if isinstance(score, dict):
            return score
        return {"score": str(score)}

    def rank_elo(
        self,
        action,
        model_ids=None,
        matches=None,
        k_factor=4.0,
        workers=None,
        strategy="lottery",
        max_jobs_per_worker=4,
    ):
        # Audit fix: drive the real EloLeaderboard / WorkerPool classes.
        _EloLeaderboard, WorkerPool = _load_elo()
        if action == "record":
            if not model_ids or not matches:
                raise ValueError("record requires model_ids and matches")
            board = _get_elo_leaderboard(k_factor)
            for mid in model_ids:
                board.add_model(str(mid))
            recorded = []
            for m in matches:
                if isinstance(m, dict):
                    winner, loser = m.get("winner_id") or m.get("winner"), m.get("loser_id") or m.get("loser")
                elif isinstance(m, (list, tuple)) and len(m) == 2:
                    winner, loser = m
                else:
                    raise ValueError("each match must be {winner_id, loser_id} dict or [winner, loser]")
                if not winner or not loser:
                    raise ValueError("match requires winner and loser")
                mr = board.record_match(str(winner), str(loser))
                recorded.append(asdict(mr))
            return {
                "recorded": recorded,
                "ranked": [asdict(r) for r in board.ranked()],
                "k_factor": board.k_factor,
            }
        if action == "ranked":
            board = _get_elo_leaderboard(k_factor)
            return board.to_dict()
        if action == "submit":
            if not workers:
                raise ValueError("submit requires workers list")
            pool = WorkerPool([str(w) for w in workers], max_jobs_per_worker=int(max_jobs_per_worker))
            try:
                if strategy:
                    pool.set_strategy(strategy)
                return {
                    "pool": pool.to_dict(),
                    "worker_loads": pool.worker_loads(),
                    "strategy": pool.get_strategy(),
                    "workers": pool.workers(),
                }
            finally:
                pool.shutdown(wait=True)
        raise ValueError(f"unknown action: {action}, expected record/ranked/submit")

    def gate_l0(self, query, context=None):
        gate_l0_check = _load_gate()
        v = gate_l0_check(query, context=context)
        if hasattr(v, "to_dict"):
            return v.to_dict()
        return v

    def score_panel(self, query, answer, criteria=None):
        # Audit fix: score_panel takes rubric=dict[str, float] (there is no
        # criteria kwarg). criteria dicts are passed straight through as rubric.
        score_panel = _load_score_panel()
        rubric = criteria if isinstance(criteria, dict) else None
        panel = score_panel(query=query, answer=answer, rubric=rubric)
        out = panel.to_dict() if hasattr(panel, "to_dict") else asdict(panel)
        return out

    def brainstorm(self, topic, action, detailed=False, options=None):
        BrainstormSession, DecideMode = _load_brainstorm()
        if action == "ideas":
            session = BrainstormSession(topic)
            if detailed:
                ideas_obj = session.generate_ideas_detailed()
                return {"topic": topic, "ideas": {k.value: asdict(v) for k, v in ideas_obj.items()}}
            ideas_obj = session.generate_ideas()
            return {"topic": topic, "ideas": {k.value: v for k, v in ideas_obj.items()}}
        if action == "decide":
            dm = DecideMode(topic, options or [])
            return {"topic": topic, "advocates": dm.generate_advocates()}
        raise ValueError(f"unknown action: {action}, expected ideas/decide")

    def plan_act(self, query):
        # Audit fix: the capability module only ships classify_mode — the
        # service now returns the real plan/act classification (no execution).
        classify_mode = _load_plan_act()
        return classify_mode(query)

    def meta_prompt(self, action, query=None, role_a=None, role_b=None, options=None, context=None, roles=None):
        # Audit fix: real API — get_stage_prompts / cognitively_clash / fuse_decision.
        get_stages, clash, fuse = _load_meta_prompt()
        if action == "get_stages":
            if not query:
                raise ValueError("get_stages requires query")
            prompts = get_stages(query, roles=roles)
            return {
                "stages": [
                    {**asdict(p), "stage": p.stage.value if hasattr(p.stage, "value") else str(p.stage)}
                    for p in prompts
                ]
            }
        if action == "clash":
            if not query or not role_a or not role_b:
                raise ValueError("clash requires query, role_a, role_b")
            view_a, view_b = clash(role_a, role_b, query)
            return {"query": query, "role_a_view": view_a, "role_b_view": view_b}
        if action == "fuse":
            if not options:
                raise ValueError("fuse requires options list")
            return {"options": list(options), "decision": fuse(list(options), context=context or "")}
        raise ValueError(f"unknown action: {action}, expected get_stages/clash/fuse")
