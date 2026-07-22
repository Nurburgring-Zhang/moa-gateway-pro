import urllib.request, json, os

req = urllib.request.Request(
    'http://127.0.0.1:8088/api/auth/login',
    data=json.dumps({'username': 'admin', 'password': 'TestPass#2024'}).encode(),
    headers={'Content-Type': 'application/json'},
)
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read()).get('token', '')

req2 = urllib.request.Request(
    'http://127.0.0.1:8088/api/api-keys',
    data=json.dumps({'name': 'perf-test-2', 'quota_rpm': 100000, 'quota_daily_tokens': 999999999}).encode(),
    headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'},
)
r2 = urllib.request.urlopen(req2, timeout=5)
d = json.loads(r2.read())
print('CREATE response keys:', list(d.keys()))
print('FULL:', json.dumps(d, indent=2)[:500])
