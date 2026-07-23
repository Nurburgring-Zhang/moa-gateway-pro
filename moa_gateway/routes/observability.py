"""Observability API routes -- test reports and execution traces.

Endpoints:
- GET  /v1/observability/reports            -- list all test reports
- GET  /v1/observability/reports/{report_id} -- get a specific report
- POST /v1/observability/reports/generate    -- generate a new report
- GET  /v1/observability/traces              -- query execution traces
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..observability.test_report import get_report_generator

router = APIRouter(prefix="/v1/observability", tags=["observability"])


@router.get("/reports")
async def list_reports():
    """List all generated test reports."""
    gen = get_report_generator()
    reports = gen.get_all_reports()
    return {
        "total": len(reports),
        "reports": [
            {
                "report_id": r.report_id,
                "endpoint_id": r.endpoint_id,
                "scenario_name": r.scenario_name,
                "summary": r.summary,
                "generated_at": r.generated_at.isoformat(),
                "trace_count": len(r.traces),
            }
            for r in reports
        ],
    }


@router.get("/reports/{report_id}")
async def get_report(report_id: str):
    """Get a specific test report by ID."""
    gen = get_report_generator()
    report = gen.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Report not found: {report_id}")
    return report.to_dict()


@router.post("/reports/generate")
async def generate_report(
    endpoint_id: str | None = Query(default=None),
    scenario_name: str | None = Query(default=None),
):
    """Generate a new test report from recorded traces."""
    gen = get_report_generator()
    report = gen.generate_report(
        endpoint_id=endpoint_id,
        scenario_name=scenario_name,
    )
    return report.to_dict()


@router.get("/traces")
async def list_traces(
    endpoint_id: str | None = Query(default=None),
    scenario_name: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
):
    """Query execution traces."""
    gen = get_report_generator()
    traces = gen.get_all_traces(
        endpoint_id=endpoint_id,
        scenario_name=scenario_name,
    )
    # Apply limit
    traces = traces[-limit:]
    return {
        "total": len(traces),
        "traces": [t.to_dict() for t in traces],
    }
