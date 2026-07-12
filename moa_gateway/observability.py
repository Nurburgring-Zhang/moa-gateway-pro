"""moa_gateway.observability — 可观测性(日志 + 简单 metrics)"""
from __future__ import annotations
import logging
import logging.handlers
import json
import time
from pathlib import Path
from typing import Dict, Any
from collections import defaultdict, deque

from .config import get_settings, DATA_DIR


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
    _instance: "Metrics" = None

    def __init__(self):
        self.counters: Dict[str, int] = defaultdict(int)
        self.timings: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))
        self.errors: Dict[str, int] = defaultdict(int)

    @classmethod
    def instance(cls) -> "Metrics":
        if cls._instance is None:
            cls._instance = Metrics()
        return cls._instance

    def incr(self, name: str, n: int = 1):
        self.counters[name] += n

    def observe(self, name: str, value: float):
        self.timings[name].append(value)

    def error(self, name: str):
        self.errors[name] += 1

    def snapshot(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
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
