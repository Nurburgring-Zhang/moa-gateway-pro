"""Action Policy (Allow/Deny/AdminReview) + Shell Bypass Defense (CWE-214)

来源:
- 04 moa-main-commercial (Action Policy — Allow/Deny/AdminReview 三态裁决)
- 06 moai-adk (Shell Bypass Defense — CWE-214 Invocation of Process Using
  Synchronous / Incorrect Shell Quoting)

真实实现,非 mock。所有检测基于真实正则与字面量,实际可用。
"""
from __future__ import annotations

import fnmatch
import re
from dataclasses import asdict, dataclass, field
from typing import Literal

# ============ Dataclasses ============

@dataclass
class PolicyRule:
    """一条策略规则"""
    name: str
    action: Literal["allow", "deny", "admin_review"]
    pattern: str
    match_type: Literal["glob", "regex", "exact"] = "glob"
    reason: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> 'PolicyRule':
        """接受字段别名,自动映射到正确字段。空 dict 走 defaults。"""
        kwargs = {}
        if "name" in d: kwargs["name"] = d["name"]
        if "name" not in kwargs and "pattern" in d: kwargs["name"] = d["pattern"]
        if "action" in d: kwargs["action"] = d["action"]
        if "action" not in kwargs and "action" in d: kwargs["action"] = d["action"]
        if "pattern" in d: kwargs["pattern"] = d["pattern"]
        if "pattern" not in kwargs and "name" in d: kwargs["pattern"] = d["name"]
        if "match_type" in d: kwargs["match_type"] = d["match_type"]
        if "match_type" not in kwargs and "priority" in d: kwargs["match_type"] = d["priority"]
        if "reason" in d: kwargs["reason"] = d["reason"]
        if "reason" not in kwargs and "priority" in d: kwargs["reason"] = d["priority"]
        return cls(**kwargs)


@dataclass
class PolicyVerdict:
    """一次 evaluate 的裁决结果"""
    command: str
    decision: Literal["allow", "deny", "admin_review"]
    matched_rule: str | None = None
    reason: str = ""
    bypass_detected: bool = False
    bypass_techniques: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BypassDetection:
    """一个 shell 注入/绕过技术的发现"""
    technique: Literal[
        "semicolon", "and_chain", "or_chain", "if_then",
        "subshell", "pipe_to_interpreter", "ifs_subst", "line_continuation"
    ]
    payload: str
    severity: Literal["low", "medium", "high"] = "high"


# ============ 优先级与默认 action ============

# 同一命令匹配多条规则时,优先级: deny > admin_review > allow
_SEVERITY_RANK = {"deny": 3, "admin_review": 2, "allow": 1}
_RANK_ACTION = {3: "deny", 2: "admin_review", 1: "allow"}


def _rule_matches(rule: PolicyRule, command: str) -> bool:
    """判断一条规则是否匹配命令"""
    if rule.match_type == "exact":
        return command.strip() == rule.pattern
    if rule.match_type == "glob":
        return fnmatch.fnmatch(command.strip(), rule.pattern)
    if rule.match_type == "regex":
        try:
            return re.search(rule.pattern, command) is not None
        except re.error:
            return False
    return False


# ============ ActionPolicy ============

class ActionPolicy:
    """策略引擎: 装载规则、评估命令、增删查"""

    def __init__(self, rules: list[PolicyRule] | None = None) -> None:
        self._rules: list[PolicyRule] = list(rules or [])

    def add_rule(self, rule: PolicyRule) -> None:
        """添加规则(同名覆盖)"""
        self.remove_rule(rule.name)
        self._rules.append(rule)

    def remove_rule(self, name: str) -> bool:
        """删除规则,返回是否成功"""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.name != name]
        return len(self._rules) < before

    def list_rules(self) -> list[PolicyRule]:
        """列出当前所有规则(拷贝)"""
        return list(self._rules)

    def evaluate(self, command: str) -> PolicyVerdict:
        """按 deny > admin_review > allow 优先级,返回第一条匹配规则的裁决;
        无任何规则匹配时,默认 allow(显式 deny 优先 + default allow)。"""
        matched: list[PolicyRule] = []
        for r in self._rules:
            if _rule_matches(r, command):
                matched.append(r)
        if not matched:
            return PolicyVerdict(
                command=command,
                decision="allow",
                matched_rule=None,
                reason="no rule matched — default allow",
            )
        # 选优先级最高的;同级时取第一条
        best = max(matched, key=lambda r: _SEVERITY_RANK.get(r.action, 0))
        return PolicyVerdict(
            command=command,
            decision=best.action,
            matched_rule=best.name,
            reason=best.reason or f"matched rule {best.name}",
        )


