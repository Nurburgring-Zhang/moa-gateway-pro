"""7-day Grace Window — FAIL 合并后 7 天仅警告不阻塞 + Audit Gate 5 步协议扩展

来源:
- 06 moai-adk-multiagent (7-day grace window — 失败合并后宽限期,仅警告不阻塞)
- 04 moa-main-commercial (Audit Gate 5 步协议:HASH → CACHE_CHECK → INVOKE → ROUTE → PERSIST)

设计要点:
- 时间戳用 time.time() 浮点秒,at 参数可注入(便于测试)
- grace_until = failed_at + grace_seconds
- should_block 规则: enabled=False → False; passed → False;
  failed & at < grace_until → False (在 grace 期,仅警告);
  failed & at >= grace_until → True (超 grace,阻塞)
- grace_status: "passing" / "in_grace" / "blocking"
- 真实时间逻辑,非 mock;JSON 序列化原生支持
"""
from __future__ import annotations
import json
import time
import uuid
import threading
from typing import List, Optional, Dict
from dataclasses import dataclass, field, asdict


# ============ Dataclasses ============

@dataclass
class CheckResult:
    """单次检查的结果记录"""
    check_id: str
    name: str
    passed: bool
    failed_at: Optional[float] = None
    grace_until: Optional[float] = None

    def to_dict(self) -> Dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


@dataclass
class GraceConfig:
    """Grace window 配置"""
    grace_seconds: float = 7 * 86400  # 7 天
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.grace_seconds < 0:
            self.grace_seconds = 0.0


# ============ CheckRegistry ============

class CheckRegistry:
    """检查注册表 + grace window 状态机"""

    def __init__(self, config: Optional[GraceConfig] = None) -> None:
        self._config: GraceConfig = config or GraceConfig()
        self._checks: Dict[str, CheckResult] = {}
        self._lock = threading.Lock()

    # ---------- 注册 / 记录 ----------

    def register(self, name: str) -> str:
        """注册一个 check,返回 check_id。"""
        check_id = str(uuid.uuid4())
        with self._lock:
            self._checks[check_id] = CheckResult(
                check_id=check_id,
                name=str(name),
                passed=True,
                failed_at=None,
                grace_until=None,
            )
        return check_id

    def record_pass(self, check_id: str) -> None:
        """记录一次通过:清空 failed_at / grace_until,passed=True。"""
        with self._lock:
            cr = self._checks.get(check_id)
            if cr is None:
                return
            cr.passed = True
            cr.failed_at = None
            cr.grace_until = None

    def record_fail(self, check_id: str, at: Optional[float] = None) -> None:
        """记录一次失败:设 failed_at + grace_until = failed_at + grace_seconds。"""
        ts = at if at is not None else time.time()
        with self._lock:
            cr = self._checks.get(check_id)
            if cr is None:
                return
            cr.passed = False
            cr.failed_at = ts
            cr.grace_until = ts + self._config.grace_seconds

    # ---------- 状态查询 ----------

    def should_block(self, check_id: str, at: Optional[float] = None) -> bool:
        """判断给定 check_id 当前(at 时刻)是否应当阻塞。

        规则:
            - enabled=False → False
            - passed → False
            - failed & at < grace_until → False (在 grace 期,仅警告)
            - failed & at >= grace_until → True (超 grace,阻塞)
            - 不存在 check_id → False
        """
        if not self._config.enabled:
            return False
        cr = self._checks.get(check_id)
        if cr is None:
            return False
        if cr.passed:
            return False
        if cr.failed_at is None or cr.grace_until is None:
            return False
        ts = at if at is not None else time.time()
        return ts >= cr.grace_until

    def get_warnings(self, at: Optional[float] = None) -> List[CheckResult]:
        """列出所有 failed 但仍在 grace 期内的 check (即应警告、不阻塞)。"""
        ts = at if at is not None else time.time()
        out: List[CheckResult] = []
        with self._lock:
            for cr in self._checks.values():
                if cr.passed:
                    continue
                if cr.failed_at is None or cr.grace_until is None:
                    continue
                if ts < cr.grace_until:
                    out.append(CheckResult(
                        check_id=cr.check_id,
                        name=cr.name,
                        passed=cr.passed,
                        failed_at=cr.failed_at,
                        grace_until=cr.grace_until,
                    ))
        return out

    def get_all(self) -> List[CheckResult]:
        """返回所有 check 的浅拷贝列表。"""
        with self._lock:
            return [
                CheckResult(
                    check_id=cr.check_id,
                    name=cr.name,
                    passed=cr.passed,
                    failed_at=cr.failed_at,
                    grace_until=cr.grace_until,
                )
                for cr in self._checks.values()
            ]

    def get(self, check_id: str) -> Optional[CheckResult]:
        """返回单个 check 的浅拷贝;不存在则 None。"""
        with self._lock:
            cr = self._checks.get(check_id)
            if cr is None:
                return None
            return CheckResult(
                check_id=cr.check_id,
                name=cr.name,
                passed=cr.passed,
                failed_at=cr.failed_at,
                grace_until=cr.grace_until,
            )

    def clear(self) -> None:
        """清空所有 check。"""
        with self._lock:
            self._checks.clear()

    # ---------- 配置 ----------

    @property
    def config(self) -> GraceConfig:
        return self._config

    def set_enabled(self, enabled: bool) -> None:
        self._config.enabled = bool(enabled)

    def set_grace_seconds(self, seconds: float) -> None:
        if seconds < 0:
            seconds = 0.0
        self._config.grace_seconds = float(seconds)

    # ---------- JSON 导出 ----------

    def export_json(self) -> str:
        """导出全部 check 为 JSON 数组字符串。"""
        with self._lock:
            data = [
                CheckResult(
                    check_id=cr.check_id,
                    name=cr.name,
                    passed=cr.passed,
                    failed_at=cr.failed_at,
                    grace_until=cr.grace_until,
                ).to_dict()
                for cr in self._checks.values()
            ]
        return json.dumps(data, ensure_ascii=False, sort_keys=True)


# ============ Module-level helpers ============

def grace_status(check_id: str, registry: CheckRegistry, at: Optional[float] = None) -> str:
    """返回 check_id 当前 grace 状态字符串。

    Returns:
        "passing"  — check 通过或不存在(无阻塞语义)
        "in_grace" — failed 但仍在 grace 期(警告)
        "blocking" — failed 且已超 grace(阻塞)
    """
    cr = registry.get(check_id)
    if cr is None or cr.passed:
        return "passing"
    if cr.failed_at is None or cr.grace_until is None:
        return "passing"
    ts = at if at is not None else time.time()
    if ts < cr.grace_until:
        return "in_grace"
    return "blocking"
