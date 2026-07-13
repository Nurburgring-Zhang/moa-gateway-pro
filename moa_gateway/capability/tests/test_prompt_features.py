"""prompt_features 单元测试 (20+ 测试)

真实 assert, 严禁 mock。所有测试基于真实启发式逻辑。
"""
from __future__ import annotations
import pytest
import sys
import math
from pathlib import Path

# 允许直接 import
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.prompt_features import (
    PromptFeatures,
    extract_features,
    domain_classify,
    complexity_score,
    urgency_score,
    should_use_pro_model,
    features_to_dict,
    features_from_dict,
    FEATURE_NAMES,
)


# ============ length 边界测试 ============

def test_length_zero():
    """空字符串 → length = 0"""
    f = extract_features("")
    assert f.length == 0.0
    assert f.word_count == 0.0
    assert f.sentence_count == 0.0


def test_length_medium():
    """500 字符中等长度 → length 介于 0-1 中段"""
    text = "This is a test sentence. " * 25  # ~600 chars
    f = extract_features(text)
    assert 0.0 < f.length < 1.0, f"expected 0<length<1, got {f.length}"
    assert f.length > 0.3, f"expected >0.3 for 600 chars, got {f.length}"


def test_length_long_capped():
    """超长文本(20000 char)→ length 接近 1 但不超"""
    text = "x" * 20000
    f = extract_features(text)
    assert 0.99 <= f.length <= 1.0, f"expected ~1, got {f.length}"


def test_length_monotonic():
    """length 随字符数单调不减"""
    f1 = extract_features("a" * 50)
    f2 = extract_features("a" * 500)
    f3 = extract_features("a" * 5000)
    assert f1.length <= f2.length <= f3.length, (
        f"non-monotonic: {f1.length}, {f2.length}, {f3.length}"
    )


# ============ word_count 中文友好 ============

def test_word_count_chinese_friendly():
    """中文按字符分词"""
    text = "你好世界这是一个测试"
    f = extract_features(text)
    # 9 个中文字符,归一化后 0-1 之间
    assert f.word_count > 0.0
    assert f.chinese_char_ratio == 1.0
    assert f.has_chinese == 1.0


def test_word_count_english():
    """英文按空格分词"""
    text = "hello world this is a test"
    f = extract_features(text)
    assert f.word_count > 0.0
    assert f.chinese_char_ratio == 0.0


# ============ sentence_count ============

def test_sentence_count_multiple():
    """多句号 → sentence_count > 0"""
    text = "First sentence. Second sentence! Third sentence? Fourth one."
    f = extract_features(text)
    assert f.sentence_count > 0.0
    assert f.sentence_count < 1.0  # 4 句不会触顶


def test_sentence_count_chinese():
    """中文句号识别"""
    text = "第一句。第二句！第三句？"
    f = extract_features(text)
    assert f.sentence_count > 0.0


# ============ has_code_block ============

def test_has_code_block_detected():
    """```...``` 块识别"""
    text = "Here is code:\n```python\nprint('hi')\n```\nDone."
    f = extract_features(text)
    assert f.has_code_block == 1.0
    assert f.code_block_count > 0.0


def test_code_block_count_multiple():
    """多 code block 计数"""
    text = "```python\na = 1\n```\nMiddle text.\n```javascript\nb = 2\n```\n```go\nc = 3\n```\nMore text."
    f = extract_features(text)
    assert f.code_block_count >= 0.4  # 3/5
    assert f.has_code_block == 1.0


def test_no_code_block():
    """无 ``` → has_code_block = 0"""
    text = "Just plain text without any code."
    f = extract_features(text)
    assert f.has_code_block == 0.0
    assert f.code_block_count == 0.0


# ============ question_count ============

def test_question_count_multiple():
    """多个 ? → question_count > 0"""
    text = "What is X? How does Y work? Why Z?"
    f = extract_features(text)
    assert f.has_question_mark == 1.0
    assert f.question_count > 0.0
    assert f.question_count < 1.0


def test_question_count_chinese():
    """中文 ？ 识别"""
    text = "什么是X？Y怎么工作？Z为什么？"
    f = extract_features(text)
    assert f.has_question_mark == 1.0
    assert f.question_count > 0.0


