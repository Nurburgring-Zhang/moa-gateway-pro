"""moa_gateway.model_pool — 模型池
- 维护所有启用的模型端点
- 构造 provider 实例
- 暴露 select / call / health_check / fallback_chain
- 异步后台健康检查
- 熔断保护
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import httpx

from .config import ModelEndpointConfig, Settings, get_settings, subscribe_settings_change
from .ha.circuit_breaker import CircuitBreakerConfig, CircuitBreakerRegistry, CircuitState
from .ha.failover import FailoverManager
from .providers import NO_AUTH_PROVIDERS, MockProvider, build_provider, is_mock_key
from .providers.base import ChatRequest, ChatResponse, Provider, ProviderError
from .storage import get_storage

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    FREE = "free"
    LITE = "lite"
    STANDARD = "standard"
    PREMIUM = "premium"
    FLAGSHIP = "flagship"

    @property
    def rank(self) -> int:
        order = ["free", "lite", "standard", "premium", "flagship"]
        return order.index(self.value)

    def __lt__(self, other):
        return self.rank < other.rank

    def __le__(self, other):
        return self.rank <= other.rank

    def __gt__(self, other):
        return self.rank > other.rank

    def __ge__(self, other):
        return self.rank >= other.rank

    # 修20: 统一 tier 序列操作,消除硬编码
    def previous(self, steps: int = 1) -> ModelTier:
        order = ["free", "lite", "standard", "premium", "flagship"]
        new_rank = max(0, self.rank - steps)
        return ModelTier(order[new_rank])

    def next(self, steps: int = 1) -> ModelTier:
        order = ["free", "lite", "standard", "premium", "flagship"]
        new_rank = min(len(order) - 1, self.rank + steps)
        return ModelTier(order[new_rank])

    @classmethod
    def order(cls) -> list[ModelTier]:
        return [cls.FREE, cls.LITE, cls.STANDARD, cls.PREMIUM, cls.FLAGSHIP]


@dataclass
class ModelEndpoint:
    """运行时模型端点(由 ModelPool 维护)"""

    config: ModelEndpointConfig
    provider_obj: Provider | None = None
    health_status: str = "unknown"
    consecutive_failures: int = 0
    last_health_check: float = 0.0
    last_error: str = ""
    cooldown_until: float = 0.0
    total_calls: int = 0
    total_failures: int = 0
    last_status_code: int | None = None

    @property
    def id(self) -> str:
        return self.config.id

    @property
    def tier(self) -> ModelTier:
        return ModelTier(self.config.tier)

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    @property
    def is_available(self) -> bool:
        if not self.enabled:
            return False
        if self.health_status in ("unhealthy", "dead"):
            return False
        # Legacy cooldown check kept for backward compat with code that
        # sets cooldown_until directly (e.g., during mock fallback)
        if self.cooldown_until and time.time() < self.cooldown_until:
            return False
        return True

    def mark_failure(self, reason: str = ""):
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_error = reason
        if self.consecutive_failures >= 3:
            self.health_status = "unhealthy"

    def mark_success(self):
        self.consecutive_failures = 0
        self.health_status = "healthy"
        self.last_error = ""
        self.cooldown_until = 0.0
        self.total_calls += 1

    def trigger_breaker(self, cooldown_seconds: int = 60):
        self.cooldown_until = time.time() + cooldown_seconds
        self.health_status = "unhealthy"
        logger.warning("Circuit breaker triggered for %s, cooldown %ss", self.id, cooldown_seconds)

    def recover_breaker(self):
        self.cooldown_until = 0.0
        self.consecutive_failures = 0
        self.health_status = "healthy"


class ModelPool:
    """工业级模型池"""

    def __init__(self, settings: Settings | None = None, storage: Any | None = None):
        self.settings = settings or get_settings()
        self.storage = storage or get_storage()
        self.endpoints: dict[str, ModelEndpoint] = {}
        self._client: httpx.AsyncClient | None = None
        self._health_task: asyncio.Task | None = None
        self._lock = threading.Lock()
        # Circuit breaker registry — one breaker per endpoint
        self._breaker_registry = CircuitBreakerRegistry()
        self._breaker_config = CircuitBreakerConfig(
            failure_threshold=self.settings.health.failure_threshold,
            recovery_timeout=float(self.settings.health.cooldown_seconds),
            half_open_max_calls=3,
            success_threshold=2,
        )
        self._breaker_state_change = self._on_breaker_state_change
        # Failover manager — provider-level health tracking
        self._failover = FailoverManager(
            max_consecutive_failures=self.settings.health.failure_threshold
        )
        # Round-1 (P0-11): per-endpoint lock for _saved_api_key race
        self._ep_locks: dict[str, asyncio.Lock] = {}
        # 修 P0-2: 同步路径上需要关闭的旧 provider 队列 (stop() 时统一 await aclose)
        # 修 P0-10: 改 deque(maxlen) 限大小,后台 task 周期清空 → 防止长跑 server 内存无限增长
        from collections import deque

        self._pending_close: deque = deque(maxlen=100)
        self._close_task: asyncio.Task | None = None
        self.refresh()
        # 修19: 订阅配置变更(WebUI 改配置后自动 reload)
        subscribe_settings_change(self._on_settings_change)
        logger.info("ModelPool initialized with %d endpoints", len(self.endpoints))

    def _on_settings_change(self, old_settings, new_settings):
        """配置变更时刷新模型池(只在非禁用端点上做,避免破坏运行中请求)"""
        try:
            self.settings = new_settings
            self.refresh()
            logger.info(
                "ModelPool reloaded after settings change: %d endpoints", len(self.endpoints)
            )
        except Exception as e:
            logger.warning("ModelPool reload failed: %s", e)

    def refresh(self) -> None:
        """从 settings + storage 刷新端点"""
        with self._lock:
            self._refresh_unlocked()

    def _refresh_unlocked(self) -> None:
        """Internal refresh without lock (caller must hold _lock)."""
        storage_eps = {e["endpoint_id"]: e for e in self.storage.list_endpoints()}
        new_ids = set()
        for cfg in self.settings.models:
            eid = cfg.id
            new_ids.add(eid)
            if eid in self.endpoints:
                ep = self.endpoints[eid]
                ep.config = cfg
                self._rebuild_provider(ep)
            else:
                ep = ModelEndpoint(config=cfg)
                self._rebuild_provider(ep)
                self.endpoints[eid] = ep
            s_ep = storage_eps.get(eid)
            if s_ep:
                self._apply_storage_overlay(ep, s_ep)
        for eid, s_ep in storage_eps.items():
            if eid in new_ids:
                continue
            cfg = ModelEndpointConfig(
                id=s_ep["endpoint_id"],
                provider=s_ep["provider"],
                model=s_ep["model"],
                tier=s_ep["tier"],
                api_base=s_ep.get("api_base") or "",
                api_key_env=s_ep.get("api_key_env") or "",
                cost_per_1k_input=s_ep.get("cost_per_1k_input") or 0.001,
                cost_per_1k_output=s_ep.get("cost_per_1k_output") or 0.002,
                max_tokens=s_ep.get("max_tokens") or 8192,
                timeout=s_ep.get("timeout") or 120,
                weight=s_ep.get("weight") or 100,
                enabled=bool(s_ep.get("enabled", True)),
                tags=s_ep.get("tags") or [],
            )
            cfg.api_key_runtime = s_ep.get("api_key", "")
            ep = ModelEndpoint(config=cfg)
            self._rebuild_provider(ep)
            self.endpoints[eid] = ep
        # Register providers in failover manager with tier-based priority
        tier_priority = {"free": 5, "lite": 4, "standard": 3, "premium": 2, "flagship": 1}
        for eid, ep in self.endpoints.items():
            if ep.config.enabled:
                priority = tier_priority.get(ep.config.tier, 3)
                self._failover.register_provider(eid, priority=priority)

    def _apply_storage_overlay(self, ep: ModelEndpoint, s_ep: dict[str, Any]) -> None:
        if s_ep.get("api_key"):
            ep.config.api_key_runtime = s_ep["api_key"]
        if s_ep.get("api_base"):
            ep.config.api_base = s_ep["api_base"]
        if s_ep.get("api_key_env"):
            ep.config.api_key_env = s_ep["api_key_env"]
        ep.config.enabled = bool(s_ep.get("enabled", ep.config.enabled))
        if s_ep.get("max_tokens"):
            ep.config.max_tokens = s_ep["max_tokens"]
        if s_ep.get("timeout"):
            ep.config.timeout = s_ep["timeout"]
        if s_ep.get("weight"):
            ep.config.weight = s_ep["weight"]
        if s_ep.get("cost_per_1k_input") is not None:
            ep.config.cost_per_1k_input = s_ep["cost_per_1k_input"]
        if s_ep.get("cost_per_1k_output") is not None:
            ep.config.cost_per_1k_output = s_ep["cost_per_1k_output"]
        self._rebuild_provider(ep)

    def _rebuild_provider(self, ep: ModelEndpoint) -> None:
        """Rebuild provider instance with per-provider connection pool."""
        if ep.provider_obj:
            self._pending_close.append(ep.provider_obj)
        ep.provider_obj = None
        try:
            # Use per-provider client if available, else shared fallback
            client = getattr(self, "_provider_clients", {}).get(
                ep.config.provider, self._client
            )
            ep.provider_obj = build_provider(
                ep.config.provider,
                api_base=ep.config.api_base,
                api_key=ep.config.api_key_runtime or "mock",
                timeout=ep.config.timeout,
                client=client,
            )
            ep._last_build_error = None  # type: ignore[attr-defined]
        except Exception as e:
            ep._last_build_error = e  # type: ignore[attr-defined]
            logger.warning("build_provider(%s) failed: %s", ep.config.provider, e)

    def upsert_endpoint(self, ep_dict: dict[str, Any]) -> ModelEndpoint:
        with self._lock:
            eid = ep_dict["endpoint_id"]
            self.storage.upsert_endpoint(ep_dict)
            s_ep = self.storage.get_endpoint(eid)
            if not s_ep:
                raise RuntimeError(f"failed to persist endpoint {eid}")
            cfg = ModelEndpointConfig(
                id=eid,
                provider=s_ep["provider"],
                model=s_ep["model"],
                tier=s_ep["tier"],
                api_base=s_ep.get("api_base") or "",
                api_key_env=s_ep.get("api_key_env") or "",
                cost_per_1k_input=s_ep.get("cost_per_1k_input") or 0.001,
                cost_per_1k_output=s_ep.get("cost_per_1k_output") or 0.002,
                max_tokens=s_ep.get("max_tokens") or 8192,
                timeout=s_ep.get("timeout") or 120,
                weight=s_ep.get("weight") or 100,
                enabled=bool(s_ep.get("enabled", True)),
                tags=s_ep.get("tags") or [],
            )
            cfg.api_key_runtime = s_ep.get("api_key", "")
            ep = ModelEndpoint(config=cfg)
            self._rebuild_provider(ep)
            self.endpoints[eid] = ep
            return ep

    def get_endpoint(self, eid: str) -> ModelEndpoint | None:
        """Get a model endpoint by ID (for health probe integration)."""
        return self.endpoints.get(eid)

    def get_breaker_status(self) -> list[dict]:
        """Return circuit breaker status for all endpoints (monitoring)."""
        return self._breaker_registry.get_all_status()

    @staticmethod
    def _on_breaker_state_change(name: str, new_state: CircuitState) -> None:
        """Emit Prometheus gauge on circuit breaker state transitions."""
        from .observability.metrics import provider_circuit_breaker_state

        value = {"closed": 0, "half_open": 0.5, "open": 1}.get(new_state.value, 0)
        provider_circuit_breaker_state.labels(provider=name).set(value)

    def remove_endpoint(self, eid: str) -> bool:
        with self._lock:
            ok = self.storage.delete_endpoint(eid)
            ep = self.endpoints.pop(eid, None)
            if ep and ep.provider_obj:
                with contextlib.suppress(Exception):
                    asyncio.get_event_loop().create_task(ep.provider_obj.aclose())
            return ok

    async def safe_refresh(self) -> None:
        """Async wrapper for refresh() — usable from async contexts with proper locking."""
        with self._lock:
            self._refresh_unlocked()

    async def start(self) -> None:
        # Per-provider connection pools for isolation
        self._provider_clients: dict[str, httpx.AsyncClient] = {}
        providers = {ep.config.provider for ep in self.endpoints.values()}
        for prov in providers:
            self._provider_clients[prov] = httpx.AsyncClient(
                timeout=httpx.Timeout(120),
                limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            )
        # Shared fallback client for any new provider added at runtime
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        for ep in self.endpoints.values():
            self._rebuild_provider(ep)
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_check_loop())
        # 修 P0-10: 启动后台 task 周期清空 _pending_close
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_pending_loop())
        await self._check_all_health()

    async def _close_pending_loop(self) -> None:
        """修 P0-10: 周期把 _pending_close 里的旧 provider 关闭,避免内存无限增长"""
        while True:
            try:
                await asyncio.sleep(5.0)  # 5s 一次
                while self._pending_close:
                    prov = self._pending_close.popleft()
                    with contextlib.suppress(Exception):
                        await prov.aclose()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("close_pending_loop error")

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._health_task
            self._health_task = None
        if self._close_task:
            self._close_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._close_task
            self._close_task = None
        for ep in self.endpoints.values():
            if ep.provider_obj:
                with contextlib.suppress(Exception):
                    await ep.provider_obj.aclose()
        while self._pending_close:
            prov = self._pending_close.popleft()
            with contextlib.suppress(Exception):
                await prov.aclose()
        # Close per-provider connection pools
        for client in getattr(self, "_provider_clients", {}).values():
            with contextlib.suppress(Exception):
                await client.aclose()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _health_check_loop(self) -> None:
        cfg = self.settings.health
        first_iter = True
        while True:
            try:
                # 修 40: 启动时立即跑首次 health check(不再等 30s)
                # 让 endpoints 立刻有 status(healthy/unhealthy),chat 不会因 unknown 卡 30s
                if first_iter:
                    first_iter = False
                else:
                    await asyncio.sleep(cfg.interval_seconds)
                await self._check_all_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("health loop error: %s", e)

    async def _check_all_health(self) -> None:
        tasks = [
            self._check_one(eid)
            for eid, ep in self.endpoints.items()
            if ep.config.enabled and ep.config.api_key_runtime
        ]
        if tasks:
            # P0-1 (Task #56): Use configurable probe_timeout instead of hardcoded 3s.
            # On timeout, do NOT degrade to MockProvider -- keep real provider.
            probe_timeout = float(self.settings.health.probe_timeout)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=probe_timeout,
                )
            except asyncio.TimeoutError:
                # P0-1 (Task #56): Do NOT degrade to MockProvider on startup timeout.
                # Real LLM APIs often take >3s. Keep real provider for runtime requests.
                for ep in self.endpoints.values():
                    if (
                        ep.config.enabled
                        and ep.config.api_key_runtime
                        and ep.provider_obj is not None
                        and ep.provider_obj.__class__.__name__ != "MockProvider"
                    ):
                        logger.warning(
                            "%s: startup health check timeout (%.1fs), keeping real provider",
                            ep.id,
                            probe_timeout,
                        )
                        ep.health_status = "unknown"
                        ep.last_error = f"startup health check timeout ({probe_timeout:.1f}s)"

    async def _check_one(self, eid: str) -> None:
        ep = self.endpoints.get(eid)
        if not ep or not ep.provider_obj:
            return
        breaker = self._breaker_registry.get_or_create(eid, self._breaker_config, self._breaker_state_change)
        try:
            ok = await asyncio.wait_for(
                ep.provider_obj.health_check(), timeout=self.settings.health.timeout_seconds
            )
            now = time.time()
            ep.last_health_check = now
            if ok:
                ep.last_status_code = 200
                breaker.record_success()
                ep.health_status = "healthy"
                ep.consecutive_failures = 0
                ep.cooldown_until = 0.0
            else:
                ep.last_status_code = None
                await self._maybe_fallback_to_mock(ep, "health_check failed")
                breaker.record_failure()
                ep.consecutive_failures += 1
                if breaker.state == CircuitState.OPEN:
                    ep.health_status = "unhealthy"
        except asyncio.TimeoutError:
            ep.last_status_code = None
            await self._maybe_fallback_to_mock(ep, "health_check timeout")
            breaker.record_failure()
            ep.consecutive_failures += 1
            if breaker.state == CircuitState.OPEN:
                ep.health_status = "unhealthy"
        except Exception as e:
            ep.last_status_code = getattr(e, "status", None)
            ep.consecutive_failures += 1
            ep.last_error = str(e)
            await self._maybe_fallback_to_mock(ep, str(e))
            breaker.record_failure()
            if breaker.state == CircuitState.OPEN:
                ep.health_status = "unhealthy"

    async def _maybe_fallback_to_mock(self, ep, reason: str) -> None:
        """修 P0-8: 只有在 confirm 是 auth 错(401/403)才切 mock,其他错(500/timeout)
        保留真实 key,让用户自己重试或修复。不再 'len > 8 and sk-' 一刀切。
        D6: 切换仅在 mock.mode=explicit 时发生; disabled 时端点保持不可用并明确报错。"""
        if not ep or not ep.config.api_key_runtime:
            return
        # 必须是确认的 auth 错(401/403)才能切
        # timeout / 500 / network err → 不切,留真实 key 给用户重试
        auth_codes = {401, 403}
        last_status = getattr(ep, "last_status_code", None)
        if last_status not in auth_codes:
            logger.debug(
                "%s: not auth error (status=%s), keep real key for retry",
                ep.id,
                last_status,
            )
            return
        if self.settings.mock.mode != "explicit":
            logger.warning(
                "%s: auth invalid (status=%s) but mock.mode=disabled — endpoint stays "
                "unavailable until the API key is fixed",
                ep.id,
                last_status,
            )
            ep.last_error = f"auth invalid (HTTP {last_status}); mock fallback disabled"
            return
        # auth 错确实发生,临时显式切 mock(session 期间, 响应带 mock 标注)
        key = ep.config.api_key_runtime

        if is_mock_key(key):
            return
        logger.warning(
            "%s: auth invalid (status=%s), temporary EXPLICIT fallback to MockProvider "
            "(responses labeled X-MOA-Mock). Real key saved for restore on next config reload.",
            ep.id,
            last_status,
        )
        ep._saved_api_key = key
        ep.config.api_key_runtime = ""
        self._rebuild_provider(ep)
        ep.health_status = "healthy"
        ep.consecutive_failures = 0
        ep.last_error = f"auth invalid (HTTP {last_status}) → explicit mock fallback"

    def available_endpoints(
        self,
        tier: ModelTier | None = None,
        require_provider: str | None = None,
        exclude_ids: list[str] | None = None,
    ) -> list[ModelEndpoint]:
        ex = set(exclude_ids or [])
        out = []
        for ep in self.endpoints.values():
            if not ep.is_available:
                continue
            if tier and ep.tier != tier:
                continue
            if require_provider and ep.config.provider != require_provider:
                continue
            if ep.id in ex:
                continue
            out.append(ep)
        return out

    def select_one(
        self,
        tier: ModelTier,
        prefer_provider: str | None = None,
        exclude_ids: list[str] | None = None,
    ) -> ModelEndpoint | None:
        cands = self.available_endpoints(
            tier=tier, require_provider=prefer_provider, exclude_ids=exclude_ids
        )
        if not cands:
            # 用 ModelTier.previous 替代硬编码
            lower = tier.previous()
            if lower != tier:
                cands = self.available_endpoints(tier=lower, exclude_ids=exclude_ids)
        if not cands:
            return None
        total = sum(c.config.weight for c in cands) or 1
        r = random.random() * total
        for c in cands:
            r -= c.config.weight
            if r <= 0:
                return c
        return cands[0]

    def select_many(
        self,
        tier: ModelTier,
        count: int,
        prefer_diversity: bool = True,
        exclude_ids: list[str] | None = None,
    ) -> list[ModelEndpoint]:
        cands = self.available_endpoints(tier=tier, exclude_ids=exclude_ids)
        if prefer_diversity:
            by_provider: dict[str, list[ModelEndpoint]] = {}
            for ep in cands:
                by_provider.setdefault(ep.config.provider, []).append(ep)
            providers = list(by_provider.keys())
            random.shuffle(providers)
            out: list[ModelEndpoint] = []
            while len(out) < count and providers:
                p = providers.pop(0)
                if by_provider[p]:
                    out.append(by_provider[p].pop(0))
            leftovers = [ep for lst in by_provider.values() for ep in lst]
            random.shuffle(leftovers)
            for ep in leftovers:
                if len(out) >= count:
                    break
                out.append(ep)
            return out[:count]
        else:
            random.shuffle(cands)
            return cands[:count]

    def get_fallback_chain(self, primary_id: str, count: int = 3) -> list[ModelEndpoint]:
        primary = self.endpoints.get(primary_id)
        if not primary:
            return []
        ptier = primary.tier
        same_provider_same: list[ModelEndpoint] = []
        same_provider_lower: list[ModelEndpoint] = []
        diff_provider_same: list[ModelEndpoint] = []
        diff_provider_any: list[ModelEndpoint] = []
        for eid, ep in self.endpoints.items():
            if eid == primary_id or not ep.is_available:
                continue
            same_provider = ep.config.provider == primary.config.provider
            if same_provider and ep.tier == ptier:
                same_provider_same.append(ep)
            elif same_provider and ep.tier < ptier:
                same_provider_lower.append(ep)
            elif (not same_provider) and ep.tier == ptier:
                diff_provider_same.append(ep)
            else:
                diff_provider_any.append(ep)
        chain = same_provider_same + same_provider_lower + diff_provider_same + diff_provider_any
        return chain[:count]

    def build_chat_request(
        self,
        ep: ModelEndpoint,
        messages: list[dict],
        temperature: float = 0.6,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        stream: bool = False,
        **extra_kwargs,
    ) -> ChatRequest:
        return ChatRequest(
            model=ep.config.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max(1, min(max_tokens, ep.config.max_tokens)),
            stream=stream,
            tools=tools,
            timeout=ep.config.timeout,
            extra=extra_kwargs,
        )

    async def call(
        self,
        endpoint_id: str,
        messages: list[dict],
        temperature: float = 0.6,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        stream: bool = False,
        max_retries: int = 3,
        **extra_kwargs,
    ) -> ChatResponse:
        ep = self.endpoints.get(endpoint_id)
        if not ep:
            raise ValueError(f"endpoint {endpoint_id} not found")

        # T5.1: every LLM dispatch gets a span under the request trace.
        from .observability.tracer import get_tracer

        with get_tracer().start_span(
            "model_pool.call",
            {
                "endpoint.id": endpoint_id,
                "model": ep.config.model,
                "provider": ep.config.provider,
            },
        ) as span:
            resp = await self._call_chain(
                endpoint_id, ep, messages, temperature, max_tokens, tools, stream, max_retries,
                **extra_kwargs,
            )
            span.set_attribute("llm.mock", self._ep_is_mock(ep))
            span.set_attribute(
                "llm.tokens", (resp.prompt_tokens or 0) + (resp.completion_tokens or 0)
            )
            return resp

    async def _call_chain(
        self,
        endpoint_id: str,
        ep: ModelEndpoint,
        messages: list[dict],
        temperature: float = 0.6,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
        stream: bool = False,
        max_retries: int = 3,
        **extra_kwargs,
    ) -> ChatResponse:
        chain = [ep] + [
            e for e in self.get_fallback_chain(endpoint_id, max_retries) if e.id != endpoint_id
        ]
        chain = chain[:max_retries]
        last_err: Exception | None = None
        t_call = time.monotonic()
        attempted_any = False
        for attempt, cur in enumerate(chain):
            # Check real circuit breaker first
            breaker = self._breaker_registry.get_or_create(cur.id, self._breaker_config, self._breaker_state_change)
            if not breaker.allow_request():
                # Sync health_status with breaker state for display purposes
                cur.health_status = "unhealthy"
                continue
            if not cur.is_available:
                continue
            attempted_any = True
            if not cur.provider_obj:
                self._rebuild_provider(cur)
            if not cur.provider_obj:
                build_err = getattr(cur, "_last_build_error", None)
                if isinstance(build_err, ProviderError) and build_err.status:
                    last_err = build_err
                else:
                    last_err = RuntimeError(f"{cur.id} provider_obj is None after rebuild")
                logger.warning("call %s: provider_obj is None", cur.id)
                _record_llm_metrics(cur, "error", 0.0, None)
                breaker.record_failure()
                continue
            req = self.build_chat_request(cur, messages, temperature, max_tokens, tools, stream, **extra_kwargs)
            t0 = time.monotonic()
            try:
                resp = await cur.provider_obj.chat(req)
                resp.cost = self._calc_cost(cur, resp.prompt_tokens, resp.completion_tokens)
                _record_llm_metrics(
                    cur,
                    "success",
                    time.monotonic() - t0,
                    resp,
                    is_mock=isinstance(cur.provider_obj, MockProvider),
                )
                breaker.record_success()
                self._failover.record_success(cur.id)
                cur.mark_success()
                return resp
            except ProviderError as e:
                last_err = e
                logger.warning("call %s failed (attempt %d): %s", cur.id, attempt + 1, e)
                breaker.record_failure()
                self._failover.record_failure(cur.id)
                cur.mark_failure(str(e))
                if (
                    getattr(e, "status", 0) in (401, 403)
                    and cur.config.api_key_runtime
                    and self.settings.mock.mode == "explicit"
                ):
                    ep_lock = self._ep_locks.setdefault(cur.id, asyncio.Lock())
                    async with ep_lock:
                        logger.warning(
                            "call %s: %d auth invalid → auto-fallback to MockProvider for this session. "
                            "原 key 仍保留在 config,重启 server 时重读 env",
                            cur.id,
                            e.status,
                        )
                        cur._saved_api_key = cur.config.api_key_runtime  # type: ignore[attr-defined]
                        cur.config.api_key_runtime = ""
                        self._rebuild_provider(cur)
                        try:
                            req2 = self.build_chat_request(
                                cur, messages, temperature, max_tokens, tools, stream
                            )
                            resp = await cur.provider_obj.chat(req2)
                            resp.cost = self._calc_cost(
                                cur, resp.prompt_tokens, resp.completion_tokens
                            )
                            _record_llm_metrics(
                                cur, "success", time.monotonic() - t0, resp, is_mock=True
                            )
                            breaker.record_success()
                            self._failover.record_success(cur.id)
                            cur.mark_success()
                            return resp
                        except Exception as e2:
                            logger.warning("mock fallback also failed: %s", e2)
                            cur.config.api_key_runtime = cur._saved_api_key  # type: ignore[attr-defined]
                            self._rebuild_provider(cur)
                _record_llm_metrics(
                    cur, "error", time.monotonic() - t0, None,
                    is_mock=isinstance(cur.provider_obj, MockProvider),
                )
                # Sync health_status from breaker state
                if breaker.state == CircuitState.OPEN:
                    cur.health_status = "unhealthy"
                from .ha.retry import calculate_delay, RetryConfig

                await asyncio.sleep(calculate_delay(attempt, RetryConfig(
                    base_delay=0.5, exponential_base=2, max_delay=30, jitter=True
                )))
                continue
            except Exception as e:
                last_err = e
                logger.exception("call %s unexpected error: %s", cur.id, e)
                breaker.record_failure()
                self._failover.record_failure(cur.id)
                cur.mark_failure(str(e))
                _record_llm_metrics(
                    cur, "error", time.monotonic() - t0, None,
                    is_mock=isinstance(cur.provider_obj, MockProvider),
                )
                from .ha.retry import calculate_delay, RetryConfig

                await asyncio.sleep(calculate_delay(attempt, RetryConfig(
                    base_delay=0.5, exponential_base=2, max_delay=30, jitter=True
                )))
                continue
        if last_err is None:
            last_err = RuntimeError("no available endpoint in chain")
        if not attempted_any:
            _record_llm_metrics(ep, "error", time.monotonic() - t_call, None)
        if isinstance(last_err, ProviderError) and last_err.status:
            raise ProviderError(
                f"all fallbacks failed for {endpoint_id}: {last_err}",
                status=last_err.status,
            ) from last_err
        raise RuntimeError(f"all fallbacks failed for {endpoint_id}: {last_err}")

    def _calc_cost(self, ep: ModelEndpoint, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1000.0) * ep.config.cost_per_1k_input + (
            completion_tokens / 1000.0
        ) * ep.config.cost_per_1k_output

    def _ep_is_mock(self, e) -> bool:
        """D6: whether an endpoint is currently served by MockProvider."""
        if isinstance(e.provider_obj, MockProvider):
            return True
        if e.provider_obj is None and self.settings.mock.mode == "disabled":
            # not served at all — don't count it as mock capacity
            return False
        return is_mock_key(e.config.api_key_runtime or "") and (
            e.config.provider not in NO_AUTH_PROVIDERS
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "total": len(self.endpoints),
            "enabled": sum(1 for e in self.endpoints.values() if e.config.enabled),
            "healthy": sum(1 for e in self.endpoints.values() if e.health_status == "healthy"),
            "unhealthy": sum(1 for e in self.endpoints.values() if e.health_status == "unhealthy"),
            "in_breaker": sum(
                1 for e in self.endpoints.values()
                if (b := self._breaker_registry.get(e.id)) and b.state != CircuitState.CLOSED
            ),
            # D6: explicit mock visibility
            "mock_backed": sum(1 for e in self.endpoints.values() if self._ep_is_mock(e)),
            "real_backed": sum(1 for e in self.endpoints.values() if not self._ep_is_mock(e)),
            "by_tier": {
                t.value: sum(1 for e in self.endpoints.values() if e.tier == t) for t in ModelTier
            },
            "by_provider": {
                p: sum(1 for e in self.endpoints.values() if e.config.provider == p)
                for p in {e.config.provider for e in self.endpoints.values()}
            },
            "endpoints": [
                {
                    "id": e.id,
                    "provider": e.config.provider,
                    "model": e.config.model,
                    "tier": e.tier.value,
                    "enabled": e.config.enabled,
                    "has_key": bool(e.config.api_key_runtime),
                    "health": e.health_status,
                    "consecutive_failures": e.consecutive_failures,
                    "cooldown_remaining": max(0, int(e.cooldown_until - time.time())),
                    "total_calls": e.total_calls,
                    "total_failures": e.total_failures,
                    "weight": e.config.weight,
                }
                for e in sorted(self.endpoints.values(), key=lambda x: x.id)
            ],
            "failover": self._failover.get_status(),
        }


def _record_llm_metrics(
    ep: ModelEndpoint,
    status: str,
    duration_s: float,
    resp: ChatResponse | None,
    *,
    is_mock: bool = False,
) -> None:
    """Mirror one provider attempt into Prometheus business metrics (D4).

    Best-effort: metric emission must never break the request path.

    D6 alignment: attempts served by MockProvider are attributed to
    ``provider="mock"`` with zero cost, so simulated traffic never inflates
    real-provider request/cost counters and stays identifiable in /metrics.
    """
    try:
        from .observability.metrics import record_llm_request

        record_llm_request(
            model=ep.config.model,
            provider="mock" if is_mock else ep.config.provider,
            status=status,
            duration_s=duration_s,
            input_tokens=resp.prompt_tokens if resp is not None else 0,
            output_tokens=resp.completion_tokens if resp is not None else 0,
            cost_usd=0.0 if is_mock else ((resp.cost if resp is not None else 0.0) or 0.0),
        )
    except Exception:
        logger.debug("failed to record llm metrics", exc_info=True)


_pool: ModelPool | None = None
_pool_lock = __import__("threading").Lock()


def get_model_pool() -> ModelPool:
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = ModelPool()
    return _pool


async def start_model_pool() -> ModelPool:
    pool = get_model_pool()
    await pool.start()
    return pool
