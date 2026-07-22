"""moa_gateway.router — 智能路由器
根据任务复杂度 / 成本 / 偏好选择合适的模型或模型组合。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .model_pool import ModelEndpoint, ModelPool, ModelTier, get_model_pool

logger = logging.getLogger(__name__)


class ComplexityLevel(str, Enum):
    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    EXPERT = "expert"

    @property
    def rank(self) -> int:
        return ["trivial", "simple", "medium", "complex", "expert"].index(self.value)


@dataclass
class RoutingDecision:
    complexity: ComplexityLevel
    tier: ModelTier
    primary: ModelEndpoint | None
    fallback_chain: list[ModelEndpoint]
    estimated_cost: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "complexity": self.complexity.value,
            "tier": self.tier.value,
            "primary": self.primary.id if self.primary else None,
            "fallback_chain": [e.id for e in self.fallback_chain],
            "estimated_cost": round(self.estimated_cost, 6),
            "reason": self.reason,
        }


class IntelligentRouter:
    """智能路由器:
    - 多维度复杂度评估(长度 + 关键词 + 技术栈 + 上下文)
    - 成本预估
    - 模型选择 + Fallback 链
    """

    # 问题类型识别(权重)。中英文都列,统一 substring 匹配
    QUESTION_PATTERNS = {
        # 英文
        "why": 20,
        "how": 25,
        "what": 10,
        "which": 10,
        "compare": 30,
        "analyze": 35,
        "design": 40,
        "implement": 35,
        "debug": 30,
        "architecture": 45,
        "strategy": 40,
        "optimize": 35,
        "review": 30,
        "evaluate": 30,
        "refactor": 30,
        "explain": 15,
        "summarize": 12,
        "translate": 8,
        "list": 8,
        # 中文
        "为什么": 20,
        "怎么": 22,
        "如何": 22,
        "什么": 8,
        "哪个": 10,
        "比较": 28,
        "对比": 28,
        "分析": 35,
        "设计": 38,
        "实现": 32,
        "调试": 28,
        "架构": 45,
        "策略": 38,
        "优化": 32,
        "评估": 28,
        "重构": 28,
        "解释": 12,
        "总结": 10,
        "翻译": 6,
        "列举": 6,
        "权衡": 30,
        "取舍": 30,
        "规划": 25,
        "排查": 28,
        "审查": 28,
        "评审": 28,
        "选型": 30,
        "落地": 22,
        "改造": 25,
        "迁移": 25,
    }

    TECH_KEYWORDS = [
        # 英文
        "architecture",
        "system design",
        "distributed",
        "concurrency",
        "security",
        "authentication",
        "database",
        "scalability",
        "kubernetes",
        "microservice",
        "devops",
        "infrastructure",
        "machine learning",
        "neural network",
        "algorithm",
        "compiler",
        "kernel",
        "protocol",
        "cryptography",
        "blockchain",
        "quantum",
        "distributed system",
        "consensus",
        "raft",
        "paxos",
        "kafka",
        "redis",
        "postgres",
        "mongodb",
        "graphql",
        "rest",
        "tla+",
        "formal verification",
        "proof",
        "theorem",
        "complexity",
        # 中文
        "分布式",
        "高并发",
        "微服务",
        "数据库",
        "架构",
        "安全",
        "认证",
        "授权",
        "可扩展",
        "可伸缩",
        "区块链",
        "加密",
        "算法",
        "编译器",
        "内核",
        "协议",
        "消息队列",
        "缓存",
        "事务",
        "索引",
        "锁",
        "并发",
        "性能",
        "延迟",
        "吞吐",
        "sql",
        "nosql",
        "图数据库",
        "时序",
        "日志",
        "监控",
        "rpc",
        "http",
        "tcp",
        "udp",
        "tls",
        "ssl",
        "jwt",
        "oauth",
        "容器",
        "服务网格",
        "网关",
        "负载均衡",
        "熔断",
        "限流",
    ]

    # 多步推理特征
    MULTI_STEP_INDICATORS = [
        "step by step",
        "first",
        "then",
        "finally",
        "and then",
        "compare and contrast",
        "tradeoff",
        "pros and cons",
        "1.",
        "2.",
        "3.",
        # 中文
        "首先",
        "然后",
        "接着",
        "最后",
        "第一步",
        "第二步",
        "第三步",
        "对比",
        "权衡",
        "取舍",
        "优缺点",
        "优劣",
        "区别",
        "差异",
    ]

    def __init__(
        self,
        model_pool: ModelPool | None = None,
        thresholds: dict[str, int] | None = None,
        tier_mapping: dict[str, str] | None = None,
        max_cost_per_request: float = 1.0,
    ):
        self.pool = model_pool or get_model_pool()
        self.thresholds = thresholds or {
            "trivial_length": 10,
            "simple_length": 50,
            "medium_length": 200,
            "complex_length": 500,
        }
        self.tier_mapping = tier_mapping or {
            "trivial": "free",
            "simple": "lite",
            "medium": "standard",
            "complex": "premium",
            "expert": "flagship",
        }
        self.max_cost_per_request = max_cost_per_request

    def evaluate_complexity(self, query: str, context: list[dict] | None = None) -> ComplexityLevel:
        """多维度复杂度评估"""
        score = 0
        ql = (query or "").lower().strip()
        qlen = len(ql)

        # 维度1:文本长度
        if qlen < self.thresholds.get("trivial_length", 10):
            score += 5
        elif qlen < self.thresholds.get("simple_length", 50):
            score += 15
        elif qlen < self.thresholds.get("medium_length", 200):
            score += 30
        elif qlen < self.thresholds.get("complex_length", 500):
            score += 50
        else:
            score += 70

        # 维度2:问题类型(中英文都支持,substr 匹配)
        for pattern, weight in self.QUESTION_PATTERNS.items():
            if pattern in ql:
                score += weight

        # 维度3:技术关键词
        tech_hits = sum(1 for kw in self.TECH_KEYWORDS if kw in ql)
        score += min(tech_hits, 5) * 5

        # 维度4:多步推理
        multi_hits = sum(1 for kw in self.MULTI_STEP_INDICATORS if kw in ql)
        if multi_hits >= 2:
            score += 15

        # 维度5:上下文长度(对话历史)
        if context:
            ctx_len = sum(len(str(m.get("content", ""))) for m in context)
            if ctx_len > 2000:
                score += 20
            elif ctx_len > 500:
                score += 10

        # 维度6:代码块
        if "```" in ql or "def " in ql or "class " in ql:
            score += 10

        # 维度7:多语种混合(往往是复杂任务)
        if re.search(r"[一-鿿]", ql) and re.search(r"[a-zA-Z]", ql):
            score += 5

        # 映射到等级
        if score < 20:
            return ComplexityLevel.TRIVIAL
        elif score < 45:
            return ComplexityLevel.SIMPLE
        elif score < 70:
            return ComplexityLevel.MEDIUM
        elif score < 100:
            return ComplexityLevel.COMPLEX
        else:
            return ComplexityLevel.EXPERT

    def estimate_cost(
        self, query: str, ep: ModelEndpoint, estimated_output_tokens: int = 600
    ) -> float:
        """估算调用成本(USD)"""
        est_input = max(1, len(query) // 3)  # 粗略
        return (est_input / 1000.0) * ep.config.cost_per_1k_input + (
            estimated_output_tokens / 1000.0
        ) * ep.config.cost_per_1k_output

    def route(
        self,
        query: str,
        context: list[dict] | None = None,
        prefer_provider: str | None = None,
        max_cost: float | None = None,
        require_tier: ModelTier | None = None,
    ) -> RoutingDecision:
        """路由决策"""
        complexity = self.evaluate_complexity(query, context)
        tier_str = self.tier_mapping.get(complexity.value, "standard")
        tier = ModelTier(tier_str)
        if require_tier:
            tier = require_tier

        primary = self.pool.select_one(tier, prefer_provider=prefer_provider)
        if not primary:
            # 降级:低一级(用 ModelTier.previous 替代硬编码)
            for delta in range(1, 5):
                lower = tier.previous(delta)
                if lower == tier:
                    break
                primary = self.pool.select_one(lower, prefer_provider=prefer_provider)
                if primary:
                    tier = lower
                    break
        if not primary:
            return RoutingDecision(
                complexity=complexity,
                tier=tier,
                primary=None,
                fallback_chain=[],
                estimated_cost=0.0,
                reason="no available model",
            )

        cost_cap = max_cost if max_cost is not None else self.max_cost_per_request
        if cost_cap and self.estimate_cost(query, primary) > cost_cap:
            # 降级找便宜的
            for delta in range(1, 5):
                lower = tier.previous(delta)
                if lower == tier:
                    break
                cand = self.pool.select_one(lower, prefer_provider=prefer_provider)
                if cand and self.estimate_cost(query, cand) <= cost_cap:
                    primary = cand
                    tier = lower
                    break

        fallback = self.pool.get_fallback_chain(primary.id, 3)
        est = self.estimate_cost(query, primary)
        return RoutingDecision(
            complexity=complexity,
            tier=tier,
            primary=primary,
            fallback_chain=fallback,
            estimated_cost=est,
            reason=f"complexity={complexity.value}, tier={tier.value}, model={primary.id}",
        )

    def route_for_moa(
        self,
        query: str,
        context: list[dict] | None = None,
        preset: str | None = None,
        reference_count: int = 4,
        aggregator_tier: ModelTier = ModelTier.PREMIUM,
    ) -> tuple[list[ModelEndpoint], ModelEndpoint | None]:
        """MoA 路由:返回 (参考模型列表, 聚合模型)"""
        # 先评估复杂度
        complexity = self.evaluate_complexity(query, context)
        # 简单任务 -> 单模型(但 MoA 显式要 ≥2 个 ref 时不简化)
        if complexity == ComplexityLevel.TRIVIAL and not preset and reference_count <= 1:
            d = self.route(query, context)
            return ([d.primary] if d.primary else [], d.primary)

        # 选参考模型(分散 provider)
        tier = ModelTier(self.tier_mapping.get(complexity.value, "standard"))
        refs = self.pool.select_many(tier, reference_count, prefer_diversity=True)
        if len(refs) < reference_count:
            # 数量不够时,从高一级补(用 ModelTier.next 替代硬编码)
            for delta in range(1, 5):
                up = tier.next(delta)
                if up == tier:  # 已经到顶
                    break
                extras = self.pool.select_many(
                    up,
                    reference_count - len(refs),
                    prefer_diversity=True,
                    exclude_ids=[e.id for e in refs],
                )
                refs.extend(extras)
                if len(refs) >= reference_count:
                    break

        # 选聚合模型(取最高 tier 中最健康的)
        aggregator = self.pool.select_one(aggregator_tier)
        if not aggregator:
            for delta in range(1, 5):
                up = aggregator_tier.next(delta)
                if up == aggregator_tier:
                    break
                aggregator = self.pool.select_one(up)
                if aggregator:
                    break
        if not aggregator and refs:
            aggregator = refs[0]

        return refs, aggregator


# 单例
_router: IntelligentRouter | None = None


def get_router() -> IntelligentRouter:
    global _router
    if _router is None:
        from .config import get_settings

        s = get_settings()
        _router = IntelligentRouter(
            thresholds=s.routing.thresholds,
            tier_mapping=s.routing.tier_mapping,
            max_cost_per_request=s.routing.max_cost_per_request,
        )
    return _router
