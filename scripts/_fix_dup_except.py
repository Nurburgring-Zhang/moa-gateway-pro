"""Fix duplicate 'except HTTPException: raise' clauses in server.py.

The pattern is:
    try:
        ...
    except HTTPException: raise  # 修 38: ...
                              <empty line>
    except HTTPException: raise  # patch v1.6.6: ...

We want to keep only one (the first) and remove the duplicate.
"""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")

# Pattern: an "except HTTPException: raise" line followed by an empty line and another
# "except HTTPException: raise" line. Remove the second one and the empty line.
pattern = re.compile(
    r"(\bexcept HTTPException:\s*raise[^\n]*\n)"  # first clause
    r"\s*\n"  # blank line(s)
    r"(\s*except HTTPException:\s*raise[^\n]*\n)"  # duplicate clause
)

new = pattern.sub(r"\1", src)
# Also catch the case where there's no blank line between two except clauses
pattern2 = re.compile(
    r"(\bexcept HTTPException:\s*raise[^\n]*\n)"
    r"(\s*except HTTPException:\s*raise[^\n]*\n)"
)
new = pattern2.sub(r"\1", new)

# Verify AST
import ast
try:
    ast.parse(new)
    print("AST parses OK")
except SyntaxError as e:
    print(f"AST parse error: {e}")
    # try to find it
    raise

removed = src.count("except HTTPException: raise") - new.count("except HTTPException: raise")
print(f"removed {removed} duplicate except clauses")
print(f"before: {src.count('except HTTPException: raise')}, after: {new.count('except HTTPException: raise')}")
p.write_text(new, encoding="utf-8")
