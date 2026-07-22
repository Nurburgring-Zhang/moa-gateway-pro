"""feedback_loop — Feedback-aware iteration (M-19) + Cross-iteration synthesis (M-50)

核心能力 (来自 09 opencode-moa feedback.json + 跨迭代评分传递):
  1. IterationRecord: 一次迭代的 proposals / panel_scores / convergent / conflicts / 选择
  2. Feedback: 一次迭代的总结 (strengths / weaknesses / next_iter_directives)
  3. feedback.json 持久化: save / load / append_iteration / load_history
  4. analyze_iteration: 算 panel_scores std → consensus, 提取 strengths/weaknesses/directives
  5. format_next_iter_prompt: 拼装给下轮的 prompt (含历史 strengths/weaknesses/directives)
  6. detect_convergence: 滑窗看 top1 panel_score 是否稳定 (std + trend)
  7. cross_iter_synthesize: 关键词 Jaccard (convergence) / best_of_each / adoption (>5%)

设计原则:
  - 所有逻辑基于真实数学 (std, Jaccard, 阈值) — 无 mock、无 hardcoded 输出
  - feedback.json 格式: {"iterations": [...], "latest_feedback": {...}}
  - strengths: panel_score >= STRENGTH_THRESHOLD (40)
  - weaknesses: panel_score < WEAKNESS_THRESHOLD (20)
  - consensus: panel_scores std < CONSENSUS_STD (5.0) → 评委意见一致
  - convergence: top1 std < CONVERGENCE_STD (3.0) within window
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

__all__ = [
    "IterationRecord",
    "Feedback",
    "STRENGTH_THRESHOLD",
    "WEAKNESS_THRESHOLD",
    "CONSENSUS_STD",
    "CONVERGENCE_STD",
    "JACCARD_CONVERGENCE_THRESHOLD",
    "ADOPTION_RATIO",
    "save_feedback",
    "load_feedback",
    "append_iteration",
    "load_history",
    "analyze_iteration",
    "format_next_iter_prompt",
    "detect_convergence",
    "cross_iter_synthesize",
    "record_to_dict",
    "feedback_to_dict",
]


# ============ 启发式常量 ============

# 评分阈值 (panel_score 范围 0-50)
STRENGTH_THRESHOLD: float = 40.0  # >= 此值视为强项
WEAKNESS_THRESHOLD: float = 20.0  # < 此值视为弱项

# consensus: panel_scores std < 此值 → 评委意见一致
CONSENSUS_STD: float = 5.0

# 收敛检测: top1 std < 此值 within window
CONVERGENCE_STD: float = 3.0

# 跨迭代 convergence: 关键词 Jaccard > 此值视为两轮在同议题
JACCARD_CONVERGENCE_THRESHOLD: float = 0.5

# 采纳阈值: curr.panel > prev.panel * ADOPTION_RATIO → 推荐采用 curr
ADOPTION_RATIO: float = 1.05

# 关键词 token 化
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]+|[\u4e00-\u9fff]")

# 简单停用词 (中英)
STOPWORDS: set[str] = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "must",
        "shall",
        "can",
        "need",
        "dare",
        "to",
        "of",
        "in",
        "for",
        "on",
        "with",
        "at",
        "by",
        "from",
        "as",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "and",
        "or",
        "but",
        "if",
        "then",
        "else",
        "when",
        "where",
        "while",
        "this",
        "that",
        "these",
        "those",
        "i",
        "you",
        "he",
        "she",
        "it",
        "we",
        "they",
        "what",
        "which",
        "who",
        "whom",
        "how",
        "why",
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "或",
        "但",
        "如果",
        "那么",
        "我",
        "你",
        "他",
        "她",
        "它",
        "我们",
        "他们",
        "这",
        "那",
        "什么",
        "怎么",
        "为什么",
        "哪个",
        "哪些",
        "一个",
        "一些",
        "可以",
        "可能",
        "应该",
        "需要",
        "进行",
        "使用",
        "通过",
        "得到",
    }
)


# ============ Dataclass 定义 ============


@dataclass
class IterationRecord:
    """一次迭代的完整记录"""

    iter_idx: int
    proposals: list[str]
    panel_scores: dict[int, float] = field(default_factory=dict)  # proposal_idx -> 0-50
    convergent_ideas: list[str] = field(default_factory=list)
    conflicts_resolved: list[str] = field(default_factory=list)
    selected_proposal_idx: int = 0
    timestamp: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> IterationRecord:
        """接受字段别名,自动映射到正确字段。空 dict 走 defaults。"""
        kwargs = {}
        if "iter_idx" in d:
            kwargs["iter_idx"] = d["iter_idx"]
        if "iter_idx" not in kwargs and "iteration" in d:
            kwargs["iter_idx"] = d["iteration"]
        if "proposals" in d:
            kwargs["proposals"] = d["proposals"]
        if "proposals" not in kwargs and "proposals" in d:
            kwargs["proposals"] = d["proposals"]
        if "panel_scores" in d:
            kwargs["panel_scores"] = d["panel_scores"]
        if "panel_scores" not in kwargs and "panel_scores" in d:
            kwargs["panel_scores"] = d["panel_scores"]
        if "convergent_ideas" in d:
            kwargs["convergent_ideas"] = d["convergent_ideas"]
        if "convergent_ideas" not in kwargs and "ideas" in d:
            kwargs["convergent_ideas"] = d["ideas"]
        if "conflicts_resolved" in d:
            kwargs["conflicts_resolved"] = d["conflicts_resolved"]
        if "conflicts_resolved" not in kwargs and "conflicts_resolved" in d:
            kwargs["conflicts_resolved"] = d["conflicts_resolved"]
        if "selected_proposal_idx" in d:
            kwargs["selected_proposal_idx"] = d["selected_proposal_idx"]
        if "selected_proposal_idx" not in kwargs and "selected_idx" in d:
            kwargs["selected_proposal_idx"] = d["selected_idx"]
        if "selected_proposal_idx" not in kwargs and "best_idx" in d:
            kwargs["selected_proposal_idx"] = d["best_idx"]
        if "timestamp" in d:
            kwargs["timestamp"] = d["timestamp"]
        if "timestamp" not in kwargs and "timestamp" in d:
            kwargs["timestamp"] = d["timestamp"]
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Feedback:
    """一次迭代的反馈总结"""

    iter_idx: int
    summary: str
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    next_iter_directives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ============ 辅助函数 ============


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std(values: list[float]) -> float:
    """总体标准差 (population std) — 用于评委一致性判定"""
    if not values:
        return 0.0
    if len(values) == 1:
        return 0.0
    mu = _mean(values)
    var = sum((v - mu) ** 2 for v in values) / len(values)
    return math.sqrt(var)


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return [t.lower() for t in WORD_RE.findall(text)]


def _keywords(text: str) -> list[str]:
    """去停用词 + 短词过滤"""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for tok in _tokenize(text):
        if tok in STOPWORDS:
            continue
        # 短英文 (len<2) 过滤, 单字中文作为有意义保留
        if len(tok) < 2 and not re.match(r"[\u4e00-\u9fff]", tok):
            continue
        if tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _jaccard(a: list[str], b: list[str]) -> float:
    if not a and not b:
        return 1.0
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    inter = sa & sb
    return len(inter) / len(union)


def _record_keyword_set(record: IterationRecord) -> set[str]:
    """聚合一次迭代的关键词 (proposals + convergent_ideas)"""
    kws: set[str] = set()
    for p in record.proposals:
        for k in _keywords(p):
            kws.add(k)
    for idea in record.convergent_ideas:
        for k in _keywords(idea):
            kws.add(k)
    return kws


def _top1_score(record: IterationRecord) -> float:
    """取 panel_scores 中最高分; 空返回 0"""
    if not record.panel_scores:
        return 0.0
    return max(record.panel_scores.values())


def _truncate(text: str, max_len: int = 80) -> str:
    """截断用于 prompt 输出"""
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# ============ 序列化辅助 ============


def record_to_dict(record: IterationRecord) -> dict:
    """IterationRecord -> dict (JSON 友好)"""
    return record.to_dict()


def feedback_to_dict(feedback: Feedback) -> dict:
    """Feedback -> dict (JSON 友好)"""
    return feedback.to_dict()


def _record_from_dict(d: dict) -> IterationRecord:
    """dict -> IterationRecord (字段兼容)"""
    return IterationRecord(
        iter_idx=int(d.get("iter_idx", 0)),
        proposals=list(d.get("proposals", []) or []),
        panel_scores=dict(d.get("panel_scores", {}) or {}),
        convergent_ideas=list(d.get("convergent_ideas", []) or []),
        conflicts_resolved=list(d.get("conflicts_resolved", []) or []),
        selected_proposal_idx=int(d.get("selected_proposal_idx", 0)),
        timestamp=float(d.get("timestamp", 0.0)),
    )


def _feedback_from_dict(d: dict) -> Feedback:
    return Feedback(
        iter_idx=int(d.get("iter_idx", 0)),
        summary=str(d.get("summary", "") or ""),
        strengths=list(d.get("strengths", []) or []),
        weaknesses=list(d.get("weaknesses", []) or []),
        next_iter_directives=list(d.get("next_iter_directives", []) or []),
    )


# ============ 持久化 API ============


def save_feedback(path: str, feedback: Feedback) -> None:
    """把 Feedback 单独写入 path (覆盖)

    格式:
      {
        "iter_idx": int,
        "summary": str,
        "strengths": [...],
        "weaknesses": [...],
        "next_iter_directives": [...]
      }
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = feedback_to_dict(feedback)
    with p.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_feedback(path: str) -> Feedback:
    """从 path 读取 Feedback; 文件不存在 → FileNotFoundError

    兼容两种格式:
      1. save_feedback 写的纯 feedback 格式 {"iter_idx": ..., ...}
      2. append_iteration 写的 history 格式 {"iterations": [...], "latest_feedback": {...}}
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"feedback file not found: {path}")
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"feedback file root must be dict, got {type(data).__name__}")
    # history 格式 → 取 latest_feedback
    if "latest_feedback" in data and isinstance(data["latest_feedback"], dict):
        return _feedback_from_dict(data["latest_feedback"])
    return _feedback_from_dict(data)


def append_iteration(path: str, record: IterationRecord, feedback: Feedback) -> None:
    """追加一次迭代到 history; 不存在则创建; 同时写 latest_feedback

    格式:
      {
        "iterations": [record_dict, ...],
        "latest_feedback": feedback_dict
      }
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            data = {}
    else:
        data = {}

    iterations = data.get("iterations", [])
    if not isinstance(iterations, list):
        iterations = []

    iterations.append(record_to_dict(record))
    data["iterations"] = iterations
    data["latest_feedback"] = feedback_to_dict(feedback)

    with p.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_history(path: str) -> list[IterationRecord]:
    """从 path 读取全部迭代记录; 按 iter_idx 升序; 文件不存在 → []"""
    p = Path(path)
    if not p.exists():
        return []
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    raw = data.get("iterations", [])
    if not isinstance(raw, list):
        return []
    records = [_record_from_dict(d) for d in raw if isinstance(d, dict)]
    records.sort(key=lambda r: r.iter_idx)
    return records


