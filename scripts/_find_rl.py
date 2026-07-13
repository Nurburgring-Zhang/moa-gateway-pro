"""Find endpoints that call check_and_incr."""
import re
content = open(r'D:\MoA Gateway Pro\moa_gateway\server.py', encoding='utf-8').read()
pat = re.compile(r'async def (\w+)\([^)]*\).*?check_and_incr', re.DOTALL)
for m in pat.finditer(content):
    pre = content[:m.start()]
    last = None
    for appm in re.finditer(r'@app\.(get|post|put|delete)\("([^"]+)"\)', pre):
        last = (appm.group(1), appm.group(2))
    if last:
        print(f'{last[1]:40s} ({last[0]}) -> {m.group(1)}')
