"""cross_iter_synth — Cross-iteration synthesis (M-50) + Step-5 three modes (M-52)

核心能力 (来自 09 opencode-moa cross-iteration synthesis 协议):
  1. IterationSnapshot: 一次迭代的轻量快照 (proposals / best_score / best_idx / summary)
  2. SynthesisMode 枚举: CONVERGENCE / BEST_OF_EACH / RECOMMENDED_ADOPTION
  3. SynthesisResult: synthesis 输出 (mode / output / sources / confidence)
  4. convergence_mode: 关键词 Jaccard 算 iter 间 overlap; overlap > 0.5 → convergent
  5. best_of_each_mode: 每个 iter 取 best_proposal → 综合输出
  6. recommended_adoption_mode: curr > prev * 1.05 → adopt curr; 反之 prev; 平局 either
  7. Step5Mode 枚举: SINTESIS_CENTRAL / SELF_IMPROVE / SKIP
  8. run_step5: Step-5 三种模式的统一入口

设计原则:
  - 所有逻辑基于真实数学 (Jaccard, 阈值 0.5/1.05, 关键词去停用词) — 无 mock
  - confidence 用 0-1 实数, 反映"对结果有多确定" (convergence 越高越确定)
  - JSON 序列化完整覆盖 dataclass + Enum
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Set, Optional, Tuple


__all__ = [
    "IterationSnapshot",
    "SynthesisMode",
    "SynthesisResult",
    "Step5Mode",
    "Step5Result",
    "JACCARD_CONVERGENCE_THRESHOLD",
    "ADOPTION_RATIO",
    "convergence_mode",
    "best_of_each_mode",
    "recommended_adoption_mode",
    "run_step5",
    "snapshot_to_dict",
    "result_to_dict",
    "step5_result_to_dict",
    "snapshot_from_dict",
]


# ============ 启发式常量 ============

# 跨 iter 关键词 Jaccard > 此值视为 convergence
JACCARD_CONVERGENCE_THRESHOLD: float = 0.5

# 采纳阈值: curr.best_score > prev.best_score * ADOPTION_RATIO → 推荐采用 curr
ADOPTION_RATIO: float = 1.05

# 关键词 token 化 (中英)
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]+|[\u4e00-\u9fff]")

# 简单停用词
STOPWORDS: Set[str] = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "need", "dare",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "and", "or", "but", "if", "then", "else", "when", "where", "while",
    "this", "that", "these", "those", "i", "you", "he", "she", "it",
    "we", "they", "what", "which", "who", "whom", "how", "why",
    "的", "了", "是", "在", "和", "与", "或", "但", "如果", "那么",
    "我", "你", "他", "她", "它", "我们", "他们", "这", "那", "什么",
    "怎么", "为什么", "哪个", "哪些", "一个", "一些", "可以", "可能",
    "应该", "需要", "进行", "使用", "通过", "得到",
})


# ============ 枚举 ============

class SynthesisMode(str, Enum):
    """M-50 cross-iteration synthesis 三种模式"""
    CONVERGENCE = "convergence"
    BEST_OF_EACH = "best_of_each"
    RECOMMENDED_ADOPTION = "recommended_adoption"


class Step5Mode(str, Enum):
    """M-52 Step-5 三种模式 (sintesis_central / self_improve / skip)"""
    SINTESIS_CENTRAL = "sintesis_central"
    SELF_IMPROVE = "self_improve"
    SKIP = "skip"


# ============ Dataclass 定义 ============

@dataclass
class IterationSnapshot:
    """一次迭代的轻量快照 (M-50 输入)"""
    iter_idx: int
    proposals: List[str] = field(default_factory=list)
    best_score: float = 0.0
    best_proposal_idx: int = -1
    summary: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class SynthesisResult:
    """M-50 cross-iteration synthesis 的输出"""
    mode: SynthesisMode
    output: str
    sources: List[int] = field(default_factory=list)  # 引用的 iter indices
    confidence: float = 0.0  # 0-1

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d


@dataclass
class Step5Result:
    """M-52 Step-5 三种模式的输出"""
    mode: Step5Mode
    output: str
    action_taken: str = ""

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d


# ============ 辅助函数 ============

def _tokenize(text: str) -> List[str]:
    if not text:
        return []
    return [t.lower() for t in WORD_RE.findall(text)]


def _keywords(text: str) -> List[str]:
    """去停用词 + 短词过滤 + dedup 保序"""
    if not text:
        return []
    out: List[str] = []
    seen: Set[str] = set()
    for tok in _tokenize(text):
        if tok in STOPWORDS:
            continue
        if len(tok) < 2 and not re.match(r"[\u4e00-\u9fff]", tok):
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _snapshot_keyword_set(snap: IterationSnapshot) -> Set[str]:
    """聚合一次 snapshot 的关键词 (proposals + summary)"""
    kws: Set[str] = set()
    for p in snap.proposals:
        for k in _keywords(p):
            kws.add(k)
    for k in _keywords(snap.summary):
        kws.add(k)
    return kws


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    inter = a & b
    return len(inter) / len(union)


def _truncate(text: str, max_len: int = 80) -> str:
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _safe_best_text(snap: IterationSnapshot) -> str:
    if 0 <= snap.best_proposal_idx < len(snap.proposals):
        return snap.proposals[snap.best_proposal_idx]
    return ""


# ============ 序列化辅助 ============

def snapshot_to_dict(snap: IterationSnapshot) -> Dict:
    return snap.to_dict()


def result_to_dict(result: SynthesisResult) -> Dict:
    return result.to_dict()


def step5_result_to_dict(result: Step5Result) -> Dict:
    return result.to_dict()


def snapshot_from_dict(d: Dict) -> IterationSnapshot:
    return IterationSnapshot(
        iter_idx=int(d.get("iter_idx", 0)),
        proposals=list(d.get("proposals", []) or []),
        best_score=float(d.get("best_score", 0.0)),
        best_proposal_idx=int(d.get("best_proposal_idx", -1)),
        summary=str(d.get("summary", "") or ""),
    )


# ============ 核心 API: convergence_mode ============

def convergence_mode(iters: List[IterationSnapshot]) -> SynthesisResult:
    """M-50 convergence 模式: 用关键词 Jaccard 算 iter 间 overlap

    流程:
      1. 收集每个 iter 的关键词集合
      2. 算两两 Jaccard 平均值
      3. avg_jaccard > JACCARD_CONVERGENCE_THRESHOLD → convergent
      4. output 列出所有"高频公共关键词" (出现在 >= half 个 iter)
      5. confidence = min(1.0, avg_jaccard)

    边界:
      - 0 iters: confidence=0, output="(no iterations)"
      - 1 iter: confidence=1.0 (自身 trivially convergent), output=该 iter 关键词
    """
    sources = [s.iter_idx for s in iters]
    if not iters:
        return SynthesisResult(
            mode=SynthesisMode.CONVERGENCE,
            output="(no iterations)",
            sources=[],
            confidence=0.0,
        )

    kw_sets: List[Set[str]] = [_snapshot_keyword_set(s) for s in iters]

    if len(iters) == 1:
        # 单一 iter: trivial convergent, output 其关键词
        only = kw_sets[0]
        out_kws = sorted(only)
        output = "Single iteration; convergent keywords: " + ", ".join(out_kws) if out_kws else "Single iteration; no keywords"
        return SynthesisResult(
            mode=SynthesisMode.CONVERGENCE,
            output=output,
            sources=sources,
            confidence=1.0,
        )

    # 两两 Jaccard
    pair_scores: List[float] = []
    for i in range(len(kw_sets)):
        for j in range(i + 1, len(kw_sets)):
            pair_scores.append(_jaccard(kw_sets[i], kw_sets[j]))
    avg_j = sum(pair_scores) / len(pair_scores) if pair_scores else 0.0
    is_convergent = avg_j > JACCARD_CONVERGENCE_THRESHOLD

    # 公共关键词: 出现在 >= half iter
    half = max(1, len(kw_sets) // 2)
    counts: Dict[str, int] = {}
    for ks in kw_sets:
        for k in ks:
            counts[k] = counts.get(k, 0) + 1
    common_kws = sorted(k for k, c in counts.items() if c >= half)

    if is_convergent:
        head = f"CONVERGENT (avg Jaccard={avg_j:.3f} > {JACCARD_CONVERGENCE_THRESHOLD})"
    else:
        head = f"DIVERGENT (avg Jaccard={avg_j:.3f} <= {JACCARD_CONVERGENCE_THRESHOLD})"

    if common_kws:
        output = f"{head}; shared keywords: " + ", ".join(common_kws)
    else:
        output = f"{head}; no shared keywords"

    return SynthesisResult(
        mode=SynthesisMode.CONVERGENCE,
        output=output,
        sources=sources,
        confidence=round(min(1.0, max(0.0, avg_j)), 4),
    )


# ============ 核心 API: best_of_each_mode ============

def best_of_each_mode(iters: List[IterationSnapshot]) -> SynthesisResult:
    """M-50 best_of_each 模式: 每个 iter 取 best_proposal → 综合输出

    流程:
      1. 每个 iter 取 best_proposal_idx 对应的文本 (或其截断)
      2. 拼装成 "iter{N}: {text} (score={s})" 多行
      3. sources = 所有 iter indices
      4. confidence = mean(best_score) / 100 (归一到 0-1), 但 0-1 裁剪
    """
    sources: List[int] = []
    lines: List[str] = []
    score_sum = 0.0
    score_n = 0

    for s in iters:
        sources.append(s.iter_idx)
        text = _safe_best_text(s)
        snippet = _truncate(text, max_len=80) if text else "(no best proposal)"
        lines.append(f"iter{s.iter_idx}: {snippet} (score={s.best_score:.2f})")
        if s.best_score > 0:
            score_sum += s.best_score
            score_n += 1

    if not lines:
        output = "(no iterations)"
        confidence = 0.0
    else:
        output = "Best of each iteration:\n" + "\n".join(lines)
        mean_score = score_sum / score_n if score_n > 0 else 0.0
        # 假设 best_score 范围 0-100, 归一到 0-1
        confidence = round(min(1.0, max(0.0, mean_score / 100.0)), 4)

    return SynthesisResult(
        mode=SynthesisMode.BEST_OF_EACH,
        output=output,
        sources=sources,
        confidence=confidence,
    )


# ============ 核心 API: recommended_adoption_mode ============

def recommended_adoption_mode(
    curr: IterationSnapshot,
    prev: IterationSnapshot,
) -> SynthesisResult:
    """M-50 recommended_adoption 模式: 决定采用 curr 还是 prev

    流程:
      1. 比较 curr.best_score vs prev.best_score
      2. curr > prev * ADOPTION_RATIO (1.05) → adopt curr
      3. prev > curr * ADOPTION_RATIO       → adopt prev
      4. 其它 (差距 < 5% 或任一为 0) → either
      5. confidence = |delta| / max(prev, curr, 1) (差距越大越确定)
    """
    cs = float(curr.best_score)
    ps = float(prev.best_score)
    delta = cs - ps

    # 平局 / 退化情况: 任一为 0 且另一边为 0 → either
    if ps <= 0 and cs <= 0:
        adoption = "either"
    elif cs > ps * ADOPTION_RATIO:
        adoption = "curr"
    elif ps > cs * ADOPTION_RATIO:
        adoption = "prev"
    else:
        adoption = "either"

    # confidence: 归一化的差距 (0-1)
    denom = max(abs(cs), abs(ps), 1.0)
    confidence = round(min(1.0, abs(delta) / denom), 4)

    # output: 描述 + 选中的 proposal 文本
    chosen_text = ""
    chosen_idx = -1
    if adoption == "curr":
        chosen_idx = curr.best_proposal_idx
        chosen_text = _truncate(_safe_best_text(curr), max_len=120)
    elif adoption == "prev":
        chosen_idx = prev.best_proposal_idx
        chosen_text = _truncate(_safe_best_text(prev), max_len=120)
    else:
        # either: 选分数更高的那个作为参考输出
        if cs >= ps:
            chosen_idx = curr.best_proposal_idx
            chosen_text = _truncate(_safe_best_text(curr), max_len=120)
        else:
            chosen_idx = prev.best_proposal_idx
            chosen_text = _truncate(_safe_best_text(prev), max_len=120)

    output = (
        f"Recommended adoption: {adoption} "
        f"(prev={ps:.2f}, curr={cs:.2f}, delta={delta:+.2f}, "
        f"ratio_thresh={ADOPTION_RATIO}); "
        f"chosen proposal[{chosen_idx}]: {chosen_text}"
    )

    return SynthesisResult(
        mode=SynthesisMode.RECOMMENDED_ADOPTION,
        output=output,
        sources=[prev.iter_idx, curr.iter_idx],
        confidence=confidence,
    )


# ============ 核心 API: run_step5 ============

def _self_improve_suggestions(synth: SynthesisResult) -> List[str]:
    """基于 best_of_each 输出生成改进建议"""
    suggestions: List[str] = []
    if synth.confidence < 0.5:
        suggestions.append("Mean best score is low; consider re-evaluating the rubric.")
    if not synth.sources:
        suggestions.append("No iterations available; cannot derive patterns.")
    elif len(synth.sources) < 3:
        suggestions.append("Few iterations available; recommend at least 3 rounds for stable patterns.")
    suggestions.append("Cross-compare top proposals; merge complementary parts where overlap is high.")
    suggestions.append("Identify the weakest iteration (lowest best_score) and investigate cause.")
    return suggestions


def run_step5(iters: List[IterationSnapshot], mode: Step5Mode) -> Step5Result:
    """M-52 Step-5 三种模式统一入口

    SINTESIS_CENTRAL: 跑 convergence_mode, 取最稳的综合结论
    SELF_IMPROVE:    跑 best_of_each_mode + 生成改进建议
    SKIP:            仅返回所有 iter 中最佳 best_proposal (best_score 最大)
    """
    if mode == Step5Mode.SINTESIS_CENTRAL:
        synth = convergence_mode(iters)
        action = f"ran convergence_mode on {len(iters)} iteration(s)"
        return Step5Result(
            mode=mode,
            output=synth.output,
            action_taken=action,
        )

    if mode == Step5Mode.SELF_IMPROVE:
        synth = best_of_each_mode(iters)
        suggestions = _self_improve_suggestions(synth)
        improve_block = "\n".join(f"  - {s}" for s in suggestions)
        output = synth.output + "\nImprovement suggestions:\n" + improve_block
        action = f"ran best_of_each_mode + generated {len(suggestions)} suggestion(s)"
        return Step5Result(
            mode=mode,
            output=output,
            action_taken=action,
        )

    # SKIP
    if not iters:
        return Step5Result(
            mode=mode,
            output="(no iterations to skip)",
            action_taken="skipped (empty history)",
        )
    # 选 best_score 最高的 iter
    best = max(iters, key=lambda s: s.best_score)
    text = _truncate(_safe_best_text(best), max_len=120)
    output = f"SKIP: best proposal from iter{best.iter_idx} (score={best.best_score:.2f}): {text}"
    return Step5Result(
        mode=mode,
        output=output,
        action_taken=f"skipped synthesis, picked iter{best.iter_idx}",
    )


# ============ JSON 序列化辅助 (顶层) ============

def synth_payload(result: SynthesisResult) -> str:
    """SynthesisResult → JSON 字符串"""
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)


def step5_payload(result: Step5Result) -> str:
    """Step5Result → JSON 字符串"""
    return json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
