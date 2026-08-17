"""ConsensusService — wraps consensus, convergent_detector, conflict_arbiter, multi_mode_synth, moaflow, section_viability.

Exposes:
  - vote_ensemble(votes, method)
  - should_rebalance(stats, config)
  - detect_convergent(proposals, min_support, viability_scores)
  - arbitrate_conflicts(options, criteria)
  - synthesize_multi_mode(mode, proposals, ...)
  - check_group_think(session_id, members, rounds, warn_threshold, block_threshold)
  - evaluate_section_viability(text, proposal_idx)
"""

from __future__ import annotations

import logging
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


def _load_consensus():
    from ..capability.consensus import ensemble_vote, rebalance_endpoints, should_rebalance

    return ensemble_vote, should_rebalance, rebalance_endpoints


def _load_convergent():
    from ..capability.convergent_detector import (
        ConflictPair,
        arbitrate_conflicts,
        convergent_summary,
        extract_ideas,
    )

    return convergent_summary, extract_ideas, arbitrate_conflicts, ConflictPair


def _load_conflict():
    # Audit fix: conflict_arbiter exposes arbitrate(options, total_proposals)
    # over ConflictOption objects (there is no arbitrate_conflicts there).
    from ..capability.conflict_arbiter import (
        ConflictOption,
        arbitrate,
        verdict_to_dict,
    )

    return arbitrate, ConflictOption, verdict_to_dict


def _load_multi_mode():
    # Audit fix: multi_mode_synth exposes run_synthesis(mode, proposals, **kw)
    # (no `synthesize`).
    from ..capability.multi_mode_synth import Proposal, SynthesisMode, run_synthesis

    return run_synthesis, SynthesisMode, Proposal


def _load_moaflow():
    from ..capability.moaflow import MemberResponse, group_think_verdict

    return MemberResponse, group_think_verdict


def _load_section_viability():
    # Audit fix: the real entry point is validate_proposal (no evaluate_sections).
    from ..capability.section_viability import validate_proposal

    return validate_proposal


def _synth_result_to_dict(result) -> dict:
    d = asdict(result)
    if hasattr(d.get("mode"), "value"):
        d["mode"] = d["mode"].value
    return d


