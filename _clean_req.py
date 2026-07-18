"""Clean up orphan `request: Request,` lines that are dead params.

For each `request: Request,` line that follows `body: CreateXxxRequest,`
and is not used in the function body, remove it.
"""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")
lines = src.split('\n')

# Find all async def signatures and their function bodies
# A function signature spans from `(request: Request,` until the next `):` at the same indent
# We want to remove `request: Request,` if:
#   1. It's part of a function signature (not body)
#   2. The function body does NOT use `request` (i.e., request variable)
#   3. The signature is multi-line

out_lines = []
i = 0
removed = 0
while i < len(lines):
    line = lines[i]
    # Check if this is a function signature with `request: Request,` on its own line
    if re.match(r'^\s+request:\s*Request,\s*$', line):
        # Find the enclosing function: walk backwards to find `async def` or `def`
        j = i - 1
        while j > 0 and not re.match(r'^\s*async\s+def\s+', lines[j]) and not re.match(r'^\s*def\s+', lines[j]):
            j -= 1
        func_start = j
        # Find end of function: next `):` at column 0 or after close paren
        j = i + 1
        while j < len(lines) and not re.search(r'\)\s*:?\s*$', lines[j]):
            j += 1
        # Wait, that's not right. Find the actual function end (next def/async def/@app)
        # Function body ends at next decorator or def/async def
        k = i + 1
        while k < len(lines):
            l = lines[k]
            if l.strip().startswith('@app.') or l.strip().startswith('def ') or l.strip().startswith('async def '):
                break
            k += 1
        func_body = '\n'.join(lines[func_start:k])
        # Check if function body uses `request` (excluding the signature itself)
        body_only = '\n'.join(lines[i+1:k])
        if 'request.' in body_only or 'request,' in body_only or 'request)' in body_only or 'request\n' in body_only or re.search(r'\brequest\b', body_only):
            # Used → keep
            out_lines.append(line)
        else:
            # Not used → remove
            removed += 1
        i += 1
    else:
        out_lines.append(line)
        i += 1

new_src = '\n'.join(out_lines)
p.write_text(new_src, encoding="utf-8")
print(f"removed {removed} dead `request: Request,` params")