# ============ 核心 API: analyze_iteration ============


def analyze_iteration(record: IterationRecord) -> Feedback:
    """从一次迭代提取 Feedback

    流程:
      1. 算 panel_scores std → consensus (std < CONSENSUS_STD)
      2. strengths: panel_score >= STRENGTH_THRESHOLD 的 proposals
      3. weaknesses: panel_score < WEAKNESS_THRESHOLD 的 proposals
      4. next_iter_directives: 基于 weaknesses 生成指令
      5. summary: 拼装统计摘要

    Returns:
        Feedback
    """
    scores_raw = record.panel_scores or {}
    # 修 37: 强转 keys 为 int(从 JSON 加载后 keys 是 str)
    scores = {int(k): float(v) for k, v in scores_raw.items()}

    # consensus 计算
    score_values = list(scores.values())
    score_std = _std(score_values)
    consensus = score_std < CONSENSUS_STD and len(score_values) >= 2

    # strengths / weaknesses
    strengths: list[str] = []
    weaknesses: list[str] = []
    for pidx, score in sorted(scores.items(), key=lambda x: -x[1]):
        proposal_text = ""
        if 0 <= pidx < len(record.proposals):
            proposal_text = record.proposals[pidx]
        snippet = _truncate(proposal_text, max_len=60)
        if score >= STRENGTH_THRESHOLD:
            strengths.append(f"proposal[{pidx}] score={score:.1f} : {snippet}")
        elif score < WEAKNESS_THRESHOLD:
            weaknesses.append(f"proposal[{pidx}] score={score:.1f} : {snippet}")

    # next_iter_directives 基于 weaknesses + 选中的 proposal 表现
    directives: list[str] = []
    if not record.panel_scores:
        directives.append("Provide panel_scores to enable comparative analysis.")
    if weaknesses:
        directives.append(
            f"Improve weak proposals: {len(weaknesses)} below {WEAKNESS_THRESHOLD:.0f} score."
        )
    if not consensus and len(score_values) >= 2:
        directives.append(
            f"Reduce panel disagreement (std={score_std:.2f}); aim for std < {CONSENSUS_STD}."
        )
    if not record.convergent_ideas:
        directives.append("Identify more cross-proposal convergent themes to anchor direction.")
    if not record.conflicts_resolved:
        directives.append("Surface and resolve explicit conflicting options before selection.")

    # selected proposal 表现
    if record.selected_proposal_idx in scores:
        sel_score = scores[record.selected_proposal_idx]
        if sel_score < STRENGTH_THRESHOLD:
            directives.append(
                f"Selected proposal[{record.selected_proposal_idx}] score={sel_score:.1f} is below strength threshold; consider stronger alternatives."
            )

    # summary
    sel_score = scores.get(record.selected_proposal_idx, 0.0)
    summary = (
        f"Iter {record.iter_idx}: {len(record.proposals)} proposals, "
        f"selected={record.selected_proposal_idx} (score={sel_score:.1f}), "
        f"mean={_mean(score_values):.1f}, std={score_std:.2f}, "
        f"convergent={len(record.convergent_ideas)}, "
        f"conflicts_resolved={len(record.conflicts_resolved)}, "
        f"consensus={consensus}"
    )

    return Feedback(
        iter_idx=record.iter_idx,
        summary=summary,
        strengths=strengths,
        weaknesses=weaknesses,
        next_iter_directives=directives,
    )


