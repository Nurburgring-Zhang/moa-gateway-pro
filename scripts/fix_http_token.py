import re
from pathlib import Path

count = 0
for fp in [
    'D:/MoA Gateway Pro/moa_gateway/ui/pages.py',
    'D:/MoA Gateway Pro/moa_gateway/ui/pages2.py',
]:
    text = Path(fp).read_text(encoding='utf-8')

    pat_get = re.compile(
        r'(await http_get\(f"\{base\}/[^"]+")(, timeout=[\d.]+)?(\))'
    )
    def rep_get(m):
        head, tmo, tail = m.group(1), m.group(2) or '', m.group(3)
        return head + tmo + ', token=sr.admin_token' + tail
    text, n_get = pat_get.subn(rep_get, text)

    pat_post = re.compile(
        r'(await http_post\(f"\{base\}/[^"]+",\s*[^,]+)(, timeout=[\d.]+)?(\))'
    )
    def rep_post(m):
        head, tmo, tail = m.group(1), m.group(2) or '', m.group(3)
        return head + tmo + ', token=sr.admin_token' + tail
    text, n_post = pat_post.subn(rep_post, text)

    Path(fp).write_text(text, encoding='utf-8')
    print(fp + ': http_get +' + str(n_get) + ', http_post +' + str(n_post))
    count += n_get + n_post

print('Total: ' + str(count))
