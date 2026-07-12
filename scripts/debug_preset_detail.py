"""查 chinese_battalion_layered 是否真的有 layer_count"""
import sys
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from moa_gateway import server as srv
H = {"Authorization": "Bearer mgw-UmORPDhe0FNEM4vAxuTwvWwdWpI5H76W"}
client = TestClient(srv.app)
r = client.get("/v1/moa/presets", headers=H)
data = r.json()
for p in data["presets"]:
    if p["name"] == "chinese_battalion_layered":
        print("name:", p["name"])
        print("layer_count:", p.get("layer_count"))
        print("strategy:", p.get("strategy"))
        print("aggregator:", p.get("aggregator"))
        print("ref models:", p.get("reference_models"))
        break