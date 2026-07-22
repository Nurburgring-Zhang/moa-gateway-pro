"""Prompt Injection 金丝雀检测 (来自参考表 S-36)

真实可用实现,非 mock。金丝雀 (canary) 是注入到 prompt 里的隐藏 token,
用于检测 LLM 是否被 prompt injection 攻击诱导泄露原始 prompt 内容。

核心能力:
- 4 种 canary 注入策略: SUFFIX / PREFIX / INVISIBLE / MULTI
- Canary 生成: 16 字符随机 token (含 'moa_canary_' 前缀)
- INVISIBLE 策略: 零宽字符 (U+200B / U+200C / U+200D / U+FEFF) 编码
- 3 档响应分类: safe / suspicious / compromised
- 15+ 已知 prompt injection 模式正则匹配 (case-insensitive)
- Jaccard token 相似度 (0-1)

设计要点:
- INVISIBLE canary 视觉不可见 (用零宽字符),但 LLM 可识别并可能回显
- Jaccard 相似度用于检测 canary 的部分回显 (即使被拼接/转义)
- classify_response 综合判断: 全 canary 泄露 = compromised,
  部分泄露 / 命中 injection 关键词 = suspicious, 都没有 = safe
"""

from __future__ import annotations

import re
import secrets
import string
from enum import Enum
from typing import Any

__all__ = [
    "CanaryStrategy",
    "CanaryDetector",
    "generate_canary",
    "INJECTION_PATTERNS",
    "INVISIBLE_CHARS",
    "CANARY_PREFIX",
]


# ============ 常量定义 ============

CANARY_PREFIX: str = "moa_canary_"
"""金丝雀 token 前缀,便于在 LLM 输出中识别"""

# 零宽字符 (用于 INVISIBLE 策略编码)
INVISIBLE_CHARS: tuple[str, ...] = ("\u200b", "\u200c", "\u200d", "\ufeff")
"""4 种零宽字符作为编码基 (类似 base-4): U+200B / U+200C / U+200D / U+FEFF"""

# token 字符集: 大小写字母 + 数字 (排除易混淆字符 0/O/1/l/I)
_CANARY_ALPHABET: str = string.ascii_letters + string.digits
# 去除易混淆字符,降低视觉/输入错误
_CANARY_ALPHABET = "".join(c for c in _CANARY_ALPHABET if c not in "0O1lI")


# ============ Canary 策略枚举 ============


class CanaryStrategy(str, Enum):
    """Canary 注入策略

    - SUFFIX: 在 prompt 末尾追加 canary token
    - PREFIX: 在 prompt 开头插入 canary token
    - INVISIBLE: 用零宽字符编码 canary,视觉不可见
    - MULTI: 同时使用 PREFIX + SUFFIX + INVISIBLE 三重注入
    """

    SUFFIX = "suffix"
    PREFIX = "prefix"
    INVISIBLE = "invisible"
    MULTI = "multi"


# ============ 已知 prompt injection 模式 (15+) ============
# 来源: 公开 prompt injection / jailbreak 攻防研究汇总
# 全部 case-insensitive,使用 frozenset 加速查找

INJECTION_PATTERNS: frozenset[str] = frozenset(
    {
        # 1. 忽略之前的指令 (经典模式)
        r"ignore (all )?(?:previous|prior|above) instructions?",
        # 2. 角色重定义
        r"you are (now )?a",
        # 3. 新系统指令
        r"new (?:system )?instructions?",
        # 4. 忽略/无视指令
        r"disregard",
        # 5. 忘记上下文
        r"forget (?:everything|all)",
        # 6. 系统提示泄露
        r"system prompt",
        # 7. 角色扮演指令
        r"act as",
        # 8. 越狱术语
        r"jailbreak",
        # 9. "Do Anything Now" jailbreak
        r"do anything now",
        # 10. DAN 越狱
        r"\bDAN\b",
        # 11. 无限制模式
        r"no restrictions",
        # 12. 无限制
        r"no limits",
        # 13. 越权/绕过
        r"bypass",
        # 14. 角色/模式切换
        r"(?:developer|admin|root|debug) mode",
        # 15. 覆盖原指令
        r"override (?:the )?(?:previous|prior|system)",
        # 16. 假装/模拟
        r"pretend (?:to be|you are)",
        # 17. 注入标记
        r"\[INST\]|<\|im_start\|>|<\|im_end\|>",
        # 18. 隐藏/嵌入指令
        r"(?:hidden|secret|covert) instructions?",
    }
)
"""15+ 已知 prompt injection 攻击模式正则集合 (全部 case-insensitive)"""


# 预编译正则 (性能优化: 避免每次 check 重复编译)
_COMPILED_INJECTION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE | re.MULTILINE) for p in INJECTION_PATTERNS
]


