"""Output Wrapping — 把 untrusted tool 输出包成 <untrusted_tool_output> 标签 (来自参考表 S-38)

防 prompt injection 来自工具输出回流 — 核心思路:
1. wrap_output() 把工具返回内容用 XML 标签隔离,标注 source / trust / length
2. unwrap_output() 还原 — 校验标签完整性后提取内容
3. sanitize_for_prompt() 在嵌入 prompt 前再次清洗已知 injection 模式
4. needs_wrapping() 启发式判断内容是否需要 wrap (URL / code / JSON 等)

XML 转义: 对 "</untrusted_tool_output>" 和 "<" 提前转义,防止 untrusted 内容
含闭合标签提前终止 wrapper,导致后续内容被模型误读为 system / user message。
"""
from __future__ import annotations

import json
import re
from enum import Enum

# ============ TrustLevel ============

class TrustLevel(str, Enum):
    """工具输出信任等级"""
    TRUSTED = "trusted"        # 系统内部 / 受信工具
    SEMI_TRUSTED = "semi"      # 半信 — 如 RAG 召回、内置 API
    UNTRUSTED = "untrusted"    # 不可信 — 外部 fetch、用户输入、web 抓取


# ============ Injection 模式 (15+ 条,与 prompt_canary 对齐) ============

# 复用 prompt_canary.INJECTION_PATTERNS (如存在),否则用本地 fallback。
# 兼容两种格式:
#   1. FrozenSet[str] of regex strings (prompt_canary 当前实现)
#   2. List[Tuple[str, str]] of (pattern_id, regex)
_INJECTION_RAW: list[tuple[str, str]] = []
try:
    from moa_gateway.capability.prompt_canary import (
        INJECTION_PATTERNS as _EXTERNAL_INJECTION_PATTERNS,  # type: ignore
    )
    _items = list(_EXTERNAL_INJECTION_PATTERNS)
    if _items and isinstance(_items[0], tuple):
        # (id, regex, ...) — take first two
        _INJECTION_RAW = [(t[0], t[1]) for t in _items if len(t) >= 2]
    else:
        # str 列表 — 用 index 作为 id
        _INJECTION_RAW = [(f"PCP{i:02d}", s) for i, s in enumerate(_items)]
except Exception:
    _INJECTION_RAW = []

if not _INJECTION_RAW:
    _INJECTION_RAW = [
        # (pattern_id, regex)
        ("IGNORE_PREVIOUS", r"(?i)ignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|prompts?)"),
        ("DISREGARD_SYSTEM", r"(?i)disregard\s+(?:the\s+)?system\s+prompt"),
        ("YOU_ARE_NOW", r"(?i)you\s+are\s+now\s+(?:a|an|the)\s+"),
        ("ACT_AS", r"(?i)act\s+as\s+(?:a|an|the)\s+"),
        ("PRETEND_TO_BE", r"(?i)pretend\s+(?:to\s+be|you\s+are)"),
        ("ROLE_OVERRIDE", r"(?i)system\s*:\s*you\s+are"),
        ("FORGET_INSTRUCTIONS", r"(?i)forget\s+(?:all\s+)?(?:your\s+)?instructions"),
        ("NEW_INSTRUCTIONS", r"(?i)new\s+instructions?\s*[:=]"),
        ("JAILBREAK_DAN", r"(?i)\bDAN\b.*(?:do\s+anything|now\s+you)"),
        ("PROMPT_LEAK", r"(?i)(?:reveal|show|print|output)\s+(?:your\s+)?(?:system\s+)?prompt"),
        ("END_MARKER_INJECT", r"<\|im_end\|>|<\|endoftext\|>|###\s*Instruction|###\s*Response"),
        ("MARKDOWN_HEADER_INJECT", r"(?im)^#{1,6}\s*(?:system|assistant|user|instruction|prompt)\s*[:：]"),
        ("XML_TAG_INJECT", r"</?system[^>]*>|</?assistant[^>]*>|</?user[^>]*>"),
        ("CODE_BLOCK_INJECT", r"```(?:system|assistant|user)\b"),
        ("TOOL_CALL_INJECT", r'(?i)"(?:tool|function)_call"\s*:\s*\{'),
    ]

