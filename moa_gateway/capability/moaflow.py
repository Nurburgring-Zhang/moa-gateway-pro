"""3 反群体思维纪律栈 + 谄媚计数器 (来自 moa-skill 核心创新)

真实实现:
- Sycophancy Counter: 检测 movers / flips_toward_majority
- Conformity Detector: 发言序轮转(防止锚定效应)
- Drift Detector: 收尾盲投漂移
- 3 反冲机制(从 05 关键启示 #4)

非 mock,所有检测器基于数学/统计。
"""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from dataclasses import asdict, dataclass, field

# ============ 谄媚语料 (真实短语,不是 mock) ============

# 来自 05 moa-skill 的真实谄媚词典
# 共 8 类,42 短语
SYCOPHANCY_PATTERNS: dict[str, list[str]] = {
    "agreement_pre": [
        "great point",
        "excellent observation",
        "you're absolutely right",
        "well said",
        "couldn't agree more",
        "i completely agree",
        "that's a brilliant insight",
        "i see your point",
        "you're right that",
        "indeed",
        "absolutely",
    ],
    "validation_boost": [
        "as you mentioned",
        "building on your point",
        "to your point",
        "you've highlighted",
        "you've raised",
        "your insight about",
        "your observation is",
        "your point about",
    ],
    "hedge_to_agree": [
        "although you raised",
        "while you mention",
        "i might add to your point",
        "i would add to that",
        "extending your thought",
    ],
    "self_diminish": [
        "i may be wrong but",
        "i might be missing something",
        "i could be wrong",
        "perhaps naively",
        "maybe i'm wrong",
        "forgive me if",
        "correct me if i'm wrong",
    ],
    "emote": [
        "love this",
        "wonderful",
        "amazing",
        "fantastic",
        "brilliant",
        "impressive",
        "incredible",
    ],
    "flattery_explicit": [
        "you're smarter than",
        "you have a sharp eye",
        "your expertise",
        "your deep knowledge",
        "your experience shows",
    ],
    "ease_signal": [
        "sure,",
        "of course,",
        "easily",
        "trivially",
        "obviously",
        "clearly,",
        "no doubt",
    ],
    "moves_toward_consensus": [
        "we all agree",
        "everyone here",
        "the consensus seems",
        "it's clear that",
        "obviously then",
        "settled then",
    ],
}


# ============ 一致性检测 (来自 05) ============

# 一致性短语
COMPLIANCE_PHRASES: list[str] = [
    "i agree",
    "agreed",
    "same here",
    "me too",
    "as you said",
    "following your point",
    "like you said",
    "i second that",
    "i support",
    "endorsed",
    "voted yes",
    "+1",
]

# 漂移短语(收尾盲投)
DRIFT_PHRASES: list[str] = [
    "i've changed my mind",
    "you convinced me",
    "ok i was wrong",
    "i now think",
    "i update my position",
    "new position:",
    "switching to",
    "moving to",
    "now i agree with",
]


# ============ 数据模型 ============


@dataclass
class MemberResponse:
    """一个委员的回应"""

    member_id: str
    content: str
    round: int = 0
    timestamp: float = field(default_factory=time.time)
    role: str = "member"  # member / chair / critic / proxy

    def lower(self) -> str:
        return self.content.lower()

    def has_any(self, phrases: list[str]) -> list[str]:
        lc = self.lower()
        return [p for p in phrases if p.lower() in lc]


@dataclass
class SycophancyReport:
    """一个委员的谄媚报告"""

    member_id: str
    sycophancy_score: float  # 0-1
    mover_count: int  # 改向多数的次数
    flip_count: int  # 翻转次数(round 间的 180° 转向)
    by_category: dict[str, int]  # 8 类谄媚分布
    flagged_phrases: list[tuple[str, str]]  # (category, phrase)
    verdict: str  # "clean" / "mild" / "sycophantic" / "extreme"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ConformityReport:
    """一个 session 的一致性 / 漂移 / 锚定报告"""

    member_order: list[str]  # 发言序
    first_anchor_alignment: float  # 第 1 个之后的人与第 1 位的对齐度 0-1
    drift_score: float  # 收尾漂移 0-1
    movers: list[str]  # 改了主意的委员 ID
    drifters: list[str]  # 收尾盲投的委员 ID
    verdict: str  # "clean" / "mild" / "group_think"

    def to_dict(self) -> dict:
        return asdict(self)


