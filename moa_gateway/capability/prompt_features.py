"""25 维 prompt 特征提取 (来自 gateswarm-router 01)

真实启发式 + regex + 域检测,所有特征转 0-1 score 供路由决策使用。

25 维:
- 结构: length / word_count / sentence_count / avg_word_length
- 代码:   has_code_block / code_block_count
- 标点:   has_question_mark / question_count / has_exclamation
- 命令:   imperative_count
- 数学:   has_math_symbols / math_density
- 链接:   has_urls / url_count
- 语言:   language_detected / has_chinese / chinese_char_ratio
- 排版:   has_list_markers / list_item_count / has_table_separator
- 标记:   has_json_braces
- 语义:   named_entity_density / negation_count / hedging_count / sentiment_polarity

域判:
- code:         has_code_block + imperative_count
- math:         math_density > 0.1
- factual:      question + named_entity
- creative:     negation + hedging
- conversational: 默认 fallback
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass

__all__ = [
    "PromptFeatures",
    "extract_features",
    "domain_classify",
    "complexity_score",
    "urgency_score",
    "should_use_pro_model",
    "features_to_dict",
    "features_from_dict",
    "FEATURE_NAMES",
]


# ============ 25 维特征名 (顺序固定,便于序列化) ============

FEATURE_NAMES: list[str] = [
    # 1-4 结构
    "length",  # 0-1
    "word_count",  # 0-1
    "sentence_count",  # 0-1
    "avg_word_length",  # 0-1
    # 5-6 代码
    "has_code_block",  # 0/1
    "code_block_count",  # 0-1 (capped)
    # 7-9 标点
    "has_question_mark",  # 0/1
    "question_count",  # 0-1
    "has_exclamation",  # 0/1
    # 10 命令
    "imperative_count",  # 0-1
    # 11-12 数学
    "has_math_symbols",  # 0/1
    "math_density",  # 0-1
    # 13-14 链接
    "has_urls",  # 0/1
    "url_count",  # 0-1
    # 15-17 语言
    "language_detected",  # 类别 (不参与 0-1 评分, 但存于 features)
    "has_chinese",  # 0/1
    "chinese_char_ratio",  # 0-1
    # 18-19 列表
    "has_list_markers",  # 0/1
    "list_item_count",  # 0-1
    # 20-21 表格 / JSON
    "has_table_separator",  # 0/1
    "has_json_braces",  # 0/1
    # 22-25 语义
    "named_entity_density",  # 0-1
    "negation_count",  # 0-1
    "hedging_count",  # 0-1
    "sentiment_polarity",  # 0-1 (0=负,0.5=中性,1=正)
]


# 注:language_detected 是类别字段, dataclass 里 str, 不参与 0-1 归一化


# ============ 启发式常量 ============

# imperative 触发短语 (英文)
IMPERATIVE_PHRASES = (
    "please",
    "show me",
    "give me",
    "write",
    "create",
    "generate",
    "list",
    "explain",
    "describe",
    "summarize",
    "translate",
    "compute",
    "calculate",
    "solve",
    "implement",
    "refactor",
    "fix",
    "debug",
    "build",
    "design",
    "draft",
    "compose",
    "请你",
    "请",
    "帮我",
    "写一下",
    "写个",
    "给出",
    "列出",
    "解释一下",
)

# 否定词
NEGATION_WORDS = frozenset(
    {
        "not",
        "no",
        "never",
        "don't",
        "won't",
        "wouldn't",
        "shouldn't",
        "can't",
        "cannot",
        "isn't",
        "aren't",
        "wasn't",
        "weren't",
        "不",
        "没",
        "没有",
        "不要",
        "不能",
        "不会",
    }
)

# 模糊 / 弱断言
HEDGING_WORDS = frozenset(
    {
        "maybe",
        "perhaps",
        "might",
        "could",
        "possibly",
        "probably",
        "likely",
        "seems",
        "appears",
        "somewhat",
        "roughly",
        "about",
        "也许",
        "可能",
        "大概",
        "或许",
        "似乎",
        "差不多",
    }
)

# 情感词 (轻量字典)
POSITIVE_WORDS = frozenset(
    {
        "good",
        "great",
        "excellent",
        "perfect",
        "amazing",
        "wonderful",
        "fantastic",
        "love",
        "best",
        "happy",
        "nice",
        "awesome",
        "好",
        "棒",
        "完美",
        "优秀",
        "喜欢",
        "开心",
        "满意",
    }
)

NEGATIVE_WORDS = frozenset(
    {
        "bad",
        "terrible",
        "awful",
        "horrible",
        "hate",
        "worst",
        "sad",
        "angry",
        "poor",
        "wrong",
        "broken",
        "ugly",
        "差",
        "糟",
        "烂",
        "讨厌",
        "失败",
        "错",
        "坏",
    }
)


# ============ 编译 regex ============

CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
INLINE_CODE_RE = re.compile(r"`[^`\n]{1,80}`")
URL_RE = re.compile(r"https?://[^\s\)\]\}\,;\"'<>]+", re.IGNORECASE)
QUESTION_RE = re.compile(r"\?+|？+")
EXCLAMATION_RE = re.compile(r"!+|！+")
MATH_SYM_RE = re.compile(r"[+\-*/^=≤≥≠≈∑∏∫∂√]")  # ASCII +-*/^= + 常见 math unicode
SENTENCE_END_RE = re.compile(r"[。.!！?？\n]+")
ZH_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
EN_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z'\-]*")
DIGIT_RE = re.compile(r"\d+")
LIST_DASH_RE = re.compile(r"(?:^|\n)\s*[-*]\s+")
LIST_NUM_RE = re.compile(r"(?:^|\n)\s*\d+[\.\)、]\s+")
TABLE_SEP_RE = re.compile(r"\|[-:|\s]+\|")  # markdown 表格分隔行
JSON_BRACE_RE = re.compile(r"\{[\s\S]*?\}")
# 简易 named entity: 连续 ≥2 个 Title-case 词 (Apple Inc / New York)
NAMED_ENTITY_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")


# ============ 辅助工具 ============


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _log_scale(x: float, ceiling: float) -> float:
    """log 归一到 0-1, ceiling 处为 1.0"""
    if ceiling <= 0:
        return 0.0
    if x <= 0:
        return 0.0
    if x >= ceiling:
        return 1.0
    # log(1+x) / log(1+ceiling)
    return math.log1p(x) / math.log1p(ceiling)


def _count_imperatives(text_lower: str) -> int:
    """统计 imperative 短语出现次数 (重叠不重复计数)"""
    seen = 0
    consumed_until = -1
    # 按长度从长到短排,优先匹配长串
    sorted_phrases = sorted(set(IMPERATIVE_PHRASES), key=len, reverse=True)
    for ph in sorted_phrases:
        start = 0
        while True:
            idx = text_lower.find(ph, start)
            if idx < 0:
                break
            # 避免和已消费区间重叠
            if idx >= consumed_until:
                seen += 1
                consumed_until = idx + len(ph)
            start = idx + len(ph)
    return seen


def _split_sentences(text: str) -> list[str]:
    if not text:
        return []
    parts = SENTENCE_END_RE.split(text)
    return [p for p in parts if p and p.strip()]


def _tokenize_words(text: str) -> list[str]:
    """分词:英文 + 单个中文字符"""
    if not text:
        return []
    en = [t.lower() for t in EN_WORD_RE.findall(text)]
    zh = ZH_CHAR_RE.findall(text)
    return en + zh


# ============ Dataclass ============


@dataclass
class PromptFeatures:
    """25 维 prompt 特征 (前 25 个 float 字段 + 1 个类别字段)"""

    # 1-4 结构
    length: float = 0.0
    word_count: float = 0.0
    sentence_count: float = 0.0
    avg_word_length: float = 0.0
    # 5-6 代码
    has_code_block: float = 0.0
    code_block_count: float = 0.0
    # 7-9 标点
    has_question_mark: float = 0.0
    question_count: float = 0.0
    has_exclamation: float = 0.0
    # 10 命令
    imperative_count: float = 0.0
    # 11-12 数学
    has_math_symbols: float = 0.0
    math_density: float = 0.0
    # 13-14 链接
    has_urls: float = 0.0
    url_count: float = 0.0
    # 15-17 语言 (language_detected 是类别,不是 0-1)
    language_detected: str = "other"
    has_chinese: float = 0.0
    chinese_char_ratio: float = 0.0
    # 18-19 列表
    has_list_markers: float = 0.0
    list_item_count: float = 0.0
    # 20-21
    has_table_separator: float = 0.0
    has_json_braces: float = 0.0
    # 22-25
    named_entity_density: float = 0.0
    negation_count: float = 0.0
    hedging_count: float = 0.0
    sentiment_polarity: float = 0.5  # 0=负, 0.5=中性, 1=正


# ============ 主提取函数 ============


def extract_features(text: str) -> PromptFeatures:
    """25 维 prompt 特征提取主函数

    所有标量字段 0-1。language_detected 为类别("en"/"zh"/"mixed"/"other")。
    """
    if text is None:
        text = ""
    raw = text
    text_lower = raw.lower()
    char_count = len(raw)

    # 1) length: log scale, 5000 chars 处归 1
    length = _log_scale(char_count, 5000.0)

    # 2) word_count: 英文词 + 中文字符数, 2000 处归 1
    en_words = EN_WORD_RE.findall(raw)
    zh_chars = ZH_CHAR_RE.findall(raw)
    total_tokens = len(en_words) + len(zh_chars)
    word_count = _log_scale(total_tokens, 2000.0)

    # 3) sentence_count: 用句末标点切分, 50 处归 1
    sentences = _split_sentences(raw)
    sent_n = len(sentences)
    sentence_count = _log_scale(sent_n, 50.0)

    # 4) avg_word_length: 归一到 0-1 (英文按字符数, 中文按 1.0), 8.0 处归 1
    if total_tokens > 0:
        en_total_chars = sum(len(w) for w in en_words)
        avg_len = en_total_chars / max(len(en_words), 1) if en_words else 0.0
    else:
        avg_len = 0.0
    avg_word_length = _clip01(avg_len / 8.0) if avg_len > 0 else 0.0

    # 5-6) code blocks
    code_blocks = CODE_BLOCK_RE.findall(raw)
    has_code_block = 1.0 if code_blocks else 0.0
    code_block_count = _clip01(len(code_blocks) / 5.0)

    # 7-9) 标点
    q_matches = QUESTION_RE.findall(raw)
    has_q = 1.0 if q_matches else 0.0
    question_count = _clip01(len(q_matches) / 10.0)
    excl_matches = EXCLAMATION_RE.findall(raw)
    has_excl = 1.0 if excl_matches else 0.0

    # 10) imperative
    imp_n = _count_imperatives(text_lower)
    imperative_count = _clip01(imp_n / 5.0)

    # 11-12) math
    math_syms = MATH_SYM_RE.findall(raw)
    has_math = 1.0 if math_syms else 0.0
    # density = math symbols / (sentence_count + 1) 避免空除
    math_density = _clip01(len(math_syms) / max(sent_n, 1) / 3.0) if math_syms else 0.0

    # 13-14) URLs
    urls = URL_RE.findall(raw)
    has_urls = 1.0 if urls else 0.0
    url_count = _clip01(len(urls) / 5.0)

    # 15-17) language
    en_lower = sum(1 for c in raw if c.isascii() and c.isalpha())
    has_zh = 1.0 if zh_chars else 0.0
    # 字母类 = en_lower + zh 字符总数
    letter_total = en_lower + len(zh_chars)
    zh_ratio = (len(zh_chars) / letter_total) if letter_total > 0 else 0.0
    chinese_char_ratio = _clip01(zh_ratio)
    if zh_ratio >= 0.7:
        lang = "zh"
    elif zh_ratio <= 0.1:
        lang = "en"
    elif zh_ratio >= 0.3:
        lang = "mixed"
    else:
        # 极少中文,但有 (如专有名词) — 仍算 en
        lang = "en" if en_lower > 0 else "other"

    # 18-19) list
    list_dash = LIST_DASH_RE.findall(raw)
    list_num = LIST_NUM_RE.findall(raw)
    list_total = len(list_dash) + len(list_num)
    has_list = 1.0 if list_total > 0 else 0.0
    list_item_count = _clip01(list_total / 10.0)

    # 20) table separator
    has_table = 1.0 if TABLE_SEP_RE.search(raw) else 0.0

    # 21) json braces
    has_json = 1.0 if JSON_BRACE_RE.search(raw) else 0.0

    # 22) named entity density
    entities = NAMED_ENTITY_RE.findall(raw)
    ne_density = _clip01(len(entities) / max(sent_n, 1) / 2.0) if entities else 0.0

    # 23) negation
    tokens_lower = _tokenize_words(raw)
    neg_n = sum(1 for t in tokens_lower if t in NEGATION_WORDS)
    negation_count = _clip01(neg_n / 5.0)

    # 24) hedging
    hed_n = sum(1 for t in tokens_lower if t in HEDGING_WORDS)
    hedging_count = _clip01(hed_n / 5.0)

    # 25) sentiment polarity
    if tokens_lower:
        pos = sum(1 for t in tokens_lower if t in POSITIVE_WORDS)
        neg = sum(1 for t in tokens_lower if t in NEGATIVE_WORDS)
        net = pos - neg
        # 范围大致 -1..+1, 用 tanh-like clip
        norm = net / max(len(tokens_lower), 1) * 10.0  # 放大到 -1..+1 附近
        norm = max(-1.0, min(1.0, norm))
        sentiment = 0.5 + 0.5 * norm  # 0..1
    else:
        sentiment = 0.5

    return PromptFeatures(
        length=round(length, 4),
        word_count=round(word_count, 4),
        sentence_count=round(sentence_count, 4),
        avg_word_length=round(avg_word_length, 4),
        has_code_block=has_code_block,
        code_block_count=round(code_block_count, 4),
        has_question_mark=has_q,
        question_count=round(question_count, 4),
        has_exclamation=has_excl,
        imperative_count=round(imperative_count, 4),
        has_math_symbols=has_math,
        math_density=round(math_density, 4),
        has_urls=has_urls,
        url_count=round(url_count, 4),
        language_detected=lang,
        has_chinese=has_zh,
        chinese_char_ratio=round(chinese_char_ratio, 4),
        has_list_markers=has_list,
        list_item_count=round(list_item_count, 4),
        has_table_separator=has_table,
        has_json_braces=has_json,
        named_entity_density=round(ne_density, 4),
        negation_count=round(negation_count, 4),
        hedging_count=round(hedging_count, 4),
        sentiment_polarity=round(sentiment, 4),
    )


# ============ 域判别 ============


def domain_classify(features: PromptFeatures) -> str:
    """根据 features 判域,5 选 1:code/math/factual/creative/conversational"""
    # 优先级 1: code
    if features.has_code_block >= 1.0 and features.imperative_count > 0:
        return "code"
    if features.has_code_block >= 1.0 and features.code_block_count >= 0.4:
        return "code"
    # 优先级 2: math
    if features.math_density > 0.1:
        return "math"
    # 优先级 3: factual
    if features.question_count > 0 and features.named_entity_density > 0:
        return "factual"
    # 优先级 4: creative
    if features.negation_count > 0 and features.hedging_count > 0:
        return "creative"
    # fallback
    return "conversational"


# ============ 综合评分 ============


def complexity_score(features: PromptFeatures) -> float:
    """0-1,综合 length + sentence_count + has_code + has_urls + math_density

    权重:
    - length 0.30
    - sentence_count 0.20
    - has_code_block 0.20
    - math_density 0.15
    - has_urls 0.10
    - word_count 0.05
    """
    s = (
        0.30 * features.length
        + 0.20 * features.sentence_count
        + 0.20 * features.has_code_block
        + 0.15 * features.math_density
        + 0.10 * features.has_urls
        + 0.05 * features.word_count
    )
    return round(_clip01(s), 4)


def urgency_score(features: PromptFeatures) -> float:
    """0-1,综合 imperative + exclamation + question

    权重:
    - imperative_count 0.5
    - has_exclamation 0.3
    - question_count 0.2
    """
    s = (
        0.5 * features.imperative_count
        + 0.3 * features.has_exclamation
        + 0.2 * features.question_count
    )
    return round(_clip01(s), 4)


# ============ Pro model 路由决策 ============

PRO_COMPLEXITY_THRESHOLD = 0.7
PRO_MATH_DENSITY_THRESHOLD = 0.15


def should_use_pro_model(features: PromptFeatures) -> bool:
    """综合路由决策:
    - complexity > 0.7 且 (有 code 或 math_density > 0.15)
    """
    if complexity_score(features) <= PRO_COMPLEXITY_THRESHOLD:
        return False
    if features.has_code_block >= 1.0:
        return True
    return features.math_density > PRO_MATH_DENSITY_THRESHOLD


# ============ JSON 序列化 ============


def features_to_dict(features: PromptFeatures) -> dict:
    """features → dict (含全部 25 + language_detected)"""
    return asdict(features)


def features_from_dict(d: dict) -> PromptFeatures:
    """dict → features, 容忍缺失字段(用 dataclass 默认值兜底)"""
    if d is None:
        return PromptFeatures()
    kwargs: dict = {}
    for name in FEATURE_NAMES:
        if name == "language_detected":
            kwargs[name] = str(d.get(name, "other"))
        else:
            val = d.get(name, 0.0)
            try:
                kwargs[name] = float(val)
            except (TypeError, ValueError):
                kwargs[name] = 0.0
    return PromptFeatures(**kwargs)
