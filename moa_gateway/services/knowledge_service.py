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

import logging
from dataclasses import asdict

from .base import ServiceBase, ServiceMethod

logger = logging.getLogger(__name__)


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


# Audit fix: fuzzy_dedup exposes the FuzzyDedupIndex class (add /
# find_duplicates) + simhash — there are no module-level `add` / `check`
# functions. Keep one process-wide index so add/check flow together.
_fuzzy_index = None


def _get_fuzzy_index():
    global _fuzzy_index
    from ..capability.fuzzy_dedup import FuzzyDedupIndex

    if _fuzzy_index is None:
        _fuzzy_index = FuzzyDedupIndex(max_size=10000)
    return _fuzzy_index


def _load_fuzzy_dedup():
    from ..capability.fuzzy_dedup import FuzzyDedupIndex, simhash, similarity

    return FuzzyDedupIndex, simhash, similarity


# Audit fix: input_fingerprint exposes exact_hash / normalized_hash /
# semantic_hash / structural_hash + InputFingerprint + FingerprintStore
# (hash_text / similar / store never existed).
_fingerprint_store = None


def _get_fingerprint_store():
    global _fingerprint_store
    from ..capability.input_fingerprint import FingerprintStore

    if _fingerprint_store is None:
        _fingerprint_store = FingerprintStore(max_size=50000)
    return _fingerprint_store


def _load_input_fingerprint():
    from ..capability.input_fingerprint import (
        FingerprintStore,
        InputFingerprint,
        exact_hash,
        normalized_hash,
        semantic_hash,
        structural_hash,
    )

    return exact_hash, normalized_hash, semantic_hash, structural_hash, InputFingerprint, FingerprintStore


def _load_rerank():
    from ..capability.rerank import MockRerankProvider, rerank_with_budget

    return MockRerankProvider, rerank_with_budget


def _load_distill():
    # Audit fix: the real entry point is distill_proposals (no `distill`).
    from ..capability.distillation import distill_proposals, multi_eval_average

    return distill_proposals, multi_eval_average


def _load_importance():
    from ..capability.importance import (
        Message,
        score_messages,
        select_top_k,
        should_compress,
    )

    return score_messages, select_top_k, should_compress, Message


def _load_context_clean():
    # Audit fix: the real entry point is clean_messages (no `clean`).
    from ..capability.context_clean import (
        clean_messages,
        from_openai_format,
        to_openai_format,
    )

    return clean_messages, from_openai_format, to_openai_format


def _load_turboquant():
    # Audit fix: real names are should_compress / apply_turboquant (no `apply`).
    from ..capability.turboquant import (
        Message,
        QuantLevel,
        TurboQuantConfig,
        apply_turboquant,
        should_compress,
    )

    return should_compress, apply_turboquant, TurboQuantConfig, QuantLevel, Message


def _load_prompt_features():
    from ..capability.prompt_features import (
        complexity_score,
        domain_classify,
        extract_features,
        should_use_pro_model,
        urgency_score,
    )

    return extract_features, complexity_score, domain_classify, urgency_score, should_use_pro_model


def _load_goal_eval():
    # Audit fix: real API is evaluate_goals / evaluate_goal /
    # generate_ceiling_report (no `evaluate`).
    from ..capability.goal_eval import (
        Goal,
        GoalTier,
        evaluate_goals,
        generate_ceiling_report,
    )

    return evaluate_goals, generate_ceiling_report, Goal, GoalTier


