"""distillation — Integrated synthesis (curation) + Multi-eval consensus averaging

来源: 09 opencode-moa (integrated synthesis + multi-eval consensus)

核心能力:
  1. DistilledIdea: 抽取的单个 idea (含 text / 来源 / 频次 / 重要性)
  2. DistillationResult: 蒸馏结果 (kept / dropped / 比例)
  3. extract_ideas: 从单 proposal 启发式抽取 idea (按句切, 关键词归一)
  4. curate_ideas: 按 frequency × importance 排序, top keep_ratio 保留
  5. distill_proposals: extract + curate 一站式
  6. multi_eval_average: 多个 evaluator 的多维评分求平均
  7. apply_bias_correction: 每个 evaluator 减自己的偏差

设计原则:
  - 所有逻辑基于真实数学/启发式 (无 mock、无 hardcoded 输出)
  - 不发明内容: extract_ideas 只搬运 proposals 中真实出现的句子
  - 与 multi_mode_synth / score_panel 解耦: 内部独立 DistilledIdea dataclass
  - frequency 跨 proposal 累加, 同一 normalized 句视为同 idea
"""
from __future__ import annotations
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Set, Any


__all__ = [
    "DistilledIdea",
    "DistillationResult",
    "MIN_WORDS_PER_IDEA",
    "MERGE_JACCARD_THRESHOLD",
    "SENTENCE_SPLIT_RE",
    "WORD_RE",
    "STOPWORDS",
    "extract_ideas",
    "curate_ideas",
    "distill_proposals",
    "multi_eval_average",
    "apply_bias_correction",
    "result_to_json",
    "idea_to_json",
]


# ============ 启发式常量 ============

# idea 最短词数 (过滤太短的句子)
MIN_WORDS_PER_IDEA = 5

# 跨 proposal 同 idea 合并的 Jaccard 阈值 (≥ 该值视为同 idea)
MERGE_JACCARD_THRESHOLD = 0.4

# 句子切分 (与 multi_mode_synth 一致)
SENTENCE_SPLIT_RE = re.compile(r"[。.!?！？;；\n]+")

# 词形归一: 英文 + 数字块 + 单个中文字符
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]*|[\u4e00-\u9fff]|\d+")

# 停用词 (英文 + 中文, 精简版)
STOPWORDS: Set[str] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after",
    "and", "or", "but", "if", "then", "else", "when", "where", "while",
    "this", "that", "these", "those", "i", "you", "he", "she", "it",
    "we", "they", "what", "which", "who", "whom",
    "的", "了", "是", "在", "和", "与", "或", "但", "如果", "那么",
    "我", "你", "他", "她", "它", "这", "那", "什么",
})


# ============ Dataclass 定义 ============

@dataclass
class DistilledIdea:
    """单个蒸馏 idea"""
    text: str
    source_proposals: List[int] = field(default_factory=list)
    frequency: int = 0
    importance_score: float = 0.0
    kept: bool = False

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class DistillationResult:
    """蒸馏结果"""
    kept_ideas: List[DistilledIdea] = field(default_factory=list)
    dropped_ideas: List[DistilledIdea] = field(default_factory=list)
    original_count: int = 0
    distilled_count: int = 0
    distillation_ratio: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# ============ 辅助函数 ============

def _tokenize(text: str) -> List[str]:
    """分词: 英文 + 数字块 + 单个中文字符"""
    if not text:
        return []
    return [t.lower() for t in WORD_RE.findall(text)]


def _split_sentences(text: str) -> List[str]:
    """按句号/分号/换行切句"""
    if not text or not text.strip():
        return []
    parts = SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _word_count(text: str) -> int:
    """统计词数"""
    if not text:
        return 0
    return len(_tokenize(text))


def _normalize_keywords(text: str) -> List[str]:
    """提取归一化的关键词 (去停用词, 小写)"""
    tokens = _tokenize(text)
    return [t for t in tokens if t not in STOPWORDS and len(t) >= 2]


