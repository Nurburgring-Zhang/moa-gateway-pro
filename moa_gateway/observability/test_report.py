"""Test report module -- execution traces and test reports.

Inspired by Paseo's LoopRecord and LoopIterationRecord data structures.
Provides:
- ExecutionTrace: record of a single agent loop execution
- TestReport: aggregated report with summary statistics
- TestReportGenerator: collect traces and generate reports
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass
class ExecutionTrace:
    """Record of a single agent loop execution (Paseo LoopRecord-inspired).

    Attributes:
        trace_id: Unique trace identifier.
        loop_type: Type of loop (react / plan_execute / scenario).
        steps: List of step dicts, each containing:
            {action, input, output, duration_ms, status, error}
        total_duration_ms: Total execution time in milliseconds.
        success: Whether the execution succeeded.
        model_used: Model name used (if any).
        tools_used: List of tool names used.
        token_count: Token count (if available).
        timestamp: When the trace was recorded.
        endpoint_id: Endpoint being tested (if applicable).
        scenario_name: Scenario name (if applicable).
    """

    trace_id: str = field(default_factory=lambda: str(uuid4()))
    loop_type: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    total_duration_ms: float = 0.0
    success: bool = False
    model_used: str | None = None
    tools_used: list[str] = field(default_factory=list)
    token_count: int | None = None
    timestamp: datetime = field(default_factory=datetime.now)
    endpoint_id: str | None = None
    scenario_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize trace to dictionary."""
        return {
            "trace_id": self.trace_id,
            "loop_type": self.loop_type,
            "steps": self.steps,
            "total_duration_ms": self.total_duration_ms,
            "success": self.success,
            "model_used": self.model_used,
            "tools_used": self.tools_used,
            "token_count": self.token_count,
            "timestamp": self.timestamp.isoformat(),
            "endpoint_id": self.endpoint_id,
            "scenario_name": self.scenario_name,
        }


