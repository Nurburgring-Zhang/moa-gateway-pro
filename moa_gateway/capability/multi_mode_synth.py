"""multi_mode_synth — 多模式综合器 (4 模式) + Integrated synthesis (来自 09 opencode-moa)

核心能力:
  1. SynthesisMode: 4 种综合模式 (CLASSIFICATION / INTEGRATED_SYNTHESIS /
     FINAL_SELECTION / CROSS_ITERATION)
  2. Proposal / SynthResult: 输入/输出数据契约 (与 convergent_detector 解耦)
  3. classify_proposals: 启发式关键词分类 (code/math/factual/creative/conversational)
  4. integrated_synthesis: 句子级 curation — 从 proposals 抽取真实句子,
     按 频次 × 长度 排序, top-N 拼到 target_chars
  5. final_selection: 选最高分 proposal, confidence = winner - 2nd
  6. cross_iteration: 跨轮比较 — convergence / best_of_each / recommended_adoption
  7. run_synthesis: 统一入口
  8. should_run_integration: 共识判定 — scores 标准差 < 0.1 → 单选, 否则集成

设计原则:
  - 所有逻辑基于真实数学/启发式 (无 mock、无 hardcoded 输出)
  - 不发明内容: integrated_synthesis 只搬运 proposals 中真实出现的句子
  - 与 convergent_detector 解耦: 内部独立 Proposal dataclass, 不 import
"""
from __future__ import annotations
import json
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Tuple, Set, Optional, Any


__all__ = [
    "SynthesisMode",
    "Proposal",
    "SynthResult",
    "CATEGORY_KEYWORDS",
    "CONSENSUS_STDDEV_THRESHOLD",
    "SENTENCE_SPLIT_RE",
    "WORD_RE",
    "STOPWORDS",
    "classify_proposals",
    "integrated_synthesis",
    "final_selection",
    "cross_iteration",
    "run_synthesis",
    "should_run_integration",
]


# ============ 枚举 ============

class SynthesisMode(str, Enum):
    """4 种综合模式"""
    CLASSIFICATION = "classification"
    INTEGRATED_SYNTHESIS = "integrated_synthesis"
    FINAL_SELECTION = "final_selection"
    CROSS_ITERATION = "cross_iteration"


# ============ 启发式常量 ============

# 共识判定阈值: scores 标准差 < 此值 → 视为高共识
CONSENSUS_STDDEV_THRESHOLD = 0.1

# 句子切分 (与 convergent_detector 一致)
SENTENCE_SPLIT_RE = re.compile(r"[。.!?！？;；\n]+")

# 词形归一: 英文 + 数字块 + 单个中文字符
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]*|[\u4e00-\u9fff]|\d+")

# 停用词 (精简版, 专供 synth 用)
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

