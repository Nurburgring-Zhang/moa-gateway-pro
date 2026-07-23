"""moa_gateway.benchmark.metrics_store — JSON-based persistence for benchmark data.

Stores performance metrics and capability results in separate JSON files
under the data/ directory, avoiding modifications to storage.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .benchmark_engine import PerformanceMetrics
    from .capability_probe import CapabilityResult

logger = logging.getLogger(__name__)


class MetricsStore:
    """Persist benchmark and capability data to independent JSON files."""

    DEFAULT_METRICS_PATH = "data/performance_metrics.json"
    DEFAULT_CAPABILITIES_PATH = "data/capability_results.json"

    def __init__(
        self,
        metrics_path: str | None = None,
        capabilities_path: str | None = None,
    ):
        self._metrics_path = pathlib.Path(metrics_path or self.DEFAULT_METRICS_PATH)
        self._capabilities_path = pathlib.Path(
            capabilities_path or self.DEFAULT_CAPABILITIES_PATH
        )

    # ========== Performance Metrics ==========

    async def save_metrics(self, metrics: dict[str, PerformanceMetrics]) -> None:
        """Save performance metrics to JSON file."""
        data: dict[str, Any] = {}
        for eid, m in metrics.items():
            data[eid] = {
                "endpoint_id": m.endpoint_id,
                "tier": m.tier.value,
                "results": [
                    {
                        "endpoint_id": r.endpoint_id,
                        "timestamp": r.timestamp.isoformat(),
                        "latency_ms": r.latency_ms,
                        "tokens_per_second": r.tokens_per_second,
                        "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens,
                        "success": r.success,
                        "error": r.error,
                    }
                    for r in m.results[-100:]  # keep last 100 results
                ],
            }
        text = json.dumps(data, indent=2, ensure_ascii=False)
        # P1-3: Use asyncio.to_thread to avoid blocking the event loop
        await asyncio.to_thread(self._write_file_sync, self._metrics_path, text)
        logger.debug("Saved metrics for %d endpoints to %s", len(data), self._metrics_path)

    async def load_metrics(self) -> dict[str, Any]:
        """Load raw metrics data from JSON file.

        Returns a dict of endpoint_id -> raw dict. The caller (BenchmarkEngine)
        is responsible for reconstructing PerformanceMetrics objects.
        """
        # P1-3: Use asyncio.to_thread to avoid blocking the event loop
        return await asyncio.to_thread(self._load_metrics_sync)

    def _load_metrics_sync(self) -> dict[str, Any]:
        if not self._metrics_path.exists():
            return {}
        try:
            data = json.loads(self._metrics_path.read_text(encoding="utf-8"))
            logger.info("Loaded metrics for %d endpoints from %s", len(data), self._metrics_path)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load metrics from %s: %s", self._metrics_path, e)
            return {}

    # ========== Capability Results ==========

    async def save_capabilities(self, results: dict[str, CapabilityResult]) -> None:
        """Save capability probe results to JSON file."""
        data: dict[str, Any] = {}
        for eid, r in results.items():
            data[eid] = {
                "endpoint_id": r.endpoint_id,
                "capabilities": [c.value for c in r.capabilities],
                "capability_details": r.capability_details,
                "tested_at": r.tested_at.isoformat() if r.tested_at else None,
            }
        text = json.dumps(data, indent=2, ensure_ascii=False)
        # P1-3: Use asyncio.to_thread to avoid blocking the event loop
        await asyncio.to_thread(self._write_file_sync, self._capabilities_path, text)
        logger.debug(
            "Saved capabilities for %d endpoints to %s",
            len(data),
            self._capabilities_path,
        )

    async def load_capabilities(self) -> dict[str, Any]:
        """Load raw capability data from JSON file.

        Returns a dict of endpoint_id -> raw dict. The caller (CapabilityProbe)
        reconstructs CapabilityResult objects.
        """
        # P1-3: Use asyncio.to_thread to avoid blocking the event loop
        return await asyncio.to_thread(self._load_capabilities_sync)

    def _load_capabilities_sync(self) -> dict[str, Any]:
        if not self._capabilities_path.exists():
            return {}
        try:
            data = json.loads(self._capabilities_path.read_text(encoding="utf-8"))
            logger.info(
                "Loaded capabilities for %d endpoints from %s",
                len(data),
                self._capabilities_path,
            )
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load capabilities from %s: %s", self._capabilities_path, e)
            return {}

    def _write_file_sync(self, path: pathlib.Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
