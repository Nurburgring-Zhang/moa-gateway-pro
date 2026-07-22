"""Smart Retry — exponential backoff with jitter."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps

logger = logging.getLogger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""

    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 30.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)
    retryable_status_codes: tuple[int, ...] = (429, 500, 502, 503, 504)


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    """Calculate backoff delay with exponential growth and optional jitter."""
    delay = config.base_delay * (config.exponential_base**attempt)
    delay = min(delay, config.max_delay)
    if config.jitter:
        delay *= 0.5 + random.random()  # +/- 50% jitter
    return delay


def retry_async(config: RetryConfig | None = None):
    """Async retry decorator with exponential backoff."""
    _config = config or RetryConfig()

    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception: Exception | None = None
            for attempt in range(_config.max_attempts):
                try:
                    return await func(*args, **kwargs)
                except _config.retryable_exceptions as e:
                    last_exception = e
                    if attempt < _config.max_attempts - 1:
                        delay = calculate_delay(attempt, _config)
                        logger.warning(
                            "Retry %d/%d for %s: %s, waiting %.2fs",
                            attempt + 1,
                            _config.max_attempts,
                            func.__name__,
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)
            raise last_exception  # type: ignore[misc]

        return wrapper

    return decorator
