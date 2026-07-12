"""moa_gateway.providers.mock — Mock Provider(无需 API key,返回真实有意义的回答)

用途:
- 没有 API key 时演示整个 MoA 协作流程
- 单元测试 / 集成测试
- 离线开发

行为:
- 根据 query 类型(代码/中文/翻译/分析/创意等)智能返回合理答案
- 模拟 token 计数 + 成本
- 模拟 latency
- 支持流式输出
"""
from __future__ import annotations
import asyncio
import time
import random
import re
from typing import Dict, List, Optional, AsyncIterator
from .base import Provider, ChatRequest, ChatResponse


# 不同 query 类型的智能回答模板
MOCK_RESPONSES = {
    "code": [
        "这是该问题的 Python 实现方案:\n\n```python\ndef solution(input):\n    \"\"\"docstring\"\"\"\n    # 核心逻辑\n    result = process(input)\n    return result\n```\n\n关键点:\n1. 时间复杂度 O(n)\n2. 空间复杂度 O(1)\n3. 边界情况已处理",
        "推荐使用标准库实现:\n\n```python\nfrom collections import OrderedDict\n\nclass LRUCache:\n    def __init__(self, capacity: int):\n        self.cache = OrderedDict()\n        self.capacity = capacity\n\n    def get(self, key):\n        if key in self.cache:\n            self.cache.move_to_end(key)\n            return self.cache[key]\n        return -1\n\n    def put(self, key, value):\n        if key in self.cache:\n            self.cache.move_to_end(key)\n        self.cache[key] = value\n        if len(self.cache) > self.capacity:\n            self.cache.popitem(last=False)\n```\n\n这是一个经典 O(1) 复杂度的 LRU Cache 实现。",
    ],
    "chinese": [
        "这个问题涉及中国文化的几个核心要素:\n\n1. **历史脉络**:从先秦到现代,经历了多次文化转型\n2. **核心精神**:仁义礼智信,这是中国传统价值观的基石\n3. **现代意义**:在新时代背景下,这些价值观仍有重要的指导意义\n\n总而言之,这个问题需要从历史和现实两个维度去理解。",
        "中国传统文化的精髓可以概括为:\n\n- **和而不同**:多元共生\n- **天人合一**:人与自然和谐\n- **自强不息**:刚健有为\n- **厚德载物**:宽容博爱\n\n这些思想至今仍深刻影响着中国人的行为方式和价值判断。",
    ],
    "math": [
        "解题思路:\n\n设未知数为 x,根据题意列出方程:\n\n    3x + 7 = 22\n\n求解:\n    3x = 22 - 7 = 15\n    x = 5\n\n**答案: x = 5**\n\n验证: 3×5 + 7 = 22 ✓",
        "可以用以下步骤求解:\n\n1. 分析问题,确定已知量\n2. 建立数学模型\n3. 求解方程\n4. 验证结果\n\n最终结果符合题目条件。",
    ],
    "translation": [
        "Translation:\n\nThe MoA (Mixture of Agents) approach leverages the collective intelligence of multiple LLMs through layered architecture. Each layer contains multiple agents that take outputs from previous layers as auxiliary information.\n\nKey advantages:\n- Improved response quality through collaboration\n- Better factuality and reasoning\n- Diversity of perspectives",
    ],
    "general": [
        "这是对您问题的综合分析:\n\n**核心观点**\n\n1. 从多个角度审视,这个问题涉及技术、流程、人三个层面\n2. 技术层面有 3-4 个可行的实现路径\n3. 流程层面需要规范化\n4. 人层面需要培训与协作\n\n**建议方案**\n\n推荐采用渐进式实施:先做最小可行版本(MVP),收集反馈,然后逐步迭代优化。这样既能快速验证,又能控制风险。",
        "针对您的问题,我从以下几个维度展开:\n\n**问题分析**\n- 表层症状:用户体验下降\n- 根本原因:架构设计未考虑扩展性\n- 影响范围:核心业务流程\n\n**解决方案**\n1. 短期(1 周):修复关键问题,恢复基本服务\n2. 中期(1 月):重构核心模块,提升可维护性\n3. 长期(3-6 月):全面优化,达到行业领先水平\n\n每个阶段都有明确的交付物和验证标准。",
    ],
    "creative": [
        "为这个主题创作一个富有想象力的作品:\n\n晨光穿透薄雾,洒在古老的青石板路上,折射出千万道金色的丝线。\n\n他站在那棵千年银杏树下,任凭金黄的叶片如蝴蝶般飘落在肩头。每一片叶,都承载着一段被时光封存的记忆。\n\n风起,叶落,故事在这个清晨重新开始。",
    ],
}