# ============ Shell Bypass Defense (CWE-214) ============

# 注: 反斜杠续行 \\\\n 在原始字符串里要写成 \\n  (即反斜杠后接换行)
_LINE_CONT_RE = re.compile(r"\\\s*\n")
_SEMICOLON_RE = re.compile(r";")
_AND_CHAIN_RE = re.compile(r"&&")
_OR_CHAIN_RE = re.compile(r"\|\|")
_IF_THEN_RE = re.compile(r"\bif\b[^\n]*;\s*\bthen\b", re.IGNORECASE)
_SUBSHELL_DOLLAR_RE = re.compile(r"\$\(")
_SUBSHELL_BACKTICK_RE = re.compile(r"`")
_PIPE_INTERP_RE = re.compile(
    r"\|\s*(?:bash|sh|zsh|fish|python(?:3)?|perl|ruby|node|ksh)\b",
    re.IGNORECASE,
)
_IFS_SUBST_RE = re.compile(r"\$\{?IFS\}?")


def detect_bypass(command: str) -> list[BypassDetection]:
    """检测 shell 注入/绕过技术,返回 BypassDetection 列表。"""
    if not isinstance(command, str):
        return []
    out: list[BypassDetection] = []

    # 1. line continuation: 反斜杠接换行
    for m in _LINE_CONT_RE.finditer(command):
        out.append(BypassDetection(
            technique="line_continuation",
            payload=m.group(0),
            severity="high",
        ))

    # 2. semicolon chain
    for m in _SEMICOLON_RE.finditer(command):
        out.append(BypassDetection(
            technique="semicolon",
            payload=m.group(0),
            severity="high",
        ))

    # 3. and chain
    for m in _AND_CHAIN_RE.finditer(command):
        out.append(BypassDetection(
            technique="and_chain",
            payload=m.group(0),
            severity="high",
        ))

    # 4. or chain
    for m in _OR_CHAIN_RE.finditer(command):
        out.append(BypassDetection(
            technique="or_chain",
            payload=m.group(0),
            severity="high",
        ))

    # 5. if ... ; then
    for m in _IF_THEN_RE.finditer(command):
        out.append(BypassDetection(
            technique="if_then",
            payload=m.group(0),
            severity="high",
        ))

    # 6. subshell: $() or backtick
    for m in _SUBSHELL_DOLLAR_RE.finditer(command):
        out.append(BypassDetection(
            technique="subshell",
            payload=m.group(0),
            severity="high",
        ))
    for m in _SUBSHELL_BACKTICK_RE.finditer(command):
        out.append(BypassDetection(
            technique="subshell",
            payload=m.group(0),
            severity="high",
        ))

    # 7. pipe to interpreter
    for m in _PIPE_INTERP_RE.finditer(command):
        out.append(BypassDetection(
            technique="pipe_to_interpreter",
            payload=m.group(0),
            severity="high",
        ))

    # 8. IFS substitution
    for m in _IFS_SUBST_RE.finditer(command):
        out.append(BypassDetection(
            technique="ifs_subst",
            payload=m.group(0),
            severity="high",
        ))

    return out