def _tq_messages_from_dicts(messages, TQMessage):
    out = []
    for m in messages or []:
        if isinstance(m, TQMessage):
            out.append(m)
        elif isinstance(m, dict):
            out.append(
                TQMessage(
                    role=str(m.get("role", "user")),
                    content=str(m.get("content", "")),
                    timestamp=float(m.get("timestamp", 0.0)),
                )
            )
        else:
            raise ValueError("each message must be a dict {role, content, timestamp}")
    return out


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
            description="模糊去重 (FuzzyDedupIndex: add/find_duplicates/simhash)",
            func=self.fuzzy_dedup,
            input_required=["action", "text"],
            input_optional=["threshold", "metadata"],
        )
        self._methods["input_fingerprint"] = ServiceMethod(
            name="input_fingerprint",
            description="input fingerprint (hash/similar/store; level: exact|normalized|semantic|structural)",
            func=self.input_fingerprint,
            input_required=["action"],
            input_optional=["text", "a", "b", "level", "metadata", "max_size", "min_levels"],
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
            description="蒸馏: distill_proposals 从多个 proposal 提炼保留 ideas; evaluations 非空时附 multi_eval_average",
            func=self.distill,
            input_required=["proposals"],
            input_optional=["keep_ratio", "evaluations"],
        )
        self._methods["importance_score"] = ServiceMethod(
            name="importance_score",
            description="消息重要性评分 (score_messages + select_top_k + should_compress)",
            func=self.importance_score,
            input_required=["messages"],
            input_optional=["top_k", "threshold"],
        )
        self._methods["context_clean"] = ServiceMethod(
            name="context_clean",
            description="上下文清理: clean_messages (合并连续同角色 / 去孤儿 tool / 截断), 输入输出均为 OpenAI 消息格式",
            func=self.context_clean,
            input_required=["messages"],
            input_optional=["max_total_chars"],
        )
        self._methods["turboquant"] = ServiceMethod(
            name="turboquant",
            description="context 量化压缩 (should_compress/apply_turboquant; level: Q0|Q1|Q2|Q4|Q8)",
            func=self.turboquant,
            input_required=["action", "messages"],
            input_optional=["level", "hard_cap", "preserve"],
        )
        self._methods["prompt_features"] = ServiceMethod(
            name="prompt_features",
            description="提取 prompt 特征 + complexity/domain/urgency/pro-model 判定",
            func=self.prompt_features,
            input_required=["text"],
        )
        self._methods["goal_eval"] = ServiceMethod(
            name="goal_eval",
            description="评估目标达成度 (evaluate_goals); generate_ceiling 且有 claim 时附 CeilingReport",
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
        # Audit fix: drive the real FuzzyDedupIndex (module-level add/check
        # never existed).
        _FuzzyDedupIndex, simhash, _similarity = _load_fuzzy_dedup()
        idx = _get_fuzzy_index()
        if action == "add":
            record_id = idx.add(text=text, metadata=metadata or {})
            return {"id": record_id, "size": idx.size()}
        if action == "check":
            dups = idx.find_duplicates(text=text, threshold=float(threshold))
            return {
                "duplicates": [
                    {"id": rid, "similarity": sim, "metadata": meta} for rid, sim, meta in dups
                ],
                "is_duplicate": len(dups) > 0,
            }
        if action == "simhash":
            return {"simhash": simhash(text=text)}
        raise ValueError(f"unknown action: {action}, expected add/check/simhash")

    def input_fingerprint(
        self,
        action,
        text=None,
        a=None,
        b=None,
        level="normalized",
        metadata=None,
        max_size=10000,
        min_levels=2,
    ):
        # Audit fix: real API — exact_hash / normalized_hash / semantic_hash /
        # structural_hash + InputFingerprint.similar_to + FingerprintStore.
        exact_hash, normalized_hash, semantic_hash, structural_hash, InputFingerprint, _Store = (
            _load_input_fingerprint()
        )
        hash_fns = {
            "exact": exact_hash,
            "normalized": normalized_hash,
            "semantic": semantic_hash,
            "structural": structural_hash,
        }
        if action == "hash":
            fn = hash_fns.get(level or "normalized")
            if fn is None:
                raise ValueError(f"unknown level: {level}, expected one of {sorted(hash_fns)}")
            return {"hash": fn(text or ""), "level": level}
        if action == "similar":
            if a is None or b is None:
                raise ValueError("similar requires a and b")
            fp_a = InputFingerprint(str(a))
            fp_b = InputFingerprint(str(b))
            return {"similarity": fp_a.similar_to(fp_b, level=level or "normalized"), "level": level}
        if action == "store":
            store = _get_fingerprint_store()
            fp = store.add(text or "", metadata=metadata or {})
            out = fp.to_dict()
            out["store_size"] = store.size()
            return out
        if action == "collisions":
            store = _get_fingerprint_store()
            hits = store.find_collisions(text or "", min_levels=int(min_levels))
            return {
                "collisions": [
                    {"fingerprint": fp.to_dict(), "score": score} for fp, score in hits
                ]
            }
        raise ValueError(f"unknown action: {action}, expected hash/similar/store/collisions")

    def rerank(self, query, documents, top_n=3, latency_budget_ms=2000):
        MockRerankProvider, rerank_with_budget = _load_rerank()
        provider = MockRerankProvider()
        result = provider.rerank(
            query=query, documents=list(documents), top_n=top_n, latency_budget_ms=latency_budget_ms
        )
        return {
            "query": result.query,
            "results": [asdict(c) for c in result.candidates],
            "latency_ms": result.latency_ms,
            "truncated": result.truncated,
        }

    def distill(self, proposals, evaluations=None, keep_ratio=0.5):
        # Audit fix: real entry point is distill_proposals(proposals, keep_ratio).
        distill_proposals, multi_eval_average = _load_distill()
        if not isinstance(proposals, list) or not all(isinstance(p, str) for p in proposals):
            raise ValueError("proposals must be a list of strings")
        result = distill_proposals(proposals=proposals, keep_ratio=float(keep_ratio))
        out = asdict(result)
        if evaluations:
            out["eval_average"] = multi_eval_average(evaluations)
        return out

    def importance_score(self, messages, top_k=3, threshold=0.5):
        # Audit fix: score_messages(messages) takes only messages; top_k /
        # threshold feed select_top_k / should_compress.
        score_messages, select_top_k, should_compress, Message = _load_importance()
        msg_objs = []
        for m in messages or []:
            if isinstance(m, Message):
                msg_objs.append(m)
            elif isinstance(m, dict):
                msg_objs.append(
                    Message(
                        role=str(m.get("role", "user")),
                        content=str(m.get("content", "")),
                        timestamp=float(m.get("timestamp", 0.0)),
                        is_tool_result=bool(m.get("is_tool_result", False)),
                        has_tool_calls=bool(m.get("has_tool_calls", False)),
                        is_decision=bool(m.get("is_decision", False)),
                    )
                )
            else:
                raise ValueError("each message must be a dict")
        scores = score_messages(msg_objs)
        return {
            "scores": [asdict(s) for s in scores],
            "selected_indices": select_top_k(scores, int(top_k)),
            "should_compress": should_compress(scores, threshold=float(threshold)),
        }

    def context_clean(self, messages, max_total_chars=10000):
        # Audit fix: real entry point is clean_messages(list[Message], int)
        # with OpenAI-format converters.
        clean_messages, from_openai_format, to_openai_format = _load_context_clean()
        if not isinstance(messages, list):
            raise ValueError("messages must be a list of OpenAI-format message dicts")
        msg_objs = from_openai_format(messages)
        cleaned, stats = clean_messages(msg_objs, max_total_chars=int(max_total_chars))
        return {
            "messages": to_openai_format(cleaned),
            "stats": asdict(stats),
        }

    def turboquant(self, action, messages, level="Q4", hard_cap=60, preserve=30):
        # Audit fix: real names are should_compress(messages, config) and
        # apply_turboquant(messages, config).
        should_compress, apply_turboquant, TurboQuantConfig, QuantLevel, TQMessage = (
            _load_turboquant()
        )
        try:
            qlevel = QuantLevel[str(level).upper()]
        except KeyError as e:
            raise ValueError(
                f"unknown level: {level}, expected one of {[q.name for q in QuantLevel]}"
            ) from e
        config = TurboQuantConfig(hard_cap=int(hard_cap), preserve=int(preserve), level=qlevel)
        msg_objs = _tq_messages_from_dicts(messages, TQMessage)
        if action == "should_compress":
            return {"should_compress": should_compress(msg_objs, config), "level": qlevel.name}
        if action == "apply":
            compressed = apply_turboquant(msg_objs, config)
            return {
                "messages": [
                    {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                    for m in compressed
                ],
                "level": qlevel.name,
                "original_count": len(msg_objs),
                "compressed_count": len(compressed),
            }
        raise ValueError(f"unknown action: {action}, expected should_compress/apply")

    def prompt_features(self, text):
        extract, complexity, domain, urgency, use_pro = _load_prompt_features()
        features = extract(text=text)
        out = asdict(features)
        out["complexity_score"] = complexity(features)
        out["domain"] = domain(features)
        out["urgency_score"] = urgency(features)
        out["should_use_pro_model"] = use_pro(features)
        return out

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
        # Audit fix: real API — evaluate_goals(list[Goal], output) +
        # generate_ceiling_report(...).
        evaluate_goals, generate_ceiling_report, Goal, GoalTier = _load_goal_eval()
        goal_objs = []
        for g in goals or []:
            if isinstance(g, Goal):
                goal_objs.append(g)
            elif isinstance(g, dict):
                tier = GoalTier(str(g.get("tier", "mechanical")))
                goal_objs.append(
                    Goal(
                        id=str(g.get("id", "")),
                        description=str(g.get("description", "")),
                        tier=tier,
                        criteria=str(g.get("criteria", "")),
                    )
                )
            else:
                raise ValueError("each goal must be a dict {id, description, tier, criteria}")
        results = evaluate_goals(goal_objs, output)
        out = {
            "results": [
                {**asdict(r), "tier": r.tier.value if hasattr(r.tier, "value") else str(r.tier)}
                for r in results
            ],
            "achieved_count": sum(1 for r in results if r.achieved),
            "goals_evaluated": len(results),
        }
        if generate_ceiling and claim:
            report = generate_ceiling_report(
                claim=str(claim),
                evidence=list(evidence or []),
                baseline=str(baseline or ""),
                gaps=list(gaps or []),
                residual_risk=str(residual_risk or ""),
            )
            out["ceiling_report"] = asdict(report)
        return out