# ============ Canary 生成函数 ============


def generate_canary(
    strategy: CanaryStrategy = CanaryStrategy.SUFFIX,
    length: int = 16,
) -> str:
    """生成金丝雀 token

    格式: "moa_canary_xxxxxxxxxxxx" (length 个随机字符)

    Args:
        strategy: 注入策略 (影响 INVISIBLE 策略的编码方式)
        length: 随机 token 部分长度 (默认 16)

    Returns:
        str: canary token

    Note:
        - SUFFIX/PREFIX/MULTI: 返回明文 "moa_canary_xxxxx..."
        - INVISIBLE: 返回零宽字符编码的字符串,视觉不可见
          每个 token 字符用 4 个零宽字符编码 (base-4)
    """
    if length < 1:
        raise ValueError(f"length must be >= 1, got {length}")

    try:
        # 生成随机 token 部分
        token_chars = "".join(secrets.choice(_CANARY_ALPHABET) for _ in range(length))
        full_token = f"{CANARY_PREFIX}{token_chars}"

        if strategy == CanaryStrategy.INVISIBLE:
            # 用零宽字符编码整个 token (包括前缀)
            return _encode_invisible(full_token)
        return full_token
    except Exception:
        # 兜底: 返回固定格式 token (理论上 secrets 不会失败)
        return f"{CANARY_PREFIX}{'x' * length}"


def _encode_invisible(token: str) -> str:
    """将 token 用零宽字符编码

    每个 ASCII 字符编码为 4 个零宽字符 (因为 0xFF > 0x7F,
    取 7 位有效位,需要 ceil(7/2) = 4 个 base-4 数字)
    """
    encoded_parts: list[str] = []
    for ch in token:
        code = ord(ch)
        # 4 个零宽字符,每字符 2 bit,共 8 bit
        for shift in (6, 4, 2, 0):
            idx = (code >> shift) & 0b11
            encoded_parts.append(INVISIBLE_CHARS[idx])
    return "".join(encoded_parts)


def _decode_invisible(encoded: str) -> str:
    """解码零宽字符序列回原 token (用于 verify 和测试)

    4 个零宽字符 → 1 个 ASCII 字符
    """
    if len(encoded) % 4 != 0:
        return ""

    # 构建反向映射
    char_to_idx: dict[str, int] = {c: i for i, c in enumerate(INVISIBLE_CHARS)}

    decoded_chars: list[str] = []
    for i in range(0, len(encoded), 4):
        code = 0
        for j in range(4):
            zw = encoded[i + j]
            if zw not in char_to_idx:
                return ""  # 非零宽字符,解码失败
            code = (code << 2) | char_to_idx[zw]
        decoded_chars.append(chr(code))
    return "".join(decoded_chars)


# ============ Jaccard 相似度计算 ============


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """计算 Jaccard 相似度 = |A ∩ B| / |A ∪ B|

    Returns:
        float: 0.0 (完全不相交) - 1.0 (完全相同)
    """
    if not set_a and not set_b:
        return 1.0  # 两个空集视为完全相同
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union else 0.0


def _tokenize(text: str) -> set:
    """文本分词 (用于 Jaccard)

    规则: 提取所有连续 [a-zA-Z0-9_] 序列,小写化
    """
    return set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))


# ============ Canary 注入/检测器 ============