def _jaccard_keywords(a: List[str], b: List[str]) -> float:
    """两个关键词集合的 Jaccard 相似度"""
    if not a and not b:
        return 1.0
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    inter = sa & sb
    return len(inter) / len(union)


def _compute_importance(text: str) -> float:
    """计算 idea 重要性 (0-1)

    启发式:
      - 长度权重 0-1 (min(1.0, len/100))
      - 关键词密度 0-1 (非停用词 / 总词数)
      - 综合: 0.5 * length + 0.5 * density
    """
    if not text or not text.strip():
        return 0.0
    n_words = _word_count(text)
    if n_words == 0:
        return 0.0
    kws = _normalize_keywords(text)
    n_kws = len(kws)
    if n_kws == 0:
        density = 0.0
    else:
        density = min(1.0, n_kws / n_words)

    length_w = min(1.0, len(text) / 100.0)
    importance = 0.5 * length_w + 0.5 * density
    return round(max(0.0, min(1.0, importance)), 4)


# ============ 1. extract_ideas ============

def extract_ideas(proposal_text: str, proposal_idx: int) -> List[DistilledIdea]:
    """从单 proposal 抽取 idea 列表

    启发式:
      1. 按句号/分号/换行切句
      2. 过滤 < MIN_WORDS_PER_IDEA 词的短句
      3. 同 proposal 内去重 (按归一关键词集合)
      4. 对每条 idea 计算 importance_score (长度 + 关键词密度)
      5. source_proposals = [proposal_idx]
      6. frequency = 1 (单 proposal)

    Args:
        proposal_text: 单 proposal 文本
        proposal_idx: proposal 索引 (用于 source_proposals)

    Returns:
        List[DistilledIdea]
    """
    if not proposal_text or not proposal_text.strip():
        return []

    sents = _split_sentences(proposal_text)
    ideas: List[DistilledIdea] = []
    seen: Set[str] = set()

    for sent in sents:
        if _word_count(sent) < MIN_WORDS_PER_IDEA:
            continue
        kws = sorted(set(_normalize_keywords(sent)))
        if not kws:
            continue
        sig = "|".join(kws)
        if sig in seen:
            continue
        seen.add(sig)
        ideas.append(
            DistilledIdea(
                text=sent,
                source_proposals=[proposal_idx],
                frequency=1,
                importance_score=_compute_importance(sent),
                kept=False,
            )
        )

    return ideas


# ============ 2. curate_ideas ============

def _merge_ideas_across_proposals(
    ideas_per_proposal: List[List[DistilledIdea]],
) -> List[DistilledIdea]:
    """跨 proposal 合并同 idea (按 Jaccard 关键词相似度 ≥ MERGE_JACCARD_THRESHOLD)

    流程:
      1. 展平所有 idea
      2. 按 Jaccard ≥ 阈值聚类 (贪心: 第一次见到该 idea 即作为 cluster 头)
      3. 同 cluster 合并: frequency 累加, source_proposals 合并, importance 取平均
      4. 保留第一次出现的 text (canonical)

    Returns:
        List[DistilledIdea] — 每个 unique idea 一条
    """
    if not ideas_per_proposal:
        return []

    # 展平 + 按 (proposal_idx, idea) 排序保证稳定性
    flat: List[DistilledIdea] = []
    for ideas in ideas_per_proposal:
        flat.extend(ideas)

    if not flat:
        return []

    clusters: List[Dict[str, Any]] = []

    for idea in flat:
        idea_kws = set(_normalize_keywords(idea.text))
        if not idea_kws:
            continue

        # 尝试合并到已有 cluster
        merged_into = False
        for cluster in clusters:
            cluster_kws = cluster["kws"]
            sim = _jaccard_keywords(list(idea_kws), list(cluster_kws))
            if sim >= MERGE_JACCARD_THRESHOLD:
                cluster["ideas"].append(idea)
                # 合并关键词
                cluster["kws"] = cluster_kws | idea_kws
                merged_into = True
                break

        if not merged_into:
            clusters.append({
                "kws": idea_kws,
                "ideas": [idea],
            })

    # 组装结果
    result: List[DistilledIdea] = []
    for cluster in clusters:
        ideas_in = cluster["ideas"]
        if not ideas_in:
            continue
        canonical = ideas_in[0].text
        total_freq = sum(i.frequency for i in ideas_in)
        all_sources: List[int] = []
        for i in ideas_in:
            for sp in i.source_proposals:
                if sp not in all_sources:
                    all_sources.append(sp)
        all_sources.sort()
        avg_importance = sum(i.importance_score for i in ideas_in) / len(ideas_in)
        result.append(
            DistilledIdea(
                text=canonical,
                source_proposals=all_sources,
                frequency=total_freq,
                importance_score=round(avg_importance, 4),
                kept=False,
            )
        )

    return result


