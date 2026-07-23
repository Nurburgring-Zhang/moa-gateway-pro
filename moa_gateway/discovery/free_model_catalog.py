"""moa_gateway.discovery.free_model_catalog -- Static catalog of free LLM platforms.

Hardcoded metadata for 40+ platforms that offer free LLM API access.
Each platform entry includes auth method, API format, free-tier type, rate limits,
region (overseas/domestic), and supported modalities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

AuthType = Literal["bearer", "none", "query_param", "optional_token"]
ApiFormat = Literal["openai", "google_gemini", "cohere", "anthropic", "gemini", "custom"]
FreeTierType = Literal["permanent", "signup_credits", "anonymous", "freemium", "free", "trial"]
Region = Literal["overseas", "domestic"]


@dataclass(frozen=True)
class PlatformInfo:
    """Metadata describing a free LLM API platform."""

    platform_id: str
    platform_name: str
    base_url: str
    api_format: ApiFormat
    auth_type: AuthType
    signup_url: str
    free_tier_type: FreeTierType
    models_endpoint: str
    rate_limit_info: str
    special_headers: dict[str, str] = field(default_factory=dict)
    credit_card_required: bool = False
    region: str = "overseas"
    modalities: list[str] = field(default_factory=lambda: ["text"])


_PLATFORMS: list[PlatformInfo] = [
    # ==================== Overseas platforms (14) ====================
    PlatformInfo(
        platform_id="gemini", platform_name="Google Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_format="google_gemini", auth_type="query_param",
        signup_url="https://aistudio.google.com/apikey",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="15 RPM, 1500 RPD, 1M TPM",
        region="overseas", modalities=["text", "image", "audio"],
    ),
    PlatformInfo(
        platform_id="groq", platform_name="Groq",
        base_url="https://api.groq.com/openai/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://console.groq.com/keys",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="30 RPM, 14400 RPD",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="cerebras", platform_name="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://cloud.cerebras.ai",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="30 RPM, 256K TPM",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="sambanova", platform_name="SambaNova",
        base_url="https://api.sambanova.ai/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://cloud.sambanova.ai",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="10 RPM",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="openrouter", platform_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://openrouter.ai/keys",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="20 RPM, 200 RPD (free models)",
        special_headers={"HTTP-Referer": "https://moa-gateway.local", "X-Title": "MOA Gateway"},
        region="overseas",
    ),
    PlatformInfo(
        platform_id="mistral", platform_name="Mistral AI",
        base_url="https://api.mistral.ai/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://console.mistral.ai",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="1 RPS, 500K TPM",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="cohere", platform_name="Cohere",
        base_url="https://api.cohere.ai/v2",
        api_format="cohere", auth_type="bearer",
        signup_url="https://dashboard.cohere.com",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="20 RPM trial, 1000 calls/month",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="nvidia_nim", platform_name="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://build.nvidia.com",
        free_tier_type="signup_credits", models_endpoint="/models",
        rate_limit_info="40 RPM, 1000 calls/month",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="cloudflare_ai", platform_name="Cloudflare AI",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai",
        api_format="openai", auth_type="bearer",
        signup_url="https://dash.cloudflare.com",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="50 Neurons/day",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="github_models", platform_name="GitHub Models",
        base_url="https://models.inference.ai.azure.com",
        api_format="openai", auth_type="bearer",
        signup_url="https://github.com/settings/tokens",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="150 RPM low tier, 4000 RPM high",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="huggingface", platform_name="Hugging Face",
        base_url="https://router.huggingface.co/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://huggingface.co/settings/tokens",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="1000 requests/day (free tier)",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="ovhcloud", platform_name="OVHcloud AI",
        base_url="https://ai-endpoints.ovhcloud.com/v1",
        api_format="openai", auth_type="none",
        signup_url="https://endpoints.ai.cloud.ovh.net",
        free_tier_type="anonymous", models_endpoint="/models",
        rate_limit_info="No auth required, rate-limited",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="llm7", platform_name="LLM7",
        base_url="https://api.llm7.io/v1",
        api_format="openai", auth_type="optional_token",
        signup_url="https://llm7.io",
        free_tier_type="permanent", models_endpoint="/models",
        rate_limit_info="Unlimited (rate-limited per IP)",
        region="overseas",
    ),
    PlatformInfo(
        platform_id="chutes", platform_name="Chutes AI",
        base_url="https://api.chutes.ai/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://chutes.ai",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="100 RPM",
        region="overseas",
    ),
    # ==================== Domestic: AI Model Companies ====================
    PlatformInfo(
        platform_id="zhipu", platform_name="Zhipu AI (GLM)",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_format="openai", auth_type="bearer",
        signup_url="https://open.bigmodel.cn",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="100 RPM free tier, GLM-4-Flash free",
        region="domestic", modalities=["text", "image"],
    ),
    PlatformInfo(
        platform_id="deepseek", platform_name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://platform.deepseek.com",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="60 RPM, low-cost / signup credits",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="siliconflow", platform_name="SiliconFlow",
        base_url="https://api.siliconflow.cn/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://cloud.siliconflow.cn",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="200 RPM, 20+ free models",
        region="domestic", modalities=["text", "image", "audio"],
    ),
    PlatformInfo(
        platform_id="moonshot", platform_name="Moonshot (Kimi)",
        base_url="https://api.moonshot.cn/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://platform.moonshot.cn",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Kimi free quota, 60 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="baichuan", platform_name="Baichuan",
        base_url="https://api.baichuan-ai.com/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://platform.baichuan-ai.com",
        free_tier_type="trial", models_endpoint="/models",
        rate_limit_info="Signup credits, 60 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="lingyiwanwu", platform_name="Lingyi Wanwu (Yi)",
        base_url="https://api.lingyiwanwu.com/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://platform.lingyiwanwu.com",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="Yi-Lightning free, 60 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="stepfun", platform_name="StepFun",
        base_url="https://api.stepfun.com/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://platform.stepfun.com",
        free_tier_type="trial", models_endpoint="/models",
        rate_limit_info="Signup credits, 60 RPM",
        region="domestic", modalities=["text", "image"],
    ),
    PlatformInfo(
        platform_id="minimax", platform_name="MiniMax",
        base_url="https://api.minimax.chat/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://platform.minimaxi.com",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="abab free quota, 60 RPM",
        region="domestic", modalities=["text", "audio"],
    ),
    PlatformInfo(
        platform_id="baai", platform_name="BAAI (MiniCPM)",
        base_url="https://api.baaiai.com/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://modelscope.cn/organization/baai",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="MiniCPM free, 60 RPM",
        region="domestic", modalities=["text", "vision"],
    ),
    PlatformInfo(
        platform_id="qwen", platform_name="Qwen (Tongyi Qianwen)",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://dashscope.console.aliyun.com",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Qwen-Turbo free, 60 RPM",
        region="domestic", modalities=["text", "image", "audio"],
    ),
    # ==================== Domestic: IT Giants / Cloud ====================
    PlatformInfo(
        platform_id="baidu_ernie", platform_name="Baidu ERNIE",
        base_url="https://qianfan.baidubce.com/v2",
        api_format="custom", auth_type="bearer",
        signup_url="https://console.bce.baidu.com/qianfan",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="ERNIE-Speed free, 60 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="hunyuan", platform_name="Tencent Hunyuan",
        base_url="https://hunyuan.tencentcloudapi.com/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://console.cloud.tencent.com/hunyuan",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="Hunyuan-Lite free, 60 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="hw_pangu", platform_name="Huawei Pangu",
        base_url="https://devstar.huaweicloud.com/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://www.huaweicloud.com/product/pangu.html",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Partial free, 30 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="doubao", platform_name="ByteDance Doubao",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_format="openai", auth_type="bearer",
        signup_url="https://console.volcengine.com/ark",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="Doubao-Lite free, 60 RPM",
        region="domestic", modalities=["text", "audio"],
    ),
    PlatformInfo(
        platform_id="iflytek", platform_name="iFlytek Spark",
        base_url="https://spark-api-open.xf-yun.com/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://xinghuo.xfyun.cn",
        free_tier_type="free", models_endpoint="/models",
        rate_limit_info="Spark Lite free, 60 RPM",
        region="domestic", modalities=["text", "audio"],
    ),
    PlatformInfo(
        platform_id="sensetime", platform_name="SenseTime SenseNova",
        base_url="https://api.sensenova.cn/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://console.sensenova.cn",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Partial free, 30 RPM",
        region="domestic", modalities=["text", "image"],
    ),
    PlatformInfo(
        platform_id="tiangong", platform_name="Kunlun Tiangong",
        base_url="https://api.tiangong.cn/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://platform.tiangong.cn",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Partial free, 30 RPM",
        region="domestic",
    ),
    # ==================== Domestic: API Aggregators ====================
    PlatformInfo(
        platform_id="modelscope", platform_name="ModelScope",
        base_url="https://api-inference.modelscope.cn/v1",
        api_format="openai", auth_type="bearer",
        signup_url="https://modelscope.cn",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Partial free, 60 RPM",
        region="domestic", modalities=["text", "image"],
    ),
    # ==================== Domestic: Telecom Operators ====================
    PlatformInfo(
        platform_id="china_mobile_wuyan", platform_name="China Mobile Wuyan",
        base_url="https://wuyan.ai/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://wuyan.ai",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Jiutian model, 30 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="china_telecom_xingchen", platform_name="China Telecom Xingchen",
        base_url="https://xingchen.eastchina.cn/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://xingchen.eastchina.cn",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Xingchen model, 30 RPM",
        region="domestic",
    ),
    PlatformInfo(
        platform_id="china_unicom_yuanjing", platform_name="China Unicom Yuanjing",
        base_url="https://api.yuanjing.com/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://api.yuanjing.com",
        free_tier_type="freemium", models_endpoint="/models",
        rate_limit_info="Yuanjing model, 30 RPM",
        region="domestic",
    ),
    # ==================== Domestic: Multimodal Specialists ====================
    PlatformInfo(
        platform_id="kling", platform_name="Kuaishou Kling",
        base_url="https://api.klingai.com/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://klingai.kuaishou.com",
        free_tier_type="trial", models_endpoint="/models",
        rate_limit_info="Video generation beta, 10 RPM",
        region="domestic", modalities=["video"],
    ),
    PlatformInfo(
        platform_id="cogview", platform_name="Zhipu CogView",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        api_format="custom", auth_type="bearer",
        signup_url="https://open.bigmodel.cn",
        free_tier_type="freemium", models_endpoint="",
        rate_limit_info="CogView text-to-image, 10 RPM",
        region="domestic", modalities=["image"],
    ),
    PlatformInfo(
        platform_id="wanx", platform_name="Tongyi Wanxiang",
        base_url="https://dashscope.aliyuncs.com/api/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://dashscope.console.aliyun.com",
        free_tier_type="freemium", models_endpoint="",
        rate_limit_info="Wanx text-to-image, 10 RPM",
        region="domestic", modalities=["image"],
    ),
    PlatformInfo(
        platform_id="minimax_music", platform_name="MiniMax Music",
        base_url="https://api.minimax.chat/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://platform.minimaxi.com",
        free_tier_type="trial", models_endpoint="",
        rate_limit_info="Music generation, 5 RPM",
        region="domestic", modalities=["music"],
    ),
    PlatformInfo(
        platform_id="tiangong_music", platform_name="Tiangong SkyMusic",
        base_url="https://api.tiangong.cn/v1",
        api_format="custom", auth_type="bearer",
        signup_url="https://platform.tiangong.cn",
        free_tier_type="trial", models_endpoint="",
        rate_limit_info="SkyMusic generation, 5 RPM",
        region="domestic", modalities=["music"],
    ),
]


def get_all_platforms() -> list[PlatformInfo]:
    """Return all registered platforms."""
    return list(_PLATFORMS)


def get_platform(platform_id: str) -> PlatformInfo | None:
    """Get a single platform by its ID. Returns None if not found."""
    for p in _PLATFORMS:
        if p.platform_id == platform_id:
            return p
    return None


def get_platforms_by_auth(auth_type: AuthType) -> list[PlatformInfo]:
    """Filter platforms by authentication type."""
    return [p for p in _PLATFORMS if p.auth_type == auth_type]


def get_platforms_by_region(region: str) -> list[PlatformInfo]:
    """Filter platforms by region ('overseas' or 'domestic')."""
    return [p for p in _PLATFORMS if p.region == region]


def get_platforms_by_modality(modality: str) -> list[PlatformInfo]:
    """Filter platforms by supported modality (e.g. 'text', 'image', 'audio', 'video', 'music')."""
    return [p for p in _PLATFORMS if modality in p.modalities]


def get_api_key_env(platform_id: str) -> str:
    """Derive the conventional environment variable name for a platform API key.

    e.g. "groq" -> "GROQ_API_KEY", "nvidia_nim" -> "NVIDIA_NIM_API_KEY"
    """
    return f"{platform_id.upper().replace('-', '_')}_API_KEY"
