"""QualityService — wraps flask_score, elo_ranking, gate_l0, score_panel, brainstrom, plan_act, meta_prompt.

Exposes:
  - score_flask(query, response, tasks)  # FLASK 多维度评分
  - rank_elo(action, model_ids, matches)  # ELO 排名
  - gate_l0(query, content)  # L0 安全门禁
  - score_panel(query, answer, criteria)  # 评分面板
  - brainstorm(topic, action)  # 头脑风暴
  - plan_act(query)  # plan+act 编排
  - meta_prompt(action, query)  # meta prompt 模板
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_flask():
    from ..capability.flask_score import score_flask

    return score_flask


def _load_elo():
    from ..capability.elo_ranking import get_ranked, record_match, submit_workers

    return record_match, get_ranked, submit_workers


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
    from ..capability.plan_act import plan_and_act

    return plan_and_act


def _load_meta_prompt():
    from ..capability.meta_prompt import clash, fuse, get_stages

    return get_stages, clash, fuse


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
            description="ELO 排名 (record / ranked / submit workers)",
            func=self.rank_elo,
            input_required=["action"],
            input_optional=["model_ids", "matches", "k_factor", "workers", "strategy"],
        )
        self._methods["gate_l0"] = ServiceMethod(
            name="gate_l0",
            description="L0 安全门禁: 检测有害 query",
            func=self.gate_l0,
            input_required=["query"],
        )
        self._methods["score_panel"] = ServiceMethod(
            name="score_panel",
            description="多维度评分面板",
            func=self.score_panel,
            input_required=["query", "answer"],
            input_optional=["criteria"],
        )
        self._methods["brainstorm"] = ServiceMethod(
            name="brainstorm",
            description="头脑风暴 (ideas / decide)",
            func=self.brainstorm,
            input_required=["topic", "action"],
        )
        self._methods["plan_act"] = ServiceMethod(
            name="plan_act",
            description="plan+act 编排",
            func=self.plan_act,
            is_async=True,
            input_required=["query"],
        )
        self._methods["meta_prompt"] = ServiceMethod(
            name="meta_prompt",
            description="meta prompt 模板 (stages / clash / fuse)",
            func=self.meta_prompt,
            input_required=["action"],
            input_optional=["query", "role_a", "role_b", "options", "context"],
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
        self, action, model_ids=None, matches=None, k_factor=4.0, workers=None, strategy="lottery"
    ):
        record_match, get_ranked, submit_workers = _load_elo()
        if action == "record":
            if not model_ids or not matches:
                raise ValueError("record requires model_ids and matches")
            return record_match(model_ids=model_ids, matches=matches, k_factor=k_factor)
        if action == "ranked":
            return get_ranked()
        if action == "submit":
            if not workers:
                raise ValueError("submit requires workers list")
            return submit_workers(workers=workers, strategy=strategy)
        raise ValueError(f"unknown action: {action}, expected record/ranked/submit")

    def gate_l0(self, query, context=None):
        gate_l0_check = _load_gate()
        v = gate_l0_check(query, context=context)
        if hasattr(v, "to_dict"):
            return v.to_dict()
        return v

    def score_panel(self, query, answer, criteria=None):
        score_panel = _load_score_panel()
        return score_panel(query=query, answer=answer, criteria=criteria or [])

    def brainstorm(self, topic, action, detailed=False, options=None):
        BrainstormSession, DecideMode = _load_brainstorm()
        if action == "ideas":
            session = BrainstormSession(topic)
            ideas_obj = session.generate_ideas_detailed() if detailed else session.generate_ideas()
            if isinstance(ideas_obj, dict):
                return ideas_obj
            return {k: v.__dict__ for k, v in ideas_obj.items()}
        if action == "decide":
            dm = DecideMode(topic, options or [])
            return {"advocates": dm.generate_advocates()}
        raise ValueError(f"unknown action: {action}, expected ideas/decide")

    async def plan_act(self, query):
        plan_and_act = _load_plan_act()
        return await plan_and_act(query)

    def meta_prompt(self, action, query=None, role_a=None, role_b=None, options=None, context=None):
        get_stages, clash, fuse = _load_meta_prompt()
        if action == "get_stages":
            return get_stages()
        if action == "clash":
            if not query or not role_a or not role_b:
                raise ValueError("clash requires query, role_a, role_b")
            return clash(query, role_a, role_b)
        if action == "fuse":
            if not options:
                raise ValueError("fuse requires options list")
            return fuse(options, context=context or "")
        raise ValueError(f"unknown action: {action}, expected get_stages/clash/fuse")
