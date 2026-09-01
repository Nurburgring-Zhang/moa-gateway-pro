"""moa_gateway.providers -- Provider HTTP call implementations.
All providers implement the same unified interface.
"""

import logging

from .anthropic_provider import AnthropicProvider
from .audio_asr_provider import (
    ASRProvider,
    IFlytekASRProvider,
    OpenAIASRProvider,
    QwenASRProvider,
)
from .audio_edit_provider import (
    AudioEditProvider,
    ElevenLabsEditProvider,
    OpenSourceAudioEditProvider,
)
from .audio_tts_provider import (
    IFlytekTTSProvider,
    OpenAITTSProvider,
    QwenTTSProvider,
    TTSProvider,
)
from .base import ChatRequest, ChatResponse, Provider, ProviderError
from .cohere_provider import CohereProvider
from .embodied_provider import (
    EmbodiedProvider,
    ROS2BridgeProvider,
    VLMEmbodiedProvider,
)
from .gemini_provider import GeminiProvider
from .image_edit_provider import (
    DallEEditProvider,
    ImageEditProvider,
    SDInpaintProvider,
)
from .image_generation_provider import (
    CogViewImageProvider,
    DallECompatImageProvider,
    ImageGenerationProvider,
    WanxImageProvider,
)
from .mock_provider import MockProvider
from .music_generation_provider import (
    MiniMaxMusicProvider,
    MockMusicProvider,
    MusicGenerationProvider,
    TiangongMusicProvider,
)
from .openai_compat import OpenAICompatProvider
from .threed_generation_provider import (
    MeshyProvider,
    ThreeDGenerationProvider,
    Tripo3DProvider,
)
from .video_edit_provider import (
    KlingVideoEditProvider,
    RunwayVideoProvider,
    VideoEditProvider,
)
from .video_generation_provider import (
    KlingVideoProvider,
    VideoGenerationProvider,
)
from .world_model_provider import (
    CosmosWorldProvider,
    VLMWorldProvider,
    WorldModelProvider,
)

logger = logging.getLogger(__name__)

__all__ = [
    "Provider",
    "ChatRequest",
    "ChatResponse",
    "ProviderError",
    "OpenAICompatProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "CohereProvider",
    "MockProvider",
    "build_provider",
    "is_mock_key",
    "NO_AUTH_PROVIDERS",
    # Multimodal providers
    "ImageGenerationProvider",
    "DallECompatImageProvider",
    "WanxImageProvider",
    "CogViewImageProvider",
    "VideoGenerationProvider",
    "KlingVideoProvider",
    "VideoEditProvider",
    "RunwayVideoProvider",
    "KlingVideoEditProvider",
    "TTSProvider",
    "OpenAITTSProvider",
    "QwenTTSProvider",
    "IFlytekTTSProvider",
    "ASRProvider",
    "OpenAIASRProvider",
    "QwenASRProvider",
    "IFlytekASRProvider",
    "MusicGenerationProvider",
    "MiniMaxMusicProvider",
    "TiangongMusicProvider",
    "MockMusicProvider",
    "AudioEditProvider",
    "ElevenLabsEditProvider",
    "OpenSourceAudioEditProvider",
    "PROVIDER_MODALITY_MAP",
    "build_multimodal_provider",
    # 3D providers
    "ThreeDGenerationProvider",
    "Tripo3DProvider",
    "MeshyProvider",
    # Image edit providers
    "ImageEditProvider",
    "DallEEditProvider",
    "SDInpaintProvider",
    # World model providers
    "WorldModelProvider",
    "VLMWorldProvider",
    "CosmosWorldProvider",
    # Embodied AI providers
    "EmbodiedProvider",
    "VLMEmbodiedProvider",
    "ROS2BridgeProvider",
]


# Provider id -> Provider class
_REGISTRY: dict = {}


def register(provider_id: str, cls):
    _REGISTRY[provider_id] = cls


# Platforms that do not require an API key (free / no-auth)
NO_AUTH_PROVIDERS: set[str] = {"ovhcloud", "llm7"}



