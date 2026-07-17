"""config_stack 真实测试(非 mock)"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from moa_gateway.capability.config_stack import (
    ConfigEntry,
    ConfigLayer,
    ConfigStack,
    PermissionMode,
    PermissionRegistry,
    PermissionRule,
    merge_layers,
    stack_from_dict,
    stack_to_dict,
)


# ============ 枚举 / 数据模型 ============
def test_config_layer_count_and_priority():
    """8 层 + 优先级 POLICY=0 最高"""
    assert len(ConfigLayer) == 8
    assert int(ConfigLayer.POLICY) == 0
    assert int(ConfigLayer.BUILTIN) == 7
    names = [l.name for l in ConfigLayer]
    assert names == ["POLICY", "USER", "PROJECT", "LOCAL", "PLUGIN", "SKILL", "SESSION", "BUILTIN"]
    print(f"  ✓ test_config_layer_count_and_priority: 8 层 {names}")


def test_permission_mode_count():
    """5 个 PermissionMode"""
    assert len(PermissionMode) == 5
    vals = {m.value for m in PermissionMode}
    assert vals == {"default", "accept_edits", "bypass_permissions", "plan", "bubble"}
    print(f"  ✓ test_permission_mode_count: {sorted(vals)}")


def test_config_entry_fields():
    """ConfigEntry 4 字段"""
    e = ConfigEntry(key="k", value=1, source=ConfigLayer.POLICY, explicit=True)
    assert e.key == "k" and e.value == 1
    assert e.source == ConfigLayer.POLICY
    assert e.explicit is True
    e2 = ConfigEntry(key="k2", value=2, source=ConfigLayer.BUILTIN)
    assert e2.explicit is False  # default
    print("  ✓ test_config_entry_fields: 4 字段 + explicit 默认 False")


def test_permission_rule_match():
    """PermissionRule.matches glob 命中"""
    r = PermissionRule(tool_pattern="Bash:*", mode=PermissionMode.ACCEPT_EDITS, reason="t")
    assert r.matches("Bash:ls")
    assert r.matches("Bash:rm -rf")
    assert not r.matches("Write")
    r2 = PermissionRule(tool_pattern="*", mode=PermissionMode.BYPASS_PERMISSIONS, reason="wild")
    assert r2.matches("anything")
    print("  ✓ test_permission_rule_match: Bash:* & * glob 正确")


# ============ ConfigStack 基础 ============
def test_stack_set_and_get():
    """set / get 基础"""
    s = ConfigStack()
    s.set("theme", "dark", ConfigLayer.USER)
    assert s.get("theme") == "dark"
    s.set("limit", 100, ConfigLayer.PROJECT)
    assert s.get("limit") == 100
    print("  ✓ test_stack_set_and_get: set + get 直读")


def test_stack_policy_overrides_all():
    """8 层中 POLICY 胜"""
    s = ConfigStack()
    s.set("k", "builtin_val", ConfigLayer.BUILTIN)
    s.set("k", "session_val", ConfigLayer.SESSION)
    s.set("k", "plugin_val", ConfigLayer.PLUGIN)
    s.set("k", "user_val", ConfigLayer.USER)
    s.set("k", "policy_val", ConfigLayer.POLICY)  # 最高
    assert s.get("k") == "policy_val"
    print("  ✓ test_stack_policy_overrides_all: POLICY 压住 7 层")


def test_stack_user_over_project():
    """USER > PROJECT"""
    s = ConfigStack()
    s.set("model", "gpt-4", ConfigLayer.PROJECT)
    s.set("model", "claude-opus", ConfigLayer.USER)
    assert s.get("model") == "claude-opus"
    print("  ✓ test_stack_user_over_project: USER 覆盖 PROJECT")


def test_stack_explicit_precedence():
    """同 layer 内 explicit=True 仍能覆盖 explicit=False(同层 set 都生效)"""
    s = ConfigStack()
    s.set("flag", "default", ConfigLayer.PROJECT, explicit=False)
    s.set("flag", "user-set", ConfigLayer.PROJECT, explicit=True)
    # 同层: 后写覆盖,explicit 只是元数据
    assert s.get("flag") == "user-set"
    val, src = s.get_with_source("flag")
    assert src == ConfigLayer.PROJECT
    # 跨层:explicit=False 在高层时,explicit=True 在低层 — 高层胜
    s2 = ConfigStack()
    s2.set("k", "implicit_high", ConfigLayer.POLICY, explicit=False)
    s2.set("k", "explicit_low", ConfigLayer.SESSION, explicit=True)
    assert s2.get("k") == "implicit_high"
    print("  ✓ test_stack_explicit_precedence: explicit 是元数据,优先级仍由 layer 决定")


def test_stack_unset_single_layer():
    """unset 单层"""
    s = ConfigStack()
    s.set("k", "policy_val", ConfigLayer.POLICY)
    s.set("k", "user_val", ConfigLayer.USER)
    assert s.get("k") == "policy_val"
    n = s.unset("k", ConfigLayer.POLICY)
    assert n == 1
    assert s.get("k") == "user_val"  # fallback 到 USER
    print("  ✓ test_stack_unset_single_layer: POLICY 删后 USER 上位")


def test_stack_unset_all_layers():
    """unset 不传 layer 删全部"""
    s = ConfigStack()
    s.set("k", "p", ConfigLayer.POLICY)
    s.set("k", "u", ConfigLayer.USER)
    s.set("k", "b", ConfigLayer.BUILTIN)
    n = s.unset("k")
    assert n == 3
    assert s.get("k") is None
    assert s.get("k", "MISSING") == "MISSING"
    print("  ✓ test_stack_unset_all_layers: 3 条全删,get 返 default")


def test_stack_get_with_source():
    """get_with_source 返回 (value, layer)"""
    s = ConfigStack()
    s.set("api_key", "sk-xxx", ConfigLayer.SESSION)
    val, src = s.get_with_source("api_key")
    assert val == "sk-xxx" and src == ConfigLayer.SESSION
    # 不存在
    v2, src2 = s.get_with_source("nope")
    assert v2 is None and src2 is None
    print("  ✓ test_stack_get_with_source: (val, source) + 不存在 (None, None)")


def test_stack_get_default_when_missing():
    """get 不存在 → default"""
    s = ConfigStack()
    assert s.get("nothing") is None
    assert s.get("nothing", 42) == 42
    assert s.get("nothing", default={"a": 1}) == {"a": 1}
    print("  ✓ test_stack_get_default_when_missing: 3 种 default 形式")


# ============ merge_layers ============
def test_merge_layers_8_layers():
    """merge_layers 8 层全部输入"""
    layers = {
        ConfigLayer.POLICY:  {"k1": "P", "k2": "P"},
        ConfigLayer.USER:    {"k2": "U", "k3": "U"},
        ConfigLayer.PROJECT: {"k3": "PR", "k4": "PR"},
        ConfigLayer.LOCAL:   {"k4": "L", "k5": "L"},
        ConfigLayer.PLUGIN:  {"k5": "PL", "k6": "PL"},
        ConfigLayer.SKILL:   {"k6": "SK", "k7": "SK"},
        ConfigLayer.SESSION: {"k7": "SE", "k8": "SE"},
        ConfigLayer.BUILTIN: {"k8": "B"},
    }
    merged = merge_layers(layers)
    # 高优先级覆盖低优先级:每对 key 中,层级更高(数字更小)的胜
    assert merged["k1"] == "P"   # 只有 POLICY
    assert merged["k2"] == "P"   # POLICY 覆盖 USER
    assert merged["k3"] == "U"   # USER 覆盖 PROJECT
    assert merged["k4"] == "PR"  # PROJECT 覆盖 LOCAL
    assert merged["k5"] == "L"   # LOCAL 覆盖 PLUGIN
    assert merged["k6"] == "PL"  # PLUGIN 覆盖 SKILL
    assert merged["k7"] == "SK"  # SKILL 覆盖 SESSION
    assert merged["k8"] == "SE"  # SESSION 覆盖 BUILTIN
    assert len(merged) == 8
    print("  ✓ test_merge_layers_8_layers: 8 key 全到位,高优先级胜")


def test_merge_layers_priority_correct():
    """merge_layers 优先级:POLICY 永远压 BUILTIN,与输入 dict 顺序无关"""
    layers = {
        ConfigLayer.BUILTIN: {"x": 1, "y": 2, "z": 3},
        ConfigLayer.POLICY:  {"x": 10, "y": 20},
    }
    m = merge_layers(layers)
    assert m == {"x": 10, "y": 20, "z": 3}
    # 反向输入:POLICY 写在 BUILTIN 之前 → 结果仍相同(按 ConfigLayer 枚举序走)
    layers2 = {
        ConfigLayer.POLICY:  {"x": 10, "y": 20},
        ConfigLayer.BUILTIN: {"x": 1, "y": 2, "z": 3},
    }
    m2 = merge_layers(layers2)
    assert m2 == {"x": 10, "y": 20, "z": 3}  # POLICY 始终胜
    print("  ✓ test_merge_layers_priority_correct: 顺序无关,按 ConfigLayer 枚举序走")


# ============ PermissionRegistry ============
def test_permission_registry_default():
    """未命中 → default_mode"""
    reg = PermissionRegistry()
    assert reg.default_mode == PermissionMode.DEFAULT
    assert reg.check("Bash") == PermissionMode.DEFAULT
    assert reg.check("Write") == PermissionMode.DEFAULT
    print("  ✓ test_permission_registry_default: 全默认 → DEFAULT")


def test_permission_registry_set_rule():
    """set_rule 后命中"""
    reg = PermissionRegistry()
    reg.set_rule("Bash", PermissionMode.ACCEPT_EDITS, reason="auto bash")
    assert reg.check("Bash") == PermissionMode.ACCEPT_EDITS
    mode, reason = reg.check_with_reason("Bash")
    assert mode == PermissionMode.ACCEPT_EDITS
    assert reason == "auto bash"
    # 同 pattern 覆盖
    reg.set_rule("Bash", PermissionMode.BYPASS_PERMISSIONS, reason="override")
    assert reg.check("Bash") == PermissionMode.BYPASS_PERMISSIONS
    print("  ✓ test_permission_registry_set_rule: 设值 + 同 pattern 覆盖")


def test_permission_registry_check_glob():
    """glob 模式匹配"""
    reg = PermissionRegistry()
    reg.set_rule("Bash:*", PermissionMode.ACCEPT_EDITS, reason="shell")
    reg.set_rule("Write*", PermissionMode.PLAN, reason="file write needs plan")
    reg.set_rule("Read", PermissionMode.BYPASS_PERMISSIONS, reason="read-only safe")
    assert reg.check("Bash:ls") == PermissionMode.ACCEPT_EDITS
    assert reg.check("Bash:rm -rf /") == PermissionMode.ACCEPT_EDITS
    assert reg.check("Write") == PermissionMode.PLAN
    assert reg.check("WriteFile") == PermissionMode.PLAN
    assert reg.check("Read") == PermissionMode.BYPASS_PERMISSIONS
    # 不匹配的(如 GlobTool) → default
    assert reg.check("GlobTool") == PermissionMode.DEFAULT
    print("  ✓ test_permission_registry_check_glob: Bash:* / Write* / Read 全部命中正确")


def test_permission_registry_check_fallback():
    """不匹配 → default"""
    reg = PermissionRegistry(default_mode=PermissionMode.BUBBLE)
    reg.set_rule("Bash", PermissionMode.ACCEPT_EDITS, reason="x")
    assert reg.check("NotBash") == PermissionMode.BUBBLE
    assert reg.check("") == PermissionMode.BUBBLE
    assert reg.check("WebSearch") == PermissionMode.BUBBLE
    print("  ✓ test_permission_registry_check_fallback: BUBBLE 兜底生效")


def test_permission_registry_set_default_mode():
    """set_default_mode 切换默认"""
    reg = PermissionRegistry()
    assert reg.default_mode == PermissionMode.DEFAULT
    reg.set_default_mode(PermissionMode.PLAN)
    assert reg.default_mode == PermissionMode.PLAN
    # 影响 check
    assert reg.check("unknown_tool") == PermissionMode.PLAN
    print("  ✓ test_permission_registry_set_default_mode: DEFAULT → PLAN 切换生效")


# ============ 边界 / JSON ============
def test_boundary_zero_layers():
    """边界: 0 layer 数据 + 空 stack"""
    # 空 stack
    s = ConfigStack()
    assert s.get("anything") is None
    assert s.get("anything", "DEF") == "DEF"
    assert len(s) == 0
    assert s.to_dict()["merged"] == {}
    # merge_layers 空输入
    assert merge_layers({}) == {}
    # merge_layers 只有 BUILTIN
    assert merge_layers({ConfigLayer.BUILTIN: {"k": "v"}}) == {"k": "v"}
    # merge_layers 只有 POLICY
    assert merge_layers({ConfigLayer.POLICY: {"k": "p"}}) == {"k": "p"}
    print("  ✓ test_boundary_zero_layers: 空 + 单层都正确")


def test_json_serialization_roundtrip():
    """整栈 JSON 往返一致"""
    s = ConfigStack()
    s.set("api_key", "sk-abc", ConfigLayer.SESSION, explicit=True)
    s.set("model", "gpt-4", ConfigLayer.PROJECT, explicit=False)
    s.set("debug", True, ConfigLayer.BUILTIN, explicit=False)
    s.set("policy_flag", "X", ConfigLayer.POLICY, explicit=True)
    # stack
    d = stack_to_dict(s)
    s2 = stack_from_dict(d)
    assert s2.get("api_key") == "sk-abc"
    assert s2.get("model") == "gpt-4"
    assert s2.get("debug") is True
    assert s2.get("policy_flag") == "X"
    v, src = s2.get_with_source("api_key")
    assert src == ConfigLayer.SESSION
    # to_dict 合并视图
    merged = s.to_dict()["merged"]
    assert merged == {
        "api_key": "sk-abc",
        "model": "gpt-4",
        "debug": True,
        "policy_flag": "X",
    }
    # PermissionRegistry 也可序列化
    reg = PermissionRegistry(default_mode=PermissionMode.PLAN)
    reg.set_rule("Bash", PermissionMode.ACCEPT_EDITS, reason="auto")
    reg.set_rule("Write*", PermissionMode.BUBBLE, reason="write bubble")
    s_json = json.dumps(reg.to_dict())
    reg2 = PermissionRegistry.from_dict(json.loads(s_json))
    assert reg2.default_mode == PermissionMode.PLAN
    assert reg2.check("Bash") == PermissionMode.ACCEPT_EDITS
    assert reg2.check("WriteFile") == PermissionMode.BUBBLE
    assert reg2.check("Other") == PermissionMode.PLAN
    print("  ✓ test_json_serialization_roundtrip: stack + registry 双向 OK")


# ============ 主入口 ============
def main() -> None:
    tests = [
        test_config_layer_count_and_priority,
        test_permission_mode_count,
        test_config_entry_fields,
        test_permission_rule_match,
        test_stack_set_and_get,
        test_stack_policy_overrides_all,
        test_stack_user_over_project,
        test_stack_explicit_precedence,
        test_stack_unset_single_layer,
        test_stack_unset_all_layers,
        test_stack_get_with_source,
        test_stack_get_default_when_missing,
        test_merge_layers_8_layers,
        test_merge_layers_priority_correct,
        test_permission_registry_default,
        test_permission_registry_set_rule,
        test_permission_registry_check_glob,
        test_permission_registry_check_fallback,
        test_permission_registry_set_default_mode,
        test_boundary_zero_layers,
        test_json_serialization_roundtrip,
    ]
    print(f"=== config_stack: running {len(tests)} tests ===")
    passed = 0
    for fn in tests:
        try:
            if fn() is True:
                passed += 1
        except Exception as e:
            print(f"  ✗ {fn.__name__}: {type(e).__name__}: {e}")
    print(f"=== {passed}/{len(tests)} passed ===")
    if passed != len(tests):
        sys.exit(1)


if __name__ == "__main__":
    main()
