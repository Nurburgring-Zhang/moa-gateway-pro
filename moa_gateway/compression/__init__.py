"""Stacked prompt-compression pipeline (RTK + Caveman + lite + ultra).

Real implementation ported from OmniRoute
(https://github.com/diegosouzapw/OmniRoute, MIT License) —
``open-sse/services/compression/*``. Deterministic, dependency-free
engines; every mode (off / lite / standard / aggressive / ultra / rtk /
stacked) performs actual text reduction with a fidelity gate that reverts
any rewrite dropping protected tokens, numbers, JSON keys or diff hunks.

Gateway policy (mirrors OmniRoute hard rule #20): ``apply_to_chat`` defaults
to False — this package never mutates live chat traffic unless an operator
explicitly opts in. The HTTP surface (/v1/compression/*) is the only entry
point.
"""

from __future__ import annotations

from .caveman import (
    CavemanConfig,
    CavemanRule,
    apply_rules_to_text,
    caveman_compress_messages,
    caveman_compress_text,
    cleanup_artifacts,
    get_rules_for_context,
    load_rule_packs,
)
from .engine import (
    DEFAULT_STACKED_PIPELINE,
    MODES,
    CompressionEngine,
    CompressionOutcome,
    get_engine,
    reset_engine,
)
from .fidelity import (
    FidelityGateConfig,
    FidelityResult,
    check_fidelity,
    fidelity_score,
)
from .lite import apply_lite_compression, model_supports_vision
from .preservation import (
    CRITICAL_KINDS,
    PreservedBlock,
    extract_preserved_blocks,
    restore_preserved_blocks,
)
from .rtk import (
    RtkFilter,
    apply_line_filter,
    deduplicate_repeated_lines,
    detect_filter,
    load_filters,
    process_rtk_text,
    rtk_compress_messages,
    smart_truncate,
)
from .stats import (
    CompressionStats,
    CompressionStatsStore,
    create_compression_stats,
    estimate_tokens,
    get_stats_store,
)
from .ultra import prune_by_score, prune_prose_only, score_token, ultra_compress_messages

__all__ = [
    "MODES",
    "DEFAULT_STACKED_PIPELINE",
    "CRITICAL_KINDS",
    "CavemanConfig",
    "CavemanRule",
    "CompressionEngine",
    "CompressionOutcome",
    "CompressionStats",
    "CompressionStatsStore",
    "FidelityGateConfig",
    "FidelityResult",
    "PreservedBlock",
    "RtkFilter",
    "apply_lite_compression",
    "apply_line_filter",
    "apply_rules_to_text",
    "caveman_compress_messages",
    "caveman_compress_text",
    "check_fidelity",
    "cleanup_artifacts",
    "create_compression_stats",
    "deduplicate_repeated_lines",
    "detect_filter",
    "estimate_tokens",
    "extract_preserved_blocks",
    "fidelity_score",
    "get_engine",
    "get_rules_for_context",
    "get_stats_store",
    "load_filters",
    "load_rule_packs",
    "model_supports_vision",
    "process_rtk_text",
    "prune_by_score",
    "prune_prose_only",
    "reset_engine",
    "restore_preserved_blocks",
    "rtk_compress_messages",
    "score_token",
    "smart_truncate",
    "ultra_compress_messages",
]