class CanaryDetector:
    """Prompt injection 金丝雀检测器

    用法:
        detector = CanaryDetector(strategy=CanaryStrategy.SUFFIX)
        injected, canary = detector.inject("原始 prompt")
        # ... 把 injected 发给 LLM, 拿到 response ...
        report = detector.check(response, canary)
        verdict = detector.classify_response(response, canary)
    """

    def __init__(
        self,
        strategy: CanaryStrategy = CanaryStrategy.SUFFIX,
        canary_length: int = 16,
    ) -> None:
        """初始化检测器

        Args:
            strategy: 默认 canary 注入策略
            canary_length: canary token 长度
        """
        self.strategy = strategy
        self.canary_length = canary_length

    def inject(self, prompt: str) -> tuple[str, str]:
        """注入 canary token 到 prompt 中

        Args:
            prompt: 原始 prompt

        Returns:
            Tuple[str, str]: (注入后 prompt, canary token)
        """
        try:
            canary = generate_canary(self.strategy, self.canary_length)

            if self.strategy == CanaryStrategy.SUFFIX:
                injected = f"{prompt}\n\n[ref: {canary}]"
            elif self.strategy == CanaryStrategy.PREFIX:
                injected = f"[ref: {canary}]\n\n{prompt}"
            elif self.strategy == CanaryStrategy.INVISIBLE:
                # 不可见 canary 直接混入 prompt (视觉上看不出区别)
                injected = f"{prompt}{canary}"
            elif self.strategy == CanaryStrategy.MULTI:
                # 三重注入: prefix + suffix + invisible
                invisible_canary = _encode_invisible(f"{CANARY_PREFIX}multi")
                injected = f"[ref: {canary}]\n\n{prompt}\n\n[ref: {canary}]{invisible_canary}"
            else:
                # 未知策略,降级为 SUFFIX
                injected = f"{prompt}\n\n[ref: {canary}]"

            return injected, canary
        except Exception:
            # 兜底: 返回原 prompt + 固定 canary
            fallback = f"{CANARY_PREFIX}{'x' * self.canary_length}"
            return f"{prompt}\n\n[ref: {fallback}]", fallback

    def check(self, response: str, expected_canary: str) -> dict[str, Any]:
        """检测 response 是否泄露 canary / 包含 injection 模式

        Args:
            response: LLM 响应文本
            expected_canary: 注入时使用的 canary token

        Returns:
            Dict[str, Any]: {
                "leaked": bool,            # 完整 canary 出现
                "partial_leak": bool,      # 前缀 ≥ 8 字符匹配
                "similarity": float,       # Jaccard 0-1
                "injection_indicators": List[str],  # 命中模式列表
            }
        """
        try:
            response_str = response or ""
            canary_str = expected_canary or ""

            # 1. 完整泄露检测
            # 对 INVISIBLE canary,先尝试解码再比对
            leaked = False
            if canary_str and canary_str in response_str:
                leaked = True
            elif canary_str and self.strategy == CanaryStrategy.INVISIBLE:
                # 检查是否包含编码后的零宽字符
                leaked = canary_str in response_str
            elif canary_str and self.strategy == CanaryStrategy.MULTI:
                # MULTI: 既要明文匹配,也要考虑可能只剩 invisible 部分
                leaked = canary_str in response_str

            # 2. 部分泄露检测 (前缀 ≥ 8 字符)
            partial_leak = False
            if canary_str and not leaked:
                # 取 canary 前 8 字符 (跳过 "moa_canary_" 前缀,看随机部分)
                # 实际策略: 取 canary 整体前 8 字符 (包括前缀一部分)
                prefix_len = min(8, len(canary_str))
                canary_prefix = canary_str[:prefix_len]
                if canary_prefix in response_str:
                    partial_leak = True

            # 3. Jaccard 相似度
            # 对 INVISIBLE 策略,先尝试解码 response 里的零宽字符
            response_for_sim = response_str
            if self.strategy == CanaryStrategy.INVISIBLE and canary_str:
                # 提取 response 中的零宽字符序列
                zw_pattern = re.compile(f"[{''.join(INVISIBLE_CHARS)}]+")
                matches = zw_pattern.findall(response_str)
                if matches:
                    # 尝试解码最长的零宽序列
                    longest = max(matches, key=len)
                    if len(longest) % 4 == 0:
                        decoded = _decode_invisible(longest)
                        if decoded:
                            response_for_sim = response_str + " " + decoded

            tokens_response = _tokenize(response_for_sim)
            tokens_canary = _tokenize(canary_str)
            similarity = _jaccard_similarity(tokens_response, tokens_canary)

            # 4. Injection 模式检测
            indicators: list[str] = []
            for pattern in _COMPILED_INJECTION_PATTERNS:
                if pattern.search(response_str):
                    indicators.append(pattern.pattern)

            return {
                "leaked": leaked,
                "partial_leak": partial_leak,
                "similarity": similarity,
                "injection_indicators": indicators,
            }
        except Exception as e:
            # 兜底: 返回安全默认值
            return {
                "leaked": False,
                "partial_leak": False,
                "similarity": 0.0,
                "injection_indicators": [],
                "error": str(e),
            }

    def classify_response(
        self,
        response: str,
        expected_canary: str,
    ) -> str:
        """对 response 做 3 档分类

        分类规则:
        - "compromised": canary 完整泄露 (LLM 直接回显了 canary)
        - "suspicious": 部分泄露 OR 命中 ≥1 injection 模式
        - "safe": 都不命中

        Args:
            response: LLM 响应文本
            expected_canary: 注入时使用的 canary token

        Returns:
            str: "safe" | "suspicious" | "compromised"
        """
        try:
            report = self.check(response, expected_canary)

            # 完整泄露 → 最高威胁等级
            if report["leaked"]:
                return "compromised"

            # 部分泄露 或 命中 injection 模式 → 可疑
            if report["partial_leak"] or report["injection_indicators"]:
                return "suspicious"

            return "safe"
        except Exception:
            # 兜底: 异常时保守判定为 suspicious
            return "suspicious"
