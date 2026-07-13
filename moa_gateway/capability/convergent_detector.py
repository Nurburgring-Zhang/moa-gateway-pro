"""convergent_detector — CONVERGENT 想法检测 + CONFLICTING 选择仲裁 (来自 09 opencode-moa)

核心能力:
  1. 想法抽取: 启发式从 proposal 文本中拆出独立想法
  2. CONVERGENT 检测: ≥ min_support 个 proposal 独立出现同一想法 → 标记共识
  3. CONFLICTING 检测: 同一议题下出现互斥选择(use/avoid, should/should not 等)
  4. 仲裁: 基于 viability 分数选 winner, 计算 confidence
  5. 汇总: convergent / conflicts / total_ideas / diversity_score

设计原则:
  - 所有逻辑基于真实数学/启发式(无 mock、无 hardcoded)
  - 关键词 Jaccard 相似度用于"同一想法"判定
  - 互斥词表 + 正则用于冲突检测
  - diversity_score = 1 - convergent_count / total_unique_ideas (越高越分歧)
"""
from __future__ import annotations
import re
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Tuple, Set, Optional


__all__ = [
    "Idea",
    "Proposal",
    "ConvergentIdea",
    "ConflictPair",
    "extract_ideas",
    "detect_convergent",
    "detect_conflicting",
    "arbitrate_conflicts",
    "convergent_summary",
    "STOPWORDS",
    "JACCARD_THRESHOLD",
]


# ============ 启发式常量 ============

# 停用词 (中英) — 关键词过滤
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
    "怎么", "为什么", "哪个", "哪些", "一个", "一些", "我们", "认为",
    "可以", "可能", "应该", "需要", "进行", "使用", "通过", "得到",
})

# 最小想法长度 (词数)
MIN_IDEA_WORDS = 4

# 关键词 Jaccard 相似度阈值 — 视为"同一想法"
JACCARD_THRESHOLD = 0.5

# 冲突检测 — 互斥前缀模式 (should X vs should not X 等)
NEGATION_PATTERNS: List[Tuple[str, str]] = [
    (r"\bshould\s+not\b", r"\bshould\b"),
    (r"\bdon'?t\b", r"\bdo\b"),
    (r"\bdoesn'?t\b", r"\bdoes\b"),
    (r"\bdidn'?t\b", r"\bdid\b"),
    (r"\bwon'?t\b", r"\bwill\b"),
    (r"\bcan'?t\b", r"\bcan\b"),
    (r"\bwouldn'?t\b", r"\bwould\b"),
    (r"\bcouldn'?t\b", r"\bcould\b"),
    (r"\bnot\s+use\b", r"\buse\b"),
    (r"\bavoid\b", r"\buse\b"),
    (r"\bdisable\b", r"\benable\b"),
    (r"\boff\b", r"\bon\b"),
    (r"\bno\b", r"\byes\b"),
    (r"\bunnecessary\b", r"\bnecessary\b"),
    (r"\bavoid\b", r"\badopt\b"),
    (r"\bunhealthy\b", r"\bhealthy\b"),
]

# 冲突检测 — "use X" vs "use Y" 模式 (抓取后跟对象)
USE_PATTERN = re.compile(r"\b(?:use|adopt|enable|choose)\s+([a-zA-Z][a-zA-Z0-9\-_]*)", re.IGNORECASE)
AVOID_PATTERN = re.compile(r"\b(?:avoid|don'?t\s+use|do\s+not\s+use|disable|reject)\s+([a-zA-Z][a-zA-Z0-9\-_]*)", re.IGNORECASE)

# 句子切分
SENTENCE_SPLIT_RE = re.compile(r"[。.!?！？;；\n]+")

# 词形归一: 简单去标点 + lower
WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]*|[\u4e00-\u9fff]|\d+")


# ============ Dataclass 定义 ============

@dataclass
class Idea:
    """单个想法 (从 proposal 抽取)"""
    text: str                          # 原始 quote
    source_proposal_idx: int           # 来源 proposal
    keywords: List[str] = field(default_factory=list)  # 归一化关键词

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class Proposal:
    """单个 proposal"""
    proposal_idx: int
    author: str
    text: str
    ideas: List[Idea] = field(default_factory=list)

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


