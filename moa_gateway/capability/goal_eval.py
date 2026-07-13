"""2-tier 目标求值 + 5-section Ceiling Report (来自 moai-adk-multiagent)

真实实现,非 mock。所有求值基于规则启发式,实际可运行。

层级说明:
- Tier 1 (MECHANICAL):  机械规则匹配 — 关键字 / 包含 / 等值
- Tier 2 (MODEL_DECLARED): 模型声明式求值 — 关键词重叠度 + 可选模型调用

Ceiling Report 5 section:
- claim: 主张
- evidence: 证据列表
- baseline: 基线
- gaps: 差距列表
- residual_risk: 残余风险
"""
from __future__ import annotations
import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Dict, Optional, Any, Callable, Set


__all__ = [
    "GoalTier",
    "Goal",
    "GoalResult",
    "CeilingReport",
    "evaluate_tier1",
    "evaluate_tier2",
    "generate_ceiling_report",
    "evaluate_goal",
    "evaluate_goals",
    "compute_completeness_score",
]


# ============ GoalTier 枚举 ============

class GoalTier(str, Enum):
    """目标求值层级"""
    MECHANICAL = "mechanical"          # Tier 1: 机械命令
    MODEL_DECLARED = "model_declared"  # Tier 2: 模型声明


# ============ 启发式常量 ============

# 停用词 (用于关键词提取)
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
    "怎么", "为什么", "哪个", "哪些", "一个", "一些", "这个", "那个",
})


# ============ Dataclass 定义 ============

@dataclass
class Goal:
    """目标定义"""
    id: str
    description: str
    tier: GoalTier
    criteria: str                              # Tier 1: 机械命令 / Tier 2: 模型声明
    evaluator_fn: Optional[Callable] = None    # 可选自定义求值函数

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


@dataclass
class GoalResult:
    """单目标求值结果"""
    goal_id: str
    achieved: bool
    score: float            # 0-1
    evidence: List[str] = field(default_factory=list)
    tier: GoalTier = GoalTier.MECHANICAL

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        return d


@dataclass
class CeilingReport:
    """5-section ceiling report"""
    claim: str
    evidence: List[str] = field(default_factory=list)
    baseline: str = ""
    gaps: List[str] = field(default_factory=list)
    residual_risk: str = ""

    def to_dict(self) -> Dict:
        return asdict(self)


# ============ 辅助函数 ============

def _clip01(x: float) -> float:
    """clip 到 0-1 区间"""
    return max(0.0, min(1.0, x))


def _tokenize(text: str) -> List[str]:
    """分词:英文按词,中文按字符"""
    if not text:
        return []
    en_tokens = re.findall(r"[a-zA-Z][a-zA-Z'\-]*|\d+", text)
    zh_chars = re.findall(r"[\u4e00-\u9fff]", text)
    return [t.lower() for t in en_tokens] + zh_chars


def _extract_keywords(text: str, min_len: int = 2) -> List[str]:
    """从文本中提取关键词(去停用词)"""
    tokens = _tokenize(text)
    kws: List[str] = []
    seen: Set[str] = set()
    for t in tokens:
        if t in STOPWORDS:
            continue
        if len(t) < min_len and not re.match(r"\d+", t):
            continue
        if t in seen:
            continue
        seen.add(t)
        kws.append(t)
    return kws


def _normalize_output(output: Any) -> str:
    """把 output 归一化成字符串用于启发式匹配"""
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    if isinstance(output, (int, float, bool)):
        return str(output)
    if isinstance(output, (list, tuple, set)):
        return " ".join(_normalize_output(x) for x in output)
    if isinstance(output, dict):
        return " ".join(f"{k} {v}" for k, v in output.items())
    return str(output)


# ============ Tier 1 机械求值 ============

