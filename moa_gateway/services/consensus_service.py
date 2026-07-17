"""ConsensusService — wraps consensus, convergent_detector, conflict_arbiter, multi_mode_synth, group_think_check, ensemble_vote, section_viability.

Exposes:
  - build_consensus(proposals, viability_scores)
  - detect_convergent(proposals, min_support, viability_scores)
  - arbitrate_conflicts(options, criteria)
  - synthesize_multi_mode(mode, proposals, ...)
  - check_group_think(members, warn_threshold, block_threshold)
  - vote_ensemble(votes, method)
  - evaluate_section_viability(text, proposal_idx)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ServiceBase, ServiceMethod, service_method


def _load_consensus():
    from ..capability.consensus import build_consensus, check_group_think, ensemble_vote
    return build_consensus, check_group_think, ensemble_vote


def _load_convergent():
    from ..capability.convergent_detector import convergent_summary, extract_ideas
    return convergent_summary, extract_ideas


def _load_conflict():
    from ..capability.conflict_arbiter import arbitrate_conflicts
    return arbitrate_conflicts


def _load_multi_mode():
    from ..capability.multi_mode_synth import synthesize
    return synthesize


def _load_section_viability():
    from ..capability.section_viability import evaluate_sections
    return evaluate_sections


class ConsensusService(ServiceBase):
    name = "consensus"
    description = "提案共识: convergent / conflict / multi-mode / group-think / ensemble-vote / section-viability"

    def _register_methods(self):
        self._methods["build_consensus"] = ServiceMethod(
            name="build_consensus", description="构建多提案共识分数",
            func=self.build_consensus,
            input_required=["proposals"], input_optional=["viability_scores"],
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
            name="synthesize_multi_mode", description="多模式综合(classification / comparison / planning / etc)",
            func=self.synthesize_multi_mode,
            input_required=["mode", "proposals"], input_optional=["ref_text"],
        )
        self._methods["check_group_think"] = ServiceMethod(
            name="check_group_think", description="群思(group-think)检测: 检测过度一致",
            func=self.check_group_think,
            input_required=["members"],
            input_optional=["warn_threshold", "block_threshold"],
        )
        self._methods["vote_ensemble"] = ServiceMethod(
            name="vote_ensemble", description="集采投票(weighted/majority/borda)",
            func=self.vote_ensemble,
            input_required=["votes", "method"],
        )
        self._methods["evaluate_section_viability"] = ServiceMethod(
            name="evaluate_section_viability", description="评估文本章节的可行性",
            func=self.evaluate_section_viability,
            input_required=["text", "proposal_idx"],
        )

    @service_method(name="build_consensus", description="构建多提案共识分数",
                    input_required=["proposals"], input_optional=["viability_scores"])
    def build_consensus(self, proposals, viability_scores=None):
        build_consensus, _gt, _ev = _load_consensus()
        return build_consensus(proposals, viability_scores=viability_scores or {})

    @service_method(name="detect_convergent", description="检测跨提案 convergent 想法",
                    input_required=["proposals"],
                    input_optional=["min_support", "viability_scores"])
    def detect_convergent(self, proposals, min_support=3, viability_scores=None):
        convergent_summary, extract_ideas = _load_convergent()
        ideas = extract_ideas(proposals, min_support=min_support)
        summary = convergent_summary(ideas, viability_scores=viability_scores or {})
        return {"ideas": [i.to_dict() if hasattr(i, "to_dict") else i for i in ideas], "summary": summary}

    @service_method(name="arbitrate_conflicts", description="多提案冲突仲裁",
                    input_required=["options"], input_optional=["criteria"])
    def arbitrate_conflicts(self, options, criteria=None):
        if not options:
            raise ValueError("options must be non-empty")
        arbitrate_conflicts = _load_conflict()
        return arbitrate_conflicts(options, criteria=criteria or {})

    @service_method(name="synthesize_multi_mode", description="多模式综合",
                    input_required=["mode", "proposals"], input_optional=["ref_text"])
    def synthesize_multi_mode(self, mode, proposals, ref_text=None):
        synthesize = _load_multi_mode()
        return synthesize(mode=mode, proposals=proposals, ref_text=ref_text or "")

    @service_method(name="check_group_think", description="群思检测",
                    input_required=["members"],
                    input_optional=["warn_threshold", "block_threshold"])
    def check_group_think(self, members, warn_threshold=0.4, block_threshold=0.7):
        _bc, check_group_think, _ev = _load_consensus()
        return check_group_think(members, warn_threshold=warn_threshold,
                                  block_threshold=block_threshold)

    @service_method(name="vote_ensemble", description="集采投票",
                    input_required=["votes", "method"])
    def vote_ensemble(self, votes, method):
        if not votes:
            raise ValueError("votes must be non-empty")
        _bc, _gt, ensemble_vote = _load_consensus()
        return ensemble_vote(votes, method=method)

    @service_method(name="evaluate_section_viability", description="评估文本章节的可行性",
                    input_required=["text", "proposal_idx"])
    def evaluate_section_viability(self, text, proposal_idx):
        evaluate_sections = _load_section_viability()
        return evaluate_sections(text, proposal_idx=proposal_idx)