@dataclass
class ConvergentIdea:
    """多 proposal 共同出现的想法 (CONVERGENT 信号)"""
    canonical_text: str                # 最长 quote 作为 canonical
    supporting_proposals: List[int]    # 支持的 proposal_idx 列表
    strength: float                    # 0-1, supporting_count / total
    exact_quote: str                   # 最长 quote

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ConflictPair:
    """冲突对 — 同一议题下互斥选择"""
    option_a: str
    option_b: str
    supporting_a: List[int] = field(default_factory=list)  # 支持 A 的 proposal
    supporting_b: List[int] = field(default_factory=list)  # 支持 B 的 proposal

    def to_dict(self) -> Dict:
        return asdict(self)


# ============ 辅助函数 ============

def _tokenize(text: str) -> List[str]:
    """分词: 英文 + 数字块 + 单个中文字符"""
    if not text:
        return []
    return [t.lower() for t in WORD_RE.findall(text)]


def _keywords(text: str) -> List[str]:
    """提取关键词: 停用词过滤 + lower"""
    tokens = _tokenize(text)
    # 去停用词,去短词 (< 2 字符英文或单字中文与停用词不冲突可保留)
    kws: List[str] = []
    seen = set()
    for t in tokens:
        if t in STOPWORDS:
            continue
        if len(t) < 2 and not re.match(r"[\u4e00-\u9fff]", t):
            continue
        if t in seen:
            continue
        seen.add(t)
        kws.append(t)
    return kws


def _jaccard(a: List[str], b: List[str]) -> float:
    """Jaccard 相似度: |A∩B| / |A∪B|"""
    if not a and not b:
        return 1.0
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    inter = sa & sb
    return len(inter) / len(union)


def _split_sentences(text: str) -> List[str]:
    """按句号/分号/换行切句, 保留原始 quote"""
    if not text or not text.strip():
        return []
    parts = SENTENCE_SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _word_count(text: str) -> int:
    """统计词数 (英文按空白 + 中文按字符)"""
    if not text:
        return 0
    return len(_tokenize(text))


# ============ 核心 API: extract_ideas ============

def extract_ideas(proposal_text: str, proposal_idx: int) -> List[Idea]:
    """启发式抽取想法

    流程:
      1. 按句号/分号/换行切句
      2. 过滤 < MIN_IDEA_WORDS 词
      3. 关键词归一化 (lower + 停用词过滤)

    Returns:
        List[Idea]
    """
    if not proposal_text or not proposal_text.strip():
        return []

    sentences = _split_sentences(proposal_text)
    ideas: List[Idea] = []
    for sent in sentences:
        # 过滤过短句子
        if _word_count(sent) < MIN_IDEA_WORDS:
            continue
        kws = _keywords(sent)
        if not kws:
            continue
        ideas.append(Idea(
            text=sent,
            source_proposal_idx=proposal_idx,
            keywords=kws,
        ))
    return ideas


# ============ 核心 API: detect_convergent ============

def detect_convergent(
    proposals: List[Proposal],
    min_support: int = 3,
) -> List[ConvergentIdea]:
    """检测 CONVERGENT 想法

    逻辑:
      1. 遍历每个 proposal 的 ideas
      2. 想法 B 与已存在 cluster A 关键词 Jaccard ≥ JACCARD_THRESHOLD → 归入 A
      3. 否则开新 cluster
      4. cluster 长度 ≥ min_support → 输出 ConvergentIdea
      5. canonical_text = cluster 中最长 quote
      6. strength = supporting_count / total_proposals

    Args:
        proposals: Proposal 列表
        min_support: 至少多少个 proposal 共享才视为 CONVERGENT

    Returns:
        ConvergentIdea 列表 (按 strength 降序)
    """
    if not proposals or min_support < 1:
        return []

    total = len(proposals)

    # cluster: list of {proposal_indices: set, ideas: [Idea]}
    # 用 keywords 集合的第一个 idea 作为 cluster 代表
    clusters: List[Dict] = []  # 每个 cluster 是 {"pidxs": set, "ideas": [Idea], "rep_kws": set}

    for prop in proposals:
        for idea in prop.ideas:
            matched = False
            idea_kws_set = set(idea.keywords)
            for cluster in clusters:
                if idea.source_proposal_idx in cluster["pidxs"]:
                    # 同一 proposal 重复想法不重复计入
                    matched = True
                    break
                sim = _jaccard(idea.keywords, list(cluster["rep_kws"]))
                if sim >= JACCARD_THRESHOLD:
                    cluster["pidxs"].add(idea.source_proposal_idx)
                    cluster["ideas"].append(idea)
                    # 更新代表关键词为 union
                    cluster["rep_kws"] = cluster["rep_kws"] | idea_kws_set
                    matched = True
                    break
            if not matched:
                clusters.append({
                    "pidxs": {idea.source_proposal_idx},
                    "ideas": [idea],
                    "rep_kws": idea_kws_set,
                })

    results: List[ConvergentIdea] = []
    for cluster in clusters:
        if len(cluster["pidxs"]) >= min_support:
            # canonical_text = 最长 quote
            longest = max(cluster["ideas"], key=lambda x: len(x.text))
            supporting = sorted(cluster["pidxs"])
            strength = len(supporting) / total
            results.append(ConvergentIdea(
                canonical_text=longest.text,
                supporting_proposals=supporting,
                strength=round(strength, 4),
                exact_quote=longest.text,
            ))

    # 按 strength 降序, 同分时按 supporting 数量
    results.sort(key=lambda c: (-c.strength, -len(c.supporting_proposals)))
    return results


