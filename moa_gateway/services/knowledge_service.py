"""KnowledgeService — wraps embedding, semantic_search, rag_search, fuzzy_dedup, input_fingerprint, rerank, distillation, importance, context_clean, turboquant, prompt_features, goal_eval.

Exposes:
  - embed(input, dim, model)
  - semantic_search(query, documents, top_k, dim)
  - rag_search(query, corpus, max_results)
  - fuzzy_dedup(action, text, threshold, metadata)
  - input_fingerprint(action, text, a, b, level)
  - rerank(query, documents, top_n, latency_budget_ms)
  - distill(proposals, keep_ratio, evaluations)
  - importance_score(messages, top_k, threshold)
  - context_clean(messages, max_total_chars)
  - turboquant(action, messages, level, hard_cap, preserve)
  - prompt_features(text)
  - goal_eval(goals, output, generate_ceiling, claim, evidence, baseline, gaps, residual_risk)
"""

from __future__ import annotations

from .base import ServiceBase, ServiceMethod


def _load_embedding():
    from ..capability.embedding import (
        MockEmbeddingProvider,
        batch_embed,
        semantic_search,
    )

    return MockEmbeddingProvider, semantic_search, batch_embed


def _load_rag():
    from ..capability.rag_search import rag_search

    return rag_search


def _load_fuzzy_dedup():
    from ..capability.fuzzy_dedup import add as fd_add
    from ..capability.fuzzy_dedup import check as fd_check
    from ..capability.fuzzy_dedup import simhash as fd_simhash

    return fd_add, fd_check, fd_simhash


def _load_input_fingerprint():
    from ..capability.input_fingerprint import hash_text, similar, store

    return hash_text, similar, store


def _load_rerank():
    from ..capability.rerank import MockRerankProvider, rerank_with_budget

    return MockRerankProvider, rerank_with_budget


def _load_distill():
    from ..capability.distillation import distill

    return distill


def _load_importance():
    from ..capability.importance import score_messages

    return score_messages


def _load_context_clean():
    from ..capability.context_clean import clean

    return clean


def _load_turboquant():
    from ..capability.turboquant import apply as tq_apply
    from ..capability.turboquant import should_compress

    return should_compress, tq_apply


def _load_prompt_features():
    from ..capability.prompt_features import extract_features

    return extract_features


def _load_goal_eval():
    from ..capability.goal_eval import evaluate

    return evaluate


