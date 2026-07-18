import urllib.request
import json
r = urllib.request.urlopen('https://api.github.com/repos/Nurburgring-Zhang/moa-gateway-pro/releases', timeout=10)
data = json.loads(r.read())
for rel in data[:5]:
    name = rel['tag_name']
    title = rel['name']
    assets = len(rel.get('assets', []))
    pub = rel.get('published_at')
    print(f"{name} | {title} | assets: {assets} | published: {pub}")
