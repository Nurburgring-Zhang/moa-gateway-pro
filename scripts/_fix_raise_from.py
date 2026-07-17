"""Final cleanup: fix all 'except HTTPException:\\n\\n    raise from e  # comment'
   → 'except HTTPException: raise  # comment' (one-liner).

   The v1.6.6 _patch_4xx.py inserted 'except HTTPException:\\n    raise' as a
   two-line block; the earlier _fix_patch_order.py added 'raise from e' but
   that creates a syntax error since 'from e' can't be alone.
"""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")
lines = src.split("\n")
patched = 0
i = 0
while i < len(lines):
    # Match: '    except HTTPException:\n\n            raise from e  # X'
    if re.match(r"^\s+except\s+HTTPException\s*:\s*$", lines[i]):
        # Look ahead 1-3 lines for 'raise from e'
        for j in range(i + 1, min(i + 4, len(lines))):
            if re.match(r"^\s+raise\s+from\s+e\s*(#.*)?$", lines[j]):
                # Fix: delete blank lines between except and raise
                # Combine into one line: 'except HTTPException: raise  # comment'
                indent = re.match(r"^(\s+)", lines[i]).group(1)
                comment = ""
                m = re.match(r"^\s+raise\s+from\s+e\s*(#.*)?$", lines[j])
                if m and m.group(1):
                    comment = "  " + m.group(1)
                lines[i] = f"{indent}except HTTPException: raise{comment}"
                # Delete lines i+1 to j (inclusive)
                del lines[i+1:j+1]
                patched += 1
                break
            elif lines[j].strip() and not lines[j].strip().startswith("raise"):
                # Some other code in between, give up
                break
    i += 1
p.write_text("\n".join(lines), encoding="utf-8")
print(f"patched {patched} 'raise from e' bad blocks")
