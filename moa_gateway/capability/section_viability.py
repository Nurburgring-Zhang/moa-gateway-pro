"""Per-section viability 实证校验 + AP 评分 (来自 opencode-moa)

真实实现,非 mock。基于规则启发式评估 proposal 的每个 section 是否可执行,
并聚合为整篇 proposal 的 AP (Action Plan) 评分 0-10。

核心概念:
- Section: proposal 切分后的一个段落(由 ## 标题 / 1. 编号 / 200 词兜底)
- SectionVerdict: 单个 section 的 viability 判定 (0-1 score + reasons + blockers)
- ProposalReport: 整篇 proposal 的聚合报告 (含 AP 0-10)
- AP 评分:
  * AP=10  所有 section viable
  * AP=5-7 至少 1 个 section viable(部分)
  * AP=2-4 所有 section 失败但有 section 存在(无 viable)
  * AP=1   无任何 section(空)

使用场景:
- MoA 验证阶段:每篇 proposal 内部自检,定位具体哪一段不可执行
- 跨 proposal 比较:挑选 AP 最高的 proposal
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

__all__ = [
    "Section",
    "SectionVerdict",
    "ProposalReport",
    "split_into_sections",
    "evaluate_section",
    "compute_ap_score",
    "validate_proposal",
    "compare_proposals",
]


# ============ 启发式常量 ============

# imperative 动词 (英文 + 中文)
IMPERATIVE_VERBS = frozenset({
    "should", "must", "will", "shall", "need", "needs", "required", "require",
    "ought",
    "应", "应该", "必须", "需", "需要", "要", "将要", "应当",
})

# cite / ref 关键词
CITE_PATTERNS = [
    re.compile(r"\[\d+\]"),                         # [1]
    re.compile(r"\[\w+(?:\s+\d+)?(?:,\s*\w+(?:\s+\d+)?)*\]"),  # [Smith 2020, Lee 2019]
    re.compile(r"\[\d+[:\-,]\d+\]"),                # [1-3] / [1,2]
    re.compile(r"https?://[^\s\)\]\}\,;\"'<>]+", re.IGNORECASE),  # URL
    re.compile(r"\bref(?:erence)?s?\b", re.IGNORECASE),
    re.compile(r"\bsee\s+(?:also\s+)?[A-Z]"),
    re.compile(r"\bsource[s]?:\s*\S+", re.IGNORECASE),
    re.compile(r"\bbibliography\b", re.IGNORECASE),
    re.compile(r"参考文献", re.IGNORECASE),
    re.compile(r"引用", re.IGNORECASE),
    re.compile(r"来源[:：]"),
]

# 数字 regex(年份/版本/百分比/普通数字)
NUMBER_RE = re.compile(r"\b\d+(?:\.\d+)?\b")

# Markdown ## 标题
MD_H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
# Markdown # 标题(也支持)
MD_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
# numbered 1. 2. 3. 顶部(top-level)
NUMBERED_RE = re.compile(r"^(?:^|\n)\s*(\d+)\.\s+([^\n]+)", re.MULTILINE)

# 段落长度边界
MIN_WORDS = 20
MAX_WORDS = 800
FALLBACK_WORDS = 200


# ============ Dataclass 定义 ============

@dataclass
class Section:
    """一个 section(段落)"""
    section_idx: int
    title: str
    text: str
    word_count: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SectionVerdict:
    """单个 section 的 viability 判定"""
    section_idx: int
    viable: bool
    score: float            # 0-1
    reasons: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ProposalReport:
    """整篇 proposal 的 viability 报告"""
    proposal_idx: int
    total_sections: int
    viable_sections: int
    failing_sections: list[int] = field(default_factory=list)
    ap_score: int = 0
    verdicts: list[SectionVerdict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdicts"] = [v.to_dict() for v in self.verdicts]
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ============ 辅助函数 ============

def _clip01(x: float) -> float:
    """clip 到 [0, 1]"""
    return max(0.0, min(1.0, x))


def _word_count(text: str) -> int:
    """分词数:英文按空格/标点分,中文按字符"""
    if not text:
        return 0
    # 英文 + 数字块
    en_tokens = re.findall(r"[a-zA-Z][a-zA-Z'\-]*|\d+", text)
    # 中文字符每个算 1
    zh_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return len(en_tokens) + len(zh_chars)


def _has_imperative(text: str) -> bool:
    """是否含 imperative 动词(should/must/will 等)"""
    if not text:
        return False
    text_lower = text.lower()
    for verb in IMPERATIVE_VERBS:
        # 中文动词直接 substring 即可,英文用 word-boundary
        if re.match(r"[\u4e00-\u9fff]", verb):
            if verb in text:
                return True
        elif re.search(rf"\b{re.escape(verb)}\b", text_lower):
            return True
    return False


def _has_number(text: str) -> bool:
    """是否含数字"""
    if not text:
        return False
    return bool(NUMBER_RE.search(text))


def _has_cite(text: str) -> bool:
    """是否含引用 / ref / URL"""
    if not text:
        return False
    return any(pat.search(text) for pat in CITE_PATTERNS)


# ============ Proposal 切分 ============

def split_into_sections(text: str) -> list[Section]:
    """把 proposal 文本切分为 sections。

    切分规则(优先级从高到低):
    1. Markdown `## ` / `# ` 标题
    2. 编号 `1. ` `2. ` 顶部标题
    3. 兜底:每 ~200 词一段

    Args:
        text: proposal 全文

    Returns:
        List[Section]: section 列表
    """
    if not text or not text.strip():
        return []

    # 优先尝试 Markdown 标题切分
    md_sections = _split_by_md_headers(text)
    if len(md_sections) >= 2:
        return _build_sections(md_sections)

    # 其次尝试 numbered
    numbered_sections = _split_by_numbered(text)
    if len(numbered_sections) >= 2:
        return _build_sections(numbered_sections)

    # 兜底:每 200 词一段
    fallback = _split_by_word_count(text, FALLBACK_WORDS)
    return _build_sections(fallback)


def _split_by_md_headers(text: str) -> list[str]:
    """按 Markdown ## / # 标题切分(保留标题在前一段)"""
    if not text:
        return []

    # 找所有 ## 位置
    h2_matches = list(MD_H2_RE.finditer(text))
    h1_matches = list(MD_H1_RE.finditer(text))

    # 优先用 ## (更细)
    if h2_matches and len(h2_matches) >= 2:
        splits: list[str] = []
        for i, m in enumerate(h2_matches):
            start = m.start()
            end = h2_matches[i + 1].start() if i + 1 < len(h2_matches) else len(text)
            chunk = text[start:end].strip()
            if chunk:
                splits.append(chunk)
        return splits

    if h1_matches and len(h1_matches) >= 2:
        splits = []
        for i, m in enumerate(h1_matches):
            start = m.start()
            end = h1_matches[i + 1].start() if i + 1 < len(h1_matches) else len(text)
            chunk = text[start:end].strip()
            if chunk:
                splits.append(chunk)
        return splits

    return []


