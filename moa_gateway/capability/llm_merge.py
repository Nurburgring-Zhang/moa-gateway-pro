"""llm_merge — LLM 响应合并 (multi-source) + LLM 降级 chain (来自 04 moa-main-commercial)

核心能力:
  1. LLMResponse dataclass — 标准化单 provider 响应
  2. 5 种合并策略: CONCAT / DEDUP / VOTE / WEIGHTED / FIRST_SUCCESS
  3. FallbackChain — 按 priority 降级,全失败抛 AllProvidersFailedError
  4. JSON 序列化支持

设计原则:
  - 真实合并,无 mock
  - VOTE: 多数共识(按 normalized text 分组)
  - WEIGHTED: 按 confidence 加权选择最佳
  - DEDUP: 移除 normalized 完全重复
  - FallbackChain 真实执行,捕获异常后降级
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple


# ============ 数据模型 ============
class MergeStrategy(str, Enum):
    """合并策略"""
    CONCAT = "CONCAT"
    DEDUP = "DEDUP"
    VOTE = "VOTE"
    WEIGHTED = "WEIGHTED"
    FIRST_SUCCESS = "FIRST_SUCCESS"


@dataclass
class LLMResponse:
    """单 provider 响应"""
    source: str  # "gpt-4o"/"deepseek-v3" 等
    text: str
    tokens: int
    latency_ms: float
    cost_usd: float
    confidence: float  # 0-1

    def to_dict(self) -> Dict:
        return {
            "source": self.source,
            "text": self.text,
            "tokens": self.tokens,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "confidence": self.confidence,
        }


@dataclass
class MergedResult:
    """合并结果"""
    text: str
    sources: List[str]
    strategy: MergeStrategy
    total_tokens: int
    total_cost_usd: float
    confidence: float  # 0-1

    def to_dict(self) -> Dict:
        return {
            "text": self.text,
            "sources": list(self.sources),
            "strategy": self.strategy.value,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "confidence": self.confidence,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


# ============ 异常 ============
class AllProvidersFailedError(Exception):
    """FallbackChain 中所有 provider 均失败"""
    def __init__(self, providers: List[str], errors: Optional[List[Exception]] = None):
        self.providers = list(providers)
        self.errors = list(errors or [])
        msg = f"All providers failed: {self.providers}"
        if self.errors:
            detail = "; ".join(f"{p}: {type(e).__name__}: {e}" for p, e in zip(self.providers, self.errors))
            msg = f"{msg} | {detail}"
        super().__init__(msg)


# ============ 工具函数 ============
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_text(text: str) -> str:
    """规范化文本用于去重 / 投票:
    - 去除首尾空白
    - 折叠所有空白字符(空格/Tab/换行)为单空格
    - 转小写
    """
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = _WHITESPACE_RE.sub(" ", s)
    return s


def _aggregate_cost(responses: List[LLMResponse]) -> float:
    return float(sum(max(0.0, r.cost_usd) for r in responses))


def _aggregate_tokens(responses: List[LLMResponse]) -> int:
    return int(sum(max(0, r.tokens) for r in responses))


def _avg_confidence(responses: List[LLMResponse]) -> float:
    if not responses:
        return 0.0
    total = sum(max(0.0, min(1.0, float(r.confidence))) for r in responses)
    return total / len(responses)


# ============ 合并策略实现 ============
def _merge_concat(responses: List[LLMResponse]) -> MergedResult:
    """CONCAT: 用 "---" 拼接所有响应文本"""
    if not responses:
        return MergedResult(
            text="",
            sources=[],
            strategy=MergeStrategy.CONCAT,
            total_tokens=0,
            total_cost_usd=0.0,
            confidence=0.0,
        )
    parts = [r.text for r in responses]
    text = "---".join(parts)
    return MergedResult(
        text=text,
        sources=[r.source for r in responses],
        strategy=MergeStrategy.CONCAT,
        total_tokens=_aggregate_tokens(responses),
        total_cost_usd=_aggregate_cost(responses),
        confidence=_avg_confidence(responses),
    )


def _merge_dedup(responses: List[LLMResponse]) -> MergedResult:
    """DEDUP: 移除完全重复的响应(基于 normalized text)
    保留首次出现的 source。
    """
    if not responses:
        return MergedResult(
            text="",
            sources=[],
            strategy=MergeStrategy.DEDUP,
            total_tokens=0,
            total_cost_usd=0.0,
            confidence=0.0,
        )
    seen: Dict[str, int] = {}
    kept: List[LLMResponse] = []
    for r in responses:
        key = _normalize_text(r.text)
        if key in seen:
            continue
        seen[key] = len(kept)
        kept.append(r)
    if not kept:
        return MergedResult(
            text="",
            sources=[],
            strategy=MergeStrategy.DEDUP,
            total_tokens=0,
            total_cost_usd=0.0,
            confidence=0.0,
        )
    # DEDUP 文本仍用 --- 拼接,但仅唯一项
    text = "---".join(r.text for r in kept)
    return MergedResult(
        text=text,
        sources=[r.source for r in kept],
        strategy=MergeStrategy.DEDUP,
        total_tokens=_aggregate_tokens(kept),
        total_cost_usd=_aggregate_cost(kept),
        confidence=_avg_confidence(kept),
    )


def _merge_vote(responses: List[LLMResponse]) -> MergedResult:
    """VOTE: 多数共识 — 按 normalized text 分组,选票数最多者
    选平局时按 confidence 总分高者胜;仍平局取首次出现。
    """
    if not responses:
        return MergedResult(
            text="",
            sources=[],
            strategy=MergeStrategy.VOTE,
            total_tokens=0,
            total_cost_usd=0.0,
            confidence=0.0,
        )
    groups: Dict[str, List[LLMResponse]] = {}
    for r in responses:
        key = _normalize_text(r.text)
        groups.setdefault(key, []).append(r)

    # 计算每组得票与 confidence 总分
    scored: List[Tuple[int, float, str, List[LLMResponse]]] = []
    for key, group in groups.items():
        votes = len(group)
        conf_sum = sum(max(0.0, min(1.0, float(r.confidence))) for r in group)
        scored.append((votes, conf_sum, key, group))

    # 排序: 票数 desc, confidence 总分 desc, key 首次出现顺序
    scored.sort(key=lambda x: (-x[0], -x[1]))
    best_votes, best_conf, best_key, best_group = scored[0]

    # text 取原始(未 normalized)文本
    text = best_group[0].text
    sources = [r.source for r in best_group]
    # 共识度 = 票数占比
    agreement = best_votes / len(responses)
    # confidence = 共识占比 * 选中组平均 confidence
    avg_conf = (sum(max(0.0, min(1.0, float(r.confidence))) for r in best_group) / best_votes) if best_votes else 0.0
    confidence = agreement * avg_conf

    return MergedResult(
        text=text,
        sources=sources,
        strategy=MergeStrategy.VOTE,
        total_tokens=_aggregate_tokens(best_group),
        total_cost_usd=_aggregate_cost(best_group),
        confidence=confidence,
    )


def _merge_weighted(responses: List[LLMResponse]) -> MergedResult:
    """WEIGHTED: 按 confidence 加权选择最佳响应
    选 confidence 最高的单个响应(若平局选首次出现)。
    """
    if not responses:
        return MergedResult(
            text="",
            sources=[],
            strategy=MergeStrategy.WEIGHTED,
            total_tokens=0,
            total_cost_usd=0.0,
            confidence=0.0,
        )
    best_idx = 0
    best_conf = max(0.0, min(1.0, float(responses[0].confidence)))
    for i, r in enumerate(responses):
        c = max(0.0, min(1.0, float(r.confidence)))
        if c > best_conf:
            best_conf = c
            best_idx = i
    best = responses[best_idx]
    return MergedResult(
        text=best.text,
        sources=[best.source],
        strategy=MergeStrategy.WEIGHTED,
        total_tokens=best.tokens,
        total_cost_usd=float(best.cost_usd),
        confidence=best_conf,
    )


def _merge_first_success(responses: List[LLMResponse]) -> MergedResult:
    """FIRST_SUCCESS: 选第一个非空响应"""
    if not responses:
        return MergedResult(
            text="",
            sources=[],
            strategy=MergeStrategy.FIRST_SUCCESS,
            total_tokens=0,
            total_cost_usd=0.0,
            confidence=0.0,
        )
    chosen: Optional[LLMResponse] = None
    for r in responses:
        if r.text and r.text.strip():
            chosen = r
            break
    if chosen is None:
        # 全部为空,返回第一个(空)
        chosen = responses[0]
    return MergedResult(
        text=chosen.text,
        sources=[chosen.source],
        strategy=MergeStrategy.FIRST_SUCCESS,
        total_tokens=chosen.tokens,
        total_cost_usd=float(chosen.cost_usd),
        confidence=max(0.0, min(1.0, float(chosen.confidence))),
    )


_STRATEGY_DISPATCH = {
    MergeStrategy.CONCAT: _merge_concat,
    MergeStrategy.DEDUP: _merge_dedup,
    MergeStrategy.VOTE: _merge_vote,
    MergeStrategy.WEIGHTED: _merge_weighted,
    MergeStrategy.FIRST_SUCCESS: _merge_first_success,
}


def merge_responses(responses: List[LLMResponse], strategy: MergeStrategy) -> MergedResult:
    """合并多源 LLM 响应

    Args:
        responses: 响应列表(可空)
        strategy: 合并策略

    Returns:
        MergedResult
    """
    if strategy not in _STRATEGY_DISPATCH:
        # 未知策略降级为 CONCAT
        return _merge_concat(list(responses or []))
    return _STRATEGY_DISPATCH[strategy](list(responses or []))


# ============ FallbackChain ============
class FallbackChain:
    """降级 chain — 按 priority 顺序调用 provider,失败则降级到下一个

    priority 数字越小越优先(0 最高)。同 priority 按注册顺序。
    """

    def __init__(self, providers: Optional[List[str]] = None):
        self._entries: List[Tuple[int, int, str]] = []  # (priority, order, provider)
        if providers:
            for p in providers:
                self.add_fallback(p, 0)

    def add_fallback(self, provider: str, priority: int = 0) -> None:
        """注册降级项
        priority 越小越优先;同 priority 后注册的先尝试(同优先级内后入先出? — 这里按注册顺序)。
        """
        if not provider:
            return
        order = len(self._entries)
        self._entries.append((int(priority), order, str(provider)))

    @property
    def providers(self) -> List[str]:
        """按 priority 排序后的 provider 列表"""
        return [p for _, _, p in sorted(self._entries, key=lambda x: (x[0], x[1]))]

    def execute(
        self,
        call_fn: Callable[[str], LLMResponse],
    ) -> LLMResponse:
        """按 priority 顺序执行,失败时降级

        Args:
            call_fn: 接受 provider 名称,返回 LLMResponse(失败可 raise Exception)

        Returns:
            首个成功的 LLMResponse

        Raises:
            AllProvidersFailedError: 所有 provider 均失败
        """
        if not self._entries:
            raise AllProvidersFailedError([], [])

        errors: List[Optional[Exception]] = []
        providers_tried: List[str] = []
        sorted_entries = sorted(self._entries, key=lambda x: (x[0], x[1]))
        for _priority, _order, provider in sorted_entries:
            providers_tried.append(provider)
            try:
                resp = call_fn(provider)
                if resp is None:
                    errors.append(ValueError(f"call_fn({provider}) returned None"))
                    continue
                return resp
            except Exception as e:  # noqa: BLE001 — 真实捕获所有异常以降级
                errors.append(e)
                continue

        raise AllProvidersFailedError(providers_tried, errors)


# ============ JSON 序列化辅助 ============
def result_to_json(result: MergedResult) -> str:
    """MergedResult → JSON 字符串"""
    return result.to_json()


def response_to_json(response: LLMResponse) -> str:
    """LLMResponse → JSON 字符串"""
    return json.dumps(response.to_dict(), ensure_ascii=False)


__all__ = [
    "MergeStrategy",
    "LLMResponse",
    "MergedResult",
    "AllProvidersFailedError",
    "merge_responses",
    "FallbackChain",
    "result_to_json",
    "response_to_json",
]
