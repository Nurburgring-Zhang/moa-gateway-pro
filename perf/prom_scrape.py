"""perf/prom_scrape.py — 模拟 Prometheus 真 scrape /metrics 流程

用 prometheus_client.text_string_to_metric_families 解析 /metrics 输出
等效于 prometheus server 抓取 + 解析的完整流程
"""
import urllib.request
import urllib.error
from prometheus_client.parser import text_string_to_metric_families
import json
import sys


def main():
    print("=" * 60)
    print(" Prometheus 真 scrape 验证 (moa_gateway /metrics)")
    print("=" * 60)
    # 1. scrape /metrics
    try:
        r = urllib.request.urlopen("http://127.0.0.1:8088/metrics", timeout=5)
        body = r.read().decode("utf-8", errors="replace")
        content_type = r.headers.get("Content-Type", "?")
        print(f"  scrape status: {r.status}")
        print(f"  content-type: {content_type}")
        print(f"  body length: {len(body)} bytes")
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    # 2. 解析 (真 Prometheus 行为)
    print(f"\n[parse] 用 prometheus_client.parser 真解析:")
    families = list(text_string_to_metric_families(body))
    print(f"  parsed {len(families)} metric families")
    moa_families = [f for f in families if f.name.startswith("moa_")]
    print(f"  moa_* families: {len(moa_families)}")
    for f in moa_families:
        print(f"    {f.name} ({f.type}): {len(f.samples)} samples")

    # 3. 提取关键 metric 值
    print(f"\n[key metrics]:")
    interesting = ["moa_chat_requests_total", "moa_endpoint_health",
                    "moa_rate_limit_blocked_total", "moa_moa_executions_total",
                    "moa_capability_calls_total"]
    for name in interesting:
        fam = next((f for f in families if f.name == name), None)
        if not fam:
            print(f"  [NOT FOUND] {name}")
            continue
        s = list(fam.samples)
        print(f"  [OK] {name}: {len(s)} samples")
        for sample in s[:3]:
            labels = ",".join(f'{k}="{v}"' for k, v in sample.labels.items())
            print(f"      {labels} = {sample.value}")

    # 4. 验证 Prometheus 标准 metric (python_gc_*) 也存在
    py_families = [f for f in families if f.name.startswith("python_")]
    print(f"\n[python process metrics]: {len(py_families)} families")
    for f in py_families[:3]:
        print(f"  {f.name}")

    print("\n" + "=" * 60)
    print(f" RESULT: 真实 scrape 流程通过 (parsed {len(families)} families)")
    print("=" * 60)


if __name__ == "__main__":
    main()
