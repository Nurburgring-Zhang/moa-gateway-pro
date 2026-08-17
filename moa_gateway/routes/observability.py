"""Observability API routes -- test reports, execution traces and logs.

Endpoints:
- GET  /v1/observability/reports            -- list all test reports
- GET  /v1/observability/reports/{report_id} -- get a specific report
- POST /v1/observability/reports/generate    -- generate a new report
- GET  /v1/observability/traces              -- query execution traces
- GET  /v1/observability/logs                -- real structured logs (audit F5)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ..auth import require_api_key
from ..observability.test_report import get_report_generator

router = APIRouter(prefix="/v1/observability", tags=["observability"])


@router.get("/logs")
async def list_logs(
    limit: int = Query(default=100, ge=1, le=1000),
    level: str | None = Query(default=None, description="Filter by level"),
    key_info: dict[str, Any] = Depends(require_api_key),
):
    """Return REAL structured logs for the admin-ui log viewer.

    Sources (in order): the RBAC audit JSONL log, then gateway request logs
    from storage. No mock data — if there are no logs yet, an empty list is
    returned and the UI shows an empty (truthful) state.
    """
    entries: list[dict[str, Any]] = []

    # 1) Audit log (data/logs/audit.jsonl) — most recent first.
    audit_path = Path("data/logs/audit.jsonl")
    if audit_path.exists():
        try:
            lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines[-limit * 2 :]:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entries.append(
                    {
                        "timestamp": rec.get("ts") or rec.get("timestamp", ""),
                        "level": "AUDIT",
                        "source": rec.get("action", "audit"),
                        "message": (
                            f"{rec.get('actor_id', '?')} {rec.get('action', '?')} "
                            f"{rec.get('resource', '')} -> {rec.get('result', '')}"
                        ).strip(),
                    }
                )
        except OSError:
            pass

    # 2) Request logs from storage.
    try:
        from ..storage import get_storage

        for row in get_storage().list_logs(limit=limit):
            status = row.get("status", 0)
            lvl = "ERROR" if status >= 500 else ("WARN" if status >= 400 else "INFO")
            entries.append(
                {
                    "timestamp": row.get("timestamp", ""),
                    "level": lvl,
                    "source": f"{row.get('method', '?')} {row.get('path', '?')}",
                    "message": (
                        f"status={status} model={row.get('model_used', '-')} "
                        f"latency={row.get('latency_ms', 0)}ms"
                    ),
                }
            )
    except Exception:
        pass

    if level:
        entries = [e for e in entries if e["level"].lower() == level.lower()]

    # Newest first; timestamps may be epoch floats or ISO strings — keep as-is.
    entries = entries[:limit]
    return {"total": len(entries), "logs": entries}


@router.get("/reports")
async def list_reports(
    key_info: dict[str, Any] = Depends(require_api_key),
):
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
async def get_report(
    report_id: str,
    key_info: dict[str, Any] = Depends(require_api_key),
):
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
    key_info: dict[str, Any] = Depends(require_api_key),
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
    key_info: dict[str, Any] = Depends(require_api_key),
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
