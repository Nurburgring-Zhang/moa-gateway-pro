"""moa_gateway.observability — 可观测性(日志 + 简单 metrics)"""
from __future__ import annotations

import json
import logging
import logging.handlers
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from .config import DATA_DIR


class JsonFormatter(logging.Formatter):
    """JSON 日志格式化器"""
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
                  + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k in ("request_id", "model", "preset", "latency_ms", "cost"):
            if hasattr(record, k):
                payload[k] = getattr(record, k)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(level: str = "INFO", log_dir: str = "data/logs",
                  json_mode: bool = False) -> None:
    """初始化全局日志"""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    # 清掉已有 handler
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = JsonFormatter() if json_mode else logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = DATA_DIR.parent / log_path
    log_path.mkdir(parents=True, exist_ok=True)
    file_h = logging.handlers.RotatingFileHandler(
        log_path / "gateway.log",
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8"
    )
    file_h.setFormatter(fmt)
    root.addHandler(file_h)

    # 屏蔽喧闹的库
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ========== 简易 in-memory metrics ==========
class Metrics:
    """进程内指标(够用即可,生产可换 Prometheus)"""
    _instance: Metrics = None

    def __init__(self):
        self.counters: dict[str, int] = defaultdict(int)
        self.timings: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self.errors: dict[str, int] = defaultdict(int)

    @classmethod
    def instance(cls) -> Metrics:
        if cls._instance is None:
            cls._instance = Metrics()
        return cls._instance

    def incr(self, name: str, n: int = 1):
        self.counters[name] += n

    def observe(self, name: str, value: float):
        self.timings[name].append(value)

    def error(self, name: str):
        self.errors[name] += 1

    def snapshot(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "counters": dict(self.counters),
            "errors": dict(self.errors),
            "timings": {},
        }
        for k, vs in self.timings.items():
            if not vs:
                continue
            v = list(vs)
            v_sorted = sorted(v)
            n = len(v)
            out["timings"][k] = {
                "n": n,
                "avg": round(sum(v) / n, 2),
                "p50": v_sorted[n // 2],
                "p95": v_sorted[int(n * 0.95)] if n > 1 else v_sorted[0],
                "p99": v_sorted[int(n * 0.99)] if n > 1 else v_sorted[0],
                "max": v_sorted[-1],
            }
        return out


# ========== Prometheus Metrics (生产可观测) ==========
try:
    from prometheus_client import (
        Counter, Histogram, Gauge,
        generate_latest, CONTENT_TYPE_LATEST,
        REGISTRY,
    )
    _PROM_OK = True
except ImportError:
    _PROM_OK = False
    # 定义 dummy 让 import 不挂
    class _Dummy:
        def labels(self, **kw): return self
        def inc(self, n=1): pass
        def observe(self, v): pass
        def set(self, v): pass
    Counter = Histogram = Gauge = lambda *a, **kw: _Dummy()
    generate_latest = lambda x: b""
    CONTENT_TYPE_LATEST = "text/plain; version=0.0.4"
    REGISTRY = None


# 全局 metric
chat_requests_total = Counter(
    "moa_chat_requests_total",
    "Total chat completion requests",
    ["model", "status"],
)
chat_latency_seconds = Histogram(
    "moa_chat_latency_seconds",
    "Chat completion latency in seconds",
    ["model"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)
endpoint_health_gauge = Gauge(
    "moa_endpoint_health",
    "Endpoint health (1=healthy, 0=unhealthy, 0.5=in_breaker)",
    ["endpoint_id"],
)
rate_limit_blocked_total = Counter(
    "moa_rate_limit_blocked_total",
    "Total requests blocked by rate limit",
    ["reason"],
)
moa_executions_total = Counter(
    "moa_moa_executions_total",
    "Total MoA executions",
    ["preset"],
)
capability_calls_total = Counter(
    "moa_capability_calls_total",
    "Total capability endpoint calls",
    ["capability", "status"],
)


def prometheus_response():
    """Generate Prometheus exposition format response"""
    if not _PROM_OK:
        return b"# prometheus_client not installed\n", 200, {"Content-Type": CONTENT_TYPE_LATEST}
    return generate_latest(REGISTRY), 200, {"Content-Type": CONTENT_TYPE_LATEST}


def record_chat(model: str, status: int, latency_s: float):
    """Record a chat completion for Prometheus"""
    status_label = "2xx" if 200 <= status < 300 else ("4xx" if 400 <= status < 500 else "5xx")
    chat_requests_total.labels(model=model, status=status_label).inc()
    chat_latency_seconds.labels(model=model).observe(latency_s)


def record_capability(name: str, status: int):
    """Record a capability call"""
    status_label = "ok" if 200 <= status < 300 else "err"
    capability_calls_total.labels(capability=name, status=status_label).inc()


def record_rate_limit_block(reason: str = "rpm"):
    rate_limit_blocked_total.labels(reason=reason).inc()


def record_moa_exec(preset: str):
    moa_executions_total.labels(preset=preset).inc()
