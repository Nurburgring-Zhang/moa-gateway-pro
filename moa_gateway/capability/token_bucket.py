"""token_bucket — 经典 Token Bucket 限流算法 (RFC 启发 + Lazy Refill)

来源: 参考表 F-32 / R-17 强化 (MoA Gateway Pro — capability layer)

经典 token bucket 语义:
  - bucket 有两个关键参数:
    * capacity   : 桶容量 (最大令牌数,也是突发上限)
    * refill_rate: 稳态补充速率 (tokens / second)
  - 每个 request 消耗 1 token (或 N 个),没有足够 token → 拒绝 (HTTP 429)
  - 桶中 token 数不会超过 capacity,多余部分被丢弃

实现要点 (与 naive "每秒计数" 区别):
  1. **Lazy refill**: 不起后台线程,只在 try_consume / peek / wait_time 时
     算出 elapsed = now - last_refill_ts,然后 tokens += elapsed * rate
     优点: 零额外线程,O(1) 内存,无时钟漂移
  2. **连续模型**: token 可以是 float(0.5 token 也算半个,虽通常整数
     但 try_consume 接受 int 内部按 float 算),state 报告精度高
  3. **线程安全**: 每个 bucket 一把 Lock,MultiKeyTokenBucket 用 RLock
     保护 dict 读改写,内部 bucket 自己有锁,所以多 key 之间不会互相阻塞
  4. **LRU 上限**: MultiKeyTokenBucket 默认 10000 key,超过时
     按"最久未使用"驱逐 (用 OrderedDict + last_access_ts)
  5. **降级容错**:
     - rate < 0 → 视为 0 (永不满,所有请求被拒) 并 warnings
     - capacity <= 0 → 所有请求被拒 (安全降级)
     - tokens <= 0 → 直接 False (不消耗也不 refill)

设计选择 (vs 滑动窗口 / 漏桶):
  - token bucket 允许突发: 如果桶满,前 capacity 个请求瞬间通过
  - 适合"60 RPM 但允许 60 个瞬时爆发"的 API 限流场景
  - 比 sliding window 内存小 (O(1) per key, vs O(N) per window)
"""
from __future__ import annotations

import threading
import time
import warnings
from collections import OrderedDict
from typing import Any, Dict, Optional


# ============ Constants ============

DEFAULT_MULTIKEY_CAPACITY: int = 10000
"""MultiKeyTokenBucket 默认 LRU 容量上限"""

DEFAULT_MULTIKEY_IDLE_SECONDS: float = 3600.0
"""cleanup_inactive 默认阈值: 1 小时未访问的 bucket 视为可清理"""


# ============ TokenBucket ============