class KnowledgeService(ServiceBase):
    name = "knowledge"
    description = "知识 / 检索 / 上下文处理"

    def _register_methods(self):
        self._methods["embed"] = ServiceMethod(
            name="embed",
            description="生成 embedding 向量",
            func=self.embed,
            input_required=["input"],
            input_optional=["dim", "model"],
        )
        self._methods["semantic_search"] = ServiceMethod(
            name="semantic_search",
            description="向量语义检索",
            func=self.semantic_search,
            input_required=["query", "documents"],
            input_optional=["top_k", "dim"],
        )
        self._methods["rag_search"] = ServiceMethod(
            name="rag_search",
            description="RAG 检索(基于 tag 过滤)",
            func=self.rag_search,
            input_required=["query", "corpus"],
            input_optional=["max_results"],
        )
        self._methods["fuzzy_dedup"] = ServiceMethod(
            name="fuzzy_dedup",
            description="模糊去重(add/check/simhash)",
            func=self.fuzzy_dedup,
            input_required=["action", "text"],
            input_optional=["threshold", "metadata"],
        )
        self._methods["input_fingerprint"] = ServiceMethod(
            name="input_fingerprint",
            description="input fingerprint(hash/similar/store)",
            func=self.input_fingerprint,
            input_required=["action"],
            input_optional=["text", "a", "b", "level", "metadata", "max_size"],
        )
        self._methods["rerank"] = ServiceMethod(
            name="rerank",
            description="重排序(query vs documents)",
            func=self.rerank,
            input_required=["query", "documents"],
            input_optional=["top_n", "latency_budget_ms"],
        )
        self._methods["distill"] = ServiceMethod(
            name="distill",
            description="蒸馏(从多个 proposal 选最优)",
            func=self.distill,
            input_required=["proposals", "evaluations"],
            input_optional=["keep_ratio"],
        )
        self._methods["importance_score"] = ServiceMethod(
            name="importance_score",
            description="消息重要性评分",
            func=self.importance_score,
            input_required=["messages"],
            input_optional=["top_k", "threshold"],
        )
        self._methods["context_clean"] = ServiceMethod(
            name="context_clean",
            description="上下文清理",
            func=self.context_clean,
            input_required=["messages"],
            input_optional=["max_total_chars"],
        )
        self._methods["turboquant"] = ServiceMethod(
            name="turboquant",
            description="context 量化压缩(should_compress/apply)",
            func=self.turboquant,
            input_required=["action", "messages"],
            input_optional=["level", "hard_cap", "preserve"],
        )
        self._methods["prompt_features"] = ServiceMethod(
            name="prompt_features",
            description="提取 prompt 特征",
            func=self.prompt_features,
            input_required=["text"],
        )
        self._methods["goal_eval"] = ServiceMethod(
            name="goal_eval",
            description="评估目标达成度",
            func=self.goal_eval,
            input_required=["goals", "output"],
            input_optional=[
                "generate_ceiling",
                "claim",
                "evidence",
                "baseline",
                "gaps",
                "residual_risk",
            ],
        )

    def embed(self, input, dim=64, model="mock-embedding-v1"):
        MockEmbeddingProvider, _, _ = _load_embedding()
        if isinstance(input, str):
            input = [input]
        p = MockEmbeddingProvider(model=model, dim=dim)
        vecs = p.embed(input)
        return {"model": model, "dim": dim, "vectors": vecs, "count": len(input)}

    def semantic_search(self, query, documents, top_k=3, dim=64):
        # Build an EmbeddingIndex from documents, then search
        from ..capability.embedding import (
            EmbeddingIndex,
            MockEmbeddingProvider,
        )
        from ..capability.embedding import (
            semantic_search as _sem,
        )

        provider = MockEmbeddingProvider(model="mock-embedding-v1", dim=dim)
        index = EmbeddingIndex(model="mock-embedding-v1", dim=dim)
        for doc in documents:
            emb = provider.embed([doc])
            index.add(doc, emb[0])
        results = _sem(index=index, query=query, top_k=top_k, dim=dim)
        return {"query": query, "results": [list(r) for r in results]}

    def rag_search(self, query, corpus, max_results=5):
        rag_search = _load_rag()
        results = rag_search(query=query, corpus=corpus, max_results=max_results)
        return {"query": query, "results": results}

    def fuzzy_dedup(self, action, text, threshold=0.8, metadata=None):
        fd_add, fd_check, fd_simhash = _load_fuzzy_dedup()
        if action == "add":
            return fd_add(text=text, metadata=metadata or {})
        if action == "check":
            return fd_check(text=text, threshold=threshold)
        if action == "simhash":
            return {"simhash": fd_simhash(text=text)}
        raise ValueError(f"unknown action: {action}")

    def input_fingerprint(
        self, action, text=None, a=None, b=None, level="normalized", metadata=None, max_size=10000
    ):
        hash_text, similar, store = _load_input_fingerprint()
        if action == "hash":
            return {"hash": hash_text(text=text)}
        if action == "similar":
            return similar(a=a, b=b, level=level)
        if action == "store":
            return {"stored": store(text=text, metadata=metadata or {}, max_size=max_size)}
        raise ValueError(f"unknown action: {action}")

    def rerank(self, query, documents, top_n=3, latency_budget_ms=2000):
        MockRerankProvider, rerank_with_budget = _load_rerank()
        provider = MockRerankProvider()
        # MockRerankProvider.rerank returns RerankResult (not list)
        try:
            result = provider.rerank(query=query, documents=documents, top_n=top_n)
        except TypeError:
            # Try different signature
            result = provider.rerank(query, documents, top_n)
        if hasattr(result, "candidates"):
            return {
                "query": result.query,
                "results": [c.__dict__ if hasattr(c, "__dict__") else c for c in result.candidates],
                "latency_ms": getattr(result, "latency_ms", 0.0),
            }
        if isinstance(result, list):
            return {
                "query": query,
                "results": [r.__dict__ if hasattr(r, "__dict__") else r for r in result],
            }
        return {"query": query, "results": str(result)}

    def distill(self, proposals, evaluations, keep_ratio=0.5):
        distill = _load_distill()
        return distill(proposals=proposals, keep_ratio=keep_ratio, evaluations=evaluations)

    def importance_score(self, messages, top_k=3, threshold=0.5):
        score = _load_importance()
        return score(messages=messages, top_k=top_k, threshold=threshold)

    def context_clean(self, messages, max_total_chars=10000):
        clean = _load_context_clean()
        return clean(messages=messages, max_total_chars=max_total_chars)

    def turboquant(self, action, messages, level="Q4", hard_cap=60, preserve=30):
        should_compress, tq_apply = _load_turboquant()
        if action == "should_compress":
            return should_compress(
                messages=messages, level=level, hard_cap=hard_cap, preserve=preserve
            )
        if action == "apply":
            return tq_apply(messages=messages, level=level, hard_cap=hard_cap, preserve=preserve)
        raise ValueError(f"unknown action: {action}")

    def prompt_features(self, text):
        extract = _load_prompt_features()
        return extract(text=text)

    def goal_eval(
        self,
        goals,
        output,
        generate_ceiling=True,
        claim="",
        evidence=None,
        baseline="",
        gaps=None,
        residual_risk="",
    ):
        evaluate = _load_goal_eval()
        return evaluate(
            goals=goals,
            output=output,
            generate_ceiling=generate_ceiling,
            claim=claim,
            evidence=evidence or [],
            baseline=baseline,
            gaps=gaps or [],
            residual_risk=residual_risk,
        )
