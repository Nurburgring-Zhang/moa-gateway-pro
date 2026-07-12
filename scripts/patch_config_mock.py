"""把 config.yaml 所有 enabled: false 改成 true,加 api_key: '' 让 mock 自动生效"""
import re
from pathlib import Path

p = Path("config.yaml")
text = p.read_text(encoding="utf-8")
original = text

# 把所有 enabled: false 改成 enabled: true
text = re.sub(r"(\s+)enabled:\s*false", r"\1enabled: true", text)

# 改完
n_changed = original.count("enabled: false") - text.count("enabled: false")
print(f"enabled: false -> true: {n_changed} 处")

# 加 api_key: "" 到每个 endpoint(在 api_key_env 之后)
# 用 YAML 块处理更稳,这里用 sed
# 不,直接给每个 endpoint 的 - id: 块添加 api_key: ''
import yaml
data = yaml.safe_load(text)
# data 是 dict,models 是 list
for ep in data.get("models", []):
    if "api_key" not in ep:
        ep["api_key"] = ""  # 空 = mock 自动 fallback
    if not ep.get("enabled"):
        ep["enabled"] = True

# 写回
p.write_text(
    yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
    encoding="utf-8",
)
print("✓ 写回 config.yaml,所有 endpoint: enabled=true + api_key='' (mock 自动)")

# 验证
data2 = yaml.safe_load(p.read_text(encoding="utf-8"))
print(f"\n验证:")
print(f"  Total: {len(data2['models'])}")
print(f"  Enabled: {sum(1 for e in data2['models'] if e.get('enabled'))}")
print(f"  With api_key: {sum(1 for e in data2['models'] if e.get('api_key'))}")