# 始终追加中文 injection 模式(无论外部是否提供)
_EXTRA_PATTERNS: list[tuple[str, str]] = [
    ("CHINESE_INJECT", r"(忽略|无视|忘记|不理会|不要遵守).{0,8}(指令|规则|提示|命令|设定|限制)"),
    ("CHINESE_OVERRIDE", r"(你现在是|请扮演|从现在开始|新的指令|新的指令[:：])"),
]
_INJECTION_RAW = _INJECTION_RAW + _EXTRA_PATTERNS

INJECTION_PATTERNS: list[tuple[str, str]] = _INJECTION_RAW


# ============ 编译正则(模块加载时一次) ============

_COMPILED_INJECTION: list[tuple[str, re.Pattern[str]]] = [
    (pid, re.compile(pat, re.IGNORECASE | re.MULTILINE)) for pid, pat in INJECTION_PATTERNS
]


# ============ XML 转义 ============

_XML_ESCAPE_MAP = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&apos;",
}


def _xml_escape_attr(s: str) -> str:
    """转义 attribute 值(防引号/尖括号提前闭合)"""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;"))


def _xml_escape_content(s: str) -> str:
    """转义内容 — 阻止 untrusted 内容伪造 wrapper 标签

    关键防御点:
    1. <untrusted_tool_output ...> (开标签) — 必须转义成 &lt;...&gt;
       否则 unwrap 会被欺骗
    2. </untrusted_tool_output> (闭标签) — 同样必须转义
    3. & — 先转义,避免后续注入产生 &lt; 二次转义
    4. 通用 < / > — 一并转义防 XSS / 注入

    策略: 转义所有 < 和 > 字符。这样:
      - </untrusted_tool_output> → &lt;/untrusted_tool_output&gt;
      - <script> → &lt;script&gt;
      - 任何后续的 XML 解析器都不会把它当作标签
    """
    # 先转义 & (避免后续注入产生 &lt; 的二次转义)
    s = s.replace("&", "&amp;")
    # 转义所有 < 和 > — 简单粗暴,安全
    s = s.replace("<", "&lt;").replace(">", "&gt;")
    return s


# ============ wrap_output ============

_WRAP_HEADER_FMT = '<untrusted_tool_output source="{source}" trust="{trust}" length="{length}"{truncated_attr}>\n'
_WRAP_FOOTER = "\n</untrusted_tool_output>"
_TRUNCATED_TAG = '<truncated total_length="{n}" />'


def wrap_output(
    content: str,
    source: str,
    trust: TrustLevel = TrustLevel.UNTRUSTED,
    max_length: int = 8192,
) -> str:
    """把 untrusted tool 输出包成 <untrusted_tool_output> 标签

    Args:
        content: 工具原始输出
        source: 来源标识(如 "web_fetch" / "rag_search" / "user_input")
        trust: 信任等级
        max_length: 截断阈值(超过则截断并加 <truncated /> 标记)

    Returns:
        包好标签的字符串,可直接拼入 prompt

    Raises:
        TypeError: content / source 不是 str
    """
    try:
        if not isinstance(content, str):
            content = str(content)
        if not isinstance(source, str):
            source = str(source)
        if max_length is None or max_length < 1:
            max_length = 8192

        original_length = len(content)
        truncated = False
        if original_length > max_length:
            content = content[:max_length]
            truncated = True

        truncated_attr = ""
        if truncated:
            truncated_attr = f' truncated="true" total_length="{original_length}"'

        # 转义 source(防 attribute injection)
        safe_source = _xml_escape_attr(source)
        safe_trust = _xml_escape_attr(trust.value)

        header = _WRAP_HEADER_FMT.format(
            source=safe_source,
            trust=safe_trust,
            length=original_length,
            truncated_attr=truncated_attr,
        )
        # 内容做 XML 转义(防闭合标签攻击)
        safe_content = _xml_escape_content(content)
        wrapped = f"{header}{safe_content}{_WRAP_FOOTER}"

        if truncated:
            # 截断标记放在 footer 前
            truncated_marker = _TRUNCATED_TAG.format(n=original_length)
            wrapped = wrapped[: -len(_WRAP_FOOTER)] + truncated_marker + _WRAP_FOOTER
        return wrapped
    except Exception as e:
        # 兜底:失败时仍返回可识别的 wrapper,不让 raw 内容泄露到 prompt
        return (
            f'<untrusted_tool_output source="__wrap_error__" trust="untrusted" '
            f'length="0" error="{_xml_escape_attr(type(e).__name__)}">\n'
            f'__wrap_failed__: {_xml_escape_attr(str(e)[:200])}\n'
            f'</untrusted_tool_output>'
        )


