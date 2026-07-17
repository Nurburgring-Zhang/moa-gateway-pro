from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Look for the actual char in the f-string
idx = src.find('MoA run failed')
print('idx:', idx)
chunk = src[idx-30:idx+50]
print('chunk:', repr(chunk))
print('bytes:')
for c in chunk:
    print(f'  {ord(c):5d}  {c!r}')
