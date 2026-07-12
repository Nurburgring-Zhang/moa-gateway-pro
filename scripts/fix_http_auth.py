from pathlib import Path
fp = 'D:/MoA Gateway Pro/moa_gateway/ui/pages.py'
text = Path(fp).read_text(encoding='utf-8')

old = '''async def http_get(url: str, timeout: float = 10) -> Dict:
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url)
        return r.json() if r.status_code == 200 else {"error": r.text}


async def http_post(url: str, payload: Dict, timeout: float = 60) -> Dict:
    import httpx
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload)
        return r.json() if r.status_code in (200, 201) else {"error": r.text}'''

new = '''async def http_get(url: str, timeout: float = 10, token: str = None) -> Dict:
    import httpx
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.get(url, headers=headers)
        return r.json() if r.status_code == 200 else {"error": r.text}


async def http_post(url: str, payload: Dict, timeout: float = 60, token: str = None) -> Dict:
    import httpx
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout) as c:
        r = await c.post(url, json=payload, headers=headers)
        return r.json() if r.status_code in (200, 201) else {"error": r.text}'''

assert old in text
text = text.replace(old, new)
Path(fp).write_text(text, encoding='utf-8')
print("OK - http_get/post accept token")
