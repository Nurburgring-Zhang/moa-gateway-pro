"""修 5 处 lambda 内部加 import threading"""
from pathlib import Path
import re

for fp in [
    "D:/MoA Gateway Pro/moa_gateway/ui/pages.py",
    "D:/MoA Gateway Pro/moa_gateway/ui/pages2.py",
]:
    text = Path(fp).read_text(encoding="utf-8")
    pat = re.compile(
        r"sr\.on_started_callbacks\.append\(lambda: threading\.Thread\(target=(\w+), daemon=True\)\.start\(\)\)",
        re.MULTILINE,
    )
    def rep(m):
        target = m.group(1)
        return "sr.on_started_callbacks.append(lambda: __import__('threading').Thread(target={}, daemon=True).start())".format(target)
    text, n = pat.subn(rep, text)
    Path(fp).write_text(text, encoding="utf-8")
    print("{}: {} 处修".format(fp, n))