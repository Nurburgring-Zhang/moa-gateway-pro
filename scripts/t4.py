from pathlib import Path
fp = 'D:/MoA Gateway Pro/scripts/audit_ui_e2e.py'
text = Path(fp).read_text(encoding='utf-8')
old = 'return data.get(\"access_token\")'
new = 'return data.get(\"token\") or data.get(\"access_token\")'
assert old in text
text = text.replace(old, new)
Path(fp).write_text(text, encoding='utf-8')
print('OK - audit_admin_login uses token field')
