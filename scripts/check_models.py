"""查 config.yaml enabled 状态"""
import yaml
with open("config.yaml", encoding="utf-8") as f:
    cfg = yaml.safe_load(f)
ms = cfg.get("models", [])
print(f"Total models: {len(ms)}")
on = [m for m in ms if m.get("enabled")]
off = [m for m in ms if not m.get("enabled")]
print(f"Enabled: {len(on)}")
print(f"Disabled: {len(off)}")
print()
print("Disabled (需要 API key):")
for m in off:
    print(f"  {m['id']:25s} tier={m.get('tier', '?'):8s} provider={m.get('provider', '?'):12s} env={m.get('api_key_env', '?')}")
print()
print("Currently enabled:")
for m in on:
    print(f"  {m['id']:25s} provider={m.get('provider', '?')}")