# ============ unwrap_output ============

# 解析 header: <untrusted_tool_output source="..." trust="..." length="..." [truncated="true" total_length="..."]>
_HEADER_RE = re.compile(
    r'<untrusted_tool_output\s+source="(?P<source>[^"]*)"\s+'
    r'trust="(?P<trust>[^"]*)"\s+'
    r'length="(?P<length>\d+)"'
    r'(?P<extra>(?:\s+truncated="(?:true|false)"(?:\s+total_length="\d+")?)*)'
    r'\s*>'
)
_TRUNCATED_INSIDE_RE = re.compile(
    r'<truncated\s+total_length="(?P<n>\d+)"\s*/>'
)
_FOOTER = "</untrusted_tool_output>"


def unwrap_output(wrapped: str) -> dict[str, str] | None:
    """从 wrap_output 生成的字符串还原内容

    Returns:
        {content, source, trust, length, truncated, total_length} 或 None(格式错误)
    """
    if not isinstance(wrapped, str) or "<untrusted_tool_output" not in wrapped:
        return None
    try:
        m = _HEADER_RE.search(wrapped)
        if not m:
            return None
        start = m.end()
        end = wrapped.find(_FOOTER, start)
        if end < 0:
            return None

        # 必须没有嵌套的 wrapper 标签(防御性检查)
        body = wrapped[start:end]
        if "<untrusted_tool_output" in body:
            return None

        # 取 truncated 标记(在剥换行前,因为 truncated tag 可能在 body 末尾的 \n 之前)
        truncated_inside = _TRUNCATED_INSIDE_RE.search(body)
        if truncated_inside:
            # truncated 标记之前是 content
            body = body[: truncated_inside.start()]
            truncated = "true"
            total_length = truncated_inside.group("n")
        else:
            # header 上有无 truncated 属性
            extra = m.group("extra") or ""
            if 'truncated="true"' in extra:
                truncated = "true"
                # 从 extra 提取 total_length
                tl_match = re.search(r'total_length="(\d+)"', extra)
                total_length = tl_match.group(1) if tl_match else m.group("length")
            else:
                truncated = "false"
                total_length = m.group("length")

        # 去掉我们 wrap 时添加的 leading/trailing \n(格式: header\n{content}\nfooter)
        if body.startswith("\n"):
            body = body[1:]
        if body.endswith("\n"):
            body = body[:-1]

        # 反转义内容(最小集)
        content = _xml_unescape_content(body)

        return {
            "content": content,
            "source": m.group("source"),
            "trust": m.group("trust"),
            "length": m.group("length"),
            "truncated": truncated,
            "total_length": total_length,
        }
    except Exception:
        return None


def _xml_unescape_content(s: str) -> str:
    """反向 XML 转义(只处理 wrap 时引入的序列)"""
    s = s.replace("&lt;/", "</")
    s = s.replace("&lt;", "<")
    s = s.replace("&gt;", ">")
    s = s.replace("&quot;", '"')
    s = s.replace("&apos;", "'")
    s = s.replace("&amp;", "&")
    return s


# ============ sanitize_for_prompt ============

