"""moa_gateway.discovery.discovery_engine — Core model discovery engine.

Concurrently requests each platform /models endpoint, parses responses
according to API format (OpenAI / Gemini / Cohere), and produces DiscoveredModel records.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .free_model_catalog import PlatformInfo, get_all_platforms, get_platform

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredModel:
    """A model discovered from a platform /models endpoint."""

    platform_id: str
    platform_name: str
    model_id: str
    display_name: str
    base_url: str
    api_format: str
    auth_type: str
    context_window: int
    free_limit: str
    streaming: bool
    function_calling: bool
    discovered_at: float
    modalities: list[str] = None


# Module-level helpers (importable by auto_configurator)

_SMALL_KEYWORDS = ("mini", "flash", "nano", "tiny")
_LITE_SIZES_RE = re.compile(r"\b(7b|8b|13b|14b|9b|11b|12b)\b")
_PARAM_SIZE_RE = re.compile(r"(\d+(?:\.\d+)?)b\b")
_CTX_KM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([km])", re.IGNORECASE)


def infer_tier(model_id: str, context_window: int) -> str:
    """Infer model tier from model name and context window.

    Rules (first match wins):
    1. Contains mini/flash/nano/tiny -> free
    2. Contains 7b/8b/13b/14b/9b/11b/12b -> lite
    3. Parameter size <10B -> free, <70B -> lite, >=70B -> standard
    4. Unknown -> standard
    """
    lower = model_id.lower()

    if any(kw in lower for kw in _SMALL_KEYWORDS):
        return "free"

    if _LITE_SIZES_RE.search(lower):
        return "lite"

    m = _PARAM_SIZE_RE.search(lower)
    if m:
        size = float(m.group(1))
        if size < 10:
            return "free"
        if size < 70:
            return "lite"
        return "standard"

    return "standard"


def infer_context_window(model_id: str) -> int:
    """Try to extract context-window size from a model name.

    Looks for patterns like 32k, 128k, 1m.  Returns 8192 if unknown.
    """
    m = _CTX_KM_RE.search(model_id)
    if m:
        num = float(m.group(1))
        unit = m.group(2).lower()
        if unit == "k":
            return int(num * 1024)
        if unit == "m":
            return int(num * 1024 * 1024)
    return 8192


class FreeModelDiscoveryEngine:
    """Concurrently discover free models across all registered platforms."""

    def __init__(self, api_keys: dict[str, str] | None = None):
        self._api_keys = api_keys or {}
        self._semaphore = asyncio.Semaphore(10)

    async def discover_all(self) -> list[DiscoveredModel]:
        """Discover models from every platform concurrently."""
        platforms = get_all_platforms()
        async with httpx.AsyncClient() as client:
            tasks = [self._discover_one(client, p) for p in platforms]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_models: list[DiscoveredModel] = []
        for platform, result in zip(platforms, results):
            if isinstance(result, Exception):
                logger.warning("Platform %s discovery failed: %s", platform.platform_id, result)
                continue
            all_models.extend(result)

        logger.info("Discovered %d models from %d platforms", len(all_models), len(platforms))
        return all_models

    async def discover_platform(self, platform_id: str) -> list[DiscoveredModel]:
        """Discover models from a single platform."""
        platform = get_platform(platform_id)
        if platform is None:
            logger.warning("Unknown platform: %s", platform_id)
            return []
        async with httpx.AsyncClient() as client:
            return await self._discover_one(client, platform)

    async def probe_model(
        self,
        base_url: str,
        model_id: str,
        api_key: str = "",
        api_format: str = "openai",
    ) -> bool:
        """Probe a single model availability by sending a minimal ping request."""
        async with self._semaphore:
            async with httpx.AsyncClient() as client:
                headers: dict[str, str] = {}
                if api_key and api_format != "google_gemini":
                    headers["Authorization"] = f"Bearer {api_key}"

                try:
                    if api_format == "google_gemini":
                        url = f"{base_url}/models/{model_id}:generateContent"
                        params = {"key": api_key} if api_key else {}
                        body: dict[str, Any] = {
                            "contents": [{"parts": [{"text": "ping"}]}],
                        }
                        resp = await client.post(
                            url, json=body, params=params, headers=headers, timeout=10.0
                        )
                    elif api_format == "cohere":
                        url = f"{base_url}/chat"
                        body = {"model": model_id, "message": "ping", "max_tokens": 1}
                        resp = await client.post(
                            url, json=body, headers=headers, timeout=10.0
                        )
                    else:
                        url = f"{base_url}/chat/completions"
                        body = {
                            "model": model_id,
                            "messages": [{"role": "user", "content": "ping"}],
                            "max_tokens": 1,
                            "stream": False,
                        }
                        resp = await client.post(
                            url, json=body, headers=headers, timeout=10.0
                        )

                    return resp.status_code == 200
                except Exception as e:
                    logger.warning("Probe failed for %s/%s: %s", base_url, model_id, e)
                    return False

    async def _discover_one(
        self, client: httpx.AsyncClient, platform: PlatformInfo
    ) -> list[DiscoveredModel]:
        """Fetch and parse models from one platform (rate-limited by semaphore)."""
        # Platforms without a /models endpoint use static configuration
        if not platform.models_endpoint:
            return self._static_models_for_platform(platform)

        async with self._semaphore:
            response_data = await self._fetch_models(client, platform)
            if not response_data:
                return []

            if platform.api_format == "google_gemini":
                return self._parse_gemini_models(response_data, platform)
            return self._parse_openai_models(response_data, platform)

    async def _fetch_models(
        self, client: httpx.AsyncClient, platform: PlatformInfo
    ) -> dict[str, Any]:
        """Send GET /models to the platform and return parsed JSON."""
        api_key = self._api_keys.get(platform.platform_id, "")

        base_url = platform.base_url
        if "{account_id}" in base_url:
            acct = self._api_keys.get(f"{platform.platform_id}_account_id", "")
            if not acct:
                logger.warning("Platform %s requires account_id, skipping", platform.platform_id)
                return {}
            base_url = base_url.replace("{account_id}", acct)

        url = base_url + platform.models_endpoint

        headers = dict(platform.special_headers)
        params: dict[str, str] = {}

        if platform.auth_type == "bearer":
            if not api_key:
                logger.warning(
                    "Platform %s requires API key (bearer), skipping", platform.platform_id
                )
                return {}
            headers["Authorization"] = f"Bearer {api_key}"
        elif platform.auth_type == "query_param":
            if not api_key:
                logger.warning(
                    "Platform %s requires API key (query_param), skipping", platform.platform_id
                )
                return {}
            params["key"] = api_key
        elif platform.auth_type == "optional_token":
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        try:
            resp = await client.get(url, headers=headers, params=params, timeout=10.0)
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            logger.warning("Platform %s: request timeout", platform.platform_id)
            return {}
        except httpx.HTTPStatusError as e:
            logger.warning(
                "Platform %s: HTTP %d", platform.platform_id, e.response.status_code
            )
            return {}
        except Exception as e:
            logger.warning("Platform %s: %s", platform.platform_id, e)
            return {}

    def _parse_openai_models(
        self, response_data: dict[str, Any], platform: PlatformInfo
    ) -> list[DiscoveredModel]:
        """Parse an OpenAI-format {data: [...]} /models response."""
        models: list[DiscoveredModel] = []
        now = time.time()

        for item in response_data.get("data", []):
            model_id = item.get("id", "")
            if not model_id:
                continue

            if platform.platform_id == "openrouter":
                if not model_id.endswith(":free"):
                    continue

            ctx = infer_context_window(model_id)

            models.append(
                DiscoveredModel(
                    platform_id=platform.platform_id,
                    platform_name=platform.platform_name,
                    model_id=model_id,
                    display_name=item.get("name", model_id),
                    base_url=platform.base_url,
                    api_format=platform.api_format,
                    auth_type=platform.auth_type,
                    context_window=ctx,
                    free_limit=platform.rate_limit_info,
                    streaming=True,
                    function_calling=True,
                    discovered_at=now,
                    modalities=list(getattr(platform, "modalities", ["text"])),
                )
            )
        return models

    def _parse_gemini_models(
        self, response_data: dict[str, Any], platform: PlatformInfo
    ) -> list[DiscoveredModel]:
        """Parse a Google Gemini {models: [...]} /models response."""
        models: list[DiscoveredModel] = []
        now = time.time()

        for item in response_data.get("models", []):
            name = item.get("name", "")
            if not name:
                continue

            model_id = name.split("/", 1)[-1] if "/" in name else name

            display_name = item.get("displayName", model_id)
            ctx = item.get("inputTokenLimit", infer_context_window(model_id))

            supported = item.get("supportedGenerationMethods", [])
            supports_chat = "generateContent" in supported
            if not supports_chat:
                continue

            models.append(
                DiscoveredModel(
                    platform_id=platform.platform_id,
                    platform_name=platform.platform_name,
                    model_id=model_id,
                    display_name=display_name,
                    base_url=platform.base_url,
                    api_format=platform.api_format,
                    auth_type=platform.auth_type,
                    context_window=ctx,
                    free_limit=platform.rate_limit_info,
                    streaming=True,
                    function_calling=True,
                    discovered_at=now,
                    modalities=list(getattr(platform, "modalities", ["text"])),
                )
            )
        return models

    def _static_models_for_platform(self, platform: PlatformInfo) -> list[DiscoveredModel]:
        """Return statically-configured models for platforms without a /models endpoint.

        Used for multimodal specialist platforms (video, music, image generation)
        that do not expose a standard GET /models endpoint.
        """
        now = time.time()
        static_map: dict[str, list[dict[str, Any]]] = {
            "kling": [{"model_id": "kling-v1", "display_name": "Kling Video v1"}],
            "cogview": [{"model_id": "cogview-3", "display_name": "CogView-3"}],
            "wanx": [{"model_id": "wanx-v1", "display_name": "Wanx v1"}],
            "minimax_music": [{"model_id": "music-01", "display_name": "MiniMax Music-01"}],
            "tiangong_music": [{"model_id": "skymusic-v1", "display_name": "SkyMusic v1"}],
        }
        entries = static_map.get(platform.platform_id, [])
        models: list[DiscoveredModel] = []
        for entry in entries:
            models.append(
                DiscoveredModel(
                    platform_id=platform.platform_id,
                    platform_name=platform.platform_name,
                    model_id=entry["model_id"],
                    display_name=entry["display_name"],
                    base_url=platform.base_url,
                    api_format=platform.api_format,
                    auth_type=platform.auth_type,
                    context_window=0,
                    free_limit=platform.rate_limit_info,
                    streaming=False,
                    function_calling=False,
                    discovered_at=now,
                    modalities=list(getattr(platform, "modalities", ["text"])),
                )
            )
        return models
