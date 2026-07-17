"""ObservabilityService — wraps trace, action_audit, audit_cache, hook_events, in_flight.

Exposes:
  - trace(action, traceparent, trace_id, span_id, name, duration_ms, status, limit)
  - audit(action, action_id, event_type, actor, outcome, resource, sub_action, metadata, timestamp, limit)
  - hook_events(action, event, data, session_id, stage)
  - in_flight(action, session_id, phase, at, checkpoints)
"""
from __future__ import annotations

from typing import Any, Dict, List

from .base import ServiceBase, ServiceMethod, service_method


def _load_trace():
    from ..capability.trace import (
        start as t_start, end as t_end, span as t_span,
        parse_traceparent, query as t_query,
    )
    return t_start, t_end, t_span, parse_traceparent, t_query


def _load_audit():
    from ..capability.action_audit import (
        record, query, stats,
    )
    return record, query, stats


def _load_hook_events():
    from ..capability.hook_events import (
        list_events, register, trigger, ralph_advance,
    )
    return list_events, register, trigger, ralph_advance


def _load_in_flight():
    from ..capability.in_flight import (
        in_flight, start as if_start, complete as if_complete,
        transition, merge as if_merge,
    )
    return in_flight, if_start, if_complete, transition, if_merge


class ObservabilityService(ServiceBase):
    name = "observability"
    description = "可观测性: trace / audit / hook / in-flight checkpoints"

    def _register_methods(self):
        self._methods["trace"] = ServiceMethod(
            name="trace", description="分布式追踪 (start/end/span/parse_traceparent/query)",
            func=self.trace,
            input_required=["action"],
        )
        self._methods["audit"] = ServiceMethod(
            name="audit", description="action 审计 (record/query/stats)",
            func=self.audit,
            input_required=["action"],
        )
        self._methods["hook_events"] = ServiceMethod(
            name="hook_events", description="hook 事件 (list_events/register/trigger/ralph_advance)",
            func=self.hook_events,
            input_required=["action"],
        )
        self._methods["in_flight"] = ServiceMethod(
            name="in_flight", description="in-flight checkpoint (in_flight/start/complete/transition/merge)",
            func=self.in_flight,
            input_required=["action"],
        )

    def trace(self, action, **kwargs):
        t_start, t_end, t_span, parse_tp, t_query = _load_trace()
        if action == "start":
            return {"trace": t_start()}
        if action == "end":
            return t_end(trace_id=kwargs.get("trace_id", ""), span_id=kwargs.get("span_id", ""),
                         status=kwargs.get("status", "ok"))
        if action == "span":
            return t_span(trace_id=kwargs.get("trace_id", ""), name=kwargs.get("name", ""),
                          duration_ms=kwargs.get("duration_ms", 0.0))
        if action == "parse_traceparent":
            return parse_tp(traceparent=kwargs.get("traceparent", ""))
        if action == "query":
            return {"traces": t_query(limit=kwargs.get("limit", 10))}
        raise ValueError(f"unknown action: {action}")

    def audit(self, action, **kwargs):
        record, query, stats = _load_audit()
        if action == "record":
            return record(event_type=kwargs.get("event_type", ""),
                          actor=kwargs.get("actor", ""),
                          outcome=kwargs.get("outcome", "allow"),
                          resource=kwargs.get("resource", ""),
                          sub_action=kwargs.get("sub_action", ""),
                          metadata=kwargs.get("metadata", {}),
                          timestamp=kwargs.get("timestamp", 0.0))
        if action == "query":
            return {"events": query(event_type=kwargs.get("event_type", ""),
                                     limit=kwargs.get("limit", 10))}
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
            return trigger(event=kwargs.get("event", ""),
                            data=kwargs.get("data", {}),
                            session_id=kwargs.get("session_id", ""))
        if action == "ralph_advance":
            return ralph_advance(stage=kwargs.get("stage", ""),
                                  data=kwargs.get("data", {}))
        raise ValueError(f"unknown action: {action}")

    def in_flight(self, action, **kwargs):
        in_flight, if_start, if_complete, transition, if_merge = _load_in_flight()
        if action == "in_flight":
            return {"in_flight": in_flight(session_id=kwargs.get("session_id", ""),
                                            phase=kwargs.get("phase", ""),
                                            at=kwargs.get("at", 0.0))}
        if action == "start":
            return if_start(phase=kwargs.get("phase", ""), at=kwargs.get("at", 0.0))
        if action == "complete":
            return if_complete(session_id=kwargs.get("session_id", ""),
                                phase=kwargs.get("phase", ""),
                                at=kwargs.get("at", 0.0))
        if action == "transition":
            return transition(session_id=kwargs.get("session_id", ""))
        if action == "merge":
            return if_merge(checkpoints=kwargs.get("checkpoints", []))
        raise ValueError(f"unknown action: {action}")
