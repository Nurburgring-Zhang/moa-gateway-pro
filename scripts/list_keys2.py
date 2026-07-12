from moa_gateway.storage import get_storage
s = get_storage()
keys = s.list_api_keys()
print("Total keys:", len(keys))
for k in keys[:5]:
    print(k)
print()
print("Creating test API key...")
new_key = s.create_api_key(name="verify_prompts_test", scope="admin")
print("Created:", new_key)
if isinstance(new_key, dict):
    for k, v in new_key.items():
        if "key" in k.lower():
            print(f"  {k} = {str(v)[:30]}...")
        else:
            print(f"  {k} = {v}")