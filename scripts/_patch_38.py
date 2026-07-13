"""Patch all 'except Exception as e: raise HTTPException(500, f"X action failed: {e}")'
to add 'except HTTPException: raise' before it."""
import re

p = r"D:\MoA Gateway Pro\moa_gateway\server.py"
content = open(p, encoding='utf-8').read()
# Pattern: '            except Exception as e:\n                raise HTTPException(500, f"X action failed: {e}")'
pat = re.compile(
    r'(\s+)except Exception as e:\n(\s+)raise HTTPException\(500, f"(\w+) action failed: \{e\}"\)',
    re.MULTILINE
)

def repl(m):
    indent1 = m.group(1)
    indent2 = m.group(2)
    name = m.group(3)
    return (
        f'{indent1}except HTTPException:\n{indent1}    raise  # 修 38: 让 4xx 直接返回(不被包 500)\n'
        f'{indent1}except Exception as e:\n{indent2}raise HTTPException(500, f"{name} action failed: {{e}}")'
    )

new = pat.sub(repl, content)
n = len(pat.findall(content))
print(f'replaced {n} patterns')
open(p, 'w', encoding='utf-8').write(new)
