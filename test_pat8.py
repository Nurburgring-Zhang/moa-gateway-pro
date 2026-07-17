import re
from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Find all "raise HTTPException(500, f"MoA run failed: {e}")"
target = 'raise HTTPException(500, f"MoA run failed: {e}")'
print('exact match:', target in src)
print('contains f"MoA run failed: {e}":', 'f"MoA run failed: {e}"' in src)

# Try simpler
pat = r'raise HTTPException\(500, f"MoA run failed: \{e\}"\)'
m = re.search(pat, src)
print('m:', m)
