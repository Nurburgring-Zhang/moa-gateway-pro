"""moa_gateway.capability.request_dedup — Request Dedup (I-13 / A-23 加强版)

来源: 参考表 I-13 Request Dedup + A-23 Request Dedup (加强版)
- I-13: 基础去重(同 method+path+body → 同 hash)
- A-23 加强版: 三种策略(EXACT / NORMALIZED / SEMANTIC)+ TTL + LRU + 响应缓存

提供:
- DedupStrategy: 三种去重策略
- hash_request: 把 (method, path, body) 映射到 hash 字符串
- DedupEntry: 索引中存储的条目(含元数据 + 响应缓存)
- RequestDedupIndex: LRU + TTL 索引(RLock 保护)

设计目标:
- 真实可用: 三种策略各自准确 ——
    EXACT 完全区分大小写,
    NORMALIZED 大小写不敏感 + 折叠空白 + 排序 body 键,
    SEMANTIC 改 1 词仍命中(基于 simhash + 汉明距离阈值)
- 线程安全: RLock 保护所有公开方法,允许 stats() 内部调用 size() 等嵌套
- 性能: 10000 次 check < 100ms(EXACT/NORMALIZED 走 O(1) 字典查找)
- 兼容 unicode / 中文 / 空 body / 嵌套 body
- 响应缓存: 之前已有 response 可直接复用,避免重复调用上游

使用示例:
    from moa_gateway.capability.request_dedup import (
        DedupStrategy, RequestDedupIndex, hash_request,
    )

    idx = RequestDedupIndex(strategy=DedupStrategy.NORMALIZED, ttl_seconds=60)

    # 1. 先查
    existing = idx.check("POST", "/v1/chat", {"msg": "hi"}, source="user_42")
    if existing is not None and existing.response is not None:
        return existing.response  # 复用缓存响应

    # 2. 真正处理
    response = do_real_request(...)

    # 3. 记录(供后续 dedup 命中)
    idx.record("POST", "/v1/chat", {"msg": "hi"}, source="user_42", response=response)
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DedupStrategy",
    "DedupEntry",
    "RequestDedupIndex",
    "hash_request",
]


# ============================================================================
# DedupStrategy
# ============================================================================


class DedupStrategy(str, Enum):
    """请求去重策略

    - EXACT:      字节级完全相同(区分大小写、空白、键顺序)
    - NORMALIZED: 标准化后比较(小写 + 折叠空白 + body 键排序 + 字符串值小写)
    - SEMANTIC:   语义近重复(simhash 64-bit + 汉明距离阈值,默认 5 bits)
    """
    EXACT = "exact"
    NORMALIZED = "normalized"
    SEMANTIC = "semantic"


# ============================================================================
# 内部辅助
# ============================================================================


# 多个空白折叠成一个
_WHITESPACE_PATTERN = re.compile(r"\s+")

# 用于 simhash 分词:保留 word char + 中文 unicode
_TOKEN_PATTERN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)

# simhash 常量
_SIMHASH_BITS = 64
_SIMHASH_MAX_GRAMS = 8192
# SEMANTIC 策略:使用 unigram (n=1) ——
# 短文本(典型 request body 10-30 词)下,1 词改动只影响 1/N 的位,
# 距离 ~64/N,便于命中;长文本下采样保持稳定性
_SIMHASH_NGRAM = 1


def _canonicalize_body(body: dict[str, Any] | None) -> str:
    """body → 稳定字符串

    用于 EXACT 策略: 排序键 + ensure_ascii=False + 紧凑分隔符。
    """
    if body is None:
        return ""
    try:
        return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        # 不可 JSON 序列化的对象 → 退化为 str()
        logger.warning("body not JSON-serializable, falling back to str(): %s", exc)
        try:
            return str(body)
        except Exception:  # noqa: BLE001
            return ""


def _normalize_text(s: str) -> str:
    """小写 + 折叠空白 + strip"""
    if not s:
        return ""
    return _WHITESPACE_PATTERN.sub(" ", s.lower()).strip()


def _normalize_value(v: Any) -> Any:
    """递归标准化一个 value(用于 NORMALIZED body)"""
    if isinstance(v, str):
        return _normalize_text(v)
    if isinstance(v, dict):
        return {k: _normalize_value(vv) for k, vv in sorted(v.items())}
    if isinstance(v, list):
        return [_normalize_value(x) for x in v]
    return v


def _body_for_normalize(body: dict[str, Any] | None) -> str:
    """NORMALIZED 用的 body 字符串:键排序 + 键与字符串值都小写 + 嵌套递归"""
    if body is None:
        return ""
    try:
        # 键也标准化(小写 + 折叠空白),然后排序
        normalized: dict[str, Any] = {}
        for raw_k in body:
            key = _normalize_text(str(raw_k)) if isinstance(raw_k, str) else raw_k
            normalized[key] = _normalize_value(body[raw_k])
        # 排序键后再序列化
        return json.dumps(
            {k: normalized[k] for k in sorted(normalized.keys())},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("body normalize failed, falling back to canonicalize: %s", exc)
        return _canonicalize_body(body)


# ----- simhash 实现(简化版,内嵌避免依赖 fuzzy_dedup) -----

def _tokenize_for_simhash(s: str) -> list[str]:
    if not s:
        return []
    try:
        return _TOKEN_PATTERN.findall(s.lower())
    except Exception:  # noqa: BLE001
        return []


def _make_ngrams(tokens: list[str], n: int) -> list[str]:
    if n <= 1 or n > len(tokens):
        return list(tokens)
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _simhash64(s: str) -> int:
    """64-bit simhash(简化版,内嵌实现)

    流程:
        1. 分词 → 3-gram
        2. 每个 gram 算 md5(取前 16 hex → 64-bit)
        3. 维护 64 维向量,bit=1 → +1,bit=0 → -1
        4. 符号位组装 64-bit int
    """
    if not s:
        return 0
    try:
        tokens = _tokenize_for_simhash(s)
        grams = _make_ngrams(tokens, _SIMHASH_NGRAM)
        if not grams:
            return 0
        # 长文本下采样
        if len(grams) > _SIMHASH_MAX_GRAMS:
            step = len(grams) / _SIMHASH_MAX_GRAMS
            sampled: list[str] = []
            i = 0.0
            while int(i) < len(grams) and len(sampled) < _SIMHASH_MAX_GRAMS:
                sampled.append(grams[int(i)])
                i += step
            grams = sampled

        lo = [0] * 32
        hi = [0] * 32
        for gram in grams:
            digest = hashlib.md5(gram.encode("utf-8")).hexdigest()
            bits = int(digest[:16], 16)
            b_lo = bits & 0xFFFFFFFF
            b_hi = (bits >> 32) & 0xFFFFFFFF
            for i in range(32):
                if (b_lo >> i) & 1:
                    lo[i] += 1
                else:
                    lo[i] -= 1
                if (b_hi >> i) & 1:
                    hi[i] += 1
                else:
                    hi[i] -= 1
        result = 0
        for i in range(32):
            if hi[i] > 0:
                result |= (1 << (i + 32))
            if lo[i] > 0:
                result |= (1 << i)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("simhash failed, returning 0: %s", exc)
        return 0


def _hamming(a: int, b: int) -> int:
    """两个 64-bit int 的汉明距离"""
    return (a ^ b).bit_count()


# ============================================================================
# hash_request
# ============================================================================


def hash_request(
    method: str,
    path: str,
    body: dict[str, Any] | None,
    strategy: DedupStrategy = DedupStrategy.NORMALIZED,
) -> str:
    """计算请求的 dedup 哈希

    Args:
        method: HTTP 方法 ("GET", "POST" ...)。不区分大小写使用 NORMALIZED
        path:   请求路径 ("/v1/chat/completions")
        body:   请求 body (dict,None 表示空 body)
        strategy: 去重策略

    Returns:
        哈希字符串
        - EXACT / NORMALIZED: 64 字符 SHA256 hex
        - SEMANTIC: 16 字符 64-bit int 的 hex(可与 _simhash 配套解读)
    """
    try:
        if strategy == DedupStrategy.EXACT:
            payload = f"{method}|{path}|{_canonicalize_body(body)}"
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()

        if strategy == DedupStrategy.NORMALIZED:
            payload = (
                f"{_normalize_text(method)}|{_normalize_text(path)}|"
                f"{_body_for_normalize(body)}"
            )
            return hashlib.sha256(payload.encode("utf-8")).hexdigest()

        if strategy == DedupStrategy.SEMANTIC:
            text = f"{method} {path} {_canonicalize_body(body)}"
            sh = _simhash64(text)
            return format(sh, "016x")

        # 兜底:未知 strategy 走 NORMALIZED
        logger.warning("unknown strategy %r, falling back to NORMALIZED", strategy)
        payload = (
            f"{_normalize_text(method)}|{_normalize_text(path)}|"
            f"{_body_for_normalize(body)}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
    except Exception as exc:  # noqa: BLE001
        # 极端兜底:返回空字符串(不会与正常 hash 冲突)
        logger.error("hash_request failed for %s %s: %s", method, path, exc)
        return ""


# ============================================================================
# DedupEntry
# ============================================================================


@dataclass
class DedupEntry:
    """去重索引条目

    Attributes:
        hash:          请求的 dedup 哈希(EXACT/NORMALIZED 是 64-char SHA256 hex,
                       SEMANTIC 是 16-char simhash hex)
        first_seen_ts: 首次见到的 epoch 秒
        count:         重复次数(1 = 首次;record 命中已存在时 +1)
        last_seen_ts:  上次见到的 epoch 秒
        response:      缓存的响应(可复用;None 表示未缓存)
        sources:       见到此请求的 source 列表(去重)
        strategy:      此 entry 的去重策略(用于 stats 分类)
    """
    hash: str
    first_seen_ts: float
    count: int = 1
    last_seen_ts: float = 0.0
    response: dict[str, Any] | None = None
    sources: list[str] = field(default_factory=list)
    strategy: DedupStrategy = DedupStrategy.NORMALIZED

    def __post_init__(self) -> None:
        if self.last_seen_ts == 0.0:
            self.last_seen_ts = self.first_seen_ts
        if self.sources is None:
            self.sources = []


# ============================================================================
# RequestDedupIndex
# ============================================================================


class RequestDedupIndex:
    """请求去重索引(LRU + TTL,RLock 保护)

    典型工作流:
        idx = RequestDedupIndex(strategy=DedupStrategy.NORMALIZED, ttl_seconds=60)
        # 1. 先查
        existing = idx.check(method, path, body, source="user_42")
        if existing is not None and existing.response is not None:
            return existing.response
        # 2. 真正处理
        response = do_request(...)
        # 3. 记录
        idx.record(method, path, body, source="user_42", response=response)

    LRU:
        每次 check / record 都把 entry 移到 OrderedDict 末尾;
        超出 max_size 时,从头部淘汰最久未访问的 entry。

    TTL:
        check / record 内部会懒清理当前命中 key 的过期状态;
        cleanup() 主动遍历全表清理。

    线程安全:
        RLock 允许同一线程重入(例如 stats() 内部 size())。
    """

    def __init__(
        self,
        strategy: DedupStrategy = DedupStrategy.NORMALIZED,
        ttl_seconds: int = 60,
        max_size: int = 10000,
        semantic_threshold: int = 5,
    ) -> None:
        """初始化

        Args:
            strategy:           去重策略
            ttl_seconds:        entry 存活时间(秒);<= 0 表示不过期
            max_size:           最大 entry 数(超出按 LRU 淘汰)
            semantic_threshold: SEMANTIC 策略下 simhash 汉明距离阈值(0..64)
        """
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must be >= 0")
        if max_size <= 0:
            raise ValueError("max_size must be > 0")
        if not 0 <= semantic_threshold <= 64:
            raise ValueError("semantic_threshold must be in [0, 64]")

        self.strategy = strategy
        self.ttl_seconds = int(ttl_seconds)
        self.max_size = int(max_size)
        self.semantic_threshold = int(semantic_threshold)

        self._lock = threading.RLock()
        # hash -> DedupEntry(OrderedDict 用于 LRU)
        self._entries: OrderedDict[str, DedupEntry] = OrderedDict()
        # SEMANTIC 策略专用:hash -> simhash int(便于 hamming 比较,免去重新解析 hex)
        self._simhashes: dict[str, int] = {}
        # stats 累计(check 命中 / 总检查)
        self._total_checks = 0
        self._total_hits = 0

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def check(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        source: str = "default",
    ) -> DedupEntry | None:
        """检查请求是否已存在(命中即返回 entry,未命中返回 None)

        命中时:
        - 更新 last_seen_ts
        - 移动 entry 到 OrderedDict 末尾(LRU touch)
        - 累加 stats 命中数
        - 不修改 entry.count(避免 check 反复调用刷数)

        Args:
            method, path, body: 请求参数
            source:            调用方标识(用于追踪;check 不写入 sources 列表,
                                仅 record 写入)

        Returns:
            已存在的 DedupEntry(可能带有缓存 response),或 None
        """
        with self._lock:
            self._total_checks += 1
            try:
                entry = self._find(method, path, body)
            except Exception as exc:  # noqa: BLE001
                logger.error("check failed: %s", exc)
                return None

            if entry is None:
                return None

            self._total_hits += 1
            entry.last_seen_ts = time.time()
            with contextlib.suppress(KeyError):
                self._entries.move_to_end(entry.hash)
            return entry

    def record(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        source: str,
        response: dict[str, Any] | None = None,
    ) -> DedupEntry:
        """记录请求(新增或更新现有)

        若已存在语义近重复的 entry(SEMANTIC)或 hash 完全相同
        (EXACT/NORMALIZED),则更新该 entry(count++、last_seen、
        sources、response);否则创建新 entry。

        Args:
            method, path, body, source: 同 check
            response:                   响应缓存(若有,后续 check 可复用)

        Returns:
            新建或更新的 DedupEntry
        """
        with self._lock:
            try:
                now = time.time()
                entry = self._find(method, path, body)
                if entry is not None:
                    # 更新现有
                    entry.count += 1
                    entry.last_seen_ts = now
                    if source and source not in entry.sources:
                        entry.sources.append(source)
                    if response is not None:
                        entry.response = response
                    with contextlib.suppress(KeyError):
                        self._entries.move_to_end(entry.hash)
                    return entry

                # 新建
                h = hash_request(method, path, body, self.strategy)
                entry = DedupEntry(
                    hash=h,
                    first_seen_ts=now,
                    count=1,
                    last_seen_ts=now,
                    response=response,
                    sources=[source] if source else [],
                    strategy=self.strategy,
                )
                self._entries[h] = entry
                if self.strategy == DedupStrategy.SEMANTIC:
                    with contextlib.suppress(ValueError):
                        self._simhashes[h] = int(h, 16)
                self._enforce_max_size()
                return entry
            except Exception as exc:  # noqa: BLE001
                # 极端兜底:返回一个 dummy entry,避免上层崩
                logger.error("record failed, returning dummy entry: %s", exc)
                return DedupEntry(
                    hash="",
                    first_seen_ts=time.time(),
                    sources=[source] if source else [],
                    strategy=self.strategy,
                )

    def cleanup(self) -> int:
        """按 TTL 主动清理过期 entry

        Returns:
            被清理的 entry 数量
        """
        with self._lock:
            try:
                if self.ttl_seconds <= 0:
                    return 0
                now = time.time()
                expired: list[str] = []
                for k, entry in self._entries.items():
                    if now - entry.last_seen_ts > self.ttl_seconds:
                        expired.append(k)
                for k in expired:
                    self._entries.pop(k, None)
                    self._simhashes.pop(k, None)
                return len(expired)
            except Exception as exc:  # noqa: BLE001
                logger.error("cleanup failed: %s", exc)
                return 0

    def stats(self) -> dict[str, Any]:
        """返回统计信息

        Returns:
            dict with keys:
                total:        当前 entry 数
                by_strategy:  Dict[strategy_value, count]
                hit_rate:     命中/总检查 * 1000(整数,便于传输;客户端除以 1000)
                hits:         命中总数
                checks:       总检查数
                max_size:     配置的 max_size
                ttl_seconds:  配置的 ttl
        """
        with self._lock:
            try:
                by_strategy: dict[str, int] = defaultdict(int)
                for entry in self._entries.values():
                    by_strategy[entry.strategy.value] += 1
                if self._total_checks > 0:
                    hit_rate = int(self._total_hits * 1000 / self._total_checks)
                else:
                    hit_rate = 0
                return {
                    "total": len(self._entries),
                    "by_strategy": dict(by_strategy),
                    "hit_rate": hit_rate,
                    "hits": self._total_hits,
                    "checks": self._total_checks,
                    "max_size": self.max_size,
                    "ttl_seconds": self.ttl_seconds,
                }
            except Exception as exc:  # noqa: BLE001
                logger.error("stats failed: %s", exc)
                return {
                    "total": 0,
                    "by_strategy": {},
                    "hit_rate": 0,
                    "hits": 0,
                    "checks": 0,
                    "max_size": self.max_size,
                    "ttl_seconds": self.ttl_seconds,
                }

    def size(self) -> int:
        """当前 entry 数"""
        with self._lock:
            return len(self._entries)

    def clear(self) -> None:
        """清空索引(测试 / 重置用)"""
        with self._lock:
            self._entries.clear()
            self._simhashes.clear()
            self._total_checks = 0
            self._total_hits = 0

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _find(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> DedupEntry | None:
        """根据 strategy 走不同查找路径,并懒清理过期 entry"""
        if self.strategy == DedupStrategy.SEMANTIC:
            return self._find_semantic(method, path, body)
        # EXACT / NORMALIZED: 直接 hash 查找
        h = hash_request(method, path, body, self.strategy)
        entry = self._entries.get(h)
        if entry is None:
            return None
        if self.ttl_seconds > 0 and time.time() - entry.last_seen_ts > self.ttl_seconds:
            # 懒清理
            self._entries.pop(h, None)
            self._simhashes.pop(h, None)
            return None
        return entry

    def _find_semantic(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> DedupEntry | None:
        """SEMANTIC 策略下的查找:simhash 汉明距离 <= threshold 视为命中"""
        if not self._entries:
            return None
        text = f"{method} {path} {_canonicalize_body(body)}"
        target_sh = _simhash64(text)
        if target_sh == 0 and not self._simhashes:
            # 全部为 0 哈希时无法区分,直接返回 None
            return None
        best_key: str | None = None
        best_dist = self.semantic_threshold + 1
        now = time.time()
        expired: list[str] = []
        try:
            for k, sh in self._simhashes.items():
                entry = self._entries.get(k)
                if entry is None:
                    continue
                # 懒清理
                if self.ttl_seconds > 0 and now - entry.last_seen_ts > self.ttl_seconds:
                    expired.append(k)
                    continue
                d = _hamming(target_sh, sh)
                if d <= self.semantic_threshold and d < best_dist:
                    best_dist = d
                    best_key = k
            for k in expired:
                self._entries.pop(k, None)
                self._simhashes.pop(k, None)
        except Exception as exc:  # noqa: BLE001
            logger.error("semantic find failed: %s", exc)
            return None
        if best_key is None:
            return None
        return self._entries.get(best_key)

    def _enforce_max_size(self) -> None:
        """超出 max_size 时按 LRU 淘汰"""
        try:
            while len(self._entries) > self.max_size:
                oldest_key, _ = self._entries.popitem(last=False)
                self._simhashes.pop(oldest_key, None)
        except Exception as exc:  # noqa: BLE001
            logger.error("LRU evict failed: %s", exc)
