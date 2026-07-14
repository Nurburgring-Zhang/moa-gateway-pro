"""Patch server.py: add 'except HTTPException: raise' before every 'except Exception'
that contains a 'raise HTTPException(500' within the next 8 lines.
"""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")
lines = src.split("\n")

# Two-pass approach:
# 1. Find all line indices of "raise HTTPException(500"
# 2. Walk back to find the matching "except Exception as e:" (most recent)
# 3. If no "except HTTPException:" exists between, insert before

# Pass 1: indices of raise HTTPException(500
raise500_lines = set()
for i, line in enumerate(lines):
    if "raise HTTPException(500" in line:
        raise500_lines.add(i)

# Pass 2: for each, find enclosing except Exception
insert_points = {}  # line_idx -> indent
for r_idx in raise500_lines:
    # walk back
    for j in range(r_idx-1, max(0, r_idx-15), -1):
        line = lines[j]
        if re.match(r"^\s+except\s+Exception(\s+as\s+\w+)?\s*:\s*$", line):
            # found except Exception
            # check between j and r_idx for "except HTTPException" or "raise  #" pass-through
            between = "\n".join(lines[j:r_idx+1])
            if "except HTTPException" in between or "pass through 4xx" in between:
                break  # already has pass-through
            indent = re.match(r"^(\s+)", line).group(1)
            if j not in insert_points:
                insert_points[j] = indent
            break

# Apply insertions
patched = len(insert_points)
new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)
    if i in insert_points:
        indent = insert_points[i]
        new_lines.append(f"{indent}except HTTPException:")
        new_lines.append(f"{indent}    raise  # patch v1.6.6: pass through 4xx")

p.write_text("\n".join(new_lines), encoding="utf-8")
print(f"patched {patched} 'except Exception' blocks with HTTPException pass-through")