# ============ 核心 API: format_next_iter_prompt ============


def format_next_iter_prompt(history_path: str) -> str:
    """读 history + latest_feedback, 拼装给下轮的 prompt

    格式:
      Previous iteration feedback: ...
      Strengths: ...
      Weaknesses: ...
      Directives for next iteration: ...
      Iterations summary: ...
    """
    p = Path(history_path)
    history = load_history(history_path)

    # latest_feedback 优先, 没有则从最后一次 record 重算
    latest: Feedback | None = None
    if p.exists():
        try:
            with p.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("latest_feedback"), dict):
                latest = _feedback_from_dict(data["latest_feedback"])
        except (json.JSONDecodeError, OSError, ValueError):
            latest = None
    if latest is None and history:
        latest = analyze_iteration(history[-1])

    lines: list[str] = []
    lines.append("Previous iteration feedback:")

    if latest is None:
        lines.append("  (no prior iteration history)")
        return "\n".join(lines)

    lines.append(f"  Summary: {latest.summary}")

    if latest.strengths:
        lines.append("  Strengths:")
        for s in latest.strengths:
            lines.append(f"    - {s}")
    else:
        lines.append("  Strengths: (none)")

    if latest.weaknesses:
        lines.append("  Weaknesses:")
        for w in latest.weaknesses:
            lines.append(f"    - {w}")
    else:
        lines.append("  Weaknesses: (none)")

    if latest.next_iter_directives:
        lines.append("  Directives for next iteration:")
        for d in latest.next_iter_directives:
            lines.append(f"    * {d}")
    else:
        lines.append("  Directives for next iteration: (none)")

    if history:
        lines.append("  Iterations summary:")
        for rec in history:
            top1 = _top1_score(rec)
            lines.append(
                f"    iter={rec.iter_idx} top_score={top1:.1f} "
                f"selected={rec.selected_proposal_idx} "
                f"convergent={len(rec.convergent_ideas)}"
            )

    return "\n".join(lines)


