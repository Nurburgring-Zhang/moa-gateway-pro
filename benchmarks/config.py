"""Benchmark configuration."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BenchmarkConfig:
    """Benchmark execution parameters."""

    base_url: str = "http://127.0.0.1:8910"
    concurrency: int = 10
    duration_seconds: int = 10
    warmup_requests: int = 5
    timeout: float = 30.0
    # Auth
    api_key: str = "demo-key-please-change"
    admin_username: str = "admin"
    admin_password: str = "BenchmarkPass#2024!"
    # Scenarios to run (empty = all)
    scenarios: list[str] = field(default_factory=list)