# 分类关键词 (中英混合)
CATEGORY_KEYWORDS: Dict[str, Set[str]] = {
    "code": {
        "function", "class", "method", "variable", "import", "return",
        "def", "python", "javascript", "typescript", "java", "code",
        "compile", "runtime", "syntax", "error", "exception", "debug",
        "refactor", "module", "package", "library", "framework", "api",
        "endpoint", "request", "response", "lambda", "closure", "decorator",
        "函数", "类", "方法", "变量", "导入", "返回", "代码", "编译",
        "运行", "语法", "异常", "调试", "重构", "模块", "包", "库",
        "框架", "接口", "请求", "响应", "装饰器", "闭包",
    },
    "math": {
        "equation", "formula", "theorem", "proof", "calculate", "compute",
        "matrix", "vector", "tensor", "derivative", "integral", "limit",
        "algebra", "geometry", "calculus", "probability", "statistics",
        "distribution", "variance", "mean", "median", "sum", "product",
        "logarithm", "exponential", "polynomial", "function", "graph",
        "等于", "公式", "定理", "证明", "计算", "矩阵", "向量",
        "导数", "积分", "极限", "代数", "几何", "概率", "统计",
        "分布", "方差", "均值", "中位数", "求和", "对数", "指数",
        "多项式", "函数", "图",
    },
    "factual": {
        "fact", "data", "study", "research", "report", "according",
        "source", "reference", "cite", "evidence", "statistics",
        "percent", "million", "billion", "year", "date", "history",
        "founded", "established", "population", "located", "capital",
        "事实", "数据", "研究", "报告", "根据", "来源", "引用",
        "证据", "统计", "百分比", "百万", "十亿", "年", "日期",
        "历史", "成立", "人口", "位于", "首都",
    },
    "creative": {
        "story", "poem", "novel", "character", "plot", "theme",
        "imagine", "creative", "design", "art", "music", "paint",
        "draw", "write", "compose", "lyric", "verse", "chapter",
        "scene", "dialogue", "narrative", "metaphor", "symbolism",
        "故事", "诗", "小说", "角色", "情节", "主题", "想象",
        "创意", "设计", "艺术", "音乐", "画", "写作", "作曲",
        "歌词", "诗句", "章节", "场景", "对话", "叙事", "隐喻",
    },
    "conversational": {
        "hello", "hi", "thanks", "thank", "please", "sorry",
        "agree", "disagree", "yes", "no", "okay", "ok", "sure",
        "great", "awesome", "nice", "good", "bad", "cool", "wow",
        "你好", "谢谢", "感谢", "请", "抱歉", "同意", "不同意",
        "好的", "是", "不", "好", "棒", "不错", "糟糕",
    },
}

# 分类默认 fallback 顺序
CATEGORY_FALLBACK_ORDER: List[str] = [
    "conversational", "factual", "creative", "math", "code",
]


# ============ Dataclass 定义 ============

@dataclass
class Proposal:
    """单个 proposal (本模块内部, 不依赖 convergent_detector)"""
    proposal_idx: int
    author: str
    text: str
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SynthResult:
    """综合结果"""
    mode: SynthesisMode
    output: str
    source_attribution: Dict[int, str] = field(default_factory=dict)
    confidence: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d


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


def _jaccard(a: List[str], b: List[str]) -> float:
    """Jaccard 相似度"""
    if not a and not b:
        return 1.0
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    inter = sa & sb
    return len(inter) / len(union)


def _classify_one(text: str) -> Tuple[str, float]:
    """单条 proposal 分类

    Returns:
        (category, confidence)
        confidence = top_score / (top_score + second_score + 1e-9) ∈ (0, 1]
    """
    if not text or not text.strip():
        return "conversational", 0.0

    tokens = _tokenize(text)
    token_set = set(tokens)

    scores: Dict[str, int] = {}
    for cat, kws in CATEGORY_KEYWORDS.items():
        # 计算 token 与该类关键词的命中数
        hit = sum(1 for kw in kws if kw.lower() in token_set or kw in text.lower())
        scores[cat] = hit

    # 排序
    sorted_cats = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    top_cat, top_score = sorted_cats[0]
    second_score = sorted_cats[1][1] if len(sorted_cats) > 1 else 0

    if top_score == 0:
        # 无任何命中 — 用 fallback
        return "conversational", 0.0

    # confidence: top 命中相对优势
    conf = top_score / (top_score + second_score + 1e-9)
    # 同时考虑绝对命中数, 0 命中已经 0, 1 命中约 0.5, 5 命中约 0.83
    # 用 sqrt 平滑
    conf = conf * min(1.0, (top_score ** 0.5) / 2.0 + 0.5)
    conf = max(0.0, min(1.0, conf))
    return top_cat, conf


# ============ 模式 1: CLASSIFICATION ============

