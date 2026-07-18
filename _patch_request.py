"""Patch server.py: replace `request: Request,` + `body = await request.json()` pattern
with `body: <PydanticModel>,`.

Pattern to find:
  async def func_name(request: Request,
                      key_info: ...):
      \"\"\"docstring\"\"\"
      body = await request.json()
      ...

Replace with:
  async def func_name(body: <PydanticModel>,
                      key_info: ...):
      \"\"\"docstring\"\"\"
      ...

But we need to know which model to use. Strategy: for each endpoint with `request: Request`,
look at what fields the body is used for, then use the auto-generated ENDPOINT_MODELS.
"""
import re
import json
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Build model name lookup
import sys
sys.path.insert(0, r"D:\MoA Gateway Pro")
from moa_gateway.req_models import ENDPOINT_MODELS

def to_class_name(endpoint):
    path = endpoint['path']
    name = path.strip('/').replace('/', '_').replace('{', '').replace('}', '').replace('-', '_')
    name = re.sub(r'__+', '_', name).strip('_')
    m = {'GET': 'Get', 'POST': 'Create', 'PUT': 'Update', 'DELETE': 'Delete'}.get(endpoint['method'], 'Req')
    parts = [p for p in name.split('_') if p and p not in ('v1', 'v2', 'api', 'capability')]
    camel = ''.join(p.capitalize() for p in parts)
    if not camel:
        camel = 'Root'
    return f'{m}{camel}Request'

# Load endpoint scan
scans = json.loads(Path(r"D:\MoA Gateway Pro\endpoints_scan.json").read_text(encoding="utf-8"))
scans_by_path = {e['path']: e for e in scans}

# Build new src: for each `@app.method("path", ...)` followed by `async def func_name(request: Request,`:
# - Replace `request: Request,` with `body: <Model>,`
# - Remove the `body = await request.json()` line
# - Subsequent `body.get(...)` still works thanks to _DictLikeMixin

# Process line by line
lines = src.split('\n')
decorator_re = re.compile(r'@app\.(get|post|put|delete)\(["\']([^"\']+)["\']')
request_re = re.compile(r'^(\s*)async\s+def\s+(\w+)\([^)]*?\s*request:\s*Request,')
body_await_re = re.compile(r'^\s*body\s*=\s*await\s+request\.json\(\)\s*$')

current_path = None
current_method = None
count = 0
out_lines = []
skip_next_body_await = False

for i, line in enumerate(lines):
    if skip_next_body_await:
        # Skip the line
        skip_next_body_await = False
        if body_await_re.match(line):
            count += 1
            continue
        # If not the expected line, just continue
    dec_m = decorator_re.match(line.strip())
    if dec_m:
        current_method = dec_m.group(1)
        current_path = dec_m.group(2)
        out_lines.append(line)
        continue
    # Check for function signature with request: Request
    req_m = request_re.match(line)
    if req_m and current_path:
        # Find or build model
        ep = scans_by_path.get(current_path)
        if ep:
            cls_name = to_class_name(ep)
            if cls_name in [c.__name__ for c in ENDPOINT_MODELS.values()]:
                # Replace request: Request, with body: <Model>,
                # Also need to keep the request param if it's used (e.g., for request.client.host)
                # Check if request is used in function body for non-body purposes
                # For now, replace - endpoints can use body.client_ip via Depends
                new_line = req_m.group(1) + f'async def {req_m.group(2)}(body: {cls_name},'
                out_lines.append(new_line)
                skip_next_body_await = True
                continue
    out_lines.append(line)

new_src = '\n'.join(out_lines)
p.write_text(new_src, encoding="utf-8")
print(f"removed {count} `body = await request.json()` lines")
