"""moa_gateway.capability.fuzzy_dedup — SimHash 模糊去重指纹

来源: 参考表 F-30 — Fuzzy Dedup (近重复检测)

提供:
- tokenize: 小写 + 分词 + 标点移除
- simhash: 64-bit 局部敏感哈希 (token n-gram → md5 累加 → 符号位)
- hamming_distance: 两个 simhash 之间的 bit 差异数
- similarity: 基于汉明距离的相似度 = 1 - hamming/64
- FuzzyDedupIndex: 内存近重复索引 (暴力 O(N) 扫,线程安全)

设计目标:
- 真实可用 simhash(同 text → 同 hash;1 字符改动 → 汉明距离小)
- 无第三方依赖(仅用 hashlib + 标准库)
- 大批量 (1k+) 性能可接受(暴力扫,O(N) 每次查询)
- 线程安全(RLock,允许多个 find 持锁释放前的 add)

使用示例:
    from moa_gateway.capability.fuzzy_dedup import (
        FuzzyDedupIndex, simhash, similarity,
    )

    idx = FuzzyDedupIndex()
    a = idx.add("the quick brown fox jumps over the lazy dog")
    b = idx.add("the quick brown fox jumps over the lazy dog")     # 重复
    c = idx.add("completely different unrelated text content here")  # 不重复

    dups = idx.find_duplicates("the quick brown fox jumps over a lazy dog", threshold=0.85)
    for dup_id, sim, meta in dups:
        print(dup_id, sim, meta)
"""
from __future__ import annotations

import hashlib
import logging
import re
import string
import threading
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "tokenize",
    "simhash",
    "hamming_distance",
    "similarity",
    "FuzzyDedupIndex",
    "FuzzyDedupRecord",
]


# 64-bit simhash 总位数
_SIMHASH_BITS = 64
_SIMHASH_MASK = (1 << _SIMHASH_BITS) - 1

# 标点移除 pattern(unicode 标点 + ascii 标点)
# 注: \p{P} 在 re 中需 unicodedata,这里用 string.punctuation + 一组常见符号
_PUNCT_PATTERN = re.compile(
    f"[{re.escape(string.punctuation)}\u2010-\u2015\u2018-\u201f\u2026\u3000-\u303f\uff00-\uffef]"
)


def tokenize(text: str) -> List[str]:
    """小写化 + 分词 + 移除标点

    流程:
        1. text 全部小写
        2. 先把标点(ASCII / 全角 / 中文)替换成空格,再按空白 split
           (这一步很关键:否则中文句子的标点会粘在前一个 token 内)
        3. 过滤空 token,二次 strip

    Args:
        text: 原始文本

    Returns:
        清洗后的 token 列表(保持原顺序)
    """
    if not text:
        return []
    try:
        lowered = text.lower()
        # 标点先替换为空白(避免中文逗号粘在 token 内)
        separated = _PUNCT_PATTERN.sub(" ", lowered)
        # unicode \s 已包含 ascii whitespace + 全角空格
        raw_tokens = re.split(r"\s+", separated)
        cleaned: List[str] = []
        for tok in raw_tokens:
            stripped = tok.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned
    except Exception as exc:  # noqa: BLE001
        logger.warning("tokenize failed, returning []: %s", exc)
        return []


