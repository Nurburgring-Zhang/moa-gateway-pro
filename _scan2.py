"""Regenerate endpoints_scan.json from current server.py."""
import re
import json
from pathlib import Path

src_path = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = src_path.read_text(encoding="utf-8")

lines = src.split('\n')
endpoints = []
i = 0
decorator_re = re.compile(r'\s*@app\.(post|get|put|delete)\(["\']([^"\']+)["\']')

while i < len(lines):
    line = lines[i]
    m = decorator_re.match(line)
    if m:
        method = m.group(1)
        path = m.group(2)
        body_fields = set()
        func_start = -1
        for j in range(i+1, min(i+80, len(lines))):
            if 'async def' in lines[j] or 'def ' in lines[j]:
                func_start = j
                break
        if func_start < 0:
            i += 1
            continue
        k = func_start
        while k < len(lines):
            l = lines[k]
            if (k > func_start) and (l.strip().startswith('@app.') or l.strip().startswith('def ') or l.strip().startswith('async def ')):
                break
            # Match body.get / body[ / body.
            for fm in re.finditer(r'body\.get\(["\']([a-zA-Z_]\w*)', l):
                body_fields.add(fm.group(1))
            for fm in re.finditer(r'body\[["\']([a-zA-Z_]\w*)', l):
                body_fields.add(fm.group(1))
            for fm in re.finditer(r'body\.([a-zA-Z_]\w*)', l):
                body_fields.add(fm.group(1))
            k += 1
        endpoints.append({
            'method': method.upper(),
            'path': path,
            'fields': sorted(body_fields),
        })
        i = k
    else:
        i += 1

Path(r"D:\MoA Gateway Pro\endpoints_scan.json").write_text(
    json.dumps(endpoints, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(f"saved {len(endpoints)} endpoints")
