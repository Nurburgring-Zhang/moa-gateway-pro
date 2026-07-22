"""moa_gateway.benchmark — 内置 Benchmark Suite + Cost Pareto Analysis

实现 Together AI MoA 论文 §3 评估方法:
- BENCHMARK_PROMPTS: 多类别标准 prompt(reasoning / code / chinese / creative)
- run_benchmark: 在指定 preset 上跑一组 prompt,记录成本 + FLASK 评分
- run_pareto: 论文 §3.4 Figure 5 风格的多 preset cost-quality tradeoff
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# ========== 内置 Benchmark Prompts ==========
# 模拟 AlpacaEval 2.0 + MT-Bench + FLASK 风格,4 个类别
BENCHMARK_PROMPTS: list[dict[str, Any]] = [
    # --- reasoning (逻辑推理) ---
    {
        "id": "reasoning_001",
        "category": "reasoning",
        "difficulty": "easy",
        "text": "小明有 5 个苹果,妈妈又给他买了 3 个,他又送给小红 2 个。最后小明有几个苹果?请详细推理。",
        "reference": "5 + 3 - 2 = 6 个",
    },
    {
        "id": "reasoning_002",
        "category": "reasoning",
        "difficulty": "medium",
        "text": "所有 A 都是 B,所有 B 都是 C,那么所有 A 都是 C 吗?这是哪类推理?给出反例。",
        "reference": "是三段论演绎推理。前提正确则结论必然正确,无反例。",
    },
    {
        "id": "reasoning_003",
        "category": "reasoning",
        "difficulty": "hard",
        "text": "有 3 个开关在房间外,对应房间内 3 个灯泡(只进一次房间),如何确定哪个开关对应哪个灯泡?给出推理。",
        "reference": "开 1 号开关 5 分钟(灯泡热),关掉进房间摸热的是 1 号;亮的是 2 号;冷且暗的是 3 号。",
    },
    # --- code (代码) ---
    {
        "id": "code_001",
        "category": "code",
        "difficulty": "easy",
        "text": "用 Python 写一个函数,接受一个 list,返回去重后的 list(保持原顺序)。",
        "reference": "def unique(lst):\n    seen = set(); out = []\n    for x in lst:\n        if x not in seen: seen.add(x); out.append(x)\n    return out",
    },
    {
        "id": "code_002",
        "category": "code",
        "difficulty": "medium",
        "text": "写一个 SQL 查询:从 orders 表中找出每个用户最近 3 笔订单,显示 user_id, order_id, amount, order_date。",
        "reference": "SELECT user_id, order_id, amount, order_date FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY order_date DESC) rn FROM orders) t WHERE rn <= 3;",
    },
    {
        "id": "code_003",
        "category": "code",
        "difficulty": "hard",
        "text": "实现 LRU Cache(最近最少使用),要求 get/put 都是 O(1)。用 Python,使用 OrderedDict 或双向链表 + hashmap。",
        "reference": "class LRUCache:\n    def __init__(self, capacity):\n        self.cap = capacity; self.cache = OrderedDict()\n    def get(self, key):\n        if key not in self.cache: return -1\n        self.cache.move_to_end(key); return self.cache[key]\n    def put(self, key, val):\n        if key in self.cache: self.cache.move_to_end(key)\n        self.cache[key] = val\n        if len(self.cache) > self.cap: self.cache.popitem(last=False)",
    },
    # --- chinese (中文) ---
    {
        "id": "chinese_001",
        "category": "chinese",
        "difficulty": "easy",
        "text": "请用一段话介绍李白和他的《静夜思》,100 字左右。",
        "reference": "李白,字太白,号青莲居士,唐代浪漫主义诗人。其《静夜思》以简洁语言写游子思乡之情:'床前明月光,疑是地上霜。举头望明月,低头思故乡。' 20 字道尽千古乡愁。",
    },
    {
        "id": "chinese_002",
        "category": "chinese",
        "difficulty": "medium",
        "text": "请将下列中文翻译成英文:实现中华民族伟大复兴的中国梦,是全体中华儿女的共同心愿,也是新时代赋予的历史使命。",
        "reference": "Realizing the Chinese Dream of the great rejuvenation of the Chinese nation is the common aspiration of all Chinese children, and also the historical mission entrusted to the new era.",
    },
    # --- creative (创作) ---
    {
        "id": "creative_001",
        "category": "creative",
        "difficulty": "medium",
        "text": "为一款面向程序员的 AI 协作工具取 3 个名字,每个名字解释含义。",
        "reference": "示例:DevPilot(开发者副驾驶)、CodeSymbiont(代码共生体)、PromptForge(锻造 prompt 的熔炉)",
    },
    {
        "id": "creative_002",
        "category": "creative",
        "difficulty": "easy",
        "text": "写一首关于 2026 春天的五言绝句。",
        "reference": "示例:东风拂柳绿,新雨润桃红。江畔人初醒,春光已满瞳。",
    },
    # --- professional (专业领域) ---
    {
        "id": "prof_001",
        "category": "professional",
        "difficulty": "medium",
        "text": "请简述 OAuth 2.0 的四种授权流程(Authorization Code / Implicit / Client Credentials / Password),并给出适用场景。",
        "reference": "1. Authorization Code - Web 服务,有后端;2. Implicit - 纯前端 SPA(已不推荐);3. Client Credentials - 服务器到服务器;4. Password - 高度信任的应用(已不推荐)",
    },
]


# ========== 跑单个 preset 的 benchmark ==========
async def run_benchmark(
    preset_name: str, prompts: list[dict[str, Any]], use_flask: bool = True
) -> dict[str, Any]:
    """对一组 prompt 跑指定 preset,记录每题的结果 + FLASK 评分"""
    from .moa import get_moa

    moa = get_moa()
    items = []
    total_cost = 0.0
    for p in prompts:
        start = time.time()
        item: dict[str, Any] = {
            "prompt_id": p["id"],
            "category": p["category"],
            "difficulty": p.get("difficulty", ""),
            "prompt": p["text"][:200],
            "preset": preset_name,
            "success": False,
            "latency_ms": 0,
            "cost": 0.0,
            "answer_preview": "",
            "flask_avg": None,
        }
        try:
            res = await moa.execute(query=p["text"], preset=preset_name)
            item["latency_ms"] = round((time.time() - start) * 1000, 1)
            item["cost"] = round(res.total_cost, 6)
            item["strategy"] = res.strategy
            item["aggregator_model"] = res.aggregator_model
            item["answer_preview"] = (res.final_content or "")[:400]
            item["success"] = bool(res.final_content)
            total_cost += res.total_cost

            # FLASK 评分(可选,会额外消耗)
            if use_flask and res.final_content:
                try:
                    flask = await moa.flask_score(
                        p["text"], res.final_content, reference=p.get("reference")
                    )
                    item["flask_scores"] = flask.get("scores", {})
                    item["flask_avg"] = flask.get("average_0_100")
                except Exception as e:
                    item["flask_error"] = str(e)
        except Exception as e:
            item["error"] = str(e)
            item["latency_ms"] = round((time.time() - start) * 1000, 1)
        items.append(item)

    return {
        "items": items,
        "total_cost": round(total_cost, 6),
    }


# ========== Cost Pareto Analysis(论文 3.4 Figure 5) ==========
async def run_pareto(prompts: list[str], presets: list[str]) -> dict[str, Any]:
    """对一组 prompt 跑多个 preset,输出 cost vs quality 的 Pareto 前沿。
    每个 preset 的得分 = FLASK average(0-100),cost = 平均 cost。
    Pareto frontier = 在 cost 上没有任何其他 preset 既更便宜又更高分。
    """
    # 转成 benchmark 格式(无 reference)
    bench_prompts = [
        {"id": f"p{i}", "category": "custom", "text": p} for i, p in enumerate(prompts)
    ]
    points = []
    for preset_name in presets:
        try:
            r = await run_benchmark(preset_name, bench_prompts, use_flask=True)
            avg_score = sum(i.get("flask_avg", 0) or 0 for i in r["items"]) / max(
                1, sum(1 for i in r["items"] if i.get("flask_avg"))
            )
            avg_cost = sum(i["cost"] for i in r["items"]) / max(1, len(r["items"]))
            avg_latency = sum(i["latency_ms"] for i in r["items"]) / max(1, len(r["items"]))
            points.append(
                {
                    "preset": preset_name,
                    "avg_score": round(avg_score, 2),
                    "avg_cost": round(avg_cost, 6),
                    "avg_latency_ms": round(avg_latency, 1),
                    "total_cost": r["total_cost"],
                    "questions_tested": len(r["items"]),
                }
            )
        except Exception as e:
            points.append(
                {
                    "preset": preset_name,
                    "error": str(e),
                    "avg_score": 0,
                    "avg_cost": float("inf"),
                    "avg_latency_ms": 0,
                }
            )

    # 计算 Pareto frontier:
    # 点 p 在前沿上,当不存在另一个点 q: q.cost <= p.cost AND q.score >= p.score
    # 但严格 Pareto 要求至少一个严格优于 p
    frontier = []
    sorted_by_cost = sorted([pt for pt in points if "error" not in pt], key=lambda x: x["avg_cost"])
    if sorted_by_cost:
        # 找随 cost 上升,score 不下降的序列
        best_score = -1
        for pt in sorted_by_cost:
            if pt["avg_score"] > best_score:
                frontier.append(pt["preset"])
                best_score = pt["avg_score"]

    # 推荐:在 score >= 0.8 * max_score 的前提下 cost 最小的
    if points:
        max_score = max((pt["avg_score"] for pt in points if "error" not in pt), default=0)
        threshold = max_score * 0.8
        candidates = [pt for pt in points if "error" not in pt and pt["avg_score"] >= threshold]
        if candidates:
            recommended = min(candidates, key=lambda x: x["avg_cost"])
        else:
            recommended = None
    else:
        recommended = None

    return {
        "prompts_count": len(prompts),
        "presets_tested": presets,
        "pareto_points": points,
        "pareto_frontier": frontier,
        "recommended": recommended["preset"] if recommended else None,
        "note": (
            "Pareto frontier: 在此序列上,沿 cost 升序排列,score 单调不减。"
            " 推荐 preset: 达到最高分 80% 时 cost 最小的。"
        ),
    }
