"""moa_gateway.benchmark.capability_probe — Model capability detection system.

Probes each endpoint with targeted test requests to discover which
capabilities a model actually supports (code, reasoning, json_mode,
function_call, streaming, etc.).
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

import httpx

from .metrics_store import MetricsStore

logger = logging.getLogger(__name__)


class Capability(Enum):
    """Model capability tags."""

    TEXT = "text"                  # basic text chat
    CODE = "code"                  # code generation
    REASONING = "reasoning"        # logical reasoning
    VISION = "vision"              # image understanding
    FUNCTION_CALL = "function_call"  # function/tool calling
    JSON_MODE = "json_mode"         # JSON output mode
    MULTILINGUAL = "multilingual"   # multi-language support
    CREATIVE = "creative"          # creative writing
    STREAMING = "streaming"         # streaming response


@dataclass
class CapabilityResult:
    """Capability probe result for one endpoint."""

    endpoint_id: str
    capabilities: list[Capability] = field(default_factory=list)
    capability_details: dict[str, bool] = field(default_factory=dict)
    tested_at: datetime = field(default_factory=datetime.now)

    def summary(self) -> dict[str, Any]:
        """Return summary dict for API responses."""
        return {
            "endpoint_id": self.endpoint_id,
            "capabilities": [c.value for c in self.capabilities],
            "capability_details": self.capability_details,
            "tested_at": self.tested_at.isoformat() if self.tested_at else None,
        }


class CapabilityProbe:
    """Probes model capabilities by sending targeted test requests."""

    # Each capability test: request payload + validation function
    # validate receives the parsed JSON response dict
    CAPABILITY_TESTS: dict[Capability, dict[str, Any]] = {}

    def __init__(
        self,
        model_pool: Any | None = None,
        health_checker: Any | None = None,
        metrics_store: MetricsStore | None = None,
        max_concurrent: int = 3,
        probe_timeout: int = 30,
    ):
        self._model_pool = model_pool
        self._health_checker = health_checker
        self._metrics_store = metrics_store or MetricsStore()
        self._max_concurrent = max_concurrent
        self._probe_timeout = probe_timeout
        self._results: dict[str, CapabilityResult] = {}
        self._running = False
        self._task: asyncio.Task | None = None

        # Initialize capability tests
        self._init_capability_tests()

    def _init_capability_tests(self) -> None:
        """Define test cases for each capability."""
        self.CAPABILITY_TESTS = {
            Capability.TEXT: {
                "messages": [{"role": "user", "content": "Hello, how are you?"}],
                "max_tokens": 50,
                "validate": self._validate_text,
            },
            Capability.CODE: {
                "messages": [
                    {"role": "user", "content": "Write a Python function to add two numbers."}
                ],
                "max_tokens": 200,
                "validate": self._validate_code,
            },
            Capability.REASONING: {
                "messages": [
                    {"role": "user", "content": "If A>B, B>C, is A>C? Reply yes or no."}
                ],
                "max_tokens": 100,
                "validate": self._validate_reasoning,
            },
            Capability.JSON_MODE: {
                "messages": [
                    {
                        "role": "user",
                        "content": "Return a JSON object with key 'status' and value 'ok'.",
                    }
                ],
                "max_tokens": 50,
                "response_format": {"type": "json_object"},
                "validate": self._validate_json_mode,
            },
            Capability.FUNCTION_CALL: {
                "messages": [{"role": "user", "content": "What's the weather in Paris?"}],
                "max_tokens": 100,
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get weather for a city",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                            },
                        },
                    }
                ],
                "validate": self._validate_function_call,
            },
            Capability.STREAMING: {
                "messages": [{"role": "user", "content": "Count from 1 to 5."}],
                "max_tokens": 50,
                "stream": True,
                "validate": self._validate_streaming,
            },
        }

    # ========== Validation functions ==========

    @staticmethod
    def _extract_content(resp_data: dict[str, Any]) -> str:
        """Extract text content from API response."""
        choices = resp_data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return message.get("content", "") or ""

    @classmethod
    def _validate_text(cls, resp_data: dict[str, Any]) -> bool:
        content = cls._extract_content(resp_data)
        return len(content) > 0

    @classmethod
    def _validate_code(cls, resp_data: dict[str, Any]) -> bool:
        content = cls._extract_content(resp_data).lower()
        return "def " in content or "function " in content or "func " in content

    @classmethod
    def _validate_reasoning(cls, resp_data: dict[str, Any]) -> bool:
        content = cls._extract_content(resp_data).lower()
        return "yes" in content

    @classmethod
    def _validate_json_mode(cls, resp_data: dict[str, Any]) -> bool:
        content = cls._extract_content(resp_data)
        # Try to parse as JSON, or check for key patterns
        import json as _json

        try:
            parsed = _json.loads(content)
            return isinstance(parsed, dict)
        except (ValueError, TypeError):
            # Fallback: check if response contains json-like structure
            return '"status"' in content or '"ok"' in content

    @classmethod
    def _validate_function_call(cls, resp_data: dict[str, Any]) -> bool:
        choices = resp_data.get("choices", [])
        if not choices:
            return False
        message = choices[0].get("message", {})
        return "tool_calls" in message and bool(message["tool_calls"])

    @staticmethod
    def _validate_streaming(resp_data: dict[str, Any]) -> bool:
        # For streaming, success means the request didn't error
        # The streaming probe sets a special marker
        return resp_data.get("_stream_success", False)

    # ========== Endpoint access ==========

    def _get_endpoint(self, endpoint_id: str):
        """Retrieve endpoint from model_pool."""
        if not self._model_pool:
            return None
        if hasattr(self._model_pool, "get_endpoint"):
            return self._model_pool.get_endpoint(endpoint_id)
        if hasattr(self._model_pool, "endpoints"):
            return self._model_pool.endpoints.get(endpoint_id)
        return None

    def _get_endpoint_config(self, endpoint) -> tuple[str, str, str, str, int]:
        """Extract (api_base, api_key, model, provider, timeout) from endpoint."""
        cfg = getattr(endpoint, "config", endpoint)
        api_base = getattr(cfg, "api_base", "")
        api_key = getattr(cfg, "api_key_runtime", "") or getattr(cfg, "api_key", "")
        model = getattr(cfg, "model", "")
        provider = getattr(cfg, "provider", "")
        timeout = getattr(cfg, "timeout", self._probe_timeout)
        return api_base, api_key, model, provider, timeout

    def _is_probeable(self, endpoint_id: str) -> bool:
        """Check if endpoint is healthy enough to probe."""
        if not self._health_checker:
            return self._get_endpoint(endpoint_id) is not None

        from ..health.health_checker import HealthStatus

        health = self._health_checker.get_health(endpoint_id)
        return health.status not in (HealthStatus.DEAD, HealthStatus.UNHEALTHY)

    # ========== Core probe logic ==========

    async def probe_endpoint(self, endpoint_id: str) -> CapabilityResult:
        """Probe all capabilities for a single endpoint."""
        endpoint = self._get_endpoint(endpoint_id)
        if not endpoint:
            return CapabilityResult(
                endpoint_id=endpoint_id,
                capabilities=[Capability.TEXT],
                capability_details={"text": True},
            )

        if not self._is_probeable(endpoint_id):
            # Return existing results or default
            if endpoint_id in self._results:
                return self._results[endpoint_id]
            return CapabilityResult(
                endpoint_id=endpoint_id,
                capabilities=[Capability.TEXT],
                capability_details={"text": True},
            )

        api_base, api_key, model, provider, timeout = self._get_endpoint_config(endpoint)
        if not api_base:
            return CapabilityResult(
                endpoint_id=endpoint_id,
                capabilities=[Capability.TEXT],
                capability_details={"text": True},
            )

        result = CapabilityResult(
            endpoint_id=endpoint_id,
            tested_at=datetime.now(),
        )

        # Probe each capability concurrently (limited concurrency)
        semaphore = asyncio.Semaphore(self._max_concurrent)

        async def _probe_one(cap: Capability, test_def: dict[str, Any]):
            async with semaphore:
                supported = await self._probe_capability(
                    endpoint_id, provider, api_base, api_key, model, test_def, timeout
                )
                result.capability_details[cap.value] = supported
                if supported:
                    result.capabilities.append(cap)

        # Always test TEXT first (it's the baseline)
        await _probe_one(Capability.TEXT, self.CAPABILITY_TESTS[Capability.TEXT])

        # Test remaining capabilities in parallel
        other_tests = {
            k: v
            for k, v in self.CAPABILITY_TESTS.items()
            if k != Capability.TEXT
        }
        await asyncio.gather(
            *[_probe_one(k, v) for k, v in other_tests.items()]
        )

        # If text failed, still mark it as supported (models always support basic text)
        if Capability.TEXT not in result.capabilities:
            result.capabilities.append(Capability.TEXT)
            result.capability_details["text"] = True

        # Sort capabilities for consistent output
        result.capabilities.sort(key=lambda c: c.value)
        self._results[endpoint_id] = result
        return result

    async def _probe_capability(
        self,
        endpoint_id: str,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
        test_def: dict[str, Any],
        timeout: int,
    ) -> bool:
        """Send a single capability test request and validate."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        is_stream = test_def.get("stream", False)
        req_timeout = min(self._probe_timeout, timeout)

        try:
            async with httpx.AsyncClient(
                timeout=req_timeout,
                trust_env=False,
            ) as client:
                if is_stream:
                    # Streaming probe: just check if the stream starts successfully
                    return await self._probe_streaming(
                        client, provider, api_base, api_key, model, test_def, headers
                    )

                response_data = await self._send_capability_request(
                    client, provider, api_base, api_key, model, test_def, headers
                )

                if response_data is None:
                    return False

                # Run validation
                validate_fn = test_def["validate"]
                return validate_fn(response_data)

        except httpx.TimeoutException:
            logger.debug("Capability probe %s: timeout", endpoint_id)
            return False
        except httpx.ConnectError:
            logger.debug("Capability probe %s: connection error", endpoint_id)
            return False
        except Exception as e:
            logger.debug("Capability probe %s: %s", endpoint_id, e)
            return False

    async def _send_capability_request(
        self,
        client: httpx.AsyncClient,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
        test_def: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any] | None:
        """Send a capability test request and return parsed response."""
        messages = test_def["messages"]
        max_tokens = test_def["max_tokens"]

        try:
            if provider == "anthropic":
                url = f"{api_base}/v1/messages"
                payload: dict[str, Any] = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                if "response_format" in test_def:
                    # Anthropic doesn't support response_format natively
                    # Add instruction to the prompt
                    pass
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
                resp = await client.post(url, json=payload, headers=headers)
            elif provider == "gemini":
                url = f"{api_base}/models/{model}:generateContent"
                params = {"key": api_key} if api_key else {}
                payload = {
                    "contents": [
                        {"parts": [{"text": messages[0]["content"] if messages else "Hi"}]}
                    ]
                }
                resp = await client.post(url, json=payload, params=params, headers=headers)
            elif provider == "cohere":
                url = f"{api_base}/chat"
                payload = {
                    "model": model,
                    "message": messages[0]["content"] if messages else "Hi",
                    "max_tokens": max_tokens,
                }
                resp = await client.post(url, json=payload, headers=headers)
            else:
                # OpenAI-compatible format
                url = f"{api_base}/chat/completions"
                payload = {
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "stream": False,
                }
                if "response_format" in test_def:
                    payload["response_format"] = test_def["response_format"]
                if "tools" in test_def:
                    payload["tools"] = test_def["tools"]
                resp = await client.post(url, json=payload, headers=headers)

            if resp.status_code == 200:
                return resp.json()
            else:
                logger.debug(
                    "Capability probe %s/%s: HTTP %d",
                    provider,
                    model,
                    resp.status_code,
                )
                return None
        except httpx.TimeoutException:
            raise
        except httpx.ConnectError:
            raise
        except Exception as e:
            logger.debug("Capability request failed for %s/%s: %s", provider, model, e)
            return None

    async def _probe_streaming(
        self,
        client: httpx.AsyncClient,
        provider: str,
        api_base: str,
        api_key: str,
        model: str,
        test_def: dict[str, Any],
        headers: dict[str, str],
    ) -> bool:
        """Test streaming support by sending a stream request."""
        messages = test_def["messages"]
        max_tokens = test_def["max_tokens"]

        try:
            # Only OpenAI-compatible providers support stream
            url = f"{api_base}/chat/completions"
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": True,
            }

            async with client.stream("POST", url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    return False
                # Read at least one chunk to confirm streaming works
                chunk_count = 0
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        chunk_count += 1
                        if chunk_count >= 1:
                            return True
                return chunk_count > 0

        except Exception as e:
            logger.debug("Streaming probe failed for %s: %s", model, e)
            return False

    async def probe_all(self) -> dict[str, CapabilityResult]:
        """Probe capabilities for all healthy endpoints."""
        if not self._model_pool:
            return {}

        if hasattr(self._model_pool, "endpoints"):
            all_ids = list(self._model_pool.endpoints.keys())
        elif hasattr(self._model_pool, "list_endpoints"):
            all_ids = [
                e.id if hasattr(e, "id") else e for e in self._model_pool.list_endpoints()
            ]
        else:
            return {}

        probeable = [eid for eid in all_ids if self._is_probeable(eid)]
        if not probeable:
            logger.info("No probeable endpoints found")
            return {}

        logger.info("Starting capability probe for %d endpoints", len(probeable))

        # Limit concurrency across endpoints
        semaphore = asyncio.Semaphore(self._max_concurrent)
        results: dict[str, CapabilityResult] = {}

        async def _probe_with_limit(eid: str):
            async with semaphore:
                results[eid] = await self.probe_endpoint(eid)

        await asyncio.gather(*[_probe_with_limit(eid) for eid in probeable])

        # Persist results
        await self._metrics_store.save_capabilities(self._results)
        logger.info("Capability probe complete: %d endpoints tested", len(results))
        return results

    # ========== Lifecycle ==========

    async def start(self) -> None:
        """Start the capability probe (load persisted results)."""
        raw_data = await self._metrics_store.load_capabilities()
        for eid, c_data in raw_data.items():
            caps = []
            for c_str in c_data.get("capabilities", []):
                try:
                    caps.append(Capability(c_str))
                except ValueError:
                    pass
            tested_at_str = c_data.get("tested_at")
            tested_at = None
            if tested_at_str:
                try:
                    tested_at = datetime.fromisoformat(tested_at_str)
                except (ValueError, TypeError):
                    tested_at = datetime.now()
            self._results[eid] = CapabilityResult(
                endpoint_id=eid,
                capabilities=caps,
                capability_details=c_data.get("capability_details", {}),
                tested_at=tested_at or datetime.now(),
            )
        logger.info("Loaded %d endpoint capabilities from storage", len(self._results))

    async def stop(self) -> None:
        """Stop and persist results."""
        await self._metrics_store.save_capabilities(self._results)
        logger.info("CapabilityProbe stopped")

    # ========== Query API ==========

    def get_capabilities(self, endpoint_id: str) -> list[Capability]:
        """Get the capability tags for an endpoint."""
        if endpoint_id in self._results:
            return self._results[endpoint_id].capabilities
        return [Capability.TEXT]  # default: at least text

    def get_result(self, endpoint_id: str) -> CapabilityResult | None:
        """Get detailed capability result for an endpoint."""
        return self._results.get(endpoint_id)

    def get_all_results(self) -> dict[str, CapabilityResult]:
        """Get all capability results."""
        return dict(self._results)

    def remove_endpoint(self, endpoint_id: str) -> None:
        """Remove capability results for an endpoint (P2-6)."""
        self._results.pop(endpoint_id, None)
        logger.debug("Removed capability results for endpoint %s", endpoint_id)

    def get_endpoints_by_capability(self, capability: Capability) -> list[str]:
        """Filter endpoints by capability."""
        return [
            eid for eid, r in self._results.items() if capability in r.capabilities
        ]

    def get_summary(self) -> dict[str, Any]:
        """Return aggregate capability summary."""
        cap_counts: dict[str, int] = {}
        for r in self._results.values():
            for c in r.capabilities:
                cap_counts[c.value] = cap_counts.get(c.value, 0) + 1
        return {
            "total_probed": len(self._results),
            "capability_counts": cap_counts,
            "endpoints": [r.summary() for r in self._results.values()],
        }
