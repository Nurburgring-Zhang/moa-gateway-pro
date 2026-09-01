"""Cache hit-rate and efficiency metrics.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Source: ``lib/clacky/agent/cost_tracker.rb`` (cache token statistics:
``cache_creation_input_tokens`` / ``cache_read_input_tokens``) — reworked into
a thread-safe, process-wide metrics collector that the gateway's HTTP surface
can expose.

Definitions used here (documented because "hit rate" is ambiguous):
- a *usage report* is one provider response carrying token accounting;
- a request is a **cache hit** when its ``cache_read_input_tokens > 0``
  (the provider served part of the prompt from its prompt cache);
- ``hit_rate = cache_hit_requests / usage_reports`` over the process lifetime
  (or since the last ``reset()``).
"""

from __future__ import annotations

import threading
from typing import Any

__all__ = ["EfficiencyMetrics", "get_metrics"]


class EfficiencyMetrics:
    """Thread-safe counters for the /v1/efficiency surface."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset_locked()

    # -- recording -------------------------------------------------------

    def record_prepare(self, messages_count: int, markers_applied: int) -> None:
        with self._lock:
            self.prepare_calls += 1
            self.messages_prepared += int(messages_count)
            self.markers_applied += int(markers_applied)

    def record_usage(self, usage: dict[str, Any] | None) -> None:
        """Record one provider usage payload (Anthropic/OpenAI-style keys).

        Counts a cache hit when ``cache_read_input_tokens > 0`` and
        accumulates cache read/write token totals.
        """
        if not isinstance(usage, dict):
            return
        try:
            read_tokens = int(usage.get("cache_read_input_tokens") or 0)
        except (TypeError, ValueError):
            read_tokens = 0
        try:
            write_tokens = int(usage.get("cache_creation_input_tokens") or 0)
        except (TypeError, ValueError):
            write_tokens = 0
        try:
            input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            input_tokens = 0
        with self._lock:
            self.usage_reports += 1
            self.total_input_tokens += input_tokens
            self.total_cache_read_tokens += max(read_tokens, 0)
            self.total_cache_write_tokens += max(write_tokens, 0)
            if read_tokens > 0:
                self.cache_hit_requests += 1

    def record_compression(
        self,
        compressed: bool,
        tokens_before: int = 0,
        tokens_after: int = 0,
        archived_messages: int = 0,
    ) -> None:
        with self._lock:
            self.compression_attempts += 1
            if compressed:
                self.compressions_done += 1
                self.tokens_saved += max(int(tokens_before) - int(tokens_after), 0)
                self.messages_archived += int(archived_messages)

    # -- queries ----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """Consistent read of every counter plus derived rates."""
        with self._lock:
            usage = self.usage_reports
            hit_rate = (self.cache_hit_requests / usage) if usage else 0.0
            attempts = self.compression_attempts
            compression_rate = (self.compressions_done / attempts) if attempts else 0.0
            return {
                "prepare_calls": self.prepare_calls,
                "messages_prepared": self.messages_prepared,
                "markers_applied": self.markers_applied,
                "usage_reports": usage,
                "cache_hit_requests": self.cache_hit_requests,
                "cache_hit_rate": round(hit_rate, 6),
                "total_input_tokens": self.total_input_tokens,
                "total_cache_read_tokens": self.total_cache_read_tokens,
                "total_cache_write_tokens": self.total_cache_write_tokens,
                "compression_attempts": attempts,
                "compressions_done": self.compressions_done,
                "compression_rate": round(compression_rate, 6),
                "tokens_saved": self.tokens_saved,
                "messages_archived": self.messages_archived,
            }

    def reset(self) -> None:
        with self._lock:
            self.reset_locked()

    def reset_locked(self) -> None:
        self.prepare_calls = 0
        self.messages_prepared = 0
        self.markers_applied = 0
        self.usage_reports = 0
        self.cache_hit_requests = 0
        self.total_input_tokens = 0
        self.total_cache_read_tokens = 0
        self.total_cache_write_tokens = 0
        self.compression_attempts = 0
        self.compressions_done = 0
        self.tokens_saved = 0
        self.messages_archived = 0


_singleton: EfficiencyMetrics | None = None
_singleton_lock = threading.Lock()


def get_metrics() -> EfficiencyMetrics:
    """Process-wide metrics instance."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = EfficiencyMetrics()
        return _singleton
