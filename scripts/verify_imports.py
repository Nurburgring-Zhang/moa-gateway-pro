"""验证 imports + 新 preset"""
import sys
sys.path.insert(0, ".")
from moa_gateway.moa import MoAOrchestrator
from moa_gateway.benchmark import BENCHMARK_PROMPTS, run_benchmark, run_pareto
from moa_gateway.config import get_settings

print("imports OK")
print(f"BENCHMARK_PROMPTS: {len(BENCHMARK_PROMPTS)}")
print(f"categories: {sorted(set(p['category'] for p in BENCHMARK_PROMPTS))}")
s = get_settings()
print(f"presets: {list(s.moa.presets.keys())}")
print(f"new strategy literals: layered={s.moa.presets['chinese_battalion_layered'].strategy}, single_proposer={s.moa.presets['qwen_single_proposer'].strategy}, ranker={s.moa.presets['ranker_qwen110b'].strategy}")
print(f"chinese_battalion_layered.layer_count = {s.moa.presets['chinese_battalion_layered'].layer_count}")