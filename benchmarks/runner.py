"""benchmarks/runner.py — Async load test runner built on httpx + asyncio."""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, List

import httpx

from .config import BenchmarkConfig


@dataclass
class BenchmarkResult:
    """Results from a single benchmark scenario run."""

    scenario: str
    total_requests: int
    successful: int
    failed: int
    duration_seconds: float
    rps: float
    latency_avg_ms: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    latency_min_ms: float
    latency_max_ms: float
    errors: dict[str, int] = field(default_factory=dict)
    status_codes: dict[int, int] = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        if self.total_requests == 0:
            return 0.0
        return self.successful / self.total_requests * 100


ScenarioFn = Callable[[httpx.AsyncClient], Coroutine[Any, Any, None]]


class LoadRunner:
    """High-performance async load test executor."""

    def __init__(self, config: BenchmarkConfig):
        self.config = config

    async def run_scenario(
        self, name: str, scenario_fn: ScenarioFn
    ) -> BenchmarkResult:
        """Execute a single benchmark scenario with specified concurrency & duration."""
        latencies: List[float] = []
        errors: dict[str, int] = {}
        status_codes: dict[int, int] = {}
        success_count = 0
        fail_count = 0
        stop_event = asyncio.Event()

        async def worker(client: httpx.AsyncClient):
            nonlocal success_count, fail_count
            while not stop_event.is_set():
                req_start = time.perf_counter()
                try:
                    await scenario_fn(client)
                    elapsed_ms = (time.perf_counter() - req_start) * 1000
                    latencies.append(elapsed_ms)
                    success_count += 1
                except httpx.HTTPStatusError as e:
                    fail_count += 1
                    code = e.response.status_code
                    status_codes[code] = status_codes.get(code, 0) + 1
                    err_key = f"HTTP_{code}"
                    errors[err_key] = errors.get(err_key, 0) + 1
                except Exception as e:
                    fail_count += 1
                    err_type = type(e).__name__
                    errors[err_type] = errors.get(err_type, 0) + 1

        async def timer():
            await asyncio.sleep(self.config.duration_seconds)
            stop_event.set()

        # Warmup
        async with httpx.AsyncClient(
            base_url=self.config.base_url, timeout=self.config.timeout
        ) as client:
            for _ in range(self.config.warmup_requests):
                try:
                    await scenario_fn(client)
                except Exception:
                    pass

        # Main run
        start_time = time.perf_counter()
        async with httpx.AsyncClient(
            base_url=self.config.base_url,
            timeout=self.config.timeout,
            limits=httpx.Limits(
                max_connections=self.config.concurrency + 10,
                max_keepalive_connections=self.config.concurrency,
            ),
        ) as client:
            workers = [
                asyncio.create_task(worker(client))
                for _ in range(self.config.concurrency)
            ]
            timer_task = asyncio.create_task(timer())
            await timer_task
            # Allow in-flight requests to finish (max 2s grace)
            await asyncio.sleep(0.1)
            for w in workers:
                w.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

        total_time = time.perf_counter() - start_time
        total = success_count + fail_count

        # Percentile calculation
        if latencies:
            sorted_lat = sorted(latencies)
            n = len(sorted_lat)
            p50 = sorted_lat[int(n * 0.50)]
            p95 = sorted_lat[min(int(n * 0.95), n - 1)]
            p99 = sorted_lat[min(int(n * 0.99), n - 1)]
            avg = statistics.mean(sorted_lat)
            lat_min = sorted_lat[0]
            lat_max = sorted_lat[-1]
        else:
            p50 = p95 = p99 = avg = lat_min = lat_max = 0.0

        return BenchmarkResult(
            scenario=name,
            total_requests=total,
            successful=success_count,
            failed=fail_count,
            duration_seconds=total_time,
            rps=total / total_time if total_time > 0 else 0,
            latency_avg_ms=avg,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            latency_p99_ms=p99,
            latency_min_ms=lat_min,
            latency_max_ms=lat_max,
            errors=errors,
            status_codes=status_codes,
        )