# ============ 核心 API: detect_convergence ============


def detect_convergence(
    history: list[IterationRecord],
    window: int = 3,
) -> dict:
    """检测迭代是否收敛 (top1 panel_score 稳定)

    逻辑:
      - 取最后 window 个 iter 的 top1 panel_score
      - 算 std
      - trend: 比较后半均值 vs 前半均值
        * 后 > 前 → "up"
        * 后 < 前 → "down"
        * 其它 → "stable"
      - converged: std < CONVERGENCE_STD 且 history 至少 window 个

    Returns:
        {
          "converged": bool,
          "std": float,
          "trend": "up" | "down" | "stable",
          "window": int,
          "samples": int,
          "top1_scores": [float, ...]
        }
    """
    window = max(window, 1)
    if not history:
        return {
            "converged": False,
            "std": 0.0,
            "trend": "stable",
            "window": window,
            "samples": 0,
            "top1_scores": [],
        }

    top1_list: list[float] = [_top1_score(r) for r in history[-window:]]
    score_std = _std(top1_list)
    converged = (len(history) >= window) and (score_std < CONVERGENCE_STD)

    # trend: split in half, compare mean(second half) - mean(first half)
    if len(top1_list) >= 2:
        mid = len(top1_list) // 2
        first_half = top1_list[:mid] if mid > 0 else top1_list[:1]
        second_half = top1_list[mid:] if mid > 0 else top1_list[1:]
        m1 = _mean(first_half)
        m2 = _mean(second_half)
        diff = m2 - m1
        if diff > 0.5:
            trend = "up"
        elif diff < -0.5:
            trend = "down"
        else:
            trend = "stable"
    else:
        trend = "stable"

    return {
        "converged": bool(converged),
        "std": round(score_std, 4),
        "trend": trend,
        "window": window,
        "samples": len(top1_list),
        "top1_scores": [round(v, 4) for v in top1_list],
    }


