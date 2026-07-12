import re
from pathlib import Path

fp = 'D:/MoA Gateway Pro/moa_gateway/auth.py'
text = Path(fp).read_text(encoding='utf-8')

# 在 authenticate_api_key 函数里,加 admin JWT 优先支持
# 找注释 "注:不再支持 ?api_key=" 后到 "    storage = get_storage()" 之前插入 JWT 块
old = '    # 先查 storage 里的 API Key(优先)\n    storage = get_storage()'
new = '    # 修24: 先试 admin JWT(WebUI 登录后拿的)\n    if token.count(".") == 2:  # JWT 格式:header.payload.signature\n        info = decode_jwt_token(token)\n        if info and info.get("role") == "admin":\n            return {"source": "admin_jwt", "name": info.get("sub", "admin"),\n                    "role": "admin",\n                    "quota_rpm": 999_999, "quota_daily_tokens": 999_999_999}\n\n    # 先查 storage 里的 API Key(优先)\n    storage = get_storage()'

assert old in text, "old not found"
text = text.replace(old, new, 1)
Path(fp).write_text(text, encoding='utf-8')
print("OK - auth.py updated")
