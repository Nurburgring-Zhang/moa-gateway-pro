"""L0 闸门 — 不启 MoA 的机械验证任务 (来自 05 moa-skill)

成本控制核心:对简单/机械/琐碎任务直接回答,避免不必要的 MoA 调用。
真实实现,非 mock。所有判定基于正则 + AST 安全求值 + 启发式逻辑。
"""
from __future__ import annotations

import ast
import operator
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

__all__ = [
    "GateVerdict",
    "MECHANICAL_PATTERNS",
    "TRIVIAL_PATTERNS",
    "COMPLEX_PATTERNS",
    "gate",
    "_eval_arithmetic",
    "_extract_numbers",
    "_is_greeting",
    "handle_unit_convert",
    "handle_datetime",
    "handle_validator",
    "handle_encode",
]


# ============ GateVerdict 数据类 ============

@dataclass
class GateVerdict:
    """L0 闸门判定结果"""
    passed: bool  # True = 直接回答, False = 需要 MoA
    reason: str
    category: str  # "trivial" / "mechanical" / "factual" / "conversational" / "complex"
    confidence: float
    shortcut_answer: str  # 如果 passed=True,直接回答(可能 empty)
    estimated_complexity: int  # 1-10
    matched_pattern: str = ""  # 调试用:命中的具体模式
    metadata: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = asdict(self)
        return d


# ============ 已知机械验证模式(正则) ============

def _eval_arithmetic(expr: str) -> Tuple[bool, str]:
    """真实算术求值(支持 + - * / 括号)
    用 Python ast 安全求值,不用 eval
    """
    # 空白清理
    expr = expr.strip()
    if not expr:
        return (False, "")

    # 允许字符白名单
    if not re.match(r"^[\d\s\+\-\*\/\.\(\)%]+$", expr):
        return (False, "")

    # 危险序列:连续运算符(** 是合法的)
    # 规则: 字符级检查,只允许 [0-9 . + - * / % ( )] 和 ** // 组合
    # 禁止 ++, +-, */, etc.
    bad = re.findall(r"[+/%][+/%]", expr)
    if bad:
        return (False, "")
    # 单独的 - 和 + 可以作为一元运算符出现在 ( 后或 表达式开头
    # 但 ** // 必须配对正确
    # 检查无效的 * 组合(除了 **)
    cleaned = expr.replace("**", "#")
    if re.search(r"\*[^/\d\s\-(]", cleaned):
        return (False, "")
    # 检查 / 组合(除了 //)
    cleaned2 = cleaned.replace("//", "@")
    if re.search(r"/[^/\d\s]", cleaned2):
        return (False, "")
    if expr[0] in "+*/." or expr[-1] in "+-*/.":
        return (False, "")

    # 多余小数点
    for num in re.findall(r"\d+\.\d+\.\d+", expr):
        return (False, "")
    # 单数字多个小数点
    for num in re.findall(r"\d+(?:\.\d+)+", expr):
        parts = num.split(".")
        if len(parts) > 2:
            return (False, "")

    try:
        tree = ast.parse(expr, mode="eval")
    except (SyntaxError, ValueError):
        return (False, "")

    # AST 安全检查:只允许 Constant / BinOp / UnaryOp / 数字运算
    _BIN_OPS: Dict[type, Callable] = {
        ast.Add: operator.add,
        ast.Sub: operator.sub,
        ast.Mult: operator.mul,
        ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv,
        ast.Mod: operator.mod,
        ast.Pow: operator.pow,
    }
    _UNARY_OPS: Dict[type, Callable] = {
        ast.UAdd: operator.pos,
        ast.USub: operator.neg,
    }

    def _eval(node: ast.AST) -> float:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Div) and right == 0:
                raise ZeroDivisionError("division by zero")
            return _BIN_OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
            return _UNARY_OPS[type(node.op)](_eval(node.operand))
        raise ValueError(f"unsupported node: {type(node).__name__}")

    try:
        result = _eval(tree)
    except (ZeroDivisionError, ValueError, TypeError, RecursionError):
        return (False, "")

    # 格式化:整数显示为 int,浮点保留合理精度
    if result == int(result) and abs(result) < 1e16:
        return (True, str(int(result)))
    formatted = f"{result:.10g}"
    return (True, formatted)


