"""Check rate limit state for test keys."""
import sys
sys.path.insert(0, '.')
from moa_gateway.storage import get_storage

s = get_storage()
keys = s.list_api_keys()
for k in keys:
    if 'test_full' in k.get('name', '') or 'rate_test' in k.get('name', ''):
        print(f'name={k["name"]} key_id={k["key_id"]} quota_rpm={k.get("quota_rpm")} enabled={k.get("enabled")}')
        with s.conn() as c:
            rows = c.execute(
                "SELECT bucket, count FROM ratelimit_buckets WHERE api_key_id=?",
                (k["key_id"],)
            ).fetchall()
            for row in rows:
                print(f'  bucket={row["bucket"]} count={row["count"]}')
