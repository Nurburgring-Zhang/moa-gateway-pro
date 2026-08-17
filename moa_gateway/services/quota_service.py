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

import logging

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


def _load_rate_quota():
    # Audit F31 fix: rate_quota exposes check_available/eta_exhaustion — there
    # is no `check_quota` symbol (the old import raised ImportError at call time).
    from ..capability.rate_quota import (
        QuotaState,
        QuotaWindow,
        check_available,
        eta_exhaustion,
        record_usage,
    )

    return check_available, eta_exhaustion, record_usage, QuotaWindow, QuotaState


# Audit fix: the previous loaders imported module-level functions that do not
# exist (the logic lives on classes). Use real class singletons instead so the
# service methods actually execute.
_per_provider_limiter = None
_token_buckets = None
_dedup_index = None


def _get_per_provider_limiter():
    global _per_provider_limiter
    if _per_provider_limiter is None:
        from ..capability.per_provider_rl import MultiProviderLimiter, make_default_limits

        _per_provider_limiter = MultiProviderLimiter(make_default_limits())
    return _per_provider_limiter


def _get_token_buckets():
    global _token_buckets
    if _token_buckets is None:
        from ..capability.token_bucket import MultiKeyTokenBucket

        _token_buckets = MultiKeyTokenBucket(default_capacity=60, default_refill_rate=1.0)
    return _token_buckets


def _get_dedup_index():
    global _dedup_index
    if _dedup_index is None:
        from ..capability.request_dedup import RequestDedupIndex

        _dedup_index = RequestDedupIndex(ttl_seconds=60, max_size=10000)
    return _dedup_index


def _load_self_heal():
    from ..capability.self_heal import (
        auto_balance,
        check_recovery,
        demote,
        promote,
        record_failure,
        record_success,
    )

    return record_failure, record_success, promote, demote, auto_balance, check_recovery


def _load_tier_recalibrate():
    from ..capability.tier_recalibrate import recalibrate

    return recalibrate


def _load_tier_promo():
    # Audit fix: tier_promo exports compute_tier / record_evidence /
    # classify_tier_from_evidence + SubAgentBoundary (can_spawn /
    # cohabitation_check are methods). The old loader imported
    # classify/compute/can_spawn/cohabitation which do not exist.
    from ..capability.tier_promo import (
        Evidence,
        PromotionConfig,
        PromotionState,
        SubAgentBoundary,
        classify_tier_from_evidence,
        compute_tier,
        promotion_state_to_dict,
        record_evidence,
    )

    return (
        compute_tier,
        record_evidence,
        classify_tier_from_evidence,
        Evidence,
        PromotionConfig,
        PromotionState,
        SubAgentBoundary,
        promotion_state_to_dict,
    )


def _load_provider_health():
    from ..capability.provider_health import (
        aggregate_scores,
        compute_score,
        rank_providers,
        recommend,
    )

    return compute_score, aggregate_scores, recommend, rank_providers


def _load_consumption_intel():
    # Audit fix: the real entry point is select_endpoint (no `analyze` exists).
    from ..capability.consumption_intel import (
        EndpointSpec,
        RequestContext,
        select_endpoint,
    )

    return select_endpoint, RequestContext, EndpointSpec


def _load_consensus():
    from ..capability.consensus import should_rebalance

    return should_rebalance


def _load_cost_estimator():
    from ..capability.cost_estimator import (
        compare_presets,
        dry_run_preset,
        estimate_moa_cost,
        format_report,
    )

    return estimate_moa_cost, dry_run_preset, compare_presets, format_report


def _build_heal_state(endpoints, endpoint_id=None):
    """Build a real HealState from an endpoint list (audit fix).

    ``endpoints`` may be a list of endpoint-id strings or dicts carrying an
    ``endpoint_id``/``id`` key. The optional ``endpoint_id`` target is always
    registered so record_success/record_failure/etc. can find it.
    """
    from ..capability.self_heal import HealState

    state = HealState()
    ids: list[str] = []
    if isinstance(endpoints, list):
        for e in endpoints:
            if isinstance(e, str):
                ids.append(e)
            elif isinstance(e, dict):
                ids.append(str(e.get("endpoint_id") or e.get("id") or ""))
    if endpoint_id:
        ids.append(str(endpoint_id))
    for eid in {i for i in ids if i}:
        state.add_endpoint(eid)
    return state


