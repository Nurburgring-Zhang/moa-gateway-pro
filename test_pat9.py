import re
from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")

# The issue: my original pattern had `r'raise HTTPException\(\s*500\s*,\s*f"([^"]*?)\{e\}([^"]*?)"\s*\)\s*from\s+e'`
# The `from e` at the end might not be matching because the `)` followed by `from e` may have different spacing
# Let me check one
target1 = 'raise HTTPException(500, f"MoA run failed: {e}")'
target2 = 'raise HTTPException(500, f"MoA run failed: {e}") from e'
print('target1:', target1 in src)
print('target2:', target2 in src)
# Find first occurrence
idx = src.find(target1)
print('next 30 chars:', repr(src[idx+len(target1):idx+len(target1)+30]))
