"""Stacked compression orchestrator.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/strategySelector.ts`` + ``engines/registry.ts``:

Modes (all real, all deterministic):

===========  =============================================================
``off``      pass-through
``lite``     whitespace / system-dedup / tool-truncate / dup-remove /
             image-placeholder (non-vision models only)
``standard`` Caveman engine at ``lite`` intensity (filler + dedup rules)
``aggressive`` RTK tool-result filters + lite passes + Caveman ``full``
             on every message except the live user turn
``ultra``    information-density token pruning (prose only)
``rtk``      CLI-output filters (56 bundled JSON definitions)
``stacked``  two-stage pipeline, OmniRoute default: ``[rtk standard,
             caveman full]``
===========  =============================================================

Gateway invariants enforced here:

* ``preserve_cache_control`` — messages carrying a ``cache_control`` marker
  are never rewritten (provider prompt-cache prefixes stay valid).
* ``fidelity_gate`` — every rewritten message is checked against the input
  (protected tokens / numbers / JSON keys / diff hunks); on failure the
  original message is restored.
* ``hard_budget_chars`` — if the result still exceeds the budget, the oldest
  tool/assistant messages are truncated until it fits.
* ``max_input_chars`` — oversized inputs bypass compression entirely.
"""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import CompressionConfig, get_settings
from .aggressive import aggressive_compress_messages
from .caveman import CavemanConfig, caveman_compress_messages
from .fidelity import FidelityGateConfig, check_fidelity, fidelity_score
from .lite import apply_lite_compression, model_supports_vision
from .rtk import rtk_compress_messages
from .stats import (
    CompressionStats,
    body_text_length,
    create_compression_stats,
    extract_message_text,
    get_stats_store,
)
from .ultra import ultra_compress_messages

logger = logging.getLogger(__name__)

MODES = ("off", "lite", "standard", "aggressive", "ultra", "rtk", "stacked")

#: OmniRoute default stacked pipeline (resolveStackedPipeline fallback).
DEFAULT_STACKED_PIPELINE: tuple[dict[str, str], ...] = (
    {"engine": "rtk", "intensity": "standard"},
    {"engine": "caveman", "intensity": "full"},
)

_HARD_BUDGET_MARKER = "\n[compression:hard-budget truncated]"


@dataclass
class CompressionOutcome:
    body: dict[str, Any]
    compressed: bool
    mode: str
    stats: CompressionStats | None = None
    techniques_used: list[str] = field(default_factory=list)
    rules_applied: list[str] = field(default_factory=list)
    fidelity_passed: bool = True
    fidelity_reverted: int = 0
    fidelity_score: float = 1.0
    engines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "compressed": self.compressed,
            "mode": self.mode,
            "engines": list(self.engines),
            "techniques_used": list(self.techniques_used),
            "rules_applied": list(self.rules_applied),
            "fidelity": {
                "passed": self.fidelity_passed,
                "reverted_messages": self.fidelity_reverted,
                "score": self.fidelity_score,
            },
            "stats": self.stats.to_dict() if self.stats else None,
        }


def _has_cache_control(message: dict[str, Any]) -> bool:
    if "cache_control" in message:
        return True
    content = message.get("content")
    if isinstance(content, list):
        return any(
            isinstance(part, dict) and "cache_control" in part for part in content
        )
    return False


def _total_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(extract_message_text(m)) for m in messages)


