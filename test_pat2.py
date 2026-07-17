from pathlib import Path
src = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py").read_text(encoding="utf-8")
# Show actual line for one match
idx = src.find('raise HTTPException(500, f"MoA run failed: {e}")')
print('idx:', idx)
if idx >= 0:
    print('chunk:', repr(src[idx-10:idx+80]))