@dataclass
class TestReport:
    """Aggregated test report with summary statistics.

    Attributes:
        report_id: Unique report identifier.
        endpoint_id: Endpoint being tested (if filtered).
        scenario_name: Scenario name (if filtered).
        traces: List of execution traces in this report.
        summary: Aggregated statistics dict.
        generated_at: When the report was generated.
    """

    report_id: str = field(default_factory=lambda: str(uuid4()))
    endpoint_id: str | None = None
    scenario_name: str | None = None
    traces: list[ExecutionTrace] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    generated_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Serialize report to dictionary."""
        return {
            "report_id": self.report_id,
            "endpoint_id": self.endpoint_id,
            "scenario_name": self.scenario_name,
            "traces": [t.to_dict() for t in self.traces],
            "summary": self.summary,
            "generated_at": self.generated_at.isoformat(),
        }

    def to_markdown(self) -> str:
        """Render report as Markdown."""
        s = self.summary
        lines = [
            f"# Test Report",
            f"",
            f"- **Report ID**: `{self.report_id}`",
            f"- **Endpoint**: {self.endpoint_id or '(all)'}",
            f"- **Scenario**: {self.scenario_name or '(all)'}",
            f"- **Generated**: {self.generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"",
            f"## Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total traces | {s.get('total', 0)} |",
            f"| Passed | {s.get('passed', 0)} |",
            f"| Failed | {s.get('failed', 0)} |",
            f"| Pass rate | {s.get('pass_rate', 0):.1%} |",
            f"| Avg latency | {s.get('avg_latency_ms', 0):.1f} ms |",
            f"| P95 latency | {s.get('p95_latency_ms', 0):.1f} ms |",
            f"",
        ]

        if self.traces:
            lines.append("## Traces")
            lines.append("")
            lines.append("| Trace ID | Loop Type | Success | Duration (ms) | Model | Tools |")
            lines.append("|----------|-----------|---------|---------------|-------|-------|")
            for t in self.traces:
                tools_str = ", ".join(t.tools_used) if t.tools_used else "-"
                model_str = t.model_used or "-"
                success_str = "PASS" if t.success else "FAIL"
                lines.append(
                    f"| `{t.trace_id[:8]}` | {t.loop_type} | {success_str} | "
                    f"{t.total_duration_ms:.1f} | {model_str} | {tools_str} |"
                )
            lines.append("")

            # Failure details
            failures = [t for t in self.traces if not t.success]
            if failures:
                lines.append("## Failed Traces")
                lines.append("")
                for t in failures:
                    lines.append(f"### `{t.trace_id[:8]}` ({t.loop_type})")
                    lines.append(f"- Duration: {t.total_duration_ms:.1f} ms")
                    lines.append(f"- Model: {t.model_used or 'N/A'}")
                    if t.steps:
                        for i, step in enumerate(t.steps):
                            status = step.get("status", "unknown")
                            error = step.get("error", "")
                            lines.append(
                                f"  - Step {i + 1}: {step.get('action', '?')} -- {status}"
                            )
                            if error:
                                lines.append(f"    Error: {error}")
                    lines.append("")

        return "\n".join(lines)


class TestReportGenerator:
    """Test report generator -- collects traces and produces reports.

    Maintains an in-memory trace buffer and a list of generated reports.
    Optionally persists reports to disk.
    """

    def __init__(self, storage_dir: str | None = None) -> None:
        # P1-4: Bound memory usage with deque(maxlen=...)
        self._traces: deque[ExecutionTrace] = deque(maxlen=10000)
        self._reports: deque[TestReport] = deque(maxlen=500)
        self._storage_dir = storage_dir
        if storage_dir:
            import os
            os.makedirs(storage_dir, exist_ok=True)

    def record_trace(self, trace: ExecutionTrace) -> None:
        """Record an execution trace."""
        self._traces.append(trace)
        logger.debug(
            "Recorded trace %s (loop=%s, success=%s, %.1fms)",
            trace.trace_id[:8],
            trace.loop_type,
            trace.success,
            trace.total_duration_ms,
        )

    def generate_report(
        self,
        endpoint_id: str | None = None,
        scenario_name: str | None = None,
    ) -> TestReport:
        """Generate a test report from recorded traces.

        Args:
            endpoint_id: Filter traces by endpoint ID.
            scenario_name: Filter traces by scenario name.

        Returns:
            TestReport with filtered traces and summary statistics.
        """
        traces = [
            t for t in self._traces
            if (not endpoint_id or t.endpoint_id == endpoint_id)
            and (not scenario_name or t.scenario_name == scenario_name)
        ]

        total = len(traces)
        passed = sum(1 for t in traces if t.success)
        failed = total - passed
        latencies = sorted([t.total_duration_ms for t in traces])

        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            p95_idx = int(len(latencies) * 0.95)
            if p95_idx >= len(latencies):
                p95_idx = len(latencies) - 1
            p95_latency = latencies[p95_idx]
        else:
            avg_latency = 0.0
            p95_latency = 0.0

        summary = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": passed / total if total > 0 else 0.0,
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
        }

        report = TestReport(
            endpoint_id=endpoint_id,
            scenario_name=scenario_name,
            traces=traces,
            summary=summary,
        )

        self._reports.append(report)

        # Optionally persist to disk
        if self._storage_dir:
            import json
            import os
            filepath = os.path.join(self._storage_dir, f"{report.report_id}.json")
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(report.to_dict(), f, indent=2, ensure_ascii=False)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to persist report: %s", exc)

        return report

    def get_all_reports(self) -> list[TestReport]:
        """Return all generated reports."""
        return list(self._reports)

    def get_report(self, report_id: str) -> TestReport | None:
        """Return a specific report by ID."""
        for r in self._reports:
            if r.report_id == report_id:
                return r
        return None

    def get_all_traces(
        self,
        endpoint_id: str | None = None,
        scenario_name: str | None = None,
    ) -> list[ExecutionTrace]:
        """Return all traces, optionally filtered."""
        return [
            t for t in self._traces
            if (not endpoint_id or t.endpoint_id == endpoint_id)
            and (not scenario_name or t.scenario_name == scenario_name)
        ]

    def clear(self) -> None:
        """Clear all traces and reports."""
        self._traces.clear()
        self._reports.clear()


# ============ Module-level singleton ============

_global_generator: TestReportGenerator | None = None


def get_report_generator() -> TestReportGenerator:
    """Get the global TestReportGenerator singleton."""
    global _global_generator
    if _global_generator is None:
        _global_generator = TestReportGenerator()
    return _global_generator


def init_report_generator(storage_dir: str | None = None) -> TestReportGenerator:
    """Initialize the global TestReportGenerator with optional storage."""
    global _global_generator
    _global_generator = TestReportGenerator(storage_dir=storage_dir)
    return _global_generator
