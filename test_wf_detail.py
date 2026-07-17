import json, urllib.request

# login
req = urllib.request.Request('http://127.0.0.1:8088/api/auth/login', data=json.dumps({'username':'admin','password':'TestPass#2024'}).encode(), headers={'Content-Type':'application/json'})
r = urllib.request.urlopen(req, timeout=5)
token = json.loads(r.read())['token']
# get key
req2 = urllib.request.Request('http://127.0.0.1:8088/api/api-keys', data=json.dumps({'name':'check_wf','quota_rpm':1000,'quota_daily_tokens':10000000}).encode(), headers={'Content-Type':'application/json','Authorization':f'Bearer {token}'})
r2 = urllib.request.urlopen(req2, timeout=5)
api_key = json.loads(r2.read())['key']
# run workflow
body = {
    'name': 'moa_quality_pipeline',
    'input': {
        'query': 'Write a Python function to compute factorial',
        'proposers': [{'model_id': 'gpt-4o-mock', 'system_prompt': 'be concise'}],
        'aggregator': {'model_id': 'gpt-4o-mock', 'synthesis_prompt': 'synth'},
    },
}
req3 = urllib.request.Request('http://127.0.0.1:8088/v1/agent/workflow/run', data=json.dumps(body).encode(), headers={'Content-Type':'application/json','Authorization':f'Bearer {api_key}'})
r3 = urllib.request.urlopen(req3, timeout=60)
result = json.loads(r3.read())
print('OK:', result.get('ok'))
print('Steps:')
for name, step in result.get('steps', {}).items():
    print('  ' + name + ': ok=' + str(step.get('ok')) + ' latency=' + str(step.get('latency_ms', 0)) + 'ms')
    if step.get('ok'):
        data = step.get('data', {})
        if isinstance(data, dict):
            keys = list(data.keys())[:5]
            print('    data keys:', keys)