def _token_ngrams(tokens: List[str], n: int) -> List[str]:
    """从 token 列表生成 n-gram token

    Args:
        tokens: token 列表
        n: n-gram 窗口大小(<=0 或 >len(tokens) 时退化为单 token)

    Returns:
        空格拼接的 n-gram 字符串列表
    """
    if n <= 1 or n > len(tokens):
        return tokens[:]
    return [" ".join(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


# simhash 大文本性能:超过此 gram 数时,均匀下采样,保持 64-bit 指纹稳定
_MAX_SIMHASH_GRAMS = 8192


def simhash(text: str, n_grams: int = 3) -> int:
    """计算 64-bit simhash

    流程:
        1. tokenize(text) → 清洗后的 token 列表
        2. 生成 n-gram token(默认 trigram,防止 short text 只得到 1 个 token)
        3. 对每个 token 算 md5 hex → 取前 16 hex(64-bit)
        4. 维护 64 维向量,token 每一位为 1 → +1,为 0 → -1
        5. 符号位(正 → 1,负/0 → 0)组装成 64-bit int 返回

    Args:
        text: 输入文本
        n_grams: n-gram 窗口大小,默认 3

    Returns:
        64-bit int simhash(0..2**64-1)
    """
    try:
        tokens = tokenize(text)
        grams = _token_ngrams(tokens, n_grams)

        if not grams:
            return 0

        # 长文本下采样:超过 _MAX_SIMHASH_GRAMS 时均匀采样,
        # 避免 1MB 文本产生 ~50k grams 拖慢 simhash
        if len(grams) > _MAX_SIMHASH_GRAMS:
            step = len(grams) / _MAX_SIMHASH_GRAMS
            sampled: List[str] = []
            i = 0.0
            while int(i) < len(grams) and len(sampled) < _MAX_SIMHASH_GRAMS:
                sampled.append(grams[int(i)])
                i += step
            grams = sampled

        # 64 维累加器(用 list[int])。拆成 lo[32] / hi[32] 两段,
        # 读符号时直接看列表元素正负,比 bit-twiddle 简单可靠。
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
            if lo[i] > 0:
                result |= 1 << i
            if hi[i] > 0:
                result |= 1 << (i + 32)
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("simhash failed, returning 0: %s", exc)
        return 0


def hamming_distance(hash1: int, hash2: int) -> int:
    """两个 64-bit hash 之间的汉明距离(bit 差异数)

    Args:
        hash1: int hash A
        hash2: int hash B

    Returns:
        不同 bit 的数量(0..64)
    """
    try:
        # 双 mask 限制在 64 bit(防止调用方传超 64-bit int)
        x = (hash1 ^ hash2) & _SIMHASH_MASK
        # bin(x).count('1') 在 CPython 上已用 C 实现,比 bit-twiddle 快
        return bin(x).count("1")
    except Exception as exc:  # noqa: BLE001
        logger.warning("hamming_distance failed, returning 64: %s", exc)
        return _SIMHASH_BITS


def similarity(hash1: int, hash2: int) -> float:
    """基于汉明距离的相似度

    similarity = 1 - hamming_distance(h1, h2) / 64

    Args:
        hash1: int hash A
        hash2: int hash B

    Returns:
        0.0..1.0 浮点相似度
    """
    dist = hamming_distance(hash1, hash2)
    return 1.0 - dist / _SIMHASH_BITS


@dataclass
class FuzzyDedupRecord:
    """索引内一条记录"""

    id: str
    text: str
    hash: int
    metadata: Dict = field(default_factory=dict)


class FuzzyDedupIndex:
    """近重复检测索引(内存版)

    暴力 O(N) 扫描实现,适合库规模 ≤ 10000。线程安全(RLock)。

    容量限制:
        - max_size: 软上限,超过时停止接受新 add(返回空 id),不主动淘汰
        - clear(): 手动清空
    """

    def __init__(self, max_size: int = 10000) -> None:
        """初始化索引

        Args:
            max_size: 最大记录数(软上限)
        """
        self._max_size = max_size
        self._records: List[FuzzyDedupRecord] = []
        self._lock = threading.RLock()

    def add(self, text: str, metadata: Optional[Dict] = None) -> str:
        """添加一条文本到索引

        Args:
            text: 文本内容
            metadata: 可选 metadata dict,会原样存储

        Returns:
            该条记录的唯一 id(uuid4 hex);若索引已满,返回空字符串 ""
        """
        with self._lock:
            try:
                if len(self._records) >= self._max_size:
                    logger.warning(
                        "FuzzyDedupIndex full (max_size=%d), add rejected", self._max_size
                    )
                    return ""
                rid = uuid.uuid4().hex
                h = simhash(text)
                rec = FuzzyDedupRecord(
                    id=rid, text=text, hash=h, metadata=metadata or {}
                )
                self._records.append(rec)
                return rid
            except Exception as exc:  # noqa: BLE001
                logger.warning("FuzzyDedupIndex.add failed: %s", exc)
                return ""

    def find_duplicates(
        self, text: str, threshold: float = 0.85
    ) -> List[Tuple[str, float, Dict]]:
        """查找与给定 text 相似的所有记录

        Args:
            text: 查询文本
            threshold: 相似度阈值,≥threshold 视为重复

        Returns:
            列表 [(id, similarity, metadata), ...],按相似度降序
        """
        with self._lock:
            try:
                qhash = simhash(text)
                results: List[Tuple[str, float, Dict]] = []
                for rec in self._records:
                    sim = similarity(qhash, rec.hash)
                    if sim >= threshold:
                        results.append((rec.id, sim, rec.metadata))
                # 降序(高相似度在前)
                results.sort(key=lambda x: x[1], reverse=True)
                return results
            except Exception as exc:  # noqa: BLE001
                logger.warning("FuzzyDedupIndex.find_duplicates failed: %s", exc)
                return []

    def size(self) -> int:
        """当前记录数"""
        with self._lock:
            return len(self._records)

    def clear(self) -> None:
        """清空索引"""
        with self._lock:
            self._records.clear()

    def __len__(self) -> int:
        return self.size()
