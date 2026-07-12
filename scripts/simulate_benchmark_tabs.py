"""模拟运行 WebUI 4 个 Benchmark tab,验证后端端点全部真实工作"""
import sys
sys.path.insert(0, ".")
import json
from fastapi.testclient import TestClient
from moa_gateway import server as srv

API_KEY = "mgw-UmORPDhe0FNEM4vAxuTwvWwdWpI5H76W"
H = {"Authorization": f"Bearer {API_KEY}"}
client = TestClient(srv.app)

print("=" * 70)
print("  模拟运行 WebUI Benchmark 4 tab")
print("=" * 70)

# ---------- 0. presets (Tab 3 下拉框要的数据) ----------
print("\n[Setup] GET /v1/moa/presets — Tab 3 (Layered) 下拉框")
r = client.get("/v1/moa/presets", headers=H)
data = r.json()
presets = data["presets"]
print(f"  status={r.status_code}, presets count={len(presets)}")
layered = [p for p in presets if p["strategy"] == "layered"]
print(f"  layered presets: {[p['name'] for p in layered]}")
for p in layered:
    print(f"    {p['name']} keys: {list(p.keys())}")
    print(f"    {p['name']}: layers={p.get('layer_count', 'MISSING')}, refs={len(p.get('reference_models') or [])}, agg={p.get('aggregator')}")

# ---------- Tab 1: Benchmark Suite ----------
print("\n" + "=" * 70)
print("[Tab 1] Benchmark Suite — POST /v1/moa/benchmark")
print("=" * 70)
r = client.post("/v1/moa/benchmark", headers=H, json={
    "presets": ["balanced", "chinese_battalion", "judge"],
    "category": "reasoning",
    "limit": 2,
})
print(f"  status={r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  categories: {data['categories']}")
    print(f"  prompts: {len(data['prompts'])} 题")
    for p in data["prompts"]:
        print(f"    [{p['id']}] {p['category']}: {p['text'][:60]}...")
    print(f"  results per preset:")
    for preset, items in data["results"].items():
        print(f"    {preset}: (items type={type(items).__name__}, len={len(items) if hasattr(items, '__len__') else 'N/A'})")
        for it in items:
            print(f"      item type={type(it).__name__}, value={str(it)[:100]}")
            if isinstance(it, dict):
                print(f"      {it.get('prompt_id', 'N/A'):18s} success={it.get('success')} "
                      f"score={it.get('flask_avg', 'N/A')} "
                      f"cost=${it.get('cost', 0):.4f} latency={it.get('latency_ms', 0)}ms")
            else:
                print(f"      (skip non-dict item)")
    print(f"  summary:")
    for preset, s in data["summary"].items():
        print(f"    {preset}: avg_score={s.get('avg_flask_score', 'N/A'):.1f} "
              f"success_rate={s.get('success_rate', 0):.0%} "
              f"total_cost=${s.get('total_cost', 0):.4f}")
else:
    print(f"  ERROR: {r.text[:300]}")

# ---------- Tab 2: Cost Pareto ----------
print("\n" + "=" * 70)
print("[Tab 2] Cost Pareto — POST /v1/moa/cost-pareto")
print("=" * 70)
r = client.post("/v1/moa/cost-pareto", headers=H, json={
    "prompts": [
        "什么是 Transformer?",
        "用 Python 写二分查找",
        "请用一句话介绍李白",
    ],
    "presets": ["fast", "balanced", "chinese_battalion", "judge"],
})
print(f"  status={r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  pareto_points:")
    for pt in data["pareto_points"]:
        if "error" in pt:
            print(f"    {pt['preset']:24s} ERROR: {pt['error'][:60]}")
        else:
            print(f"    {pt['preset']:24s} score={pt['avg_score']:6.1f} "
                  f"cost=${pt['avg_cost']:.6f} latency={pt['avg_latency_ms']:.0f}ms")
    print(f"  pareto_frontier: {data['pareto_frontier']}")
    print(f"  recommended: {data['recommended']}")
else:
    print(f"  ERROR: {r.text[:300]}")

# ---------- Tab 3: Layered Flow ----------
print("\n" + "=" * 70)
print("[Tab 3] Layered Flow — POST /v1/moa/execute (preset=chinese_battalion_layered)")
print("=" * 70)
# 3a. 拿 preset 详情
r = client.get("/v1/moa/presets", headers=H)
data = r.json()
preset = next((p for p in data["presets"] if p["name"] == "chinese_battalion_layered"), None)
if preset:
    print(f"  preset config:")
    print(f"    strategy: {preset.get('strategy')}, layers: {preset.get('layer_count', 'N/A')}")
    refs = preset.get('reference_models') or []
    print(f"    refs: {[r.get('id') for r in refs]}")
    print(f"    aggregator: {preset.get('aggregator')}")
# 3b. 实际跑
r = client.post("/v1/moa/execute", headers=H, json={
    "model": "moa",
    "messages": [{"role": "user", "content": "用一句话介绍 Transformer"}],
    "preset": "chinese_battalion_layered",
})
print(f"  execute status={r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  strategy={data.get('strategy')}, layers_count={data.get('layers_count')}")
    print(f"  references: {len(data.get('references', []))}")
    print(f"  aggregator_model={data.get('aggregator_model')}")
    print(f"  total_cost=${data.get('total_cost', 0):.4f}, latency={data.get('total_latency_ms', 0):.0f}ms")
    if data.get('layer_outputs'):
        for layer_name, outputs in data['layer_outputs'].items():
            print(f"  {layer_name}: {len(outputs)} 个模型:")
            for o in outputs:
                print(f"    - {o['model']:20s} ${o.get('cost', 0):.4f} :: {o.get('content', '')[:60]}...")
    print(f"  final_content: {(data.get('final_content') or '')[:120]}...")
else:
    print(f"  ERROR: {r.text[:300]}")

# ---------- Tab 4: FLASK 评分 ----------
print("\n" + "=" * 70)
print("[Tab 4] FLASK 评分 — POST /v1/moa/flask")
print("=" * 70)
r = client.post("/v1/moa/flask", headers=H, json={
    "query": "什么是 Transformer?",
    "response": "Transformer 是一种基于自注意力机制的神经网络架构,由 Vaswani 等人在 2017 年提出。它抛弃了 RNN/CNN,完全依赖 self-attention,实现并行训练,广泛应用于 NLP 和 CV 任务。",
})
print(f"  status={r.status_code}")
if r.status_code == 200:
    data = r.json()
    print(f"  judge_model={data.get('judge_model')}, cost=${data.get('judge_cost', 0):.4f}")
    scores = data.get("scores", {})
    if scores:
        print(f"  scored dimensions: {len(scores)}")
        for dim, v in list(scores.items())[:6]:
            s = v.get("score_1_5") if isinstance(v, dict) else v
            r_str = v.get("reason", "")[:50] if isinstance(v, dict) else ""
            print(f"    {dim:15s} -> {s}/5  ({r_str})")
        print(f"  average_1_5: {data.get('average_1_5')}")
        print(f"  average_0_100: {data.get('average_0_100')}")
    else:
        print(f"  no scores parsed; raw: {(data.get('raw_response') or '')[:200]}")
else:
    print(f"  ERROR: {r.text[:300]}")

print("\n" + "=" * 70)
print("  4 tab 后端端点全部模拟运行完毕")
print("=" * 70)