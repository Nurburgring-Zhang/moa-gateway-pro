"""更激进批量修:把所有 sr.loop + run_coroutine_threadsafe + fut.result 模式统一改成 sr.call_async"""
import re
from pathlib import Path

files = [
    "D:/MoA Gateway Pro/moa_gateway/ui/pages.py",
    "D:/MoA Gateway Pro/moa_gateway/ui/pages2.py",
]

# 通用模式:无论缩进和 except 处理
# 抓取:
#   if sr.loop[ and sr.loop.is_running()]:
#       fut = asyncio.run_coroutine_threadsafe(do(), sr.loop)
#       try:
#           <var> = fut.result(timeout=<tmo>)
#       except [Exception as ex]:
#           <return OR data = {"error": str(ex)}>
# 替换为:
#   <var> = sr.call_async(do(), timeout=<tmo>)
#   if <var> is None:
#       [对应的处理]

# 因为模式太多变,直接用更宽松的匹配
# 模式 A:except 后是 return(2 种缩进)
PAT_A = re.compile(
    r'^( *)if sr\.loop(?:\s+and\s+sr\.loop\.is_running\(\))?:\n'
    r' *fut = asyncio\.run_coroutine_threadsafe\(do\(\), sr\.loop\)\n'
    r' *try:\n'
    r' *(\w+) = fut\.result\(timeout=(\d+)\)\n'
    r' *except(?: Exception)?(?: as ex)?:\n'
    r' *return\n',
    re.MULTILINE
)

# 模式 B:except 后是 data = {"error": str(ex)}(用于先设默认值)
PAT_B = re.compile(
    r'^( *)if sr\.loop(?:\s+and\s+sr\.loop\.is_running\(\))?:\n'
    r' *fut = asyncio\.run_coroutine_threadsafe\(do\(\), sr\.loop\)\n'
    r' *try:\n'
    r' *(\w+) = fut\.result\(timeout=(\d+)\)\n'
    r' *except(?: Exception)?(?: as ex)?:\n'
    r' *\w+ = \{"error": str\(ex\)\}\n',
    re.MULTILINE
)

def rep_a(m):
    indent = m.group(1)
    var = m.group(2)
    tmo = m.group(3)
    return (
        f'{indent}{var} = sr.call_async(do(), timeout={tmo})\n'
        f'{indent}if {var} is None:\n'
        f'{indent}    return\n'
    )

def rep_b(m):
    indent = m.group(1)
    var = m.group(2)
    tmo = m.group(3)
    return (
        f'{indent}{var} = sr.call_async(do(), timeout={tmo})\n'
        f'{indent}if {var} is None:\n'
        f'{indent}    {var} = {{"error": "call_async timeout/failed"}}\n'
    )

for fp in files:
    text = Path(fp).read_text(encoding="utf-8")

    text, na = PAT_A.subn(rep_a, text)
    text, nb = PAT_B.subn(rep_b, text)

    Path(fp).write_text(text, encoding="utf-8")
    print(f"{fp}: 模式A={na}, 模式B={nb}")

# 重新数剩余
print("\n剩余 sr.loop:")
for fp in files:
    text = Path(fp).read_text(encoding="utf-8")
    for i, line in enumerate(text.split("\n"), 1):
        if "sr.loop" in line:
            print(f"  {fp}:{i}: {line.strip()[:80]}")