def sanitize_for_prompt(content: str, aggressive: bool = False) -> str:
    """清洗已知 injection 模式,在嵌入 prompt 前最后一道防线

    Args:
        content: 待清洗文本
        aggressive: True 时更激进 — 移除所有 <...> 标签,替换 backticks 内容,
                    限制连续换行 / 移除 Markdown 标题注入

    Returns:
        清洗后字符串
    """
    if not isinstance(content, str):
        content = str(content)
    try:
        out = content

        # 阶段 1: 替换已知 injection 模式(无论 aggressive)
        for pid, pat in _COMPILED_INJECTION:
            if pid in ("END_MARKER_INJECT", "MARKDOWN_HEADER_INJECT"):
                # 这些用 redacted 占位符
                out = pat.sub(f"[REDACTED:{pid}]", out)
            else:
                out = pat.sub(f"[REDACTED:{pid}]", out)

        if aggressive:
            # 阶段 2a: 移除所有 <...> 标签(包括 script、iframe、unknown)
            out = re.sub(r"<[^>]+>", "[TAG-REMOVED]", out)
            # 阶段 2b: 替换 backticks 包裹的内容 ```...```
            out = re.sub(r"```[^\n]*\n.*?```", "[CODE-BLOCK-REMOVED]", out, flags=re.DOTALL)
            out = re.sub(r"`[^`]+`", "[INLINE-CODE-REMOVED]", out)
            # 阶段 2c: 替换可能的 role 标签 (system:/assistant:/user:)
            out = re.sub(
                r"(?im)^(system|assistant|user)\s*[:：]\s*",
                r"[ROLE:\1] ",
                out,
            )
            # 阶段 2d: 把超长空白压成单个
            out = re.sub(r"\n{4,}", "\n\n\n", out)
            # 阶段 2e: 把 "ignore previous" 类残余再 strip 一次大小写无关
            out = re.sub(r"(?i)ignore\s+previous", "[REDACTED:ignore-prev]", out)
        else:
            # 非 aggressive: 只对 system/assistant XML 标签加占位
            out = re.sub(
                r"</?(?:system|assistant|user)(?:\s[^>]*)?>",
                "[ROLE-TAG]",
                out,
            )

        return out
    except Exception:
        # 兜底:失败时返回 "[SANITIZE-FAILED]" + 长度信息
        return f"[SANITIZE-FAILED len={len(content)}]"


# ============ needs_wrapping ============

_NEEDS_WRAPPING_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE),     # URL
    re.compile(r"```[\s\S]*?```"),                            # code block
    re.compile(r"`[^`\n]+`"),                                 # inline code
    re.compile(r"\{[\s\S]*?\}"),                              # JSON-ish {...}
    re.compile(r"<[a-zA-Z][^>]*>"),                           # XML/HTML tag
    re.compile(r"(?i)(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|above)"),
    re.compile(r"(?i)(?:system|assistant|user)\s*[:：]"),
    re.compile(r"<\|\s*(?:im_end|endoftext|end_of_turn)\s*\|>"),
]


def needs_wrapping(content: str) -> bool:
    """启发式判断内容是否需要 wrap (含 URL / code / JSON / 注入特征等)

    Args:
        content: 待判断文本

    Returns:
        True 表示内容含 untrusted 标志,建议 wrap
    """
    if not isinstance(content, str):
        return False
    if not content:
        return False
    try:
        # 显式 JSON
        stripped = content.strip()
        if stripped and stripped[0] in "{[" and stripped[-1] in "}]":
            try:
                json.loads(stripped)
                return True
            except (ValueError, TypeError):
                pass
        # 正则特征
        return any(pat.search(content) for pat in _NEEDS_WRAPPING_PATTERNS)
    except Exception:
        # 出错就保守地返回 True(宁可多 wrap)
        return True


# ============ 便捷聚合函数 ============

def safe_wrap(
    content: str,
    source: str,
    trust: TrustLevel = TrustLevel.UNTRUSTED,
    max_length: int = 8192,
    aggressive_sanitize: bool = False,
) -> str:
    """wrap + sanitize 一步到位 — 给上层 prompt 拼装用"""
    if aggressive_sanitize:
        content = sanitize_for_prompt(content, aggressive=True)
    return wrap_output(content, source, trust, max_length)


__all__ = [
    "TrustLevel",
    "INJECTION_PATTERNS",
    "wrap_output",
    "unwrap_output",
    "sanitize_for_prompt",
    "needs_wrapping",
    "safe_wrap",
]
