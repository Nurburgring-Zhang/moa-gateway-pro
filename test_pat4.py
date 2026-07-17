import re
from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Try a simpler regex without f-string
pat = re.compile(r'raise HTTPException\(500, f"[^"]*\{e\}[^"]*"\) from e')
print('full sample around idx 43638:')
print(repr(src[43620:43700]))

# The f-string may have special chars
# Try matching with non-anchored regex
pat = re.compile(r'raise HTTPException\(500, f".+?\{e\}.+?\) from e')
m = pat.search(src)
print('m:', m)
print('group:', m.group() if m else None)
