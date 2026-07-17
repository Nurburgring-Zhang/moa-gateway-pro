"""ConsensusService — wraps consensus, convergent_detector, conflict_arbiter, multi_mode_synth, moaflow, ensemble_vote.

Exposes:
  - vote_ensemble(votes, method)
  - should_rebalance(stats, config)
  - detect_convergent(proposals, min_support, viability_scores)
  - arbitrate_conflicts(options, criteria)
  - synthesize_multi_mode(mode, proposals, ref_text)
  - check_group_think(session_id, members, rounds, warn_threshold, block_threshold)
  - evaluate_section_viability(text, proposal_idx)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ServiceBase, ServiceMethod, service_method


def _load_consensus():
    from ..capability.consensus import ensemble_vote, should_rebalance, rebalance_endpoints
    return ensemble_vote, should_rebalance, rebalance_endpoints


def _load_convergent():
    from ..capability.convergent_detector import convergent_summary, extract_ideas
    return convergent_summary, extract_ideas


def _load_conflict():
    from ..capability.conflict_arbiter import arbitrate_conflicts
    return arbitrate_conflicts


def _load_multi_mode():
    from ..capability.multi_mode_synth import synthesize
    return synthesize


def _load_moaflow():
    from ..capability.moaflow import MemberResponse, group_think_verdict
    return MemberResponse, group_think_verdict


def _load_section_viability():
    from ..capability.section_viability import evaluate_sections
    return evaluate_sections


class ConsensusService(ServiceBase):
    name = "consensus"
    description = "提案共识: ensemble / rebalance / convergent / conflict / multi-mode / group-think / section-viability"

    def _register_methods(self):
        self._methods["vote_ensemble"] = ServiceMethod(
            name="vote_ensemble", description="集采投票 (majority/weighted/borda/approval)",
            func=self.vote_ensemble,
            input_required=["votes"],
            input_optional=["method"],
        )
        self._methods["should_rebalance"] = ServiceMethod(
            name="should_rebalance", description="检查是否需要 rebalance",
            func=self.should_rebalance,
            input_required=["stats"],
            input_optional=["config"],
        )
        self._methods["detect_convergent"] = ServiceMethod(
            name="detect_convergent", description="检测跨提案 convergent 想法",
            func=self.detect_convergent,
            input_required=["proposals"],
            input_optional=["min_support", "viability_scores"],
        )
        self._methods["arbitrate_conflicts"] = ServiceMethod(
            name="arbitrate_conflicts", description="多提案冲突仲裁",
            func=self.arbitrate_conflicts,
            input_required=["options"], input_optional=["criteria"],
        )
        self._methods["synthesize_multi_mode"] = ServiceMethod(
            name="synthesize_multi_mode", description="多模式综合(classification / comparison / planning)",
            func=self.synthesize_multi_mode,
            input_required=["mode", "proposals"], input_optional=["ref_text"],
        )
        self._methods["check_group_think"] = ServiceMethod(
            name="check_group_think", description="群思检测(moaflow)",
            func=self.check_group_think,
            input_required=["session_id", "members"],
            input_optional=["rounds", "warn_threshold", "block_threshold"],
        )
        self._methods["evaluate_section_viability"] = ServiceMethod(
            name="evaluate_section_viability", description="评估文本章节的可行性",
            func=self.evaluate_section_viability,
            input_required=["text", "proposal_idx"],
        )

    def vote_ensemble(self, votes, method="weighted"):
        ensemble_vote, *_ = _load_consensus()
        from ..capability.consensus import Vote
        v_objs = [Vote(**v) if isinstance(v, dict) else v for v in votes]
        if not v_objs:
            raise ValueError("votes must be non-empty")
        result = ensemble_vote(v_objs, method=method)
        if hasattr(result, "to_dict"):
            return result.to_dict()
        return result

    def should_rebalance(self, stats, config=None):
        _, should_rebalance, _ = _load_consensus()
        from ..capability.consensus import TierStat
        stats_objs = {k: TierStat(**v) if isinstance(v, dict) else v for k, v in stats.items()}
        return {"should_rebalance": should_rebalance(stats_objs, config or {})}

    def detect_convergent(self, proposals, min_support=3, viability_scores=None):
        convergent_summary, extract_ideas = _load_convergent()
        from ..capability.convergent_detector import Proposal, arbitrate_conflicts
        # Convert dict proposals to Proposal objects
        prop_objs = []
        for p in proposals:
            if isinstance(p, dict):
                prop = Proposal(**{k: v for k, v in p.items() if k in Proposal.__dataclass_fields__})
                if not getattr(prop, "ideas", None):
                    prop.ideas = extract_ideas(prop.text, prop.proposal_idx)
                prop_objs.append(prop)
            else:
                prop_objs.append(p)
        summary = convergent_summary(prop_objs, min_support=min_support)
        if viability_scores:
            if "conflicts" in summary:
                summary["arbitrations"] = [
                    {"option_a": c.option_a, "option_b": c.option_b,
                     "winner": w, "confidence": conf}
                    for c, w, conf in arbitrate_conflicts(summary["conflicts"], viability_scores)
                ]
        if hasattr(summary, "to_dict"):
            return summary.to_dict()
        return summary

    def arbitrate_conflicts(self, options, criteria=None):
        if not options:
            raise ValueError("options must be non-empty")
        arbitrate_conflicts = _load_conflict()
        return arbitrate_conflicts(options, criteria=criteria or {})

    def synthesize_multi_mode(self, mode, proposals, ref_text=None):
        synthesize = _load_multi_mode()
        return synthesize(mode=mode, proposals=proposals, ref_text=ref_text or "")

    def check_group_think(self, session_id, members, rounds=None,
                          warn_threshold=0.4, block_threshold=0.7):
        MemberResponse, group_think_verdict = _load_moaflow()
        m_objs = [MemberResponse(**m) if isinstance(m, dict) else m for m in members]
        rounds_objs = None
        if rounds:
            rounds_objs = [[MemberResponse(**m) for m in r] for r in rounds]
        v = group_think_verdict(
            session_id=session_id, members=m_objs, rounds=rounds_objs,
            warn_threshold=warn_threshold, block_threshold=block_threshold,
        )
        if hasattr(v, "to_dict"):
            return v.to_dict()
        return v

    def evaluate_section_viability(self, text, proposal_idx):
        evaluate_sections = _load_section_viability()
        return evaluate_sections(text, proposal_idx=proposal_idx)
