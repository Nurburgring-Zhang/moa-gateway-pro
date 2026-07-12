"""查 presets 端点真实返回"""
import sys, json
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from moa_gateway import server as srv
H = {"Authorization": "Bearer mgw-UmORPDhe0FNEM4vAxuTwvWwdWpI5H76W"}
client = TestClient(srv.app)
r = client.get("/v1/moa/presets", headers=H)
data = r.json()
for p in data["presets"]:
    print(p["name"], "keys:", list(p.keys()))