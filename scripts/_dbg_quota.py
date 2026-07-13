"""Start server in background, then test."""
import sys, os, time
sys.path.insert(0, '.')
os.environ['MOA_ADMIN_PASSWORD'] = 'TestPass#2024'
os.environ['PYTHONPATH'] = '.'

from moa_gateway.server import create_app
import uvicorn
import threading
import urllib.request, urllib.parse, json

app = create_app()
config = uvicorn.Config(app, host='127.0.0.1', port=8088, log_level='warning')
server = uvicorn.Server(config)
t = threading.Thread(target=server.run, daemon=True)
t.start()
time.sleep(4)

def call(method, path, headers=None, body=None):
    headers = headers or {}
    data = json.dumps(body).encode() if body else None
    if data:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(f'http://127.0.0.1:8088{path}', data=data, headers=headers, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=30)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

# login
s, b = call('POST', '/api/auth/login', body={'username':'admin','password':'TestPass#2024'})
print(f'login: {s} {b[:100]}')
admin_token = json.loads(b)['token']

# /v1/quota with admin JWT
s, b = call('GET', '/v1/quota', headers={'Authorization': f'Bearer {admin_token}'})
print(f'v1/quota (admin JWT): {s} {b[:300]}')

# create api key
s, b = call('POST', '/api/api-keys', headers={'Authorization': f'Bearer {admin_token}'}, body={'name':'dbg','quota_rpm':100,'quota_daily_tokens':1000000})
print(f'create key: {s} {b[:200]}')
key = json.loads(b)['key']

s, b = call('GET', '/v1/quota', headers={'Authorization': f'Bearer {key}'})
print(f'v1/quota (api key): {s} {b[:300]}')

# feedback-iter
s, b = call('POST', '/v1/capability/feedback-iter',
    headers={'Authorization': f'Bearer {key}'},
    body={'record': {'iter_idx':0,'proposals':['x'],'panel_scores':{0:40.0},'convergent_ideas':[],'conflicts_resolved':[],'selected_proposal_idx':0,'timestamp':100.0}})
print(f'feedback-iter: {s} {b[:400]}')

# /v1/chat/completions
s, b = call('POST', '/v1/chat/completions',
    headers={'Authorization': f'Bearer {key}'},
    body={'model': 'auto', 'messages': [{'role': 'user', 'content': 'Hi'}]})
print(f'chat completions: {s} {b[:300]}')

server.should_exit = True
time.sleep(1)
