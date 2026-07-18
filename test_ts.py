import urllib.request, json
# login
req = urllib.request.Request('http://127.0.0.1:8088/api/auth/login', data=json.dumps({'username':'admin','password':'TestPass#2024'}).encode(), headers={'Content-Type':'application/json'})
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read())['token']
# get key
req2 = urllib.request.Request('http://127.0.0.1:8088/api/api-keys', data=json.dumps({'name':'p1','quota_rpm':1000,'quota_daily_tokens':10000000}).encode(), headers={'Content-Type':'application/json','Authorization':f'Bearer {token}'})
r2 = urllib.request.urlopen(req2, timeout=5)
api_key = json.loads(r2.read())['key']
# call tool-screening
body = {'tool_name': 'exec', 'arguments': {'cmd': 'rm -rf /'}}
req3 = urllib.request.Request('http://127.0.0.1:8088/v1/capability/tool-screening', data=json.dumps(body).encode(), headers={'Content-Type':'application/json','Authorization':f'Bearer {api_key}'})
r3 = urllib.request.urlopen(req3, timeout=10)
print(json.loads(r3.read()))
