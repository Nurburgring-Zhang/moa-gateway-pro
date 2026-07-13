"""config_stack — 8 层配置合并栈 + 5 个 Permission Mode (来自 06 moai-adk-multiagent)

核心能力:
  1. ConfigLayer 8 层枚举: POLICY > USER > PROJECT > LOCAL > PLUGIN > SKILL > SESSION > BUILTIN
  2. ConfigEntry 数据模型: key / value / source / explicit
  3. ConfigStack: 8 层 set/get/unset/get_with_source/to_dict,按优先级返回
  4. merge_layers: 多层 dict 按优先级合并为单层视图
  5. PermissionMode 5 模式: DEFAULT / ACCEPT_EDITS / BYPASS_PERMISSIONS / PLAN / BUBBLE
  6. PermissionRegistry: glob 模式匹配 + default 兜底
  7. JSON 序列化往返

设计原则:
  - 优先级用 IntEnum,值越小越高(POLICY=0 最高,BUILTIN=7 最低)
  - ConfigStack 内部按 layer 维护一个 Dict[layer][key] = ConfigEntry,get 时按 0..7 扫描
  - explicit 字段仅作元数据保留:优先级仍由 layer 决定,但 explicit=False 视为默认
  - PermissionRegistry 用 fnmatch 做 glob 匹配,顺序按 set_rule 插入顺序(先到先得)
  - 所有算法基于真实数据结构(无 mock、无 hardcoded)
"""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from enum import Enum, IntEnum
from typing import List, Dict, Optional, Any, Tuple
import fnmatch


# ============ 配置层枚举 ============
class ConfigLayer(IntEnum):
    """8 层配置来源,值越小优先级越高

    合并栈(高 -> 低):
      0 POLICY           组织/合规策略(最高)
      1 USER             用户全局偏好
      2 PROJECT          项目级配置
      3 LOCAL            仓库本地 override(.git 风格)
      4 PLUGIN           插件注入
      5 SKILL            skill 文件提供
      6 SESSION          当前会话临时
      7 BUILTIN          内置默认值(最低)
    """
    POLICY = 0
    USER = 1
    PROJECT = 2
    LOCAL = 3
    PLUGIN = 4
    SKILL = 5
    SESSION = 6
    BUILTIN = 7


# ============ 权限模式枚举 ============
class PermissionMode(str, Enum):
    """5 种工具权限模式

    DEFAULT:            走 LLM/规则默认判定
    ACCEPT_EDITS:       自动接受文件编辑
    BYPASS_PERMISSIONS: 跳过所有权限检查
    PLAN:               仅规划模式,任何写操作都需要确认
    BUBBLE:             隔离沙箱,所有副作用在 bubble 内
    """
    DEFAULT = "default"
    ACCEPT_EDITS = "accept_edits"
    BYPASS_PERMISSIONS = "bypass_permissions"
    PLAN = "plan"
    BUBBLE = "bubble"


# ============ 数据模型 ============
@dataclass
class ConfigEntry:
    """单层配置项

    - key: 配置键
    - value: 配置值(任意类型)
    - source: 来自哪一层
    - explicit: True=显式 set,False=从默认/隐式载入
    """
    key: str
    value: Any
    source: ConfigLayer
    explicit: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["source"] = self.source.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ConfigEntry":
        d2 = dict(d)
        d2["source"] = ConfigLayer(int(d["source"]))
        return cls(**d2)


@dataclass
class PermissionRule:
    """权限规则:tool_pattern -> mode

    - tool_pattern: fnmatch glob(e.g. "Bash", "Bash:*", "Write*")
    - mode: 命中时使用的 PermissionMode
    - reason: 规则说明
    """
    tool_pattern: str
    mode: PermissionMode
    reason: str

    def matches(self, tool_name: str) -> bool:
        return fnmatch.fnmatchcase(tool_name, self.tool_pattern)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PermissionRule":
        d2 = dict(d)
        d2["mode"] = PermissionMode(d["mode"])
        return cls(**d2)