class TokenBucket:
    """单 key 的 Token Bucket 限流器

    经典语义:
      - 初始时桶满 (capacity tokens)
      - 每次 try_consume(n) 试图扣 n 个 token
      - 不足时 → False (HTTP 429 等价)
      - 时间流逝自动补充 (lazy: 在调用 try_consume / peek 时才计算)

    Thread-safe: 内部用 threading.Lock 保护所有状态读写。
    适合作为模块级单例 / 类属性 / 多 key registry 中的子对象。

    Example:
        >>> tb = TokenBucket(capacity=60, refill_rate=1.0)  # 60 RPM
        >>> tb.try_consume()   # True
        >>> tb.try_consume(70) # False (超过剩余)
        >>> tb.wait_time(5)    # ~5.0 seconds
    """

    __slots__ = (
        "capacity",
        "refill_rate",
        "_tokens",
        "_last_refill_ts",
        "_lock",
        "_created_ts",
        "_total_consumed",
        "_total_denied",
    )

    def __init__(self, capacity: int, refill_rate: float) -> None:
        """构造一个 token bucket

        Args:
            capacity: 桶容量 (最大 token 数, 突发上限). 必须 >= 0.
                      0 表示永久拒绝 (沙箱/熔断场景).
            refill_rate: 稳态补充速率 (tokens / second).
                          负数会被降级为 0 并发出 UserWarning.
                          0 表示永不再补充 (用完即止).

        Raises:
            TypeError: capacity / refill_rate 不是数字
            ValueError: capacity < 0
        """
        if not isinstance(capacity, (int, float)) or isinstance(capacity, bool):
            raise TypeError(f"capacity must be numeric, got {type(capacity).__name__}")
        if not isinstance(refill_rate, (int, float)) or isinstance(refill_rate, bool):
            raise TypeError(f"refill_rate must be numeric, got {type(refill_rate).__name__}")
        if capacity < 0:
            raise ValueError(f"capacity must be >= 0, got {capacity}")

        if refill_rate < 0:
            warnings.warn(
                f"refill_rate={refill_rate} is negative; degrading to 0 (bucket will never refill)",
                UserWarning,
                stacklevel=2,
            )
            refill_rate = 0.0

        self.capacity: float = float(capacity)
        self.refill_rate: float = float(refill_rate)
        self._tokens: float = float(capacity)   # 初始满桶
        self._last_refill_ts: float = time.monotonic()
        self._lock: threading.Lock = threading.Lock()
        self._created_ts: float = time.time()
        self._total_consumed: int = 0
        self._total_denied: int = 0

    # -------- 内部辅助 --------

    def _refill_locked(self, now: Optional[float] = None) -> None:
        """在已加锁的前提下,按 elapsed * rate 补 token 到 capacity 上限

        调用方必须持有 self._lock。
        """
        if now is None:
            now = time.monotonic()
        elapsed = now - self._last_refill_ts
        if elapsed > 0 and self.refill_rate > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
        self._last_refill_ts = now

    # -------- 公共 API --------

    def try_consume(self, tokens: int = 1) -> bool:
        """尝试消耗 N 个 token

        Args:
            tokens: 要扣的 token 数,必须为正整数 (<=0 直接 False).

        Returns:
            True  = 成功扣减
            False = token 不足 (HTTP 429 语义);调用方应回退 / 排队 / 拒绝
        """
        try:
            if not isinstance(tokens, (int, float)):
                return False
            tokens_f = float(tokens)
            if tokens_f <= 0:
                return False

            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens_f:
                    self._tokens -= tokens_f
                    self._total_consumed += int(tokens_f) if tokens_f.is_integer() else 1
                    return True
                self._total_denied += 1
                return False
        except Exception:
            # 兜底:任何异常都视为拒绝,绝不让限流器崩溃请求路径
            return False

    def peek(self) -> float:
        """查看当前可用 token 数 (不消耗,会触发 lazy refill)

        Returns:
            当前 token 数,精度 float,范围 [0, capacity]
        """
        try:
            with self._lock:
                self._refill_locked()
                return self._tokens
        except Exception:
            return 0.0

    def wait_time(self, tokens: int = 1) -> float:
        """算"再等多少秒"才能成功 consume(tokens)

        Args:
            tokens: 要扣的 token 数,<=0 视为 0.0 秒(无需等待,虽然仍会拒绝).

        Returns:
            等待秒数:
              * 0.0 = 当前就能 consume (即使 0 容量桶也是 0,因为 0<=tokens 永远不通过)
              * > 0.0 = 需要等这么久
              * float('inf') = 桶永远不会满 (rate=0 且当前不足)
        """
        try:
            if not isinstance(tokens, (int, float)):
                return 0.0
            tokens_f = float(tokens)
            if tokens_f <= 0:
                return 0.0

            with self._lock:
                self._refill_locked()
                if self._tokens >= tokens_f:
                    return 0.0
                if self.refill_rate <= 0:
                    return float("inf")
                deficit = tokens_f - self._tokens
                return deficit / self.refill_rate
        except Exception:
            return 0.0

    def reset(self) -> None:
        """重置 bucket: 满桶 + 计数清零 + 时间戳更新"""
        with self._lock:
            self._tokens = self.capacity
            self._last_refill_ts = time.monotonic()
            self._total_consumed = 0
            self._total_denied = 0

    def state(self) -> Dict[str, Any]:
        """返回 bucket 当前快照 (用于监控 / 调试 / 导出 metrics)

        Returns:
            dict with keys:
              capacity, refill_rate, tokens, available (int floor),
              last_refill_ts, created_ts,
              total_consumed, total_denied, denied_ratio
        """
        try:
            with self._lock:
                self._refill_locked()
                tokens = self._tokens
                consumed = self._total_consumed
                denied = self._total_denied
                last_refill = self._last_refill_ts
                created = self._created_ts

            total = consumed + denied
            return {
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
                "tokens": tokens,
                "available": int(tokens) if tokens >= 1.0 else 0,
                "last_refill_ts": last_refill,
                "created_ts": created,
                "total_consumed": consumed,
                "total_denied": denied,
                "denied_ratio": (denied / total) if total > 0 else 0.0,
            }
        except Exception:
            return {
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
                "tokens": 0.0,
                "available": 0,
                "last_refill_ts": 0.0,
                "created_ts": 0.0,
                "total_consumed": 0,
                "total_denied": 0,
                "denied_ratio": 0.0,
            }


# ============ MultiKeyTokenBucket ============

