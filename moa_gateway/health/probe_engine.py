"""moa_gateway.health.probe_engine — API probe engine.

Sends lightweight test requests to detect endpoint availability.
Adjusts probe frequency based on endpoint health status.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .health_checker import HealthChecker, HealthStatus

logger = logging.getLogger(__name__)


class ProbeEngine:
    """API probe engine — sends lightweight test requests to detect availability."""

    PROBE_MESSAGES = [
        {"role": "user", "content": "Hi"},
        {"role": "user", "content": "Hello"},
        {"role": "user", "content": "1+1=?"},
    ]

    # Probe frequency configuration (seconds)
    PROBE_INTERVALS = {
        "new": 600,           # newly discovered API: every 10 minutes
        "healthy": 1800,      # healthy API: every 30 minutes
        "degraded": 300,      # degraded API: every 5 minutes
        "unhealthy": 180,     # unhealthy API: every 3 minutes
        "dead": 3600,         # dead API: every hour (in case it comes back)
    }

    def __init__(
        self,
        health_checker: HealthChecker,
        model_pool: Any | None = None,
        probe_timeout: int = 15,
        probe_intervals: dict[str, int] | None = None,
    ):
        self._health_checker = health_checker
        self._model_pool = model_pool
        self._probe_timeout = probe_timeout
        self._running = False
        self._tasks: dict[str, asyncio.Task] = {}  # endpoint_id -> probe task
        # P1-2: Allow config-injected probe intervals (fall back to class defaults)
        self._probe_intervals: dict[str, int] = (
            dict(self.PROBE_INTERVALS) if probe_intervals is None else probe_intervals
        )

    def _get_endpoint(self, endpoint_id: str):
        """Retrieve endpoint from model_pool, adapting to its interface."""
        if not self._model_pool:
            return None
        # ModelPool stores endpoints in a dict; also check for get_endpoint method
        if hasattr(self._model_pool, "get_endpoint"):
            return self._model_pool.get_endpoint(endpoint_id)
        if hasattr(self._model_pool, "endpoints"):
            return self._model_pool.endpoints.get(endpoint_id)
        return None

    async def probe_endpoint(self, endpoint_id: str) -> bool:
        """Probe a single endpoint. Returns True if healthy."""
        endpoint = self._get_endpoint(endpoint_id)
        if not endpoint:
            return False

        # Extract config attributes (ModelEndpoint has .config with attributes)
        cfg = getattr(endpoint, "config", endpoint)
        api_base = getattr(cfg, "api_base", "")
        api_key = getattr(cfg, "api_key_runtime", "") or getattr(cfg, "api_key", "")
        model = getattr(cfg, "model", "")
        provider = getattr(cfg, "provider", "")
        timeout = getattr(cfg, "timeout", self._probe_timeout)

        if not api_base:
            return False

        health = self._health_checker.get_health(endpoint_id)
        start_time = time.monotonic()

        try:
            async with httpx.AsyncClient(
                timeout=min(self._probe_timeout, timeout),
                trust_env=False,
            ) as client:
                success = await self._send_probe(
                    client, provider, api_base, api_key, model
                )
                latency_ms = (time.monotonic() - start_time) * 1000

                if success:
                    health.record_success(latency_ms)
                    return True
                else:
                    health.record_failure(
                        f"Probe returned non-200 for {provider}/{model}",
                        error_type="probe_failed",
                    )
                    return False
        except httpx.TimeoutException:
            health.record_failure("Request timeout", error_type="timeout")
            return False
        except httpx.ConnectError as e:
            health.record_failure(f"Connection error: {e}", error_type="connect_error")
            return False
        except httpx.HTTPError as e:
            health.record_failure(f"HTTP error: {e}", error_type="http_error")
            return False
        except Exception as e:
            health.record_failure(
                f"Unexpected: {type(e).__name__}: {e}", error_type="unexpected"
            )
            return False

    async def _send_probe(
        self,
        client: httpx.AsyncClient,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
    ) -> bool:
        """Send a probe request appropriate for the provider type."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Select a probe message deterministically
        msg_idx = hash(model) % len(self.PROBE_MESSAGES)
        messages = [self.PROBE_MESSAGES[msg_idx]]

        try:
            if provider == "anthropic":
                # Anthropic uses a different API format
                url = f"{api_base}/v1/messages"
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": 5,
                }
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
                resp = await client.post(url, json=payload, headers=headers)
            elif provider == "gemini":
                # Google Gemini format
                url = f"{api_base}/models/{model}:generateContent"
                params = {"key": api_key} if api_key else {}
                payload = {"contents": [{"parts": [{"text": "Hi"}]}]}
                resp = await client.post(
                    url, json=payload, params=params, headers=headers
                )
            elif provider == "cohere":
                # Cohere format
                url = f"{api_base}/chat"
                payload = {"model": model, "message": "Hi", "max_tokens": 5}
                resp = await client.post(url, json=payload, headers=headers)
            else:
                # Default: OpenAI-compatible format
                url = f"{api_base}/chat/completions"
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": 5,
                    "stream": False,
                }
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                return True
            else:
                # Record the HTTP error in health
                logger.debug(
                    "Probe %s/%s: HTTP %d",
                    provider, model, resp.status_code,
                )
                return False
        except httpx.TimeoutException:
            raise
        except httpx.ConnectError:
            raise
        except Exception as e:
            logger.warning("Probe failed for %s/%s: %s", provider, model, e)
            return False

    def get_probe_interval(self, endpoint_id: str) -> int:
        """Return probe interval based on endpoint health status."""
        health = self._health_checker.get_health(endpoint_id)
        # New endpoint: no results yet
        if (
            health.consecutive_failures == 0
            and health.success_rate == 1.0
            and health.last_success_at is None
            and health.total_probes == 0
        ):
            return self._probe_intervals.get("new", 600)
        return self._probe_intervals.get(health.status.value, 1800)

    async def _probe_loop(self, endpoint_id: str) -> None:
        """Continuous probe loop for a single endpoint."""
        while self._running:
            try:
                await self.probe_endpoint(endpoint_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Probe loop error for %s: %s", endpoint_id, e)
            interval = self.get_probe_interval(endpoint_id)
            await asyncio.sleep(interval)

    async def start_monitoring(self, endpoint_id: str) -> None:
        """Start monitoring an endpoint."""
        if endpoint_id in self._tasks:
            return
        if not self._running:
            self._running = True
        self._tasks[endpoint_id] = asyncio.create_task(self._probe_loop(endpoint_id))
        logger.info("Started monitoring endpoint: %s", endpoint_id)

    async def stop_monitoring(self, endpoint_id: str) -> None:
        """Stop monitoring an endpoint."""
        task = self._tasks.pop(endpoint_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info("Stopped monitoring endpoint: %s", endpoint_id)

    async def start_all(self, endpoint_ids: list[str]) -> None:
        """Start monitoring all endpoints."""
        self._running = True
        for eid in endpoint_ids:
            await self.start_monitoring(eid)
        logger.info("Started monitoring %d endpoints", len(endpoint_ids))

    async def stop_all(self) -> None:
        """Stop all monitoring."""
        self._running = False
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        logger.info("Stopped all monitoring")

    def get_monitored_endpoints(self) -> list[str]:
        """Return list of currently monitored endpoint IDs."""
        return list(self._tasks.keys())