def is_mock_key(api_key: str) -> bool:
    """Check if API key is a mock placeholder (no real key set)."""
    if not api_key:
        return True
    k = api_key.strip()
    if not k:
        return True
    if k.startswith("your-") or k.startswith("sk-your-"):
        return True
    return bool(k in {"mock", "mock-key"})


def build_provider(provider_id: str, **kwargs) -> Provider:
    """Build a Provider instance by provider id.

    D6 explicit-mock policy: if api_key is mock/empty/your-xxx the behavior
    is governed by settings.mock.mode:
      - "explicit" (default): fall back to MockProvider, but every response
        is labeled (provider="mock", X-MOA-Mock header at route level).
      - "disabled": raise ProviderError so the caller returns a clear 503 —
        no simulated output ever leaves the gateway.
    Platforms supporting no-auth (e.g. ovhcloud/llm7) or no_auth_required=True
    always use the real provider.
    """
    api_key = kwargs.get("api_key", "")
    no_auth_required = kwargs.pop("no_auth_required", False)
    if is_mock_key(api_key):
        # Platform supports no-auth or explicit flag -> use real provider
        if provider_id in NO_AUTH_PROVIDERS or no_auth_required:
            logger.info(
                "[provider] %s supports no-auth, using real Provider (empty key)",
                provider_id,
            )
            kwargs["api_key"] = ""
        else:
            mock_mode = _get_mock_mode()
            if mock_mode == "disabled":
                raise ProviderError(
                    f"provider '{provider_id}' (model={kwargs.get('model', '?')}) has no real "
                    "API key and mock mode is disabled; configure a real key or set "
                    "mock.mode=explicit",
                    status=503,
                )
            logger.warning(
                "[provider] WARNING: %s (model=%s) has NO real API key — using MockProvider. "
                "Responses will be synthetic. If this is unintended, check your env var "
                "configuration (api_key_env setting).",
                provider_id,
                kwargs.get("model", "?"),
            )
            return MockProvider(
                model=kwargs.get("model", "mock"), timeout=kwargs.get("timeout", 30)
            )
    if provider_id not in _REGISTRY:
        return OpenAICompatProvider(**kwargs)
    return _REGISTRY[provider_id](**kwargs)  # type: ignore[no-any-return]


def _get_mock_mode() -> str:
    """Read settings.mock.mode lazily (import here to avoid circular imports)."""
    try:
        from ..config import get_settings

        return get_settings().mock.mode
    except Exception:
        return "explicit"


# Auto-register built-in providers -- OpenAI-compatible (international)
register("openai", OpenAICompatProvider)       # openai_compat
register("deepseek", OpenAICompatProvider)     # openai_compat
register("mistral", OpenAICompatProvider)      # openai_compat
register("groq", OpenAICompatProvider)         # openai_compat
register("openrouter", OpenAICompatProvider)   # openai_compat
register("cerebras", OpenAICompatProvider)     # openai_compat
register("sambanova", OpenAICompatProvider)    # openai_compat
register("github_models", OpenAICompatProvider)  # openai_compat
register("huggingface", OpenAICompatProvider)  # openai_compat
register("siliconflow", OpenAICompatProvider)  # openai_compat
register("ovhcloud", OpenAICompatProvider)     # openai_compat (no-auth)
register("llm7", OpenAICompatProvider)         # openai_compat (no-auth)
register("chutes", OpenAICompatProvider)       # openai_compat
register("nvidia_nim", OpenAICompatProvider)   # openai_compat
register("cloudflare_ai", OpenAICompatProvider)  # openai_compat
# Domestic platforms (OpenAI-compatible)
register("zhipu", OpenAICompatProvider)        # openai_compat
register("moonshot", OpenAICompatProvider)     # openai_compat
register("qwen", OpenAICompatProvider)         # openai_compat
register("doubao", OpenAICompatProvider)       # openai_compat
register("lingyiwanwu", OpenAICompatProvider)  # openai_compat
register("lingyi", OpenAICompatProvider)       # openai_compat
register("baichuan", OpenAICompatProvider)     # openai_compat
register("minimax", OpenAICompatProvider)      # openai_compat
register("stepfun", OpenAICompatProvider)      # openai_compat
register("baai", OpenAICompatProvider)         # openai_compat
register("modelscope", OpenAICompatProvider)   # openai_compat
register("tiangong", OpenAICompatProvider)     # openai_compat
register("baidu_ernie", OpenAICompatProvider)  # openai_compat
register("hunyuan", OpenAICompatProvider)      # openai_compat
register("hw_pangu", OpenAICompatProvider)     # openai_compat
register("iflytek", OpenAICompatProvider)      # openai_compat
register("sensetime", OpenAICompatProvider)    # openai_compat
register("china_mobile_wuyan", OpenAICompatProvider)      # openai_compat
register("china_telecom_xingchen", OpenAICompatProvider)  # openai_compat
register("china_unicom_yuanjing", OpenAICompatProvider)   # openai_compat
# Multimodal specialist platforms
register("kling", OpenAICompatProvider)        # multimodal (video)
register("cogview", OpenAICompatProvider)      # multimodal (image)
register("wanx", OpenAICompatProvider)         # multimodal (image)
register("minimax_music", OpenAICompatProvider)   # multimodal (music)
register("tiangong_music", OpenAICompatProvider)  # multimodal (music)

