import re
from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Find all "raise HTTPException(500, f"
print('count of "raise HTTPException(500, f":', src.count('raise HTTPException(500, f'))
print('count of "raise HTTPException(500,":', src.count('raise HTTPException(500,'))

# Try a basic regex
pat = r'raise HTTPException\(500, f"[^"]+\{e\}[^"]*"\) from e'
matches = re.findall(pat, src)
print('matches:', len(matches))
print('first:', matches[0] if matches else 'none')