def _handle_arithmetic(query: str) -> Tuple[bool, str, str]:
    """检测并计算算术表达式"""
    # 整个 query 即表达式
    if re.match(r"^[\d\s\+\-\*\/\.\(\)]+$", query.strip()):
        ok, ans = _eval_arithmetic(query.strip())
        if ok:
            return (True, ans, query.strip())

    # 包含关键词 "calculate/compute/evaluate/what is X"
    m = re.search(r"(?:calculate|compute|evaluate|what(?:'s| is)?)\s+([\d\s\+\-\*\/\.\(\)]+)", query, re.IGNORECASE)
    if m:
        expr = m.group(1).strip().rstrip("?=.,")
        if re.match(r"^[\d\s\+\-\*\/\.\(\)]+$", expr):
            ok, ans = _eval_arithmetic(expr)
            if ok:
                return (True, ans, expr)
    return (False, "", "")


def _handle_datetime(query: str) -> Tuple[bool, str, str]:
    """日期/时间查询 — 真实返回当前时间"""
    q = query.lower()
    triggers = ["today", "now", "current date", "current time", "current year",
                "what day", "what date", "what year"]
    if not any(t in q for t in triggers):
        return (False, "", "")
    now = datetime.now()
    # 简单回答
    if "year" in q:
        return (True, str(now.year), "year")
    if "date" in q or "today" in q or "what day" in q:
        return (True, now.strftime("%Y-%m-%d (%A)"), "date")
    # 默认 now
    return (True, now.strftime("%Y-%m-%d %H:%M:%S"), "datetime")


# 单位转换表 (to canonical unit)
_UNIT_TO_BASE: Dict[str, Tuple[str, float]] = {
    # length -> meter
    "m": ("length", 1.0), "meter": ("length", 1.0), "meters": ("length", 1.0), "metre": ("length", 1.0), "metres": ("length", 1.0),
    "km": ("length", 1000.0), "kilometer": ("length", 1000.0), "kilometers": ("length", 1000.0),
    "cm": ("length", 0.01), "centimeter": ("length", 0.01), "centimeters": ("length", 0.01),
    "mm": ("length", 0.001), "millimeter": ("length", 0.001), "millimeters": ("length", 0.001),
    "mi": ("length", 1609.344), "mile": ("length", 1609.344), "miles": ("length", 1609.344),
    "yd": ("length", 0.9144), "yard": ("length", 0.9144), "yards": ("length", 0.9144),
    "ft": ("length", 0.3048), "foot": ("length", 0.3048), "feet": ("length", 0.3048),
    "in": ("length", 0.0254), "inch": ("length", 0.0254), "inches": ("length", 0.0254),
    # mass -> gram
    "g": ("mass", 1.0), "gram": ("mass", 1.0), "grams": ("mass", 1.0),
    "kg": ("mass", 1000.0), "kilogram": ("mass", 1000.0), "kilograms": ("mass", 1000.0),
    "mg": ("mass", 0.001), "milligram": ("mass", 0.001),
    "lb": ("mass", 453.592), "lbs": ("mass", 453.592), "pound": ("mass", 453.592), "pounds": ("mass", 453.592),
    "oz": ("mass", 28.3495), "ounce": ("mass", 28.3495), "ounces": ("mass", 28.3495),
    # time -> second
    "s": ("time", 1.0), "sec": ("time", 1.0), "second": ("time", 1.0), "seconds": ("time", 1.0),
    "min": ("time", 60.0), "minute": ("time", 60.0), "minutes": ("time", 60.0),
    "h": ("time", 3600.0), "hr": ("time", 3600.0), "hour": ("time", 3600.0), "hours": ("time", 3600.0),
    "day": ("time", 86400.0), "days": ("time", 86400.0),
    # temperature handled separately
    "c": ("temperature", 1.0), "celsius": ("temperature", 1.0),
    "f": ("temperature", 1.0), "fahrenheit": ("temperature", 1.0),
    "k": ("temperature", 1.0), "kelvin": ("temperature", 1.0),
}


