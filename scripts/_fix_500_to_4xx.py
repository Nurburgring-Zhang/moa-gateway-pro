"""Move exception handlers and _err_500 to AFTER `app = FastAPI(...)`."""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Step 1: Find the inserted block and extract it
# The block starts with "# ============ Round-1 修复:全局异常 handler" and ends with "_err_500"
# It's inserted between `metrics = Metrics.instance()\n` and the next code

start_marker = "    # ============ Round-1 修复:全局异常 handler"
end_marker = "        return HTTPException(500, f\"{action}: {e}\")\n"

start_idx = src.find(start_marker)
end_idx = src.find(end_marker, start_idx)
if start_idx < 0 or end_idx < 0:
    print("Could not find the inserted block")
    raise SystemExit(1)

# Include the closing "    \n" (blank line) and any other lines
end_idx = src.find("\n\n", end_idx) + 2  # include blank line after
extracted = src[start_idx:end_idx]
print(f"Extracted {len(extracted)} bytes from offset {start_idx} to {end_idx}")
print(f"First 100 chars: {extracted[:100]!r}")

# Remove the block from its current location
src = src[:start_idx] + src[end_idx:]

# Step 2: Find `app = FastAPI(\n` and insert after the entire `app = FastAPI(...)` block
# Find the closing of `app = FastAPI(\n...\n)`
m = re.search(r"    app = FastAPI\(\n(?:[^\)]*\n)+?    \)\n", src)
if not m:
    print("Could not find `app = FastAPI(...)` block")
    raise SystemExit(1)

insert_pos = m.end()
print(f"Inserting after `app = FastAPI(...)` at offset {insert_pos}")

# Insert the extracted block, with a leading newline
new_src = src[:insert_pos] + "\n" + extracted + src[insert_pos:]

# Verify AST
import ast
try:
    ast.parse(new_src)
    print("AST parses OK")
except SyntaxError as e:
    print(f"AST parse error at line {e.lineno}: {e.text!r}")
    raise

p.write_text(new_src, encoding="utf-8")
print(f"Done. New length: {len(new_src)}")