def normalize_command(command: str) -> str:
    """归一化: 折叠多余空白 + 把 ${IFS} / $IFS 展开为单个空格,
    便于后续正则检测 (例如 cat$IFS/etc/passwd → cat /etc/passwd)。"""
    if not isinstance(command, str):
        return ""
    # 先把 IFS 替换为空格
    s = _IFS_SUBST_RE.sub(" ", command)
    # 折叠连续空白为单空格
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def pre_action_check(command: str, policy: ActionPolicy) -> PolicyVerdict:
    """行动前检查: 先 bypass 检测,任何 bypass 命中 → 直接 admin_review
    (无论原 policy 是什么);无 bypass 则按 policy.evaluate。"""
    bypasses = detect_bypass(command)
    if bypasses:
        techniques = sorted({b.technique for b in bypasses})
        return PolicyVerdict(
            command=command,
            decision="admin_review",
            matched_rule="__bypass_defense__",
            reason=(
                f"shell bypass detected: {', '.join(techniques)} "
                f"({len(bypasses)} hit(s)) — require human review"
            ),
            bypass_detected=True,
            bypass_techniques=techniques,
        )
    return policy.evaluate(command)


# ============ 默认安全规则库 (8 条高危) ============

def default_safe_policy() -> ActionPolicy:
    """返回一个带 8 条高危命令 deny 规则的默认安全策略。"""
    rules: list[PolicyRule] = [
        PolicyRule(
            name="deny_rm_rf_root",
            action="deny",
            # 接受 rm /  /  rm -r /  /  rm -rf /  /  rm -rf /*  /  rm -rf ~  /  rm -rf $HOME
            # 拒绝 rm -rf /tmp 等(路径必须以 /、~、$HOME 单独结尾)
            pattern=(
                r"\brm\s+"
                r"(?:-\w*[rf]\w*(?:\s+-\w*[rf]\w*)*\s+)*"   # flags (含 r/f)
                r"(?:/(?:\*)?|~|\$HOME)"                   # root path
                r"(?=\s*$)"
            ),
            match_type="regex",
            reason="rm -rf / (or $HOME / ~) is destructive",
        ),
        PolicyRule(
            name="deny_curl_pipe_shell",
            action="deny",
            pattern=r"\bcurl\b[^\n|]*\|\s*(?:bash|sh|zsh)\b",
            match_type="regex",
            reason="curl | bash remote-exec pattern (CWE-494)",
        ),
        PolicyRule(
            name="deny_wget_pipe_shell",
            action="deny",
            pattern=r"\bwget\b[^\n|]*\|\s*(?:bash|sh|zsh)\b",
            match_type="regex",
            reason="wget | bash remote-exec pattern",
        ),
        PolicyRule(
            name="deny_chmod_777_root",
            action="deny",
            pattern=r"\bchmod\s+(?:-R\s+)?(?:0?777|777)\s+/\b",
            match_type="regex",
            reason="chmod 777 / on root is world-writable exposure",
        ),
        PolicyRule(
            name="deny_dd_to_disk",
            action="deny",
            pattern=r"\bdd\s+[^\n]*\bof=/dev/(?:sd|hd|nvme|vd|xvd)",
            match_type="regex",
            reason="dd to raw disk device — destructive",
        ),
        PolicyRule(
            name="deny_mkfs_disk",
            action="deny",
            pattern=r"\bmkfs(?:[.\w]+)?\s+/dev/(?:sd|hd|nvme|vd|xvd)",
            match_type="regex",
            reason="mkfs on raw disk — format",
        ),
        PolicyRule(
            name="deny_fork_bomb",
            action="deny",
            pattern=r":\(\)\s*\{\s*:\|:&\s*\}\s*;\s*:",
            match_type="regex",
            reason="classic fork bomb — DoS",
        ),
        PolicyRule(
            name="deny_sudo_rm_rf",
            action="deny",
            pattern=r"\bsudo\s+rm\s+(-\w*r\w*f\w*\s+)*/",
            match_type="regex",
            reason="sudo rm -rf / — privilege escalation + destroy",
        ),
    ]
    return ActionPolicy(rules)