def _convert_temperature(value: float, from_u: str, to_u: str) -> Optional[float]:
    """温度转换: 先转 Celsius,再转目标"""
    from_u = from_u.lower()
    to_u = to_u.lower()
    # to celsius
    if from_u in ("c", "celsius"):
        c = value
    elif from_u in ("f", "fahrenheit"):
        c = (value - 32) * 5 / 9
    elif from_u in ("k", "kelvin"):
        c = value - 273.15
    else:
        return None
    # from celsius
    if to_u in ("c", "celsius"):
        return c
    if to_u in ("f", "fahrenheit"):
        return c * 9 / 5 + 32
    if to_u in ("k", "kelvin"):
        return c + 273.15
    return None


def handle_unit_convert(query: str) -> Tuple[bool, str, str]:
    """convert X unit to unit"""
    m = re.search(r"convert\s+(\d+\.?\d*)\s*([a-zA-Z]+)\s+to\s+([a-zA-Z]+)", query, re.IGNORECASE)
    if not m:
        return (False, "", "")
    try:
        value = float(m.group(1))
    except ValueError:
        return (False, "", "")
    from_u = m.group(2).lower()
    to_u = m.group(3).lower()
    # 保留原始 value 字符串表示(整数不要 .0)
    raw_value = m.group(1)
    # temperature
    if from_u in ("c", "celsius", "f", "fahrenheit", "k", "kelvin") and \
       to_u in ("c", "celsius", "f", "fahrenheit", "k", "kelvin"):
        res = _convert_temperature(value, from_u, to_u)
        if res is None:
            return (False, "", "")
        return (True, f"{raw_value} {from_u} = {res:.4g} {to_u}", m.group(0))
    # regular units
    if from_u not in _UNIT_TO_BASE or to_u not in _UNIT_TO_BASE:
        return (False, "", "")
    f_cat, f_factor = _UNIT_TO_BASE[from_u]
    t_cat, t_factor = _UNIT_TO_BASE[to_u]
    if f_cat == "temperature" or t_cat == "temperature":
        return (False, "", "")
    if f_cat != t_cat:
        return (False, "", f"category mismatch: {f_cat} vs {t_cat}")
    base = value * f_factor
    result = base / t_factor
    return (True, f"{raw_value} {from_u} = {result:.6g} {to_u}", m.group(0))


def handle_validator(query: str) -> Tuple[bool, str, str]:
    """validate json/regex/email/url/ip"""
    m = re.match(r"^validate\s+(json|regex|email|url|ip)$", query.strip(), re.IGNORECASE)
    if not m:
        return (False, "", "")
    target = m.group(1).lower()
    # 简单回答:提示用户提供内容
    return (True, f"please provide the {target} value to validate", m.group(0))


def handle_encode(query: str) -> Tuple[bool, str, str]:
    """what is the color/hex/md5/sha/base64 of X — 这些都需要上下文,直接转交 MoA
    这里只识别模式,实际不计算 (因为是部分 query)
    """
    m = re.match(r"^what\s+is\s+the\s+(color|hex|md5|sha\d*|base64)\s+of", query, re.IGNORECASE)
    if not m:
        return (False, "", "")
    # 需要具体值,转 MoA
    return (False, "", m.group(0))


# 机械模式表 (pattern, name, handler)
MECHANICAL_PATTERNS: List[Tuple[str, str, Optional[Callable]]] = [
    # 算术: 纯表达式 / "what is X" / "calculate X" — 宽松模式,handler 内自检
    (r"^[\d\s\+\-\*\/\.\(\)]+$|^(?:calculate|compute|evaluate|what(?:'s| is)?)\s+[\d\(]", "arithmetic", _handle_arithmetic),
    (r"today|now|current (?:date|time|year)|what\s+(?:day|date|year)", "datetime", _handle_datetime),
    (r"convert\s+(\d+\.?\d*)\s*(\w+)\s+to\s+(\w+)", "unit_convert", handle_unit_convert),
    (r"^validate\s+(json|regex|email|url|ip)$", "validator", handle_validator),
    (r"^what\s+is\s+the\s+(color|hex|md5|sha\d*|base64)\s+of", "encode", handle_encode),
]


