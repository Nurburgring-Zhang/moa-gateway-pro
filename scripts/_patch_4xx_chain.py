"""Add 'from <var>' to all 'raise XError(...)' inside 'except X as <var>:' blocks."""
import re
from pathlib import Path

FILES = [
    r"D:\MoA Gateway Pro\moa_gateway\providers\anthropic_provider.py",
    r"D:\MoA Gateway Pro\moa_gateway\providers\openai_compat.py",
    r"D:\MoA Gateway Pro\moa_gateway\server.py",
    r"D:\MoA Gateway Pro\moa_gateway\moa.py",
]

total = 0
for fpath in FILES:
    p = Path(fpath)
    if not p.exists():
        continue
    src = p.read_text(encoding="utf-8")
    lines = src.split("\n")
    patched = 0
    for i in range(len(lines)):
        # match 'except XXXError as e:'
        m = re.match(r"^(\s+)except\s+\w+(?:Error)?\s+as\s+(\w+)\s*:\s*$", lines[i])
        if not m:
            continue
        var = m.group(2)
        # Look ahead 5 lines
        for j in range(i + 1, min(i + 6, len(lines))):
            lj = lines[j]
            mm = re.match(r"^(\s+)raise\s+(\w+)\((.+)\)\s*$", lj)
            if not mm:
                continue
            raise_indent = mm.group(1)
            if not raise_indent.startswith(m.group(1)):
                break
            if " from " in lj:
                break
            lines[j] = lj.rstrip() + f" from {var}"
            patched += 1
            break
    p.write_text("\n".join(lines), encoding="utf-8")
    if patched:
        print(f"{fpath}: patched {patched}")
        total += patched
print(f"total: {total}")
