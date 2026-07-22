"""Benchmark report generator."""
from __future__ import annotations

import platform
import sys
from datetime import datetime
from typing import List

from .runner import BenchmarkResult


def generate_report(
    results: List[BenchmarkResult],
    config_summary: dict,
) -> str:
    """Generate a Markdown performance baseline report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    py_ver = sys.version.split()[0]
    os_info = f"{platform.system()} {platform.release()}"

    lines = [
        "# MoA Gateway Pro -- 性能基线报告",
        "",
        f"> 生成时间: {now}",
        "",
        "## 测试环境",
        "",
        f"- **OS**: {os_info}",
        f"- **Python**: {py_ver}",
        f"- **并发数**: {config_summary.get('concurrency', 'N/A')}",
        f"- **持续时间**: {config_summary.get('duration', 'N/A')}s",
        f"- **目标URL**: {config_summary.get('base_url', 'N/A')}",
        f"- **Uvicorn Workers**: 1",
        f"- **Database**: SQLite (本地)",
        "",
        "## 基线结果",
        "",
        "| 场景 | 并发 | 持续 | 总请求 | RPS | P50(ms) | P95(ms) | P99(ms) | Min(ms) | Max(ms) | 成功率 |",
        "|------|------|------|--------|-----|---------|---------|---------|---------|---------|--------|",
    ]

    for r in results:
        lines.append(
            f"| {r.scenario} | {config_summary.get('concurrency', '')} "
            f"| {r.duration_seconds:.1f}s "
            f"| {r.total_requests} "
            f"| {r.rps:.1f} "
            f"| {r.latency_p50_ms:.2f} "
            f"| {r.latency_p95_ms:.2f} "
            f"| {r.latency_p99_ms:.2f} "
            f"| {r.latency_min_ms:.2f} "
            f"| {r.latency_max_ms:.2f} "
            f"| {r.success_rate:.1f}% |"
        )

    lines.extend([
        "",
        "## 详细结果",
        "",
    ])

    for r in results:
        lines.extend([
            f"### {r.scenario}",
            "",
            f"- **总请求**: {r.total_requests}",
            f"- **成功**: {r.successful} | **失败**: {r.failed}",
            f"- **RPS**: {r.rps:.1f}",
            f"- **延迟均值**: {r.latency_avg_ms:.2f} ms",
            f"- **P50**: {r.latency_p50_ms:.2f} ms | **P95**: {r.latency_p95_ms:.2f} ms | **P99**: {r.latency_p99_ms:.2f} ms",
            f"- **成功率**: {r.success_rate:.1f}%",
        ])
        if r.errors:
            lines.append(f"- **错误分布**: {r.errors}")
        lines.append("")

    # Bottleneck analysis
    lines.extend([
        "## 瓶颈分析",
        "",
    ])

    slowest = max(results, key=lambda r: r.latency_p99_ms) if results else None
    if slowest:
        lines.append(f"- **最慢场景**: `{slowest.scenario}` (P99={slowest.latency_p99_ms:.2f}ms)")

    high_error = [r for r in results if r.success_rate < 99.0]
    if high_error:
        for r in high_error:
            lines.append(f"- **高错误率**: `{r.scenario}` (成功率={r.success_rate:.1f}%, 错误={r.errors})")
    else:
        lines.append("- 所有场景成功率 >= 99%，无明显瓶颈")

    lines.extend([
        "",
        "## 优化建议",
        "",
        "1. **连接池**: 生产环境建议使用 PostgreSQL + 连接池（已支持）",
        "2. **Worker数**: 生产环境设置 workers=CPU核心数 (uvicorn --workers N)",
        "3. **缓存**: /v1/models 结果可加 TTL 缓存（模型列表变动低频）",
        "4. **异步IO**: 确保所有数据库操作使用异步驱动",
        "5. **负载均衡**: 高并发下建议前置 Nginx/Caddy 反向代理",
        "",
        "---",
        "",
        "*本报告由 `benchmarks/run_benchmark.py` 自动生成，可集成到 CI/CD 流水线进行回归检测。*",
        "",
    ])

    return "\n".join(lines)