# ============ Trivial 模式(不需要 MoA,直接 chat) ============

TRIVIAL_PATTERNS: List[str] = [
    r"^hi\b|^hello\b|^hey\b|^yo\b",
    r"^thanks?\b|^thank\s+you\b|^thx\b|^ty\b",
    r"^bye\b|^goodbye\b|^see\s+you\b",
    r"^yes\b|^no\b|^ok\b|^okay\b|^sure\b|^nope\b|^yep\b|^yeah\b",
    r"^how\s+are\s+you\b",
    r"^good\s+morning\b|^good\s+afternoon\b|^good\s+evening\b|^good\s+night\b",
]


# ============ 复杂模式(需要 MoA) ============

COMPLEX_PATTERNS: List[str] = [
    r"compare\s+.+\s+(?:vs\.?|versus|and|or)\s+",
    r"design\s+.+(?:system|architecture|api|database|service|module)",
    r"analyze\s+.+(?:code|article|paper|dataset|text|log|file|function)",
    r"write\s+(?:a\s+)?(?:function|class|module|story|essay|article|test|script|poem)",
    r"explain\s+(?:why|how).+(?:works?|functions?|happens?|possible|caused)",
    r"implement\s+.+",
    r"refactor\s+.+",
    r"debug\s+.+",
    r"review\s+(?:this|my|the)\s+(?:code|pr|pull\s+request)",
    r"create\s+(?:a\s+)?(?:plan|strategy|roadmap|proposal)",
    r"summarize\s+.+",
    r"translate\s+.+\s+to\s+",
    r"build\s+(?:a|an)\s+",
]


# ============ 安全 hook ============

# 危险模式:即便内容本身是复杂/对话,如果 query 包含恶意代码痕迹,直接转 MoA 让大模型处理
_DANGEROUS_PATTERNS: List[str] = [
    r"__import__",
    r"\bos\.system\b",
    r"\bsubprocess\b",
    r"\beval\s*\(",
    r"\bexec\s*\(",
    r"rm\s+-rf\s+/",
    r"\bshellcode\b",
    r"\bcode\s+injection\b",
]


# ============ 工具函数 ============

def _extract_numbers(text: str) -> List[float]:
    """从文本提取数字 (含负号、小数、百分号)"""
    result: List[float] = []
    for m in re.finditer(r"-?\d+\.?\d*", text):
        try:
            result.append(float(m.group(0)))
        except ValueError:
            continue
    return result


def _is_greeting(query: str) -> bool:
    """简单招呼判断"""
    q = query.strip().lower()
    if not q:
        return False
    return any(re.match(p, q) for p in TRIVIAL_PATTERNS)


def _has_dangerous_code(query: str) -> bool:
    return any(re.search(p, query, re.IGNORECASE) for p in _DANGEROUS_PATTERNS)


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


# ============ 主闸门函数 ============

