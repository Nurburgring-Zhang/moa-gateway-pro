"""Graceful Shutdown — ensure in-flight requests complete before exit."""

from __future__ import annotations

import asyncio
import logging
import signal
import time

logger = logging.getLogger(__name__)


class GracefulShutdown:
    """Manages graceful shutdown with request draining and timeout."""

    def __init__(self, timeout: int = 30) -> None:
        self._timeout = timeout
        self._shutting_down = False
        self._active_requests = 0
        self._shutdown_event: asyncio.Event | None = None

    @property
    def is_shutting_down(self) -> bool:
        """Whether shutdown has been initiated."""
        return self._shutting_down

    @property
    def active_requests(self) -> int:
        """Number of currently active requests."""
        return self._active_requests

    def increment_requests(self) -> None:
        """Track a new active request."""
        self._active_requests += 1

    def decrement_requests(self) -> None:
        """Track a completed request."""
        self._active_requests -= 1

    async def shutdown(self) -> None:
        """Initiate graceful shutdown — drain active requests with timeout."""
        self._shutting_down = True
        logger.info("Graceful shutdown initiated. Active requests: %d", self._active_requests)

        start = time.time()
        while self._active_requests > 0:
            if time.time() - start > self._timeout:
                logger.warning(
                    "Shutdown timeout (%ds)! Forcing exit with %d active requests",
                    self._timeout,
                    self._active_requests,
                )
                break
            await asyncio.sleep(0.1)

        elapsed = time.time() - start
        logger.info("Graceful shutdown complete in %.1fs", elapsed)

    def setup_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register OS signal handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))
            except NotImplementedError:
                # Windows does not support add_signal_handler
                signal.signal(sig, lambda s, f: None)

    def get_status(self) -> dict:
        """Return current shutdown status."""
        return {
            "shutting_down": self._shutting_down,
            "active_requests": self._active_requests,
            "timeout_seconds": self._timeout,
        }


# Global singleton
graceful = GracefulShutdown()
