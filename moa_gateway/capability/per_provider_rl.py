"""per_provider_rl — Per-provider 限流 + Per-model 并发池

来源: 04 moa-main-commercial (per-provider rate limiting + 并发池)

真实实现:
- 每个 provider 独立维护:
  * 滑窗 usage 记录(60s 内)
  * cooldown 截止时间(429 后)
  * 并发信号量(per-provider)
- 限流 3 维:RPM (请求数)、IPM (input tokens)、concurrent (并发)
- 拒绝时给出 retry_after_seconds:
  * RPM 超限 → 60/max_rpm(下一请求理论时间)
  * IPM 超限 → 60/max_ipm
  * 并发超限 → 0(立即重试,等别人完成)
- cooldown 跟踪: 429 后 provider 进入冷却期,期内所有请求拒绝
- 多 provider 聚合: MultiProviderLimiter 统一调度

非 mock,所有计算为确定性的数学公式 + 真实时间记录。
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any


# ============ Constants ============

WINDOW_SECONDS: float = 60.0

# 并发槽位默认值(若 acquire_slot 没传 max_concurrent)
DEFAULT_MAX_CONCURRENT: int = 8


# ============ Dataclasses ============

@dataclass
class ProviderLimit:
    """单个 provider 的限流配置。"""
    provider: str
    max_requests_per_minute: int
    max_inputs_per_minute: int
    max_concurrent: int
    cooldown_seconds_after_429: float = 60.0

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("provider must be non-empty string")
        if self.max_requests_per_minute < 0:
            raise ValueError(
                f"max_requests_per_minute must be >= 0, got {self.max_requests_per_minute}"
            )
        if self.max_inputs_per_minute < 0:
            raise ValueError(
                f"max_inputs_per_minute must be >= 0, got {self.max_inputs_per_minute}"
            )
        if self.max_concurrent < 0:
            raise ValueError(
                f"max_concurrent must be >= 0, got {self.max_concurrent}"
            )
        if self.cooldown_seconds_after_429 < 0:
            raise ValueError(
                f"cooldown_seconds_after_429 must be >= 0, got {self.cooldown_seconds_after_429}"
            )


@dataclass
class UsageRecord:
    """一次使用记录(可聚合,默认 request_count=1)。"""
    provider: str
    timestamp: float
    request_count: int = 1
    input_tokens: int = 0

    def __post_init__(self) -> None:
        if self.request_count < 0:
            raise ValueError(f"request_count must be >= 0, got {self.request_count}")
        if self.input_tokens < 0:
            raise ValueError(f"input_tokens must be >= 0, got {self.input_tokens}")


@dataclass
class RateLimitDecision:
    """限流检查结果。"""
    allowed: bool
    reason: str
    retry_after_seconds: Optional[float]
    current_rpm: float
    current_ipm: float
    current_concurrent: int


# ============ Per-provider Limiter ============

class ProviderLimiter:
    """单个 provider 的限流器(线程安全)。"""

    def __init__(self, limit: ProviderLimit) -> None:
        self.limit = limit
        self._history: List[UsageRecord] = []
        self._cooldown_until: float = 0.0
        self._concurrent: int = 0
        self._lock = threading.RLock()
        self._slot_lock = threading.Lock()
        self._slot_cond = threading.Condition(self._slot_lock)

    # ---------- Sliding window ----------

    def _usage_window(self, now: float, window_seconds: float = WINDOW_SECONDS) -> List[UsageRecord]:
        """获取 [now-window, now] 内的 usage 记录(已 prune 窗口外)。"""
        with self._lock:
            cutoff = now - window_seconds
            # 复用 prune:从左 pop 旧于 cutoff 的
            while self._history and self._history[0].timestamp < cutoff:
                self._history.pop(0)
            return list(self._history)

    def _prune(self, now: float, window_seconds: float = WINDOW_SECONDS) -> None:
        with self._lock:
            cutoff = now - window_seconds
            while self._history and self._history[0].timestamp < cutoff:
                self._history.pop(0)

    def _current_rpm(self, now: float) -> float:
        """60s 窗口内 sum(request_count)。"""
        window = self._usage_window(now, WINDOW_SECONDS)
        return float(sum(r.request_count for r in window))

    def _current_ipm(self, now: float) -> float:
        """60s 窗口内 sum(input_tokens)。"""
        window = self._usage_window(now, WINDOW_SECONDS)
        return float(sum(r.input_tokens for r in window))

    def _current_concurrent(self) -> int:
        with self._slot_lock:
            return self._concurrent

    # ---------- Cooldown ----------

    def mark_429(self, duration_seconds: Optional[float] = None, at: Optional[float] = None) -> float:
        """标记 429,设置 cooldown 截止时间。返回 cooldown_until 绝对时间戳。"""
        if at is None:
            at = time.time()
        if duration_seconds is None:
            duration_seconds = self.limit.cooldown_seconds_after_429
        if duration_seconds < 0:
            raise ValueError(f"duration_seconds must be >= 0, got {duration_seconds}")
        with self._lock:
            new_until = at + duration_seconds
            if new_until > self._cooldown_until:
                self._cooldown_until = new_until
            return self._cooldown_until

    def is_in_cooldown(self, at: Optional[float] = None) -> bool:
        if at is None:
            at = time.time()
        with self._lock:
            return at < self._cooldown_until

    def cooldown_remaining(self, at: Optional[float] = None) -> float:
        if at is None:
            at = time.time()
        with self._lock:
            remaining = self._cooldown_until - at
            return remaining if remaining > 0 else 0.0

    # ---------- Rate limit check ----------

    def check_rate_limit(
        self,
        concurrent_now: Optional[int] = None,
        at: Optional[float] = None,
    ) -> RateLimitDecision:
        """检查是否允许下一次请求。"""
        if at is None:
            at = time.time()
        with self._lock:
            self._prune(at, WINDOW_SECONDS)
            rpm = self._current_rpm(at)
            ipm = self._current_ipm(at)

        if concurrent_now is None:
            concurrent_now = self._current_concurrent()

        max_rpm = self.limit.max_requests_per_minute
        max_ipm = self.limit.max_inputs_per_minute
        max_conc = self.limit.max_concurrent

        # cooldown 期内一律拒绝
        if self.is_in_cooldown(at):
            remain = self.cooldown_remaining(at)
            return RateLimitDecision(
                allowed=False,
                reason=f"cooldown: provider {self.limit.provider!r} in 429 cooldown",
                retry_after_seconds=remain if remain > 0 else 0.0,
                current_rpm=rpm,
                current_ipm=ipm,
                current_concurrent=concurrent_now,
            )

        # 0 max → always deny(任何 max_*=0 等同于封禁)
        if max_rpm == 0:
            return RateLimitDecision(
                allowed=False,
                reason=f"rpm denied: provider {self.limit.provider!r} max_rpm=0",
                retry_after_seconds=None,
                current_rpm=rpm,
                current_ipm=ipm,
                current_concurrent=concurrent_now,
            )
        if max_ipm == 0:
            return RateLimitDecision(
                allowed=False,
                reason=f"ipm denied: provider {self.limit.provider!r} max_ipm=0",
                retry_after_seconds=None,
                current_rpm=rpm,
                current_ipm=ipm,
                current_concurrent=concurrent_now,
            )

        # RPM 检查
        if rpm >= max_rpm:
            return RateLimitDecision(
                allowed=False,
                reason=f"rpm exceeded: {rpm} >= {max_rpm}",
                retry_after_seconds=60.0 / max_rpm,
                current_rpm=rpm,
                current_ipm=ipm,
                current_concurrent=concurrent_now,
            )
        # IPM 检查
        if ipm >= max_ipm:
            return RateLimitDecision(
                allowed=False,
                reason=f"ipm exceeded: {ipm} >= {max_ipm}",
                retry_after_seconds=60.0 / max_ipm,
                current_rpm=rpm,
                current_ipm=ipm,
                current_concurrent=concurrent_now,
            )
        # 并发检查
        if max_conc <= 0 or concurrent_now >= max_conc:
            return RateLimitDecision(
                allowed=False,
                reason=f"concurrent pool full: {concurrent_now} >= {max_conc}",
                retry_after_seconds=0.0,
                current_rpm=rpm,
                current_ipm=ipm,
                current_concurrent=concurrent_now,
            )
        # 全部通过
        return RateLimitDecision(
            allowed=True,
            reason="ok: within all limits",
            retry_after_seconds=None,
            current_rpm=rpm,
            current_ipm=ipm,
            current_concurrent=concurrent_now,
        )

    # ---------- Record usage ----------

    def record_usage(
        self,
        request_count: int = 1,
        input_tokens: int = 0,
        at: Optional[float] = None,
    ) -> None:
        """追加一次 usage 记录到滑窗。"""
        if at is None:
            at = time.time()
        rec = UsageRecord(
            provider=self.limit.provider,
            timestamp=float(at),
            request_count=int(request_count),
            input_tokens=int(input_tokens),
        )
        with self._lock:
            self._history.append(rec)
            # 顺手 prune 旧的,避免 history 无界增长
            cutoff = at - WINDOW_SECONDS
            while self._history and self._history[0].timestamp < cutoff:
                self._history.pop(0)

    # ---------- Concurrency slot ----------

    def try_acquire_slot(self) -> bool:
        """非阻塞尝试获取一个并发槽位。返回 True 表示获取成功。"""
        with self._slot_cond:
            if self._concurrent < self.limit.max_concurrent:
                self._concurrent += 1
                return True
            return False

    def release_slot(self) -> None:
        """释放一个并发槽位。"""
        with self._slot_cond:
            if self._concurrent > 0:
                self._concurrent -= 1
            self._slot_cond.notify_all()

    def acquire_slot(self, max_concurrent: Optional[int] = None) -> Optional[Any]:
        """获取一个 semaphore-style 上下文管理器。

        - 若 provider 池未满,返回 contextmanager(__enter__ 占用,__exit__ 释放)
        - 若已满(max_concurrent<=0 或已达 max),返回 None
        """
        if max_concurrent is None:
            max_concurrent = self.limit.max_concurrent
        if max_concurrent <= 0:
            return None
        if not self.try_acquire_slot():
            return None

        # 实际释放计数
        acquired = True
        limiter = self

        @contextmanager
        def _slot_cm():
            nonlocal acquired
            try:
                yield
            finally:
                if acquired:
                    limiter.release_slot()
                    acquired = False

        return _slot_cm()

    # ---------- Introspection ----------

    def history_size(self) -> int:
        with self._lock:
            return len(self._history)

    def snapshot(self) -> Dict[str, Any]:
        """当前状态快照(用于 JSON 序列化 / 调试)。"""
        with self._lock:
            return {
                "provider": self.limit.provider,
                "limit": asdict(self.limit),
                "history_size": len(self._history),
                "cooldown_until": self._cooldown_until,
                "concurrent": self._current_concurrent(),
            }


# ============ Multi-provider Aggregator ============

class MultiProviderLimiter:
    """多 provider 限流聚合。"""

    def __init__(self, limits: Dict[str, ProviderLimit]) -> None:
        if not limits:
            raise ValueError("limits must not be empty")
        self._limiters: Dict[str, ProviderLimiter] = {
            p: ProviderLimiter(lim) for p, lim in limits.items()
        }

    def _get(self, provider: str) -> ProviderLimiter:
        if provider not in self._limiters:
            raise KeyError(f"provider {provider!r} not in limits")
        return self._limiters[provider]

    def check(
        self,
        provider: str,
        concurrent_now: Optional[int] = None,
        at: Optional[float] = None,
    ) -> RateLimitDecision:
        return self._get(provider).check_rate_limit(concurrent_now=concurrent_now, at=at)

    def record(
        self,
        provider: str,
        request_count: int = 1,
        input_tokens: int = 0,
        at: Optional[float] = None,
    ) -> None:
        self._get(provider).record_usage(request_count=request_count, input_tokens=input_tokens, at=at)

    def mark_429(
        self,
        provider: str,
        duration_seconds: Optional[float] = None,
        at: Optional[float] = None,
    ) -> float:
        return self._get(provider).mark_429(duration_seconds=duration_seconds, at=at)

    def is_in_cooldown(self, provider: str, at: Optional[float] = None) -> bool:
        return self._get(provider).is_in_cooldown(at=at)

    def acquire_slot(self, provider: str, max_concurrent: Optional[int] = None) -> Optional[Any]:
        return self._get(provider).acquire_slot(max_concurrent=max_concurrent)

    def providers(self) -> List[str]:
        return list(self._limiters.keys())

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {p: lim.snapshot() for p, lim in self._limiters.items()}


# ============ JSON helpers ============

def decision_to_dict(decision: RateLimitDecision) -> Dict[str, Any]:
    d = asdict(decision)
    # 已经是 dict 了
    return d


def decision_to_json(decision: RateLimitDecision) -> str:
    return json.dumps(decision_to_dict(decision), ensure_ascii=False, sort_keys=True)


def snapshot_to_json(multi: MultiProviderLimiter) -> str:
    return json.dumps(multi.snapshot(), ensure_ascii=False, sort_keys=True)


# ============ Presets ============

def make_default_limits() -> Dict[str, ProviderLimit]:
    """3 个常见 provider 的默认限流配置(测试 / 启动用)。"""
    return {
        "openai": ProviderLimit(
            provider="openai",
            max_requests_per_minute=60,
            max_inputs_per_minute=200_000,
            max_concurrent=20,
            cooldown_seconds_after_429=60.0,
        ),
        "anthropic": ProviderLimit(
            provider="anthropic",
            max_requests_per_minute=50,
            max_inputs_per_minute=150_000,
            max_concurrent=15,
            cooldown_seconds_after_429=60.0,
        ),
        "together": ProviderLimit(
            provider="together",
            max_requests_per_minute=120,
            max_inputs_per_minute=500_000,
            max_concurrent=30,
            cooldown_seconds_after_429=30.0,
        ),
    }


__all__ = [
    "WINDOW_SECONDS",
    "DEFAULT_MAX_CONCURRENT",
    "ProviderLimit",
    "UsageRecord",
    "RateLimitDecision",
    "ProviderLimiter",
    "MultiProviderLimiter",
    "decision_to_dict",
    "decision_to_json",
    "snapshot_to_json",
    "make_default_limits",
]
