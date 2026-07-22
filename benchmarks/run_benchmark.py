"""
MoA Gateway Pro -- Benchmark Runner (main entry point).

Usage:
    python -m benchmarks.run_benchmark [OPTIONS]

Options:
    --concurrency N     Number of concurrent workers (default: 10)
    --duration N        Duration in seconds per scenario (default: 10)
    --scenario NAME     Run specific scenario only (default: all)
    --base-url URL      Target server URL (default: http://127.0.0.1:8910)
    --output PATH       Output report path (default: PERFORMANCE_BASELINE.md)
    --json              Also output results as JSON
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from .config import BenchmarkConfig
from .report import generate_report
from .runner import BenchmarkResult, LoadRunner
from .scenarios.admin import admin_stats
from .scenarios.auth import login_flow
from .scenarios.chat import chat_completions
from .scenarios.health import health_check, health_detailed
from .scenarios.models import list_models, list_models_no_auth

# Registry of all benchmark scenarios
SCENARIOS = {
    "health_check": health_check,
    "health_detailed": health_detailed,
    "list_models": list_models,
    "list_models_no_auth": list_models_no_auth,
    "login_flow": login_flow,
    "chat_completions": chat_completions,
    "admin_stats": admin_stats,
}

# Default scenario subset for quick benchmarks
DEFAULT_SCENARIOS = [
    "health_check",
    "health_detailed",
    "list_models",
    "list_models_no_auth",
    "login_flow",
    "admin_stats",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MoA Gateway Pro -- Performance Benchmark Suite"
    )
    parser.add_argument(
        "--concurrency", type=int, default=10, help="Concurrent workers (default: 10)"
    )
    parser.add_argument(
        "--duration", type=int, default=10, help="Duration per scenario in seconds (default: 10)"
    )
    parser.add_argument(
        "--scenario", type=str, default="", help="Run specific scenario (default: all)"
    )
    parser.add_argument(
        "--base-url", type=str, default="http://127.0.0.1:8910", help="Target URL"
    )
    parser.add_argument(
        "--output",
        type=str,
        default="PERFORMANCE_BASELINE.md",
        help="Output report path",
    )
    parser.add_argument(
        "--json", action="store_true", help="Also output JSON results"
    )
    return parser.parse_args()


async def run_all(config: BenchmarkConfig, scenario_names: list[str]) -> list[BenchmarkResult]:
    """Run all specified scenarios sequentially."""
    results = []
    for name in scenario_names:
        if name not in SCENARIOS:
            print(f"  [SKIP] Unknown scenario: {name}")
            continue
        print(f"  [+] Running: {name} (concurrency={config.concurrency}, duration={config.duration_seconds}s)")
        runner = LoadRunner(config)
        result = await runner.run_scenario(name, SCENARIOS[name])
        results.append(result)
        print(
            f"      -> {result.total_requests} reqs | "
            f"RPS={result.rps:.1f} | "
            f"P50={result.latency_p50_ms:.1f}ms | "
            f"P95={result.latency_p95_ms:.1f}ms | "
            f"Success={result.success_rate:.1f}%"
        )
    return results


def main():
    args = parse_args()
    config = BenchmarkConfig(
        base_url=args.base_url,
        concurrency=args.concurrency,
        duration_seconds=args.duration,
    )

    # Determine scenarios
    if args.scenario:
        scenario_names = [s.strip() for s in args.scenario.split(",")]
    else:
        scenario_names = DEFAULT_SCENARIOS

    print("=" * 60)
    print("  MoA Gateway Pro -- Performance Benchmark")
    print("=" * 60)
    print(f"  Target:      {config.base_url}")
    print(f"  Concurrency: {config.concurrency}")
    print(f"  Duration:    {config.duration_seconds}s per scenario")
    print(f"  Scenarios:   {len(scenario_names)}")
    print("=" * 60)
    print()

    start = time.time()
    results = asyncio.run(run_all(config, scenario_names))
    elapsed = time.time() - start

    print()
    print(f"  Total benchmark time: {elapsed:.1f}s")
    print()

    # Generate report
    config_summary = {
        "concurrency": config.concurrency,
        "duration": config.duration_seconds,
        "base_url": config.base_url,
    }
    report = generate_report(results, config_summary)

    # Write report
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path(r"D:\WORKSPACE\moa-gateway-pro") / output_path
    output_path.write_text(report, encoding="utf-8")
    print(f"  Report saved: {output_path}")

    # Optional JSON
    if args.json:
        json_path = output_path.with_suffix(".json")
        json_data = []
        for r in results:
            json_data.append({
                "scenario": r.scenario,
                "total_requests": r.total_requests,
                "successful": r.successful,
                "failed": r.failed,
                "duration_seconds": round(r.duration_seconds, 2),
                "rps": round(r.rps, 2),
                "latency_avg_ms": round(r.latency_avg_ms, 2),
                "latency_p50_ms": round(r.latency_p50_ms, 2),
                "latency_p95_ms": round(r.latency_p95_ms, 2),
                "latency_p99_ms": round(r.latency_p99_ms, 2),
                "latency_min_ms": round(r.latency_min_ms, 2),
                "latency_max_ms": round(r.latency_max_ms, 2),
                "success_rate": round(r.success_rate, 2),
                "errors": r.errors,
            })
        json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  JSON saved:   {json_path}")

    print()
    print("  Done!")


if __name__ == "__main__":
    main()
