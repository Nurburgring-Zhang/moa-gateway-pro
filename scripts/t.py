from pathlib import Path
fp = 'D:/MoA Gateway Pro/moa_gateway/ui/pages.py'
text = Path(fp).read_text(encoding='utf-8')
old = 'return await http_post(f"{base}/api/endpoints/{eid}/toggle", {})'
new = 'return await http_post(f"{base}/api/endpoints/{eid}/toggle", {}, token=sr.admin_token)'
if old in text:
    text = text.replace(old, new)
    Path(fp).write_text(text, encoding='utf-8')
    print('OK')
else:
    print('NOT FOUND')