# ============ 核心 API: detect_conflicting ============

# 冲突检测的 should 后续对象提取: 抓首个内容词
_SHOULD_OBJ_RE = re.compile(
    r"\bshould\s+(?:not\s+)?([a-z][a-z\-]+)",
    re.IGNORECASE,
)
# 排除的弱动词 (情态/助动词/无意义动词)
_WEAK_VERBS = frozenset({
    "be", "have", "do", "get", "make", "use", "consider", "also",
    "always", "just", "only", "still", "really", "actually", "probably",
    "maybe", "perhaps", "try", "keep", "put", "let", "see", "go", "come",
    "take", "give", "say", "tell", "look", "find", "know", "think",
    "want", "need", "feel", "seem", "leave", "work", "run", "start",
})


def _extract_should_target(text: str) -> List[Tuple[str, str]]:
    """从文本中提取所有 should/should-not 模式

    Returns:
        List of (target_word_lower, polarity) where polarity is "positive" or "negative"
    """
    out: List[Tuple[str, str]] = []
    if not text:
        return out
    # 先用 not-aware 的扫描
    for m in re.finditer(r"\bshould\s+(not\s+)?([a-z][a-z\-]+)", text, re.IGNORECASE):
        neg = m.group(1) is not None
        word = m.group(2).lower()
        if word in _WEAK_VERBS:
            continue
        out.append((word, "negative" if neg else "positive"))
    return out


def detect_conflicting(proposals: List[Proposal]) -> List[ConflictPair]:
    """检测 CONFLICTING 互斥选择

    启发式:
      1. "use X" vs "use Y" 模式 — 同一对象在 use/avoid 两侧
      2. "should X" vs "should not X" 模式 — should 后首个内容词相同
      3. 通用否定模式: on/off, yes/no, enable/disable

    Returns:
        ConflictPair 列表
    """
    if not proposals:
        return []

    # 收集每个 proposal 的 use/avoid/should/should-not 选项
    # 格式: (option_text, core_key, proposal_idx, polarity)
    options: List[Tuple[str, str, int, str]] = []

    for prop in proposals:
        text = prop.text
        if not text:
            continue

        # 1) use/avoid 模式
        for m in USE_PATTERN.finditer(text):
            obj = m.group(1).lower()
            options.append((f"use {obj}", obj, prop.proposal_idx, "positive"))
        for m in AVOID_PATTERN.finditer(text):
            obj = m.group(1).lower()
            options.append((f"avoid {obj}", obj, prop.proposal_idx, "negative"))

        # 2) should/should not 模式 — 用首个内容词作为 core_key
        for target, polarity in _extract_should_target(text):
            if polarity == "positive":
                options.append((f"should {target}", target, prop.proposal_idx, "positive"))
            else:
                options.append((f"should not {target}", target, prop.proposal_idx, "negative"))

    # 按 core_key 分组
    groups: Dict[str, List[Tuple[str, int, str]]] = defaultdict(list)
    for opt, key, pidx, pol in options:
        if not key:
            continue
        groups[key].append((opt, pidx, pol))

    conflicts: List[ConflictPair] = []
    seen_pairs: Set[Tuple[str, str]] = set()

    for key, items in groups.items():
        positives = [(o, p) for o, p, pol in items if pol == "positive"]
        negatives = [(o, p) for o, p, pol in items if pol == "negative"]
        if not positives or not negatives:
            continue

        # 选最长的 positive / negative 作为 option_a / option_b
        pos_text, _ = max(positives, key=lambda x: len(x[0]))
        neg_text, _ = max(negatives, key=lambda x: len(x[0]))

        # 防止正反文本完全相同
        if pos_text == neg_text:
            continue

        # 标准化配对键 (字典序)
        pair_key = tuple(sorted([pos_text, neg_text]))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)

        sup_a = sorted({p for o, p in positives})
        sup_b = sorted({p for o, p in negatives})

        conflicts.append(ConflictPair(
            option_a=pos_text,
            option_b=neg_text,
            supporting_a=sup_a,
            supporting_b=sup_b,
        ))

    # 按支持数降序
    conflicts.sort(key=lambda c: (-(len(c.supporting_a) + len(c.supporting_b)), c.option_a))
    return conflicts


