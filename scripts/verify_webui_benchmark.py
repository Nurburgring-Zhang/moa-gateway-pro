"""WebUI benchmark 页面完整性检查"""
import re
from pathlib import Path

p = Path(r"D:\MoA Gateway Pro\moa_gateway\webui\index.html")
content = p.read_text(encoding="utf-8")
print(f"WebUI total: {len(content)} bytes")

funcs = ["runBenchmarkSuite", "runCostPareto", "runLayeredFlow", "runFlaskScore",
         "renderBenchmarkSummary", "renderBenchmarkDetail", "renderCostPareto",
         "renderLayeredDiagram", "renderLayeredSVG", "renderFlaskResult",
         "populateLayeredPresets", "escapeHtml"]
print()
print("JS Functions:")
for f in funcs:
    count = len(re.findall(rf"function {f}\b", content))
    print(f"  {'OK ' if count > 0 else 'NO '} {f:30s} -> {count}")

print()
print("Tab IDs:")
for t in ["bench-tab-suite", "bench-tab-pareto", "bench-tab-layered", "bench-tab-flask"]:
    count = len(re.findall(rf'id="{t}"', content))
    print(f"  {'OK ' if count > 0 else 'NO '} {t:30s} -> {count}")

print()
print(f"page-benchmark div: {len(re.findall(r'id=.page-benchmark.', content))}")
print(f"data-page=benchmark nav: {len(re.findall(r'data-page=.benchmark.', content))}")

# SVG 模板
print()
print("SVG render functions found:")
for s in ["svg = `<svg", "build SVG", "polyPath"]:
    print(f"  '{s}' found: {s in content}")

# 数据元素
print()
print("HTML elements:")
for e in ["bench-suite-summary", "bench-suite-detail", "bench-pareto-result",
          "bench-layered-result", "bench-flask-result", "pareto-presets",
          "layered-preset", "flask-query", "flask-response"]:
    count = len(re.findall(rf'id="{e}"', content))
    print(f"  {'OK ' if count > 0 else 'NO '} #{e:30s} -> {count}")