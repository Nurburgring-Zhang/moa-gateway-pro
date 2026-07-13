"""Debug canary endpoint 500."""
import sys, os, time
sys.path.insert(0, '.')
os.environ['MOA_ADMIN_PASSWORD'] = 'TestPass#2024'
os.environ['PYTHONPATH'] = '.'

from moa_gateway.server import create_app
import uvicorn, threading, urllib.request, urllib.error, json

app = create_app()
config = uvicorn.Config(app, host='127.0.0.1', port=8089, log_level='info')
server = uvicorn.Server(config)
t = threading.Thread(target=server.run, daemon=True); t.start()
time.sleep(4)

def call(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body else None
    h = {"Content-Type": "application/json"}
    if headers: h.update(headers)
    req = urllib.request.Request(f'http://127.0.0.1:8089{path}', data=data, headers=h, method=method)
    try:
        r = urllib.request.urlopen(req, timeout=15)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

s, b = call('POST', '/api/auth/login', body={'username':'admin','password':'TestPass#2024'})
print(f'login: {s} {b[:80]}')
admin_token = json.loads(b)['token']
admin_h = {'Authorization': f'Bearer {admin_token}'}

# canary inject
s, b = call('POST', '/v1/capability/canary', body={'action':'inject','prompt':'Hello','strategy':'suffix'}, headers=admin_h)
print(f'inject: {s} {b[:300]}')

# canary check
s, b = call('POST', '/v1/capability/canary', body={'action':'check','response':'hi moa_canary_xxx','canary':'moa_canary_xxx'}, headers=admin_h)
print(f'check: {s} {b[:300]}')

server.should_exit = True
time.sleep(1)