# ============ imperative ============

def test_imperative_please():
    """'please' 触发 imperative"""
    text = "Please explain how neural networks work."
    f = extract_features(text)
    assert f.imperative_count > 0.0


def test_imperative_show_me():
    """'show me' 触发"""
    text = "Show me an example of recursion."
    f = extract_features(text)
    assert f.imperative_count > 0.0


def test_imperative_write():
    """'write' 触发"""
    text = "Write a function to compute factorial."
    f = extract_features(text)
    assert f.imperative_count > 0.0


# ============ math ============

def test_math_symbols_detected():
    """+ - * / 符号识别"""
    text = "Compute 3 + 5 * 2 = ?"
    f = extract_features(text)
    assert f.has_math_symbols == 1.0
    assert f.math_density > 0.0


def test_math_density_high():
    """高 math density"""
    text = "a + b - c * d / e ^ f = g"
    f = extract_features(text)
    assert f.math_density > 0.3, f"expected >0.3, got {f.math_density}"


# ============ URL ============

def test_url_count_multiple():
    """多 URL 计数"""
    text = "Check https://example.com and https://github.com/user/repo for info."
    f = extract_features(text)
    assert f.has_urls == 1.0
    assert f.url_count > 0.0
    assert f.url_count < 1.0  # 2 URLs / 5 cap


def test_url_count_zero():
    """无 URL → 0"""
    text = "Just plain text here."
    f = extract_features(text)
    assert f.has_urls == 0.0
    assert f.url_count == 0.0


# ============ language_detected ============

def test_language_pure_english():
    """全英文 → 'en'"""
    text = "This is a complete English sentence without any Chinese characters at all."
    f = extract_features(text)
    assert f.language_detected == "en"
    assert f.has_chinese == 0.0


def test_language_pure_chinese():
    """全中文 → 'zh'"""
    text = "这是一段纯中文文本没有任何英文单词"
    f = extract_features(text)
    assert f.language_detected == "zh"
    assert f.has_chinese == 1.0
    assert f.chinese_char_ratio >= 0.7


def test_language_mixed():
    """中英混合 → 'mixed'"""
    # 6 en letters + 6 zh chars → 6/12 = 0.5 → mixed
    text = "Hello 中文测试 mixed 混合"
    f = extract_features(text)
    assert f.language_detected == "mixed"
    assert f.has_chinese == 1.0
    assert 0.3 <= f.chinese_char_ratio <= 0.7


# ============ chinese_char_ratio ============

def test_chinese_char_ratio_partial():
    """部分中文"""
    text = "Hello World 你好"  # 10 letters + 2 zh chars → 2/12 ≈ 0.167
    f = extract_features(text)
    assert 0.0 < f.chinese_char_ratio < 1.0


# ============ list_markers ============

def test_list_markers_dash():
    """'- ' 列表标记识别"""
    text = "Items:\n- first\n- second\n- third"
    f = extract_features(text)
    assert f.has_list_markers == 1.0
    assert f.list_item_count > 0.0


def test_list_markers_numbered():
    """'1.' 列表标记识别"""
    text = "Steps:\n1. First step\n2. Second step\n3. Third step"
    f = extract_features(text)
    assert f.has_list_markers == 1.0
    assert f.list_item_count > 0.0


# ============ table_separator ============

def test_table_separator_detected():
    """'|---|' markdown 表格分隔"""
    text = "| Name | Age |\n|------|-----|\n| Alice | 30 |"
    f = extract_features(text)
    assert f.has_table_separator == 1.0


# ============ json_braces ============

def test_json_braces_detected():
    """'{...}' 识别"""
    text = 'Config: {"key": "value", "n": 42}'
    f = extract_features(text)
    assert f.has_json_braces == 1.0


# ============ negation ============

def test_negation_count_multiple():
    """多个 not 计数"""
    text = "I do not like this. No, never, it's not good. Don't go there."
    f = extract_features(text)
    assert f.negation_count > 0.0
    # 该文本有 ~5 个 negation token,触顶 1.0 是合理的(归一化设计)
    assert f.negation_count >= 0.5


def test_negation_count_zero():
    """无否定词 → 0"""
    text = "I love this great wonderful perfect solution today."
    f = extract_features(text)
    assert f.negation_count == 0.0


