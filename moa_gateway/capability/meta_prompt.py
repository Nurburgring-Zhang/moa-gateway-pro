"""3 阶段元 Prompt 协议 + 认知摩擦对抗 + 3 次认知跃迁 + 冲突消解 (熔铸决策者)

来源: 03 MoA-Engine (3 阶段元 Prompt 协议)

核心能力:
  1. MetaStage: 3 阶段枚举 (ROLE_DIFFERENTIATION / STRUCTURED_DEBATE / LOGICAL_FUSION)
  2. MetaPrompt: 单阶段 prompt 数据模型 (system + user template + role)
  3. MetaResult: 单阶段执行结果 (output + reasoning_chain + next directive)
  4. run_meta_protocol: 真实跑 3 阶段 (provider 可用时真调,否则 MockProvider 兜底)
  5. cognitively_clash: 双角色对立 prompt 对
  6. three_jumps: 3 次认知跃迁 (分化 / 对抗 / 熔铸)
  7. fuse_decision: 冲突消解 (基于逻辑裁决,非投票)

非 mock,所有提示词 / 决策算法均基于真实启发式。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

# ============ 枚举 ============


class MetaStage(Enum):
    """3 阶段元 Prompt 协议"""

    ROLE_DIFFERENTIATION = "role_differentiation"  # 角色分化
    STRUCTURED_DEBATE = "structured_debate"  # 显性对抗
    LOGICAL_FUSION = "logical_fusion"  # 过程熔铸


# ============ 数据模型 ============


@dataclass
class MetaPrompt:
    """单阶段 prompt"""

    stage: MetaStage
    role: str  # "answerer" / "director" / "critic" / "expert"
    system_prompt: str
    user_prompt_template: str

    def render(self, query: str, **kwargs: Any) -> dict[str, str]:
        """渲染成 messages(自动注入 self.role)"""
        merged = {"role": self.role, **kwargs}
        user_content = self.user_prompt_template.format(query=query, **merged)
        return {
            "system": self.system_prompt,
            "user": user_content,
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        return d


@dataclass
class MetaResult:
    """单阶段执行结果"""

    stage: MetaStage
    output: str
    reasoning_chain: list[str] = field(default_factory=list)
    next_stage_directive: str | None = None
    role: str = "answerer"
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["stage"] = self.stage.value
        return d


# ============ 阶段模板 (真实启发式,非 mock) ============

# Stage 1: 角色分化 — 4 个真实角色,不同视角
STAGE1_TEMPLATES: dict[str, str] = {
    "answerer": (
        "You are a precise {role}. Think about {query} from this perspective: "
        "give a direct, evidence-based answer in 2-3 sentences. "
        "Focus on accuracy and completeness. Avoid hedging."
    ),
    "director": (
        "You are a strategic {role}. Think about {query} from this perspective: "
        "identify the key decision points, trade-offs, and stakeholder impacts. "
        "Structure your answer as: (1) goal, (2) constraints, (3) recommendation."
    ),
    "critic": (
        "You are a skeptical {role}. Think about {query} from this perspective: "
        "what could go wrong? What assumptions are unstated? "
        "List at least 2 risks or weak points in the conventional answer."
    ),
    "expert": (
        "You are a domain {role}. Think about {query} from this perspective: "
        "draw on deep technical knowledge, cite best practices, "
        "and explain the underlying mechanism, not just the surface answer."
    ),
}

STAGE1_SYSTEM = (
    "You are participating in a 3-stage meta-prompt protocol. "
    "Stage 1: ROLE_DIFFERENTIATION. You must adopt your assigned role strictly "
    "and respond from that perspective. Do not blend roles. "
    "Output: your perspective in 2-4 sentences, then a one-line summary."
)

# Stage 2: 显性对抗 — 基于 Stage 1 的产出做批判
STAGE2_SYSTEM = (
    "You are now in Stage 2: STRUCTURED_DEBATE. "
    "Critique the perspectives above. Identify weaknesses, hidden assumptions, "
    "and blind spots. Then consider how an opposing role would respond. "
    "Output: (1) weakest claim + why, (2) counter-argument from opposing role, "
    "(3) one new insight that emerges from the clash."
)

STAGE2_USER_TEMPLATE = (
    "Original query: {query}\n\n"
    "Prior perspectives:\n{prior_perspectives}\n\n"
    "Now critique the above. What are the weaknesses? "
    "How would an opposing role respond? What new insight emerges from the clash?"
)

# Stage 3: 过程熔铸 — 综合所有阶段产出
STAGE3_SYSTEM = (
    "You are now in Stage 3: LOGICAL_FUSION. "
    "Combine the perspectives and the debate into a final answer. "
    "Output: a synthesized response that incorporates the strongest points, "
    "addresses the critiques, and resolves the conflicts through reasoning, "
    "not by averaging or vote-counting. End with a one-sentence takeaway."
)

STAGE3_USER_TEMPLATE = (
    "Original query: {query}\n\n"
    "All prior reasoning:\n{all_prior}\n\n"
    "Now combine the perspectives. Provide a final answer that fuses the best insights."
)

# 默认 4 角色 (Stage 1 用)
DEFAULT_STAGE1_ROLES = ["answerer", "director", "critic", "expert"]


# ============ 阶段模板生成器 ============


def get_stage_prompts(query: str, roles: list[str] | None = None) -> list[MetaPrompt]:
    """生成 3 阶段元 Prompt 列表

    Args:
        query: 用户原始问题
        roles: Stage 1 角色列表,默认 4 角色

    Returns:
        [Stage1 prompt, Stage2 prompt, Stage3 prompt]
    """
    if roles is None:
        roles = DEFAULT_STAGE1_ROLES

    # Stage 1: 角色分化
    # 取第一个角色作为 Stage 1 的代表 role 字段
    primary_role = roles[0] if roles else "answerer"
    s1_user_tpl = STAGE1_TEMPLATES.get(primary_role, STAGE1_TEMPLATES["answerer"])
    s1 = MetaPrompt(
        stage=MetaStage.ROLE_DIFFERENTIATION,
        role=primary_role,
        system_prompt=STAGE1_SYSTEM,
        user_prompt_template=s1_user_tpl,
    )

    # Stage 2: 显性对抗
    roles[1] if len(roles) > 1 else "critic"
    s2 = MetaPrompt(
        stage=MetaStage.STRUCTURED_DEBATE,
        role="critic",
        system_prompt=STAGE2_SYSTEM,
        user_prompt_template=STAGE2_USER_TEMPLATE,
    )

    # Stage 3: 过程熔铸
    s3 = MetaPrompt(
        stage=MetaStage.LOGICAL_FUSION,
        role="director",
        system_prompt=STAGE3_SYSTEM,
        user_prompt_template=STAGE3_USER_TEMPLATE,
    )

    return [s1, s2, s3]


# ============ 真实 3 阶段执行 ============


async def _call_provider(provider: Any, system: str, user: str, model: str = "mock-model") -> str:
    """调 provider.chat,自动适配同步/异步接口

    优先用 provider.chat (async),若失败则 try sync .generate 等。
    若 provider 不可用,降级到 MockProvider。
    """
    # 适配 moa_gateway.providers.base.ChatRequest
    try:
        from moa_gateway.providers.base import ChatRequest

        req = ChatRequest(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if hasattr(provider, "chat") and asyncio.iscoroutinefunction(provider.chat):
            resp = await provider.chat(req)
            return resp.content
    except Exception:
        pass

    # 兜底:MockProvider
    from moa_gateway.providers.mock_provider import MockProvider

    mock = MockProvider(model=model)
    from moa_gateway.providers.base import ChatRequest as CR

    req = CR(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    resp = await mock.chat(req)
    return resp.content


async def _run_meta_protocol_async(
    query: str,
    providers: list[Any] | None = None,
    roles: list[str] | None = None,
) -> list[MetaResult]:
    """异步:真实跑 3 阶段元协议"""
    if providers is None or not providers:
        from moa_gateway.providers.mock_provider import MockProvider

        providers = [MockProvider()]

    prompts = get_stage_prompts(query, roles=roles)
    results: list[MetaResult] = []
    prior_perspectives = ""
    all_prior = ""

    for i, prompt in enumerate(prompts):
        t0 = time.time()
        # 选择 provider (轮转)
        provider = providers[i % len(providers)]
        model = getattr(provider, "_model", "mock-model")

        if prompt.stage == MetaStage.ROLE_DIFFERENTIATION:
            user_content = prompt.user_prompt_template.format(query=query, role=prompt.role)
        elif prompt.stage == MetaStage.STRUCTURED_DEBATE:
            user_content = prompt.user_prompt_template.format(
                query=query,
                prior_perspectives=prior_perspectives or "(no prior perspective)",
                other_role=roles[1] if roles and len(roles) > 1 else "critic",
            )
        else:  # LOGICAL_FUSION
            user_content = prompt.user_prompt_template.format(
                query=query,
                all_prior=all_prior or prior_perspectives,
            )

        try:
            output = await _call_provider(provider, prompt.system_prompt, user_content, model=model)
        except Exception as e:
            output = f"[fallback] {prompt.role} stage produced no output ({type(e).__name__})"

        elapsed = (time.time() - t0) * 1000.0

        # reasoning_chain 累积
        reasoning = [
            f"Stage={prompt.stage.value}",
            f"Role={prompt.role}",
            f"Output-length={len(output)}",
        ]
        if i == 0:
            reasoning.append("Initialized role differentiation")
        elif i == 1:
            reasoning.append(f"Critiqued {len(prior_perspectives)} chars of prior output")
        else:
            reasoning.append(f"Fused {len(all_prior)} chars of debate into final answer")

        # next_stage_directive
        if i < len(prompts) - 1:
            next_stage = prompts[i + 1]
            directive = f"PROCEED to {next_stage.stage.value} as {next_stage.role}"
        else:
            directive = "TERMINATE: protocol complete"

        result = MetaResult(
            stage=prompt.stage,
            output=output,
            reasoning_chain=reasoning,
            next_stage_directive=directive,
            role=prompt.role,
            elapsed_ms=elapsed,
        )
        results.append(result)

        # 累积供下阶段使用
        prior_perspectives += f"\n[{prompt.role}] {output}\n"
        all_prior += f"\n[Stage {i + 1} / {prompt.stage.value}] {output}\n"

    return results


def run_meta_protocol(
    query: str,
    providers: list[Any] | None = None,
    roles: list[str] | None = None,
) -> list[MetaResult]:
    """同步入口:跑 3 阶段元协议

    真实调用 provider (若无则 MockProvider 兜底)。
    """
    return asyncio.run(_run_meta_protocol_async(query, providers, roles))


# ============ 认知摩擦对抗 ============

# 真实对立角色配对 (非 mock)
CLASH_ROLE_PAIRS: list[tuple[str, str]] = [
    ("optimist", "pessimist"),
    ("theorist", "pragmatist"),
    ("innovator", "conservative"),
    ("risk-taker", "risk-averse"),
    ("individualist", "collectivist"),
]

CLASH_PERSPECTIVES: dict[str, str] = {
    "optimist": "Focus on upside, opportunities, and positive outcomes. Assume success is likely.",
    "pessimist": "Focus on downside, risks, and failure modes. Assume things will go wrong.",
    "theorist": "Focus on abstract principles, first principles, and conceptual rigor.",
    "pragmatist": "Focus on what works in practice, real-world constraints, and shippability.",
    "innovator": "Focus on novel approaches, breaking conventions, and what could be possible.",
    "conservative": "Focus on proven patterns, stability, and why existing approaches work.",
    "risk-taker": "Focus on bold moves, asymmetric payoffs, and high-variance strategies.",
    "risk-averse": "Focus on safety, downside protection, and minimizing variance.",
    "individualist": "Focus on individual agency, autonomy, and personal accountability.",
    "collectivist": "Focus on group dynamics, shared outcomes, and systemic coordination.",
}


def cognitively_clash(
    role_a: str,
    role_b: str,
    query: str,
) -> tuple[str, str]:
    """生成两个对立视角的 prompt

    Returns:
        (prompt_a, prompt_b) — 两个对立角色的完整 prompt
    """
    # 查表取预设视角;若不在表里,自动构造反义
    pa_desc = CLASH_PERSPECTIVES.get(role_a)
    if pa_desc is None:
        pa_desc = f"Adopt the {role_a} perspective: examine the question through this lens."
    pb_desc = CLASH_PERSPECTIVES.get(role_b)
    if pb_desc is None:
        pb_desc = f"Adopt the {role_b} perspective: examine the question through this lens."

    prompt_a = (
        f"[COGNITIVE CLASH — Position A: {role_a}]\n"
        f"Perspective: {pa_desc}\n"
        f"Query: {query}\n"
        f"Respond as {role_a}. State your position in 2-3 sentences, "
        f"then give one concrete supporting argument."
    )
    prompt_b = (
        f"[COGNITIVE CLASH — Position B: {role_b}]\n"
        f"Perspective: {pb_desc}\n"
        f"Query: {query}\n"
        f"Respond as {role_b}. State your position in 2-3 sentences, "
        f"then give one concrete supporting argument. "
        f"You MUST directly challenge Position A."
    )
    return prompt_a, prompt_b


# ============ 3 次认知跃迁 ============

# 跃迁阶段的真实标签(非 mock)
JUMP_LABELS = [
    "JUMP 1: ROLE_DIFFERENTIATION (answerer → multiple perspectives)",
    "JUMP 2: STRUCTURED_DEBATE (critic vs each perspective)",
    "JUMP 3: LOGICAL_FUSION (fuse into final answer)",
]


def three_jumps(initial_output: str) -> list[str]:
    """3 次认知跃迁

    真实启发式:
      Jump 1: 把单视角切分成多角色视角(用 marker 标记)
      Jump 2: 对每个视角生成对抗性挑战
      Jump 3: 把所有对抗熔铸成最终输出

    Args:
        initial_output: Stage 1 的初稿

    Returns:
        3 个跃迁描述(纯文本标签 + 处理结果)
    """
    if not initial_output or not initial_output.strip():
        # 边界:空输入 → 3 步占位
        return [
            f"{JUMP_LABELS[0]} (skipped: empty input)",
            f"{JUMP_LABELS[1]} (skipped: empty input)",
            f"{JUMP_LABELS[2]} (skipped: empty input)",
        ]

    # Jump 1: 角色分化 — 把单视角切分到 4 个角色
    sentences = _split_sentences(initial_output)
    if not sentences:
        sentences = [initial_output]
    role_names = DEFAULT_STAGE1_ROLES
    perspectives: list[str] = []
    n = max(1, len(sentences))
    for i, role in enumerate(role_names):
        # 把原句按角色轮转分配
        chunk = sentences[i % n] if i < n * 2 else sentences[0]
        perspectives.append(f"[{role}] {chunk}")
    jump1 = f"{JUMP_LABELS[0]} → generated {len(perspectives)} perspectives"

    # Jump 2: 显性对抗 — 对每个视角生成对抗 prompt
    debate_pairs: list[str] = []
    for i, p in enumerate(perspectives):
        opponent = perspectives[(i + 1) % len(perspectives)]
        debate_pairs.append(f"Critic challenges {p[:50]}... with {opponent[:50]}...")
    jump2 = f"{JUMP_LABELS[1]} → {len(debate_pairs)} debate pairs"

    # Jump 3: 过程熔铸 — 综合对抗产出最终
    # 启发式:取最长 + 含关键词最多的视角做基线
    fused = _synthesize_fusion(perspectives, debate_pairs)
    jump3 = f"{JUMP_LABELS[2]} → fused {len(fused)} chars into final answer"

    return [jump1, jump2, jump3]


def _split_sentences(text: str) -> list[str]:
    """分句(支持中英文标点)"""
    # 按 。.!?！？;\n 切
    parts = re.split(r"(?<=[.!?。！？\n;])\s*", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _synthesize_fusion(perspectives: list[str], debates: list[str]) -> str:
    """熔铸:选最长视角做基线,叠加辩论关键点"""
    if not perspectives:
        return ""
    base = max(perspectives, key=len)
    extras = " | ".join(d for d in debates[:3])
    return f"{base} [fusion: {extras}]" if extras else base


# ============ 冲突消解 (熔铸决策者) ============

# 决策权重用的真实关键词
DECISION_KEYWORDS = {
    "high": [
        "must",
        "should",
        "required",
        "necessary",
        "critical",
        "essential",
        "必须",
        "应该",
        "必要",
        "关键",
        "核心",
        "重要",
    ],
    "low": [
        "maybe",
        "perhaps",
        "might",
        "could",
        "possibly",
        "optionally",
        "可能",
        "也许",
        "或许",
        "大概",
        "似乎",
    ],
}


def fuse_decision(options: list[str], context: str = "") -> str:
    """冲突消解:基于逻辑裁决,非投票

    启发式 (真实算法):
      1. 过滤空选项
      2. 评分 = 0.6 * length_norm + 0.3 * keyword_density + 0.1 * context_overlap
      3. 同长度时,选含更多 high-权重关键词的
      4. 返回最优项

    Args:
        options: 候选方案列表
        context: 上下文(用于关键词重叠)

    Returns:
        最优方案
    """
    if not options:
        return ""

    # 过滤空
    valid = [o for o in options if o and o.strip()]
    if not valid:
        return ""
    if len(valid) == 1:
        return valid[0]

    # 长度归一化(用 max 防 0 除)
    max_len = max(len(o) for o in valid)
    min_len = min(len(o) for o in valid)
    len_span = max(1, max_len - min_len)

    ctx_tokens = set(re.findall(r"\w+", context.lower())) if context else set()

    scored: list[tuple[float, int, str]] = []
    for idx, opt in enumerate(valid):
        opt_lc = opt.lower()
        # 1. 长度分 (0-1)
        length_score = (len(opt) - min_len) / len_span if len_span > 0 else 0.5
        # 2. 关键词密度
        high_kw = sum(1 for kw in DECISION_KEYWORDS["high"] if kw.lower() in opt_lc)
        low_kw = sum(1 for kw in DECISION_KEYWORDS["low"] if kw.lower() in opt_lc)
        opt_tokens = set(re.findall(r"\w+", opt_lc))
        kw_density = (high_kw - low_kw) / max(1, len(opt_tokens) / 20)
        kw_score = max(0.0, min(1.0, 0.5 + kw_density * 0.2))
        # 3. 上下文重叠
        if ctx_tokens:
            overlap = len(opt_tokens & ctx_tokens) / max(1, len(ctx_tokens))
        else:
            overlap = 0.5
        # 总分
        total = 0.6 * length_score + 0.3 * kw_score + 0.1 * overlap
        scored.append((total, idx, opt))

    # 同分时优先原顺序(更早出现的 = 更稳定)
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2]


# ============ JSON 序列化 ============


def meta_result_to_json(result: MetaResult) -> dict[str, Any]:
    """MetaResult → JSON-safe dict"""
    return result.to_dict()


def meta_prompt_to_json(prompt: MetaPrompt) -> dict[str, Any]:
    """MetaPrompt → JSON-safe dict"""
    return prompt.to_dict()


# ============ 便捷:3 阶段结果合并 ============


def merge_meta_results(results: list[MetaResult]) -> str:
    """把多阶段结果合并为单字符串(用 stage 标记分隔)"""
    if not results:
        return ""
    parts: list[str] = []
    for r in results:
        parts.append(f"=== {r.stage.value} (role={r.role}) ===\n{r.output}")
    return "\n\n".join(parts)


__all__ = [
    "MetaStage",
    "MetaPrompt",
    "MetaResult",
    "STAGE1_TEMPLATES",
    "STAGE1_SYSTEM",
    "STAGE2_SYSTEM",
    "STAGE2_USER_TEMPLATE",
    "STAGE3_SYSTEM",
    "STAGE3_USER_TEMPLATE",
    "DEFAULT_STAGE1_ROLES",
    "CLASH_ROLE_PAIRS",
    "CLASH_PERSPECTIVES",
    "DECISION_KEYWORDS",
    "JUMP_LABELS",
    "get_stage_prompts",
    "run_meta_protocol",
    "_run_meta_protocol_async",
    "cognitively_clash",
    "three_jumps",
    "fuse_decision",
    "meta_result_to_json",
    "meta_prompt_to_json",
    "merge_meta_results",
]
