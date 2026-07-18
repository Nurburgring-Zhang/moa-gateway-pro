"""Remove duplicate body params in function signatures."""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Find consecutive body lines: `body: X,\n     body: Y,`
# Remove the second one
pattern = re.compile(r'([ \t]*body:\s*\w+\S*,)\n(\s+)body:\s*\w+\S*,', re.MULTILINE)
new_src, count = pattern.subn(r'\1', src)
print(f"removed {count} duplicate body params")
p.write_text(new_src, encoding="utf-8")
