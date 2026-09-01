"""统计真实注册的 API 端点数（穿透 FastAPI _IncludedRouter 惰性包装）"""
import os, sys

os.environ.setdefault("MOA_GATEWAY_KEY", "count-endpoints-key")
sys.path.insert(0, ".")

from fastapi.routing import APIRoute
from moa_gateway.server import app

pairs = []
seen_paths = set()

def collect(routes, prefix=""):
    for r in routes:
        tn = type(r).__name__
        if tn == "_IncludedRouter":
            orig = getattr(r, "original_router", None)
            if orig is not None:
                collect(orig.routes, prefix)
        elif isinstance(r, APIRoute):
            for m in sorted(r.methods or []):
                if m == "HEAD":
                    continue
                pairs.append((m, r.path))
                seen_paths.add(r.path)

collect(app.routes)

print(f"唯一路径数: {len(seen_paths)}")
print(f"(method, path) 端点对数: {len(pairs)}")

from collections import Counter
mc = Counter(m for m, _ in pairs)
print("按方法分布:", dict(mc))

with open("endpoint_list.txt", "w", encoding="utf-8") as f:
    for m, p in sorted(pairs, key=lambda x: (x[1], x[0])):
        f.write(f"{m} {p}\n")
print("清单已写入 endpoint_list.txt")
