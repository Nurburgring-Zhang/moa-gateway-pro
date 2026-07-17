"""Read the test results file (which has 'Admin token:' prefix line)."""
with open(r'D:\MoA Gateway Pro\test_results_v2.json', encoding='utf-8', errors='replace') as f:
    raw = f.read()
# Find first { that starts the JSON
idx = raw.find('{\n  "total"')
if idx < 0:
    idx = raw.find('{"total"')
content = raw[idx:] if idx >= 0 else raw
import json
d = json.loads(content)
print('Total:', d['total'])
print('2xx:', d['ok_2xx_count'])
print('5xx:', d['5xx_count'])
print('By status:')
for k, v in d['by_status'].items():
    print(f'  {k}: {v}')
print()
print('=== 5xx endpoints ===')
for r in d['results']:
    if r.get('is_5xx'):
        print(f"  [{r['i']}] {r['method']} {r['path']} (auth={r['auth']}): {r['snippet'][:200]}")
print()
print('=== Non-2xx non-5xx ===')
for r in d['results']:
    s = r.get('status')
    if not r.get('ok_2xx') and not r.get('is_5xx') and s != 'ERR':
        print(f"  [{r['i']}] {r['method']} {r['path']} (auth={r['auth']}): {s} - {r['snippet'][:200]}")
