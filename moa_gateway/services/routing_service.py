"""RoutingService — wraps router, channels, reference_router, channels, cost_estimator.

Exposes:
  - route(query, model_hint, strategy)  # 路由选择最合适的模型
  - execute_chain(query, enabled)  # 沿 chain 执行
  - classify_error(error)  # 错误分类
  - chain_info()  # chain 拓扑信息
  - cost_estimate(input_tokens, output_tokens, channels)  # 多通道成本估算
  - reference_route(query, main_model, ref_model, strategy)  # 引用路由
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_router():
    from ..router import get_router

    return get_router


def _load_channels():
    from ..capability.channels import (
        ChannelChain,
        ChannelType,
        classify_error,
    )

    return ChannelChain, classify_error, ChannelType


def _load_cost_estimator():
    from ..capability.cost_estimator import estimate_cost

    return estimate_cost


def _load_reference_router():
    from ..capability.reference_router import ReferenceRouter

    return ReferenceRouter


class RoutingService(ServiceBase):
    name = "routing"
    description = "模型路由: complex→expensive / simple→cheap / reference_router / cost estimate"

    def _register_methods(self):
        self._methods["route"] = ServiceMethod(
            name="route",
            description="根据 query 复杂度路由到合适模型",
            func=self.route,
            input_required=["query"],
            input_optional=["model_hint", "strategy"],
        )
        self._methods["chain_info"] = ServiceMethod(
            name="chain_info",
            description="获取通道 chain 拓扑信息",
            func=self.chain_info,
        )
        self._methods["execute_chain"] = ServiceMethod(
            name="execute_chain",
            description="沿 chain 顺序执行",
            func=self.execute_chain,
            input_required=["query", "enabled"],
        )
        self._methods["classify_error"] = ServiceMethod(
            name="classify_error",
            description="错误分类(rate_limit / auth / quota / transient)",
            func=self.classify_error,
            input_required=["error"],
        )
        self._methods["cost_estimate"] = ServiceMethod(
            name="cost_estimate",
            description="多通道成本估算",
            func=self.cost_estimate,
            input_required=["input_tokens", "output_tokens", "channels"],
            input_optional=["include_fallback", "format"],
        )
        self._methods["reference_route"] = ServiceMethod(
            name="reference_route",
            description="Reference Router: 影子模式/对比",
            func=self.reference_route,
            input_required=["query", "main_model", "ref_model"],
            input_optional=["strategy", "max_latency_ms", "cost_ratio_cap"],
        )

    def route(self, query, model_hint=None, strategy=None):
        get_router = _load_router()
        r = get_router()
        return r.route(query, model_hint=model_hint, strategy=strategy)

    def chain_info(self):
        ChannelChain, _, _ = _load_channels()
        chain = ChannelChain()
        return {
            "channels": [c.name for c in chain.channels],
            "topology": [{"name": c.name, "type": str(c.channel_type)} for c in chain.channels],
        }

    def execute_chain(self, query, enabled):
        ChannelChain, _, _ = _load_channels()
        chain = ChannelChain()
        return chain.execute(query, enabled=enabled)

    def classify_error(self, error):
        _, classify_error, _ = _load_channels()
        return classify_error(error)

    def cost_estimate(
        self, input_tokens, output_tokens, channels, include_fallback=True, format="report"
    ):
        estimate_cost = _load_cost_estimator()
        return estimate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            channels=channels,
            include_fallback=include_fallback,
            format=format,
        )

    def reference_route(
        self,
        query,
        main_model,
        ref_model,
        strategy="shadow",
        max_latency_ms=5000,
        cost_ratio_cap=2.0,
    ):
        ReferenceRouter = _load_reference_router()
        rr = ReferenceRouter(
            main_model=main_model,
            ref_model=ref_model,
            strategy=strategy,
            max_latency_ms=max_latency_ms,
            cost_ratio_cap=cost_ratio_cap,
        )
        return {
            "strategy": strategy,
            "main": main_model,
            "ref": ref_model,
            "decision": rr.decide(query),
        }
