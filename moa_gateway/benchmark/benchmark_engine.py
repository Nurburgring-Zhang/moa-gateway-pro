"""moa_gateway.benchmark.benchmark_engine — Performance tier benchmarking engine.

Periodically sends standard requests to each healthy endpoint, measures
latency and token throughput, and assigns a performance tier (S/A/B/C).
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

from .metrics_store import MetricsStore

logger = logging.getLogger(__name__)


class PerformanceTier(Enum):
    """Performance tier classification.

    S: p95 latency < 1s, success rate > 99%
    A: p95 latency < 3s, success rate > 95%
    B: p95 latency < 10s, success rate > 80%
    C: everything else
    """

    S = "S"
    A = "A"
    B = "B"
    C = "C"


@dataclass
class BenchmarkResult:
    """Single benchmark result for one endpoint."""

    endpoint_id: str
    timestamp: datetime
    latency_ms: float
    tokens_per_second: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    success: bool = True
    error: str | None = None


@dataclass
class PerformanceMetrics:
    """Aggregated performance metrics for one endpoint (multiple benchmark rounds)."""

    endpoint_id: str
    tier: PerformanceTier = PerformanceTier.C
    results: list[BenchmarkResult] = field(default_factory=list)

    @property
    def latency_p50(self) -> float:
        latencies = sorted(r.latency_ms for r in self.results if r.success)
        if not latencies:
            return 0.0
        return latencies[len(latencies) // 2]

    @property
    def latency_p95(self) -> float:
        latencies = sorted(r.latency_ms for r in self.results if r.success)
        if not latencies:
            return 0.0
        idx = min(int(len(latencies) * 0.95), len(latencies) - 1)
        return latencies[idx]

    @property
    def avg_tokens_per_second(self) -> float:
        tps_list = [
            r.tokens_per_second
            for r in self.results
            if r.success and r.tokens_per_second is not None
        ]
        if not tps_list:
            return 0.0
        return sum(tps_list) / len(tps_list)

    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        return sum(1 for r in self.results if r.success) / len(self.results)

    def update_tier(self) -> None:
        """Recompute performance tier based on current metrics."""
        p95 = self.latency_p95
        rate = self.success_rate
        if p95 > 0 and p95 < 1000 and rate > 0.99:
            self.tier = PerformanceTier.S
        elif p95 > 0 and p95 < 3000 and rate > 0.95:
            self.tier = PerformanceTier.A
        elif p95 < 10000 and rate > 0.80:
            self.tier = PerformanceTier.B
        else:
            self.tier = PerformanceTier.C

    def summary(self) -> dict[str, Any]:
        """Return summary dict for API responses."""
        return {
            "endpoint_id": self.endpoint_id,
            "tier": self.tier.value,
            "total_benchmarks": len(self.results),
            "success_rate": round(self.success_rate, 4),
            "latency_p50_ms": round(self.latency_p50, 2),
            "latency_p95_ms": round(self.latency_p95, 2),
            "avg_tokens_per_second": round(self.avg_tokens_per_second, 2),
        }


class BenchmarkEngine:
    """Performance benchmark engine — measures endpoint latency and throughput."""

    # Standard benchmark prompts of varying complexity
    BENCHMARK_PROMPTS = [
        # Lightweight: fast response test
        {
            "messages": [{"role": "user", "content": "Say hello in one word."}],
            "max_tokens": 10,
        },
        # Medium: reasoning test
        {
            "messages": [{"role": "user", "content": "What is 15 * 23? Reply with just the number."}],
            "max_tokens": 10,
        },
        # Heavy: generation test
        {
            "messages": [{"role": "user", "content": "Write a haiku about autumn."}],
            "max_tokens": 50,
        },
    ]

    def __init__(
        self,
        health_checker: Any | None = None,
        model_pool: Any | None = None,
        metrics_store: MetricsStore | None = None,
        max_concurrent: int = 5,
        probe_timeout: int = 30,
        interval_seconds: int = 3600,
    ):
        self._health_checker = health_checker
        self._model_pool = model_pool
        self._metrics_store = metrics_store or MetricsStore()
        self._max_concurrent = max_concurrent
        self._probe_timeout = probe_timeout
        self._interval_seconds = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None
        self._metrics: dict[str, PerformanceMetrics] = {}

    # ========== Endpoint access ==========

    def _get_endpoint(self, endpoint_id: str):
        """Retrieve endpoint from model_pool, adapting to its interface."""
        if not self._model_pool:
            return None
        if hasattr(self._model_pool, "get_endpoint"):
            return self._model_pool.get_endpoint(endpoint_id)
        if hasattr(self._model_pool, "endpoints"):
            return self._model_pool.endpoints.get(endpoint_id)
        return None

    def _get_endpoint_config(self, endpoint) -> tuple[str, str, str, str, int]:
        """Extract (api_base, api_key, model, provider, timeout) from endpoint."""
        cfg = getattr(endpoint, "config", endpoint)
        api_base = getattr(cfg, "api_base", "")
        api_key = getattr(cfg, "api_key_runtime", "") or getattr(cfg, "api_key", "")
        model = getattr(cfg, "model", "")
        provider = getattr(cfg, "provider", "")
        timeout = getattr(cfg, "timeout", self._probe_timeout)
        return api_base, api_key, model, provider, timeout

    def _is_benchmarkable(self, endpoint_id: str) -> bool:
        """Check if endpoint is healthy enough to benchmark."""
        if not self._health_checker:
            # No health checker — benchmark if endpoint exists
            return self._get_endpoint(endpoint_id) is not None

        from ..health.health_checker import HealthStatus

        health = self._health_checker.get_health(endpoint_id)
        # Skip DEAD and UNHEALTHY endpoints
        return health.status not in (HealthStatus.DEAD, HealthStatus.UNHEALTHY)

    # ========== Core benchmark logic ==========

    async def benchmark_endpoint(self, endpoint_id: str) -> BenchmarkResult:
        """Benchmark a single endpoint. Returns BenchmarkResult."""
        endpoint = self._get_endpoint(endpoint_id)
        if not endpoint:
            return BenchmarkResult(
                endpoint_id=endpoint_id,
                timestamp=datetime.now(),
                latency_ms=0.0,
                success=False,
                error="endpoint not found",
            )

        if not self._is_benchmarkable(endpoint_id):
            return BenchmarkResult(
                endpoint_id=endpoint_id,
                timestamp=datetime.now(),
                latency_ms=0.0,
                success=False,
                error="endpoint unhealthy or dead",
            )

        api_base, api_key, model, provider, timeout = self._get_endpoint_config(endpoint)
        if not api_base:
            return BenchmarkResult(
                endpoint_id=endpoint_id,
                timestamp=datetime.now(),
                latency_ms=0.0,
                success=False,
                error="no api_base configured",
            )

        # Select a random benchmark prompt
        prompt = random.choice(self.BENCHMARK_PROMPTS)
        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=min(self._probe_timeout, timeout),
                trust_env=False,
            ) as client:
                response_data = await self._send_benchmark_request(
                    client, provider, api_base, api_key, model, prompt
                )
                latency_ms = (time.monotonic() - start_time) * 1000

                # Extract token usage if available
                usage = response_data.get("usage", {}) if response_data else {}
                input_tokens = usage.get("prompt_tokens") if usage else None
                output_tokens = usage.get("completion_tokens") if usage else None

                # Calculate tokens per second
                tps: float | None = None
                if output_tokens and latency_ms > 0:
                    tps = (output_tokens / latency_ms) * 1000

                result = BenchmarkResult(
                    endpoint_id=endpoint_id,
                    timestamp=datetime.now(),
                    latency_ms=latency_ms,
                    tokens_per_second=tps,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    success=True,
                )
                self._record_result(endpoint_id, result)
                logger.debug(
                    "Benchmark %s: %.0fms, %d in / %d out tokens, tps=%.1f",
                    endpoint_id,
                    latency_ms,
                    input_tokens or 0,
                    output_tokens or 0,
                    tps or 0,
                )
                return result

        except httpx.TimeoutException:
            latency_ms = (time.monotonic() - start_time) * 1000
            result = BenchmarkResult(
                endpoint_id=endpoint_id,
                timestamp=datetime.now(),
                latency_ms=latency_ms,
                success=False,
                error="request timeout",
            )
            self._record_result(endpoint_id, result)
            return result
        except httpx.ConnectError as e:
            result = BenchmarkResult(
                endpoint_id=endpoint_id,
                timestamp=datetime.now(),
                latency_ms=0.0,
                success=False,
                error=f"connection error: {e}",
            )
            self._record_result(endpoint_id, result)
            return result
        except Exception as e:
            result = BenchmarkResult(
                endpoint_id=endpoint_id,
                timestamp=datetime.now(),
                latency_ms=0.0,
                success=False,
                error=f"{type(e).__name__}: {e}",
            )
            self._record_result(endpoint_id, result)
            logger.warning("Benchmark %s failed: %s", endpoint_id, e)
            return result

    async def _send_benchmark_request(
        self,
        client: httpx.AsyncClient,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
        prompt: dict,
    ) -> dict[str, Any] | None:
        """Send a benchmark request and return parsed response data."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        messages = prompt["messages"]
        max_tokens = prompt["max_tokens"]

        try:
            if provider == "anthropic":
                url = f"{api_base}/v1/messages"
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
                resp = await client.post(url, json=payload, headers=headers)
            elif provider == "gemini":
                url = f"{api_base}/models/{model}:generateContent"
                params = {"key": api_key} if api_key else {}
                payload = {
                    "contents": [
                        {"parts": [{"text": messages[0]["content"] if messages else "Hi"}]}
                    ]
                }
                resp = await client.post(url, json=payload, params=params, headers=headers)
            elif provider == "cohere":
                url = f"{api_base}/chat"
                payload = {
                    "model": model,
                    "message": messages[0]["content"] if messages else "Hi",
                    "max_tokens": max_tokens,
                }
                resp = await client.post(url, json=payload, headers=headers)
            else:
                # Default: OpenAI-compatible format
                url = f"{api_base}/chat/completions"
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                return resp.json()
            else:
                logger.debug(
                    "Benchmark %s/%s: HTTP %d",
                    provider,
                    model,
                    resp.status_code,
                )
                return None
        except httpx.TimeoutException:
            raise
        except httpx.ConnectError:
            raise
        except Exception as e:
            logger.warning("Benchmark request failed for %s/%s: %s", provider, model, e)
            return None

    def _record_result(self, endpoint_id: str, result: BenchmarkResult) -> None:
        """Record a benchmark result and update tier."""
        if endpoint_id not in self._metrics:
            self._metrics[endpoint_id] = PerformanceMetrics(endpoint_id=endpoint_id)
        metrics = self._metrics[endpoint_id]
        metrics.results.append(result)
        # Keep only last 200 results to bound memory
        if len(metrics.results) > 200:
            metrics.results = metrics.results[-200:]
        metrics.update_tier()

    def remove_endpoint(self, endpoint_id: str) -> None:
        """Remove all benchmark metrics for an endpoint (P2-6)."""
        self._metrics.pop(endpoint_id, None)
        logger.debug("Removed benchmark metrics for endpoint %s", endpoint_id)

    async def benchmark_all(self) -> dict[str, BenchmarkResult]:
        """Benchmark all healthy endpoints concurrently."""
        if not self._model_pool:
            return {}

        # Get all endpoint IDs
        if hasattr(self._model_pool, "endpoints"):
            all_ids = list(self._model_pool.endpoints.keys())
        elif hasattr(self._model_pool, "list_endpoints"):
            all_ids = [e.id if hasattr(e, "id") else e for e in self._model_pool.list_endpoints()]
        else:
            return {}

        # Filter to benchmarkable endpoints
        benchmarkable = [eid for eid in all_ids if self._is_benchmarkable(eid)]
        if not benchmarkable:
            logger.info("No benchmarkable endpoints found")
            return {}

        logger.info("Starting benchmark for %d endpoints", len(benchmarkable))

        # Concurrency-limited execution
        semaphore = asyncio.Semaphore(self._max_concurrent)
        results: dict[str, BenchmarkResult] = {}

        async def _bench_with_limit(eid: str):
            async with semaphore:
                results[eid] = await self.benchmark_endpoint(eid)

        await asyncio.gather(*[_bench_with_limit(eid) for eid in benchmarkable])

        # Persist metrics
        await self._metrics_store.save_metrics(self._metrics)
        logger.info("Benchmark complete: %d endpoints tested", len(results))
        return results

    async def _benchmark_loop(self) -> None:
        """Continuous benchmark loop."""
        while self._running:
            try:
                await self.benchmark_all()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Benchmark loop error: %s", e)
            await asyncio.sleep(self._interval_seconds)

    # ========== Lifecycle ==========

    async def start(self) -> None:
        """Start the benchmark engine."""
        self._running = True
        # Load persisted metrics
        raw_data = await self._metrics_store.load_metrics()
        for eid, m_data in raw_data.items():
            metrics = PerformanceMetrics(endpoint_id=eid)
            metrics.tier = PerformanceTier(m_data.get("tier", "C"))
            for r_data in m_data.get("results", []):
                try:
                    metrics.results.append(
                        BenchmarkResult(
                            endpoint_id=eid,
                            timestamp=datetime.fromisoformat(r_data["timestamp"]),
                            latency_ms=r_data["latency_ms"],
                            tokens_per_second=r_data.get("tokens_per_second"),
                            input_tokens=r_data.get("input_tokens"),
                            output_tokens=r_data.get("output_tokens"),
                            success=r_data.get("success", False),
                            error=r_data.get("error"),
                        )
                    )
                except (KeyError, ValueError) as e:
                    logger.debug("Skipping invalid result for %s: %s", eid, e)
            self._metrics[eid] = metrics
        logger.info("Loaded %d endpoint metrics from storage", len(self._metrics))

        # Start background loop
        self._task = asyncio.create_task(self._benchmark_loop())
        logger.info("BenchmarkEngine started (interval=%ds)", self._interval_seconds)

    async def stop(self) -> None:
        """Stop the benchmark engine."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        # Persist final state
        await self._metrics_store.save_metrics(self._metrics)
        logger.info("BenchmarkEngine stopped")

    # ========== Query API ==========

    def get_tier(self, endpoint_id: str) -> PerformanceTier:
        """Get the performance tier for an endpoint."""
        if endpoint_id in self._metrics:
            return self._metrics[endpoint_id].tier
        return PerformanceTier.C

    def get_metrics(self, endpoint_id: str) -> PerformanceMetrics | None:
        """Get detailed performance metrics for an endpoint."""
        return self._metrics.get(endpoint_id)

    def get_all_metrics(self) -> dict[str, PerformanceMetrics]:
        """Get all performance metrics."""
        return dict(self._metrics)

    def get_endpoints_by_tier(self, tier: PerformanceTier) -> list[str]:
        """Filter endpoints by performance tier."""
        return [eid for eid, m in self._metrics.items() if m.tier == tier]

    def get_tier_summary(self) -> dict[str, Any]:
        """Return aggregate tier summary."""
        tier_counts: dict[str, int] = {}
        for m in self._metrics.values():
            t = m.tier.value
            tier_counts[t] = tier_counts.get(t, 0) + 1
        return {
            "total_tracked": len(self._metrics),
            "tier_counts": tier_counts,
            "endpoints": [m.summary() for m in self._metrics.values()],
        }