def _detect_query_type(query: str) -> str:
    """根据 query 关键字检测类型"""
    q = query.lower()
    if re.search(r"代码|编程|实现|写一个|写一段|python|java|c\+\+|function|class|算法|code", q, re.I):
        return "code"
    if re.search(r"中文|古诗|李白|文化|历史|哲学|思想", q):
        return "chinese"
    if re.search(r"求|解|方程|数学|计算|等于|公式", q):
        return "math"
    if re.search(r"翻译|英文|english|translate", q):
        return "translation"
    if re.search(r"诗|故事|创作|写一|想象", q):
        return "creative"
    return "general"


def _generate_response(query: str, model: str) -> tuple:
    """生成 (content, prompt_tokens, completion_tokens)"""
    qtype = _detect_query_type(query)
    candidates = MOCK_RESPONSES.get(qtype, MOCK_RESPONSES["general"])
    content = random.choice(candidates)
    # 加点"模型特征" — 不同 mock model 输出略有不同
    variations = {
        "fast": "[Fast Mock] ",
        "balanced": "[Balanced Mock] ",
        "quality": "[Quality Mock — Deep Reasoning]\n\n",
        "premium": "[Premium Mock]\n\n",
    }
    for k, prefix in variations.items():
        if k in model.lower():
            content = prefix + content
            break
    else:
        content = f"[Mock:{model}] " + content
    # 估算 tokens(粗略 1 token ≈ 2 字符)
    pt = max(50, len(query) // 2)
    ct = max(100, len(content) // 2)
    return content, pt, ct


def _estimate_cost(model: str, pt: int, ct: int) -> float:
    """Mock 成本估算(便宜但不是 0)"""
    pricing = {
        "lite": (0.0001, 0.0001),
        "standard": (0.0005, 0.001),
        "premium": (0.002, 0.006),
        "flagship": (0.005, 0.015),
    }
    for tier, (c_in, c_out) in pricing.items():
        if tier in model.lower():
            return (pt / 1000) * c_in + (ct / 1000) * c_out
    return (pt + ct) / 1000 * 0.001


class MockProvider(Provider):
    """Mock Provider — 不打真 API,返回智能模拟回答"""

    def __init__(self, model: str = "mock-model", **kwargs):
        # 不需要 api_base / api_key
        super().__init__(api_base="mock://", api_key="mock-key", **kwargs)
        self._model = model
        self._call_count = 0

    async def chat(self, req: ChatRequest) -> ChatResponse:
        t0 = time.time()
        # 模拟网络延迟(lite 50-150ms,standard 200-500ms,premium 500-1500ms)
        delay = 0.1 + random.random() * 0.4
        if "lite" in req.model.lower() or "turbo" in req.model.lower():
            delay *= 0.5
        elif "premium" in req.model.lower() or "max" in req.model.lower():
            delay *= 2.0
        await asyncio.sleep(delay)

        # 提取用户问题
        user_msg = ""
        for m in reversed(req.messages):
            if m.get("role") == "user":
                user_msg = m.get("content", "")
                break
        if not user_msg:
            user_msg = "Hello"

        content, pt, ct = _generate_response(user_msg, req.model)
        cost = _estimate_cost(req.model, pt, ct)
        self._call_count += 1

        return ChatResponse(
            content=content,
            finish_reason="stop",
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=pt + ct,
            model=req.model,
            provider="mock",
            latency_ms=(time.time() - t0) * 1000,
            cost=cost,
        )

    async def chat_stream(self, req: ChatRequest) -> AsyncIterator[str]:
        """流式输出 — 按词 yield"""
        resp = await self.chat(req)
        # 模拟流式:按字符 yield
        words = re.split(r"(\s+)", resp.content)
        for w in words:
            await asyncio.sleep(0.02)
            yield w

    async def health_check(self) -> bool:
        """Mock 永远健康"""
        return True
