import urllib.request, json

# login
req = urllib.request.Request('http://127.0.0.1:8088/api/auth/login', data=json.dumps({'username':'admin','password':'TestPass#2024'}).encode(), headers={'Content-Type':'application/json'})
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read())['token']
# get key
req2 = urllib.request.Request('http://127.0.0.1:8088/api/api-keys', data=json.dumps({'name':'final_test','quota_rpm':1000,'quota_daily_tokens':10000000}).encode(), headers={'Content-Type':'application/json','Authorization':f'Bearer {token}'})
r2 = urllib.request.urlopen(req2, timeout=5)
api_key = json.loads(r2.read())['key']
# list agents
req3 = urllib.request.Request('http://127.0.0.1:8088/v1/agent/list', headers={'Authorization':f'Bearer {api_key}'})
r3 = urllib.request.urlopen(req3, timeout=5)
agents = json.loads(r3.read())['agents']
total_methods = sum(len(a['methods']) for a in agents)
print(f'Total services: {len(agents)}')
print(f'Total methods: {total_methods}')
for a in agents:
    name = a["name"]
    n = len(a["methods"])
    print(f'  {name}: {n} methods')

# also list workflows
req4 = urllib.request.Request('http://127.0.0.1:8088/v1/agent/workflows', headers={'Authorization':f'Bearer {api_key}'})
r4 = urllib.request.urlopen(req4, timeout=5)
workflows = json.loads(r4.read())['workflows']
print(f'\nTotal workflows: {len(workflows)}')
for w in workflows:
    n_steps = len(w.get('steps', []))
    print(f'  {w["name"]}: {n_steps} steps')