# Special-format platforms
register("cohere", CohereProvider)
register("anthropic", AnthropicProvider)
register("gemini", GeminiProvider)
register("mock", MockProvider)


# =====================================================================
# Multimodal Provider Registry
# =====================================================================

# Modality -> list of (platform_id, provider_class) tuples
PROVIDER_MODALITY_MAP: dict[str, list[tuple[str, type]]] = {
    "image": [
        ("cogview", CogViewImageProvider),
        ("wanx", WanxImageProvider),
        ("zhipu", CogViewImageProvider),  # Zhipu also supports CogView
        ("openai", DallECompatImageProvider),
    ],
    "video": [
        ("kling", KlingVideoProvider),
    ],
    "video_edit": [
        ("runway", RunwayVideoProvider),
        ("kling", KlingVideoEditProvider),
    ],
    "audio_tts": [
        ("minimax", OpenAITTSProvider),
        ("qwen", QwenTTSProvider),
        ("iflytek", IFlytekTTSProvider),
        ("doubao", OpenAITTSProvider),
    ],
    "audio_asr": [
        ("qwen", QwenASRProvider),
        ("iflytek", IFlytekASRProvider),
    ],
    "music": [
        ("minimax_music", MiniMaxMusicProvider),
        ("tiangong_music", TiangongMusicProvider),
    ],
    "image_edit": [
        ("openai", DallEEditProvider),
        ("sd", SDInpaintProvider),
    ],
    "3d": [
        ("tripo3d", Tripo3DProvider),
        ("meshy", MeshyProvider),
    ],
    "audio_edit": [
        ("elevenlabs", ElevenLabsEditProvider),
    ],
    "world_model": [
        ("vlm", VLMWorldProvider),
        ("cosmos", CosmosWorldProvider),
    ],
    "embodied": [
        ("vlm", VLMEmbodiedProvider),
        ("ros2", ROS2BridgeProvider),
    ],
}


def build_multimodal_provider(
    modality: str,
    platform_id: str,
    api_key: str = "",
    api_base: str = "",
) -> object | None:
    """Build a multimodal provider instance for the given modality and platform.

    Args:
        modality: One of 'image', 'video', 'audio_tts', 'audio_asr', 'music'.
        platform_id: The platform ID (e.g. 'cogview', 'kling', 'minimax_music').
        api_key: API key for the platform.
        api_base: Base URL for the platform API.

    Returns:
        Provider instance, or None if no provider is registered for the
        given modality + platform combination.
    """
    from ..discovery.free_model_catalog import get_platform

    entries = PROVIDER_MODALITY_MAP.get(modality, [])
    for pid, cls in entries:
        if pid == platform_id:
            if not api_base:
                platform = get_platform(platform_id)
                if platform:
                    api_base = platform.base_url
            return cls(api_base=api_base, api_key=api_key)  # type: ignore[no-any-return]
    return None
