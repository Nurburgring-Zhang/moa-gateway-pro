import re
from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
pat = re.compile(r'raise HTTPException\(\s*500\s*,\s*f"([^"]*?)\{e\}([^"]*?)"\s*\)\s*from\s+e')
matches = pat.findall(src)
print(f'matched: {len(matches)}')
for m in matches[:5]:
    print(repr(m))
