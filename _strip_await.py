"""Remove all `body = await request.json()` lines — body is now a Pydantic model."""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Pattern: `    body = await request.json()\n` (any indent)
new_src = re.sub(
    r'^[ \t]*body\s*=\s*await\s+request\.json\(\)\s*\n',
    '',
    src,
    flags=re.MULTILINE,
)

# Also remove `import json` if no longer used? It's still used in many places.
# Don't touch imports.

p.write_text(new_src, encoding="utf-8")
removed = src.count('await request.json()') - new_src.count('await request.json()')
print(f"removed {removed} `body = await request.json()` lines")