def classify_proposals(proposals: List[Proposal]) -> SynthResult:
    """启发式分类每条 proposal

    Args:
        proposals: 任意数量 (允许空 / 1 个)

    Returns:
        SynthResult, output 为 JSON 字符串
        metadata 含 categories 汇总
    """
    items: List[Dict[str, Any]] = []
    cat_counter: Counter = Counter()
    confidences: List[float] = []

    for p in proposals:
        cat, conf = _classify_one(p.text)
        items.append({
            "idx": p.proposal_idx,
            "author": p.author,
            "category": cat,
            "confidence": round(conf, 4),
        })
        cat_counter[cat] += 1
        confidences.append(conf)

    if not items:
        output = "[]"
        avg_conf = 0.0
    else:
        output = json.dumps(items, ensure_ascii=False)
        avg_conf = sum(confidences) / len(confidences)

    metadata = {
        "total": len(items),
        "category_distribution": dict(cat_counter),
        "avg_confidence": round(avg_conf, 4),
    }

    return SynthResult(
        mode=SynthesisMode.CLASSIFICATION,
        output=output,
        source_attribution={},
        confidence=avg_conf,
        metadata=metadata,
    )


# ============ 模式 2: INTEGRATED_SYNTHESIS ============

def integrated_synthesis(
    proposals: List[Proposal],
    target_chars: int = 300,
) -> SynthResult:
    """集成综合 (curation, 不发明)

    流程:
      1. 拆句
      2. 过滤 < 4 词的短句
      3. 对每个 sentence, 找出包含它的 proposal 集合
      4. 按 出现频次 (覆盖 proposal 数) × 长度 加权排序
      5. 累加到 target_chars, top-N 拼接
      6. source_attribution: 每个引用 sentence → 它在的 proposal 列表
         (这里 Dict[int, str]: key=首个 proposal_idx, value=该 sentence)

    Args:
        proposals: Proposal 列表
        target_chars: 目标字符数

    Returns:
        SynthResult
    """
    if not proposals:
        return SynthResult(
            mode=SynthesisMode.INTEGRATED_SYNTHESIS,
            output="",
            source_attribution={},
            confidence=0.0,
            metadata={"sentences_used": 0, "total_candidates": 0},
        )

    if target_chars <= 0:
        target_chars = 1

    # 1. 收集所有句子 + 反向索引
    sentence_to_proposals: Dict[str, List[int]] = defaultdict(list)
    # 同一 proposal 不重复计
    seen_per_proposal: Dict[int, Set[str]] = defaultdict(set)

    for p in proposals:
        sents = _split_sentences(p.text)
        for sent in sents:
            if _word_count(sent) < 4:
                continue
            sent_key = sent.strip()
            if sent_key in seen_per_proposal[p.proposal_idx]:
                continue
            seen_per_proposal[p.proposal_idx].add(sent_key)
            sentence_to_proposals[sent_key].append(p.proposal_idx)

    if not sentence_to_proposals:
        return SynthResult(
            mode=SynthesisMode.INTEGRATED_SYNTHESIS,
            output="",
            source_attribution={},
            confidence=0.0,
            metadata={"sentences_used": 0, "total_candidates": 0},
        )

    # 2. 排序权重: 出现 proposal 数 × 长度 (出现越多且越长越靠前)
    n_proposals = len(proposals)
    scored: List[Tuple[float, str, List[int]]] = []
    for sent, pidxs in sentence_to_proposals.items():
        freq = len(pidxs)
        length = len(sent)
        # 频率权重 0-1, 长度权重 0-1
        freq_w = freq / n_proposals
        len_w = min(1.0, length / 100.0)
        score = freq_w * 0.7 + len_w * 0.3
        scored.append((score, sent, pidxs))

    scored.sort(key=lambda x: -x[0])

    # 3. 累加到 target_chars
    selected: List[Tuple[str, List[int]]] = []
    total_chars = 0
    for score, sent, pidxs in scored:
        if total_chars + len(sent) > target_chars and selected:
            # 已至少选 1 句, 超出则停
            break
        selected.append((sent, pidxs))
        total_chars += len(sent)
        if total_chars >= target_chars:
            break

    # 4. 拼接
    output_parts = [s for s, _ in selected]
    output = " ".join(output_parts)

    # 5. source_attribution: key = 首个 proposal_idx, value = 该 sentence (截断 200 字)
    source_attribution: Dict[int, str] = {}
    for sent, pidxs in selected:
        key = pidxs[0]
        snippet = sent if len(sent) <= 200 else sent[:200] + "..."
        # 同一 proposal_idx 多个 sentence → 用换行拼接
        if key in source_attribution:
            source_attribution[key] += "\n" + snippet
        else:
            source_attribution[key] = snippet

    # 6. confidence: 基于重复率 (出现 proposal 多的 sentence 比例)
    # duplication_rate = sum(freq-1) / total (理论上限 = len(selected) * (n-1))
    if not selected:
        confidence = 0.0
    else:
        sum_freq = sum(len(pidxs) for _, pidxs in selected)
        max_possible = len(selected) * n_proposals
        # 真实覆盖率: 至少被 1 个 proposal 提的句子数 / 所有候选
        coverage = len(selected) / len(sentence_to_proposals)
        # 综合: 选中的总频次占比 (越接近 1, 越像共识)
        confidence = round(min(1.0, (sum_freq / max_possible) * coverage + coverage * 0.2), 4)
        # 重复率惩罚: 如果高度重复 (top 句在多数 proposal 中出现), 反而 confidence 高
        # 这里已经反映了, 不再扣分

    return SynthResult(
        mode=SynthesisMode.INTEGRATED_SYNTHESIS,
        output=output,
        source_attribution=source_attribution,
        confidence=confidence,
        metadata={
            "sentences_used": len(selected),
            "total_candidates": len(sentence_to_proposals),
            "total_chars": total_chars,
            "target_chars": target_chars,
        },
    )


