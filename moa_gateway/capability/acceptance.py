"""acceptance — Acceptance Tree (Given/When/Then) + EARS/GEARS 模式匹配 (来自 06 moai-adk-multiagent)

核心能力:
  1. AcceptanceCriterion 数据模型: G/W/T 三段式验收标准 (id + parent_id 支持嵌套)
  2. AcceptanceTree 树形容器: add/get/children/descendants + validate_ids (重复 + 格式)
  3. 5 GEARS 模式: Given/When/Then × 正常/异常 (侧重结构化验收)
  4. 6 EARS legacy 模式: UBIQUITOUS / EVENT_DRIVEN / STATE_DRIVEN / OPTIONAL / UNWANTED / TIMED
  5. parse_ears 启发式: 从自然语言文本匹配 EARS prefix,产出 AcceptanceCriterion
  6. validate_pattern: 判定一个 criterion 属于 GEARS 哪个 mode (EARS 不走本函数)
  7. JSON 序列化往返 (tree_to_dict / tree_from_dict)

设计原则:
  - 模仿 task_tree.py 风格: dataclass + 树容器 + 纯函数 + 序列化辅助
  - GEARS/EARS 模式判定基于真实关键字匹配 (given/when/then + EARS prefix 触发词)
  - 启发式 parse_ears 用正则逐行匹配,支持多行输入 (每行一个 criterion)
  - validate_pattern 严格区分: 给定完整 G/W/T → 归 GEARS 之一;否则 EARS
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


# ============ 模式枚举 ============
class GEARSPattern(str, Enum):
    """5 种 GEARS 模式 — Given/When/Then × 正常/异常"""
    GIVEN_NORMAL = "GIVEN_正常"
    WHEN_NORMAL = "WHEN_正常"
    THEN_NORMAL = "THEN_正常"
    GIVEN_ABNORMAL = "GIVEN_异常"
    WHEN_ABNORMAL = "WHEN_异常"


class EARSPattern(str, Enum):
    """6 种 EARS legacy 模式 — 来自 EARS (Easy Approach to Requirements Syntax)"""
    UBIQUITOUS = "UBIQUITOUS"   # 总是 (无前置条件)
    EVENT_DRIVEN = "EVENT_DRIVEN"  # 当 ... 时
    STATE_DRIVEN = "STATE_DRIVEN"  # 当 ... 状态
    OPTIONAL = "OPTIONAL"        # 可以
    UNWANTED = "UNWANTED"        # 若 ... 不该发生
    TIMED = "TIMED"              # 在 T 内


# ============ 数据模型 ============
@dataclass
class AcceptanceCriterion:
    """单个验收标准 (Given/When/Then 树节点)

    字段语义:
      - id: 唯一标识
      - given: 前置条件
      - when:  触发动作
      - then:  预期结果
      - parent_id: 父 criterion id;None 表示根
      - children_ids: 子 criterion id 列表(由 AcceptanceTree 维护)
      - pattern: 可选标注;若未指定则 validate_pattern 推断
    """
    id: str
    given: str
    when: str
    then: str
    parent_id: str | None = None
    children_ids: list[str] = field(default_factory=list)
    pattern: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AcceptanceCriterion:
        return cls(**d)


# ============ 树容器 ============
class AcceptanceTree:
    """AcceptanceCriterion 树形容器

    - 内部 _criteria: Dict[str, AcceptanceCriterion]
    - 父子关系由 AcceptanceCriterion.children_ids / parent_id 双向维护
    - 与 task_tree.py 保持一致的 API 风格
    """

    def __init__(self, root_id: str) -> None:
        self._criteria: dict[str, AcceptanceCriterion] = {}
        root = AcceptanceCriterion(
            id=root_id,
            given="(root)",
            when="(root)",
            then="(root)",
            parent_id=None,
        )
        self._criteria[root_id] = root

    # ---- CRUD ----
    def add_criterion(self, criterion: AcceptanceCriterion) -> None:
        """添加验收标准;若 parent_id 存在则自动挂到父节点的 children_ids"""
        if criterion.id in self._criteria:
            raise ValueError(f"duplicate criterion id: {criterion.id}")
        self._criteria[criterion.id] = criterion
        if criterion.parent_id is not None:
            parent = self._criteria.get(criterion.parent_id)
            if parent is None:
                raise ValueError(f"parent not found: {criterion.parent_id}")
            if criterion.id not in parent.children_ids:
                parent.children_ids.append(criterion.id)

    def get_criterion(self, ac_id: str) -> AcceptanceCriterion | None:
        return self._criteria.get(ac_id)

    def all_criteria(self) -> list[AcceptanceCriterion]:
        return list(self._criteria.values())

    # ---- 层级遍历 ----
    def get_children(self, ac_id: str) -> list[AcceptanceCriterion]:
        """直接子节点 (不含孙及以下)"""
        c = self._criteria.get(ac_id)
        if c is None:
            return []
        return [self._criteria[cid] for cid in c.children_ids if cid in self._criteria]

    def get_descendants(self, ac_id: str) -> list[AcceptanceCriterion]:
        """全部后代 (深度优先,不含自身)"""
        out: list[AcceptanceCriterion] = []

        def dfs(cur_id: str) -> None:
            for cid in self._criteria[cur_id].children_ids:
                if cid in self._criteria:
                    out.append(self._criteria[cid])
                    dfs(cid)

        if ac_id in self._criteria:
            dfs(ac_id)
        return out

    # ---- ID 校验 ----
    def validate_ids(self) -> list[str]:
        """返回所有错误信息 (空 list = 全部合法)。

        检查项:
          1. id 非空 + 只能含字母/数字/_/-, 长度 1..64
          2. 不重复
        """
        errors: list[str] = []
        pattern = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
        seen: dict[str, int] = {}
        for ac in self._criteria.values():
            if not ac.id:
                errors.append("empty criterion id")
                continue
            if not pattern.match(ac.id):
                errors.append(f"invalid id format: {ac.id!r}")
            seen[ac.id] = seen.get(ac.id, 0) + 1
        for ac_id, cnt in seen.items():
            if cnt > 1:
                errors.append(f"duplicate id: {ac_id} (×{cnt})")
        return errors


# ============ GEARS 模式判定 (纯函数) ============
_ABNORMAL_KEYWORDS = (
    "异常", "失败", "错误", "出错", "timeout", "超时", "reject", "deny",
    "fail", "error", "exception", "invalid", "throw", "raises",
)


def _has_abnormal(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in _ABNORMAL_KEYWORDS)


def validate_pattern(ac: AcceptanceCriterion) -> str:
    """判定一个 criterion 属于哪种 GEARS 模式。

    判定规则 (基于 given/when/then 字段填充情况):
      - 仅 when 非空  → WHEN_NORMAL / WHEN_ABNORMAL
      - 仅 given 非空 → GIVEN_NORMAL / GIVEN_ABNORMAL
      - then 非空     → THEN_NORMAL (必有结果段)
      - 全空          → 默认 THEN_NORMAL
    异常关键词出现时升级为 *_ABNORMAL。
    """
    g = (ac.given or "").strip()
    w = (ac.when or "").strip()
    t = (ac.then or "").strip()

    if w and not g and not t:
        return GEARSPattern.WHEN_ABNORMAL.value if _has_abnormal(w) else GEARSPattern.WHEN_NORMAL.value
    if g and not w and not t:
        return GEARSPattern.GIVEN_ABNORMAL.value if _has_abnormal(g) else GEARSPattern.GIVEN_NORMAL.value
    # then 非空 (含 G/W 齐全) → THEN_NORMAL
    if t:
        return GEARSPattern.THEN_NORMAL.value
    return GEARSPattern.THEN_NORMAL.value


# ============ EARS 启发式解析 (纯函数) ============
# 匹配顺序: STATE_DRIVEN (最具体) → TIMED → UNWANTED → OPTIONAL → EVENT_DRIVEN → UBIQUITOUS
_EARS_PATTERNS: tuple[tuple[EARSPattern, re.Pattern[str]], ...] = (
    (EARSPattern.STATE_DRIVEN, re.compile(
        r"^\s*(?:while|when)\s+(?:in\s+)?(?:[A-Za-z_][\w\-]*\s+)?(?:state|mode|status|condition)\b[^\n,]*,\s*(.+)$",
        re.IGNORECASE,
    )),
    (EARSPattern.TIMED, re.compile(
        r"^\s*(?:within|after|before)\s+(\d+\s*(?:ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?))\s*,\s*(.+)$",
        re.IGNORECASE,
    )),
    (EARSPattern.UNWANTED, re.compile(
        r"^\s*if\s+(.+?)\s*,?\s*(?:then\s+)?(?:shall|should|must|will|would)?\s*not\s+(.+)$",
        re.IGNORECASE,
    )),
    (EARSPattern.OPTIONAL, re.compile(
        r"^\s*(?:optionally|may|optional|可以)\s+(.+?)(?:\s*[,.;:]\s*(.+))?$",
        re.IGNORECASE,
    )),
    (EARSPattern.EVENT_DRIVEN, re.compile(
        r"^\s*when\s+(.+?)\s*,\s*(?:then\s+)?(.+)$",
        re.IGNORECASE,
    )),
    (EARSPattern.UBIQUITOUS, re.compile(
        r"^\s*(?:the\s+system\s+)?(?:shall|should|must|will|always|总是|始终)\s+(.+)$",
        re.IGNORECASE,
    )),
)


def _classify_line(line: str) -> tuple[EARSPattern, str, str, str] | None:
    """对单行启发式分类。返回 (pattern, given, when, then) 或 None。"""
    s = line.strip()
    if not s:
        return None
    for pattern, rx in _EARS_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        [g for g in m.groups() if g is not None]
        if pattern == EARSPattern.STATE_DRIVEN:
            action = m.group(1).strip()
            # 从匹配整体中提取 state 关键字前的 token
            full = s.lower()
            state_token = ""
            for kw in ("state", "mode", "status", "condition"):
                idx = full.find(kw)
                if idx >= 0:
                    tail = s[:idx].strip()
                    parts = tail.split()
                    state_token = parts[-1] if parts else kw
                    break
            return (pattern, f"state={state_token}", f"in_state={state_token}", action)
        if pattern == EARSPattern.TIMED:
            window, action = m.group(1).strip(), m.group(2).strip()
            return (pattern, f"timed_window={window}", f"within={window}", action)
        if pattern == EARSPattern.UNWANTED:
            trigger, forbidden = m.group(1).strip(), m.group(2).strip()
            return (pattern, f"trigger={trigger}", f"if={trigger}", f"NOT {forbidden}")
        if pattern == EARSPattern.OPTIONAL:
            main = m.group(1).strip()
            extra = (m.group(2) or "").strip()
            return (pattern, "(optional)", f"may={main}", extra or main)
        if pattern == EARSPattern.EVENT_DRIVEN:
            trigger, action = m.group(1).strip(), m.group(2).strip()
            return (pattern, f"trigger={trigger}", f"event={trigger}", action)
        if pattern == EARSPattern.UBIQUITOUS:
            action = m.group(1).strip()
            return (pattern, "(ubiquitous)", f"always={action}", action)
    return None


def parse_ears(text: str) -> list[AcceptanceCriterion]:
    """启发式从自然语言文本解析 EARS acceptance criteria。

    支持多行 (每行一个 criterion,空行跳过)。
    每条解析结果用行号作为 id (如 "ac-1", "ac-2" ...)。
    """
    out: list[AcceptanceCriterion] = []
    lines = text.splitlines()
    counter = 0
    for line in lines:
        classified = _classify_line(line)
        if classified is None:
            continue
        counter += 1
        pattern, given, when, then = classified
        out.append(AcceptanceCriterion(
            id=f"ac-{counter}",
            given=given,
            when=when,
            then=then,
            pattern=pattern.value,
        ))
    return out


# ============ JSON 序列化 ============
def tree_to_dict(tree: AcceptanceTree) -> dict[str, Any]:
    """把整棵验收树序列化为 dict。"""
    return {"criteria": [c.to_dict() for c in tree.all_criteria()]}


def tree_from_dict(d: dict[str, Any]) -> AcceptanceTree:
    """从 dict 反序列化出 AcceptanceTree。根 = parent_id 为 None 的那个。"""
    raw = d.get("criteria", [])
    items = [AcceptanceCriterion.from_dict(x) for x in raw]
    if not items:
        raise ValueError("no criteria in dict")
    root = next((c for c in items if c.parent_id is None), None)
    if root is None:
        raise ValueError("no root criterion (parent_id is None) in dict")
    tree = AcceptanceTree(root.id)
    tree._criteria[root.id] = root
    for c in items:
        if c.id == root.id:
            continue
        tree.add_criterion(c)
    return tree
