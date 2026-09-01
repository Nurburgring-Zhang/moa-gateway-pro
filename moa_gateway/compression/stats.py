"""Compression statistics accounting.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/stats.ts``: per-request stats (token estimate
= chars/4, savings percent, techniques used) plus a process-wide cumulative
store keyed by mode. The store is thread-safe, kept in memory, and can be
persisted to / restored from a JSON file so operators can audit long-term
compression savings.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Char-based token estimate (OmniRoute ``estimateCompressionTokens``)."""
    if not text:
        return 0
    return -(-len(text) // CHARS_PER_TOKEN)  # ceil division


def body_text_length(body: dict[str, Any] | Any) -> int:
    """Total character length of the message payloads in a chat body."""
    if not isinstance(body, dict):
        return len(str(body)) if body is not None else 0
    messages = body.get("messages")
    if not isinstance(messages, list):
        return len(json.dumps(body, ensure_ascii=False))
    total = 0
    for message in messages:
        total += message_text_length(message)
    return total


def message_text_length(message: Any) -> int:
    if not isinstance(message, dict):
        return 0
    content = message.get("content")
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                total += len(part["text"])
        return total
    return 0


def extract_message_text(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "\n".join(part for part in parts if part)
    return ""


@dataclass
class CompressionStats:
    """Per-request compression accounting (mirrors TS ``CompressionStats``)."""

    original_tokens: int
    compressed_tokens: int
    savings_percent: float
    techniques_used: list[str] = field(default_factory=list)
    mode: str = "off"
    timestamp: float = field(default_factory=time.time)
    rules_applied: list[str] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def saved_tokens(self) -> int:
        return max(0, self.original_tokens - self.compressed_tokens)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_tokens": self.original_tokens,
            "compressed_tokens": self.compressed_tokens,
            "saved_tokens": self.saved_tokens,
            "savings_percent": self.savings_percent,
            "techniques_used": list(self.techniques_used),
            "rules_applied": list(self.rules_applied),
            "mode": self.mode,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
        }


def create_compression_stats(
    original_body: dict[str, Any],
    compressed_body: dict[str, Any],
    mode: str,
    techniques_used: list[str],
    rules_applied: list[str] | None = None,
    duration_ms: float = 0.0,
) -> CompressionStats:
    original_tokens = estimate_tokens(json.dumps(original_body, ensure_ascii=False))
    compressed_tokens = estimate_tokens(json.dumps(compressed_body, ensure_ascii=False))
    savings = (
        round((original_tokens - compressed_tokens) / original_tokens * 10000) / 100
        if original_tokens > 0
        else 0.0
    )
    return CompressionStats(
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        savings_percent=savings,
        techniques_used=list(dict.fromkeys(techniques_used)),
        mode=mode,
        rules_applied=list(dict.fromkeys(rules_applied or [])),
        duration_ms=duration_ms,
    )


class CompressionStatsStore:
    """Thread-safe cumulative per-mode stats (in-memory + JSON persistence)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._modes: dict[str, dict[str, Any]] = {}
        self._started_at = time.time()

    def _bucket(self, mode: str) -> dict[str, Any]:
        bucket = self._modes.get(mode)
        if bucket is None:
            bucket = {
                "calls": 0,
                "compressed_calls": 0,
                "fallback_calls": 0,
                "original_chars": 0,
                "compressed_chars": 0,
                "saved_chars": 0,
                "original_tokens": 0,
                "compressed_tokens": 0,
                "total_duration_ms": 0.0,
                "techniques": {},
            }
            self._modes[mode] = bucket
        return bucket

    def record(
        self,
        mode: str,
        original_chars: int,
        compressed_chars: int,
        compressed: bool,
        techniques_used: list[str] | None = None,
        original_tokens: int = 0,
        compressed_tokens: int = 0,
        duration_ms: float = 0.0,
        fallback: bool = False,
    ) -> None:
        with self._lock:
            bucket = self._bucket(mode)
            bucket["calls"] += 1
            bucket["original_chars"] += max(0, original_chars)
            bucket["compressed_chars"] += max(0, compressed_chars)
            bucket["saved_chars"] += max(0, original_chars - compressed_chars)
            bucket["original_tokens"] += max(0, original_tokens)
            bucket["compressed_tokens"] += max(0, compressed_tokens)
            bucket["total_duration_ms"] += max(0.0, duration_ms)
            if compressed:
                bucket["compressed_calls"] += 1
            if fallback:
                bucket["fallback_calls"] += 1
            for technique in techniques_used or []:
                bucket["techniques"][technique] = bucket["techniques"].get(technique, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            modes: dict[str, Any] = {}
            for mode, bucket in self._modes.items():
                original = bucket["original_chars"]
                saved = bucket["saved_chars"]
                modes[mode] = {
                    **bucket,
                    "avg_savings_percent": round(saved / original * 10000) / 100
                    if original > 0
                    else 0.0,
                    "avg_duration_ms": round(bucket["total_duration_ms"] / bucket["calls"], 2)
                    if bucket["calls"]
                    else 0.0,
                }
            return {
                "started_at": self._started_at,
                "total_calls": sum(b["calls"] for b in self._modes.values()),
                "total_saved_chars": sum(b["saved_chars"] for b in self._modes.values()),
                "modes": modes,
            }

    def reset(self) -> None:
        with self._lock:
            self._modes.clear()
            self._started_at = time.time()

    def persist(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = self.snapshot()
        path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info("compression stats persisted to %s", path)

    def load(self, path: str | Path) -> bool:
        path = Path(path)
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("failed to load compression stats from %s: %s", path, exc)
            return False
        modes = data.get("modes")
        if not isinstance(modes, dict):
            return False
        with self._lock:
            for mode, bucket in modes.items():
                if not isinstance(bucket, dict):
                    continue
                target = self._bucket(str(mode))
                for key in (
                    "calls",
                    "compressed_calls",
                    "fallback_calls",
                    "original_chars",
                    "compressed_chars",
                    "saved_chars",
                    "original_tokens",
                    "compressed_tokens",
                ):
                    value = bucket.get(key, 0)
                    if isinstance(value, int):
                        target[key] = value
                duration = bucket.get("total_duration_ms", 0.0)
                if isinstance(duration, (int, float)):
                    target["total_duration_ms"] = float(duration)
                techniques = bucket.get("techniques")
                if isinstance(techniques, dict):
                    for name, count in techniques.items():
                        if isinstance(count, int):
                            target["techniques"][str(name)] = count
        return True


_store = CompressionStatsStore()


def get_stats_store() -> CompressionStatsStore:
    """Process-wide cumulative stats store (single instance)."""
    return _store
