"""Metrics collector -- wraps agent loop execution with trace recording.

Integrates with the TestReportGenerator to automatically record execution
traces whenever an agent loop runs.  Can be used as a decorator or wrapper.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from .test_report import ExecutionTrace, TestReportGenerator, get_report_generator

logger = logging.getLogger(__name__)


class MetricsCollector:
    """Runtime metrics collector -- integrates into AgentLoop and ProbeEngine.

    Wraps agent loop execution to automatically record traces.
    """

    def __init__(self, report_generator: TestReportGenerator | None = None) -> None:
        self._generator = report_generator or get_report_generator()

    async def trace_loop_execution(
        self,
        loop: Any,
        messages: list[dict[str, Any]],
        context: Any | None = None,
        endpoint_id: str | None = None,
        scenario_name: str | None = None,
    ) -> Any:
        """Wrap an agent loop run, recording the execution trace.

        Args:
            loop: AgentLoop instance (must have async run() method).
            messages: Messages to pass to the loop.
            context: AgentContext (optional).
            endpoint_id: Endpoint being tested (for filtering).
            scenario_name: Scenario name (for filtering).

        Returns:
            The LoopResult from the loop execution.
        """
        start = time.monotonic()

        try:
            result = await loop.run(messages, context)
        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            trace = ExecutionTrace(
                loop_type=type(loop).__name__,
                steps=[],
                total_duration_ms=round(duration_ms, 2),
                success=False,
                model_used=getattr(context, "model", None)
                if context else None,
                tools_used=[],
                endpoint_id=endpoint_id,
                scenario_name=scenario_name,
            )
            self._generator.record_trace(trace)
            raise

        duration_ms = (time.monotonic() - start) * 1000

        # Extract metadata from the result
        steps: list[dict[str, Any]] = []
        if hasattr(result, "tool_calls") and result.tool_calls:
            for i, tc in enumerate(result.tool_calls):
                step_info: dict[str, Any] = {
                    "action": tc.name,
                    "input": str(tc.arguments)[:500],
                    "status": "success",
                }
                if hasattr(result, "tool_results") and i < len(result.tool_results):
                    tr = result.tool_results[i]
                    step_info["output"] = str(tr.output)[:500]
                    step_info["duration_ms"] = tr.latency_ms
                    step_info["status"] = "success" if tr.success else "failure"
                    if tr.error:
                        step_info["error"] = tr.error
                steps.append(step_info)

        tools_used = list({
            tc.name for tc in result.tool_calls
        }) if hasattr(result, "tool_calls") and result.tool_calls else []

        trace = ExecutionTrace(
            loop_type=type(loop).__name__,
            steps=steps,
            total_duration_ms=round(duration_ms, 2),
            success=result.success if hasattr(result, "success") else True,
            model_used=getattr(context, "model", None)
            if context else None,
            tools_used=tools_used,
            token_count=getattr(result, "total_tokens", None)
            if hasattr(result, "total_tokens") else None,
            endpoint_id=endpoint_id,
            scenario_name=scenario_name,
        )

        self._generator.record_trace(trace)
        logger.info(
            "Trace recorded: loop=%s success=%s duration=%.1fms tools=%s",
            trace.loop_type,
            trace.success,
            trace.total_duration_ms,
            tools_used,
        )

        return result

    def record_custom_trace(
        self,
        loop_type: str,
        success: bool,
        duration_ms: float,
        steps: list[dict[str, Any]] | None = None,
        model_used: str | None = None,
        tools_used: list[str] | None = None,
        endpoint_id: str | None = None,
        scenario_name: str | None = None,
    ) -> ExecutionTrace:
        """Manually record a custom execution trace.

        Useful for recording traces from non-standard execution paths
        (e.g., probe engine, workflow runner).
        """
        trace = ExecutionTrace(
            loop_type=loop_type,
            steps=steps or [],
            total_duration_ms=duration_ms,
            success=success,
            model_used=model_used,
            tools_used=tools_used or [],
            endpoint_id=endpoint_id,
            scenario_name=scenario_name,
        )
        self._generator.record_trace(trace)
        return trace

    def get_generator(self) -> TestReportGenerator:
        """Return the underlying report generator."""
        return self._generator
