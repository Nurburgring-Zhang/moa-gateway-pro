"""验证 server.py 的 prompts 端点(用 API key)"""
import sys, os
sys.path.insert(0, ".")
os.environ["PYTHONPATH"] = "."

from fastapi.testclient import TestClient
from moa_gateway import server as srv

API_KEY = "mgw-UmORPDhe0FNEM4vAxuTwvWwdWpI5H76W"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

client = TestClient(srv.app)

print("=" * 70)
print("  验证 server.py 的 /v1/moa/prompts 端点")
print("=" * 70)

# 1. 无 auth
print("\n[1] 无 auth GET /v1/moa/prompts (期望 401)")
r = client.get("/v1/moa/prompts")
print(f"  status: {r.status_code}")
assert r.status_code in (401, 403)
print("  PASS")

# 2. GET 列表
print("\n[2] GET /v1/moa/prompts")
r = client.get("/v1/moa/prompts", headers=HEADERS)
print(f"  status: {r.status_code}")
data = r.json()
print(f"  total templates: {data.get('total')}")
print(f"  placeholders: {list(data.get('placeholders', {}).keys())}")
for t in data.get("templates", [])[:5]:
    print(f"    - {t['name']:30s} src={t['source']:8s} size={t['size']:5d}B")
assert r.status_code == 200
assert data["total"] >= 12
print("  PASS")

# 3. GET aggregator
print("\n[3] GET /v1/moa/prompts/aggregator")
r = client.get("/v1/moa/prompts/aggregator", headers=HEADERS)
data = r.json()
print(f"  status: {r.status_code}, length: {len(data['content'])}")
print(f"  preview: {data['content'][:60]}...")
assert r.status_code == 200
assert "聚合器" in data["content"] or "聚合" in data["content"]
print("  PASS")

# 4. PUT 创建
print("\n[4] PUT /v1/moa/prompts/test_user_template")
test_content = """# User-defined test prompt
用户测试 prompt: 这个内容必须被真实存储和读取"""
r = client.put("/v1/moa/prompts/test_user_template",
               headers=HEADERS,
               json={"content": test_content})
print(f"  status: {r.status_code}")
data = r.json()
print(f"  saved: {data.get('saved')}, path: {data.get('path')}")
assert r.status_code == 200
assert data["saved"]
print("  PASS")

# 5. GET 验证保存
print("\n[5] GET /v1/moa/prompts/test_user_template (验证保存)")
r = client.get("/v1/moa/prompts/test_user_template", headers=HEADERS)
data = r.json()
print(f"  status: {r.status_code}")
assert data["content"].strip() == test_content.strip()
print(f"  内容匹配 ✓ (length={len(data['content'])})")
assert r.status_code == 200
print("  PASS")

# 6. DELETE
print("\n[6] DELETE /v1/moa/prompts/test_user_template")
r = client.delete("/v1/moa/prompts/test_user_template", headers=HEADERS)
print(f"  status: {r.status_code}")
assert r.status_code == 200
print("  PASS")

# 7. 列表现在还显示该 template 吗? (应该不显示 user-override,但 default 还在)
print("\n[7] GET /v1/moa/prompts (确认 test_user_template 消失)")
r = client.get("/v1/moa/prompts", headers=HEADERS)
data = r.json()
names = [t["name"] for t in data["templates"]]
assert "test_user_template" not in names
print(f"  test_user_template 不在列表 ✓ (total now: {data['total']})")
print("  PASS")

# 8. invalid name (含特殊字符)
print("\n[8] GET /v1/moa/prompts/foo.bar (含 dot,期望 400)")
r = client.get("/v1/moa/prompts/foo.bar", headers=HEADERS)
print(f"  status: {r.status_code}")
assert r.status_code == 400
print("  PASS")

# 8b. 含特殊字符 (空格,被 URL encode)
print("\n[8b] GET /v1/moa/prompts/foo%20bar (含空格,期望 400)")
r = client.get("/v1/moa/prompts/foo%20bar", headers=HEADERS)
print(f"  status: {r.status_code}")
assert r.status_code == 400
print("  PASS")

# 9. PUT 空内容
print("\n[9] PUT /v1/moa/prompts/empty (空内容,期望 400)")
r = client.put("/v1/moa/prompts/empty", headers=HEADERS, json={"content": ""})
print(f"  status: {r.status_code}")
assert r.status_code == 400
print("  PASS")

# 10. 列表里 source=user 的能区分
print("\n[10] 创建一个用户模板并验证 source=user")
client.put("/v1/moa/prompts/my_aggregator",
           headers=HEADERS,
           json={"content": "我的自定义聚合器"})
r = client.get("/v1/moa/prompts", headers=HEADERS)
data = r.json()
my_entry = next((t for t in data["templates"] if t["name"] == "my_aggregator"), None)
if my_entry:
    print(f"  source={my_entry['source']}, read_only={my_entry.get('read_only')}")
    assert my_entry["source"] == "user"
    assert my_entry["read_only"] is False
    print("  PASS")
else:
    print(f"  FAIL: my_aggregator not in list. names={[t['name'] for t in data['templates']]}")
    sys.exit(1)
# 清理
client.delete("/v1/moa/prompts/my_aggregator", headers=HEADERS)

print("\n" + "=" * 70)
print("  全部 10 项验证通过")
print("=" * 70)