def curate_ideas(
    ideas_per_proposal: List[List[DistilledIdea]],
    keep_ratio: float = 0.5,
) -> DistillationResult:
    """curation: 按 frequency × importance 排序, 保留 top keep_ratio

    Args:
        ideas_per_proposal: 每个 proposal 抽出的 idea 列表
        keep_ratio: 保留比例 (0-1, 0 → 全部丢弃, 1 → 全部保留)

    Returns:
        DistillationResult
    """
    merged = _merge_ideas_across_proposals(ideas_per_proposal)
    original_count = len(merged)

    if original_count == 0:
        return DistillationResult(
            kept_ideas=[],
            dropped_ideas=[],
            original_count=0,
            distilled_count=0,
            distillation_ratio=0.0,
            metadata={"keep_ratio": keep_ratio, "sort_key": "freq_x_importance"},
        )

    # clamp keep_ratio
    keep_ratio = max(0.0, min(1.0, float(keep_ratio)))

    # 排序: frequency × importance_score 降序
    def _score(idea: DistilledIdea) -> float:
        return float(idea.frequency) * float(idea.importance_score)

    ranked = sorted(merged, key=_score, reverse=True)

    # keep N = round(keep_ratio * total)
    n_keep = int(round(keep_ratio * original_count))
    # 至少 0, 至多 original_count
    n_keep = max(0, min(original_count, n_keep))

    kept = ranked[:n_keep]
    dropped = ranked[n_keep:]

    # 设置 kept 标记
    for idea in kept:
        idea.kept = True
    for idea in dropped:
        idea.kept = False

    distilled_count = len(kept)
    ratio = (distilled_count / original_count) if original_count > 0 else 0.0

    return DistillationResult(
        kept_ideas=kept,
        dropped_ideas=dropped,
        original_count=original_count,
        distilled_count=distilled_count,
        distillation_ratio=round(ratio, 4),
        metadata={
            "keep_ratio": keep_ratio,
            "sort_key": "freq_x_importance",
            "n_proposals": len(ideas_per_proposal),
        },
    )


# ============ 3. distill_proposals ============

def distill_proposals(
    proposals: List[str],
    keep_ratio: float = 0.5,
) -> DistillationResult:
    """一站式: extract + curate

    Args:
        proposals: 原始 proposal 文本列表
        keep_ratio: 保留比例 (0-1)

    Returns:
        DistillationResult
    """
    if not proposals:
        return DistillationResult(
            kept_ideas=[],
            dropped_ideas=[],
            original_count=0,
            distilled_count=0,
            distillation_ratio=0.0,
            metadata={"keep_ratio": keep_ratio, "n_proposals": 0},
        )

    ideas_per_proposal: List[List[DistilledIdea]] = []
    for idx, text in enumerate(proposals):
        ideas_per_proposal.append(extract_ideas(text, idx))

    result = curate_ideas(ideas_per_proposal, keep_ratio=keep_ratio)
    # 补充 n_proposals 到 metadata
    result.metadata["n_proposals"] = len(proposals)
    return result