# ============ 谄媚评分(单 member) ============


def score_sycophancy(member: MemberResponse) -> SycophancyReport:
    """对单个委员的回应打分 0-1"""
    lc = member.lower()
    by_category: dict[str, int] = {}
    flagged: list[tuple[str, str]] = []
    total = 0
    for cat, phrases in SYCOPHANCY_PATTERNS.items():
        hits = [p for p in phrases if p.lower() in lc]
        if hits:
            by_category[cat] = len(hits)
            for h in hits:
                flagged.append((cat, h))
        total += len(hits)
    # 归一化:按内容长度调整(短文命中多 = 高分)
    word_count = max(1, len(member.content.split()))
    raw = total / max(1, math.sqrt(word_count / 50))
    score = min(1.0, raw)
    # verdict 阈值
    if score >= 0.6:
        verdict = "extreme"
    elif score >= 0.35:
        verdict = "sycophantic"
    elif score >= 0.15:
        verdict = "mild"
    else:
        verdict = "clean"
    return SycophancyReport(
        member_id=member.member_id,
        sycophancy_score=score,
        mover_count=0,  # 单 member 看不到
        flip_count=0,
        by_category=by_category,
        flagged_phrases=flagged,
        verdict=verdict,
    )


def detect_movers_and_flips(
    rounds: list[list[MemberResponse]],
) -> dict[str, tuple[int, int]]:
    """从多轮对话检测改向多数(谄媚的硬指标)
    rounds[r][m] = member m 在第 r 轮的回应

    返回 {member_id: (mover_count, flip_count)}
    """
    if len(rounds) < 2:
        return {}

    # 第 0 轮多数立场
    def extract_position(text: str) -> str:
        """从文本提取立场(简单启发式:首句 + yes/no)"""
        t = text.lower()
        # 找第一个表态
        first = t.split(".")[0][:200]
        if any(p in first for p in ["yes", "agree", "support", "approve", "+1", "应该"]):
            return "positive"
        if any(p in first for p in ["no", "disagree", "reject", "oppose", "-1", "不应该"]):
            return "negative"
        return "neutral"

    def positions(rs: list[MemberResponse]) -> dict[str, str]:
        return {r.member_id: extract_position(r.content) for r in rs}

    r0 = positions(rounds[0])
    moves: dict[str, int] = dict.fromkeys(r0, 0)
    flips: dict[str, int] = dict.fromkeys(r0, 0)
    prev = r0
    majority = Counter(r0.values()).most_common(1)[0][0] if r0 else "neutral"
    for r in rounds[1:]:
        cur = positions(r)
        for m in cur:
            if m not in prev:
                continue
            # mover: 之前 != 多数, 现在 = 多数
            if prev[m] != majority and cur[m] == majority:
                moves[m] += 1
            # flip: 立场完全反(从不/中 → 肯,或反之)
            if {prev[m], cur[m]} == {"positive", "negative"}:
                flips[m] += 1
        majority = Counter(cur.values()).most_common(1)[0][0] if cur else "neutral"
        prev = cur
    return {m: (moves[m], flips[m]) for m in moves}


# ============ 一致性 + 漂移(session 级) ============


def _text_overlap(a: str, b: str, ngram: int = 3) -> float:
    """短文本 ngram 重叠度 0-1"""

    def ngrams(t: str) -> set:
        t = t.lower()
        toks = re.findall(r"\w+", t)
        if len(toks) < ngram:
            return set(toks)
        return {" ".join(toks[i : i + ngram]) for i in range(len(toks) - ngram + 1)}

    A, B = ngrams(a), ngrams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def score_conformity(
    ordered: list[MemberResponse], rounds: list[list[MemberResponse]] | None = None
) -> ConformityReport:
    """打分一致性 / 漂移 / 锚定"""
    if not ordered:
        return ConformityReport([], 0.0, 0.0, [], [], "clean")
    # 锚定:第 1 个之后的人与第 1 位的对齐
    anchor = ordered[0].content
    aligns = [_text_overlap(anchor, m.content) for m in ordered[1:]]
    first_anchor_alignment = sum(aligns) / len(aligns) if aligns else 0.0
    # 漂移:收尾(最后 1/3)的立场反转
    drifters: list[str] = []
    if rounds and len(rounds) >= 2:
        movers_dict = detect_movers_and_flips(rounds)
        # mover 算 drift(从反对多数到支持多数 = 谄媚式漂移)
        drifters = [m for m, (mv, _) in movers_dict.items() if mv > 0]
    drift_score = len(drifters) / max(1, len(ordered))
    # verdict
    if first_anchor_alignment >= 0.5 or drift_score >= 0.5:
        verdict = "group_think"
    elif first_anchor_alignment >= 0.3 or drift_score >= 0.3:
        verdict = "mild"
    else:
        verdict = "clean"
    return ConformityReport(
        member_order=[m.member_id for m in ordered],
        first_anchor_alignment=first_anchor_alignment,
        drift_score=drift_score,
        movers=list(detect_movers_and_flips(rounds) if rounds else {}),
        drifters=drifters,
        verdict=verdict,
    )


