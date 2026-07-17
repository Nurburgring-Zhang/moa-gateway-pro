from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Print actual bytes around the match
chunk = src[43600:43720]
print('len:', len(chunk))
print('first 80:', repr(chunk[:80]))
print('chars in chunk:')
for c in chunk[:60]:
    print(f'  {ord(c):5d}  {c!r}')
