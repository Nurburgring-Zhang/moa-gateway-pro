import urllib.request, json
r = urllib.request.urlopen('http://127.0.0.1:8088/openapi.json', timeout=5)
data = json.loads(r.read())
schemas = data.get('components', {}).get('schemas', {})
print('total schemas:', len(schemas))
for n in sorted(schemas.keys())[:5]:
    s = schemas[n]
    props = s.get('properties', {})
    print('  ' + n + ':', len(props), 'fields')
for name in ['CreateMoaEvalRequest', 'CreateMoaSimilarityRequest', 'CreateMoaEngineRequest']:
    print('  ' + name + ':', 'YES' if name in schemas else 'NO')