# ============ 模式 3: FINAL_SELECTION ============

def final_selection(
    proposals: List[Proposal],
    scores: Dict[int, float],
) -> SynthResult:
    """选最高分 proposal

    Args:
        proposals: 候选 proposal
        scores: proposal_idx → score (0-1)

    Returns:
        SynthResult
        - output = 最高分 proposal 的 text
        - source_attribution = {winner_idx: text 前 100 字}
        - confidence = winner_score - 2nd_score
    """
    if not proposals:
        return SynthResult(
            mode=SynthesisMode.FINAL_SELECTION,
            output="",
            source_attribution={},
            confidence=0.0,
            metadata={"winner_idx": None, "runner_up_idx": None},
        )

    # 按 score 降序排
    ranked: List[Tuple[int, float]] = sorted(
        scores.items(), key=lambda x: -x[1]
    )

    # 选 winner — 仅在 proposals 里
    valid = [(idx, sc) for idx, sc in ranked if any(p.proposal_idx == idx for p in proposals)]

    if not valid:
        # scores 里没有匹配任何 proposal, 取 proposals[0] 作为退化
        winner = proposals[0]
        winner_score = float(scores.get(winner.proposal_idx, 0.0))
        runner_up_score = 0.0
        metadata = {
            "winner_idx": winner.proposal_idx,
            "winner_score": round(winner_score, 4),
            "runner_up_idx": None,
            "runner_up_score": 0.0,
        }
    else:
        winner_idx, winner_score = valid[0]
        winner = next(p for p in proposals if p.proposal_idx == winner_idx)
        runner_up_score = valid[1][1] if len(valid) > 1 else 0.0
        runner_up_idx = valid[1][0] if len(valid) > 1 else None
        metadata = {
            "winner_idx": winner_idx,
            "winner_score": round(winner_score, 4),
            "runner_up_idx": runner_up_idx,
            "runner_up_score": round(runner_up_score, 4),
        }

    confidence = max(0.0, round(winner_score - runner_up_score, 4))

    # 头 100 字
    snippet = winner.text[:100] + ("..." if len(winner.text) > 100 else "")

    return SynthResult(
        mode=SynthesisMode.FINAL_SELECTION,
        output=winner.text,
        source_attribution={winner.proposal_idx: snippet},
        confidence=confidence,
        metadata=metadata,
    )


# ============ 模式 4: CROSS_ITERATION ============

