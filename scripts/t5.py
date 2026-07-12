from pathlib import Path
fp = 'D:/MoA Gateway Pro/moa_gateway/ui/server_runner.py'
text = Path(fp).read_text(encoding='utf-8')
old = 'self.admin_token = data.get(\"access_token\")'
new = 'self.admin_token = data.get(\"token\") or data.get(\"access_token\")'
assert old in text, 'not found'
text = text.replace(old, new)
Path(fp).write_text(text, encoding='utf-8')
print('OK - server_runner._admin_login uses token field')
