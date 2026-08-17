"""moa_gateway.discovery — Free model discovery system.

Auto-discover, configure, and schedule free LLM endpoints across 17+ platforms.
Supports OpenAI-compatible, Google Gemini, and Cohere API formats.
"""

from .auto_configurator import AutoConfigurator
from .discovery_engine import DiscoveredModel, FreeModelDiscoveryEngine
from .free_model_catalog import (
    PlatformInfo,
    get_all_platforms,
    get_api_key_env,
    get_platform,
    get_platforms_by_auth,
)
from .scheduler import DiscoveryScheduler

__all__ = [
    "PlatformInfo",
    "get_all_platforms",
    "get_platform",
    "get_platforms_by_auth",
    "get_api_key_env",
    "DiscoveredModel",
    "FreeModelDiscoveryEngine",
    "AutoConfigurator",
    "DiscoveryScheduler",
]
