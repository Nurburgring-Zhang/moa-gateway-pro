import re
from pathlib import Path
import os
ROOT = "D:/MoA Gateway Pro" if os.name == "nt" else "/d/MoA Gateway Pro"
text = Path(f"{ROOT}/moa_gateway/webui.py").read_text(encoding='utf-8')
paths = re.findall(r'@app\.(\w+)\("([^"]+)"', text)
for method, path in paths:
    print(method.upper(), path)