# ============ hedging ============

def test_hedging_count_multiple():
    """多个 maybe 计数"""
    text = "Maybe we could try. Perhaps this might work. It could possibly help."
    f = extract_features(text)
    assert f.hedging_count > 0.0
    # 触顶 1.0 是合理的(归一化设计)
    assert f.hedging_count >= 0.5


def test_hedging_count_zero():
    """无 hedging 词 → 0"""
    text = "Do this. Then that. After that stop."
    f = extract_features(text)
    assert f.hedging_count == 0.0


# ============ sentiment_polarity ============

def test_sentiment_positive():
    """正向词 → polarity > 0.5"""
    text = "This is a great and excellent and perfect solution. I love it."
    f = extract_features(text)
    assert f.sentiment_polarity > 0.5, f"expected >0.5, got {f.sentiment_polarity}"


def test_sentiment_negative():
    """负向词 → polarity < 0.5"""
    text = "This is terrible and awful and horrible. I hate this bad thing."
    f = extract_features(text)
    assert f.sentiment_polarity < 0.5, f"expected <0.5, got {f.sentiment_polarity}"


def test_sentiment_neutral():
    """中性文本 → polarity ≈ 0.5"""
    text = "The cat sat on the mat. It was a Tuesday afternoon."
    f = extract_features(text)
    assert 0.4 <= f.sentiment_polarity <= 0.6, (
        f"expected ~0.5, got {f.sentiment_polarity}"
    )


# ============ named_entity_density ============

def test_named_entity_density_high():
    """多 Title-case 连续词 → density > 0"""
    text = "Apple Inc released the iPhone. New York Times reported on Google."
    f = extract_features(text)
    assert f.named_entity_density > 0.0


# ============ domain_classify 5 域 ============

def test_domain_code():
    """code 域:有代码块 + imperative"""
    text = "```python\ndef hello():\n    print('hi')\n```\nPlease explain this code."
    f = extract_features(text)
    assert domain_classify(f) == "code"


def test_domain_math():
    """math 域:math_density > 0.1"""
    text = "Solve x^2 + 3x - 4 = 0 for x."
    f = extract_features(text)
    assert domain_classify(f) == "math"


def test_domain_factual():
    """factual 域:有 ? + named entity"""
    text = "What is the capital of New York State? Where is Google headquartered?"
    f = extract_features(text)
    assert domain_classify(f) == "factual"


def test_domain_creative():
    """creative 域:negation + hedging"""
    text = "Maybe we could try writing a story that is not about war. Perhaps it might involve peace."
    f = extract_features(text)
    assert domain_classify(f) == "creative"


def test_domain_conversational():
    """conversational 域:默认"""
    text = "Hi there. How are you doing today?"
    f = extract_features(text)
    # 这条 query 有 ? 但 named_entity_density = 0,不应归 factual
    # 也不应归 code/math/creative → conversational
    assert domain_classify(f) == "conversational"


# ============ complexity_score ============

def test_complexity_short_simple():
    """短简单文本 → complexity 低"""
    f = extract_features("Hi.")
    assert 0.0 <= complexity_score(f) < 0.3


def test_complexity_long_with_code():
    """长文本+代码 → complexity 高"""
    text = "Here is a detailed explanation:\n" + ("Some text here. " * 100) + "\n```python\ndef f():\n    return 42\n```\n"
    f = extract_features(text)
    assert complexity_score(f) > 0.5, f"expected >0.5, got {complexity_score(f)}"


def test_complexity_bounded():
    """complexity 总在 0-1"""
    f = extract_features("x" * 100000 + "```\ncode\n```" * 20)
    c = complexity_score(f)
    assert 0.0 <= c <= 1.0


# ============ urgency_score ============

def test_urgency_calm():
    """平静文本 → urgency 低"""
    f = extract_features("The weather today is mild and pleasant.")
    assert urgency_score(f) < 0.3


def test_urgency_urgent():
    """imperative + ! + ? 多 → urgency 高"""
    text = "Please help me now! I need this immediately! Can you do it fast?"
    f = extract_features(text)
    assert urgency_score(f) > 0.3, f"expected >0.3, got {urgency_score(f)}"


