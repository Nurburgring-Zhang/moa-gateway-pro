from moa_gateway.storage import get_storage
s = get_storage()
new_key = s.create_api_key(name="verify_prompts_test", quota_rpm=1000, quota_daily_tokens=999999999)
print("Created:", new_key)
# 打印完整 dict
for k, v in new_key.items():
    if "key" in k.lower():
        print(f"  {k} = {str(v)}")
    else:
        print(f"  {k} = {v}")