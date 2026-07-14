"""Debug what's in the file."""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")
lines = src.split("\n")

# Find all lines containing 'except Exception'
for i, line in enumerate(lines):
    if "except Exception" in line and "except HTTPException" not in line:
        # show 2 lines context
        print(f"line {i+1}: {line.rstrip()}")
        if i+1 < len(lines):
            print(f"  next: {lines[i+1].rstrip()}")
