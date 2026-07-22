"""perf/osv_scan.py — 用 OSV.dev API 扫描 requirements.txt 里的依赖漏洞

OSV.dev 提供 free public API,POST /v1/query 一次查多个包
"""
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path


def main():
    print("=" * 60)
    print(" OSV.dev 依赖漏洞扫描")
    print("=" * 60)

    # 读 _reqs.txt
    reqs_path = Path("_reqs.txt")
    if not reqs_path.exists():
        print("  _reqs.txt not found")
        sys.exit(1)
    deps = []
    for line in reqs_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "==" in line:
            name, ver = line.split("==", 1)
            deps.append({"name": name.strip(), "version": ver.strip()})

    print(f"  {len(deps)} deps to query")

    # 批量查 OSV
    queries = [{"package": {"name": d["name"], "ecosystem": "PyPI"}, "version": d["version"]} for d in deps]
    req = urllib.request.Request(
        "https://api.osv.dev/v1/querybatch",
        data=json.dumps({"queries": queries}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        r = urllib.request.urlopen(req, timeout=30)
        data = json.loads(r.read())
    except Exception as e:
        print(f"  ERROR: {e}")
        sys.exit(1)

    results = data.get("results", [])
    vuln_count = 0
    vuln_deps = []
    for i, result in enumerate(results):
        vulns = result.get("vulns", [])
        if vulns:
            dep = deps[i]
            vuln_count += len(vulns)
            vuln_deps.append((dep, vulns))

    print(f"\n  Total: {len(deps)} deps, {vuln_count} vulnerabilities")
    if vuln_deps:
        print(f"\n  [VULNERABLE] {len(vuln_deps)} deps:")
        for dep, vulns in vuln_deps:
            print(f"  {dep['name']} {dep['version']}: {len(vulns)} CVEs")
            for v in vulns[:2]:
                summary = v.get("summary", v.get("details", "")[:80])
                print(f"    {v['id']}: {summary[:80]}")
    else:
        print("  [CLEAN] no known vulnerabilities")

    print("\n" + "=" * 60)
    print(f" RESULT: {len(deps)} deps scanned, {vuln_count} vulns found")
    print("=" * 60)


if __name__ == "__main__":
    main()