class CompressionEngine:
    """Deterministic multi-engine chat-body / text compressor."""

    def __init__(self, config: CompressionConfig | None = None) -> None:
        self.config = config or get_settings().compression
        self._lock = threading.Lock()

    # ---------- public API ----------

    def compress_body(
        self,
        body: dict[str, Any],
        mode: str | None = None,
        model: str | None = None,
        stacked_pipeline: list[dict[str, str]] | None = None,
    ) -> CompressionOutcome:
        """Compress a chat-completions style body (messages list)."""
        start = time.perf_counter()
        effective_mode = mode or self.config.default_mode or "off"
        if effective_mode not in MODES:
            raise ValueError(f"unknown compression mode: {effective_mode!r}")

        messages = body.get("messages")
        if (
            not self.config.enabled
            or effective_mode == "off"
            or not isinstance(messages, list)
            or not messages
        ):
            return CompressionOutcome(body=body, compressed=False, mode=effective_mode)

        original_chars = _total_chars(messages)
        if original_chars > self.config.max_input_chars:
            logger.info(
                "compression skipped: input %d chars exceeds max_input_chars %d",
                original_chars,
                self.config.max_input_chars,
            )
            return CompressionOutcome(body=body, compressed=False, mode=effective_mode)

        # cache_control protection: split protected vs compressible messages.
        protected_index: set[int] = set()
        if self.config.preserve_cache_control:
            protected_index = {
                i for i, msg in enumerate(messages) if _has_cache_control(msg)
            }

        work_messages = [
            msg for i, msg in enumerate(messages) if i not in protected_index
        ]
        original_by_index = {
            i: copy.deepcopy(msg) for i, msg in enumerate(work_messages)
        }

        compressed_messages, techniques, rules, engines = self._run_mode(
            effective_mode, work_messages, model, stacked_pipeline
        )

        # Re-insert protected messages at their original positions.
        merged: list[dict[str, Any]] = []
        work_iter = iter(compressed_messages)
        for i in range(len(messages)):
            if i in protected_index:
                merged.append(messages[i])
            else:
                merged.append(next(work_iter))

        # Fidelity gate: per-message invariant check, revert on failure.
        reverted = 0
        if self.config.fidelity_gate:
            gate_cfg = FidelityGateConfig(enabled=True)
            checked: list[dict[str, Any]] = []
            for i, msg in enumerate(merged):
                original = None
                if i in protected_index:
                    checked.append(msg)
                    continue
                # Map merged index back to the working-list original.
                work_index = i - sum(1 for p in protected_index if p < i)
                original = original_by_index.get(work_index)
                if original is None:
                    checked.append(msg)
                    continue
                original_text = extract_message_text(original)
                new_text = extract_message_text(msg)
                if new_text != original_text and original_text:
                    verdict = check_fidelity(original_text, new_text, gate_cfg)
                    if not verdict.passed:
                        reverted += 1
                        logger.info(
                            "fidelity gate reverted message %d (%s): %s",
                            i,
                            verdict.failed_invariant,
                            verdict.detail,
                        )
                        checked.append(original)
                        continue
                checked.append(msg)
            merged = checked

        merged = self._enforce_hard_budget(merged)

        new_body = {**body, "messages": merged}
        compressed_chars = _total_chars(merged)
        compressed_flag = compressed_chars < original_chars

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        stats = create_compression_stats(
            body,
            {"messages": merged},
            effective_mode,
            techniques,
            rules,
            duration_ms,
        )
        overall_score = self._overall_fidelity(body, new_body)

        get_stats_store().record(
            mode=effective_mode,
            original_chars=original_chars,
            compressed_chars=compressed_chars,
            compressed=compressed_flag,
            techniques_used=techniques,
            original_tokens=stats.original_tokens,
            compressed_tokens=stats.compressed_tokens,
            duration_ms=duration_ms,
            fallback=reverted > 0,
        )

        return CompressionOutcome(
            body=new_body,
            compressed=compressed_flag,
            mode=effective_mode,
            stats=stats,
            techniques_used=techniques,
            rules_applied=rules,
            fidelity_passed=reverted == 0,
            fidelity_reverted=reverted,
            fidelity_score=overall_score,
            engines=engines,
        )

    def compress_text(self, text: str, mode: str | None = None) -> dict[str, Any]:
        """Compress a single text (the route-level convenience API)."""
        start = time.perf_counter()
        effective_mode = mode or self.config.default_mode or "off"
        if effective_mode not in MODES:
            raise ValueError(f"unknown compression mode: {effective_mode!r}")
        if not self.config.enabled or effective_mode == "off" or not text:
            return {
                "text": text,
                "compressed": False,
                "mode": effective_mode,
                "fidelity_score": 1.0,
                "original_chars": len(text),
                "compressed_chars": len(text),
            }
        if len(text) > self.config.max_input_chars:
            return {
                "text": text,
                "compressed": False,
                "mode": effective_mode,
                "fidelity_score": 1.0,
                "original_chars": len(text),
                "compressed_chars": len(text),
            }

        outcome = self.compress_body(
            {"messages": [{"role": "user", "content": text}]}, mode=effective_mode
        )
        result_text = extract_message_text(outcome.body["messages"][0])
        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        get_stats_store().record(
            mode=f"{effective_mode}:text",
            original_chars=len(text),
            compressed_chars=len(result_text),
            compressed=len(result_text) < len(text),
            techniques_used=outcome.techniques_used,
            duration_ms=duration_ms,
        )
        return {
            "text": result_text,
            "compressed": outcome.compressed,
            "mode": effective_mode,
            "fidelity_score": outcome.fidelity_score,
            "techniques_used": outcome.techniques_used,
            "original_chars": len(text),
            "compressed_chars": len(result_text),
        }

    # ---------- internals ----------

    def _run_mode(
        self,
        mode: str,
        messages: list[dict[str, Any]],
        model: str | None,
        stacked_pipeline: list[dict[str, str]] | None,
    ) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
        if mode == "lite":
            supports_vision = model_supports_vision(model) if model else None
            out, techniques = apply_lite_compression(
                messages,
                preserve_system_prompt=True,
                supports_vision=supports_vision,
            )
            return out, techniques, [], ["lite"]

        if mode == "standard":
            result = caveman_compress_messages(
                messages, CavemanConfig(intensity="lite")
            )
            techniques = ["caveman-lite"] if result.rules_applied else []
            return (
                result.messages,
                techniques,
                result.rules_applied,
                ["caveman"],
            )

        if mode == "aggressive":
            out, techniques, rules = aggressive_compress_messages(messages)
            return out, techniques, rules, ["rtk", "lite", "caveman"]

        if mode == "ultra":
            result = ultra_compress_messages(messages)
            return result.messages, result.techniques_used, [], ["ultra"]

        if mode == "rtk":
            out, techniques, rules, filters = rtk_compress_messages(
                messages, intensity="standard"
            )
            rules = [*rules, *(f"filter:{f}" for f in filters)]
            return out, techniques, rules, ["rtk"]

        if mode == "stacked":
            pipeline = stacked_pipeline or list(DEFAULT_STACKED_PIPELINE)
            return self._run_stacked(messages, pipeline)

        return messages, [], [], []

    def _run_stacked(
        self,
        messages: list[dict[str, Any]],
        pipeline: list[dict[str, str]],
    ) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
        """Two-stage (or N-stage) engine pipeline, OmniRoute stacked loop."""
        current = messages
        all_techniques: list[str] = []
        all_rules: list[str] = []
        engines: list[str] = []
        for step in pipeline:
            engine = str(step.get("engine", ""))
            intensity = str(step.get("intensity", "standard"))
            if engine == "rtk":
                current, techniques, rules, filters = rtk_compress_messages(
                    current, intensity=intensity
                )
                all_techniques.extend(techniques)
                all_rules.extend(rules)
                all_rules.extend(f"filter:{f}" for f in filters)
            elif engine == "caveman":
                caveman_intensity = intensity if intensity in ("lite", "full", "ultra") else "full"
                result = caveman_compress_messages(
                    current, CavemanConfig(intensity=caveman_intensity)
                )
                current = result.messages
                if result.rules_applied:
                    all_techniques.append(f"caveman-{caveman_intensity}")
                    all_rules.extend(result.rules_applied)
            elif engine == "lite":
                current, techniques = apply_lite_compression(current)
                all_techniques.extend(techniques)
            elif engine == "ultra":
                result_ultra = ultra_compress_messages(current)
                current = result_ultra.messages
                all_techniques.extend(result_ultra.techniques_used)
            else:
                logger.warning("unknown stacked engine %r skipped", engine)
                continue
            engines.append(engine)
        return (
            current,
            list(dict.fromkeys(all_techniques)),
            list(dict.fromkeys(all_rules)),
            list(dict.fromkeys(engines)),
        )

    def _enforce_hard_budget(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        budget = self.config.hard_budget_chars
        if budget <= 0 or _total_chars(messages) <= budget:
            return messages

        result = [dict(m) for m in messages]
        # Truncate oldest tool/assistant messages first; the live user turn
        # (last user message) is only truncated as a last resort.
        last_user_index = -1
        for i in range(len(result) - 1, -1, -1):
            if result[i].get("role") == "user":
                last_user_index = i
                break

        candidates = [
            i
            for i, msg in enumerate(result)
            if msg.get("role") in ("tool", "assistant")
            and isinstance(msg.get("content"), str)
        ]
        candidates.sort(key=lambda i: i)
        if last_user_index >= 0 and isinstance(result[last_user_index].get("content"), str):
            candidates.append(last_user_index)

        for index in candidates:
            if _total_chars(result) <= budget:
                break
            msg = result[index]
            content = msg["content"]
            overhead = _total_chars(result) - len(content)
            allowance = max(0, budget - overhead - len(_HARD_BUDGET_MARKER))
            if allowance < len(content):
                result[index] = {
                    **msg,
                    "content": content[:allowance] + _HARD_BUDGET_MARKER,
                }
                logger.info(
                    "hard budget enforced: message %d truncated to %d chars",
                    index,
                    allowance,
                )
        return result

    def _overall_fidelity(
        self, original_body: dict[str, Any], new_body: dict[str, Any]
    ) -> float:
        original_text = "\n".join(
            extract_message_text(m) for m in original_body.get("messages", [])
        )
        new_text = "\n".join(
            extract_message_text(m) for m in new_body.get("messages", [])
        )
        return fidelity_score(original_text, new_text)


_engine_lock = threading.Lock()
_engine: CompressionEngine | None = None


def get_engine() -> CompressionEngine:
    """Process-wide engine bound to the live gateway settings.

    The singleton is rebuilt whenever the settings instance changes
    (configuration reload / test isolation), so it never serves a stale
    ``CompressionConfig``.
    """
    global _engine
    with _engine_lock:
        current_config = get_settings().compression
        if _engine is None or _engine.config is not current_config:
            _engine = CompressionEngine(current_config)
        return _engine


def reset_engine() -> None:
    """Drop the cached singleton (tests / settings reload)."""
    global _engine
    with _engine_lock:
        _engine = None
