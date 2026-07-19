"""QuotaService — wraps rate_quota, per_provider_rl, token_bucket, request_dedup, rate_quota, self_heal, tier_recalibrate, tier_promo, provider_health, consumption_intel.

Exposes:
  - check_quota(windows, requested, burn_rate)
  - record_quota(windows, tokens, at)
  - per_provider_action(action, provider, limits, concurrent, request_count, cooldown_seconds)
  - try_consume(key, tokens, capacity, refill_rate)
  - token_bucket_state()
  - cleanup_token_bucket()
  - dedup_check(method, path, body, source)
  - dedup_record(method, path, body, source, response)
  - dedup_stats()
  - self_heal_record_failure(endpoints, endpoint_id, at, reason)
  - self_heal_record_success(endpoints, endpoint_id, at)
  - self_heal_promote(endpoints, endpoint_id, reason, at)
  - self_heal_demote(endpoints, endpoint_id, reason, at)
  - self_heal_auto_balance(endpoints, at)
  - self_heal_check_recovery(endpoints, endpoint_id, at)
  - tier_recalibrate(tiers, ...)
  - tier_promo_classify(evidence, tier_1, tier_2, tier_3, tier_4, confidence_threshold)
  - tier_promo_compute(count, confidence)
  - tier_promo_can_spawn(parent_id, allowed_children, child_id)
  - tier_promo_cohabitation(parent_a, children_a, parent_b, children_b)
  - provider_health_aggregate(providers, prefer_tier)
  - consumption_intel(context, endpoints)
  - should_rebalance(stats, ...)
  - cost_estimate_alert / cost_estimate
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ServiceBase, ServiceMethod, service_method


def _load_rate_quota():
    from ..capability.rate_quota import (
        check_quota, record_usage, QuotaWindow, QuotaState,
    )
    return check_quota, record_usage, QuotaWindow, QuotaState


def _load_per_provider():
    from ..capability.per_provider_rl import (
        check_limit, record_request, mark_429, get_status,
    )
    return check_limit, record_request, mark_429, get_status


def _load_token_bucket():
    from ..capability.token_bucket import try_consume, get_state, cleanup
    return try_consume, get_state, cleanup


def _load_request_dedup():
    from ..capability.request_dedup import check, record, stats
    return check, record, stats


def _load_self_heal():
    from ..capability.self_heal import (
        record_failure, record_success, promote, demote,
        auto_balance, check_recovery,
    )
    return record_failure, record_success, promote, demote, auto_balance, check_recovery


def _load_tier_recalibrate():
    from ..capability.tier_recalibrate import recalibrate
    return recalibrate


def _load_tier_promo():
    from ..capability.tier_promo import (
        classify, compute, can_spawn, cohabitation,
    )
    return classify, compute, can_spawn, cohabitation


def _load_provider_health():
    from ..capability.provider_health import (
        compute_score, aggregate_scores, recommend, rank_providers,
    )
    return compute_score, aggregate_scores, recommend, rank_providers


def _load_consumption_intel():
    from ..capability.consumption_intel import analyze
    return analyze


def _load_consensus():
    from ..capability.consensus import should_rebalance
    return should_rebalance


def _load_cost_estimator():
    from ..capability.cost_estimator import (
        estimate_moa_cost, dry_run_preset, compare_presets, format_report,
    )
    return estimate_moa_cost, dry_run_preset, compare_presets, format_report


class QuotaService(ServiceBase):
    name = "quota"
    description = "Quota / rate limit / dedup / self-heal / tier / provider-health"

    def _register_methods(self):
        # rate quota
        self._methods["check_quota"] = ServiceMethod(
            name="check_quota", description="检查 quota (token-burn rate vs 5h window)",
            func=self.check_quota,
            input_required=["windows", "requested"],
        )
        self._methods["record_quota"] = ServiceMethod(
            name="record_quota", description="记录 quota usage",
            func=self.record_quota,
            input_required=["windows", "tokens"],
        )
        # per provider
        self._methods["per_provider_action"] = ServiceMethod(
            name="per_provider_action", description="per-provider rate limit (check/record/mark_429/status)",
            func=self.per_provider_action,
            input_required=["action", "provider"],
            input_optional=["limits", "concurrent", "request_count", "cooldown_seconds", "max_requests_per_minute", "max_inputs_per_minute", "max_concurrent"],
        )
        # token bucket
        self._methods["try_consume"] = ServiceMethod(
            name="try_consume", description="token bucket 消费",
            func=self.try_consume,
            input_required=["key", "tokens"],
            input_optional=["capacity", "refill_rate"],
        )
        self._methods["token_bucket_state"] = ServiceMethod(
            name="token_bucket_state", description="所有 token bucket 状态",
            func=self.token_bucket_state,
        )
        self._methods["token_bucket_cleanup"] = ServiceMethod(
            name="token_bucket_cleanup", description="清理过期 token bucket",
            func=self.token_bucket_cleanup,
        )
        # dedup
        self._methods["dedup_check"] = ServiceMethod(
            name="dedup_check", description="检查 request 是否重复",
            func=self.dedup_check,
            input_required=["method", "path", "body"],
        )
        self._methods["dedup_record"] = ServiceMethod(
            name="dedup_record", description="记录 request 响应",
            func=self.dedup_record,
            input_required=["method", "path", "body"],
        )
        self._methods["dedup_stats"] = ServiceMethod(
            name="dedup_stats", description="dedup 统计",
            func=self.dedup_stats,
        )
        # self-heal
        self._methods["self_heal_record_failure"] = ServiceMethod(
            name="self_heal_record_failure", description="记录端点失败",
            func=self.self_heal_record_failure,
            input_required=["endpoints", "endpoint_id"],
        )
        self._methods["self_heal_record_success"] = ServiceMethod(
            name="self_heal_record_success", description="记录端点成功",
            func=self.self_heal_record_success,
            input_required=["endpoints", "endpoint_id"],
        )
        self._methods["self_heal_promote"] = ServiceMethod(
            name="self_heal_promote", description="promote 端点 tier",
            func=self.self_heal_promote,
            input_required=["endpoints", "endpoint_id", "reason"],
        )
        self._methods["self_heal_demote"] = ServiceMethod(
            name="self_heal_demote", description="demote 端点 tier",
            func=self.self_heal_demote,
            input_required=["endpoints", "endpoint_id", "reason"],
        )
        self._methods["self_heal_auto_balance"] = ServiceMethod(
            name="self_heal_auto_balance", description="自动 rebalance 端点 tier",
            func=self.self_heal_auto_balance,
            input_required=["endpoints"],
        )
        self._methods["self_heal_check_recovery"] = ServiceMethod(
            name="self_heal_check_recovery", description="检查端点是否已恢复",
            func=self.self_heal_check_recovery,
            input_required=["endpoints", "endpoint_id"],
        )
        # tier recalibrate
        self._methods["tier_recalibrate"] = ServiceMethod(
            name="tier_recalibrate", description="tier 参数重校准",
            func=self.tier_recalibrate,
            input_required=["tiers"],
        )
        # tier promo
        self._methods["tier_promo_classify"] = ServiceMethod(
            name="tier_promo_classify", description="tier 提升分类(基于 evidence 事件)",
            func=self.tier_promo_classify,
            input_required=["evidence"],
            input_optional=["tier_1", "tier_2", "tier_3", "tier_4", "confidence_threshold"],
        )
        self._methods["tier_promo_compute"] = ServiceMethod(
            name="tier_promo_compute", description="tier 提升计算",
            func=self.tier_promo_compute,
            input_required=["count"],
            input_optional=["confidence"],
        )
        self._methods["tier_promo_can_spawn"] = ServiceMethod(
            name="tier_promo_can_spawn", description="检查 parent 是否可 spawn child",
            func=self.tier_promo_can_spawn,
            input_required=["parent_id", "allowed_children", "child_id"],
        )
        self._methods["tier_promo_cohabitation"] = ServiceMethod(
            name="tier_promo_cohabitation", description="检查两个 parent 是否可同居",
            func=self.tier_promo_cohabitation,
            input_required=["parent_a", "children_a", "parent_b", "children_b"],
        )
        # provider health
        self._methods["provider_health_aggregate"] = ServiceMethod(
            name="provider_health_aggregate", description="聚合 provider 健康度",
            func=self.provider_health_aggregate,
            input_required=["providers"],
        )
        # consumption intel
        self._methods["consumption_intel"] = ServiceMethod(
            name="consumption_intel", description="消费智能分析",
            func=self.consumption_intel,
            input_required=["context", "endpoints"],
        )
        # should rebalance
        self._methods["should_rebalance"] = ServiceMethod(
            name="should_rebalance", description="检查是否需要 rebalance",
            func=self.should_rebalance,
            input_required=["stats"],
        )
        # cost estimate
        self._methods["cost_estimate"] = ServiceMethod(
            name="cost_estimate", description="多通道成本估算",
            func=self.cost_estimate,
            input_required=["input_tokens", "output_tokens", "channels"],
            input_optional=["include_fallback", "format"],
        )

    # rate quota
    def check_quota(self, windows, requested, burn_rate_per_hour=None):
        check_quota, _, _, _ = _load_rate_quota()
        wins = [w for w in windows if isinstance(w, dict)]
        if not wins:
            raise ValueError("windows must be non-empty list")
        from ..capability.rate_quota import QuotaWindow
        qwins = {w["name"]: QuotaWindow(**{k: v for k, v in w.items() if k in QuotaWindow.__dataclass_fields__})
                 for w in wins}
        return check_quota(qwins, requested, burn_rate_per_hour=burn_rate_per_hour or 0)

    def record_quota(self, windows, tokens, at=None):
        _, record_usage, _, QuotaState = _load_rate_quota()
        from ..capability.rate_quota import QuotaWindow
        qwins = {w["name"]: QuotaWindow(**{k: v for k, v in w.items() if k in QuotaWindow.__dataclass_fields__})
                 for w in windows}
        state = QuotaState(windows=qwins, last_updated=at or 0.0)
        record_usage(state, tokens, at=at)
        return {"windows": {name: w.__dict__ for name, w in state.windows.items()},
                "last_updated": state.last_updated}

    # per provider
    def per_provider_action(self, action, provider, limits=None, concurrent=0,
                            request_count=0, cooldown_seconds=60.0,
                            max_requests_per_minute=60, max_inputs_per_minute=1000,
                            max_concurrent=5):
        check_limit, record_request, mark_429, get_status = _load_per_provider()
        if not limits:
            limits = {provider: {"provider": provider,
                                 "max_requests_per_minute": max_requests_per_minute,
                                 "max_inputs_per_minute": max_inputs_per_minute,
                                 "max_concurrent": max_concurrent}}
        if action == "check":
            return check_limit(limits, provider, concurrent=concurrent)
        if action == "record":
            return record_request(limits, provider, request_count=request_count)
        if action == "mark_429":
            return mark_429(limits, provider, cooldown_seconds=cooldown_seconds)
        if action == "status":
            return get_status(limits, provider)
        raise ValueError(f"unknown action: {action}")

    # token bucket
    def try_consume(self, key, tokens, capacity=60, refill_rate=1.0):
        try_consume, _, _ = _load_token_bucket()
        return try_consume(key=key, tokens=tokens, capacity=capacity, refill_rate=refill_rate)

    def token_bucket_state(self, **kwargs):
        _, get_state, _ = _load_token_bucket()
        return get_state()

    def token_bucket_cleanup(self):
        _, _, cleanup = _load_token_bucket()
        return {"cleaned": cleanup()}

    # dedup
    def dedup_check(self, method, path, body, source=""):
        check, _, _ = _load_request_dedup()
        return check(method=method, path=path, body=body, source=source)

    def dedup_record(self, method, path, body, response, source=""):
        _, record, _ = _load_request_dedup()
        return record(method=method, path=path, body=body, response=response, source=source)

    def dedup_stats(self):
        _, _, stats = _load_request_dedup()
        return stats()

    # self heal
    def self_heal_record_failure(self, endpoints, endpoint_id, at=0.0):
        rf, *_ = _load_self_heal()
        return rf(endpoints=endpoints, endpoint_id=endpoint_id, at=at)

    def self_heal_record_success(self, endpoints, endpoint_id, at=0.0):
        _, rs, *_ = _load_self_heal()
        return rs(endpoints=endpoints, endpoint_id=endpoint_id, at=at)

    def self_heal_promote(self, endpoints, endpoint_id, reason="", at=0.0):
        *_, promote, _, _ = _load_self_heal()
        return promote(endpoints=endpoints, endpoint_id=endpoint_id, reason=reason, at=at)

    def self_heal_demote(self, endpoints, endpoint_id, reason="", at=0.0):
        *_, demote, _ = _load_self_heal()
        return demote(endpoints=endpoints, endpoint_id=endpoint_id, reason=reason, at=at)

    def self_heal_auto_balance(self, endpoints, at=0.0):
        all_fns = _load_self_heal()
        return all_fns[4](endpoints=endpoints, at=at)

    def self_heal_check_recovery(self, endpoints, endpoint_id, at=0.0):
        all_fns = _load_self_heal()
        return all_fns[5](endpoints=endpoints, endpoint_id=endpoint_id, at=at)

    # tier recalibrate
    def tier_recalibrate(self, tiers):
        recalibrate = _load_tier_recalibrate()
        return recalibrate(tiers=tiers)

    # tier promo
    def tier_promo_classify(self, evidence, tier_1=1, tier_2=3, tier_3=5, tier_4=10, confidence_threshold=0.7):
        classify, *_ = _load_tier_promo()
        return classify(evidence=evidence, tier_1=tier_1, tier_2=tier_2,
                        tier_3=tier_3, tier_4=tier_4,
                        confidence_threshold=confidence_threshold)

    def tier_promo_compute(self, count, confidence=0.5):
        all_fns = _load_tier_promo()
        return all_fns[1](count=count, confidence=confidence)

    def tier_promo_can_spawn(self, parent_id, allowed_children, child_id):
        all_fns = _load_tier_promo()
        return all_fns[2](parent_id=parent_id, allowed_children=allowed_children, child_id=child_id)

    def tier_promo_cohabitation(self, parent_a, children_a, parent_b, children_b):
        all_fns = _load_tier_promo()
        return all_fns[3](parent_a=parent_a, children_a=children_a,
                          parent_b=parent_b, children_b=children_b)

    # provider health
    def provider_health_aggregate(self, providers, prefer_tier=None):
        compute_score, aggregate_scores, recommend, rank_providers = _load_provider_health()
        from ..capability.provider_health import HealthMetrics
        scores = []
        for p in providers:
            if isinstance(p, dict):
                # Filter unknown fields, support both 'breaker_open' and 'circuit_open'
                valid = {k: v for k, v in p.items() if k in HealthMetrics.__dataclass_fields__}
                m = HealthMetrics(**valid)
                scores.append(compute_score(m))
        agg = aggregate_scores(scores)
        rec = recommend(agg, prefer_tier=prefer_tier) if prefer_tier else None
        return {
            "scores": {k: {"score": v.score, "tier": v.tier, "reasons": v.reasons}
                        for k, v in agg.items()},
            "ranked": [{"provider": p, "score": s} for p, s in rank_providers(agg)],
            "recommended": rec,
        }

    # consumption intel
    def consumption_intel(self, context, endpoints):
        analyze = _load_consumption_intel()
        return analyze(context=context, endpoints=endpoints)

    # should rebalance
    def should_rebalance(self, stats, config=None):
        from ..capability.consensus import TierStat
        stats_objs = {k: TierStat(**v) if isinstance(v, dict) else v for k, v in stats.items()}
        # Inline implementation since we can't easily pass config through _load_consensus
        total = len(stats_objs)
        if total == 0:
            return {"should_rebalance": False}
        high = sum(1 for s in stats_objs.values() if s.success_count / max(s.total_calls, 1) > 0.95)
        low = sum(1 for s in stats_objs.values() if s.success_count / max(s.total_calls, 1) < 0.5)
        return {"should_rebalance": high >= 1 and low >= 1, "high_count": high, "low_count": low, "total": total}

    # cost estimate
    def cost_estimate(self, input_tokens, output_tokens, channels, include_fallback=True, preset_name="balanced", retry_factor=1.0):
        estimate_moa_cost, *_ = _load_cost_estimator()
        from ..capability.cost_estimator import Channel as CEChannel
        # Convert dict channels to Channel objects
        ch_objs = []
        for ch in channels:
            if isinstance(ch, dict):
                ch_objs.append(CEChannel(**{k: v for k, v in ch.items() if k in CEChannel.__dataclass_fields__}))
            else:
                ch_objs.append(ch)
        result = estimate_moa_cost(
            input_tokens=input_tokens, output_tokens=output_tokens,
            channels=ch_objs, preset_name=preset_name,
            include_fallback=include_fallback, retry_factor=retry_factor,
        )
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"result": str(result)}