# ============ should_use_pro_model ============

def test_pro_model_simple_no():
    """简单文本 → False"""
    f = extract_features("Hi.")
    assert should_use_pro_model(f) is False


def test_pro_model_complex_with_code_yes():
    """长+复杂+代码 → True"""
    text = ("This is a complex programming task. " * 50) + "\n```python\ndef complex():\n    return [x**2 for x in range(100)]\n```\n"
    f = extract_features(text)
    assert should_use_pro_model(f) is True, (
        f"expected True, complexity={complexity_score(f)}, has_code={f.has_code_block}, math_density={f.math_density}"
    )


def test_pro_model_complex_with_math_yes():
    """长+复杂+math → True"""
    # 实际场景:math 教学 + 代码块(解方程的 Python 实现)
    text = (
        "Let us solve the equation x^2 + 3x - 4 = 0 step by step. "
        "We use the quadratic formula. " * 30
        + "Here is the code:\n```python\nimport math\ndef solve(a, b, c):\n    d = b*b - 4*a*c\n    return (-b + math.sqrt(d)) / (2*a), (-b - math.sqrt(d)) / (2*a)\n```\n"
        "The result is x = 1 and x = -4. " * 20
    )
    f = extract_features(text)
    assert should_use_pro_model(f) is True, (
        f"expected True, complexity={complexity_score(f)}, math_density={f.math_density}, has_code={f.has_code_block}, length={f.length}"
    )


def test_pro_model_complex_no_code_no_math():
    """复杂但无 code/math → False (短文)"""
    f = extract_features("A short essay about life. " * 20)
    assert should_use_pro_model(f) is False


# ============ JSON 序列化往返 ============

def test_features_to_dict_keys():
    """features_to_dict 含 25 + language"""
    f = extract_features("Hello world.")
    d = features_to_dict(f)
    assert isinstance(d, dict)
    # 25 个 float + 1 个 str
    assert "language_detected" in d
    for name in FEATURE_NAMES:
        assert name in d, f"missing key: {name}"


def test_features_to_from_roundtrip():
    """dict 往返保持一致"""
    f1 = extract_features("Please write a Python function. ```python\ndef f(): pass\n```")
    d = features_to_dict(f1)
    f2 = features_from_dict(d)
    for name in FEATURE_NAMES:
        if name == "language_detected":
            assert f1.language_detected == f2.language_detected
        else:
            assert getattr(f1, name) == getattr(f2, name), (
                f"mismatch on {name}: {getattr(f1, name)} vs {getattr(f2, name)}"
            )


def test_features_from_dict_missing():
    """缺字段时用默认值"""
    f = features_from_dict({})
    assert isinstance(f, PromptFeatures)
    assert f.language_detected == "other"
    assert f.length == 0.0


def test_features_from_dict_invalid_type():
    """无效类型 → fallback 0"""
    f = features_from_dict({"length": "not_a_number", "has_code_block": None})
    assert f.length == 0.0
    assert f.has_code_block == 0.0


# ============ 边界与鲁棒性 ============

def test_none_input():
    """None 输入不崩"""
    f = extract_features(None)
    assert isinstance(f, PromptFeatures)
    assert f.length == 0.0


def test_exclamation_detected():
    """! 识别 has_exclamation"""
    text = "Wow! Amazing! Incredible!"
    f = extract_features(text)
    assert f.has_exclamation == 1.0


def test_all_features_in_range():
    """所有标量特征都在 0-1"""
    f = extract_features(
        "Please explain this code:\n"
        "```python\ndef add(a, b):\n    return a + b\n```\n"
        "What does it do? Maybe you could test it? "
        "Check https://example.com and https://github.com. "
        "This is a great solution. No errors. "
        "List: \n- item1\n- item2\n- item3\n"
        "| col1 | col2 |\n|------|------|\n| a | b |\n"
    )
    for name in FEATURE_NAMES:
        if name == "language_detected":
            continue
        val = getattr(f, name)
        assert 0.0 <= val <= 1.0, f"{name} out of range: {val}"


def test_feature_names_count():
    """FEATURE_NAMES 数量 = 25"""
    assert len(FEATURE_NAMES) == 25