def cross_iteration(
    prev_proposals: List[Proposal],
    curr_proposals: List[Proposal],
) -> SynthResult:
    """跨轮比较

    三种信号:
      - convergence: 想法 Jaccard > 0.5 → 标记收敛
      - best_of_each: 各轮最佳 (按 text 长度 + 关键词密度) 合并
      - recommended_adoption: curr 优势指标 ≥ prev → 推 curr

    Args:
        prev_proposals: 上一轮 proposals
        curr_proposals: 本轮 proposals

    Returns:
        SynthResult.output: 描述字符串 (含 recommendation)
    """
    if not prev_proposals and not curr_proposals:
        return SynthResult(
            mode=SynthesisMode.CROSS_ITERATION,
            output="",
            source_attribution={},
            confidence=0.0,
            metadata={"convergence": 0.0, "recommendation": "insufficient_data"},
        )

    # 计算每轮 token 集合
    def _proposal_kws(props: List[Proposal]) -> Set[str]:
        all_kws: Set[str] = set()
        for p in props:
            tokens = _tokenize(p.text)
            all_kws.update(t for t in tokens if t not in STOPWORDS and len(t) >= 2)
        return all_kws

    prev_kws = _proposal_kws(prev_proposals)
    curr_kws = _proposal_kws(curr_proposals)

    # 1. convergence (Jaccard)
    convergence = _jaccard(list(prev_kws), list(curr_kws))
    converged = convergence > 0.5

    # 2. best_of_each — 用 score 启发式: 长度 + 关键词密度
    def _score_prop(p: Proposal) -> float:
        tokens = _tokenize(p.text)
        n = len(tokens)
        if n == 0:
            return 0.0
        unique = len(set(t for t in tokens if t not in STOPWORDS))
        density = unique / n
        return n * density

    prev_best = max(prev_proposals, key=_score_prop) if prev_proposals else None
    curr_best = max(curr_proposals, key=_score_prop) if curr_proposals else None

    # 3. recommended_adoption — 综合分
    def _round_score(props: List[Proposal]) -> float:
        if not props:
            return 0.0
        return sum(_score_prop(p) for p in props) / len(props)

    prev_avg = _round_score(prev_proposals)
    curr_avg = _round_score(curr_proposals)

    if not prev_proposals:
        recommendation = "adopt_curr"
        adopt_reason = "no previous round to compare"
    elif not curr_proposals:
        recommendation = "keep_prev"
        adopt_reason = "no current proposals"
    else:
        # 阈值: curr 需比 prev 高 5% 才推 adoption
        if curr_avg > prev_avg * 1.05:
            recommendation = "adopt_curr"
            adopt_reason = f"curr_avg={curr_avg:.2f} > prev_avg={prev_avg:.2f}"
        elif curr_avg < prev_avg * 0.95:
            recommendation = "keep_prev"
            adopt_reason = f"prev_avg={prev_avg:.2f} > curr_avg={curr_avg:.2f}"
        else:
            recommendation = "converged"
            adopt_reason = f"avg within ±5% (prev={prev_avg:.2f}, curr={curr_avg:.2f})"

    # 4. 组装 output
    lines: List[str] = []
    lines.append(f"[CROSS_ITERATION] prev={len(prev_proposals)} curr={len(curr_proposals)}")
    lines.append(f"convergence: {convergence:.4f} ({'CONVERGED' if converged else 'DIVERGENT'})")
    if prev_best is not None:
        snippet = prev_best.text[:80] + ("..." if len(prev_best.text) > 80 else "")
        lines.append(f"prev_best: idx={prev_best.proposal_idx} score={_score_prop(prev_best):.2f} | {snippet}")
    if curr_best is not None:
        snippet = curr_best.text[:80] + ("..." if len(curr_best.text) > 80 else "")
        lines.append(f"curr_best: idx={curr_best.proposal_idx} score={_score_prop(curr_best):.2f} | {snippet}")
    lines.append(f"avg_score: prev={prev_avg:.4f} curr={curr_avg:.4f}")
    lines.append(f"recommendation: {recommendation} ({adopt_reason})")

    output = "\n".join(lines)

    # source_attribution: best of each
    source_attribution: Dict[int, str] = {}
    if prev_best is not None:
        snippet = prev_best.text[:100] + ("..." if len(prev_best.text) > 100 else "")
        source_attribution[prev_best.proposal_idx] = f"[prev_best] {snippet}"
    if curr_best is not None:
        snippet = curr_best.text[:100] + ("..." if len(curr_best.text) > 100 else "")
        source_attribution[curr_best.proposal_idx] = f"[curr_best] {snippet}"

    # confidence: convergence 越接近 1 或 recommendation 越确定, confidence 越高
    if recommendation == "converged":
        confidence = round(min(1.0, convergence + 0.2), 4)
    elif recommendation == "adopt_curr" and curr_proposals:
        # 优势越大越 confident
        if prev_avg > 0:
            ratio = curr_avg / prev_avg
            confidence = round(min(1.0, max(0.0, (ratio - 1.0) * 2.0 + 0.3)), 4)
        else:
            confidence = 0.5
    elif recommendation == "keep_prev" and prev_proposals:
        if curr_avg > 0:
            ratio = prev_avg / curr_avg
            confidence = round(min(1.0, max(0.0, (ratio - 1.0) * 2.0 + 0.3)), 4)
        else:
            confidence = 0.5
    else:
        confidence = 0.0

    metadata = {
        "convergence": round(convergence, 4),
        "converged": converged,
        "prev_avg_score": round(prev_avg, 4),
        "curr_avg_score": round(curr_avg, 4),
        "prev_best_idx": prev_best.proposal_idx if prev_best else None,
        "curr_best_idx": curr_best.proposal_idx if curr_best else None,
        "recommendation": recommendation,
        "adopt_reason": adopt_reason,
    }

    return SynthResult(
        mode=SynthesisMode.CROSS_ITERATION,
        output=output,
        source_attribution=source_attribution,
        confidence=confidence,
        metadata=metadata,
    )


