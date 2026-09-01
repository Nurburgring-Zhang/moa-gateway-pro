"""Immutable system prompt + ``system_injected`` side channel.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Design sources:

- ``lib/clacky/agent/system_prompt_builder.rb`` — the system prompt is built
  ONCE at session start and never rewritten mid-session.
- ``lib/clacky/agent/message_compressor.rb`` / ``agent.rb`` — every piece of
  dynamic or internal context (compression instructions, fork notices,
  runtime state) is appended as a ``role: "user"`` message flagged
  ``system_injected: true`` instead of mutating the frozen system prompt.

Rationale: provider prompt caches key on the exact prefix starting at the
system prompt. Rewriting the system prompt on every turn (to inject the time,
the current task status, etc.) invalidates the whole cached prefix. Freezing
the system content and routing dynamic notes through a side channel keeps the
prefix byte-stable so cache breakpoints keep hitting.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SystemPromptMutationError",
    "FrozenSystemPrompt",
    "SystemPromptRegistry",
    "get_system_prompt_registry",
    "side_channel_message",
    "strip_internal_fields",
    "INTERNAL_MESSAGE_FIELDS",
]

# Internal-only fields that must be stripped before a message sequence is sent
# to a provider API (port of MessageHistory::INTERNAL_FIELDS semantics).
INTERNAL_MESSAGE_FIELDS = (
    "system_injected",
    "compressed_summary",
    "chunk_path",
    "topics",
    "subagent_instructions",
    "subagent_summary",
    "compression_level",
    "transient",
)


class SystemPromptMutationError(ValueError):
    """Raised when a caller tries to change an already-frozen system prompt."""


@dataclass
class FrozenSystemPrompt:
    """A system prompt whose content is locked for the lifetime of a session."""

    prompt_id: str
    content: str
    content_hash: str
    frozen_at: float
    side_channel_injections: int = 0

    def system_message(self) -> dict[str, Any]:
        """The canonical system message for this frozen prompt."""
        return {"role": "system", "content": self.content}

    def verify(self, content: str) -> bool:
        return sha256_text(content) == self.content_hash


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SystemPromptRegistry:
    """Session-keyed freeze registry: the first freeze wins, later freezes of
    the same id must carry identical content or they raise.

    Thread-safe; the gateway serves many sessions concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._frozen: dict[str, FrozenSystemPrompt] = {}

    def freeze(self, prompt_id: str, content: str) -> FrozenSystemPrompt:
        """Freeze *content* under *prompt_id*. Idempotent for identical
        content; raises :class:`SystemPromptMutationError` on any change."""
        content = content or ""
        digest = sha256_text(content)
        with self._lock:
            existing = self._frozen.get(prompt_id)
            if existing is None:
                frozen = FrozenSystemPrompt(
                    prompt_id=prompt_id,
                    content=content,
                    content_hash=digest,
                    frozen_at=time.time(),
                )
                self._frozen[prompt_id] = frozen
                return frozen
            if existing.content_hash != digest:
                raise SystemPromptMutationError(
                    f"system prompt '{prompt_id}' is frozen; refusing to "
                    f"replace cached content (hash {existing.content_hash[:12]}...) "
                    f"with different content (hash {digest[:12]}...). Route "
                    f"dynamic content through the side channel instead."
                )
            return existing

    def get(self, prompt_id: str) -> FrozenSystemPrompt | None:
        with self._lock:
            return self._frozen.get(prompt_id)

    def get_or_freeze(self, prompt_id: str, content: str) -> FrozenSystemPrompt:
        # freeze() is idempotent for identical content and raises on change,
        # which is exactly get-or-freeze semantics.
        return self.freeze(prompt_id, content)

    def release(self, prompt_id: str) -> bool:
        """Drop a frozen prompt (session teardown). Returns True if present."""
        with self._lock:
            return self._frozen.pop(prompt_id, None) is not None

    def inject_side_channel(
        self,
        prompt_id: str,
        dynamic_content: str,
        messages: list[dict[str, Any]],
        system_content: str | None = None,
    ) -> list[dict[str, Any]]:
        """Build a cache-friendly request sequence:

        ``[frozen system] + messages + [side-channel user message]``

        - When *system_content* is provided it is verified against the frozen
          hash first (raises :class:`SystemPromptMutationError` on mismatch)
          and any existing system messages inside *messages* are dropped so
          the frozen one stays the single prefix anchor.
        - The side-channel note is appended as a ``system_injected`` user
          message — visible to the model, invisible to chat replay, and never
          a cache-marker candidate.
        """
        with self._lock:
            frozen = self._frozen.get(prompt_id)
            if frozen is None:
                if system_content is None:
                    raise KeyError(
                        f"system prompt '{prompt_id}' is not frozen; freeze it "
                        f"before injecting side-channel content"
                    )
                frozen = self.freeze(prompt_id, system_content)
            if system_content is not None and not frozen.verify(system_content):
                raise SystemPromptMutationError(
                    f"system prompt '{prompt_id}' changed after freezing; "
                    f"the cached prefix would be invalidated"
                )
            frozen.side_channel_injections += 1

        out: list[dict[str, Any]] = [frozen.system_message()]
        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                continue  # the frozen prompt is the only system message
            out.append(dict(msg))
        out.append(side_channel_message(dynamic_content))
        return out

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                pid: {
                    "content_hash": fp.content_hash,
                    "frozen_at": fp.frozen_at,
                    "side_channel_injections": fp.side_channel_injections,
                    "content_chars": len(fp.content),
                }
                for pid, fp in self._frozen.items()
            }


_registry = SystemPromptRegistry()


def get_system_prompt_registry() -> SystemPromptRegistry:
    """Process-wide registry (tests may instantiate their own)."""
    return _registry


def side_channel_message(content: str, **extra: Any) -> dict[str, Any]:
    """Build a ``system_injected`` user message carrying dynamic content.

    Port of the OpenClacky convention (compression instructions, fork
    notices): role ``user`` so providers with strict turn structure accept it,
    ``system_injected: true`` so UIs hide it and cache-marker logic skips it.
    """
    msg: dict[str, Any] = {
        "role": "user",
        "content": content,
        "system_injected": True,
    }
    msg.update(extra)
    return msg


def strip_internal_fields(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return copies of *messages* without internal-only bookkeeping fields
    (port of ``MessageHistory#strip_for_api``): providers must only see plain
    ``{role, content, ...}`` payloads."""
    out: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        out.append({k: v for k, v in msg.items() if k not in INTERNAL_MESSAGE_FIELDS})
    return out