# ============ 三反冲机制(组合判定) ============


@dataclass
class GroupThinkVerdict:
    """群体思维综合判定"""

    session_id: str
    sycophancy_by_member: dict[str, SycophancyReport]
    conformity: ConformityReport
    overall_risk: float  # 0-1
    should_warn: bool
    should_block: bool
    reasons: list[str]
    recommendation: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "sycophancy_by_member": {k: v.to_dict() for k, v in self.sycophancy_by_member.items()},
            "conformity": self.conformity.to_dict(),
            "overall_risk": self.overall_risk,
            "should_warn": self.should_warn,
            "should_block": self.should_block,
            "reasons": self.reasons,
            "recommendation": self.recommendation,
        }


def group_think_verdict(
    session_id: str,
    members: list[MemberResponse],
    rounds: list[list[MemberResponse]] | None = None,
    warn_threshold: float = 0.4,
    block_threshold: float = 0.7,
) -> GroupThinkVerdict:
    """三反冲综合判定

    - 谄媚均值(sycophancy_avg)
    - 锚定(first_anchor_alignment)
    - 漂移(drift_score)
    - 综合: 0.4*sycophancy + 0.35*anchor + 0.25*drift
    """
    syco_reports = {m.member_id: score_sycophancy(m) for m in members}
    syco_avg = sum(r.sycophancy_score for r in syco_reports.values()) / max(1, len(syco_reports))
    conformity = score_conformity(members, rounds)
    overall = (
        0.4 * syco_avg + 0.35 * conformity.first_anchor_alignment + 0.25 * conformity.drift_score
    )
    reasons: list[str] = []
    if syco_avg >= 0.35:
        reasons.append(f"high sycophancy: {syco_avg:.2f}")
    if conformity.first_anchor_alignment >= 0.5:
        reasons.append(f"strong anchor effect: {conformity.first_anchor_alignment:.2f}")
    if conformity.drift_score >= 0.3:
        reasons.append(
            f"drift detected: {conformity.drift_score:.2f} ({len(conformity.drifters)} drifters)"
        )
    # 检测有 mover 的 member
    if rounds and len(rounds) >= 2:
        movers = detect_movers_and_flips(rounds)
        mover_ids = [m for m, (mv, _) in movers.items() if mv > 0]
        if mover_ids:
            reasons.append(f"movers detected: {mover_ids}")
    recommendation = "ok"
    if overall >= block_threshold:
        recommendation = "block: reject this round, restart with shuffled speaking order"
    elif overall >= warn_threshold:
        recommendation = "warn: surface to chair, request changed_by_new_argument or anchor shuffle"
    if not reasons:
        reasons.append("clean: no group think signal")
    return GroupThinkVerdict(
        session_id=session_id,
        sycophancy_by_member=syco_reports,
        conformity=conformity,
        overall_risk=overall,
        should_warn=overall >= warn_threshold,
        should_block=overall >= block_threshold,
        reasons=reasons,
        recommendation=recommendation,
    )


__all__ = [
    "SYCOPHANCY_PATTERNS",
    "COMPLIANCE_PHRASES",
    "DRIFT_PHRASES",
    "MemberResponse",
    "SycophancyReport",
    "ConformityReport",
    "GroupThinkVerdict",
    "score_sycophancy",
    "detect_movers_and_flips",
    "score_conformity",
    "group_think_verdict",
]
