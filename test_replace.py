from pathlib import Path
p = Path(r"D:\MoA Gateway Pro\moa_gateway\server.py")
src = p.read_text(encoding="utf-8")
src = src.replace("raise _err_500(e, 'MoA run failed:')", "raise _err_500(e, 'MoA run failed')", 2)
p.write_text(src, encoding="utf-8")
print('done')
