"""RoutingService — wraps router, channels, reference_router, cost_estimator.

Exposes:
  - route(query, model_hint, strategy)  # 路由选择最合适的模型
  - execute_chain(query, enabled)  # 沿 chain 执行
  - classify_error(error)  # 错误分类
  - chain_info()  # chain 拓扑信息
  - cost_estimate(input_tokens, output_tokens, channels)  # 多通道成本估算
  - reference_route(query, main_model, ref_model, strategy)  # 引用路由
"""

from __future__ import annotations

import logging

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


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
    # Audit fix: the real function is estimate_moa_cost (no `estimate_cost` here).
    from ..capability.cost_estimator import Channel, estimate_moa_cost

    return estimate_moa_cost, Channel


def _load_reference_router():
    # Audit fix: reference_router has no ReferenceRouter class. The real API is
    # ReferenceConfig + the async route_with_reference(query, config) pipeline.
    from ..capability.reference_router import (
        ReferenceConfig,
        RefStrategy,
        route_with_reference,
    )

    return ReferenceConfig, RefStrategy, route_with_reference


def _channel_result_to_dict(result) -> dict:
    """Serialize a channels.ChannelResult NamedTuple into a JSON-safe dict."""
    return {
        "channel": result.channel.value if hasattr(result.channel, "value") else str(result.channel),
        "success": result.success,
        "output": result.output,
        "latency_ms": result.latency_ms,
        "error": result.error,
    }


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
            description="沿 chain 顺序执行 (enabled 过滤通道)",
            func=self.execute_chain,
            is_async=True,
            input_required=["query", "enabled"],
        )
        self._methods["classify_error"] = ServiceMethod(
            name="classify_error",
            description="错误分类(auth / timeout / empty / cli)",
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
            description=(
                "Reference Router: 主模型 + 参考模型校准 (route_with_reference). "
                "strategy: none/shadow/validate/veto; 返回 decision/similarity/agreement/calibration"
            ),
            func=self.reference_route,
            is_async=True,
            input_required=["query", "main_model", "ref_model"],
            input_optional=["strategy", "max_latency_ms", "cost_ratio_cap"],
        )

    def route(self, query, model_hint=None, strategy=None):
        # Audit fix: IntelligentRouter.route has no model_hint/strategy kwargs.
        # Map model_hint -> prefer_provider; strategy is advisory and noted only.
        get_router = _load_router()
        r = get_router()
        decision = r.route(query, prefer_provider=model_hint)
        out = decision.to_dict()
        if strategy:
            out["requested_strategy"] = strategy
        return out

    def chain_info(self):
        ChannelChain, _, _ = _load_channels()
        chain = ChannelChain()
        return {
            "channels": [c.name for c in chain.channels],
            "topology": [
                {
                    "name": c.name,
                    "type": c.channel_type.value,
                    "enabled": c.enabled,
                }
                for c in chain.channels
            ],
        }

    async def execute_chain(self, query, enabled):
        # Audit fix: honour `enabled`, await the async chain execution, and
        # serialize ChannelType/ChannelResult into JSON-safe values.
        ChannelChain, _, ChannelType = _load_channels()
        chain = ChannelChain()
        if isinstance(enabled, dict):
            for c in chain.channels:
                if c.name in enabled:
                    c.enabled = bool(enabled[c.name])
                elif c.channel_type.value in enabled:
                    c.enabled = bool(enabled[c.channel_type.value])
        elif isinstance(enabled, (list, tuple, set)):
            allowed = set(enabled)
            for c in chain.channels:
                c.enabled = c.name in allowed or c.channel_type.value in allowed
        # execute_safe never raises: on total failure it carries an error dict.
        out = await chain.execute_safe(query)
        serialized = {
            "channel": out["channel"].value if out.get("channel") is not None else None,
            "result": _channel_result_to_dict(out["result"]) if out.get("result") else None,
            "fallback_path": [ct.value for ct in out.get("fallback_path", [])],
            "attempts": [_channel_result_to_dict(a) for a in out.get("attempts", [])],
        }
        if out.get("error"):
            serialized["error"] = out["error"]
        return serialized

    def classify_error(self, error):
        _, classify_error, _ = _load_channels()
        # classify_error expects an exception instance; JSON callers pass the
        # error message string — wrap it so type+message classification works.
        exc = error if isinstance(error, BaseException) else Exception(str(error))
        category = classify_error(exc)
        return {"category": category, "error": str(error)}

    def cost_estimate(
        self, input_tokens, output_tokens, channels, include_fallback=True, format="report"
    ):
        # Audit fix: build real Channel objects (Channel has no from_dict) and
        # call estimate_moa_cost.
        estimate_moa_cost, Channel = _load_cost_estimator()
        channel_objs = []
        for ch in channels or []:
            if isinstance(ch, Channel):
                channel_objs.append(ch)
            elif isinstance(ch, dict):
                valid = {k: v for k, v in ch.items() if k in Channel.__dataclass_fields__}
                channel_objs.append(Channel(**valid))
            else:
                raise ValueError("each channel must be a dict")
        estimate = estimate_moa_cost(
            input_tokens=int(input_tokens),
            output_tokens=int(output_tokens),
            channels=channel_objs,
            include_fallback=bool(include_fallback),
        )
        out = estimate.to_dict()
        out["format"] = format
        return out

    async def reference_route(
        self,
        query,
        main_model,
        ref_model,
        strategy="shadow",
        max_latency_ms=5000,
        cost_ratio_cap=2.0,
    ):
        # Audit fix: drive the real async route_with_reference pipeline with a
        # ReferenceConfig (there is no ReferenceRouter class).
        ReferenceConfig, RefStrategy, route_with_reference = _load_reference_router()
        try:
            strat = RefStrategy(strategy)
        except ValueError as e:
            valid = [s.value for s in RefStrategy]
            raise ValueError(f"unknown strategy: {strategy!r}, expected one of {valid}") from e
        config = ReferenceConfig(
            main_model=str(main_model),
            ref_model=str(ref_model),
            strategy=strat,
            max_latency_ms=int(max_latency_ms),
            cost_ratio_cap=float(cost_ratio_cap),
        )
        result = await route_with_reference(query, config)
        out = result.to_dict()
        out["main_model"] = str(main_model)
        out["ref_model"] = str(ref_model)
        return out