def _split_by_numbered(text: str) -> list[str]:
    """按 numbered `1. ` `2. ` 切分"""
    if not text:
        return []
    matches = list(NUMBERED_RE.finditer(text))
    if len(matches) < 2:
        return []
    splits: list[str] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            splits.append(chunk)
    return splits


def _split_by_word_count(text: str, words_per_chunk: int) -> list[str]:
    """按词数兜底切分。

    策略:先按句子边界(中英句号/换行)切,再按 word 数聚合。
    如果整段没法按句子切(如纯空格串),则退化为按 word 数硬切。
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    # 按句子切分
    sentences = re.split(r"(?<=[。.!?！？\n])\s+", text)
    sentences = [s for s in sentences if s.strip()]

    chunks: list[str] = []
    cur: list[str] = []
    cur_words = 0
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        s_words = _word_count(s)
        if cur_words + s_words > words_per_chunk and cur:
            chunks.append(" ".join(cur))
            cur = [s]
            cur_words = s_words
        else:
            cur.append(s)
            cur_words += s_words
    if cur:
        chunks.append(" ".join(cur))

    # 兜底:如果只有一个 chunk 但总词数远超 words_per_chunk,
    # 说明句子切分没起作用(纯空格串),按 word 数硬切
    if len(chunks) == 1:
        total_wc = _word_count(chunks[0])
        if total_wc > words_per_chunk:
            tokens = re.findall(r"\S+", chunks[0])
            chunks = []
            for i in range(0, len(tokens), words_per_chunk):
                chunks.append(" ".join(tokens[i:i + words_per_chunk]))

    return chunks if chunks else [text]


def _extract_title(chunk: str) -> str:
    """从 chunk 第一行提取 section title(去掉 ## 和编号前缀)"""
    if not chunk:
        return ""
    first_line = chunk.split("\n", 1)[0].strip()
    # 去掉 ## 前缀
    first_line = re.sub(r"^#+\s*", "", first_line)
    # 去掉 numbered 前缀
    first_line = re.sub(r"^\d+\.\s*", "", first_line)
    # 截断过长 title
    if len(first_line) > 80:
        first_line = first_line[:77] + "..."
    return first_line or "(untitled)"


def _build_sections(chunks: list[str]) -> list[Section]:
    """把 chunk 列表组装为 Section 列表"""
    out: list[Section] = []
    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        title = _extract_title(chunk)
        wc = _word_count(chunk)
        out.append(Section(
            section_idx=i,
            title=title,
            text=chunk,
            word_count=wc,
        ))
    return out


# ============ Section Viability 评估 ============

def evaluate_section(section: Section) -> SectionVerdict:
    """评估单个 section 是否 viable。

    规则:
    - word_count < 20  → not viable (太短)
    - word_count > 800 → not viable (太长,需拆分)
    - 缺 imperative 动词 → not viable (无可执行)
    - 缺数字 → warn
    - 缺 cite/ref → warn

    Score 计算:
        base 0.5
        - 0.3 (若 < MIN_WORDS)
        - 0.2 (若 > MAX_WORDS)
        + 0.1 (含 imperative)
        + 0.1 (含数字)

    Args:
        section: 待评估的 Section

    Returns:
        SectionVerdict: 包含 viable 标志、0-1 score、reasons、blockers
    """
    reasons: list[str] = []
    blockers: list[str] = []

    wc = section.word_count
    text = section.text or ""

    # 1) 长度检查
    too_short = wc < MIN_WORDS
    too_long = wc > MAX_WORDS

    if too_short:
        blockers.append(f"section too short ({wc} words < {MIN_WORDS})")
    elif too_long:
        blockers.append(f"section too long ({wc} words > {MAX_WORDS}); split required")
    else:
        reasons.append(f"length OK ({wc} words)")

    # 2) imperative 检查
    has_imp = _has_imperative(text)
    if not has_imp:
        blockers.append("no imperative verb (should/must/will) found; not actionable")
    else:
        reasons.append("has imperative verb (actionable)")

    # 3) 数字
    has_num = _has_number(text)
    if has_num:
        reasons.append("has specific numbers")
    else:
        # warn 而非 blocker(不直接毙掉)
        reasons.append("warn: no specific numbers (consider adding metrics)")

    # 4) cite
    has_cite = _has_cite(text)
    if has_cite:
        reasons.append("has citations/refs")
    else:
        reasons.append("warn: no citations/refs (consider adding sources)")

    # score 聚合
    score = 0.5
    if too_short:
        score -= 0.3
    if too_long:
        score -= 0.2
    if has_imp:
        score += 0.1
    if has_num:
        score += 0.1
    score = _clip01(round(score, 4))

    # viable 判定
    viable = (not too_short) and (not too_long) and has_imp

    return SectionVerdict(
        section_idx=section.section_idx,
        viable=viable,
        score=score,
        reasons=reasons,
        blockers=blockers,
    )


# ============ AP 评分 ============

def compute_ap_score(report: ProposalReport) -> int:
    """计算 proposal 的 AP(Action Plan)评分, 0-10。

    规则:
    - total_sections == 0            → 1  (空)
    - viable_sections == total       → 10 (全 viable)
    - viable_sections >= 1           → 5-7(部分 viable, 按 viable/total 比例 5..7)
    - viable_sections == 0, total>0  → 2-4(全失败但有 section, 按 total 个数)
    """
    total = report.total_sections
    viable = report.viable_sections

    if total == 0:
        # 空 proposal
        return 1

    if viable == total:
        # 全 viable
        return 10

    if viable >= 1:
        # 部分 viable
        ratio = viable / total
        # 5 (ratio=0) .. 7 (ratio=1, exclusive)
        ap = int(5 + ratio * 2)
        ap = min(ap, 7)
        ap = max(ap, 5)
        return ap

    # viable == 0, total > 0
    # 全失败:2 (total=1) .. 4 (total>=5)
    # 段数越多说明至少有 attempt,微加
    bonus = min(2, max(0, total - 1))
    return 2 + bonus


# ============ 端到端 ============

def validate_proposal(text: str, proposal_idx: int = 0) -> ProposalReport:
    """完整流程:切分 → 评估 → 聚合 AP。

    Args:
        text: proposal 全文
        proposal_idx: proposal 编号(用于报告标识)

    Returns:
        ProposalReport: 含所有 section verdicts + AP score
    """
    if not text or not text.strip():
        return ProposalReport(
            proposal_idx=proposal_idx,
            total_sections=0,
            viable_sections=0,
            failing_sections=[],
            ap_score=1,
            verdicts=[],
        )

    sections = split_into_sections(text)
    verdicts: list[SectionVerdict] = []
    viable_count = 0
    failing: list[int] = []

    for sec in sections:
        v = evaluate_section(sec)
        verdicts.append(v)
        if v.viable:
            viable_count += 1
        else:
            failing.append(sec.section_idx)

    report = ProposalReport(
        proposal_idx=proposal_idx,
        total_sections=len(sections),
        viable_sections=viable_count,
        failing_sections=failing,
        ap_score=0,  # 稍后填
        verdicts=verdicts,
    )
    report.ap_score = compute_ap_score(report)
    return report


# ============ 多 proposal 比较 ============

def compare_proposals(reports: list[ProposalReport]) -> dict:
    """比较多篇 proposal 的 viability 报告。

    Args:
        reports: 多个 ProposalReport

    Returns:
        Dict: {
            "best_idx": int or None,
            "worst_idx": int or None,
            "avg_ap": float,
            "all_viable_count": int,
            "n_proposals": int,
            "ranking": List[(idx, ap_score)],
        }
    """
    empty_result: dict = {
        "best_idx": None,
        "worst_idx": None,
        "avg_ap": 0.0,
        "all_viable_count": 0,
        "n_proposals": 0,
        "ranking": [],
    }

    if not reports:
        return empty_result

    if len(reports) == 1:
        r = reports[0]
        return {
            "best_idx": r.proposal_idx,
            "worst_idx": r.proposal_idx,
            "avg_ap": float(r.ap_score),
            "all_viable_count": 1 if (r.total_sections > 0 and r.viable_sections == r.total_sections) else 0,
            "n_proposals": 1,
            "ranking": [(r.proposal_idx, r.ap_score)],
        }

    # 多个:按 ap_score 排序
    ranking = sorted(
        [(r.proposal_idx, r.ap_score) for r in reports],
        key=lambda x: x[1],
        reverse=True,
    )
    best_idx = ranking[0][0]
    worst_idx = ranking[-1][0]
    avg_ap = sum(r.ap_score for r in reports) / len(reports)
    all_viable_count = sum(
        1 for r in reports
        if r.total_sections > 0 and r.viable_sections == r.total_sections
    )

    return {
        "best_idx": best_idx,
        "worst_idx": worst_idx,
        "avg_ap": round(avg_ap, 2),
        "all_viable_count": all_viable_count,
        "n_proposals": len(reports),
        "ranking": ranking,
    }
