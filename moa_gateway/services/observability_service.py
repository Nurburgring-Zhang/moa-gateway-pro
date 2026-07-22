"""ObservabilityService — wraps trace, action_audit, audit_cache, hook_events, in_flight.

Exposes:
  - trace(action, traceparent, trace_id, span_id, name, duration_ms, status, limit)
  - audit(action, action_id, event_type, actor, outcome, resource, sub_action, metadata, timestamp, limit)
  - hook_events(action, event, data, session_id, stage)
  - in_flight(action, session_id, phase, at, checkpoints)
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_trace():
    from ..capability.trace import (
        format_traceparent,
        new_span,
        new_trace,
        parse_traceparent,
    )

    return new_trace, new_span, format_traceparent, parse_traceparent


def _load_audit():
    from ..capability.action_audit import (
        query,
        record,
        stats,
    )

    return record, query, stats


def _load_hook_events():
    from ..capability.hook_events import (
        list_events,
        ralph_advance,
        register,
        trigger,
    )

    return list_events, register, trigger, ralph_advance


def _load_in_flight():
    from ..capability.in_flight import (
        complete as if_complete,
    )
    from ..capability.in_flight import (
        in_flight,
        transition,
    )
    from ..capability.in_flight import (
        merge as if_merge,
    )
    from ..capability.in_flight import (
        start as if_start,
    )

    return in_flight, if_start, if_complete, transition, if_merge


class ObservabilityService(ServiceBase):
    name = "observability"
    description = "可观测性: trace / audit / hook / in-flight checkpoints"

    def _register_methods(self):
        self._methods["trace"] = ServiceMethod(
            name="trace",
            description="分布式追踪 (start/end/span/parse_traceparent/query)",
            func=self.trace,
            input_required=["action"],
        )
        self._methods["audit"] = ServiceMethod(
            name="audit",
            description="action 审计 (record/query/stats)",
            func=self.audit,
            input_required=["action"],
        )
        self._methods["hook_events"] = ServiceMethod(
            name="hook_events",
            description="hook 事件 (list_events/register/trigger/ralph_advance)",
            func=self.hook_events,
            input_required=["action"],
        )
        self._methods["in_flight"] = ServiceMethod(
            name="in_flight",
            description="in-flight checkpoint (in_flight/start/complete/transition/merge)",
            func=self.in_flight,
            input_required=["action"],
        )

    def trace(self, action, **kwargs):
        new_trace, new_span, format_tp, parse_tp = _load_trace()
        if action == "start":
            tags = kwargs.get("tags") or {}
            return {"trace": new_trace(tags)}
        if action == "format_traceparent":
            from ..capability.trace import TraceContext

            ctx = TraceContext(
                trace_id=kwargs.get("trace_id", "0" * 32),
                span_id=kwargs.get("span_id", "0" * 16),
                flags=kwargs.get("flags", "01"),
            )
            return {"traceparent": format_tp(ctx)}
        if action == "parse_traceparent":
            return parse_tp(traceparent=kwargs.get("traceparent", ""))
        if action == "query":
            # 真 query 走 TraceCollector
            from ..capability.trace import TraceCollector

            tc = TraceCollector()
            traces = tc.query(limit=kwargs.get("limit", 10))
            return {"traces": traces}
        raise ValueError(f"unknown action: {action}")

    def audit(self, action, **kwargs):
        record, query, stats = _load_audit()
        if action == "record":
            return record(
                event_type=kwargs.get("event_type", ""),
                actor=kwargs.get("actor", ""),
                outcome=kwargs.get("outcome", "allow"),
                resource=kwargs.get("resource", ""),
                sub_action=kwargs.get("sub_action", ""),
                metadata=kwargs.get("metadata", {}),
                timestamp=kwargs.get("timestamp", 0.0),
            )
        if action == "query":
            return {
                "events": query(
                    event_type=kwargs.get("event_type", ""), limit=kwargs.get("limit", 10)
                )
            }
        if action == "stats":
            return stats()
        raise ValueError(f"unknown action: {action}")

    def hook_events(self, action, **kwargs):
        list_events, register, trigger, ralph_advance = _load_hook_events()
        if action == "list_events":
            return {"events": list_events()}
        if action == "register":
            return register(event=kwargs.get("event", ""))
        if action == "trigger":
            return trigger(
                event=kwargs.get("event", ""),
                data=kwargs.get("data", {}),
                session_id=kwargs.get("session_id", ""),
            )
        if action == "ralph_advance":
            return ralph_advance(stage=kwargs.get("stage", ""), data=kwargs.get("data", {}))
        raise ValueError(f"unknown action: {action}")

    def in_flight(self, action, **kwargs):
        in_flight, if_start, if_complete, transition, if_merge = _load_in_flight()
        if action == "in_flight":
            return {
                "in_flight": in_flight(
                    session_id=kwargs.get("session_id", ""),
                    phase=kwargs.get("phase", ""),
                    at=kwargs.get("at", 0.0),
                )
            }
        if action == "start":
            return if_start(phase=kwargs.get("phase", ""), at=kwargs.get("at", 0.0))
        if action == "complete":
            return if_complete(
                session_id=kwargs.get("session_id", ""),
                phase=kwargs.get("phase", ""),
                at=kwargs.get("at", 0.0),
            )
        if action == "transition":
            return transition(session_id=kwargs.get("session_id", ""))
        if action == "merge":
            return if_merge(checkpoints=kwargs.get("checkpoints", []))
        raise ValueError(f"unknown action: {action}")
