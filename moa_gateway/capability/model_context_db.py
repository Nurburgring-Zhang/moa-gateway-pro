"""moa_gateway.capability.model_context_db — 40+ 模型上下文窗口数据库 + 智能 max_tokens

来源: 10 Verdex (40+ 模型上下文数据库 + 动态熔断器)

提供:
- ModelSpec dataclass: 单模型完整规格(context / max_output / cost / 能力)
- MODEL_DATABASE: 40+ 真实模型手写数据库(基于公开数据)
- get_model_spec: 按 id 查规格
- list_models: 多维过滤(提供商/工具/视觉/最小上下文/最大成本)
- calculate_max_tokens: 根据 context window 智能算 max_tokens(含 safety margin)
- estimate_cost: 按 token 数估算 USD 成本
- find_cheapest_for_context: 找能装下指定 context 的最便宜模型
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)

__all__ = [
    "ModelSpec",
    "MODEL_DATABASE",
    "get_model_spec",
    "list_models",
    "calculate_max_tokens",
    "estimate_cost",
    "find_cheapest_for_context",
]


@dataclass
class ModelSpec:
    """单个模型规格

    所有字段都基于公开数据手写,用于:
    1) 路由层根据 context 选模型
    2) calculate_max_tokens 防止超 context
    3) estimate_cost 提前算 USD
    4) 找 cheapest fit
    """

    id: str
    provider: str
    family: str
    context_window: int
    max_output: int
    input_cost_per_1k: float
    output_cost_per_1k: float
    supports_tools: bool = True
    supports_vision: bool = False
    supports_streaming: bool = True
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def effective_max_tokens(self, input_tokens: int, safety_margin: float = 0.1) -> int:
        """根据当前 input 算理论上还剩多少输出空间

        公式: (context_window - input_tokens) * (1 - safety_margin)
        """
        if input_tokens < 0:
            raise ValueError(f"input_tokens must be >= 0, got {input_tokens}")
        if not 0.0 <= safety_margin < 1.0:
            raise ValueError(f"safety_margin must be in [0, 1), got {safety_margin}")
        remaining = self.context_window - input_tokens
        if remaining <= 0:
            return 0
        usable = int(remaining * (1.0 - safety_margin))
        return max(0, min(usable, self.max_output))


# =============================================================================
# 真实模型数据库(40+)
# 数据源:各厂商官方文档 / OpenRouter / SiliconFlow 公开价目(2024-2025)
# 注:成本单位 USD/1k tokens,价格可能随厂商调整而变化
# =============================================================================

MODEL_DATABASE: dict[str, ModelSpec] = {
    # ===================== 国产模型 =====================
    "deepseek-v3": ModelSpec(
        id="deepseek-v3",
        provider="deepseek",
        family="deepseek",
        context_window=64000,
        max_output=8000,
        input_cost_per_1k=0.00027,
        output_cost_per_1k=0.0011,
        supports_tools=True,
        supports_vision=False,
        notes="DeepSeek-V3 64K context, MoE 架构,极低成本",
    ),
    "deepseek-r1": ModelSpec(
        id="deepseek-r1",
        provider="deepseek",
        family="deepseek",
        context_window=64000,
        max_output=8000,
        input_cost_per_1k=0.00055,
        output_cost_per_1k=0.0022,
        supports_tools=True,
        supports_vision=False,
        notes="DeepSeek-R1 推理模型,带思维链",
    ),
    "glm-4-plus": ModelSpec(
        id="glm-4-plus",
        provider="zhipu",
        family="glm",
        context_window=128000,
        max_output=4000,
        input_cost_per_1k=0.007,
        output_cost_per_1k=0.007,
        supports_tools=True,
        supports_vision=False,
        notes="智谱 GLM-4-Plus 128K,Function call 支持",
    ),
    "glm-4-flash": ModelSpec(
        id="glm-4-flash",
        provider="zhipu",
        family="glm",
        context_window=128000,
        max_output=4000,
        input_cost_per_1k=0.0001,
        output_cost_per_1k=0.0001,
        supports_tools=True,
        supports_vision=False,
        notes="智谱 GLM-4-Flash 极速低价",
    ),
    "moonshot-v1-8k": ModelSpec(
        id="moonshot-v1-8k",
        provider="moonshot",
        family="moonshot",
        context_window=8000,
        max_output=2000,
        input_cost_per_1k=0.002,
        output_cost_per_1k=0.002,
        supports_tools=True,
        supports_vision=False,
        notes="Moonshot Kimi 8K 上下文",
    ),
    "moonshot-v1-32k": ModelSpec(
        id="moonshot-v1-32k",
        provider="moonshot",
        family="moonshot",
        context_window=32000,
        max_output=2000,
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.003,
        supports_tools=True,
        supports_vision=False,
        notes="Moonshot Kimi 32K",
    ),
    "moonshot-v1-128k": ModelSpec(
        id="moonshot-v1-128k",
        provider="moonshot",
        family="moonshot",
        context_window=128000,
        max_output=2000,
        input_cost_per_1k=0.006,
        output_cost_per_1k=0.006,
        supports_tools=True,
        supports_vision=False,
        notes="Moonshot Kimi 128K 长文档",
    ),
    "qwen-plus": ModelSpec(
        id="qwen-plus",
        provider="alibaba",
        family="qwen",
        context_window=131072,
        max_output=8192,
        input_cost_per_1k=0.0008,
        output_cost_per_1k=0.002,
        supports_tools=True,
        supports_vision=False,
        notes="通义千问 Plus 131K,平衡性能与价格",
    ),
    "qwen-max": ModelSpec(
        id="qwen-max",
        provider="alibaba",
        family="qwen",
        context_window=32768,
        max_output=8192,
        input_cost_per_1k=0.0024,
        output_cost_per_1k=0.0096,
        supports_tools=True,
        supports_vision=False,
        notes="通义千问 Max 高质量推理",
    ),
    "qwen-long": ModelSpec(
        id="qwen-long",
        provider="alibaba",
        family="qwen",
        context_window=1000000,
        max_output=6000,
        input_cost_per_1k=0.0005,
        output_cost_per_1k=0.002,
        supports_tools=True,
        supports_vision=False,
        notes="通义千问 Long 1M 超长上下文",
    ),
    "qwen-vl-max": ModelSpec(
        id="qwen-vl-max",
        provider="alibaba",
        family="qwen",
        context_window=32768,
        max_output=8192,
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.009,
        supports_tools=False,
        supports_vision=True,
        notes="通义千问 VL Max 多模态视觉",
    ),
    "doubao-pro": ModelSpec(
        id="doubao-pro",
        provider="bytedance",
        family="doubao",
        context_window=128000,
        max_output=4096,
        input_cost_per_1k=0.0008,
        output_cost_per_1k=0.001,
        supports_tools=True,
        supports_vision=False,
        notes="豆包 Pro 128K",
    ),
    "doubao-lite": ModelSpec(
        id="doubao-lite",
        provider="bytedance",
        family="doubao",
        context_window=128000,
        max_output=4096,
        input_cost_per_1k=0.0003,
        output_cost_per_1k=0.0006,
        supports_tools=True,
        supports_vision=False,
        notes="豆包 Lite 极速低价",
    ),
    "baichuan3-turbo": ModelSpec(
        id="baichuan3-turbo",
        provider="baichuan",
        family="baichuan",
        context_window=32000,
        max_output=4000,
        input_cost_per_1k=0.001,
        output_cost_per_1k=0.001,
        supports_tools=True,
        supports_vision=False,
        notes="百川 3 Turbo 32K",
    ),
    "lingyi": ModelSpec(
        id="lingyi",
        provider="zero-one",
        family="lingyi",
        context_window=16000,
        max_output=2000,
        input_cost_per_1k=0.001,
        output_cost_per_1k=0.001,
        supports_tools=True,
        supports_vision=False,
        notes="零一万物 Lingyi 16K",
    ),
    # ===================== OpenAI =====================
    "gpt-4o": ModelSpec(
        id="gpt-4o",
        provider="openai",
        family="gpt-4o",
        context_window=128000,
        max_output=16384,
        input_cost_per_1k=0.0025,
        output_cost_per_1k=0.01,
        supports_tools=True,
        supports_vision=True,
        notes="OpenAI GPT-4o 多模态 128K",
    ),
    "gpt-4o-mini": ModelSpec(
        id="gpt-4o-mini",
        provider="openai",
        family="gpt-4o",
        context_window=128000,
        max_output=16384,
        input_cost_per_1k=0.00015,
        output_cost_per_1k=0.0006,
        supports_tools=True,
        supports_vision=True,
        notes="OpenAI GPT-4o-mini 极低价",
    ),
    "gpt-4-turbo": ModelSpec(
        id="gpt-4-turbo",
        provider="openai",
        family="gpt-4",
        context_window=128000,
        max_output=4096,
        input_cost_per_1k=0.01,
        output_cost_per_1k=0.03,
        supports_tools=True,
        supports_vision=True,
        notes="OpenAI GPT-4 Turbo 128K 视觉",
    ),
    "o1-preview": ModelSpec(
        id="o1-preview",
        provider="openai",
        family="o1",
        context_window=128000,
        max_output=32768,
        input_cost_per_1k=0.015,
        output_cost_per_1k=0.06,
        supports_tools=False,
        supports_vision=False,
        notes="OpenAI o1-preview 推理模型,无 tools",
    ),
    "o1-mini": ModelSpec(
        id="o1-mini",
        provider="openai",
        family="o1",
        context_window=128000,
        max_output=65536,
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.012,
        supports_tools=False,
        supports_vision=False,
        notes="OpenAI o1-mini 推理小模型",
    ),
    "o3-mini": ModelSpec(
        id="o3-mini",
        provider="openai",
        family="o3",
        context_window=200000,
        max_output=100000,
        input_cost_per_1k=0.0011,
        output_cost_per_1k=0.0044,
        supports_tools=True,
        supports_vision=False,
        notes="OpenAI o3-mini 200K 推理,支持 tools",
    ),
    "gpt-3.5-turbo": ModelSpec(
        id="gpt-3.5-turbo",
        provider="openai",
        family="gpt-3.5",
        context_window=16385,
        max_output=4096,
        input_cost_per_1k=0.0005,
        output_cost_per_1k=0.0015,
        supports_tools=True,
        supports_vision=False,
        notes="OpenAI GPT-3.5 Turbo 16K 经典款",
    ),
    # ===================== Anthropic =====================
    "claude-3-5-sonnet": ModelSpec(
        id="claude-3-5-sonnet",
        provider="anthropic",
        family="claude-3.5",
        context_window=200000,
        max_output=8192,
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        supports_tools=True,
        supports_vision=True,
        notes="Claude 3.5 Sonnet 200K 多模态",
    ),
    "claude-3-opus": ModelSpec(
        id="claude-3-opus",
        provider="anthropic",
        family="claude-3",
        context_window=200000,
        max_output=4096,
        input_cost_per_1k=0.015,
        output_cost_per_1k=0.075,
        supports_tools=True,
        supports_vision=True,
        notes="Claude 3 Opus 高质量但昂贵",
    ),
    "claude-3-haiku": ModelSpec(
        id="claude-3-haiku",
        provider="anthropic",
        family="claude-3",
        context_window=200000,
        max_output=4096,
        input_cost_per_1k=0.00025,
        output_cost_per_1k=0.00125,
        supports_tools=True,
        supports_vision=True,
        notes="Claude 3 Haiku 极速低价",
    ),
    "claude-3-5-haiku": ModelSpec(
        id="claude-3-5-haiku",
        provider="anthropic",
        family="claude-3.5",
        context_window=200000,
        max_output=8192,
        input_cost_per_1k=0.0008,
        output_cost_per_1k=0.004,
        supports_tools=True,
        supports_vision=True,
        notes="Claude 3.5 Haiku 升级版",
    ),
    # ===================== Google =====================
    "gemini-1.5-pro": ModelSpec(
        id="gemini-1.5-pro",
        provider="google",
        family="gemini-1.5",
        context_window=2000000,
        max_output=8192,
        input_cost_per_1k=0.00125,
        output_cost_per_1k=0.005,
        supports_tools=True,
        supports_vision=True,
        notes="Gemini 1.5 Pro 2M 超长上下文",
    ),
    "gemini-1.5-flash": ModelSpec(
        id="gemini-1.5-flash",
        provider="google",
        family="gemini-1.5",
        context_window=1000000,
        max_output=8192,
        input_cost_per_1k=0.000075,
        output_cost_per_1k=0.0003,
        supports_tools=True,
        supports_vision=True,
        notes="Gemini 1.5 Flash 极低价 1M context",
    ),
    "gemini-2.0-flash": ModelSpec(
        id="gemini-2.0-flash",
        provider="google",
        family="gemini-2.0",
        context_window=1000000,
        max_output=8192,
        input_cost_per_1k=0.0001,
        output_cost_per_1k=0.0004,
        supports_tools=True,
        supports_vision=True,
        notes="Gemini 2.0 Flash 实验版",
    ),
    # ===================== Mistral =====================
    "mistral-large": ModelSpec(
        id="mistral-large",
        provider="mistral",
        family="mistral-large",
        context_window=128000,
        max_output=8192,
        input_cost_per_1k=0.002,
        output_cost_per_1k=0.006,
        supports_tools=True,
        supports_vision=False,
        notes="Mistral Large 128K",
    ),
    "mistral-small": ModelSpec(
        id="mistral-small",
        provider="mistral",
        family="mistral-small",
        context_window=32000,
        max_output=8192,
        input_cost_per_1k=0.0002,
        output_cost_per_1k=0.0006,
        supports_tools=True,
        supports_vision=False,
        notes="Mistral Small 经济型",
    ),
    "mixtral-8x7b": ModelSpec(
        id="mixtral-8x7b",
        provider="mistral",
        family="mixtral",
        context_window=32000,
        max_output=4096,
        input_cost_per_1k=0.0006,
        output_cost_per_1k=0.0006,
        supports_tools=True,
        supports_vision=False,
        notes="Mixtral 8x7B MoE 32K",
    ),
    # ===================== SiliconFlow (OpenAI 兼容 API) =====================
    "Qwen/Qwen2.5-72B-Instruct": ModelSpec(
        id="Qwen/Qwen2.5-72B-Instruct",
        provider="siliconflow",
        family="qwen",
        context_window=131072,
        max_output=8192,
        input_cost_per_1k=0.00041,
        output_cost_per_1k=0.00124,
        supports_tools=True,
        supports_vision=False,
        notes="SiliconFlow Qwen2.5-72B 131K",
    ),
    "deepseek-ai/DeepSeek-V3": ModelSpec(
        id="deepseek-ai/DeepSeek-V3",
        provider="siliconflow",
        family="deepseek",
        context_window=64000,
        max_output=8000,
        input_cost_per_1k=0.00027,
        output_cost_per_1k=0.0011,
        supports_tools=True,
        supports_vision=False,
        notes="SiliconFlow DeepSeek-V3 镜像",
    ),
    "meta-llama/Llama-3.3-70B-Instruct": ModelSpec(
        id="meta-llama/Llama-3.3-70B-Instruct",
        provider="siliconflow",
        family="llama",
        context_window=131072,
        max_output=8192,
        input_cost_per_1k=0.00059,
        output_cost_per_1k=0.00079,
        supports_tools=True,
        supports_vision=False,
        notes="SiliconFlow Llama-3.3-70B 131K",
    ),
    # ===================== OpenRouter (聚合路由) =====================
    "anthropic/claude-3.5-sonnet": ModelSpec(
        id="anthropic/claude-3.5-sonnet",
        provider="openrouter",
        family="claude-3.5",
        context_window=200000,
        max_output=8192,
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        supports_tools=True,
        supports_vision=True,
        notes="OpenRouter 路由 Claude 3.5 Sonnet",
    ),
    "openai/gpt-4o": ModelSpec(
        id="openai/gpt-4o",
        provider="openrouter",
        family="gpt-4o",
        context_window=128000,
        max_output=16384,
        input_cost_per_1k=0.0025,
        output_cost_per_1k=0.01,
        supports_tools=True,
        supports_vision=True,
        notes="OpenRouter 路由 GPT-4o",
    ),
    "google/gemini-pro-1.5": ModelSpec(
        id="google/gemini-pro-1.5",
        provider="openrouter",
        family="gemini-1.5",
        context_window=2000000,
        max_output=8192,
        input_cost_per_1k=0.00125,
        output_cost_per_1k=0.005,
        supports_tools=True,
        supports_vision=True,
        notes="OpenRouter 路由 Gemini 1.5 Pro 2M",
    ),
    # ===================== Mock(测试 / dry-run) =====================
    "mock": ModelSpec(
        id="mock",
        provider="mock",
        family="mock",
        context_window=8000,
        max_output=2000,
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
        supports_tools=True,
        supports_vision=False,
        notes="Mock 模型 0 成本,用于 dry-run",
    ),
    "mock-fast": ModelSpec(
        id="mock-fast",
        provider="mock",
        family="mock",
        context_window=4000,
        max_output=1000,
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
        supports_tools=False,
        supports_vision=False,
        notes="Mock 快速小模型",
    ),
    "mock-smart": ModelSpec(
        id="mock-smart",
        provider="mock",
        family="mock",
        context_window=200000,
        max_output=8192,
        input_cost_per_1k=0.0,
        output_cost_per_1k=0.0,
        supports_tools=True,
        supports_vision=True,
        notes="Mock 智能大模型(无成本)",
    ),
}


# =============================================================================
# API
# =============================================================================


def get_model_spec(model_id: str) -> ModelSpec | None:
    """查 model 规格(精确匹配)

    Args:
        model_id: 模型 id,如 "deepseek-v3" / "gpt-4o"

    Returns:
        ModelSpec 或 None(未找到)
    """
    return MODEL_DATABASE.get(model_id)


def list_models(
    provider: str | None = None,
    supports_tools: bool | None = None,
    supports_vision: bool | None = None,
    min_context: int = 0,
    max_cost: float | None = None,
) -> list[ModelSpec]:
    """列模型,按过滤条件

    Args:
        provider: 过滤 provider,如 "openai" / "deepseek"
        supports_tools: 过滤是否支持 tools/function call
        supports_vision: 过滤是否支持 vision
        min_context: 最小 context window(>=)
        max_cost: input cost per 1k 的上限(<,不是 <=)

    Returns:
        过滤后的 ModelSpec 列表(按 context_window 降序)
    """
    result: list[ModelSpec] = []
    for spec in MODEL_DATABASE.values():
        if provider is not None and spec.provider != provider:
            continue
        if supports_tools is not None and spec.supports_tools != supports_tools:
            continue
        if supports_vision is not None and spec.supports_vision != supports_vision:
            continue
        if spec.context_window < min_context:
            continue
        if max_cost is not None and spec.input_cost_per_1k >= max_cost:
            continue
        result.append(spec)

    result.sort(key=lambda s: s.context_window, reverse=True)
    return result


def calculate_max_tokens(
    model_id: str,
    input_tokens: int,
    requested_output: int,
    safety_margin: float = 0.1,
) -> int:
    """根据模型 context window 智能调整 max_tokens

    真实逻辑:
        1) 查 ModelSpec 拿 context_window
        2) 剩余空间 = context_window - input_tokens
        3) 应用 safety_margin(留 buffer)
        4) max_tokens = min(requested_output, 剩余空间, max_output)
        5) 如果 input 已超 context,返回 0 + warning
        6) 如果 requested > 剩余,返回剩余 + warning

    Args:
        model_id: 模型 id
        input_tokens: 当前 prompt 占用的 token 数
        requested_output: 业务层请求的 max_tokens
        safety_margin: 安全 buffer(0-1,默认 0.1 = 10%)

    Returns:
        调整后的 max_tokens(>= 0)
    """
    spec = get_model_spec(model_id)
    if spec is None:
        raise ValueError(f"Unknown model_id: {model_id!r}")

    if input_tokens < 0:
        raise ValueError(f"input_tokens must be >= 0, got {input_tokens}")
    if requested_output < 0:
        raise ValueError(f"requested_output must be >= 0, got {requested_output}")
    if not 0.0 <= safety_margin < 1.0:
        raise ValueError(f"safety_margin must be in [0, 1), got {safety_margin}")

    # input 已超 context
    if input_tokens >= spec.context_window:
        warnings.warn(
            f"input_tokens ({input_tokens}) >= context_window ({spec.context_window}) "
            f"for model {model_id!r}, returning max_tokens=0",
            UserWarning,
            stacklevel=2,
        )
        return 0

    # 剩余空间(应用 safety_margin)
    remaining_raw = spec.context_window - input_tokens
    remaining_with_margin = int(remaining_raw * (1.0 - safety_margin))

    # 取最小:requested / 剩余 / 模型硬上限
    effective = min(requested_output, remaining_with_margin, spec.max_output)
    effective = max(0, effective)

    # warning:被截断
    if requested_output > remaining_with_margin:
        warnings.warn(
            f"requested_output ({requested_output}) exceeds remaining context "
            f"({remaining_with_margin} after safety_margin={safety_margin}) "
            f"for model {model_id!r}, truncated to {effective}",
            UserWarning,
            stacklevel=2,
        )

    return effective


def estimate_cost(
    model_id: str,
    input_tokens: int,
    output_tokens: int,
) -> dict[str, float]:
    """估算成本

    Args:
        model_id: 模型 id
        input_tokens: 输入 token 数
        output_tokens: 期望输出 token 数

    Returns:
        {input_cost, output_cost, total_cost, currency}
        - input_cost = input_tokens / 1000 * input_cost_per_1k
        - output_cost = output_tokens / 1000 * output_cost_per_1k
        - currency = "USD"
    """
    spec = get_model_spec(model_id)
    if spec is None:
        raise ValueError(f"Unknown model_id: {model_id!r}")
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError(
            f"input_tokens/output_tokens must be >= 0, "
            f"got input={input_tokens}, output={output_tokens}"
        )

    input_cost = (input_tokens / 1000.0) * spec.input_cost_per_1k
    output_cost = (output_tokens / 1000.0) * spec.output_cost_per_1k
    total_cost = input_cost + output_cost

    return {
        "input_cost": round(input_cost, 8),
        "output_cost": round(output_cost, 8),
        "total_cost": round(total_cost, 8),
        "currency": "USD",
    }


def find_cheapest_for_context(
    required_context: int,
    max_cost_per_1k: float | None = None,
) -> list[ModelSpec]:
    """找能在给定 context 下工作的最便宜模型(按 input cost 升序)

    Args:
        required_context: 所需最小 context_window(>=)
        max_cost_per_1k: 可接受的 input cost per 1k 上限(<)

    Returns:
        按 input_cost_per_1k 升序排列的 ModelSpec 列表
        如果 required_context > 所有模型 max context,返回空列表
    """
    candidates: list[ModelSpec] = []
    for spec in MODEL_DATABASE.values():
        if spec.context_window < required_context:
            continue
        if max_cost_per_1k is not None and spec.input_cost_per_1k > max_cost_per_1k:
            continue
        candidates.append(spec)

    candidates.sort(key=lambda s: s.input_cost_per_1k)
    return candidates