# ============ 核心 API: cross_iter_synthesize ============


def cross_iter_synthesize(
    prev: IterationRecord,
    curr: IterationRecord,
) -> dict:
    """跨迭代综合: convergence / best_of_each / recommended_adoption

    convergence:
      - 关键词 Jaccard(prev_keywords, curr_keywords) > JACCARD_CONVERGENCE_THRESHOLD
    best_of_each:
      - prev_best: top1 来自 prev
      - curr_best: top1 来自 curr
    recommended_adoption:
      - curr_top1 > prev_top1 * ADOPTION_RATIO → "curr"
      - prev_top1 > curr_top1 * ADOPTION_RATIO → "prev"
      - 其它 → "either" (差异 < 5%)

    Returns:
        {
          "convergence": bool,
          "jaccard": float,
          "prev_best": {"proposal_idx", "score", "text"},
          "curr_best": {"proposal_idx", "score", "text"},
          "recommended_adoption": "prev" | "curr" | "either",
          "score_delta": float
        }
    """
    prev_kws = sorted(_record_keyword_set(prev))
    curr_kws = sorted(_record_keyword_set(curr))
    j = _jaccard(prev_kws, curr_kws)
    convergence = j > JACCARD_CONVERGENCE_THRESHOLD

    # best of each
    def _best(record: IterationRecord) -> dict:
        if not record.panel_scores:
            return {"proposal_idx": -1, "score": 0.0, "text": ""}
        best_idx = max(record.panel_scores, key=lambda k: record.panel_scores[k])
        best_score = record.panel_scores[best_idx]
        text = ""
        if 0 <= best_idx < len(record.proposals):
            text = record.proposals[best_idx]
        return {
            "proposal_idx": int(best_idx),
            "score": round(float(best_score), 4),
            "text": _truncate(text, max_len=80),
        }

    prev_best = _best(prev)
    curr_best = _best(curr)

    # recommended_adoption
    ps = prev_best["score"]
    cs = curr_best["score"]
    delta = cs - ps
    if ps <= 0 and cs <= 0:
        adoption = "either"
    elif cs > ps * ADOPTION_RATIO:
        adoption = "curr"
    elif ps > cs * ADOPTION_RATIO:
        adoption = "prev"
    else:
        adoption = "either"

    return {
        "convergence": bool(convergence),
        "jaccard": round(j, 4),
        "prev_best": prev_best,
        "curr_best": curr_best,
        "recommended_adoption": adoption,
        "score_delta": round(delta, 4),
    }
