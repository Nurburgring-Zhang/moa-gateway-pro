"""Subagent result folding and cost merging.

Ported from OpenClacky (https://github.com/clacky-ai/openclacky, MIT License).
Sources: ``lib/clacky/agent.rb`` — ``generate_subagent_summary`` (the
``[SUBAGENT SUMMARY]`` block), ``extract_subagent_transcript`` +
``cap_transcript_events`` (bounded transcript), and the cost accounting that
rolls a subagent's spend into the parent session.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SUBAGENT_SUMMARY_HEADER",
    "MAX_TRANSCRIPT_EVENTS",
    "MAX_TRANSCRIPT_BYTES",
    "CostLedger",
    "generate_subagent_summary",
    "fold_subagent_result",
    "extract_subagent_transcript",
]

SUBAGENT_SUMMARY_HEADER = "[SUBAGENT SUMMARY]"

# Ports of Agent::MAX_TRANSCRIPT_EVENTS / MAX_TRANSCRIPT_BYTES — session
# archives are rewritten in full on every save, so transcripts stay bounded.
MAX_TRANSCRIPT_EVENTS = 200
MAX_TRANSCRIPT_BYTES = 64 * 1024


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return str(content)


def _tool_call_names(messages: list[dict[str, Any]]) -> list[str]:
    """Unique tool names invoked by assistant messages, in first-use order
    (port of the tool_calls extraction in generate_subagent_summary; handles
    both flat ``tc[:name]`` and OpenAI ``tc["function"]["name"]`` shapes)."""
    names: list[str] = []
    seen: set[str] = set()
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function") if isinstance(tc.get("function"), dict) else tc
            name = func.get("name") or tc.get("name")
            if name and str(name) not in seen:
                seen.add(str(name))
                names.append(str(name))
    return names


def generate_subagent_summary(
    new_messages: list[dict[str, Any]],
    iterations: int = 0,
    total_cost: float = 0.0,
) -> str:
    """Build the ``[SUBAGENT SUMMARY]`` block (verbatim port of
    ``Agent#generate_subagent_summary``'s output format).

    *new_messages* is the subagent's OWN trail (messages added after the
    fork), not the full history.
    """
    tool_calls = _tool_call_names(new_messages)
    last_response: str | None = None
    for msg in reversed(new_messages):
        if msg.get("role") != "assistant":
            continue
        text = _message_text(msg.get("content"))
        if text:
            last_response = text
            break

    parts: list[str] = [SUBAGENT_SUMMARY_HEADER]
    parts.append(f"Completed in {iterations} iterations, cost: ${round(float(total_cost), 4)}")
    if tool_calls:
        parts.append(f"Tools used: {', '.join(tool_calls)}")
    parts.append("")
    parts.append("Results:")
    parts.append(last_response or "(No response)")
    return "\n".join(parts)


def fold_subagent_result(
    parent_messages: list[dict[str, Any]],
    subagent_new_messages: list[dict[str, Any]],
    iterations: int = 0,
    total_cost: float = 0.0,
) -> list[dict[str, Any]]:
    """Fold a finished subagent back into the parent history.

    Port of the parent-side fold: the summary REPLACES the subagent
    instructions message (the one flagged ``subagent_instructions``) so the
    parent sees one compact block instead of the fork scaffolding. When no
    instructions message exists (e.g. a detached run), the summary is
    appended as a new ``system_injected`` user message.
    Returns a new list; inputs are never mutated.
    """
    summary = generate_subagent_summary(subagent_new_messages, iterations, total_cost)
    out: list[dict[str, Any]] = []
    folded = False
    for msg in parent_messages:
        if not folded and msg.get("subagent_instructions"):
            replacement = dict(msg)
            replacement["content"] = summary
            replacement.pop("subagent_instructions", None)
            replacement["subagent_summary"] = True
            replacement["system_injected"] = True
            out.append(replacement)
            folded = True
        else:
            out.append(copy.deepcopy(msg))
    if not folded:
        out.append(
            {
                "role": "user",
                "content": summary,
                "system_injected": True,
                "subagent_summary": True,
            }
        )
    return out


def _transcript_entry_bytes(entry: dict[str, Any]) -> int:
    content = entry.get("content")
    size = len(str(content).encode("utf-8")) if content is not None else 0
    for tc in entry.get("tool_calls") or []:
        if isinstance(tc, dict):
            size += len(str(tc.get("arguments", "")).encode("utf-8"))
    return size


def extract_subagent_transcript(
    subagent_messages: list[dict[str, Any]],
    parent_message_count: int = 0,
    max_events: int = MAX_TRANSCRIPT_EVENTS,
    max_bytes: int = MAX_TRANSCRIPT_BYTES,
) -> list[dict[str, Any]]:
    """Bounded, LLM-free trail of what the subagent did after the fork.

    Port of ``extract_subagent_transcript`` + ``cap_transcript_events``:
    system-injected scaffolding is dropped, oldest events are evicted first
    (the tail explains how the subagent ended up where it did), and a
    ``[N earlier event(s) omitted]`` marker records the loss honestly.
    """
    new_messages = subagent_messages[parent_message_count:] or []
    events: list[dict[str, Any]] = []
    for msg in new_messages:
        if msg.get("system_injected"):
            continue
        role = str(msg.get("role", ""))
        if role not in ("assistant", "tool", "user"):
            continue
        entry: dict[str, Any] = {"role": role}
        text = _message_text(msg.get("content"))
        if text:
            entry["content"] = msg.get("content") if isinstance(msg.get("content"), str) else text
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            packed: list[dict[str, Any]] = []
            for tc in tool_calls:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function") if isinstance(tc.get("function"), dict) else tc
                name = func.get("name") or tc.get("name")
                if not name:
                    continue
                packed.append(
                    {
                        "name": name,
                        "arguments": func.get("arguments", tc.get("arguments", {})) or {},
                    }
                )
            if packed:
                entry["tool_calls"] = packed
        if msg.get("tool_call_id"):
            entry["tool_call_id"] = msg["tool_call_id"]
        if "content" in entry or "tool_calls" in entry:
            events.append(entry)

    kept = events[-max_events:]
    budget = max_bytes
    trimmed: list[dict[str, Any]] = []
    for entry in reversed(kept):
        budget -= _transcript_entry_bytes(entry)
        if budget <= 0:
            break
        trimmed.append(entry)
    kept = list(reversed(trimmed))
    dropped = len(events) - len(kept)
    if dropped <= 0:
        return kept
    return [{"role": "system", "content": f"[{dropped} earlier event(s) omitted]"}] + kept


@dataclass
class CostLedger:
    """Cost merge point: parent session + absorbed subagent spend.

    Port intent: OpenClacky inherits ``previous_total_tokens`` into the fork
    and reports ``subagent.total_cost`` in the summary; the gateway keeps an
    explicit ledger so the parent's billing/quota accounting sees the whole
    fan-out.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    subagent_cost_usd: float = 0.0
    subagent_iterations: int = 0
    subagent_runs: int = 0
    per_model_cost: dict[str, float] = field(default_factory=dict)

    def absorb_subagent_cost(
        self,
        cost_usd: float,
        iterations: int = 0,
        model: str | None = None,
    ) -> None:
        """Merge one finished subagent's spend into the parent ledger."""
        amount = max(float(cost_usd), 0.0)
        self.subagent_cost_usd += amount
        self.subagent_iterations += max(int(iterations), 0)
        self.subagent_runs += 1
        if model:
            self.per_model_cost[model] = self.per_model_cost.get(model, 0.0) + amount

    @property
    def total_cost_usd(self) -> float:
        return self.cost_usd + self.subagent_cost_usd

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "subagent_cost_usd": self.subagent_cost_usd,
            "subagent_iterations": self.subagent_iterations,
            "subagent_runs": self.subagent_runs,
            "total_cost_usd": self.total_cost_usd,
            "per_model_cost": dict(self.per_model_cost),
        }