# ============ 4. multi_eval_average ============

def multi_eval_average(
    evaluations: List[Dict[str, float]],
) -> Dict[str, float]:
    """多评分器共识平均

    多个 evaluator 的多维评分 → 每个维度求平均 + 偏差 (bias)

    偏差定义: 每个 evaluator 的平均 - 总体平均, 反映 evaluator 的系统偏差
             (正向 = 倾向高估, 负向 = 倾向低估)

    Args:
        evaluations: 每个 evaluator 的多维评分 (List[Dict[维度 → 分数]])
                     允许 evaluator 之间维度不完全一致

    Returns:
        Dict 含:
          - 各维度的平均分: "<dim>_avg"
          - 偏差字典: "biases" → {evaluator_idx: bias_value}
          - evaluator_count: 实际 evaluator 数
    """
    if not evaluations:
        return {
            "evaluator_count": 0,
            "biases": {},
            "dimensions": [],
        }

    # 收集所有维度
    all_dims: Set[str] = set()
    for ev in evaluations:
        all_dims.update(ev.keys())

    if not all_dims:
        return {
            "evaluator_count": len(evaluations),
            "biases": {},
            "dimensions": [],
        }

    # 计算每个维度的平均
    dim_avg: Dict[str, float] = {}
    for dim in all_dims:
        vals = [float(ev[dim]) for ev in evaluations if dim in ev]
        if vals:
            dim_avg[dim] = round(sum(vals) / len(vals), 4)

    # 总体平均 (所有 evaluator 所有维度的平均)
    all_vals: List[float] = []
    for ev in evaluations:
        for v in ev.values():
            all_vals.append(float(v))
    overall_mean = sum(all_vals) / len(all_vals) if all_vals else 0.0

    # 每个 evaluator 的偏差 = 该 evaluator 平均 - 总体平均
    biases: Dict[str, float] = {}
    for i, ev in enumerate(evaluations):
        ev_vals = [float(v) for v in ev.values()]
        if ev_vals:
            ev_mean = sum(ev_vals) / len(ev_vals)
            biases[str(i)] = round(ev_mean - overall_mean, 4)
        else:
            biases[str(i)] = 0.0

    result: Dict[str, Any] = {
        "evaluator_count": len(evaluations),
        "biases": biases,
        "dimensions": sorted(all_dims),
    }
    # 维度平均: key = "<dim>_avg"
    for dim in sorted(all_dims):
        if dim in dim_avg:
            result[f"{dim}_avg"] = dim_avg[dim]

    return result


# ============ 5. apply_bias_correction ============

def apply_bias_correction(
    scores: Dict[str, float],
    biases: Dict[str, float],
) -> Dict[str, float]:
    """偏差修正: score - bias

    Args:
        scores: 维度 → 分数
        biases: evaluator_idx (str) → bias_value

    Returns:
        修正后的 scores (同 key, value = score - avg_bias_for_dim)
        bias 作用于"该 evaluator 的所有维度", 这里取所有 bias 的平均作为通用修正
    """
    if not scores:
        return {}

    if not biases:
        return dict(scores)

    bias_vals = [float(b) for b in biases.values()]
    avg_bias = sum(bias_vals) / len(bias_vals) if bias_vals else 0.0

    corrected: Dict[str, float] = {}
    for k, v in scores.items():
        corrected[k] = round(float(v) - avg_bias, 4)

    return corrected


# ============ JSON 序列化 ============

def result_to_json(result: DistillationResult) -> str:
    """DistillationResult → JSON"""
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def idea_to_json(idea: DistilledIdea) -> str:
    """DistilledIdea → JSON"""
    return json.dumps(idea.to_dict(), ensure_ascii=False, indent=2)