# ============ 核心 API: arbitrate_conflicts ============

def arbitrate_conflicts(
    conflicts: List[ConflictPair],
    viability_scores: Dict[int, float],
) -> List[Tuple[ConflictPair, str, float]]:
    """仲裁冲突

    逻辑:
      - 对每个 conflict, 比较 option_a 与 option_b 的 viability 加权
      - 选高者作为 winner
      - confidence = (winner_viability - loser_viability) / max_viability
        若 winner==loser, confidence = 0.5
        若无 viability 数据, 用 supporting 数量作为 fallback

    Args:
        conflicts: ConflictPair 列表
        viability_scores: proposal_idx → viability (0-1)

    Returns:
        List of (ConflictPair, winner_choice: "A"/"B", confidence: 0-1)
    """
    results: List[Tuple[ConflictPair, str, float]] = []

    for conflict in conflicts:
        # 计算 option_a / option_b 的 viability 加权
        # viability_A = mean(viability of supporting_a) * (1 + 0.1 * len(supporting_a))
        # 简化: 用 sum / max
        def _score(proposal_indices: List[int]) -> float:
            if not proposal_indices:
                return 0.0
            scores = [viability_scores.get(p, 0.5) for p in proposal_indices]
            if not scores:
                return 0.5
            # 加权: 平均 viability 乘以支持数 (支持越多越可信)
            avg = sum(scores) / len(scores)
            return avg * len(scores)

        score_a = _score(conflict.supporting_a)
        score_b = _score(conflict.supporting_b)

        if score_a > score_b:
            winner = "A"
            confidence = (score_a - score_b) / max(score_a, 1e-9)
            confidence = min(1.0, confidence)
        elif score_b > score_a:
            winner = "B"
            confidence = (score_b - score_a) / max(score_b, 1e-9)
            confidence = min(1.0, confidence)
        else:
            winner = "A"  # 平局默认 A
            confidence = 0.5

        results.append((conflict, winner, round(confidence, 4)))

    return results


# ============ 核心 API: convergent_summary ============

def convergent_summary(
    proposals: List[Proposal],
    min_support: int = 3,
) -> Dict:
    """汇总报告: convergent / conflicts / total_ideas / diversity_score

    diversity_score = 1 - (convergent_count / total_unique_ideas)
    - 越高越分歧 (无 convergent 时 = 1.0)
    - 全 convergent 时 = 0
    - 没有想法时 = 1.0 (退化为最大分歧)
    """
    convergent = detect_convergent(proposals, min_support=min_support)
    conflicts = detect_conflicting(proposals)

    # total_unique_ideas: 跨 proposal 想法的 cluster 数
    # 复用 detect_convergent 的 cluster 逻辑 (min_support=1 视为全 cluster)
    clusters: List[Dict] = []
    for prop in proposals:
        for idea in prop.ideas:
            matched = False
            for cluster in clusters:
                if idea.source_proposal_idx in cluster["pidxs"]:
                    matched = True
                    break
                sim = _jaccard(idea.keywords, list(cluster["rep_kws"]))
                if sim >= JACCARD_THRESHOLD:
                    cluster["pidxs"].add(idea.source_proposal_idx)
                    cluster["rep_kws"] = cluster["rep_kws"] | set(idea.keywords)
                    matched = True
                    break
            if not matched:
                clusters.append({
                    "pidxs": {idea.source_proposal_idx},
                    "rep_kws": set(idea.keywords),
                })

    total_unique_ideas = len(clusters)
    convergent_count = len(convergent)
    total_ideas = sum(len(p.ideas) for p in proposals)

    if total_unique_ideas == 0:
        diversity_score = 1.0
    else:
        diversity_score = 1.0 - (convergent_count / total_unique_ideas)
        diversity_score = max(0.0, min(1.0, diversity_score))

    return {
        "convergent": [c.to_dict() for c in convergent],
        "conflicts": [c.to_dict() for c in conflicts],
        "total_ideas": total_ideas,
        "total_unique_ideas": total_unique_ideas,
        "diversity_score": round(diversity_score, 4),
        "proposal_count": len(proposals),
    }