class MultiKeyTokenBucket:
    """多 key 的 Token Bucket 注册表 (按 key 维度独立限流)

    典型场景:
      - 每个 API key / user id / IP 单独限流
      - 每个 provider / route 单独限流

    设计:
      - dict[ key -> TokenBucket ]
      - LRU 上限 (默认 10000),超出时按 last_access_ts 淘汰最久未用的
      - 每次 get_bucket / try_consume 都会更新 last_access_ts
      - cleanup_inactive(max_idle_seconds) 用于定期清理冷 key

    Thread-safety:
      - 外部 RLock 保护 dict 自身(增/删/evict)
      - 内部每个 TokenBucket 自己的 Lock 保护状态
      - 因此多线程读 dict 和多 key 并发 consume 都不会互相死锁
    """

    __slots__ = (
        "default_capacity",
        "default_refill_rate",
        "_buckets",
        "_max_keys",
        "_lock",
    )

    def __init__(
        self,
        default_capacity: int,
        default_refill_rate: float,
        max_keys: int = DEFAULT_MULTIKEY_CAPACITY,
    ) -> None:
        """构造多 key 注册表

        Args:
            default_capacity: 新建 bucket 的默认 capacity
            default_refill_rate: 新建 bucket 的默认 refill_rate (tokens/sec)
            max_keys: LRU 上限,默认 10000,<=0 表示不限制
        """
        if not isinstance(default_capacity, (int, float)) or isinstance(default_capacity, bool):
            raise TypeError(f"default_capacity must be numeric, got {type(default_capacity).__name__}")
        if default_capacity < 0:
            raise ValueError(f"default_capacity must be >= 0, got {default_capacity}")
        if not isinstance(default_refill_rate, (int, float)) or isinstance(default_refill_rate, bool):
            raise TypeError(f"default_refill_rate must be numeric, got {type(default_refill_rate).__name__}")

        self.default_capacity: float = float(default_capacity)
        self.default_refill_rate: float = float(default_refill_rate)
        self._max_keys: int = int(max_keys)
        self._buckets: "OrderedDict[str, TokenBucket]" = OrderedDict()
        self._lock: threading.RLock = threading.RLock()

    # -------- 内部辅助 --------

    def _evict_if_needed_locked(self) -> None:
        """如果超过 max_keys,弹出最久未访问的 bucket

        调用方必须持有 self._lock。
        """
        if self._max_keys <= 0:
            return
        while len(self._buckets) >= self._max_keys:
            self._buckets.popitem(last=False)  # FIFO 头 = LRU 尾

    def _touch_locked(self, key: str) -> None:
        """更新 key 的访问顺序为最近 (LRU)

        调用方必须持有 self._lock。
        """
        if key in self._buckets:
            self._buckets.move_to_end(key, last=True)

    # -------- 公共 API --------

    def get_bucket(self, key: str) -> TokenBucket:
        """获取或懒创建 key 对应的 TokenBucket

        Args:
            key: 任意可哈希字符串,空字符串也允许 (作为 default key)

        Returns:
            该 key 对应的 TokenBucket (如果不存在则用 default 创建)
        """
        try:
            k = str(key) if key is not None else ""
            with self._lock:
                bucket = self._buckets.get(k)
                if bucket is None:
                    bucket = TokenBucket(
                        capacity=int(self.default_capacity),
                        refill_rate=self.default_refill_rate,
                    )
                    self._evict_if_needed_locked()
                    self._buckets[k] = bucket
                else:
                    self._touch_locked(k)
                return bucket
        except Exception:
            # 兜底:返回一个临时 bucket,不影响主流程
            return TokenBucket(
                capacity=int(self.default_capacity),
                refill_rate=self.default_refill_rate,
            )

    def try_consume(self, key: str, tokens: int = 1) -> bool:
        """key 维度 try_consume,自动懒创建 bucket

        Args:
            key: 限流维度 (user/api-key/route)
            tokens: 消耗 token 数,默认 1

        Returns:
            True = 通过, False = 被限流
        """
        try:
            bucket = self.get_bucket(key)
            return bucket.try_consume(tokens)
        except Exception:
            return False

    def cleanup_inactive(self, max_idle_seconds: float = DEFAULT_MULTIKEY_IDLE_SECONDS) -> int:
        """清理长期未访问的 bucket (按 bucket 的 last_refill_ts 与 now 差判断)

        Args:
            max_idle_seconds: 超过这么久没访问的 bucket 视为可清理,默认 3600s

        Returns:
            实际清理的 bucket 数量
        """
        try:
            with self._lock:
                now = time.monotonic()
                to_delete = []
                for key, bucket in self._buckets.items():
                    # 注意:这里 peek() 会再次拿 bucket 锁,顺序安全(RLock + 不同对象)
                    with bucket._lock:
                        idle = now - bucket._last_refill_ts
                    if idle >= max_idle_seconds:
                        to_delete.append(key)
                for k in to_delete:
                    self._buckets.pop(k, None)
                return len(to_delete)
        except Exception:
            return 0

    def size(self) -> int:
        """当前 bucket 数量"""
        try:
            with self._lock:
                return len(self._buckets)
        except Exception:
            return 0

    def all_states(self) -> Dict[str, Dict[str, Any]]:
        """导出所有 bucket 的 state 快照 (用于监控导出)

        Returns:
            { key -> state_dict } 形式,顺序按 LRU (最近访问的在最后)
        """
        try:
            result: Dict[str, Dict[str, Any]] = {}
            with self._lock:
                keys = list(self._buckets.keys())
                for k in keys:
                    bucket = self._buckets.get(k)
                    if bucket is not None:
                        self._touch_locked(k)
                        result[k] = bucket.state()
            return result
        except Exception:
            return {}

    def reset_all(self) -> None:
        """重置所有 bucket (满桶 + 计数清零);用于测试或全局复位"""
        try:
            with self._lock:
                for bucket in self._buckets.values():
                    bucket.reset()
        except Exception:
            pass


__all__ = [
    "TokenBucket",
    "MultiKeyTokenBucket",
    "DEFAULT_MULTIKEY_CAPACITY",
    "DEFAULT_MULTIKEY_IDLE_SECONDS",
]