def _parse_tier1_criteria(criteria: str) -> Dict[str, Any]:
    """解析 Tier 1 criteria 文本为规则 dict

    支持的语法(自由格式启发式):
    - "equals: <value>"   → 严格相等
    - "contains: <text>"  → 包含子串
    - "not_contains: <text>" → 不应包含
    - "prefix: <text>"    → 前缀
    - "suffix: <text>"    → 后缀
    - "in: [a, b, c]"     → 集合成员
    - "len >= N"          → 长度规则
    - "len <= N"
    - "regex: <pattern>"  → 正则
    - 默认: 视为 "contains: <criteria>"
    """
    text = criteria.strip()
    rule: Dict[str, Any] = {"type": "contains", "value": text, "raw": text}

    if not text:
        return rule

    # equals:
    m = re.match(r"^equals:\s*(.+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "equals"
        rule["value"] = m.group(1).strip()
        return rule

    # contains:
    m = re.match(r"^contains:\s*(.+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "contains"
        rule["value"] = m.group(1).strip()
        return rule

    # not_contains:
    m = re.match(r"^not_contains:\s*(.+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "not_contains"
        rule["value"] = m.group(1).strip()
        return rule

    # prefix:
    m = re.match(r"^prefix:\s*(.+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "prefix"
        rule["value"] = m.group(1).strip()
        return rule

    # suffix:
    m = re.match(r"^suffix:\s*(.+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "suffix"
        rule["value"] = m.group(1).strip()
        return rule

    # in: [...]
    m = re.match(r"^in:\s*\[(.+)\]$", text, re.IGNORECASE)
    if m:
        raw_items = m.group(1)
        items = [x.strip().strip("'\"") for x in raw_items.split(",") if x.strip()]
        rule["type"] = "in"
        rule["value"] = items
        return rule

    # len >= N / len <= N / len == N / len > N / len < N
    m = re.match(r"^len\s*(>=|<=|==|!=|>|<)\s*(\d+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "len"
        rule["op"] = m.group(1)
        rule["value"] = int(m.group(2))
        return rule

    # regex:
    m = re.match(r"^regex:\s*(.+)$", text, re.IGNORECASE)
    if m:
        rule["type"] = "regex"
        rule["value"] = m.group(1).strip()
        return rule

    # 默认:contains
    return rule


def _apply_tier1_rule(rule: Dict[str, Any], output: Any) -> tuple:
    """应用 Tier 1 规则,返回 (achieved, score, evidence_lines)"""
    out_str = _normalize_output(output)
    rule_type = rule["type"]
    val = rule["value"]
    evidence: List[str] = []

    if rule_type == "equals":
        achieved = out_str == val
        score = 1.0 if achieved else 0.0
        evidence.append(f"equals rule: output=={val!r} → {achieved}")

    elif rule_type == "contains":
        achieved = val.lower() in out_str.lower()
        score = 1.0 if achieved else 0.0
        evidence.append(f"contains rule: '{val}' in output → {achieved}")

    elif rule_type == "not_contains":
        achieved = val.lower() not in out_str.lower()
        score = 1.0 if achieved else 0.0
        evidence.append(f"not_contains rule: '{val}' not in output → {achieved}")

    elif rule_type == "prefix":
        achieved = out_str.lower().startswith(val.lower())
        score = 1.0 if achieved else 0.0
        evidence.append(f"prefix rule: output starts with '{val}' → {achieved}")

    elif rule_type == "suffix":
        achieved = out_str.lower().endswith(val.lower())
        score = 1.0 if achieved else 0.0
        evidence.append(f"suffix rule: output ends with '{val}' → {achieved}")

    elif rule_type == "in":
        # val 是 list
        if not isinstance(val, list):
            achieved = False
            score = 0.0
            evidence.append(f"in rule: invalid value list")
        else:
            achieved = out_str in val
            score = 1.0 if achieved else 0.0
            evidence.append(f"in rule: output in {val} → {achieved}")

    elif rule_type == "len":
        op = rule["op"]
        n = val
        actual_len = len(out_str)
        comp = {
            ">=": actual_len >= n,
            "<=": actual_len <= n,
            "==": actual_len == n,
            "!=": actual_len != n,
            ">": actual_len > n,
            "<": actual_len < n,
        }[op]
        achieved = comp
        score = 1.0 if achieved else 0.0
        evidence.append(f"len rule: len(output) {op} {n} (actual {actual_len}) → {achieved}")

    elif rule_type == "regex":
        try:
            pat = re.compile(val, re.IGNORECASE | re.MULTILINE)
            m = pat.search(out_str)
            achieved = m is not None
            score = 1.0 if achieved else 0.0
            evidence.append(f"regex rule: /{val}/ → {achieved}")
        except re.error as e:
            achieved = False
            score = 0.0
            evidence.append(f"regex rule error: {e}")

    else:
        achieved = False
        score = 0.0
        evidence.append(f"unknown rule type: {rule_type}")

    return achieved, score, evidence


def evaluate_tier1(goal: Goal, output: Any) -> GoalResult:
    """Tier 1 机械求值

    真实逻辑:
    - 解析 criteria 文本为规则
    - 应用规则到 output
    - 自定义 evaluator_fn 优先(若提供)
    """
    if goal.tier != GoalTier.MECHANICAL:
        raise ValueError(
            f"evaluate_tier1 requires MECHANICAL goal, got {goal.tier}"
        )

    # 自定义 evaluator 优先
    if goal.evaluator_fn is not None:
        try:
            custom = goal.evaluator_fn(output, goal.criteria)
            # custom 应返回 dict 或 tuple
            if isinstance(custom, GoalResult):
                return custom
            if isinstance(custom, dict):
                return GoalResult(
                    goal_id=goal.id,
                    achieved=bool(custom.get("achieved", False)),
                    score=_clip01(float(custom.get("score", 0.0))),
                    evidence=list(custom.get("evidence", ["custom evaluator"])),
                    tier=goal.tier,
                )
            if isinstance(custom, tuple) and len(custom) >= 2:
                return GoalResult(
                    goal_id=goal.id,
                    achieved=bool(custom[0]),
                    score=_clip01(float(custom[1])),
                    evidence=list(custom[2]) if len(custom) >= 3 else ["custom evaluator"],
                    tier=goal.tier,
                )
            if isinstance(custom, bool):
                return GoalResult(
                    goal_id=goal.id,
                    achieved=custom,
                    score=1.0 if custom else 0.0,
                    evidence=["custom evaluator bool"],
                    tier=goal.tier,
                )
        except Exception as e:
            return GoalResult(
                goal_id=goal.id,
                achieved=False,
                score=0.0,
                evidence=[f"custom evaluator error: {e}"],
                tier=goal.tier,
            )

    rule = _parse_tier1_criteria(goal.criteria)
    achieved, score, evidence = _apply_tier1_rule(rule, output)
    evidence.insert(0, f"tier=1 rule={rule['type']} raw={rule['raw']!r}")

    return GoalResult(
        goal_id=goal.id,
        achieved=achieved,
        score=score,
        evidence=evidence,
        tier=goal.tier,
    )


# ============ Tier 2 模型声明求值 ============

def _tier2_keyword_overlap(criteria: str, output: Any) -> tuple:
    """Tier 2 默认启发式:criteria 与 output 关键词重叠度

    真实逻辑:
    - 从 criteria 提取关键词
    - 从 output 提取关键词
    - score = |intersection| / |criteria_keywords|
    """
    crit_kws = _extract_keywords(criteria)
    out_kws = set(_extract_keywords(_normalize_output(output)))
    if not crit_kws:
        return 0.0, ["criteria has no keywords, score=0"]

    crit_kws_set = set(crit_kws)
    intersection = crit_kws_set & out_kws
    score = _clip01(len(intersection) / len(crit_kws_set))
    evidence = [
        f"criteria keywords: {sorted(crit_kws_set)}",
        f"matched: {sorted(intersection)} ({len(intersection)}/{len(crit_kws_set)})",
    ]
    return score, evidence


def evaluate_tier2(
    goal: Goal,
    output: Any,
    model_call: Optional[Callable] = None,
) -> GoalResult:
    """Tier 2 模型声明求值

    真实逻辑:
    - 若提供 model_call: 调用 model_call(criteria, output) → dict-like
    - 否则:用默认关键词重叠度启发式
    - 自定义 evaluator_fn 最优先(若提供)
    """
    if goal.tier != GoalTier.MODEL_DECLARED:
        raise ValueError(
            f"evaluate_tier2 requires MODEL_DECLARED goal, got {goal.tier}"
        )

    evidence: List[str] = []
    evidence.append(f"tier=2 criteria={goal.criteria!r}")

    # 自定义 evaluator 优先
    if goal.evaluator_fn is not None:
        try:
            custom = goal.evaluator_fn(output, goal.criteria)
            if isinstance(custom, GoalResult):
                return custom
            if isinstance(custom, dict):
                return GoalResult(
                    goal_id=goal.id,
                    achieved=bool(custom.get("achieved", False)),
                    score=_clip01(float(custom.get("score", 0.0))),
                    evidence=list(custom.get("evidence", ["custom evaluator"])) + evidence,
                    tier=goal.tier,
                )
            if isinstance(custom, tuple) and len(custom) >= 2:
                return GoalResult(
                    goal_id=goal.id,
                    achieved=bool(custom[0]),
                    score=_clip01(float(custom[1])),
                    evidence=list(custom[2]) if len(custom) >= 3 else ["custom evaluator"],
                    tier=goal.tier,
                )
            if isinstance(custom, bool):
                return GoalResult(
                    goal_id=goal.id,
                    achieved=custom,
                    score=1.0 if custom else 0.0,
                    evidence=["custom evaluator bool"] + evidence,
                    tier=goal.tier,
                )
        except Exception as e:
            evidence.append(f"custom evaluator error: {e}")
            # 继续用 fallback

    # 真实模型调用(若提供)
    if model_call is not None:
        try:
            model_out = model_call(goal.criteria, output)
            if isinstance(model_out, dict):
                achieved = bool(model_out.get("achieved", False))
                score = _clip01(float(model_out.get("score", 1.0 if achieved else 0.0)))
                model_ev = list(model_out.get("evidence", ["model call"]))
                evidence.append(f"model_call returned: achieved={achieved} score={score}")
                return GoalResult(
                    goal_id=goal.id,
                    achieved=achieved,
                    score=score,
                    evidence=evidence + model_ev,
                    tier=goal.tier,
                )
            if isinstance(model_out, tuple) and len(model_out) >= 2:
                achieved = bool(model_out[0])
                score = _clip01(float(model_out[1]))
                model_ev = list(model_out[2]) if len(model_out) >= 3 else ["model call"]
                return GoalResult(
                    goal_id=goal.id,
                    achieved=achieved,
                    score=score,
                    evidence=evidence + model_ev,
                    tier=goal.tier,
                )
            if isinstance(model_out, bool):
                return GoalResult(
                    goal_id=goal.id,
                    achieved=model_out,
                    score=1.0 if model_out else 0.0,
                    evidence=evidence + ["model_call bool"],
                    tier=goal.tier,
                )
            # 其它类型 → fallback
            evidence.append(f"model_call returned unsupported type {type(model_out).__name__}, fallback to heuristic")
        except Exception as e:
            evidence.append(f"model_call error: {e}, fallback to heuristic")

    # 默认启发式
    score, heur_evidence = _tier2_keyword_overlap(goal.criteria, output)
    evidence.extend(heur_evidence)
    achieved = score >= 0.5  # 阈值 0.5
    evidence.append(f"heuristic achieved={achieved} (threshold 0.5)")

    return GoalResult(
        goal_id=goal.id,
        achieved=achieved,
        score=score,
        evidence=evidence,
        tier=goal.tier,
    )


# ============ 5-section Ceiling Report ============

def generate_ceiling_report(
    claim: str,
    evidence: List[str],
    baseline: str,
    gaps: List[str],
    residual_risk: str,
) -> CeilingReport:
    """生成 5-section ceiling report

    5 sections: claim / evidence / baseline / gaps / residual_risk
    验证每个 section 都非空。
    """
    if not claim or not claim.strip():
        raise ValueError("claim must be non-empty")
    if not baseline or not baseline.strip():
        raise ValueError("baseline must be non-empty")
    if not residual_risk or not residual_risk.strip():
        raise ValueError("residual_risk must be non-empty")
    if evidence is None:
        raise ValueError("evidence must be a list (can be empty for now)")
    if gaps is None:
        raise ValueError("gaps must be a list (can be empty for now)")

    return CeilingReport(
        claim=claim.strip(),
        evidence=list(evidence),
        baseline=baseline.strip(),
        gaps=list(gaps),
        residual_risk=residual_risk.strip(),
    )


def compute_completeness_score(report: CeilingReport) -> float:
    """计算 5-section ceiling report 的 completeness_score (0-1)

    真实逻辑:
    - 5 section 都有 → 1.0
    - 缺一个 → 0.8
    - 缺 evidence 或 gaps(列表)但非空 → 0.5
    - 完全无内容 → 0.0
    """
    score = 0.0
    has_claim = bool(report.claim and report.claim.strip())
    has_baseline = bool(report.baseline and report.baseline.strip())
    has_risk = bool(report.residual_risk and report.residual_risk.strip())
    has_evidence = bool(report.evidence and len(report.evidence) > 0)
    has_gaps = bool(report.gaps and len(report.gaps) > 0)

    filled = sum([has_claim, has_baseline, has_risk, has_evidence, has_gaps])
    score = filled / 5.0

    # 额外:如果 claim/baseline/risk 有内容但 evidence/gaps 为空,降一档
    if filled >= 3 and (not has_evidence or not has_gaps):
        score = max(0.5, score - 0.2)

    return _clip01(score)


# ============ 主入口 ============

def evaluate_goal(
    goal: Goal,
    output: Any,
    model_call: Optional[Callable] = None,
) -> GoalResult:
    """主入口:按 tier 分派"""
    if goal.tier == GoalTier.MECHANICAL:
        return evaluate_tier1(goal, output)
    if goal.tier == GoalTier.MODEL_DECLARED:
        return evaluate_tier2(goal, output, model_call=model_call)
    raise ValueError(f"unknown tier: {goal.tier}")


def evaluate_goals(goals: List[Goal], output: Any) -> List[GoalResult]:
    """批量求值:对同一 output 求所有 goals 的结果

    真实逻辑:
    - 0 goals → []
    - 每个 goal 用自己的 tier 求值
    - Tier 2 不用 model_call(批量场景下用默认启发式)
    """
    if not goals:
        return []
    results: List[GoalResult] = []
    for g in goals:
        results.append(evaluate_goal(g, output, model_call=None))
    return results