# ============ 统一入口 ============

def run_synthesis(
    mode: SynthesisMode,
    proposals: List[Proposal],
    **kwargs: Any,
) -> SynthResult:
    """统一入口 — 根据 mode 分派

    Args:
        mode: SynthesisMode
        proposals: Proposal 列表
        **kwargs:
            - target_chars (int, INTEGRATED_SYNTHESIS)
            - scores (Dict[int, float], FINAL_SELECTION)
            - prev_proposals (List[Proposal], CROSS_ITERATION)
            - curr_proposals (List[Proposal], CROSS_ITERATION)
    """
    if mode == SynthesisMode.CLASSIFICATION:
        return classify_proposals(proposals)
    elif mode == SynthesisMode.INTEGRATED_SYNTHESIS:
        target_chars = int(kwargs.get("target_chars", 300))
        return integrated_synthesis(proposals, target_chars=target_chars)
    elif mode == SynthesisMode.FINAL_SELECTION:
        scores = kwargs.get("scores") or {}
        if not isinstance(scores, dict):
            scores = {}
        return final_selection(proposals, scores=scores)
    elif mode == SynthesisMode.CROSS_ITERATION:
        prev_proposals = kwargs.get("prev_proposals") or []
        curr_proposals = kwargs.get("curr_proposals")
        if curr_proposals is None:
            curr_proposals = proposals
        return cross_iteration(prev_proposals, curr_proposals)
    else:
        raise ValueError(f"unknown SynthesisMode: {mode!r}")


# ============ 共识判定 ============

def should_run_integration(
    proposals: List[Proposal],
    scores: Dict[int, float],
) -> bool:
    """判定是否需要 INTEGRATED_SYNTHESIS

    规则:
      - proposals 为空 → False (无内容)
      - scores 标准差 < CONSENSUS_STDDEV_THRESHOLD (0.1) → False (高共识, 单选就够)
      - 否则 → True (低共识, 用集成)

    Returns:
        bool
    """
    if not proposals:
        return False
    if not scores:
        # 无 score → 退化为: 多 proposal 倾向集成
        return len(proposals) > 1

    values = [float(v) for v in scores.values()]
    if len(values) < 2:
        return False  # 单个分数, 无分歧可言

    try:
        std = statistics.pstdev(values)
    except statistics.StatisticsError:
        return False

    return std >= CONSENSUS_STDDEV_THRESHOLD
