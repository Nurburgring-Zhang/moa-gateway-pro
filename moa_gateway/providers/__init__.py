"""moa_gateway.providers — 各模型提供商的 HTTP 调用实现
抽象出统一接口,所有提供商实现都遵循相同协议。
"""
import os
import logging
from .base import Provider, ChatRequest, ChatResponse, ProviderError
from .openai_compat import OpenAICompatProvider
from .anthropic_provider import AnthropicProvider
from .mock_provider import MockProvider

logger = logging.getLogger(__name__)

__all__ = [
    "Provider", "ChatRequest", "ChatResponse", "ProviderError",
    "OpenAICompatProvider", "AnthropicProvider", "MockProvider",
    "build_provider", "is_mock_key",
]


# Provider id -> Provider class
_REGISTRY: dict = {}


def register(provider_id: str, cls):
    _REGISTRY[provider_id] = cls


def is_mock_key(api_key: str) -> bool:
    """判断 API key 是否是 mock 占位符(没设真 key)"""
    if not api_key:
        return True
    k = api_key.strip()
    if not k:
        return True
    if k.startswith("your-") or k.startswith("sk-your-"):
        return True
    if k == "mock" or k == "mock-key":
        return True
    return False


def build_provider(provider_id: str, **kwargs) -> Provider:
    """根据 provider id 构建一个 Provider 实例
    如果 api_key 是 mock/空/your-xxx,自动用 MockProvider(让没 key 也能演示)
    """
    api_key = kwargs.get("api_key", "")
    if is_mock_key(api_key):
        logger.info(
            "[provider] %s 的 api_key 是 mock/空,自动使用 MockProvider(无网络调用,返回智能模拟回答)",
            kwargs.get("model", "?"),
        )
        return MockProvider(model=kwargs.get("model", "mock"), timeout=kwargs.get("timeout", 30))
    if provider_id not in _REGISTRY:
        return OpenAICompatProvider(**kwargs)
    return _REGISTRY[provider_id](**kwargs)


# 自动注册内置 provider
register("openai", OpenAICompatProvider)
register("deepseek", OpenAICompatProvider)
register("zhipu", OpenAICompatProvider)
register("moonshot", OpenAICompatProvider)
register("qwen", OpenAICompatProvider)
register("doubao", OpenAICompatProvider)
register("lingyi", OpenAICompatProvider)
register("baichuan", OpenAICompatProvider)
register("mistral", OpenAICompatProvider)
register("cohere", OpenAICompatProvider)
register("groq", OpenAICompatProvider)
register("openrouter", OpenAICompatProvider)
register("minimax", OpenAICompatProvider)
register("anthropic", AnthropicProvider)
register("mock", MockProvider)