def _heal_result(result):
    """Serialize HealAction (or a list of them) into JSON-safe dicts."""

    def _one(action):
        if hasattr(action, "to_dict"):
            return action.to_dict()
        if hasattr(action, "__dict__"):
            return dict(action.__dict__)
        return action

    if isinstance(result, list):
        return [_one(a) for a in result]
    return _one(result)


class QuotaService(ServiceBase):
    name = "quota"
    description = "Quota / rate limit / dedup / self-heal / tier / provider-health"

    def _register_methods(self):
        # rate quota
        self._methods["check_quota"] = ServiceMethod(
            name="check_quota",
            description="检查 quota (token-burn rate vs 5h window)",
            func=self.check_quota,
            input_required=["windows", "requested"],
        )
        self._methods["record_quota"] = ServiceMethod(
            name="record_quota",
            description="记录 quota usage",
            func=self.record_quota,
            input_required=["windows", "tokens"],
        )
        # per provider
        self._methods["per_provider_action"] = ServiceMethod(
            name="per_provider_action",
            description="per-provider rate limit (check/record/mark_429/status)",
            func=self.per_provider_action,
            input_required=["action", "provider"],
            input_optional=[
                "limits",
                "concurrent",
                "request_count",
                "cooldown_seconds",
                "max_requests_per_minute",
                "max_inputs_per_minute",
                "max_concurrent",
            ],
        )
        # token bucket
        self._methods["try_consume"] = ServiceMethod(
            name="try_consume",
            description="token bucket 消费",
            func=self.try_consume,
            input_required=["key", "tokens"],
            input_optional=["capacity", "refill_rate"],
        )
        self._methods["token_bucket_state"] = ServiceMethod(
            name="token_bucket_state",
            description="所有 token bucket 状态",
            func=self.token_bucket_state,
        )
        self._methods["token_bucket_cleanup"] = ServiceMethod(
            name="token_bucket_cleanup",
            description="清理过期 token bucket",
            func=self.token_bucket_cleanup,
        )
        # dedup
        self._methods["dedup_check"] = ServiceMethod(
            name="dedup_check",
            description="检查 request 是否重复",
            func=self.dedup_check,
            input_required=["method", "path", "body"],
        )
        self._methods["dedup_record"] = ServiceMethod(
            name="dedup_record",
            description="记录 request 响应",
            func=self.dedup_record,
            input_required=["method", "path", "body"],
        )
        self._methods["dedup_stats"] = ServiceMethod(
            name="dedup_stats",
            description="dedup 统计",
            func=self.dedup_stats,
        )
        # self-heal
        self._methods["self_heal_record_failure"] = ServiceMethod(
            name="self_heal_record_failure",
            description="记录端点失败",
            func=self.self_heal_record_failure,
            input_required=["endpoints", "endpoint_id"],
        )
        self._methods["self_heal_record_success"] = ServiceMethod(
            name="self_heal_record_success",
            description="记录端点成功",
            func=self.self_heal_record_success,
            input_required=["endpoints", "endpoint_id"],
        )
        self._methods["self_heal_promote"] = ServiceMethod(
            name="self_heal_promote",
            description="promote 端点 tier",
            func=self.self_heal_promote,
            input_required=["endpoints", "endpoint_id", "reason"],
        )
        self._methods["self_heal_demote"] = ServiceMethod(
            name="self_heal_demote",
            description="demote 端点 tier",
            func=self.self_heal_demote,
            input_required=["endpoints", "endpoint_id", "reason"],
        )
        self._methods["self_heal_auto_balance"] = ServiceMethod(
            name="self_heal_auto_balance",
            description="自动 rebalance 端点 tier",
            func=self.self_heal_auto_balance,
            input_required=["endpoints"],
        )
        self._methods["self_heal_check_recovery"] = ServiceMethod(
            name="self_heal_check_recovery",
            description="检查端点是否已恢复",
            func=self.self_heal_check_recovery,
            input_required=["endpoints", "endpoint_id"],
        )
        # tier recalibrate
        self._methods["tier_recalibrate"] = ServiceMethod(
            name="tier_recalibrate",
            description="tier 参数重校准",
            func=self.tier_recalibrate,
            input_required=["tiers"],
        )
        # tier promo
        self._methods["tier_promo_classify"] = ServiceMethod(
            name="tier_promo_classify",
            description="tier 提升分类: 逐条 record_evidence 累加 evidence, 返回最终 PromotionState (tier/count/confidence)",
            func=self.tier_promo_classify,
            input_required=["evidence"],
            input_optional=["tier_1", "tier_2", "tier_3", "tier_4", "confidence_threshold"],
        )
        self._methods["tier_promo_compute"] = ServiceMethod(
            name="tier_promo_compute",
            description="tier 提升计算: compute_tier(evidence_count, confidence) — confidence 低于阈值时维持当前 tier",
            func=self.tier_promo_compute,
            input_required=["count"],
            input_optional=["confidence", "current_tier"],
        )
        self._methods["tier_promo_can_spawn"] = ServiceMethod(
            name="tier_promo_can_spawn",
            description="检查 parent 是否可 spawn child (SubAgentBoundary.can_spawn 白名单检查)",
            func=self.tier_promo_can_spawn,
            input_required=["parent_id", "allowed_children", "child_id"],
        )
        self._methods["tier_promo_cohabitation"] = ServiceMethod(
            name="tier_promo_cohabitation",
            description="检查两个 parent 边界是否兼容 (SubAgentBoundary.cohabitation_check: 同 parent → True, 跨 parent → False)",
            func=self.tier_promo_cohabitation,
            input_required=["parent_a", "children_a", "parent_b", "children_b"],
        )
        # provider health
        self._methods["provider_health_aggregate"] = ServiceMethod(
            name="provider_health_aggregate",
            description="聚合 provider 健康度",
            func=self.provider_health_aggregate,
            input_required=["providers"],
        )
        # consumption intel
        self._methods["consumption_intel"] = ServiceMethod(
            name="consumption_intel",
            description="消费智能分析",
            func=self.consumption_intel,
            input_required=["context", "endpoints"],
        )
        # should rebalance
        self._methods["should_rebalance"] = ServiceMethod(
            name="should_rebalance",
            description="检查是否需要 rebalance",
            func=self.should_rebalance,
            input_required=["stats"],
        )
        # cost estimate
        self._methods["cost_estimate"] = ServiceMethod(
            name="cost_estimate",
            description="多通道成本估算",
            func=self.cost_estimate,
            input_required=["input_tokens", "output_tokens", "channels"],
            input_optional=["include_fallback", "format"],
        )

    # rate quota
    def check_quota(self, windows, requested, burn_rate_per_hour=None):
        check_available, eta_exhaustion, _, QuotaWindow, QuotaState = _load_rate_quota()
        wins = [w for w in windows if isinstance(w, dict)]
        if not wins:
            raise ValueError("windows must be non-empty list")

        import time as _t

        qwins = {
            w["name"]: QuotaWindow(
                **{k: v for k, v in w.items() if k in QuotaWindow.__dataclass_fields__}
            )
            for w in wins
        }
        state = QuotaState(windows=qwins, last_updated=_t.time())
        ok, reason = check_available(state, requested)
        burn = burn_rate_per_hour or 0
        return {
            "available": ok,
            "reason": reason,
            "eta_hours": (
                {name: eta_exhaustion(state, float(burn), name) for name in qwins}
                if burn
                else {}
            ),
        }

    def record_quota(self, windows, tokens, at=None):
        _, _, record_usage, QuotaWindow, QuotaState = _load_rate_quota()

        qwins = {
            w["name"]: QuotaWindow(
                **{k: v for k, v in w.items() if k in QuotaWindow.__dataclass_fields__}
            )
            for w in windows
        }
        state = QuotaState(windows=qwins, last_updated=at or 0.0)
        record_usage(state, tokens, at=at)
        return {
            "windows": {name: w.__dict__ for name, w in state.windows.items()},
            "last_updated": state.last_updated,
        }

    # per provider
    def per_provider_action(
        self,
        action,
        provider,
        limits=None,
        concurrent=0,
        request_count=0,
        cooldown_seconds=60.0,
        max_requests_per_minute=60,
        max_inputs_per_minute=1000,
        max_concurrent=5,
    ):
        # Audit fix: drive the real MultiProviderLimiter (module-level functions
        # never existed).
        from ..capability.per_provider_rl import decision_to_dict

        limiter = _get_per_provider_limiter()
        if action == "check":
            return decision_to_dict(limiter.check(provider, concurrent_now=concurrent))
        if action == "record":
            limiter.record(provider, request_count=request_count)
            return {"recorded": True, "provider": provider, "request_count": request_count}
        if action == "mark_429":
            cooldown_until = limiter.mark_429(provider, duration_seconds=cooldown_seconds)
            return {"provider": provider, "cooldown_until": cooldown_until}
        if action == "status":
            snap = limiter.snapshot()
            return snap.get(provider, {"provider": provider})
        raise ValueError(f"unknown action: {action}")

    # token bucket
    def try_consume(self, key, tokens, capacity=60, refill_rate=1.0):
        # Audit fix: use the real MultiKeyTokenBucket. (capacity/refill_rate apply
        # when the bucket is lazily created for a new key.)
        buckets = _get_token_buckets()
        bucket = buckets.get_bucket(str(key))
        # honour caller-requested capacity for a freshly created bucket
        if getattr(bucket, "capacity", None) is not None and capacity:
            try:
                bucket.capacity = int(capacity)
            except (TypeError, ValueError) as e:
                logger.warning("try_consume: invalid capacity %r, keeping default: %s", capacity, e)
        return {"allowed": bool(buckets.try_consume(str(key), int(tokens))), "key": str(key)}

    def token_bucket_state(self, **kwargs):
        return _get_token_buckets().all_states()

    def token_bucket_cleanup(self):
        return {"cleaned": _get_token_buckets().cleanup_inactive()}

    # dedup
    def dedup_check(self, method, path, body, source=""):
        idx = _get_dedup_index()
        entry = idx.check(method, path, body, source=source)
        if entry is None:
            return {"duplicate": False}
        return {"duplicate": True, "has_response": getattr(entry, "response", None) is not None}

    def dedup_record(self, method, path, body, response, source=""):
        idx = _get_dedup_index()
        idx.record(method, path, body, source=source, response=response)
        return {"recorded": True}

    def dedup_stats(self):
        return _get_dedup_index().stats()

    # self heal
    # Audit fix: the underlying self_heal functions take a HealState (``state=``),
    # not an ``endpoints=`` kwarg. Build a real HealState from the provided
    # endpoint list and call them correctly (was: guaranteed TypeError).
    def self_heal_record_failure(self, endpoints, endpoint_id, at=0.0):
        rf, *_ = _load_self_heal()
        state = _build_heal_state(endpoints, endpoint_id)
        return _heal_result(rf(state=state, endpoint_id=endpoint_id, at=at or None))

    def self_heal_record_success(self, endpoints, endpoint_id, at=0.0):
        _, rs, *_ = _load_self_heal()
        state = _build_heal_state(endpoints, endpoint_id)
        return _heal_result(rs(state=state, endpoint_id=endpoint_id, at=at or None))

    def self_heal_promote(self, endpoints, endpoint_id, reason="", at=0.0):
        # _load_self_heal order: (record_failure, record_success, promote,
        # demote, auto_balance, check_recovery). promote is index 2.
        promote = _load_self_heal()[2]
        state = _build_heal_state(endpoints, endpoint_id)
        return _heal_result(promote(state=state, endpoint_id=endpoint_id, reason=reason, at=at or None))

    def self_heal_demote(self, endpoints, endpoint_id, reason="", at=0.0):
        # demote is index 3 in the _load_self_heal tuple.
        demote = _load_self_heal()[3]
        state = _build_heal_state(endpoints, endpoint_id)
        return _heal_result(demote(state=state, endpoint_id=endpoint_id, reason=reason, at=at or None))

    def self_heal_auto_balance(self, endpoints, at=0.0):
        all_fns = _load_self_heal()
        if len(all_fns) <= 4:
            raise RuntimeError(f"self_heal module expected 5+ functions, got {len(all_fns)}")
        state = _build_heal_state(endpoints)
        return _heal_result(all_fns[4](state=state, at=at or None))

    def self_heal_check_recovery(self, endpoints, endpoint_id, at=0.0):
        all_fns = _load_self_heal()
        if len(all_fns) <= 5:
            raise RuntimeError(f"self_heal module expected 6+ functions, got {len(all_fns)}")
        state = _build_heal_state(endpoints, endpoint_id)
        return _heal_result(all_fns[5](state=state, endpoint_id=endpoint_id, at=at or None))

    # tier recalibrate
    def tier_recalibrate(self, tiers):
        # Audit fix: recalibrate takes a list[TierMetrics] positional
        # (metrics_list), not a tiers= kwarg.
        from dataclasses import asdict

        from ..capability.tier_recalibrate import TierLabel, TierMetrics, should_retrain

        recalibrate = _load_tier_recalibrate()
        if not isinstance(tiers, list) or not tiers:
            raise ValueError("tiers must be a non-empty list of tier metric dicts")
        metrics_list = []
        for t in tiers:
            if not isinstance(t, dict):
                raise ValueError("each tier entry must be a dict")
            fields = dict(t)
            fields["tier"] = TierLabel(str(fields.get("tier", "standard")))
            valid = {k: v for k, v in fields.items() if k in TierMetrics.__dataclass_fields__}
            metrics_list.append(TierMetrics(**valid))
        plans = recalibrate(metrics_list)
        return {
            "plans": [asdict(p) for p in plans],
            "should_retrain": should_retrain(plans),
            "tiers_evaluated": len(metrics_list),
        }

    # tier promo
    def tier_promo_classify(
        self, evidence, tier_1=1, tier_2=3, tier_3=5, tier_4=10, confidence_threshold=0.7
    ):
        # Audit fix: drive classify via the real record_evidence pipeline
        # (no module-level `classify` exists).
        (
            _compute,
            record_evidence,
            _classify_from_evidence,
            Evidence,
            PromotionConfig,
            PromotionState,
            _Boundary,
            state_to_dict,
        ) = _load_tier_promo()
        if not isinstance(evidence, list):
            raise ValueError("evidence must be a list of evidence dicts")
        config = PromotionConfig(
            tier_1_threshold=int(tier_1),
            tier_2_threshold=int(tier_2),
            tier_3_threshold=int(tier_3),
            tier_4_threshold=int(tier_4),
            confidence_threshold=float(confidence_threshold),
        )
        ev_objs = []
        for e in evidence:
            if isinstance(e, Evidence):
                ev_objs.append(e)
            elif isinstance(e, dict):
                ev_objs.append(
                    Evidence(
                        event_type=str(e.get("event_type", "")),
                        timestamp=float(e.get("timestamp", 0.0)),
                        weight=float(e.get("weight", 1.0)),
                    )
                )
            else:
                raise ValueError("each evidence entry must be a dict")
        state = PromotionState()
        for ev in ev_objs:
            state = record_evidence(state, ev, config)
        out = state_to_dict(state)
        out["evidence_used"] = len(ev_objs)
        out["config"] = {
            "tier_1_threshold": config.tier_1_threshold,
            "tier_2_threshold": config.tier_2_threshold,
            "tier_3_threshold": config.tier_3_threshold,
            "tier_4_threshold": config.tier_4_threshold,
            "confidence_threshold": config.confidence_threshold,
        }
        return out

    def tier_promo_compute(self, count, confidence=0.5, current_tier=None):
        # Audit fix: real entry point is compute_tier(evidence_count,
        # confidence, config) — no module-level `compute` exists.
        compute_tier, *_ = _load_tier_promo()
        from ..capability.tier_promo import PromotionConfig, PromotionLevel

        config = PromotionConfig()
        cur = PromotionLevel[str(current_tier)] if current_tier else None
        tier = compute_tier(
            evidence_count=int(count), confidence=float(confidence), config=config, current_tier=cur
        )
        return {
            "tier": tier.name,
            "tier_value": tier.value,
            "evidence_count": int(count),
            "confidence": float(confidence),
            "suppressed": float(confidence) < config.confidence_threshold,
        }

    def tier_promo_can_spawn(self, parent_id, allowed_children, child_id):
        # Audit fix: can_spawn is a method on SubAgentBoundary, not a
        # module-level function.
        *_, SubAgentBoundary, _state_to_dict = _load_tier_promo()
        boundary = SubAgentBoundary(
            parent_id=str(parent_id), allowed_children=[str(c) for c in (allowed_children or [])]
        )
        return {
            "allowed": boundary.can_spawn(str(child_id)),
            "parent_id": boundary.parent_id,
            "child_id": str(child_id),
            "allowed_children": list(boundary.allowed_children),
        }

    def tier_promo_cohabitation(self, parent_a, children_a, parent_b, children_b):
        # Audit fix: cohabitation_check is a method on SubAgentBoundary.
        # Real semantics: boundaries are compatible iff both parents are the
        # same parent (cross-parent cohabitation is rejected by design).
        *_, SubAgentBoundary, _state_to_dict = _load_tier_promo()
        boundary_a = SubAgentBoundary(
            parent_id=str(parent_a), allowed_children=[str(c) for c in (children_a or [])]
        )
        compatible = boundary_a.cohabitation_check(str(parent_b))
        return {
            "compatible": compatible,
            "parent_a": str(parent_a),
            "parent_b": str(parent_b),
            "children_a": [str(c) for c in (children_a or [])],
            "children_b": [str(c) for c in (children_b or [])],
            "reason": "same parent namespace" if compatible else "cross-parent isolation enforced",
        }

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
            "scores": {
                k: {"score": v.score, "tier": v.tier, "reasons": v.reasons} for k, v in agg.items()
            },
            "ranked": [{"provider": p, "score": s} for p, s in rank_providers(agg)],
            "recommended": rec,
        }

    # consumption intel
    def consumption_intel(self, context, endpoints):
        # Audit fix: call the real select_endpoint pipeline (no `analyze` exists).
        select_endpoint, RequestContext, EndpointSpec = _load_consumption_intel()
        ctx = (
            context
            if isinstance(context, RequestContext)
            else RequestContext.from_dict(context or {})
        )
        specs = []
        for ep in endpoints or []:
            if isinstance(ep, EndpointSpec):
                specs.append(ep)
            elif isinstance(ep, dict):
                specs.append(
                    EndpointSpec(
                        endpoint_id=str(ep.get("endpoint_id") or ep.get("id") or ""),
                        model_id=str(ep.get("model_id") or ep.get("model") or ep.get("endpoint_id") or ""),
                        cost_per_1k_input=float(ep.get("cost_per_1k_input", 0.0)),
                        cost_per_1k_output=float(ep.get("cost_per_1k_output", 0.0)),
                        avg_latency_ms=float(ep.get("avg_latency_ms", 0.0)),
                        capabilities=list(ep.get("capabilities", [])),
                        tier=ep.get("tier", "standard"),
                        enabled=bool(ep.get("enabled", True)),
                        consecutive_failures=int(ep.get("consecutive_failures", 0)),
                    )
                )
        decision = select_endpoint(ctx, specs)
        return decision.to_dict()

    # should rebalance
    def should_rebalance(self, stats, config=None):
        # Audit fix: call the real consensus.should_rebalance(stats, config)
        # (the previous inline reimplementation ignored `config` entirely).
        should_rebalance = _load_consensus()
        from ..capability.consensus import TierStat

        if not isinstance(stats, dict):
            raise ValueError("stats must be a dict of tier -> TierStat dict")
        stats_objs = {}
        for k, v in stats.items():
            if isinstance(v, TierStat):
                stats_objs[k] = v
            elif isinstance(v, dict):
                valid = {f: v[f] for f in TierStat.__dataclass_fields__ if f in v}
                stats_objs[k] = TierStat(**valid)
            else:
                raise ValueError(f"stats[{k}] must be a dict")
        return {
            "should_rebalance": should_rebalance(stats_objs, config or {}),
            "tiers_evaluated": len(stats_objs),
        }

    # cost estimate
    def cost_estimate(
        self,
        input_tokens,
        output_tokens,
        channels,
        include_fallback=True,
        preset_name="balanced",
        retry_factor=1.0,
    ):
        estimate_moa_cost, *_ = _load_cost_estimator()
        from ..capability.cost_estimator import Channel as CEChannel

        # Convert dict channels to Channel objects
        ch_objs = []
        for ch in channels:
            if isinstance(ch, dict):
                ch_objs.append(
                    CEChannel(
                        **{k: v for k, v in ch.items() if k in CEChannel.__dataclass_fields__}
                    )
                )
            else:
                ch_objs.append(ch)
        result = estimate_moa_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            channels=ch_objs,
            preset_name=preset_name,
            include_fallback=include_fallback,
            retry_factor=retry_factor,
        )
        if hasattr(result, "to_dict"):
            return result.to_dict()
        if isinstance(result, dict):
            return result
        return {"result": str(result)}
