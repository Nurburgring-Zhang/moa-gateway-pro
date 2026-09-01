"""Token-efficiency toolkit (M6).

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
The package brings the OpenClacky prompt-caching discipline to the gateway:

- ``markers``        — Anthropic-style double ``cache_control={"type":"ephemeral"}``
                       markers on the last two messages;
- ``system_prompt``  — immutable (frozen) system prompt + ``system_injected``
                       side-channel messages;
- ``compressor``     — Insert-then-Compress session compression with real
                       extractive summarization and chunk-MD archiving;
- ``idle_scheduler`` — 266 s idle compression timers (< 5-min cache TTL);
- ``summarizer``     — deterministic extractive summarizer built on the
                       gateway's distillation primitive;
- ``tokens``         — token estimation heuristics (port);
- ``metrics``        — cache hit-rate counters.

All pieces are opt-in: importing this package changes no default gateway
behavior.
"""

from .tokens import (
    MESSAGE_OVERHEAD_TOKENS,
    estimate_content_tokens,
    estimate_message_tokens,
    estimate_messages_tokens,
)
from .markers import (
    EPHEMERAL_CACHE_CONTROL,
    MARKER_COUNT,
    add_cache_control_to_message,
    apply_cache_markers,
    is_compression_instruction,
    strip_cache_markers,
)
from .system_prompt import (
    INTERNAL_MESSAGE_FIELDS,
    FrozenSystemPrompt,
    SystemPromptMutationError,
    SystemPromptRegistry,
    get_system_prompt_registry,
    side_channel_message,
    strip_internal_fields,
)
from .summarizer import (
    TOPIC_MAX_ITEMS,
    KeyInformation,
    SummaryResult,
    extract_key_information,
    extract_topics,
    summarize_messages,
)
from .compressor import (
    COMPRESSION_PROMPT,
    CompressionResult,
    SessionCompressor,
    build_compression_message,
    calculate_target_recent_count,
    clean_rebuilt_history,
    compression_needed,
    get_recent_messages_with_tool_pairs,
    idle_compression_needed,
    parse_compressed_result,
    parse_topics,
    rebuild_with_compression,
    resolve_archive_dir,
    truncate_tool_result,
)
from .idle_scheduler import (
    IDLE_DELAY_SECONDS,
    IdleCompressionScheduler,
    get_idle_scheduler,
    make_idle_compress_task,
)
from .metrics import EfficiencyMetrics, get_metrics

__all__ = [
    # tokens
    "MESSAGE_OVERHEAD_TOKENS",
    "estimate_content_tokens",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    # markers
    "EPHEMERAL_CACHE_CONTROL",
    "MARKER_COUNT",
    "add_cache_control_to_message",
    "apply_cache_markers",
    "is_compression_instruction",
    "strip_cache_markers",
    # system prompt
    "INTERNAL_MESSAGE_FIELDS",
    "FrozenSystemPrompt",
    "SystemPromptMutationError",
    "SystemPromptRegistry",
    "get_system_prompt_registry",
    "side_channel_message",
    "strip_internal_fields",
    # summarizer
    "TOPIC_MAX_ITEMS",
    "KeyInformation",
    "SummaryResult",
    "extract_key_information",
    "extract_topics",
    "summarize_messages",
    # compressor
    "COMPRESSION_PROMPT",
    "CompressionResult",
    "SessionCompressor",
    "build_compression_message",
    "calculate_target_recent_count",
    "clean_rebuilt_history",
    "compression_needed",
    "get_recent_messages_with_tool_pairs",
    "idle_compression_needed",
    "parse_compressed_result",
    "parse_topics",
    "rebuild_with_compression",
    "resolve_archive_dir",
    "truncate_tool_result",
    # idle scheduler
    "IDLE_DELAY_SECONDS",
    "IdleCompressionScheduler",
    "get_idle_scheduler",
    "make_idle_compress_task",
    # metrics
    "EfficiencyMetrics",
    "get_metrics",
]
