"""Fix duplicate body parameter in function signatures."""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Pattern: in a multi-line function signature, two `body: ...` lines.
# We want to keep the first one and remove the second (which is the old Dict version).
# Use a more careful approach: look at consecutive lines.

lines = src.split('\n')
out = []
i = 0
removed = 0
while i < len(lines):
    line = lines[i]
    if re.match(r'^\s+body:\s*\w+', line):
        # Check if the previous non-blank line is also a `body:` line
        j = i - 1
        while j >= 0 and lines[j].strip() == '':
            j -= 1
        if j >= 0 and re.match(r'^\s+body:\s*\w+', lines[j]):
            # Duplicate — skip this line
            removed += 1
            i += 1
            continue
    out.append(line)
    i += 1

new_src = '\n'.join(out)
p.write_text(new_src, encoding="utf-8")
print(f"removed {removed} duplicate body params")
