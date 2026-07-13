"""Frozen Zone (4-enum: frozen-canonical / frozen-safety / evolvable-tuning / evolvable-experimental)
+ HARNESS_FROZEN_* 8 sentinels.

来源: 06 moai-adk-multiagent — 文件 / 路径的"冰冻区"分类与 harness 哨兵常量。

真实实现,非 mock。所有 8 个 HARNESS_FROZEN_* 常量字面量定义,FrozenRegistry
使用 dict 维护 path → FrozenEntry 映射,is_frozen / is_evolvable / can_modify
按 zone 枚举分类判定,assert_modifiable 在 frozen 时抛 FrozenZoneError。
"""
from __future__ import annotations

import json
import time
from enum import Enum
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict, field


# ============ HARNESS_FROZEN_* 8 sentinels ============
# 哨兵常量:用于在文件 / 模块 / 路径上挂载"冰冻声明",
# 运行时其它模块(如 harness-learner、配置加载器)读到这些字面量后会拒绝修改。
# 命名规则统一 HARNESS_FROZEN_<AREA>。

HARNESS_FROZEN_CANONICAL    = "HARNESS_FROZEN_CANONICAL"
HARNESS_FROZEN_SAFETY       = "HARNESS_FROZEN_SAFETY"
HARNESS_FROZEN_TUNING       = "HARNESS_FROZEN_TUNING"
HARNESS_FROZEN_EXPERIMENTAL = "HARNESS_FROZEN_EXPERIMENTAL"
HARNESS_FROZEN_LEARNER      = "HARNESS_FROZEN_LEARNER"
HARNESS_FROZEN_REGISTRY     = "HARNESS_FROZEN_REGISTRY"
HARNESS_FROZEN_BOOTSTRAP    = "HARNESS_FROZEN_BOOTSTRAP"
HARNESS_FROZEN_CONFIG       = "HARNESS_FROZEN_CONFIG"

# 8 个哨兵集中导出,便于测试 / 反射校验
ALL_HARNESS_FROZEN_SENTINELS: List[str] = [
    HARNESS_FROZEN_CANONICAL,
    HARNESS_FROZEN_SAFETY,
    HARNESS_FROZEN_TUNING,
    HARNESS_FROZEN_EXPERIMENTAL,
    HARNESS_FROZEN_LEARNER,
    HARNESS_FROZEN_REGISTRY,
    HARNESS_FROZEN_BOOTSTRAP,
    HARNESS_FROZEN_CONFIG,
]

# zone → 默认 sentinel 的映射;FrozenEntry 缺省时也用这张表兜底
_ZONE_DEFAULT_SENTINEL: Dict["Zone", str] = {}


# ============ Zone 枚举 ============

class Zone(str, Enum):
    """冰冻区 4 分类。

    - FROZEN_CANONICAL: 规范定本,任何修改需走版本化流程
    - FROZEN_SAFETY:    安全相关,任何修改需安全评审
    - EVOLVABLE_TUNING: 调参区,可被 harness / 调参器自动更新
    - EVOLVABLE_EXPERIMENTAL: 实验区,可被 harness / 实验框架自由读写
    """
    FROZEN_CANONICAL    = "frozen-canonical"
    FROZEN_SAFETY       = "frozen-safety"
    EVOLVABLE_TUNING    = "evolvable-tuning"
    EVOLVABLE_EXPERIMENTAL = "evolvable-experimental"

    @property
    def is_frozen(self) -> bool:
        """该 zone 是否属于冰冻(不可被 harness 直接修改)"""
        return self in (Zone.FROZEN_CANONICAL, Zone.FROZEN_SAFETY)

    @property
    def is_evolvable(self) -> bool:
        """该 zone 是否属于可演化(可被 harness 自动调参/实验)"""
        return self in (Zone.EVOLVABLE_TUNING, Zone.EVOLVABLE_EXPERIMENTAL)

    @property
    def default_sentinel(self) -> str:
        """zone 的默认 sentinel(用于给 FrozenEntry 兜底)"""
        mapping = {
            Zone.FROZEN_CANONICAL:      HARNESS_FROZEN_CANONICAL,
            Zone.FROZEN_SAFETY:         HARNESS_FROZEN_SAFETY,
            Zone.EVOLVABLE_TUNING:      HARNESS_FROZEN_TUNING,
            Zone.EVOLVABLE_EXPERIMENTAL: HARNESS_FROZEN_EXPERIMENTAL,
        }
        return mapping[self]


# 模块级填充映射(放在 Zone 定义之后,避免前向引用问题)
_ZONE_DEFAULT_SENTINEL = {
    Zone.FROZEN_CANONICAL:      HARNESS_FROZEN_CANONICAL,
    Zone.FROZEN_SAFETY:         HARNESS_FROZEN_SAFETY,
    Zone.EVOLVABLE_TUNING:      HARNESS_FROZEN_TUNING,
    Zone.EVOLVABLE_EXPERIMENTAL: HARNESS_FROZEN_EXPERIMENTAL,
}


# ============ Exception ============

class FrozenZoneError(Exception):
    """当对一个冰冻区路径发起修改时抛出。

    message 格式: "frozen zone violation: {path} [{sentinel}] — {reason}"
    """
    def __init__(self, path: str, sentinel: str, reason: str = "") -> None:
        self.path = path
        self.sentinel = sentinel
        self.reason = reason
        msg = f"frozen zone violation: {path} [{sentinel}]"
        if reason:
            msg += f" — {reason}"
        super().__init__(msg)