# ============ 配置栈 ============
class ConfigStack:
    """8 层配置合并容器

    内部存储:self._layers: Dict[int, Dict[str, ConfigEntry]]
    key 仍是 int(layer.value),便于快速按层扫描。
    """

    def __init__(self) -> None:
        self._layers: Dict[int, Dict[str, ConfigEntry]] = {
            layer.value: {} for layer in ConfigLayer
        }

    # ---- 写入 ----
    def set(self, key: str, value: Any, layer: ConfigLayer, explicit: bool = True) -> None:
        """设置一个 key 在指定 layer 的值

        - 同一 layer 内 set 会覆盖
        - explicit=False 表示隐式/默认载入,显式查询时仍按 layer 优先级生效
        """
        if not isinstance(layer, ConfigLayer):
            raise TypeError(f"layer must be ConfigLayer, got {type(layer).__name__}")
        self._layers[layer.value][key] = ConfigEntry(
            key=key, value=value, source=layer, explicit=explicit
        )

    # ---- 读取 ----
    def get(self, key: str, default: Any = None) -> Any:
        """按 8 层优先级返回最高层的值;都没有则返回 default"""
        for layer in ConfigLayer:  # IntEnum 0..7 自然顺序
            entry = self._layers[layer.value].get(key)
            if entry is not None:
                return entry.value
        return default

    def get_with_source(self, key: str) -> Tuple[Any, ConfigLayer]:
        """返回 (value, source_layer);不存在时 (None, None)"""
        for layer in ConfigLayer:
            entry = self._layers[layer.value].get(key)
            if entry is not None:
                return entry.value, entry.source
        return None, None  # type: ignore[return-value]

    def get_entry(self, key: str, layer: ConfigLayer) -> Optional[ConfigEntry]:
        """拿到指定 layer 的原始 ConfigEntry(否则 None)"""
        return self._layers[layer.value].get(key)

    # ---- 删除 ----
    def unset(self, key: str, layer: Optional[ConfigLayer] = None) -> int:
        """删除 key

        - layer=None: 从所有 8 层删除,返回总删除条数
        - layer=具体层: 只删该层,返回 0/1
        """
        if layer is None:
            n = 0
            for layer_dict in self._layers.values():
                if key in layer_dict:
                    del layer_dict[key]
                    n += 1
            return n
        return 1 if self._layers[layer.value].pop(key, None) is not None else 0

    # ---- 视图 ----
    def to_dict(self) -> Dict[str, Any]:
        """合并后视图:key -> 最高优先级的 value

        同时保留 _entries 字段,记录每个 key 实际胜出层(便于审计)。
        """
        merged: Dict[str, Any] = {}
        entries: Dict[str, Dict[str, Any]] = {}
        for layer in ConfigLayer:
            for k, entry in self._layers[layer.value].items():
                if k not in merged:
                    merged[k] = entry.value
                    entries[k] = entry.to_dict()
        return {"merged": merged, "entries": entries}

    def layers_snapshot(self) -> Dict[str, Dict[str, Any]]:
        """导出每层的原始键值(便于调试/序列化)"""
        return {
            layer.name: {k: e.to_dict() for k, e in self._layers[layer.value].items()}
            for layer in ConfigLayer
        }

    def load_snapshot(self, snap: Dict[str, Dict[str, Any]]) -> None:
        """从 layers_snapshot 恢复;未知 layer 名静默忽略"""
        self._layers = {layer.value: {} for layer in ConfigLayer}
        for name, items in snap.items():
            try:
                layer = ConfigLayer[name]
            except KeyError:
                continue
            for k, ed in items.items():
                self._layers[layer.value][k] = ConfigEntry.from_dict(ed)

    def __len__(self) -> int:
        """合并后的 key 数(去重)"""
        seen: set = set()
        for layer_dict in self._layers.values():
            seen.update(layer_dict.keys())
        return len(seen)


