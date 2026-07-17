import re
from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Try a simpler regex
pat = re.compile(r'raise HTTPException\(500, f"(.+?)\{e\}(.+?)"\) from e')
matches = pat.findall(src)
print(f'matched: {len(matches)}')
for m in matches[:5]:
    print(repr(m))

# Also try without f-string
pat2 = re.compile(r'raise HTTPException\(500, f"[^"]*\{e\}[^"]*"\) from e')
matches2 = pat2.findall(src)
print(f'pat2 matched: {len(matches2)}')