# ============ FrozenEntry ============

@dataclass
class FrozenEntry:
    """一条冰冻声明记录。"""
    path: str
    zone: Zone
    sentinel: str = ""
    reason: str = ""
    added_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        # 强制把 zone 字符串规范化为 Zone 枚举
        if not isinstance(self.zone, Zone):
            self.zone = Zone(self.zone)
        # sentinel 缺省 → 用 zone 的 default_sentinel
        if not self.sentinel:
            self.sentinel = _ZONE_DEFAULT_SENTINEL[self.zone]

    def to_dict(self) -> Dict:
        """JSON 友好字典"""
        d = asdict(self)
        d["zone"] = self.zone.value
        return d

    def to_json(self) -> str:
        """JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# ============ FrozenRegistry ============

class FrozenRegistry:
    """冰冻区注册表:path → FrozenEntry 的内存索引。

    同一 path 重复 add 时按"sentinel+reason 刷新 + added_at 更新"处理,
    不抛错(便于运行时声明漂移时自我修复)。
    """

    def __init__(self) -> None:
        self._entries: Dict[str, FrozenEntry] = {}

    # ----- mutators -----
    def add(self, entry: FrozenEntry) -> None:
        """注册一条冰冻声明(同 path 覆盖)"""
        if not isinstance(entry, FrozenEntry):
            raise TypeError(f"expected FrozenEntry, got {type(entry).__name__}")
        if not entry.path:
            raise ValueError("FrozenEntry.path must be non-empty")
        self._entries[entry.path] = entry

    def remove(self, path: str) -> bool:
        """移除一条冰冻声明,返回是否命中"""
        return self._entries.pop(path, None) is not None

    def clear(self) -> None:
        """清空所有声明(主要用于测试)"""
        self._entries.clear()

    # ----- queries -----
    def get_zone(self, path: str) -> Optional[Zone]:
        """查 path 的 zone;不存在返回 None"""
        e = self._entries.get(path)
        return e.zone if e is not None else None

    def get_sentinel(self, path: str) -> Optional[str]:
        """查 path 的 sentinel;不存在返回 None"""
        e = self._entries.get(path)
        return e.sentinel if e is not None else None

    def is_frozen(self, path: str) -> bool:
        """FROZEN_CANONICAL / FROZEN_SAFETY → True;其余(包括不存在) → False"""
        e = self._entries.get(path)
        if e is None:
            return False
        return e.zone.is_frozen

    def is_evolvable(self, path: str) -> bool:
        """EVOLVABLE_* → True;其余(包括不存在) → False"""
        e = self._entries.get(path)
        if e is None:
            return False
        return e.zone.is_evolvable

    def get(self, path: str) -> Optional[FrozenEntry]:
        """原始 entry 查询"""
        return self._entries.get(path)

    def list_paths(self) -> List[str]:
        """所有已注册 path(拷贝)"""
        return list(self._entries.keys())

    def list_entries(self) -> List[FrozenEntry]:
        """所有已注册 entry(拷贝)"""
        return list(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, path: str) -> bool:
        return path in self._entries

    # ----- serialization -----
    def to_json(self) -> str:
        """整张注册表序列化为 JSON 数组"""
        return json.dumps(
            [e.to_dict() for e in self._entries.values()],
            ensure_ascii=False,
            sort_keys=True,
        )

    def to_dict(self) -> Dict[str, Dict]:
        """整张注册表序列化为 dict[path → entry-dict]"""
        return {p: e.to_dict() for p, e in self._entries.items()}


# ============ 顶层工具函数 ============

def can_modify(path: str, zone: Zone) -> bool:
    """静态判定:给定 path 与所属 zone,是否允许被 harness 直接修改。

    - FROZEN_* → False
    - EVOLVABLE_* → True
    - 未知(非 Zone 实例) → 按 False 处理(保守)
    """
    if not isinstance(zone, Zone):
        return False
    return zone.is_evolvable


def assert_modifiable(path: str, registry: FrozenRegistry) -> None:
    """断言:对 path 的修改是被允许的。

    步骤:
    1. 查 registry 拿到 zone;若无注册,视为 EVOLVABLE(默认可写)
    2. zone.is_frozen → 抛 FrozenZoneError
    3. 否则静默通过
    """
    if not isinstance(registry, FrozenRegistry):
        raise TypeError(
            f"assert_modifiable requires FrozenRegistry, got {type(registry).__name__}"
        )
    zone = registry.get_zone(path)
    if zone is None:
        # 未注册 → 默认视为可演化区(放行)
        return
    if zone.is_frozen:
        sentinel = registry.get_sentinel(path) or zone.default_sentinel
        reason = ""
        e = registry.get(path)
        if e is not None:
            reason = e.reason
        raise FrozenZoneError(path=path, sentinel=sentinel, reason=reason)
    # EVOLVABLE_* → 放行


def classify(zone: Zone) -> str:
    """辅助:把 Zone 分类成 'frozen' / 'evolvable' / 'unknown' 字符串。"""
    if not isinstance(zone, Zone):
        return "unknown"
    if zone.is_frozen:
        return "frozen"
    if zone.is_evolvable:
        return "evolvable"
    return "unknown"
