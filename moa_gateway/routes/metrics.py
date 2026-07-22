"""Prometheus metrics endpoint — enhanced with LLM business metrics."""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import Response

from ..model_pool import get_model_pool
from ..observability import (
    endpoint_health_gauge,
    get_tracer,
    prometheus_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def prometheus_metrics():
    """Prometheus scrape endpoint (public, no auth, for Prometheus server scrape).

    Exposes:
    - moa_llm_request_duration_seconds (histogram)
    - moa_llm_requests_total (counter)
    - moa_llm_tokens_total (counter)
    - moa_llm_cost_dollars_total (counter)
    - moa_cache_hits_total / moa_cache_misses_total (counter)
    - moa_active_connections (gauge)
    - moa_provider_errors_total (counter)
    - moa_endpoint_health (gauge)
    - moa_chat_requests_total (counter, legacy)
    - moa_chat_latency_seconds (histogram, legacy)
    - moa_rate_limit_blocked_total (counter)
    - moa_executions_total (counter)
    """
    pool = get_model_pool()
    try:
        snap = pool.snapshot()
        for ep in snap.get("endpoints", []):
            ep_id = ep.get("id", "unknown")
            status = ep.get("health") or ep.get("status") or "unknown"
            value = 1.0 if status == "healthy" else (0.5 if "breaker" in str(status) else 0.0)
            try:
                endpoint_health_gauge.labels(endpoint_id=ep_id).set(value)
            except Exception as e:
                logger.warning("Prometheus endpoint gauge update failed for %s: %s", ep_id, e)
    except Exception as e:
        logger.warning("Prometheus metrics snapshot failed: %s", e)
    body, status, headers = prometheus_response()
    return Response(content=body, status_code=status, headers=headers)


@router.get("/metrics/traces")
async def recent_traces():
    """Get recent trace spans (for debugging/dev UI)."""
    tracer = get_tracer()
    spans = tracer.get_recent_spans(limit=50)
    return {"spans": spans, "count": len(spans)}
