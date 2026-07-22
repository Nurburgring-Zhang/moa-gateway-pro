"""Harness Routing 3 档 (minimal / standard / thorough) + 5 优先级 (P0-P4)
+ Auto-Detection Rules (来自 06 moai-adk-multiagent)

真实启发式,非 mock。所有判定基于 task_description / file_count / single_domain
等真实信号,输出可下发的 HarnessConfig。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum

# ============ 枚举: 3 档 + 5 优先级 ============


class HarnessTier(str, Enum):
    """harness 强度档位"""

    MINIMAL = "minimal"
    STANDARD = "standard"
    THOROUGH = "thorough"


class Priority(str, Enum):
    """任务优先级 (P0=urgent ... P4=background)"""

    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


# ============ Dataclass ============


@dataclass
class RoutingDecision:
    """单次 route 决策的内部结构 (含 reason + agent_count)"""

    tier: HarnessTier
    priority: Priority
    agent_count: int
    tools_enabled: list[str]
    reason: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        d["priority"] = self.priority.value
        return d


@dataclass
class HarnessConfig:
    """最终下发的 harness 配置 (公开 API)"""

    tier: HarnessTier
    priority: Priority
    tools: list[str]
    max_iterations: int
    decision: RoutingDecision | None = None  # 调试/可观测

    def to_dict(self) -> dict:
        d = asdict(self)
        d["tier"] = self.tier.value
        d["priority"] = self.priority.value
        if self.decision is not None:
            d["decision"] = self.decision.to_dict()
        else:
            d["decision"] = None
        return d


# ============ 启发式关键词词典 (auto_detect_tier) ============

# MINIMAL 触发词
_MINIMAL_KEYWORDS = frozenset(
    {
        "fix",
        "bug",
        "typo",
        "small",
        "tiny",
        "trivial",
        "hotfix",
        "patch",
        "rename",
        "format",
        "lint",
        "comment",
        "docs-fix",
    }
)

# THOROUGH 触发词
_THOROUGH_KEYWORDS = frozenset(
    {
        "design",
        "architecture",
        "refactor",
        "redesign",
        "migrate",
        "overhaul",
        "restructure",
        "rewrite",
        "investigate",
        "research",
        "explore",
        "audit",
        "analyze",
        "plan",
        "strategy",
    }
)

# 优先级/严重度映射 (priority_from_severity)
_SEVERITY_TO_PRIORITY: dict[str, Priority] = {
    "critical": Priority.P0,
    "urgent": Priority.P0,
    "blocker": Priority.P0,
    "high": Priority.P1,
    "important": Priority.P1,
    "medium": Priority.P2,
    "normal": Priority.P2,
    "default": Priority.P2,
    "low": Priority.P3,
    "minor": Priority.P3,
    "backlog": Priority.P4,
    "someday": Priority.P4,
    "later": Priority.P4,
}

# 任务文本 → 优先级的隐式信号
_IMPLICIT_PRIORITY_KEYWORDS: dict[str, Priority] = {
    "urgent": Priority.P0,
    "asap": Priority.P0,
    "critical": Priority.P0,
    "blocker": Priority.P0,
    "important": Priority.P1,
    "high": Priority.P1,
    "backlog": Priority.P4,
    "someday": Priority.P4,
    "later": Priority.P4,
}

# 不同 tier 对应的迭代上限
_MAX_ITERATIONS: dict[HarnessTier, int] = {
    HarnessTier.MINIMAL: 3,
    HarnessTier.STANDARD: 8,
    HarnessTier.THOROUGH: 20,
}

# 不同 tier 对应 agent 数
_AGENT_COUNT: dict[HarnessTier, int] = {
    HarnessTier.MINIMAL: 1,
    HarnessTier.STANDARD: 3,
    HarnessTier.THOROUGH: 6,
}


# ============ 核心函数: route_request ============


def route_request(
    task_description: str,
    file_count: int = 0,
    single_domain: bool = True,
    is_bugfix: bool = False,
    is_docs: bool = False,
) -> HarnessConfig:
    """根据任务信号选择 harness 档位 + 优先级 + 工具集。

    启发式规则 (按优先级评估,先命中先用):
      1. P0 + bugfix           → MINIMAL (急救式修复)
      2. P2 + single_domain + file<=3 → MINIMAL (小型明确任务)
      3. 多文件 (file_count > 8) 或 多域 (!single_domain) → THOROUGH
      4. docs 任务            → STANDARD (偶尔降一档)
      5. default              → STANDARD

    task_description 会先尝试提取隐式优先级 (urgent/asap/backlog ...),
    提取不到默认 P2。
    """
    desc = (task_description or "").strip()
    priority = _extract_implicit_priority(desc)

    # rule 1: P0 + bugfix → MINIMAL
    if priority == Priority.P0 and is_bugfix:
        tier = HarnessTier.MINIMAL
        reason = "P0 bugfix: minimal hotfix path"
    # rule 2: P2 + small + single-domain → MINIMAL
    elif priority == Priority.P2 and single_domain and file_count <= 3 and file_count >= 0:
        tier = HarnessTier.MINIMAL
        reason = f"small scoped task: {file_count} file(s), single domain"
    # rule 3: 多文件 / 多域 → THOROUGH
    elif (file_count > 8) or (not single_domain):
        why = [] if single_domain else ["multi-domain"]
        if file_count > 8:
            why.append(f"{file_count} files")
        tier = HarnessTier.THOROUGH
        reason = "broad scope: " + ", ".join(why) if why else "broad scope"
    # rule 4: docs → STANDARD
    elif is_docs:
        tier = HarnessTier.STANDARD
        reason = "documentation task: standard flow"
    # rule 5: default
    else:
        tier = HarnessTier.STANDARD
        reason = "default standard flow"

    tools = tools_for_tier(tier)
    decision = RoutingDecision(
        tier=tier,
        priority=priority,
        agent_count=_AGENT_COUNT[tier],
        tools_enabled=list(tools),
        reason=reason,
    )
    return HarnessConfig(
        tier=tier,
        priority=priority,
        tools=tools,
        max_iterations=_MAX_ITERATIONS[tier],
        decision=decision,
    )


# ============ 核心函数: auto_detect_tier ============

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z\-]+")


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def auto_detect_tier(task: str, files: list[str] | None = None) -> HarnessTier:
    """根据 task 关键词 + 文件数自动判断 harness 档位。

    关键词权重:
      - "fix"/"typo"/"bug"/"hotfix"  → MINIMAL
      - "design"/"architecture"/"refactor" → THOROUGH
      - 文件数 > 8                   → THOROUGH
      - 默认                         → STANDARD
    """
    tokens = _tokenize(task)
    token_set = set(tokens)

    # thorough 关键词优先 (design 比 fix 更能说明复杂度)
    if token_set & _THOROUGH_KEYWORDS:
        return HarnessTier.THOROUGH
    # minimal 关键词
    if token_set & _MINIMAL_KEYWORDS:
        return HarnessTier.MINIMAL
    # 文件数兜底
    if files and len(files) > 8:
        return HarnessTier.THOROUGH
    return HarnessTier.STANDARD


# ============ 核心函数: priority_from_severity ============


def priority_from_severity(severity: str) -> Priority:
    """把严重度字符串 (critical/high/medium/low/backlog) 映射到 Priority。"""
    key = (severity or "").strip().lower()
    if key in _SEVERITY_TO_PRIORITY:
        return _SEVERITY_TO_PRIORITY[key]
    # 未识别 → P2 (普通)
    return Priority.P2


# ============ 核心函数: tools_for_tier ============

_TOOLS_MINIMAL = ["read_file", "search"]
_TOOLS_STANDARD = _TOOLS_MINIMAL + ["write_file", "edit", "bash"]
_TOOLS_THOROUGH = _TOOLS_STANDARD + ["run_tests", "web_search", "subagent"]


def tools_for_tier(tier: HarnessTier) -> list[str]:
    """返回该档位默认可用的工具列表 (深拷贝,避免外部修改)。"""
    if tier == HarnessTier.MINIMAL:
        return list(_TOOLS_MINIMAL)
    if tier == HarnessTier.STANDARD:
        return list(_TOOLS_STANDARD)
    if tier == HarnessTier.THOROUGH:
        return list(_TOOLS_THOROUGH)
    raise ValueError(f"unknown tier: {tier!r}")


# ============ 辅助: 从 task 文本提取隐式优先级 ============


def _extract_implicit_priority(task: str) -> Priority:
    """从 task 文本中嗅探优先级关键词;找不到默认 P2。"""
    if not task:
        return Priority.P2
    lower = task.lower()
    for kw, pri in _IMPLICIT_PRIORITY_KEYWORDS.items():
        # 单词边界匹配,避免 'low' 误匹配 'allow'
        if re.search(rf"\b{re.escape(kw)}\b", lower):
            return pri
    return Priority.P2


# ============ JSON 序列化辅助 ============


def config_to_json(cfg: HarnessConfig) -> str:
    """把 HarnessConfig 序列化为 JSON 字符串。"""
    return json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2)


def config_from_json(payload: str) -> HarnessConfig:
    """从 JSON 字符串反序列化 HarnessConfig。"""
    data = json.loads(payload)
    decision_data = data.get("decision")
    decision = None
    if decision_data is not None:
        decision = RoutingDecision(
            tier=HarnessTier(decision_data["tier"]),
            priority=Priority(decision_data["priority"]),
            agent_count=int(decision_data["agent_count"]),
            tools_enabled=list(decision_data["tools_enabled"]),
            reason=str(decision_data["reason"]),
        )
    return HarnessConfig(
        tier=HarnessTier(data["tier"]),
        priority=Priority(data["priority"]),
        tools=list(data["tools"]),
        max_iterations=int(data["max_iterations"]),
        decision=decision,
    )
