import json, urllib.request, urllib.error, sys
sys.path.insert(0, '.')
from moa_gateway.storage import get_storage
s = get_storage()
key = s.create_api_key(name='diag3', quota_rpm=1000, quota_daily_tokens=999999999)['key']
h = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}

def call(method, path, body=None, q=None):
    url = f'http://127.0.0.1:8911{path}'
    if q: url += '?' + q
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=15)
        body = r.read().decode('utf-8', errors='replace')
        return f'status={r.status} body[{len(body)}]={body[:300]}'
    except urllib.error.HTTPError as e:
        return f'HTTP {e.code} body={e.read().decode("utf-8", errors="replace")[:300]}'
    except Exception as e:
        return f'ERR {type(e).__name__}: {e}'

for n, m, p, b, q in [
    ('1. endpoints', 'GET', '/api/endpoints', None, None),
    ('2. presets', 'GET', '/v1/moa/presets', None, None),
    ('3. prompts', 'GET', '/v1/moa/prompts', None, None),
    ('4. benchmark', 'POST', '/v1/moa/benchmark', {}, 'preset=fast&max_questions=1'),
]:
    print(f'=== {n} ===')
    print(call(m, p, b, q))
    print()
