"""O2 — TaskAnalyzer: 任务 -> 结构化任务画像(能力需求)。

真实复用既有 capability 做语义/复杂度/门控分析, 而非杜撰:
  - prompt_features.extract_features : 文本特征(代码块/疑问/数学/长度...)
  - gate_l0.gate                     : 是否简单到直答 vs 需要 MoA 编排 + 复杂度
  - plan_act.plan_and_act            : plan/act 模式判定

输出 TaskProfile(dict):
  text / features / complexity / needs_moa / mode / signals /
  capability_hints(按关键词推断的能力类型与关键词)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# 关键词 -> 能力类型提示 (供 planner 做匹配的起点)
# 对抗复审 Fix: 把高假阳性裸动词(plan/react/reason/embed)改为更具体短语, 降低日常用语误触发。
# code/data/compute/analyze/search 保留(技术语境下更可能是有意调用)。
_KEYWORD_CAPABILITY_HINTS: list[tuple[list[str], str, list[str]]] = [
    (["search", "web", "internet", "lookup", "搜索", "查询", "browse"], "skill", ["web_search"]),
    (["code", "python", "compute", "calculate", "run script", "代码", "计算", "执行"], "skill", ["code_execute"]),
    (["analyze", "data", "stats", "trend", "anomaly", "分析", "数据", "统计"], "skill", ["analyze_data"]),
    (["read file", "load file", "读文件", "读取"], "skill", ["file_read"]),
    (["write file", "save file", "写文件", "保存"], "skill", ["file_write"]),
    (["verify api", "test endpoint", "接口验证", "api 测试"], "skill", ["api_verify"]),
    (["make a plan", "plan and execute", "plan the steps", "plan_execute", "decompose", "multi-step", "step by step", "规划", "拆解", "分步"], "loop", ["plan_execute"]),
    (["react loop", "react agent", "think and act", "推理并行动"], "loop", ["react"]),
    (["workflow", "pipeline", "dag", "工作流", "流程"], "graph", []),
    (["ensemble", "multi-model", "mixture", "多模型", "集成", "MoA", "moa"], "moa", []),
    (["mcp", "tool server", "工具服务"], "mcp", []),
    (["cli", "command", "shell", "命令", "终端"], "cli", []),
    (["embedding", "vectorize", "vector", "向量", "嵌入"], "api", ["embedding"]),
    (["image", "图片", "图像", "画"], "api", ["image"]),
    (["audio", "speech", "tts", "语音", "音频"], "api", ["audio"]),
    (["video", "视频"], "api", ["video"]),
    (["3d", "model generation", "三维"], "api", ["3d"]),
]


class TaskAnalyzer:
    """把任务文本解析为结构化任务画像。"""

    def analyze(self, task: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        task = (task or "").strip()
        ctx = context or {}
        profile: dict[str, Any] = {
            "text": task,
            "features": {},
            "complexity": 5,
            "needs_moa": False,
            "gate_category": "",
            "mode": "act",
            "signals": [],
            "capability_hints": [],
            "mock_llm_note": None,
        }
        if not task:
            profile["error"] = "empty task"
            return profile

        # 1) 文本特征 (真实 capability)
        try:
            from ..capability.prompt_features import extract_features

            feats = extract_features(task)
            profile["features"] = {
                k: getattr(feats, k)
                for k in (
                    "length",
                    "word_count",
                    "has_code_block",
                    "has_question_mark",
                    "has_math_symbols",
                    "imperative_count",
                )
                if hasattr(feats, k)
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("prompt_features failed: %s", e)

        # 2) 门控: 是否需 MoA + 复杂度 (真实 capability)
        try:
            from ..capability.gate_l0 import gate

            verdict = gate(task, ctx.get("history"))
            profile["needs_moa"] = not bool(verdict.passed)
            profile["gate_category"] = verdict.category
            profile["complexity"] = int(getattr(verdict, "estimated_complexity", 5) or 5)
        except Exception as e:  # noqa: BLE001
            logger.warning("gate_l0 failed: %s", e)

        # 3) plan/act 模式 (真实 capability)
        try:
            from ..capability.plan_act import classify_mode

            pa = classify_mode(task)
            profile["mode"] = pa.get("mode", "act")
            profile["signals"] = pa.get("signals", [])
        except Exception as e:  # noqa: BLE001
            logger.warning("plan_act failed: %s", e)

        # 4) 关键词 -> 能力提示 (确定性启发式; LLM 增强由 planner 负责, 无 key 时为 mock 标注)
        # 对抗复审 Fix: 单词关键词用词边界匹配, 避免 plan/code/data/graph 等常见词假阳性。
        lowered = task.lower()
        hints: list[dict[str, Any]] = []
        for kws, cap_type, names in _KEYWORD_CAPABILITY_HINTS:
            matched = [k for k in kws if self._kw_match(k, lowered)]
            if matched:
                hints.append({"type": cap_type, "names": names, "matched": matched})
        profile["capability_hints"] = hints

        # 5) 复合任务判定: 命中多类能力 or 复杂度高 -> 需要联合/复合编排
        distinct_types = {h["type"] for h in hints}
        profile["is_composite"] = (len(distinct_types) >= 2) or profile["complexity"] >= 7
        profile["distinct_capability_types"] = sorted(distinct_types)

        return profile

    @staticmethod
    def _kw_match(keyword: str, lowered_text: str) -> bool:
        """关键词匹配: 多词短语用子串; 单个词用词边界(减少常见词假阳性)。"""
        k = keyword.lower()
        if " " in k:
            return k in lowered_text
        import re

        return re.search(r"(?<![a-z0-9_])" + re.escape(k) + r"(?![a-z0-9_])", lowered_text) is not None
