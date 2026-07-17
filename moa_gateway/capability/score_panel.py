"""5 维评分面板 (TQ/CO/AP/SE/IN) + multi-eval averaging (来自 opencode-moa)

真实实现,非 mock。所有评分基于规则启发式,实际可运行。

维度说明:
- TQ (Technical Quality): 技术质量 — 代码块/命令/数字密度
- CO (Completeness): 完整性 — 覆盖查询的所有子问题
- AP (Applicability): 实用性 — actionable 步骤
- SE (Specificity/Evidence): 具体性 — 数字/引用/URL
- IN (Insight): 洞察 — 转折词/稀有词
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field

__all__ = [
    "DimensionScore",
    "PanelScore",
    "score_panel",
    "score_technical_quality",
    "score_completeness",
    "score_applicability",
    "score_specificity",
    "score_insight",
    "multi_eval_average",
]


# ============ 默认权重 ============

DEFAULT_WEIGHTS: dict[str, float] = {
    "TQ": 0.25,
    "CO": 0.25,
    "AP": 0.20,
    "SE": 0.15,
    "IN": 0.15,
}


# ============ 启发式常量 ============

# 转折 / 强调关键词 (中英)
TRANSITION_WORDS = frozenset({
    "however", "actually", "but", "although", "though", "yet",
    "相反", "虽然", "但是", "然而", "事实上", "其实", "尽管",
})

INSIGHT_MARKERS = frozenset({
    "uniquely", "specifically", "particularly", "notably", "crucially",
    "especially", "remarkably", "surprisingly",
    "特别是", "尤其", "值得注意的是", "关键的是", "特别地",
})

ACTIONABLE_VERBS = frozenset({
    "you can", "you should", "you may", "try", "use", "run", "execute",
    "install", "configure", "set", "create", "add", "remove", "delete",
    "你可以", "可以尝试", "建议", "推荐", "使用", "运行", "执行",
    "安装", "配置", "设置", "创建", "添加", "删除",
})

STEP_PATTERNS = [
    r"\bstep\s*\d+\b",
    r"\bsteps?\s*[:\.\-]",
    r"^\s*\d+[\.\)、]\s+",
    r"^#+\s+\d+[\.\)、]",
    r"步骤\s*\d+",
    r"第[一二三四五六七八九十\d]+步",
    r"首先[，,。\s]+",
    r"然后[，,。\s]+",
    r"接着[，,。\s]+",
    r"最后[，,。\s]+",
]

CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```", re.MULTILINE)
INLINE_CODE_RE = re.compile(r"`[^`\n]{1,80}`")
URL_RE = re.compile(r"https?://[^\s\)\]\}\,;\"'<>]+", re.IGNORECASE)
CITATION_RE = re.compile(r"\[\d+\]|\[\w+(?:\s+\d+)?(?:,\s*\w+(?:\s+\d+)?)*\]")
YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
VERSION_RE = re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b")
PERCENT_RE = re.compile(r"\b\d+(?:\.\d+)?\s*%")
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

# 常见词 (用于"稀有词"启发式)
COMMON_WORDS = frozenset({
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
    "怎么", "为什么", "哪个", "哪些",
})


# ============ Dataclass 定义 ============

@dataclass
class DimensionScore:
    """单维度评分"""
    name: str        # "TQ" / "CO" / "AP" / "SE" / "IN"
    full_name: str   # "Technical Quality" 等
    score: float     # 0-100
    max_score: float = 100
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PanelScore:
    """5 维评分结果"""
    tq: DimensionScore  # Technical Quality
    co: DimensionScore  # Completeness
    ap: DimensionScore  # Applicability
    se: DimensionScore  # Specificity/Evidence
    in_: DimensionScore # Insight
    overall: float       # 加权平均
    weights: dict[str, float] = field(default_factory=dict)
    verdict: str = ""   # "excellent" / "good" / "fair" / "poor"
    feedback: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        # 将 in_ 键名规范化为 in
        d["in"] = d.pop("in_")
        return d


# ============ 辅助函数 ============

def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _verdict_from_score(overall: float) -> str:
    """根据 overall 判定 verdict 等级"""
    if overall >= 85:
        return "excellent"
    if overall >= 70:
        return "good"
    if overall >= 50:
        return "fair"
    return "poor"


def _tokenize_words(text: str) -> list[str]:
    """分词:英文按空格,中文按字符"""
    if not text:
        return []
    # 英文 + 数字块
    en_tokens = re.findall(r"[a-zA-Z][a-zA-Z'\-]*|\d+", text)
    # 单个中文字符也算 token
    zh_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return [t.lower() for t in en_tokens] + zh_chars


def _split_subqueries(query: str) -> list[str]:
    """把 query 拆成子问题列表"""
    if not query or not query.strip():
        return []
    text = query.strip()
    # 优先用 ? 拆分
    parts: list[str] = []
    if "?" in text:
        parts = [p.strip() for p in text.split("?") if p.strip()]
    elif "？" in text:
        parts = [p.strip() for p in text.split("？") if p.strip()]
    else:
        # 用句号/分号/逗号 + "and" 拆分
        raw = re.split(r"[。.!！;；]+|\s+and\s+|\s+or\s+", text)
        parts = [p.strip() for p in raw if p.strip() and len(p.strip()) > 2]
    # 去重但保持顺序
    seen = set()
    out = []
    for p in parts:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _subquery_keywords(subquery: str) -> list[str]:
    """从子问题中提取关键词"""
    tokens = _tokenize_words(subquery)
    # 过滤常见词和短词
    kws = [t for t in tokens if t not in COMMON_WORDS and len(t) >= 2]
    return kws


# ============ 5 维评分函数 ============

def score_technical_quality(answer: str) -> DimensionScore:
    """TQ: 真实启发式 — 字数 / 代码块 / 数字密度

    真实逻辑:
    - 长度 100-2000 字 → 高分
    - 含代码块 ```...``` → +20
    - 含数字 → +5 each (cap 30)
    """
    notes: list[str] = []
    if not answer:
        return DimensionScore(
            name="TQ", full_name="Technical Quality",
            score=0.0, notes=["empty answer"],
        )

    text = answer.strip()
    # 字数 (中英都按 1 字算)
    char_count = len(text)
    # 用空白分词得到英文词数
    len(re.findall(r"\S+", text))

    # 基础分:按字数
    if char_count < 30:
        base = 15.0
        notes.append(f"very short ({char_count} chars)")
    elif char_count < 100:
        base = 35.0
        notes.append(f"short ({char_count} chars)")
    elif char_count <= 2000:
        base = 60.0
        notes.append(f"good length ({char_count} chars)")
    elif char_count <= 5000:
        base = 70.0
        notes.append(f"long ({char_count} chars)")
    else:
        base = 65.0
        notes.append(f"very long ({char_count} chars) — minor risk of verbosity")

    # 代码块加分
    code_blocks = CODE_BLOCK_RE.findall(text)
    inline_codes = INLINE_CODE_RE.findall(text)
    code_bonus = 0.0
    if code_blocks:
        code_bonus += 20.0
        notes.append(f"{len(code_blocks)} code block(s) +20")
    if inline_codes:
        inline_bonus = min(10.0, 3.0 * len(inline_codes))
        code_bonus += inline_bonus
        notes.append(f"{len(inline_codes)} inline code +{inline_bonus:.0f}")

    # 数字加分 (cap 30)
    numbers = NUMBER_RE.findall(text)
    num_bonus = 0.0
    if numbers:
        num_bonus = min(30.0, 5.0 * len(numbers))
        notes.append(f"{len(numbers)} numbers +{num_bonus:.0f}")

    # 句子结构 (按句号/换行分段)
    sentences = re.split(r"[。.!！?\n]+", text)
    sentences = [s for s in sentences if s.strip()]
    structure_bonus = 0.0
    if 3 <= len(sentences) <= 50:
        structure_bonus = 5.0
        notes.append(f"{len(sentences)} sentences +5")

    final = _clip(base + code_bonus + num_bonus + structure_bonus)
    return DimensionScore(
        name="TQ", full_name="Technical Quality",
        score=round(final, 2), notes=notes,
    )


def score_completeness(query: str, answer: str) -> DimensionScore:
    """CO: 真实启发式 — 查询子问题数 vs 答案覆盖

    真实逻辑:
    - 拆分 query 为子问题(用 ? 句号 等)
    - 检查 answer 是否每个子问题都有回答
    """
    notes: list[str] = []
    if not answer or not answer.strip():
        return DimensionScore(
            name="CO", full_name="Completeness",
            score=0.0, notes=["empty answer"],
        )
    if not query or not query.strip():
        # 没有 query 时按答案长度给一个基础分
        char_count = len(answer.strip())
        base = 50.0 if char_count >= 100 else 25.0
        return DimensionScore(
            name="CO", full_name="Completeness",
            score=base, notes=["no query provided"],
        )

    subqueries = _split_subqueries(query)
    if not subqueries:
        # 拆不出子问题,按长度给基础分
        char_count = len(answer.strip())
        base = 55.0 if char_count >= 100 else 30.0
        return DimensionScore(
            name="CO", full_name="Completeness",
            score=base, notes=["no decomposable sub-queries"],
        )

    answer_lower = answer.lower()
    covered = 0
    coverage_details: list[str] = []
    for sq in subqueries:
        kws = _subquery_keywords(sq)
        if not kws:
            covered += 1
            coverage_details.append(f"'{sq[:30]}': no keywords, mark covered")
            continue
        # 至少 30% 关键词出现在 answer 中
        hits = sum(1 for k in kws if k in answer_lower)
        ratio = hits / len(kws) if kws else 0
        if ratio >= 0.3:
            covered += 1
            coverage_details.append(f"'{sq[:30]}': {hits}/{len(kws)} kw")
        else:
            coverage_details.append(f"'{sq[:30]}': {hits}/{len(kws)} kw MISS")

    coverage_ratio = covered / len(subqueries)
    notes.append(f"covered {covered}/{len(subqueries)} sub-queries")
    for d in coverage_details[:5]:
        notes.append(d)

    # 基础分
    base_score = coverage_ratio * 100.0
    # 答案长度 bonus (长答案更可能完整)
    char_count = len(answer.strip())
    length_bonus = 0.0
    if char_count >= 200:
        length_bonus = 5.0
    elif char_count >= 100:
        length_bonus = 2.0

    final = _clip(base_score + length_bonus)
    return DimensionScore(
        name="CO", full_name="Completeness",
        score=round(final, 2), notes=notes,
    )


def score_applicability(answer: str) -> DimensionScore:
    """AP: 真实启发式 — actionable 关键词 + step-by-step 结构

    真实逻辑:
    - 含 "step 1" / "步骤" / "1." → 高分
    - 含 "you can" / "try" / "use" → +20
    """
    notes: list[str] = []
    if not answer or not answer.strip():
        return DimensionScore(
            name="AP", full_name="Applicability",
            score=0.0, notes=["empty answer"],
        )

    text = answer.strip()
    text_lower = text.lower()

    # 基础分:长度
    char_count = len(text)
    if char_count < 50:
        base = 20.0
        notes.append(f"very short ({char_count} chars)")
    elif char_count < 150:
        base = 40.0
    elif char_count <= 3000:
        base = 55.0
        notes.append("adequate length")
    else:
        base = 60.0

    # 步骤结构检测
    step_hits = 0
    step_patterns_found: list[str] = []
    for pat in STEP_PATTERNS:
        matches = re.findall(pat, text, re.IGNORECASE | re.MULTILINE)
        if matches:
            step_hits += len(matches)
            step_patterns_found.append(pat[:20])

    step_bonus = 0.0
    if step_hits >= 3:
        step_bonus = 25.0
        notes.append(f"strong step structure ({step_hits} markers) +25")
    elif step_hits >= 1:
        step_bonus = 12.0
        notes.append(f"some step structure ({step_hits} markers) +12")
    else:
        notes.append("no step structure detected")

    # actionable 动词
    verb_hits = 0
    verbs_found: list[str] = []
    for verb in ACTIONABLE_VERBS:
        if verb in text_lower:
            verb_hits += 1
            verbs_found.append(verb)
    verb_bonus = 0.0
    if verb_hits >= 4:
        verb_bonus = 20.0
        notes.append(f"strong actionable ({verb_hits} verbs) +20")
    elif verb_hits >= 1:
        verb_bonus = min(15.0, 5.0 * verb_hits)
        notes.append(f"actionable ({verb_hits} verbs) +{verb_bonus:.0f}")
    else:
        notes.append("no actionable verbs")

    # 命令/代码示例
    cmd_bonus = 0.0
    if CODE_BLOCK_RE.search(text):
        cmd_bonus = 5.0
        notes.append("has code block +5")

    final = _clip(base + step_bonus + verb_bonus + cmd_bonus)
    return DimensionScore(
        name="AP", full_name="Applicability",
        score=round(final, 2), notes=notes,
    )


def score_specificity(answer: str) -> DimensionScore:
    """SE: 真实启发式 — 具体证据

    真实逻辑:
    - URL 计数 → +10 each
    - 数字(年份/版本/百分比) → +5 each
    - 引用 [...] → +5
    """
    notes: list[str] = []
    if not answer or not answer.strip():
        return DimensionScore(
            name="SE", full_name="Specificity",
            score=0.0, notes=["empty answer"],
        )

    text = answer.strip()
    char_count = len(text)

    # 长度基础分
    if char_count < 50:
        base = 15.0
    elif char_count < 200:
        base = 35.0
    else:
        base = 45.0

    # URL
    urls = URL_RE.findall(text)
    url_bonus = 0.0
    if urls:
        url_bonus = min(30.0, 10.0 * len(urls))
        notes.append(f"{len(urls)} URL(s) +{url_bonus:.0f}")

    # 年份
    years = YEAR_RE.findall(text)
    year_bonus = 0.0
    if years:
        year_bonus = min(15.0, 5.0 * len(years))
        notes.append(f"{len(years)} year(s) +{year_bonus:.0f}")

    # 版本号
    versions = VERSION_RE.findall(text)
    version_bonus = 0.0
    if versions:
        version_bonus = min(15.0, 3.0 * len(versions))
        notes.append(f"{len(versions)} version(s) +{version_bonus:.0f}")

    # 百分比
    percents = PERCENT_RE.findall(text)
    percent_bonus = 0.0
    if percents:
        percent_bonus = min(10.0, 5.0 * len(percents))
        notes.append(f"{len(percents)} percent(s) +{percent_bonus:.0f}")

    # 引用
    citations = CITATION_RE.findall(text)
    cite_bonus = 0.0
    if citations:
        cite_bonus = min(15.0, 5.0 * len(citations))
        notes.append(f"{len(citations)} citation(s) +{cite_bonus:.0f}")

    final = _clip(base + url_bonus + year_bonus + version_bonus + percent_bonus + cite_bonus)
    return DimensionScore(
        name="SE", full_name="Specificity",
        score=round(final, 2), notes=notes,
    )


def score_insight(query: str, answer: str) -> DimensionScore:
    """IN: 真实启发式 — 独特洞察

    真实逻辑:
    - 包含"however" / "actually" / "but" / "虽然" 等转折 → +15
    - 包含"uniquely" / "specifically" / "特别是" → +15
    - 长度 / 复杂度(稀有词) → +5
    """
    notes: list[str] = []
    if not answer or not answer.strip():
        return DimensionScore(
            name="IN", full_name="Insight",
            score=0.0, notes=["empty answer"],
        )

    text = answer.strip()
    text_lower = text.lower()
    char_count = len(text)

    # 基础分:长度
    if char_count < 80:
        base = 20.0
        notes.append("very short")
    elif char_count < 300:
        base = 45.0
    else:
        base = 55.0
        notes.append("substantial length")

    # 转折词
    transition_hits = []
    for w in TRANSITION_WORDS:
        if w in text_lower:
            transition_hits.append(w)
    transition_bonus = 0.0
    if len(transition_hits) >= 3:
        transition_bonus = 20.0
    elif len(transition_hits) >= 1:
        transition_bonus = 15.0
    if transition_hits:
        notes.append(f"transitions ({', '.join(transition_hits[:3])}) +{transition_bonus:.0f}")

    # 洞察标记
    insight_hits = []
    for w in INSIGHT_MARKERS:
        if w in text_lower:
            insight_hits.append(w)
    insight_bonus = 0.0
    if len(insight_hits) >= 2:
        insight_bonus = 18.0
    elif len(insight_hits) >= 1:
        insight_bonus = 12.0
    if insight_hits:
        notes.append(f"insight markers ({', '.join(insight_hits[:3])}) +{insight_bonus:.0f}")

    # 稀有词 (vocabulary diversity)
    tokens = _tokenize_words(text)
    if tokens:
        token_counts = Counter(tokens)
        # 稀有词 = 总词数 - 常见词数
        rare_count = sum(c for t, c in token_counts.items() if t not in COMMON_WORDS)
        rare_ratio = rare_count / len(tokens)
        rare_bonus = 0.0
        if rare_ratio >= 0.4:
            rare_bonus = 10.0
        elif rare_ratio >= 0.25:
            rare_bonus = 5.0
        if rare_bonus > 0:
            notes.append(f"vocab diversity {rare_ratio:.2f} +{rare_bonus:.0f}")
    else:
        rare_bonus = 0.0

    # 查询关键词回引 (说明 answer 在和 query 主题相关)
    query_feedback_bonus = 0.0
    if query and query.strip():
        qkws = [k for k in _subquery_keywords(query) if len(k) >= 3]
        if qkws:
            hits = sum(1 for k in qkws if k in text_lower)
            ratio = hits / len(qkws)
            if ratio >= 0.5:
                query_feedback_bonus = 5.0
                notes.append(f"query keywords reflected ({hits}/{len(qkws)}) +5")

    final = _clip(base + transition_bonus + insight_bonus + rare_bonus + query_feedback_bonus)
    return DimensionScore(
        name="IN", full_name="Insight",
        score=round(final, 2), notes=notes,
    )


# ============ 主评分函数 ============

def score_panel(
    query: str,
    answer: str,
    rubric: dict[str, float] | None = None,
) -> PanelScore:
    """5 维评分主函数

    Args:
        query: 用户查询
        answer: 待评分的答案
        rubric: 自定义权重,如 {"TQ": 0.3, "CO": 0.3, ...}

    Returns:
        PanelScore: 5 维评分结果
    """
    weights = dict(rubric) if rubric else dict(DEFAULT_WEIGHTS)
    # 归一化权重
    total_w = sum(weights.values())
    if total_w > 0:
        weights = {k: v / total_w for k, v in weights.items()}
    else:
        weights = dict(DEFAULT_WEIGHTS)

    tq = score_technical_quality(answer)
    co = score_completeness(query, answer)
    ap = score_applicability(answer)
    se = score_specificity(answer)
    in_ = score_insight(query, answer)

    overall = (
        weights.get("TQ", 0) * tq.score
        + weights.get("CO", 0) * co.score
        + weights.get("AP", 0) * ap.score
        + weights.get("SE", 0) * se.score
        + weights.get("IN", 0) * in_.score
    )
    overall = _clip(overall)

    verdict = _verdict_from_score(overall)

    # 生成 feedback
    feedback: list[str] = []
    dim_scores = [
        ("TQ", "Technical Quality", tq.score),
        ("CO", "Completeness", co.score),
        ("AP", "Applicability", ap.score),
        ("SE", "Specificity", se.score),
        ("IN", "Insight", in_.score),
    ]
    for code, full, s in dim_scores:
        if s < 50:
            feedback.append(f"{code} ({full}) is weak ({s:.1f}) — consider improving")
        elif s >= 85:
            feedback.append(f"{code} ({full}) is strong ({s:.1f})")
    if verdict == "excellent":
        feedback.append("Overall: excellent answer")
    elif verdict == "poor":
        feedback.append("Overall: poor — significant improvements needed")
    elif verdict == "fair":
        feedback.append("Overall: fair — acceptable but improvable")

    return PanelScore(
        tq=tq, co=co, ap=ap, se=se, in_=in_,
        overall=round(overall, 2),
        weights={k: round(v, 4) for k, v in weights.items()},
        verdict=verdict,
        feedback=feedback,
    )


# ============ Multi-eval averaging ============

def multi_eval_average(scores: list[PanelScore]) -> PanelScore:
    """多次评分取平均(multi-eval averaging)

    真实逻辑:同维度取均值,verdict 重新计算
    """
    if not scores:
        raise ValueError("scores must be non-empty")
    if len(scores) == 1:
        return scores[0]

    n = len(scores)

    def avg_dim(dim_getter) -> DimensionScore:
        first = dim_getter(scores[0])
        all_scores = [dim_getter(s).score for s in scores]
        all_notes_lists = [dim_getter(s).notes for s in scores]
        avg_score = sum(all_scores) / n
        # 合并 notes (去重)
        merged_notes: list[str] = []
        seen = set()
        for notes in all_notes_lists:
            for note in notes:
                if note not in seen:
                    seen.add(note)
                    merged_notes.append(note)
        return DimensionScore(
            name=first.name, full_name=first.full_name,
            score=round(_clip(avg_score), 2),
            notes=merged_notes,
        )

    tq = avg_dim(lambda s: s.tq)
    co = avg_dim(lambda s: s.co)
    ap = avg_dim(lambda s: s.ap)
    se = avg_dim(lambda s: s.se)
    in_ = avg_dim(lambda s: s.in_)

    # 合并权重(取平均)
    all_keys = set()
    for s in scores:
        all_keys.update(s.weights.keys())
    merged_weights: dict[str, float] = {}
    for k in all_keys:
        vals = [s.weights.get(k, 0.0) for s in scores]
        merged_weights[k] = sum(vals) / n
    # 重新归一化
    w_total = sum(merged_weights.values())
    if w_total > 0:
        merged_weights = {k: v / w_total for k, v in merged_weights.items()}

    overall = (
        merged_weights.get("TQ", 0) * tq.score
        + merged_weights.get("CO", 0) * co.score
        + merged_weights.get("AP", 0) * ap.score
        + merged_weights.get("SE", 0) * se.score
        + merged_weights.get("IN", 0) * in_.score
    )
    overall = _clip(overall)
    verdict = _verdict_from_score(overall)

    # 合并 feedback
    merged_feedback: list[str] = []
    seen = set()
    for s in scores:
        for fb in s.feedback:
            if fb not in seen:
                seen.add(fb)
                merged_feedback.append(fb)
    merged_feedback.append(f"Averaged over {n} evaluations")

    return PanelScore(
        tq=tq, co=co, ap=ap, se=se, in_=in_,
        overall=round(overall, 2),
        weights={k: round(v, 4) for k, v in merged_weights.items()},
        verdict=verdict,
        feedback=merged_feedback,
    )