class ConsensusService(ServiceBase):
    name = "consensus"
    description = "提案共识: ensemble / rebalance / convergent / conflict / multi-mode / group-think / section-viability"

    def _register_methods(self):
        self._methods["vote_ensemble"] = ServiceMethod(
            name="vote_ensemble",
            description="集采投票 (majority/weighted/borda/approval)",
            func=self.vote_ensemble,
            input_required=["votes"],
            input_optional=["method"],
        )
        self._methods["should_rebalance"] = ServiceMethod(
            name="should_rebalance",
            description="检查是否需要 rebalance",
            func=self.should_rebalance,
            input_required=["stats"],
            input_optional=["config"],
        )
        self._methods["detect_convergent"] = ServiceMethod(
            name="detect_convergent",
            description="检测跨提案 convergent 想法 (+ conflicts; viability_scores 非空时附仲裁)",
            func=self.detect_convergent,
            input_required=["proposals"],
            input_optional=["min_support", "viability_scores"],
        )
        self._methods["arbitrate_conflicts"] = ServiceMethod(
            name="arbitrate_conflicts",
            description=(
                "冲突仲裁: options 为 ConflictPair dicts {option_a, option_b} 时走 convergent_detector "
                "(criteria 作 viability_scores); 为 ConflictOption dicts 时走 conflict_arbiter.arbitrate "
                "(criteria.total_proposals 作总提案数)"
            ),
            func=self.arbitrate_conflicts,
            input_required=["options"],
            input_optional=["criteria"],
        )
        self._methods["synthesize_multi_mode"] = ServiceMethod(
            name="synthesize_multi_mode",
            description=(
                "多模式综合 run_synthesis: classification / integrated_synthesis / final_selection / "
                "cross_iteration; 可选 scores / target_chars / prev_proposals"
            ),
            func=self.synthesize_multi_mode,
            input_required=["mode", "proposals"],
            input_optional=["scores", "target_chars", "prev_proposals"],
        )
        self._methods["check_group_think"] = ServiceMethod(
            name="check_group_think",
            description="群思检测(moaflow)",
            func=self.check_group_think,
            input_required=["session_id", "members"],
            input_optional=["rounds", "warn_threshold", "block_threshold"],
        )
        self._methods["evaluate_section_viability"] = ServiceMethod(
            name="evaluate_section_viability",
            description="评估提案文本各章节可行性 (validate_proposal → ProposalReport + AP score)",
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
        convergent_summary, extract_ideas, cd_arbitrate, ConflictPair = _load_convergent()
        from ..capability.convergent_detector import Idea, Proposal

        # Convert dict/string proposals to Proposal objects
        prop_objs = []
        for idx, p in enumerate(proposals):
            if isinstance(p, str):
                # string → wrap as Proposal with text = the string, ideas = [single Idea]
                prop = Proposal(
                    text=p,
                    proposal_idx=idx,
                    author=f"prop_{idx}",
                    ideas=[Idea(text=p, source_proposal_idx=idx)],
                )
                prop_objs.append(prop)
            elif isinstance(p, dict):
                prop = Proposal(
                    **{k: v for k, v in p.items() if k in Proposal.__dataclass_fields__}
                )
                if "ideas" not in p and not getattr(prop, "ideas", None):
                    try:
                        prop.ideas = extract_ideas(prop.text, prop.proposal_idx)
                    except (AttributeError, TypeError):
                        # Proposal 没 ideas 字段,跳过提取
                        pass
                prop_objs.append(prop)
            else:
                prop_objs.append(p)
        summary = convergent_summary(prop_objs, min_support=min_support)
        if viability_scores and summary.get("conflicts"):
            # Audit fix: summary["conflicts"] carries dicts (ConflictPair.to_dict);
            # rebuild ConflictPair objects for the real arbitrate_conflicts API.
            pairs = []
            for c in summary["conflicts"]:
                pairs.append(
                    ConflictPair(
                        option_a=c.get("option_a", ""),
                        option_b=c.get("option_b", ""),
                        supporting_a=list(c.get("supporting_a", [])),
                        supporting_b=list(c.get("supporting_b", [])),
                    )
                )
            # viability_scores keys arrive as strings over JSON — normalize to int
            vs = {int(k): float(v) for k, v in viability_scores.items()}
            summary["arbitrations"] = [
                {
                    "option_a": pair.option_a,
                    "option_b": pair.option_b,
                    "winner": winner,
                    "confidence": conf,
                }
                for pair, winner, conf in cd_arbitrate(pairs, vs)
            ]
        if hasattr(summary, "to_dict"):
            return summary.to_dict()
        return summary

    def arbitrate_conflicts(self, options, criteria=None):
        if not options:
            raise ValueError("options must be non-empty")
        arbitrate, ConflictOption, verdict_to_dict = _load_conflict()
        _, _, cd_arbitrate, ConflictPair = _load_convergent()
        criteria = criteria or {}
        first = options[0]
        if isinstance(first, dict) and ("option_a" in first or "option_b" in first):
            # ConflictPair shape → convergent_detector.arbitrate_conflicts
            pairs = []
            for c in options:
                pairs.append(
                    ConflictPair(
                        option_a=str(c.get("option_a", "")),
                        option_b=str(c.get("option_b", "")),
                        supporting_a=list(c.get("supporting_a", [])),
                        supporting_b=list(c.get("supporting_b", [])),
                    )
                )
            vs = {int(k): float(v) for k, v in (criteria or {}).items()}
            return [
                {
                    "option_a": pair.option_a,
                    "option_b": pair.option_b,
                    "winner": winner,
                    "confidence": conf,
                }
                for pair, winner, conf in cd_arbitrate(pairs, vs)
            ]
        # ConflictOption shape → conflict_arbiter.arbitrate
        opt_objs = []
        for o in options:
            if isinstance(o, ConflictOption):
                opt_objs.append(o)
            elif isinstance(o, dict):
                valid = {k: v for k, v in o.items() if k in ConflictOption.__dataclass_fields__}
                opt_objs.append(ConflictOption(**valid))
            else:
                raise ValueError("each option must be a dict")
        verdict = arbitrate(opt_objs, total_proposals=int(criteria.get("total_proposals", 0)))
        return verdict_to_dict(verdict)

    def synthesize_multi_mode(self, mode, proposals, scores=None, target_chars=None, prev_proposals=None):
        # Audit fix: drive run_synthesis(mode, proposals, **kwargs).
        run_synthesis, SynthesisMode, Proposal = _load_multi_mode()
        try:
            mode_enum = SynthesisMode(str(mode))
        except ValueError as e:
            valid = [m.value for m in SynthesisMode]
            raise ValueError(f"unknown mode: {mode!r}, expected one of {valid}") from e

        def _to_proposals(items):
            out = []
            for idx, p in enumerate(items or []):
                if isinstance(p, Proposal):
                    out.append(p)
                elif isinstance(p, str):
                    out.append(Proposal(proposal_idx=idx, author=f"prop_{idx}", text=p))
                elif isinstance(p, dict):
                    valid = {k: v for k, v in p.items() if k in Proposal.__dataclass_fields__}
                    if "proposal_idx" not in valid:
                        valid["proposal_idx"] = idx
                    if "author" not in valid:
                        valid["author"] = f"prop_{idx}"
                    out.append(Proposal(**valid))
                else:
                    raise ValueError("each proposal must be str or dict")
            return out

        prop_objs = _to_proposals(proposals)
        kwargs = {}
        if scores:
            kwargs["scores"] = {int(k): float(v) for k, v in scores.items()}
        if target_chars is not None:
            kwargs["target_chars"] = int(target_chars)
        if prev_proposals is not None:
            kwargs["prev_proposals"] = _to_proposals(prev_proposals)
        result = run_synthesis(mode_enum, prop_objs, **kwargs)
        return _synth_result_to_dict(result)

    def check_group_think(
        self, session_id, members, rounds=None, warn_threshold=0.4, block_threshold=0.7
    ):
        MemberResponse, group_think_verdict = _load_moaflow()
        # 容错: string / dict / MemberResponse 三种类型都接
        m_objs = []
        for _idx, m in enumerate(members):
            if isinstance(m, str):
                m_objs.append(MemberResponse(member_id=m, content=""))
            elif isinstance(m, dict):
                m_objs.append(
                    MemberResponse(
                        **{k: v for k, v in m.items() if k in MemberResponse.__dataclass_fields__}
                    )
                )
            else:
                m_objs.append(m)
        rounds_objs = None
        if rounds:
            rounds_objs = [[MemberResponse(**m) for m in r] for r in rounds]
        v = group_think_verdict(
            session_id=session_id,
            members=m_objs,
            rounds=rounds_objs,
            warn_threshold=warn_threshold,
            block_threshold=block_threshold,
        )
        if hasattr(v, "to_dict"):
            return v.to_dict()
        return v

    def evaluate_section_viability(self, text, proposal_idx):
        # Audit fix: real entry point is validate_proposal(text, proposal_idx).
        validate_proposal = _load_section_viability()
        report = validate_proposal(str(text), proposal_idx=int(proposal_idx))
        return asdict(report)
