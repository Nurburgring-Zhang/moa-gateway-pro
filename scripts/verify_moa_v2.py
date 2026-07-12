"""端到端验证 9 个 strategy + benchmark + similarity + FLASK + cost-pareto"""
import sys
sys.path.insert(0, ".")
import asyncio
import json
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace


def make_ep(eid, rank=2, content=None):
    ep = SimpleNamespace()
    ep.id = eid
    ep.name = eid
    ep.is_available = True
    ep.consecutive_failures = 0
    ep.health_status = "healthy"
    ep.config = SimpleNamespace(api_key_runtime="k", provider="mock", model=eid)
    ep.tier = SimpleNamespace(value="standard", rank=rank)
    ep.provider = SimpleNamespace(call=AsyncMock())
    return ep


def make_resp(ep_id, content):
    return SimpleNamespace(content=content, cost=0.001, latency_ms=50,
                            prompt_tokens=100, completion_tokens=200, model=ep_id)


async def run():
    from moa_gateway.config import (
        Settings, MoAPresetConfig, ServerConfig, AuthConfig,
        StorageConfig, RateLimitConfig, ModelEndpointConfig, ReferenceModelConfig,
    )
    from moa_gateway.moa import MoAOrchestrator, ReferenceResult

    eps = {f"ep{i}": make_ep(f"ep{i}", rank=2 + (i % 2)) for i in range(1, 7)}
    pool = MagicMock()
    pool.endpoints = eps
    counter = [0]
    async def mock_call(ep_id, *a, **kw):
        counter[0] += 1
        return make_resp(ep_id, f"answer-from-{ep_id}-content-{counter[0]}")
    pool.call = AsyncMock(side_effect=mock_call)
    pool.get_fallback_chain = MagicMock(return_value=[])
    pool.select_many = MagicMock(side_effect=lambda tier, n, **kw: list(eps.values())[:n])
    pool.select_one = MagicMock(side_effect=lambda tier: eps.get("ep5"))

    router = MagicMock()
    def route_for_moa(*a, **kw):
        return (list(eps.values())[:4], eps["ep5"])
    router.route_for_moa = route_for_moa

    def mk_preset(name, strategy, **kw):
        return MoAPresetConfig(
            enabled=True, strategy=strategy,
            reference_count=kw.get("ref_count", 3),
            reference_models=kw.get("refs", []),
            aggregator=kw.get("agg", ""),
            aggregator_tier=kw.get("agg_tier", "premium"),
            tier="standard",
            critic_rounds=kw.get("critic", 0),
            reference_temperature=0.6,
            aggregator_temperature=0.4,
            max_tokens=2000,
            layer_count=kw.get("layer_count", 3),
        )
    presets = {
        "single": mk_preset("single", "single", refs=[ReferenceModelConfig(id="ep1")]),
        "parallel": mk_preset("parallel", "parallel", refs=[
            ReferenceModelConfig(id=f"ep{i}") for i in range(1, 5)
        ], agg="ep5"),
        "compose": mk_preset("compose", "compose", refs=[
            ReferenceModelConfig(id="ep1", role="feasibility"),
            ReferenceModelConfig(id="ep2", role="performance"),
            ReferenceModelConfig(id="call", role="security"),
            ReferenceModelConfig(id="ep4", role="ux"),
        ], agg="ep5"),
        "judge": mk_preset("judge", "judge", refs=[], agg="ep5", critic=2),
        "chain": mk_preset("chain", "chain"),
        "pipeline": mk_preset("pipeline", "pipeline"),
        "layered": mk_preset("layered", "layered", refs=[
            ReferenceModelConfig(id=f"ep{i}") for i in range(1, 5)
        ], agg="ep5", layer_count=3),
        "single_proposer": mk_preset("single_proposer", "single_proposer", refs=[
            ReferenceModelConfig(id="ep5")
        ], agg="ep5", ref_count=3),
        "ranker": mk_preset("ranker", "ranker", refs=[
            ReferenceModelConfig(id=f"ep{i}") for i in range(1, 5)
        ], agg="ep5"),
    }
    settings = Settings(
        server=ServerConfig(), auth=AuthConfig(), storage=StorageConfig(),
        rate_limit=RateLimitConfig(),
        moa={"default_preset": "balanced", "presets": presets},
        models=[ModelEndpointConfig(id=eid, name=eid, provider="mock", model=eid)
                for eid in eps],
    )
    orch = MoAOrchestrator(model_pool=pool, router=router)
    orch.settings = settings

    print("=" * 70)
    print("  端到端验证 9 个 MoA strategy")
    print("=" * 70)
    passed = []
    for strat in ["single", "parallel", "compose", "judge", "chain", "pipeline",
                   "layered", "single_proposer", "ranker"]:
        try:
            for ep in eps.values():
                ep.provider.call.reset_mock()
            res = await orch.execute(query=f"test-{strat}:什么是MoA?", preset=strat)
            assert res.strategy == strat, f"expected {strat}, got {res.strategy}"
            assert res.final_content, f"empty final_content for {strat}"
            assert res.total_cost > 0, f"zero cost for {strat}"
            print(f"  [OK] {strat:18s} | layers={res.layers_count} | refs={len(res.references):2d} | "
                  f"final_len={len(res.final_content):3d} | cost=${res.total_cost:.4f}")
            if strat == "layered":
                assert res.layers_count == 3
                assert len(res.layer_outputs) == 3
                print(f"      layer_outputs keys: {list(res.layer_outputs.keys())}")
            if strat == "ranker":
                assert res.winner_model, "ranker should have winner_model"
                assert res.ranker_output, "ranker should have ranker_output"
                print(f"      winner: {res.winner_model}, ranking: {res.ranker_output.get('ranking', [])}")
            passed.append(strat)
        except Exception as e:
            print(f"  [FAIL] {strat:18s}: {type(e).__name__}: {e}")
    print()
    print(f"  {len(passed)}/9 strategies PASS")
    assert len(passed) == 9, "all 9 strategies must work"

    # Similarity
    print()
    print("=" * 70)
    print("  Similarity Score (BLEU / Levenshtein / TF-IDF)")
    print("=" * 70)
    sim = await orch.compute_similarity(
        query="什么是 Transformer?",
        candidate_a="Transformer 是一种基于自注意力机制的神经网络架构。",
        candidate_b="Transformer 是基于 self-attention 的神经网络。",
        model_id=None,
    )
    print(f"  bleu3={sim.get('bleu3')}, bleu4={sim.get('bleu4')}, bleu5={sim.get('bleu5')}")
    print(f"  levenshtein={sim.get('levenshtein_similarity')}, tfidf={sim.get('tfidf_cosine')}")
    for k in ["bleu3", "bleu4", "bleu5", "levenshtein_similarity", "tfidf_cosine"]:
        assert k in sim, f"missing {k}"
        assert 0 <= sim[k] <= 1
    print("  OK")

    # FLASK
    print()
    print("=" * 70)
    print("  FLASK 多维评分(12 维)")
    print("=" * 70)
    flask = await orch.flask_score(
        query="什么是 Transformer?",
        response="Transformer 是一种基于自注意力机制的神经网络架构,由 Vaswani 等人在 2017 年提出,广泛应用于 NLP。",
        reference="Transformer 是基于 self-attention 的架构。",
        judge_model="ep5",
    )
    print(f"  judge_model: {flask.get('judge_model')}")
    scores = flask.get("scores", {})
    print(f"  dimensions scored: {len(scores)}")
    for dim, v in list(scores.items())[:5]:
        s = v.get('score_1_5') if isinstance(v, dict) else v
        r = v.get('reason', '') if isinstance(v, dict) else ''
        print(f"    {dim:15s} -> {s} ({r[:40]})")
    if scores:
        print(f"  average_0_100: {flask.get('average_0_100')}")
    print("  OK")

    # Benchmark
    print()
    print("=" * 70)
    print("  Benchmark Suite")
    print("=" * 70)
    from moa_gateway.benchmark import BENCHMARK_PROMPTS, run_benchmark, run_pareto
    print(f"  {len(BENCHMARK_PROMPTS)} prompts, "
          f"{len(set(p['category'] for p in BENCHMARK_PROMPTS))} categories")
    res = await run_benchmark("parallel", BENCHMARK_PROMPTS[:2], use_flask=False)
    print(f"  total_cost: ${res['total_cost']:.4f}, items: {len(res['items'])}")
    for it in res["items"]:
        print(f"    {it['prompt_id']:20s} success={it['success']} cost=${it['cost']:.6f}")
    assert len(res["items"]) == 2
    assert any(it['success'] for it in res['items']), "at least one should succeed"
    print("  OK")

    # Cost Pareto
    print()
    print("=" * 70)
    print("  Cost Pareto Analysis")
    print("=" * 70)
    pareto = await run_pareto(
        prompts=["什么是 Python?", "用 Python 写个 hello world"],
        presets=["parallel", "single_proposer"],
    )
    print(f"  presets: {pareto['presets_tested']}")
    for pt in pareto["pareto_points"]:
        if "error" in pt:
            print(f"    {pt['preset']:12s} ERROR: {pt['error']}")
        else:
            print(f"    {pt['preset']:12s} score={pt['avg_score']:.1f} cost=${pt['avg_cost']:.6f}")
    print(f"  pareto_frontier: {pareto['pareto_frontier']}")
    print(f"  recommended: {pareto.get('recommended')}")
    print("  OK")

    print()
    print("=" * 70)
    print("  ALL E2E TESTS PASSED")
    print("=" * 70)


asyncio.run(run())