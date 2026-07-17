"""Find bare except Exception referencing undefined 'e'."""
import re

with open(r'D:\MoA Gateway Pro\moa_gateway\server.py', encoding='utf-8') as f:
    src = f.read()

# Find all 'except Exception:' (without 'as e')
bare_re = re.compile(r'^(\s*)except Exception\s*:\s*$', re.MULTILINE)
bares = []
for m in bare_re.finditer(src):
    line_num = src[:m.start()].count('\n') + 1
    # Find the body (next 1-5 lines)
    body_start = m.end()
    body_end = body_start + 600
    body = src[body_start:body_end]
    # Check if body references 'e' but no 'as e' is in scope
    # Look for f"...{e}..." or f':e}}' or 'logger.exception("...", e)'
    has_e_ref = bool(re.search(r'\{e\}|%s.*?, ?e\b|raise .*?from e', body))
    if has_e_ref:
        # Get the line content
        lines_after = body.split('\n')[:6]
        bares.append((line_num, lines_after))

print(f"Total bare 'except Exception:' referencing 'e': {len(bares)}")
print()
for line_num, lines_after in bares:
    print(f"=== L{line_num} ===")
    for i, l in enumerate(lines_after[:6]):
        print(f"  L{line_num + i}: {l}")
    print()
