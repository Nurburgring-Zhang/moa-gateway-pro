"""Idle compression scheduler.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Source: ``lib/clacky/idle_compression_timer.rb`` (``Clacky::IdleCompressionTimer``).

The original triggers memory compression after a period of inactivity. The
delay defaults to 266 seconds — deliberately UNDER the providers' 5-minute
prompt-cache TTL so that the compression call itself still hits the existing
prefix cache instead of paying for a cold rebuild.

Port notes (honest Ruby -> Python differences):
- One scheduler instance manages MANY sessions (keyed by session id), where
  the Ruby original is per-agent. The gateway is a multi-tenant server, so a
  per-agent timer object per session would leak threads without a registry.
- Ruby's ``Thread#raise`` interrupts an in-flight compression; CPython cannot
  raise into another thread. ``cancel()`` therefore (a) sets the session's
  cancel event — checked right before the compression work starts — and
  (b) joins a compression that is already running with the same bounded 5 s
  timeout the original uses, so callers still get a consistent history before
  they proceed.
- The compression worker thread is registered under the lock BEFORE it is
  started (verbatim port of the original's race guard) so ``cancel()`` can
  always find it even if it fires immediately.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import compressor as _compressor
from .. import config as _cfg

logger = logging.getLogger(__name__)

__all__ = [
    "IDLE_DELAY_SECONDS",
    "IdleCompressionScheduler",
    "make_idle_compress_task",
    "get_idle_scheduler",
]

# Port of IdleCompressionTimer::IDLE_DELAY. Must stay below the 5-minute
# prompt-cache TTL so idle compression reuses the warm prefix cache.
IDLE_DELAY_SECONDS = 266.0

_CompressTask = Callable[[], Any]
_OnCompress = Callable[[str, bool], None]


class IdleCompressionScheduler:
    """Per-session "wait N seconds of inactivity, then compress" timers.

    Usage (mirrors the original's contract)::

        scheduler = IdleCompressionScheduler(on_compress=notify_ui)
        scheduler.arm(session_id, task)   # after each agent run completes
        scheduler.cancel(session_id)      # when new user input arrives
        scheduler.shutdown()              # application shutdown

    ``task`` is a zero-argument callable returning a truthy value on
    successful compression; see :func:`make_idle_compress_task` for the real
    pipeline task builder.
    """

    def __init__(
        self,
        on_compress: _OnCompress | None = None,
        delay_s: float | None = None,
    ) -> None:
        self._on_compress = on_compress
        # None -> read settings.efficiency.idle_delay_s lazily at arm() time
        # so config changes take effect without recreating the scheduler.
        self._delay_override = delay_s
        self._lock = threading.RLock()
        self._cancel_events: dict[str, threading.Event] = {}
        self._timer_threads: dict[str, threading.Thread] = {}
        self._compress_threads: dict[str, threading.Thread] = {}
        self._shutdown = False

    # -- configuration ---------------------------------------------------

    @property
    def delay_s(self) -> float:
        if self._delay_override is not None:
            return float(self._delay_override)
        try:
            return float(_cfg.get_settings().efficiency.idle_delay_s)
        except Exception:  # settings unavailable (unit context) — use port default
            return IDLE_DELAY_SECONDS

    # -- state queries ---------------------------------------------------

    def is_shutdown(self) -> bool:
        with self._lock:
            return self._shutdown

    def is_active(self, session_id: str) -> bool:
        """True if the timer OR the compression is currently active for the
        session (port of ``active?``)."""
        with self._lock:
            timer = self._timer_threads.get(session_id)
            compress = self._compress_threads.get(session_id)
            return bool(
                (timer is not None and timer.is_alive())
                or (compress is not None and compress.is_alive())
            )

    def is_compressing(self, session_id: str) -> bool:
        """True only while compression work is actually in flight, not during
        the pre-compression idle countdown (port of ``compressing?``)."""
        with self._lock:
            compress = self._compress_threads.get(session_id)
            return bool(compress is not None and compress.is_alive())

    @property
    def active_sessions(self) -> list[str]:
        with self._lock:
            return sorted(
                sid
                for sid in set(self._timer_threads) | set(self._compress_threads)
                if self.is_active(sid)
            )

    # -- lifecycle -------------------------------------------------------

    def arm(self, session_id: str, task: _CompressTask) -> bool:
        """Start (or restart) the idle timer for a session.

        Cancels any existing timer first, then waits ``delay_s`` before
        compressing. Returns False once the scheduler has been shut down.
        """
        self.cancel(session_id)
        with self._lock:
            if self._shutdown:
                return False
            event = threading.Event()
            timer = threading.Thread(
                target=self._timer_loop,
                args=(session_id, task, event),
                name=f"idle-compression-timer-{session_id}",
                daemon=True,
            )
            self._cancel_events[session_id] = event
            self._timer_threads[session_id] = timer
            timer.start()
            logger.debug(
                "efficiency: idle compression armed for %s (%.1fs)",
                session_id,
                self.delay_s,
            )
            return True

    def cancel(self, session_id: str) -> None:
        """Cancel the timer and any in-progress compression for a session.

        Joins a running compression worker (bounded, 5 s like the original)
        OUTSIDE the lock so the caller sees a consistent history before
        starting the next agent run (port of ``cancel``'s join discipline).
        """
        compress_to_join: threading.Thread | None = None
        with self._lock:
            event = self._cancel_events.pop(session_id, None)
            if event is not None:
                event.set()
            self._timer_threads.pop(session_id, None)
            compress_to_join = self._compress_threads.pop(session_id, None)
        if compress_to_join is not None and compress_to_join.is_alive():
            compress_to_join.join(5.0)

    def cancel_all(self) -> None:
        for session_id in list(self._cancel_events):
            self.cancel(session_id)

    def shutdown(self) -> None:
        """Permanently stop this scheduler (port of ``shutdown``): no new
        timers can be armed afterwards."""
        with self._lock:
            self._shutdown = True
            session_ids = list(self._cancel_events)
        for session_id in session_ids:
            self.cancel(session_id)

    # -- internals -------------------------------------------------------

    def _timer_loop(
        self, session_id: str, task: _CompressTask, event: threading.Event
    ) -> None:
        # Interruptible sleep: cancel() sets the event.
        if event.wait(self.delay_s):
            return
        if self.is_shutdown():
            return
        worker: threading.Thread | None = None
        with self._lock:
            # Superseded by a newer arm() for this session? Then exit quietly.
            if self._cancel_events.get(session_id) is not event:
                return
            if self._shutdown:
                return
            # Register the worker under the mutex BEFORE it starts running
            # (verbatim port) so cancel() can always find and join it even if
            # it fires immediately.
            worker = threading.Thread(
                target=self._run_compression,
                args=(session_id, task, event),
                name=f"idle-compression-work-{session_id}",
                daemon=True,
            )
            self._compress_threads[session_id] = worker
        assert worker is not None
        worker.start()
        worker.join()
        with self._lock:
            if self._compress_threads.get(session_id) is worker:
                self._compress_threads.pop(session_id, None)
            if self._cancel_events.get(session_id) is event:
                self._cancel_events.pop(session_id, None)
            if self._timer_threads.get(session_id) is threading.current_thread():
                self._timer_threads.pop(session_id, None)

    def _run_compression(
        self, session_id: str, task: _CompressTask, event: threading.Event
    ) -> None:
        if event.is_set():
            # Cancelled between the countdown and the work starting.
            logger.info("efficiency: idle compression cancelled for %s", session_id)
            self._fire(session_id, False)
            return
        try:
            success = bool(task())
        except Exception as exc:  # noqa: BLE001 — scheduler must never kill its thread
            logger.exception("efficiency: idle compression error for %s: %s", session_id, exc)
            success = False
        self._fire(session_id, success)

    def _fire(self, session_id: str, success: bool) -> None:
        if self._on_compress is None:
            return
        try:
            self._on_compress(session_id, success)
        except Exception as exc:  # noqa: BLE001 — callback faults must not kill the worker
            logger.exception("efficiency: on_compress callback error: %s", exc)


def make_idle_compress_task(
    session_id: str,
    messages_provider: Callable[[str], list[dict[str, Any]] | None],
    compressor: _compressor.SessionCompressor | None = None,
    on_compressed: Callable[[str, _compressor.CompressionResult], None] | None = None,
) -> _CompressTask:
    """Build the REAL idle-compression job for a session.

    ``messages_provider(session_id)`` returns the session's current message
    list (or None when the session is gone); the task runs the full
    Insert-then-Compress pipeline with ``force=True`` (idle gate) and reports
    the :class:`CompressionResult` through ``on_compressed`` when something
    was actually compressed (e.g. to persist the rebuilt history).
    """
    engine = compressor or _compressor.SessionCompressor()

    def task() -> bool:
        messages = messages_provider(session_id)
        if not messages:
            logger.debug("efficiency: idle compression skipped %s (no messages)", session_id)
            return False
        result = engine.compress(list(messages), session_id, force=True)
        if result.compressed and on_compressed is not None:
            on_compressed(session_id, result)
        return result.compressed

    return task


_singleton: IdleCompressionScheduler | None = None
_singleton_lock = threading.Lock()


def get_idle_scheduler() -> IdleCompressionScheduler:
    """Process-wide scheduler instance (lazy; safe to call from tests — the
    singleton holds no config state bound at import time)."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = IdleCompressionScheduler()
        return _singleton
