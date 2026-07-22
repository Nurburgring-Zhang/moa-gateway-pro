"""moa_gateway.capability.rerank — Cohere v4 Rerank 模拟 + Stream delta 完整代理

来源: 04 moa-main-commercial (Rerank) + 08 moa-server (Stream delta 工具调用)

提供:
- RerankCandidate / RerankResult dataclass: 候选 + 结果载体
- relevance_score(query, doc) -> float: 关键词 Jaccard + 长度惩罚的启发式打分
- MockRerankProvider: 模拟 Cohere Rerank v4 端点
  - rerank(query, documents, top_n, latency_budget_ms) -> RerankResult
  - 真按启发式打分,真按 score 降序排序,真按 top_n 截断,真测耗时
  - 超过 latency_budget_ms → 截断 + truncated=True
- rerank_with_budget(query, documents, top_n, latency_budget_ms) -> RerankResult
  顶层便捷函数,默认调用 MockRerankProvider
- stream_delta_proxy(chunks) -> List[Dict]: 工具调用流代理
  - 首 chunk 含 id / type / function.name;后续 chunk 累加 args_delta
  - 保留每个工具调用的 index,不被服务端粘合
- format_for_openai(chunks) -> List[Dict]: 把内部 chunk 转 OpenAI delta 格式

设计约束:
- relevance_score 是 deterministic 启发式(关键词 Jaccard 0.7 + 长度惩罚 0.2 + clamp)
- MockRerankProvider 不依赖外部网络,可在没 API key 时 E2E
- stream_delta_proxy 的 tool_calls 聚合符合 OpenAI streaming spec:
  delta.tool_calls[i] = {"index": i, "id": "...", "type": "function", "function": {"name": ..., "arguments": ...}}
  其中后续 chunk 只携带 {"index": i, "function": {"arguments": <delta>}}
- 与 OpenAI /v1/chat/completions 流式响应兼容,可直接转发给客户端
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "RerankCandidate",
    "RerankResult",
    "MockRerankProvider",
    "relevance_score",
    "rerank_with_budget",
    "stream_delta_proxy",
    "format_for_openai",
]


# =============================================================================
# Token 化工具(与 embedding.py 中的保持一致:小写 + 非字母数字切分)
# =============================================================================


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    """小写 + 切分非字母数字

    与 BERT/SentencePiece 等真实 tokenizer 完全不同,
    仅用于让"相似 text token 重叠多"成立。
    """
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


def _keyword_set(text: str) -> set:
    """token 的集合(用于 Jaccard)

    - 去重
    - 长度 < 2 的 token 过滤(避免 "a" / "I" 这种停用词噪声)
    """
    return {t for t in _tokenize(text) if len(t) >= 2}


# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class RerankCandidate:
    """单条重排候选

    - doc_id:  文档标识(可由 caller 决定是 uuid / hash / 自增 id)
    - text:    原始文本
    - initial_score: rerank 之前的 score(比如 BM25 / embedding cosine / 0.0)
    - rerank_score:  rerank 之后的 score(0-1)
    - rank:    重排后的位置(1-based,1 = 最相关)
    """

    doc_id: str
    text: str
    initial_score: float
    rerank_score: float
    rank: int


@dataclass
class RerankResult:
    """一次 rerank 调用的结果

    - query:        原始查询
    - candidates:   按 rank 升序排列(已截断到 top_n 或更少)
    - latency_ms:   实际耗时(毫秒)
    - truncated:    是否因超过 latency_budget_ms 而被截断
    """

    query: str
    candidates: list[RerankCandidate] = field(default_factory=list)
    latency_ms: float = 0.0
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        """JSON 友好 dict(用于 API 响应 / 日志)"""
        return {
            "query": self.query,
            "candidates": [
                {
                    "doc_id": c.doc_id,
                    "text": c.text,
                    "initial_score": c.initial_score,
                    "rerank_score": c.rerank_score,
                    "rank": c.rank,
                }
                for c in self.candidates
            ],
            "latency_ms": self.latency_ms,
            "truncated": self.truncated,
        }


# =============================================================================
# relevance_score: 关键词 Jaccard 0.7 + 长度惩罚 0.2
# =============================================================================


def relevance_score(query: str, doc: str) -> float:
    """启发式 query-doc 相关性打分(0-1,clamp)

    公式:
        jaccard = |kw(query) ∩ kw(doc)| / |kw(query) ∪ kw(doc)|  ∈ [0, 1]
        length_penalty = 1 - |len(doc) - len(query)| / max(len(doc), len(query), 1)
                       ∈ [0, 1]
        score = 0.7 * jaccard + 0.2 * length_penalty + 0.1 * (kw(doc) ⊆ kw(query) bonus)
        score = clamp(score, 0, 1)

    边界:
    - query 空 / kw(query) 空 → 0
    - doc 空 / kw(doc) 空 → 0
    - 大小写不敏感
    """
    q_kw = _keyword_set(query)
    d_kw = _keyword_set(doc)
    if not q_kw or not d_kw:
        return 0.0

    intersection = q_kw & d_kw
    union = q_kw | d_kw
    jaccard = len(intersection) / len(union) if union else 0.0

    q_len = max(len(query), 1)
    d_len = max(len(doc), 1)
    max_len = max(q_len, d_len)
    length_penalty = 1.0 - abs(d_len - q_len) / max_len

    # 包含性 bonus:doc 的所有 kw 都被 query 覆盖 → 接近 1
    coverage = len(intersection) / len(d_kw) if d_kw else 0.0

    score = 0.7 * jaccard + 0.2 * length_penalty + 0.1 * coverage
    if score < 0.0:
        score = 0.0
    elif score > 1.0:
        score = 1.0
    return score


# =============================================================================
# MockRerankProvider: 模拟 Cohere Rerank v4
# =============================================================================


@dataclass
class MockRerankProvider:
    """模拟 Cohere Rerank v4 端点

    - model: 报告的 model id
    - rerank(query, documents, top_n, latency_budget_ms) -> RerankResult
      * 真按启发式打分(relevance_score)
      * 真按 score 降序排序
      * 真按 top_n 截断
      * 真测耗时(time.perf_counter)
      * 真按 latency_budget_ms 触发 truncated

    - 可在没真 API key 时做 E2E(测试 / 本地开发)
    """

    model: str = "rerank-v4"
    _stats: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._stats["n_calls"] = 0
        self._stats["n_truncated"] = 0

    def stats(self) -> dict:
        return dict(self._stats)

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        top_n: int = 10,
        latency_budget_ms: float = 2000.0,
    ) -> RerankResult:
        """按 query 相关性对 documents 重排

        Args:
            query: 查询文本
            documents: 候选文档文本列表
            top_n: 最多返回条数(>= 1)
            latency_budget_ms: 软超时(毫秒),超过则截断到当前已处理的位置

        Returns:
            RerankResult
        """
        top_n = max(top_n, 1)
        if latency_budget_ms < 0:
            latency_budget_ms = 0.0

        self._stats["n_calls"] += 1

        start = time.perf_counter()
        # 真实计时的处理:每个 doc 算分并按"是否超 budget"决定截断
        # 注:heuristic 极快,正常情况下不会超 budget,所以截断路径用 budget
        # 极小(如 0)来强制触发
        scored: list[tuple[int, float, str]] = []
        truncated = False
        budget_s = latency_budget_ms / 1000.0
        for i, doc in enumerate(documents):
            elapsed = time.perf_counter() - start
            if elapsed > budget_s and i > 0:
                # 至少要返回 1 条;超过 budget 就截断到当前位置
                truncated = True
                break
            s = relevance_score(query, doc)
            scored.append((i, s, doc))

        # 降序
        scored.sort(key=lambda t: t[1], reverse=True)
        # 截断到 top_n
        top_scored = scored[:top_n]

        # 构造 RerankCandidate,带 rank(1-based)
        candidates: list[RerankCandidate] = []
        for new_pos, (orig_idx, score, doc) in enumerate(top_scored, start=1):
            candidates.append(
                RerankCandidate(
                    doc_id=f"doc-{orig_idx}",
                    text=doc,
                    initial_score=0.0,
                    rerank_score=score,
                    rank=new_pos,
                )
            )

        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if truncated:
            self._stats["n_truncated"] += 1

        return RerankResult(
            query=query,
            candidates=candidates,
            latency_ms=elapsed_ms,
            truncated=truncated,
        )


# =============================================================================
# 顶层便捷函数
# =============================================================================


_DEFAULT_PROVIDER: MockRerankProvider | None = None


def _get_default_provider() -> MockRerankProvider:
    global _DEFAULT_PROVIDER
    if _DEFAULT_PROVIDER is None:
        _DEFAULT_PROVIDER = MockRerankProvider()
    return _DEFAULT_PROVIDER


def rerank_with_budget(
    query: str,
    documents: Sequence[str],
    top_n: int = 10,
    latency_budget_ms: float = 2000.0,
) -> RerankResult:
    """顶层便捷函数:真按 score 排序 + 截断到 top_n + 测耗时

    Args:
        query: 查询文本
        documents: 候选文档文本列表
        top_n: 最多返回条数(>= 1)
        latency_budget_ms: 软超时(毫秒)

    Returns:
        RerankResult
    """
    return _get_default_provider().rerank(
        query=query,
        documents=documents,
        top_n=top_n,
        latency_budget_ms=latency_budget_ms,
    )


# =============================================================================
# Stream delta 完整代理(工具调用)
# =============================================================================


def stream_delta_proxy(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """工具调用流代理:拼接 args_delta,保留 index / id

    输入 chunks 格式(类似 OpenAI 流式响应 delta):
        chunk_0: {"tool_calls": [{"index": 0, "id": "call_xxx", "type": "function",
                                  "function": {"name": "search", "arguments": "{\""}}]}
        chunk_1: {"tool_calls": [{"index": 0, "function": {"arguments": "query\": "}}]}
        chunk_2: {"tool_calls": [{"index": 1, "id": "call_yyy", "type": "function",
                                  "function": {"name": "calc", "arguments": "42"}}]}

    输出:每个 chunk 是一个完整的 delta(只有第一条带 id/type/name,后续只追加 args_delta)
        chunk_0: {"tool_calls": [{"index": 0, "id": "call_xxx", "type": "function",
                                  "function": {"name": "search", "arguments": "{\""}}]}
        chunk_1: {"tool_calls": [{"index": 0, "function": {"arguments": "query\": "}}]}
        chunk_2: {"tool_calls": [{"index": 1, "id": "call_yyy", "type": "function",
                                  "function": {"name": "calc", "arguments": "42"}}]}

    关键不变式:
    - index: 始终保留,用于前端把同一个工具调用的多个 delta 拼起来
    - id: 仅首 chunk 带(后续 chunk 缺省),不会被服务端粘合
    - type: 仅首 chunk 带 "function"
    - function.name: 仅首 chunk 带
    - function.arguments: 累加所有 chunk 的 args_delta 片段(原样拼接,不解析)

    Args:
        chunks: 原始 chunk 列表(可能含 role/content/tool_calls 等其他字段,本函数只处理 tool_calls)

    Returns:
        同等长度的 chunk 列表,内部 tool_calls 字段被"完整代理"化
    """
    if not chunks:
        return []

    out: list[dict[str, Any]] = []
    # 已建立的工具调用:index -> 累计状态
    # 状态字段: id, type, name, arguments(累加)
    seen: dict[int, dict[str, Any]] = {}

    for chunk in chunks:
        new_chunk: dict[str, Any] = {}
        # 透传其他字段(role / content / finish_reason 等)
        for k, v in chunk.items():
            if k == "tool_calls":
                continue
            new_chunk[k] = v

        tc_in = chunk.get("tool_calls")
        if not tc_in:
            out.append(new_chunk)
            continue

        new_tc: list[dict[str, Any]] = []
        for call in tc_in:
            idx = call.get("index", 0)
            state = seen.setdefault(
                idx,
                {"id": None, "type": None, "name": None, "arguments": ""},
            )

            entry: dict[str, Any] = {"index": idx}

            # id / type / function.name:仅在首次出现时写入 delta
            cid = call.get("id")
            if cid is not None:
                state["id"] = cid
                entry["id"] = cid

            ctype = call.get("type")
            if ctype is not None:
                state["type"] = ctype
                entry["type"] = ctype

            fn = call.get("function") or {}
            cname = fn.get("name")
            cargs = fn.get("arguments")

            fn_out: dict[str, Any] = {}
            if cname is not None:
                state["name"] = cname
                fn_out["name"] = cname

            if cargs is not None and cargs != "":
                # 累加 args_delta(原样拼接)
                state["arguments"] = state["arguments"] + cargs
                fn_out["arguments"] = cargs

            if fn_out:
                entry["function"] = fn_out

            new_tc.append(entry)

        new_chunk["tool_calls"] = new_tc
        out.append(new_chunk)

    return out


def format_for_openai(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把内部 chunk 转 OpenAI delta 格式

    输入(内部格式):
        {"role": "assistant", "content": "Let me", "tool_calls": [...]}

    输出(OpenAI delta 格式):
        {"choices": [{"delta": {"role": "assistant", "content": "Let me",
                                 "tool_calls": [...]}, "index": 0}]}

    用于把 stream_delta_proxy 处理过的 chunk 直接打包成 OpenAI 兼容响应。

    Args:
        chunks: 内部 chunk 列表(通常先经过 stream_delta_proxy)

    Returns:
        OpenAI 兼容的 chunk 列表
    """
    out: list[dict[str, Any]] = []
    for _i, chunk in enumerate(chunks):
        delta: dict[str, Any] = {}
        for k, v in chunk.items():
            delta[k] = v
        out.append({"choices": [{"delta": delta, "index": 0}]})
    return out
