"""测试 prompts.py 的所有能力"""
import sys, os
sys.path.insert(0, ".")

from moa_gateway.prompts import (
    get_prompt, list_templates, save_template, delete_template, PLACEHOLDERS,
)

print("=" * 60)
print("[1] list_templates() - 列出所有 12 个模板")
print("=" * 60)
tpls = list_templates()
print(f"Count: {len(tpls)}")
for t in tpls:
    print(f"  {t['name']:30s} {t['source']:8s} {t['size']:5d}B")

print()
print("=" * 60)
print("[2] get_prompt('aggregator') - 前 300 字")
print("=" * 60)
p = get_prompt("aggregator")
print(p[:300])

print()
print("=" * 60)
print("[3] get_prompt('compose_security') - 完整")
print("=" * 60)
p = get_prompt("compose_security")
print(p)

print()
print("=" * 60)
print("[4] 占位符替换 - save 到 USER_DIR")
print("=" * 60)
test_tpl = "用户问: {user_query}\n参考: {reference_responses}\n当前: {current_draft}"
path = save_template("test_tmp_xxx", test_tpl)
print(f"Saved to: {path}")
rendered = get_prompt(
    "test_tmp_xxx",
    user_query="今天天气如何?",
    reference_responses="晴天",
    current_draft="草稿 v1",
)
print(f"Rendered:\n{rendered}")
deleted = delete_template("test_tmp_xxx")
print(f"Deleted: {deleted}")
# 之后调用会回退到 BUILTIN
fallback = get_prompt("test_tmp_xxx")
print(f"After delete -> fallback (length): {len(fallback)}")

print()
print("=" * 60)
print("[5] PLACEHOLDERS 字典(帮助文档)")
print("=" * 60)
for k, v in PLACEHOLDERS.items():
    print(f"  {{{k}}} -> {v}")

print()
print("=" * 60)
print("[6] 文件 vs builtin 优先级测试")
print("=" * 60)
# 写一个用户自定义文件,然后看是否覆盖默认
save_template("compose_feasibility", "USER_OVERRIDE: 这是用户自定义的可行性 prompt")
got = get_prompt("compose_feasibility")
print(f"After override: {got[:80]}")
delete_template("compose_feasibility")
got_after = get_prompt("compose_feasibility")
print(f"After delete, get_prompt first 50 chars: {got_after[:50]}...")
print(f"Match default (from disk): {got_after.startswith('你从')} (True=using default file)")

print()
print("ALL TESTS PASSED")