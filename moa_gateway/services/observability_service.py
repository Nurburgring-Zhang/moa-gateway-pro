"""ObservabilityService — wraps trace, action_audit, audit_cache, hook_events, in_flight.

Exposes:
  - trace(action, traceparent, trace_id, span_id, name, duration_ms, status, limit)
  - audit(action, action_id, event_type, actor, outcome, resource, sub_action, metadata, timestamp, limit)
  - hook_events(action, event, data, session_id, stage)
  - in_flight(action, session_id, phase, at, checkpoints)
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


def _load_trace():
    from ..capability.trace import (
        format_traceparent,
        new_span,
        new_trace,
        parse_traceparent,
    )

    return new_trace, new_span, format_traceparent, parse_traceparent


# Audit fix: the old loaders imported module-level functions that do not exist
# (the logic lives on AuditGate / HookRegistry / InFlightDetector classes).
# Use real class singletons so these service methods actually execute.
_audit_gate = None
_hook_registry = None
_inflight_detector = None
_trace_collector = None


def _get_audit_gate():
    global _audit_gate
    if _audit_gate is None:
        from ..capability.action_audit import AuditGate

        _audit_gate = AuditGate()
    return _audit_gate


def _get_hook_registry():
    global _hook_registry
    if _hook_registry is None:
        from ..capability.hook_events import HookRegistry

        _hook_registry = HookRegistry()
    return _hook_registry


def _get_inflight_detector():
    global _inflight_detector
    if _inflight_detector is None:
        from ..capability.in_flight import InFlightDetector

        _inflight_detector = InFlightDetector()
    return _inflight_detector


def _get_trace_collector():
    global _trace_collector
    if _trace_collector is None:
        from ..capability.trace import TraceCollector

        _trace_collector = TraceCollector()
    return _trace_collector


def _ctx_to_dict(ctx) -> dict:
    return {
        "trace_id": ctx.trace_id,
        "span_id": ctx.span_id,
        "parent_span_id": ctx.parent_span_id,
        "start_ts": ctx.start_ts,
        "tags": dict(ctx.tags or {}),
    }


class ObservabilityService(ServiceBase):
    name = "observability"
    description = "可观测性: trace / audit / hook / in-flight checkpoints"

    def _register_methods(self):
        self._methods["trace"] = ServiceMethod(
            name="trace",
            description="分布式追踪 (start/span/end/format_traceparent/parse_traceparent/query)",
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
        # Audit fix: TraceContext has no `flags` field and parse_traceparent
        # takes `header`; use a shared TraceCollector so start/end/query flow.
        new_trace, new_span, format_tp, parse_tp = _load_trace()
        collector = _get_trace_collector()
        if action == "start":
            tags = kwargs.get("tags") or {}
            ctx = collector.start_trace(traceparent_header=kwargs.get("traceparent"))
            if tags:
                ctx.tags.update(tags)
            return {"trace": _ctx_to_dict(ctx), "traceparent": format_tp(ctx)}
        if action == "span":
            parent = parse_tp(header=kwargs.get("traceparent", ""))
            if parent is None:
                parent = new_trace(kwargs.get("tags") or {})
            child = new_span(parent, name=kwargs.get("name", "span"), tags=kwargs.get("tags") or {})
            duration_ms = kwargs.get("duration_ms")
            if duration_ms is not None:
                collector.record_span(
                    child,
                    name=kwargs.get("name", "span"),
                    duration_ms=float(duration_ms),
                    status=kwargs.get("status", "ok"),
                )
            return {"span": _ctx_to_dict(child), "traceparent": format_tp(child)}
        if action == "end":
            ctx = parse_tp(header=kwargs.get("traceparent", ""))
            if ctx is None:
                raise ValueError("end requires a valid traceparent header")
            collector.end_trace(ctx, status=kwargs.get("status", "ok"), error=kwargs.get("error"))
            return {"ended": True, "trace_id": ctx.trace_id}
        if action == "format_traceparent":
            from ..capability.trace import TraceContext

            ctx = TraceContext(
                trace_id=kwargs.get("trace_id", "0" * 32),
                parent_span_id=kwargs.get("parent_span_id"),
                span_id=kwargs.get("span_id", "0" * 16),
                start_ts=time.time(),
            )
            return {"traceparent": format_tp(ctx, flags=kwargs.get("flags", "01"))}
        if action == "parse_traceparent":
            ctx = parse_tp(header=kwargs.get("traceparent", ""))
            return {"parsed": _ctx_to_dict(ctx) if ctx else None}
        if action == "query":
            traces = collector.query(
                since_ts=kwargs.get("since_ts"),
                min_duration_ms=kwargs.get("min_duration_ms"),
                status=kwargs.get("status"),
                limit=int(kwargs.get("limit", 10)),
            )
            return {"traces": traces, "stats": collector.stats()}
        raise ValueError(f"unknown action: {action}")

    def audit(self, action, **kwargs):
        gate = _get_audit_gate()
        if action == "record":
            log = gate.audit(
                action_id=kwargs.get("action_id", "a1"),
                action_data=kwargs.get("action_data", {}),
            )
            return log.to_dict()
        if action == "query":
            logs = gate.get_logs()
            limit = int(kwargs.get("limit", 10))
            return {"events": [l.to_dict() for l in logs[-limit:]]}
        if action == "stats":
            return {"log_count": len(gate.get_logs()), "cache_size": len(gate.get_cache())}
        raise ValueError(f"unknown action: {action}")

    def hook_events(self, action, **kwargs):
        from ..capability.hook_events import HookEvent, ralph_loop

        reg = _get_hook_registry()
        if action == "list_events":
            return {"events": [e.value for e in HookEvent]}
        if action == "register":
            # A callable handler cannot be supplied over JSON dispatch; report the
            # registry state honestly instead of pretending to register.
            return {
                "registered_event": kwargs.get("event"),
                "total_handlers": len(reg.list_handlers()),
                "note": "handler registration requires an in-process callable",
            }
        if action == "trigger":
            event_name = kwargs.get("event", "SessionStart")
            event = HookEvent(event_name)  # raises ValueError if unknown
            results = reg.trigger(event, data=kwargs.get("data", {}))
            return {"event": event_name, "handlers_invoked": len(results)}
        if action == "ralph_advance":
            return ralph_loop(stage=kwargs.get("stage", ""), data=kwargs.get("data", {}))
        raise ValueError(f"unknown action: {action}")

    def in_flight(self, action, **kwargs):
        from ..capability.in_flight import (
            Phase,
            TeamCheckpointMerger,
            checkpoint_from_dict,
            phase_state_to_dict,
        )

        detector = _get_inflight_detector()
        if action == "start":
            sid = detector.record_start(
                Phase(kwargs.get("phase", "analyze")), at=kwargs.get("at")
            )
            return {"session_id": sid}
        if action == "complete":
            detector.record_complete(
                kwargs.get("session_id", ""), Phase(kwargs.get("phase", "analyze")), at=kwargs.get("at")
            )
            return {"completed": True}
        if action == "in_flight":
            states = detector.detect_in_flight(at=kwargs.get("at"))
            return {"in_flight": [phase_state_to_dict(ps) for ps in states]}
        if action == "transition":
            phase = detector.detect_phase_transition(kwargs.get("session_id", ""))
            return {"transition_to": phase.value if phase else None}
        if action == "merge":
            # Audit fix: build Checkpoints via checkpoint_from_dict so the
            # phase string is converted to the Phase enum.
            merger = TeamCheckpointMerger()
            for c in kwargs.get("checkpoints", []):
                if isinstance(c, dict):
                    merger.add_checkpoint(checkpoint_from_dict(c))
            return merger.merge()
        raise ValueError(f"unknown action: {action}")
