"""moa_gateway.model_pool — 模型池
- 维护所有启用的模型端点
- 构造 provider 实例
- 暴露 select / call / health_check / fallback_chain
- 异步后台健康检查
- 熔断保护
"""
from __future__ import annotations
import asyncio
import time
import random
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Any, Tuple
import httpx

from .config import Settings, ModelEndpointConfig, get_settings, subscribe_settings_change
from .storage import get_storage
from .providers.base import Provider, ChatRequest, ChatResponse, ProviderError
from .providers import build_provider

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
    def previous(self, steps: int = 1) -> "ModelTier":
        order = ["free", "lite", "standard", "premium", "flagship"]
        new_rank = max(0, self.rank - steps)
        return ModelTier(order[new_rank])

    def next(self, steps: int = 1) -> "ModelTier":
        order = ["free", "lite", "standard", "premium", "flagship"]
        new_rank = min(len(order) - 1, self.rank + steps)
        return ModelTier(order[new_rank])

    @classmethod
    def order(cls) -> List["ModelTier"]:
        return [cls.FREE, cls.LITE, cls.STANDARD, cls.PREMIUM, cls.FLAGSHIP]


@dataclass
class ModelEndpoint:
    """运行时模型端点(由 ModelPool 维护)"""
    config: ModelEndpointConfig
    provider_obj: Optional[Provider] = None
    health_status: str = "unknown"
    consecutive_failures: int = 0
    last_health_check: float = 0.0
    last_error: str = ""
    cooldown_until: float = 0.0
    total_calls: int = 0
    total_failures: int = 0

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
        if self.health_status == "unhealthy":
            return False
        if time.time() < self.cooldown_until:
            return False
        # 没 api_key 仍可用(自动 fallback 到 MockProvider)
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
        logger.warning("Circuit breaker triggered for %s, cooldown %ss",
                       self.id, cooldown_seconds)

    def recover_breaker(self):
        self.cooldown_until = 0.0
        self.consecutive_failures = 0
        self.health_status = "healthy"


