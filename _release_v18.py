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
        if "@github.com" in out:
            token_part = out.split("://", 1)[1].split("@", 1)[0]
            if token_part and token_part != "Nurburgring-Zhang":
                return token_part
    except Exception:
        pass
    return ""

RELEASE_NOTES = """# MoA Gateway Pro v1.8.0 — Pydantic + OpenAPI Auto Docs

**Release Date**: 2026-07-18
**Previous Version**: v1.7.5

## What's New

### Pydantic BaseModel for all 73+ endpoints
- Auto-generated Pydantic request models for every endpoint
- 90 OpenAPI schemas in `/openapi.json` (was 0)
- Full Swagger UI integration at `/docs`
- Automatic 422 validation with field-level error messages

### Backwards Compatible
- Added `_DictLikeMixin` — endpoints using `body.get("key")` and `body["key"]` work unchanged
- All existing tests pass: 137/137 basic + 512/512 deep + 12/12 security + 7/7 workflows

### Architecture (carried from v1.7.0)
- 11 services / 176 methods via Service Layer
- AgentDispatch single entry point
- Workflow engine with real inter-module data flow
- 7 builtin workflow templates

## Performance
- 7193 RPS on concurrent /health (p50=0.81ms)
- All previous benchmarks intact

## Files Added
- `moa_gateway/req_models.py` — 90 Pydantic models (auto-generated)

## Files Modified
- `moa_gateway/server.py` — 83 endpoints now use Pydantic request models
"""

# 1. Create release
url = 'https://api.github.com/repos/Nurburgring-Zhang/moa-gateway-pro/releases'
data = {
    'tag_name': 'v1.8.0',
    'name': 'MoA Gateway Pro v1.8.0 - Pydantic + OpenAPI Auto Docs',
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
    upload_url = result.get('upload_url', '').replace('{?name,label}', '?name=MoA-Gateway-Pro-v1.8.0.zip')
    # 2. Upload asset
    with open(r'D:\MoA Gateway Pro\zip\MoA Gateway Pro v1.8.0.zip', 'rb') as f:
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
