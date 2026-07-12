"""查 benchmark 端点真实返回"""
import sys, json
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from moa_gateway import server as srv
H = {"Authorization": "Bearer mgw-UmORPDhe0FNEM4vAxuTwvWwdWpI5H76W"}
client = TestClient(srv.app)
r = client.post("/v1/moa/benchmark", headers=H, json={
    "presets": ["balanced"],
    "category": "reasoning",
    "limit": 2,
})
data = r.json()
print("presets:", data.get("presets"))
print("tested_prompts:", data.get("tested_prompts"))
print("results['balanced']:", data["results"]["balanced"])
print("summary:", data["summary"])