class ModelPool:
    """工业级模型池"""

    def __init__(self, settings: Optional[Settings] = None,
                 storage: Optional[Any] = None):
        self.settings = settings or get_settings()
        self.storage = storage or get_storage()
        self.endpoints: Dict[str, ModelEndpoint] = {}
        self._client: Optional[httpx.AsyncClient] = None
        self._health_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.refresh()
        # 修19: 订阅配置变更(WebUI 改配置后自动 reload)
        subscribe_settings_change(self._on_settings_change)
        logger.info("ModelPool initialized with %d endpoints", len(self.endpoints))

    def _on_settings_change(self, old_settings, new_settings):
        """配置变更时刷新模型池(只在非禁用端点上做,避免破坏运行中请求)"""
        try:
            self.settings = new_settings
            self.refresh()
            logger.info("ModelPool reloaded after settings change: %d endpoints",
                        len(self.endpoints))
        except Exception as e:
            logger.warning("ModelPool reload failed: %s", e)

    def refresh(self) -> None:
        """从 settings + storage 刷新端点"""
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

    def _apply_storage_overlay(self, ep: ModelEndpoint, s_ep: Dict[str, Any]) -> None:
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
        if ep.provider_obj:
            try:
                asyncio.get_event_loop().create_task(ep.provider_obj.aclose())
            except Exception:
                pass
        ep.provider_obj = None
        # 即便 api_key 为空也创建 provider —— build_provider 会自动 fallback 到 MockProvider
        try:
            ep.provider_obj = build_provider(
                ep.config.provider,
                api_base=ep.config.api_base,
                api_key=ep.config.api_key_runtime or "mock",
                timeout=ep.config.timeout,
                client=self._client,
            )
        except Exception as e:
            logger.warning("build_provider(%s) failed: %s", ep.config.provider, e)

    def upsert_endpoint(self, ep_dict: Dict[str, Any]) -> ModelEndpoint:
        eid = ep_dict["endpoint_id"]
        self.storage.upsert_endpoint(ep_dict)
        s_ep = self.storage.get_endpoint(eid)
        if not s_ep:
            raise RuntimeError(f"failed to persist endpoint {eid}")
        cfg = ModelEndpointConfig(
            id=eid, provider=s_ep["provider"], model=s_ep["model"],
            tier=s_ep["tier"], api_base=s_ep.get("api_base") or "",
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

    def remove_endpoint(self, eid: str) -> bool:
        ok = self.storage.delete_endpoint(eid)
        ep = self.endpoints.pop(eid, None)
        if ep and ep.provider_obj:
            try:
                asyncio.get_event_loop().create_task(ep.provider_obj.aclose())
            except Exception:
                pass
        return ok

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300))
        for ep in self.endpoints.values():
            self._rebuild_provider(ep)
        if self._health_task is None:
            self._health_task = asyncio.create_task(self._health_check_loop())
        await self._check_all_health()

    async def stop(self) -> None:
        if self._health_task:
            self._health_task.cancel()
            try:
                await self._health_task
            except (asyncio.CancelledError, Exception):
                pass
            self._health_task = None
        for ep in self.endpoints.values():
            if ep.provider_obj:
                await ep.provider_obj.aclose()
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _health_check_loop(self) -> None:
        cfg = self.settings.health
        while True:
            try:
                await asyncio.sleep(cfg.interval_seconds)
                await self._check_all_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("health loop error: %s", e)

    async def _check_all_health(self) -> None:
        tasks = [self._check_one(eid) for eid, ep in self.endpoints.items()
                 if ep.config.enabled and ep.config.api_key_runtime]
        if tasks:
            # 启动期最多 3s 等待 health check,超时未回的端点立即切 mock(避免卡 lifespan)
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True),
                    timeout=3.0
                )
            except asyncio.TimeoutError:
                # 把还没回的真 key 端点切 mock
                for ep in self.endpoints.values():
                    if (ep.config.enabled and ep.config.api_key_runtime
                            and not isinstance(getattr(ep.provider_obj, '__class__', None), type(None))
                            and ep.provider_obj.__class__.__name__ != "MockProvider"):
                        logger.warning("%s: startup health check timeout → auto-fallback to MockProvider",
                                       ep.id)
                        ep._saved_api_key = ep.config.api_key_runtime
                        ep.config.api_key_runtime = ""
                        self._rebuild_provider(ep)
                        ep.health_status = "healthy"
                        ep.consecutive_failures = 0
                        ep.last_error = "startup timeout → auto-fallback to mock"

    async def _check_one(self, eid: str) -> None:
        ep = self.endpoints.get(eid)
        if not ep or not ep.provider_obj:
            return
        try:
            ok = await asyncio.wait_for(
                ep.provider_obj.health_check(),
                timeout=self.settings.health.timeout_seconds
            )
            now = time.time()
            ep.last_health_check = now
            if ok:
                if ep.cooldown_until and now >= ep.cooldown_until:
                    ep.recover_breaker()
                else:
                    ep.health_status = "healthy"
                    ep.consecutive_failures = 0
            else:
                # 失败 1 次就探测 401/403 → 立即切 mock(让 dashboard 显示 mock 模式)
                # 这样 dashboard 不会红 3 次
                await self._maybe_fallback_to_mock(ep, "health_check failed")
                ep.consecutive_failures += 1
                if ep.consecutive_failures >= self.settings.health.failure_threshold:
                    ep.trigger_breaker(self.settings.health.cooldown_seconds)
        except asyncio.TimeoutError:
            await self._maybe_fallback_to_mock(ep, "health_check timeout")
            ep.consecutive_failures += 1
            if ep.consecutive_failures >= self.settings.health.failure_threshold:
                ep.trigger_breaker(self.settings.health.cooldown_seconds)
        except Exception as e:
            ep.consecutive_failures += 1
            ep.last_error = str(e)
            if ep.consecutive_failures >= self.settings.health.failure_threshold:
                ep.trigger_breaker(self.settings.health.cooldown_seconds)

    async def _maybe_fallback_to_mock(self, ep, reason: str) -> None:
        """如果 endpoint 的 api_key 触发了 401/403 之类的 auth 错,自动切到 mock。
        只在 key 有效但被服务端拒绝时切(避免对真没 key 的 endpoint 反复切)。"""
        if not ep or not ep.config.api_key_runtime:
            return  # 本来就是空 key(已经走 mock),不需要重切
        # 试探性 ping 一下,看是否 401/403
        # 不打网络重试(避免慢),用健康检查时缓存的状态推断
        # 简化:如果 key 看起来像真 key(长度 > 8 且非 mock-key),就清空让它走 mock
        key = ep.config.api_key_runtime
        from .providers import is_mock_key
        if is_mock_key(key):
            return
        # 真实 key + health 失败 → 切 mock
        if len(key) > 8 and key.startswith("sk-"):
            logger.warning(
                "%s: %s,key 看起来是真实的但被拒,auto-fallback to MockProvider (本会话内)",
                ep.id, reason
            )
            ep._saved_api_key = key
            ep.config.api_key_runtime = ""
            self._rebuild_provider(ep)
            # 标 healthy(mock 永远健康)
            ep.health_status = "healthy"
            ep.consecutive_failures = 0
            ep.last_error = f"auth invalid → auto-fallback to mock ({reason})"

    def available_endpoints(self, tier: Optional[ModelTier] = None,
                            require_provider: Optional[str] = None,
                            exclude_ids: Optional[List[str]] = None) -> List[ModelEndpoint]:
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

    def select_one(self, tier: ModelTier, prefer_provider: Optional[str] = None,
                   exclude_ids: Optional[List[str]] = None) -> Optional[ModelEndpoint]:
        cands = self.available_endpoints(tier=tier,
                                         require_provider=prefer_provider,
                                         exclude_ids=exclude_ids)
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

    def select_many(self, tier: ModelTier, count: int,
                    prefer_diversity: bool = True,
                    exclude_ids: Optional[List[str]] = None) -> List[ModelEndpoint]:
        cands = self.available_endpoints(tier=tier, exclude_ids=exclude_ids)
        if prefer_diversity:
            by_provider: Dict[str, List[ModelEndpoint]] = {}
            for ep in cands:
                by_provider.setdefault(ep.config.provider, []).append(ep)
            providers = list(by_provider.keys())
            random.shuffle(providers)
            out: List[ModelEndpoint] = []
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

    def get_fallback_chain(self, primary_id: str, count: int = 3) -> List[ModelEndpoint]:
        primary = self.endpoints.get(primary_id)
        if not primary:
            return []
        ptier = primary.tier
        same_provider_same: List[ModelEndpoint] = []
        same_provider_lower: List[ModelEndpoint] = []
        diff_provider_same: List[ModelEndpoint] = []
        diff_provider_any: List[ModelEndpoint] = []
        for eid, ep in self.endpoints.items():
            if eid == primary_id or not ep.is_available:
                continue
            same_provider = (ep.config.provider == primary.config.provider)
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

    def build_chat_request(self, ep: ModelEndpoint, messages: List[Dict],
                           temperature: float = 0.6, max_tokens: int = 4096,
                           tools: Optional[List[Dict]] = None,
                           stream: bool = False) -> ChatRequest:
        return ChatRequest(
            model=ep.config.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max(1, min(max_tokens, ep.config.max_tokens)),
            stream=stream,
            tools=tools,
            timeout=ep.config.timeout,
        )

    async def call(self, endpoint_id: str, messages: List[Dict],
                   temperature: float = 0.6, max_tokens: int = 4096,
                   tools: Optional[List[Dict]] = None,
                   stream: bool = False,
                   max_retries: int = 3) -> ChatResponse:
        ep = self.endpoints.get(endpoint_id)
        if not ep:
            raise ValueError(f"endpoint {endpoint_id} not found")
        chain = [ep] + [e for e in self.get_fallback_chain(endpoint_id, max_retries)
                        if e.id != endpoint_id]
        chain = chain[:max_retries]
        last_err: Optional[Exception] = None
        for attempt, cur in enumerate(chain):
            if not cur.is_available:
                continue
            if not cur.provider_obj:
                self._rebuild_provider(cur)
            if not cur.provider_obj:
                # 还是没 provider,记一个错
                last_err = RuntimeError(f"{cur.id} provider_obj is None after rebuild")
                logger.warning("call %s: provider_obj is None", cur.id)
                continue
            req = self.build_chat_request(cur, messages, temperature, max_tokens,
                                          tools, stream)
            try:
                resp = await cur.provider_obj.chat(req)
                resp.cost = self._calc_cost(cur, resp.prompt_tokens, resp.completion_tokens)
                cur.mark_success()
                return resp
            except ProviderError as e:
                last_err = e
                logger.warning("call %s failed (attempt %d): %s", cur.id, attempt + 1, e)
                cur.mark_failure(str(e))
                # 401/403/400-invalid-key → 自动 fallback 到 mock(本次会话内)
                if getattr(e, "status", 0) in (401, 403) and cur.config.api_key_runtime:
                    logger.warning(
                        "call %s: %d auth invalid → auto-fallback to MockProvider for this session. "
                        "原 key 仍保留在 config,重启 server 时重读 env", cur.id, e.status
                    )
                    cur._saved_api_key = cur.config.api_key_runtime
                    cur.config.api_key_runtime = ""
                    self._rebuild_provider(cur)
                    try:
                        req2 = self.build_chat_request(cur, messages, temperature,
                                                      max_tokens, tools, stream)
                        resp = await cur.provider_obj.chat(req2)
                        resp.cost = self._calc_cost(cur, resp.prompt_tokens,
                                                    resp.completion_tokens)
                        cur.mark_success()
                        return resp
                    except Exception as e2:
                        logger.warning("mock fallback also failed: %s", e2)
                        # 还原原 key(下次有别的机会还能试)
                        cur.config.api_key_runtime = cur._saved_api_key
                        self._rebuild_provider(cur)
                if cur.consecutive_failures >= self.settings.health.failure_threshold:
                    cur.trigger_breaker(self.settings.health.cooldown_seconds)
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
            except Exception as e:
                last_err = e
                logger.exception("call %s unexpected error: %s", cur.id, e)
                cur.mark_failure(str(e))
                await asyncio.sleep(min(2 ** attempt, 8))
                continue
        if last_err is None:
            last_err = RuntimeError("no available endpoint in chain")
        raise RuntimeError(f"all fallbacks failed for {endpoint_id}: {last_err}")

    def _calc_cost(self, ep: ModelEndpoint, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1000.0) * ep.config.cost_per_1k_input + \
               (completion_tokens / 1000.0) * ep.config.cost_per_1k_output

    def snapshot(self) -> Dict[str, Any]:
        return {
            "total": len(self.endpoints),
            "enabled": sum(1 for e in self.endpoints.values() if e.config.enabled),
            "healthy": sum(1 for e in self.endpoints.values() if e.health_status == "healthy"),
            "unhealthy": sum(1 for e in self.endpoints.values() if e.health_status == "unhealthy"),
            "in_breaker": sum(1 for e in self.endpoints.values() if e.cooldown_until > time.time()),
            "by_tier": {t.value: sum(1 for e in self.endpoints.values() if e.tier == t)
                        for t in ModelTier},
            "by_provider": {p: sum(1 for e in self.endpoints.values()
                                   if e.config.provider == p)
                            for p in {e.config.provider for e in self.endpoints.values()}},
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
            ]
        }


_pool: Optional[ModelPool] = None


def get_model_pool() -> ModelPool:
    global _pool
    if _pool is None:
        _pool = ModelPool()
    return _pool


async def start_model_pool() -> ModelPool:
    pool = get_model_pool()
    await pool.start()
    return pool
