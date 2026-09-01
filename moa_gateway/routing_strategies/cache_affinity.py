"""Prompt-cache affinity (rendezvous hashing).

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT):
``open-sse/services/combo/promptCacheAffinity.ts`` —

- ``resolvePromptCacheAffinityKey``: explicit ``prompt_cache_key``
  (body or metadata, ≤4096 chars) wins; otherwise a deterministic prefix key
  derived from the conversation prefix (all messages except the trailing
  turn — the reusable cached prefix). Single-turn requests have no reusable
  prefix and resolve to no key (upstream contract).
- ``promptCacheTargetIdentity``: ``connection:<id>`` else ``execution:<key>``.
- ``rendezvousScore``: sha256(key + "\\0" + identity), top 128 bits as BigInt;
  normalized score = (value >> 64) / (2^64 − 1).
- ``combinedAffinityScore`` = cache*0.75 + availability*0.25 (availability
  is 1.0 for non-oauth targets; the gateway tracks no oauth sessions).
- ``applyPromptCacheAffinity`` (global scope): sort by score desc →
  identity asc → original index.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

_MAX_KEY_LEN = 4096
_MAX_RENDEZVOUS_HIGH_BITS = (1 << 64) - 1


@dataclass(frozen=True)
class CacheAffinityResolution:
    key: str
    source: str  # "explicit" | "prefix"
    fingerprint: str


def _as_record(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _read_explicit_prompt_cache_key(body: dict[str, Any]) -> str | None:
    metadata = _as_record(body.get("metadata"))
    candidates = [body.get("prompt_cache_key")]
    if metadata is not None:
        candidates.append(metadata.get("prompt_cache_key"))
    for value in candidates:
        if isinstance(value, str):
            normalized = value.strip()
            if 0 < len(normalized) <= _MAX_KEY_LEN:
                return normalized
    return None


def _normalize_messages(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Extract role-bearing messages from chat ``messages`` / responses ``input``."""
    messages = body.get("messages")
    if isinstance(messages, list) and messages:
        result = []
        for item in messages:
            record = _as_record(item)
            if record is not None and isinstance(record.get("role"), str):
                result.append(record)
        return result or None

    raw_input = body.get("input")
    if isinstance(raw_input, str) and raw_input:
        return [{"role": "user", "content": raw_input}]
    if isinstance(raw_input, list) and raw_input:
        result = []
        for item in raw_input:
            if isinstance(item, str):
                result.append({"role": "user", "content": item})
                continue
            record = _as_record(item)
            if record is not None and isinstance(record.get("role"), str):
                result.append(record)
        return result or None
    return None


def _prefix_key(messages: list[dict[str, Any]]) -> str:
    """Deterministic key over the reusable conversation prefix.

    The prefix is everything except the trailing message: providers cache the
    stable leading part of a conversation, so affinity must hash the turns
    that are actually reused. A single message has no reusable prefix.
    """
    if len(messages) <= 1:
        return ""
    hasher = hashlib.sha256()
    for message in messages[:-1]:
        role = message.get("role", "")
        content = message.get("content", "")
        if not isinstance(content, str):
            import json

            try:
                content = json.dumps(content, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError):
                content = ""
        hasher.update(f"{role}\x1f{content}\x1e".encode("utf-8"))
    return hasher.hexdigest()


def resolve_prompt_cache_affinity_key(
    body: dict[str, Any] | None,
) -> CacheAffinityResolution | None:
    """Port of ``resolvePromptCacheAffinityKey``."""
    if not body:
        return None
    explicit = _read_explicit_prompt_cache_key(body)
    key = explicit
    source = "explicit"
    if key is None:
        messages = _normalize_messages(body)
        key = _prefix_key(messages) if messages else ""
        source = "prefix"
    if not key:
        return None
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    return CacheAffinityResolution(key=key, source=source, fingerprint=fingerprint)


def prompt_cache_target_identity(endpoint_id: str, connection_id: str | None) -> str:
    """Port of ``promptCacheTargetIdentity``."""
    conn = (connection_id or "").strip()
    if conn:
        return f"connection:{conn}"
    return f"execution:{endpoint_id}"


def rendezvous_score(key: str, identity: str) -> int:
    """Port of ``rendezvousScore`` — sha256(key\\0identity), first 128 bits."""
    digest = hashlib.sha256(key.encode("utf-8") + b"\0" + identity.encode("utf-8")).hexdigest()
    return int(digest[:32], 16)


def normalized_rendezvous_score(key: str, identity: str) -> float:
    """Port of ``normalizedRendezvousScore`` = (value >> 64) / (2^64 − 1)."""
    return float(rendezvous_score(key, identity) >> 64) / float(_MAX_RENDEZVOUS_HIGH_BITS)


def combined_affinity_score(key: str, endpoint_id: str, connection_id: str | None) -> float:
    """Port of ``combinedAffinityScore`` (availability fixed at 1.0)."""
    cache_score = normalized_rendezvous_score(
        key, prompt_cache_target_identity(endpoint_id, connection_id)
    )
    return cache_score * 0.75 + 1.0 * 0.25


def rank_by_cache_affinity(
    candidates: list[Any],
    body: dict[str, Any] | None,
) -> tuple[list[Any], CacheAffinityResolution | None]:
    """Port of ``applyPromptCacheAffinity`` (scope="global").

    Accepts objects exposing ``endpoint_id`` / ``connection_id``; returns the
    reordered list plus the resolution used (None → order unchanged).
    """
    resolution = resolve_prompt_cache_affinity_key(body)
    if resolution is None or len(candidates) <= 1:
        return list(candidates), resolution

    ranked = []
    for index, candidate in enumerate(candidates):
        identity = prompt_cache_target_identity(
            candidate.endpoint_id, candidate.connection_id
        )
        score = combined_affinity_score(resolution.key, candidate.endpoint_id, candidate.connection_id)
        ranked.append((score, identity, index, candidate))
    ranked.sort(key=lambda entry: (-entry[0], entry[1], entry[2]))
    return [candidate for _, _, _, candidate in ranked], resolution
