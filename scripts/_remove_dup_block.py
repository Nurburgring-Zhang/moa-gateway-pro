"""Remove the first (duplicated) exception handler block."""
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Find the first occurrence of the marker
first_marker = "    # ============ Round-1 修复:全局异常 handler (P0-1/P0-2/P0-3 P1-1) ============"
second_marker_idx = src.find(first_marker, 100)  # second occurrence
print(f"second_marker at: {second_marker_idx}")
first_idx = src.find(first_marker)
print(f"first_marker at: {first_idx}")

# We want to delete from first_idx-4 (include the empty line before) to just before second_marker
# But we also need to make sure we delete the trailing blank lines too.
# Let's find the end of the first block — the line with `return HTTPException(500, f"{action}: {e}")\n\n    @asynccontextmanager`
end_pattern_idx = src.find('return HTTPException(500, f"{action}: {e}")', first_idx)
print(f"end_pattern at: {end_pattern_idx}")
# Find the next newline
nl = src.find("\n", end_pattern_idx)
# Skip blank lines
while src[nl+1] == "\n":
    nl += 1
# Include up to (and including) the next non-blank newline
end_idx = nl + 1
print(f"end_idx: {end_idx}")
print(f"snippet around end: {src[end_idx-5:end_idx+50]!r}")

# Delete from just after the empty line above first_marker to end_idx
# The empty line before first_marker:
# Look back from first_idx
look_back = first_idx - 1
while src[look_back] in " \t":
    look_back -= 1
if src[look_back] == "\n":
    # include the previous newline
    start_idx = look_back
else:
    start_idx = first_idx
print(f"start_idx: {start_idx}")
print(f"snippet around start: {src[start_idx-5:start_idx+50]!r}")

new_src = src[:start_idx] + src[end_idx:]

# Verify AST
import ast
try:
    ast.parse(new_src)
    print("AST parses OK")
except SyntaxError as e:
    print(f"AST parse error at line {e.lineno}: {e.text!r}")
    raise

p.write_text(new_src, encoding="utf-8")
print(f"Done. Removed {end_idx - start_idx} chars. New length: {len(new_src)}")
