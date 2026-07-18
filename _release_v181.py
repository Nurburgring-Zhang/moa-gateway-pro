import urllib.request
import json
import os
os.environ['PYTHONIOENCODING'] = 'utf-8'

# Read PAT from git remote URL or env (NEVER hardcode)
def _get_pat():
    pat = os.environ.get("GITHUB_PAT")
    if pat:
        return pat
    try:
        out = os.popen("git config --get remote.origin.url").read().strip()
        # https://USER:TOKEN@github.com/...
        if "@github.com" in out and "://" in out and "@" in out.split("://", 1)[1]:
            after_scheme = out.split("://", 1)[1]
            user_token = after_scheme.split("@", 1)[0]
            if ":" in user_token:
                return user_token.split(":", 1)[1]
    except Exception:
        pass
    return ""

with open(r'D:\MoA Gateway Pro\RELEASE_NOTES_v1.8.md', encoding='utf-8') as f:
    RELEASE_NOTES = f.read()

# 1. Create release
url = 'https://api.github.com/repos/Nurburgring-Zhang/moa-gateway-pro/releases'
data = {
    'tag_name': 'v1.8.1',
    'name': 'MoA Gateway Pro v1.8.1 - OpenAPI field docs + endpoint signature cleanup',
    'body': RELEASE_NOTES,
    'draft': False,
    'prerelease': False,
}
req = urllib.request.Request(
    url,
    data=json.dumps(data).encode(),
    headers={
        'Authorization': f'token {_get_pat()}',
        'Accept': 'application/vnd.github.v3+json',
        'Content-Type': 'application/json',
        'User-Agent': 'Mavis-Deployer',
    },
    method='POST',
)
try:
    r = urllib.request.urlopen(req, timeout=30)
    result = json.loads(r.read())
    print('release created:', result.get('html_url'))
    upload_url = result.get('upload_url', '').replace('{?name,label}', '?name=MoA-Gateway-Pro-v1.8.1.zip')
    # 2. Upload asset
    zip_path = r'D:\MoA Gateway Pro\zip\MoA Gateway Pro v1.8.1.zip'
    with open(zip_path, 'rb') as f:
        zip_data = f.read()
    req2 = urllib.request.Request(
        upload_url,
        data=zip_data,
        headers={
            'Authorization': f'token {_get_pat()}',
            'Accept': 'application/vnd.github.v3+json',
            'Content-Type': 'application/zip',
            'User-Agent': 'Mavis-Deployer',
        },
        method='POST',
    )
    r2 = urllib.request.urlopen(req2, timeout=120)
    asset = json.loads(r2.read())
    print('zip uploaded:', asset.get('browser_download_url'))
    print('size:', asset.get('size'), 'bytes')
except urllib.error.HTTPError as e:
    body = e.read().decode('utf-8', errors='replace')
    print('HTTP', e.code, ':', body[:500])
