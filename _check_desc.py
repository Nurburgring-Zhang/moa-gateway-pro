"""验证 v1.8.1 Field description 在 OpenAPI 里生效。"""
import json
import urllib.request

r = urllib.request.urlopen('http://127.0.0.1:8088/openapi.json', timeout=5)
data = json.loads(r.read())
schemas = data['components']['schemas']
print('total schemas:', len(schemas))

# 抽 5 个 model 看 description
samples = [
    'CreateMoaEvalRequest',
    'CreateMoaSimilarityRequest',
    'CreateChatCompletionRequest',  # 也许不存在
    'CreateNLayerMoaRequest',
    'CreateCapabilityRequest',
    'CreateConsensusRequest',
]
for name in samples:
    if name not in schemas:
        print(f'  {name}: NOT FOUND')
        continue
    schema = schemas[name]
    fields = schema.get('properties', {})
    desc_count = sum(1 for v in fields.values() if v.get('description'))
    total = len(fields)
    print(f'  {name}: {desc_count}/{total} fields have description')
    for k, v in list(fields.items())[:3]:
        d = v.get('description', '<NONE>')
        print(f'    {k}: {d}')

# 全局统计
total_fields = 0
fields_with_desc = 0
for s in schemas.values():
    for k, v in s.get('properties', {}).items():
        total_fields += 1
        if v.get('description'):
            fields_with_desc += 1
print(f'\nGLOBAL: {fields_with_desc}/{total_fields} fields have description ({100*fields_with_desc/total_fields:.1f}%)')
