"""MoAService — wraps n_layer_moa, moa_engine, cross_iter_synth.

Exposes:
  - run_three_layer(query, proposers, aggregators, temperature, max_total_tokens)
  - run_engine(query, proposers, aggregator, validate_only)
  - cross_iter(iters, action)  # convergence / best_of_each / adoption / step5
  - validate_config(proposers, aggregator)  # validate_only mode
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_n_layer_moa():
    from ..capability.n_layer_moa import (
        Aggregator,
        Proposer,
        run_three_layer_moa,
    )

    return Proposer, Aggregator, run_three_layer_moa


def _load_moa_engine():
    from ..capability.moa_engine import Aggregator, Proposer, run_moa, validate_moa

    return Proposer, Aggregator, run_moa, validate_moa


def _load_cross_iter():
    from ..capability.cross_iter_synth import (
        adoption_rate,
        analyze_convergence,
        best_of_each,
        step5_review,
    )

    return analyze_convergence, best_of_each, adoption_rate, step5_review


class MoAService(ServiceBase):
    name = "moa"
    description = "Mixture-of-Agents orchestration: 3-layer / multi-mode / engine / cross-iter"

    def _register_methods(self):
        self._methods["run_three_layer"] = ServiceMethod(
            name="run_three_layer",
            description="3-layer MoA (论文主架构): L1 proposers 并行, L2 + L3 用 aggregators 合成",
            func=self.run_three_layer,
            is_async=True,
            input_required=["query", "proposers", "aggregators"],
            input_optional=["temperature", "max_total_tokens"],
        )
        self._methods["run_engine"] = ServiceMethod(
            name="run_engine",
            description="MoA Engine (3 proposer + 1 aggregator), 轻量级单层 MoA",
            func=self.run_engine,
            is_async=True,
            input_required=["query", "proposers", "aggregator"],
            input_optional=["validate_only", "temperature"],
        )
        self._methods["cross_iter"] = ServiceMethod(
            name="cross_iter",
            description="跨迭代分析: convergence / best_of_each / adoption / step5",
            func=self.cross_iter,
            input_required=["iters", "action"],
        )
        self._methods["validate_config"] = ServiceMethod(
            name="validate_config",
            description="校验 MoA 配置(不实际运行)",
            func=self.validate_config,
            input_required=["proposers", "aggregator"],
        )

    async def run_three_layer(
        self, query, proposers, aggregators, temperature=0.6, max_total_tokens=0
    ):
        Proposer, Aggregator, run_three_layer_moa = _load_n_layer_moa()
        ps = []
        for p in proposers:
            valid = {k: v for k, v in p.items() if k in Proposer.__dataclass_fields__}
            ps.append(Proposer(**valid))
        aggs = []
        for a in aggregators:
            valid = {k: v for k, v in a.items() if k in Aggregator.__dataclass_fields__}
            aggs.append(Aggregator(**valid))
        if not ps:
            raise ValueError("proposers must be non-empty")
        if len(aggs) != 3:
            raise ValueError(f"3-layer MoA needs exactly 3 aggregators, got {len(aggs)}")
        result = await run_three_layer_moa(
            query,
            proposers=ps,
            aggregators=aggs,
            temperature=temperature,
            max_total_tokens=max_total_tokens,
        )
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"result": str(result)}

    async def run_engine(self, query, proposers, aggregator, validate_only=False, temperature=0.6):
        Proposer, Aggregator, run_moa, validate_moa = _load_moa_engine()
        ps = []
        for p in proposers:
            # filter out unknown fields like 'name' that aren't in Proposer
            valid = {k: v for k, v in p.items() if k in Proposer.__dataclass_fields__}
            ps.append(Proposer(**valid))
        agg = None
        if aggregator:
            valid = {k: v for k, v in aggregator.items() if k in Aggregator.__dataclass_fields__}
            agg = Aggregator(**valid)
        if validate_only:
            errors = validate_moa(ps, agg)
            return {"validate_only": True, "errors": errors, "valid": len(errors) == 0}
        if not ps:
            raise ValueError("proposers must be non-empty")

        # provider_fn: 同步函数,返回 (text, tokens). 走 mock provider (default) 或 build_provider.
        def provider_fn(actor, prompt):
            return (f"[{type(actor).__name__}:{actor.model_id}] response to: {prompt[:50]}", 100)

        result = await run_moa(query, ps, agg, provider_fn=provider_fn)
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"result": str(result)}

    def cross_iter(self, iters, action):
        analyze_convergence, best_of_each, adoption_rate, step5_review = _load_cross_iter()
        if action == "convergence":
            return analyze_convergence(iters)
        if action == "best_of_each":
            return best_of_each(iters)
        if action == "adoption":
            return adoption_rate(iters)
        if action in ("step5", "review"):
            mode = iters[0].get("step5_mode", "skip") if iters else "skip"
            return step5_review(iters, mode=mode)
        raise ValueError(
            f"unknown action: {action}, expected one of convergence/best_of_each/adoption/step5"
        )

    def validate_config(self, proposers, aggregator):
        _P, _A, _run_moa, validate_moa = _load_moa_engine()
        ps = []
        for p in proposers:
            valid = {k: v for k, v in p.items() if k in _P.__dataclass_fields__}
            ps.append(_P(**valid))
        agg = None
        if aggregator:
            valid = {k: v for k, v in aggregator.items() if k in _A.__dataclass_fields__}
            agg = _A(**valid)
        errors = validate_moa(ps, agg)
        return {"errors": errors, "valid": len(errors) == 0}
