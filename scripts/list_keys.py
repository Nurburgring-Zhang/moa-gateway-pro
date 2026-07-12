"""查 storage 现有 API key"""
import sys, asyncio
sys.path.insert(0, ".")
from moa_gateway.storage import get_storage


async def main():
    s = get_storage()
    keys = await s.list_api_keys()
    print(f"Existing keys: {len(keys)}")
    for k in keys[:5]:
        # 字段名可能不同,做安全打印
        kid = k.get("id") or k.get("kid") or "?"
        prefix = (k.get("key_prefix") or k.get("key") or "")[:20]
        name = k.get("name", "?")
        print(f"  id={kid}, prefix={prefix}, name={name}")

asyncio.run(main())