# ============ 合并函数 ============
def merge_layers(layers_data: Dict[ConfigLayer, Dict[str, Any]]) -> Dict[str, Any]:
    """按 8 层优先级合并多层 dict

    - layers_data: {ConfigLayer.POLICY: {...}, ConfigLayer.USER: {...}, ...}
    - 返回单层 dict,高优先级 key 覆盖低优先级(POLICY 压住 BUILTIN)
    - 不存在的 layer 不参与
    - 输入 dict 顺序无关:始终按 ConfigLayer 枚举序(高->低)写入
    """
    out: Dict[str, Any] = {}
    for layer in reversed(list(ConfigLayer)):  # 7..0:BUILTIN 先,POLICY 后写覆盖
        layer_dict = layers_data.get(layer, {})
        if not layer_dict:
            continue
        for k, v in layer_dict.items():
            out[k] = v
    return out


# ============ 权限注册表 ============
class PermissionRegistry:
    """工具级权限规则注册表

    匹配规则:
      - 按 set_rule 插入顺序遍历
      - 第一个 tool_pattern 命中 tool_name 的规则胜出
      - 都不命中 → 返回 self._default_mode
    """

    def __init__(self, default_mode: PermissionMode = PermissionMode.DEFAULT) -> None:
        if not isinstance(default_mode, PermissionMode):
            raise TypeError(f"default_mode must be PermissionMode, got {type(default_mode).__name__}")
        self._default_mode: PermissionMode = default_mode
        self._rules: List[PermissionRule] = []

    def set_rule(self, tool_pattern: str, mode: PermissionMode, reason: str = "") -> None:
        """新增/覆盖规则:同 pattern 后写覆盖先写"""
        if not isinstance(mode, PermissionMode):
            raise TypeError(f"mode must be PermissionMode, got {type(mode).__name__}")
        for i, r in enumerate(self._rules):
            if r.tool_pattern == tool_pattern:
                self._rules[i] = PermissionRule(tool_pattern, mode, reason)
                return
        self._rules.append(PermissionRule(tool_pattern, mode, reason))

    def remove_rule(self, tool_pattern: str) -> bool:
        """按 pattern 删除规则;返回是否真的删除了一条"""
        for i, r in enumerate(self._rules):
            if r.tool_pattern == tool_pattern:
                self._rules.pop(i)
                return True
        return False

    def check(self, tool_name: str) -> PermissionMode:
        """查 tool_name 命中的 mode,默认兜底"""
        for r in self._rules:
            if r.matches(tool_name):
                return r.mode
        return self._default_mode

    def check_with_reason(self, tool_name: str) -> Tuple[PermissionMode, str]:
        """返回 (mode, reason);reason 在未命中时为空串"""
        for r in self._rules:
            if r.matches(tool_name):
                return r.mode, r.reason
        return self._default_mode, ""

    def set_default_mode(self, mode: PermissionMode) -> None:
        if not isinstance(mode, PermissionMode):
            raise TypeError(f"mode must be PermissionMode, got {type(mode).__name__}")
        self._default_mode = mode

    @property
    def default_mode(self) -> PermissionMode:
        return self._default_mode

    def all_rules(self) -> List[PermissionRule]:
        return list(self._rules)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default_mode": self._default_mode.value,
            "rules": [r.to_dict() for r in self._rules],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PermissionRegistry":
        reg = cls(default_mode=PermissionMode(d["default_mode"]))
        for rd in d.get("rules", []):
            reg._rules.append(PermissionRule.from_dict(rd))
        return reg


# ============ JSON 序列化 ============
def stack_to_dict(stack: ConfigStack) -> Dict[str, Any]:
    """整栈 → JSON-friendly dict"""
    return stack.layers_snapshot()


def stack_from_dict(d: Dict[str, Any]) -> ConfigStack:
    s = ConfigStack()
    s.load_snapshot(d)
    return s