def gate(query: str, context: Optional[List[Dict]] = None) -> GateVerdict:
    """L0 闸门主函数

    真实逻辑:
    1. 空 query → 复杂
    2. 危险代码模式 → 转 MoA (不能让本地求值)
    3. 机械模式(算术/日期/单位转换/校验)→ 真实计算,passed=True
    4. 算术无效语法 → 转 MoA
    5. Trivial 模式(招呼/yes-no)→ passed=True
    6. 复杂模式(对比/设计/分析)→ passed=False
    7. 默认 conversational / 长 query → passed=False
    """
    if not isinstance(query, str):
        query = str(query) if query is not None else ""
    q = query.strip()

    # 1. 空
    if not q:
        return GateVerdict(
            passed=False,
            reason="empty query",
            category="conversational",
            confidence=0.99,
            shortcut_answer="",
            estimated_complexity=1,
            matched_pattern="empty",
        )

    # 2. 危险代码:不本地求值,转 MoA
    if _has_dangerous_code(q):
        return GateVerdict(
            passed=False,
            reason="dangerous code pattern detected; refuse local eval, route to MoA",
            category="complex",
            confidence=0.95,
            shortcut_answer="",
            estimated_complexity=8,
            matched_pattern="dangerous_code",
        )

    # 3. 机械模式
    for pattern, name, handler in MECHANICAL_PATTERNS:
        if re.search(pattern, q, re.IGNORECASE):
            if handler is None:
                # 标记但不直接计算
                continue
            try:
                ok, answer, matched = handler(q)
            except Exception:
                ok, answer, matched = False, "", ""
            if ok:
                return GateVerdict(
                    passed=True,
                    reason=f"mechanical pattern matched: {name}",
                    category="mechanical",
                    confidence=0.95,
                    shortcut_answer=answer,
                    estimated_complexity=1,
                    matched_pattern=name,
                )
            # 算术匹配但求值失败:语法无效,转 MoA
            if name == "arithmetic":
                return GateVerdict(
                    passed=False,
                    reason="arithmetic pattern matched but expression invalid",
                    category="complex",
                    confidence=0.7,
                    shortcut_answer="",
                    estimated_complexity=3,
                    matched_pattern="arithmetic_invalid",
                )
            # 其他 handler 返回 False,继续看下一个 pattern

    # 5. Trivial 模式
    ql = q.lower()
    for pattern in TRIVIAL_PATTERNS:
        if re.match(pattern, ql):
            return GateVerdict(
                passed=True,
                reason="trivial pattern (greeting/yes-no/acknowledgement)",
                category="trivial",
                confidence=0.92,
                shortcut_answer=_make_trivial_reply(q),
                estimated_complexity=1,
                matched_pattern="trivial",
            )

    # 6. 复杂模式
    for pattern in COMPLEX_PATTERNS:
        if re.search(pattern, ql, re.IGNORECASE):
            return GateVerdict(
                passed=False,
                reason=f"complex pattern matched: {pattern}",
                category="complex",
                confidence=0.85,
                shortcut_answer="",
                estimated_complexity=7,
                matched_pattern="complex",
            )

    # 7. 长度启发式:长 query → 复杂
    wc = _word_count(q)
    if wc > 60:
        return GateVerdict(
            passed=False,
            reason=f"long query ({wc} words), likely complex",
            category="complex",
            confidence=0.75,
            shortcut_answer="",
            estimated_complexity=8,
            matched_pattern="length",
        )

    # 8. 默认 conversational → 复杂
    return GateVerdict(
        passed=False,
        reason="default conversational/ambiguous — needs MoA",
        category="conversational",
        confidence=0.65,
        shortcut_answer="",
        estimated_complexity=5,
        matched_pattern="default",
    )


def _make_trivial_reply(query: str) -> str:
    ql = query.strip().lower()
    if re.match(r"^hi\b|^hello\b|^hey\b|^yo\b", ql):
        return "Hello! How can I help you today?"
    if re.match(r"^thanks?\b|^thank\s+you\b|^thx\b|^ty\b", ql):
        return "You're welcome!"
    if re.match(r"^bye\b|^goodbye\b|^see\s+you\b", ql):
        return "Goodbye! Have a great day."
    if re.match(r"^yes\b|^yep\b|^yeah\b|^sure\b", ql):
        return "Got it."
    if re.match(r"^no\b|^nope\b", ql):
        return "Understood."
    if re.match(r"^ok\b|^okay\b", ql):
        return "OK."
    if re.match(r"^how\s+are\s+you\b", ql):
        return "I'm doing well, thanks for asking! How can I help?"
    if re.match(r"^good\s+morning\b", ql):
        return "Good morning!"
    if re.match(r"^good\s+afternoon\b", ql):
        return "Good afternoon!"
    if re.match(r"^good\s+evening\b", ql):
        return "Good evening!"
    if re.match(r"^good\s+night\b", ql):
        return "Good night!"
    return "Acknowledged."
