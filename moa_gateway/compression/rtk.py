"""RTK engine — CLI / structured tool-output compression via JSON filters.

Ported from OmniRoute (https://github.com/diegosouzapw/OmniRoute, MIT License),
``open-sse/services/compression/engines/rtk/``:

* 56 filter definitions in ``rtk/filters/*.json`` (aws, docker, git, npm,
  pytest, ...). Field mapping follows ``filterSchema.ts``:
  ``rules.includePatterns`` -> keep patterns, ``rules.dropPatterns`` -> strip
  patterns, ``preserve.errorPatterns + preserve.summaryPatterns`` -> priority
  patterns, ``rules.headLines/tailLines`` -> preserved head/tail,
  ``rules.maxLines`` -> hard line cap, ``rules.deduplicate`` -> per-filter
  consecutive-duplicate collapse.
* Detection matches a filter by the originating command regex
  (``match.commands``) or by content fingerprints (``match.patterns``); the
  highest ``priority`` wins.
* Generic passes (always applied): repeated-line deduplication and smart
  head/tail truncation that keeps error/traceback lines as priority.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_FILTERS_DIR = Path(__file__).resolve().parent / "rtk" / "filters"
_PORT_DATA_PATH = Path(__file__).resolve().parent / "port_data.json"

_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
DEFAULT_PRIORITY_RE = re.compile(
    r"error|failed|exception|traceback|TS\d{4}|FAIL|\u2716", re.IGNORECASE
)
OMITTED_MARKER = "... ({count} lines omitted)"

_RTK_INTENSITY_MAX_LINES = {"minimal": 240, "standard": 120, "aggressive": 60}


@dataclass(frozen=True)
class RtkFilter:
    id: str
    label: str
    category: str
    priority: int
    match_commands: tuple[re.Pattern, ...]
    match_patterns: tuple[re.Pattern, ...]
    keep_patterns: tuple[re.Pattern, ...]
    strip_patterns: tuple[re.Pattern, ...]
    collapse_patterns: tuple[re.Pattern, ...]
    priority_patterns: tuple[re.Pattern, ...]
    deduplicate: bool
    max_lines: int
    preserve_head: int
    preserve_tail: int


@dataclass
class RtkProcessResult:
    text: str
    compressed: bool
    filter_id: str | None
    techniques_used: list[str] = field(default_factory=list)
    rules_applied: list[str] = field(default_factory=list)


def _compile(patterns: list[str] | None, flags: int = re.IGNORECASE) -> tuple[re.Pattern, ...]:
    compiled = []
    for pattern in patterns or []:
        try:
            compiled.append(re.compile(pattern, flags))
        except re.error as exc:
            logger.warning("dropping invalid RTK pattern %r: %s", pattern, exc)
    return tuple(compiled)


def _normalize_filter(raw: dict[str, Any]) -> RtkFilter | None:
    filter_id = str(raw.get("id", ""))
    if not filter_id:
        return None
    rules = raw.get("rules") or {}
    preserve = raw.get("preserve") or {}
    match = raw.get("match") or {}
    priority_patterns = [
        *list(preserve.get("errorPatterns") or []),
        *list(preserve.get("summaryPatterns") or []),
    ]
    return RtkFilter(
        id=filter_id,
        label=str(raw.get("label", filter_id)),
        category=str(raw.get("category", "")),
        priority=int(raw.get("priority", 0)),
        match_commands=_compile(list(match.get("commands") or [])),
        match_patterns=_compile(list(match.get("patterns") or []), re.IGNORECASE | re.MULTILINE),
        keep_patterns=_compile(list(rules.get("includePatterns") or [])),
        strip_patterns=_compile(list(rules.get("dropPatterns") or [])),
        collapse_patterns=_compile(list(rules.get("collapsePatterns") or [])),
        priority_patterns=_compile(priority_patterns),
        deduplicate=bool(rules.get("deduplicate", False)),
        max_lines=int(rules.get("maxLines", 0) or 0),
        preserve_head=int(rules.get("headLines", 20) or 0),
        preserve_tail=int(rules.get("tailLines", 20) or 0),
    )


_lock = threading.Lock()
_filters: list[RtkFilter] | None = None
_detectors: list[dict[str, Any]] | None = None


def load_filters() -> list[RtkFilter]:
    """Load and cache all 56 bundled RTK filter definitions."""
    global _filters
    with _lock:
        if _filters is not None:
            return _filters
        loaded: list[RtkFilter] = []
        if _FILTERS_DIR.is_dir():
            for path in sorted(_FILTERS_DIR.glob("*.json")):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    logger.warning("failed to load RTK filter %s: %s", path, exc)
                    continue
                normalized = _normalize_filter(raw)
                if normalized is not None:
                    loaded.append(normalized)
        loaded.sort(key=lambda f: (-f.priority, f.id))
        _filters = loaded
        logger.info("loaded %d RTK filters from %s", len(loaded), _FILTERS_DIR)
        return loaded


def load_detectors() -> list[dict[str, Any]]:
    """CLI-output detectors ported from OmniRoute (``port_data.json``)."""
    global _detectors
    with _lock:
        if _detectors is not None:
            return _detectors
        try:
            data = json.loads(_PORT_DATA_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("port_data.json unavailable; RTK detectors disabled")
            _detectors = []
            return _detectors
        detectors = []
        for det in data.get("detectors", []):
            if not isinstance(det, dict):
                continue
            detectors.append(det)
        _detectors = detectors
        return detectors


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def detect_filter(text: str, command: str | None = None) -> RtkFilter | None:
    """Pick the best-matching filter by command hint then content fingerprint."""
    filters = load_filters()
    if command:
        stripped = command.strip()
        for candidate in filters:
            if any(p.search(stripped) for p in candidate.match_commands):
                return candidate
    for candidate in filters:  # already sorted by priority desc
        if any(p.search(text) for p in candidate.match_patterns):
            return candidate
    return None


def detect_command_type(text: str) -> str | None:
    """Best-effort CLI output classification via the ported detectors."""
    sample = text[:4000]
    for det in load_detectors():
        content_patterns = det.get("contentPatterns") or []
        matched = 0
        for entry in content_patterns:
            source = entry.get("source") if isinstance(entry, dict) else entry
            if not source:
                continue
            flags = 0
            if isinstance(entry, dict) and "m" in str(entry.get("flags", "")):
                flags |= re.MULTILINE
            try:
                if re.search(source, sample, flags):
                    matched += 1
            except re.error:
                continue
        if matched > 0:
            return str(det.get("type", ""))
    return None


def deduplicate_repeated_lines(text: str, threshold: int = 3) -> tuple[str, int]:
    """Collapse runs of identical lines (TS ``deduplicateRepeatedLines``)."""
    threshold = max(2, threshold)
    lines = re.split(r"\r?\n", text)
    output: list[str] = []
    collapsed = 0
    index = 0
    while index < len(lines):
        line = lines[index]
        run_length = 1
        while index + run_length < len(lines) and lines[index + run_length] == line:
            run_length += 1
        if line.strip() and run_length >= threshold:
            output.append(line)
            output.append(f"[line repeated {run_length - 1}x]")
            output.append(f"[rtk:dropped {run_length - 1} repeated lines]")
            collapsed += run_length - 1
            index += run_length
            continue
        output.append(line)
        index += 1
    return "\n".join(output), collapsed


def smart_truncate(
    text: str,
    max_lines: int = 0,
    max_chars: int = 0,
    preserve_head: int = 24,
    preserve_tail: int = 24,
    priority_patterns: tuple[re.Pattern, ...] | list[re.Pattern] = (),
) -> tuple[str, bool, int]:
    """Keep head + priority + tail lines; drop the middle with a marker."""
    lines = re.split(r"\r?\n", text)
    over_line = max_lines > 0 and len(lines) > max_lines
    over_char = max_chars > 0 and len(text) > max_chars
    if not over_line and not over_char:
        return text, False, 0

    preserve_head = max(0, preserve_head)
    preserve_tail = max(0, preserve_tail)
    priority_lines = (
        [line for line in lines if any(p.search(line) for p in priority_patterns)]
        if priority_patterns
        else []
    )

    head = lines[:preserve_head]
    tail = lines[-preserve_tail:] if preserve_tail > 0 else []
    selected = list(head)
    for line in priority_lines:
        if line not in selected:
            selected.append(line)
    tail_start = len(lines) - len(tail)
    for offset, line in enumerate(tail):
        original_index = tail_start + offset
        if original_index >= preserve_head and line not in selected:
            selected.append(line)

    dropped_lines = max(0, len(lines) - len(selected))
    result = "\n".join(
        [
            *selected[: len(head)],
            f"[rtk:truncated {dropped_lines} lines]",
            *selected[len(head):],
        ]
    )

    if max_chars > 0 and len(result) > max_chars:
        marker = "\n[rtk:truncated by chars]\n"
        budget = max(0, max_chars - len(marker))
        if budget == 0:
            return marker[:max_chars], True, dropped_lines
        # 55% of the budget to the head, 45% to the tail (TS smartTruncate).
        head_chars = (budget * 55 + 99) // 100
        tail_chars = max(0, budget - head_chars)
        tail_text = result[-tail_chars:] if tail_chars > 0 else ""
        result = f"{result[:head_chars]}{marker}{tail_text}"
        if len(result) > max_chars:
            result = result[:max_chars]

    return result, True, dropped_lines


def apply_line_filter(text: str, rtk_filter: RtkFilter) -> tuple[str, list[str]]:
    """Apply one filter definition; returns (text, applied_rule_ids)."""
    applied: list[str] = []
    lines = re.split(r"\r?\n", strip_ansi(text))
    original_count = len(lines)

    if rtk_filter.strip_patterns:
        kept = [
            line
            for line in lines
            if not any(p.search(line) for p in rtk_filter.strip_patterns)
        ]
        if len(kept) != len(lines):
            applied.append(f"{rtk_filter.id}:strip")
        lines = kept

    if rtk_filter.keep_patterns:
        matched = [
            line
            for line in lines
            if any(p.search(line) for p in rtk_filter.keep_patterns)
            or any(p.search(line) for p in rtk_filter.priority_patterns)
        ]
        if matched:
            lines = matched
            applied.append(f"{rtk_filter.id}:keep")

    if rtk_filter.collapse_patterns:
        seen: set[str] = set()
        collapsed_lines: list[str] = []
        for line in lines:
            if any(p.search(line) for p in rtk_filter.collapse_patterns):
                key = line.strip()
                if key in seen:
                    continue
                seen.add(key)
            collapsed_lines.append(line)
        if len(collapsed_lines) != len(lines):
            applied.append(f"{rtk_filter.id}:collapse")
        lines = collapsed_lines

    if rtk_filter.deduplicate:
        deduped, collapsed = deduplicate_repeated_lines("\n".join(lines))
        if collapsed > 0:
            lines = re.split(r"\r?\n", deduped)
            applied.append(f"{rtk_filter.id}:deduplicate")

    max_lines = rtk_filter.max_lines
    if max_lines > 0 and len(lines) > max_lines:
        head_keep = min(rtk_filter.preserve_head, max_lines)
        tail_keep = min(rtk_filter.preserve_tail, max(0, max_lines - head_keep))
        omitted = len(lines) - head_keep - tail_keep
        truncated = [
            *lines[:head_keep],
            OMITTED_MARKER.format(count=omitted),
            *(lines[-tail_keep:] if tail_keep > 0 else []),
        ]
        lines = truncated[:max_lines]
        applied.append(f"{rtk_filter.id}:truncate")

    stripped_lines = max(0, original_count - len(lines))
    return "\n".join(lines), applied + (
        [f"{rtk_filter.id}:stripped={stripped_lines}"] if stripped_lines else []
    )


def effective_max_lines(intensity: str) -> int:
    return _RTK_INTENSITY_MAX_LINES.get(intensity, _RTK_INTENSITY_MAX_LINES["standard"])


def process_rtk_text(
    text: str,
    command: str | None = None,
    intensity: str = "standard",
    max_chars: int = 12000,
    dedup_threshold: int = 3,
) -> RtkProcessResult:
    """Full RTK pass over one tool-output text."""
    if not text or not text.strip():
        return RtkProcessResult(text=text, compressed=False, filter_id=None)

    original_len = len(text)
    techniques: list[str] = []
    rules: list[str] = []
    result = strip_ansi(text)
    if result != text:
        techniques.append("rtk-strip-ansi")

    matched = detect_filter(result, command)
    filter_id = matched.id if matched else None
    if matched is not None:
        result, applied = apply_line_filter(result, matched)
        if applied:
            techniques.append("rtk-filter")
            rules.extend(applied)

    deduped, collapsed = deduplicate_repeated_lines(result, dedup_threshold)
    if collapsed > 0:
        result = deduped
        techniques.append("rtk-dedup")
        rules.append("rtk:dedup")

    head_tail = 16 if intensity == "aggressive" else 24
    priority = [DEFAULT_PRIORITY_RE]
    if matched is not None:
        priority.extend(matched.priority_patterns)
    truncated, was_truncated, dropped = smart_truncate(
        result,
        max_lines=effective_max_lines(intensity),
        max_chars=max_chars,
        preserve_head=head_tail,
        preserve_tail=head_tail,
        priority_patterns=priority,
    )
    if was_truncated:
        result = truncated
        techniques.append("rtk-truncate")
        rules.append(f"rtk:truncate:{dropped}")

    return RtkProcessResult(
        text=result,
        compressed=len(result) < original_len,
        filter_id=filter_id,
        techniques_used=list(dict.fromkeys(techniques)),
        rules_applied=list(dict.fromkeys(rules)),
    )


def rtk_compress_messages(
    messages: list[dict[str, Any]],
    intensity: str = "standard",
    apply_to_tool_results: bool = True,
    apply_to_cli_content: bool = True,
) -> tuple[list[dict[str, Any]], list[str], list[str], list[str]]:
    """RTK pass over chat messages.

    Tool/function results are always candidates; plain string messages are
    compressed only when a CLI-output detector or filter fingerprint matches
    (so ordinary prose is never line-filtered).
    Returns (messages, techniques, rules, matched_filters).
    """
    techniques: list[str] = []
    rules: list[str] = []
    matched_filters: list[str] = []
    out: list[dict[str, Any]] = []

    for msg in messages:
        role = str(msg.get("role", ""))
        content = msg.get("content")
        if not isinstance(content, str) or not content.strip():
            out.append(msg)
            continue

        is_tool = role in ("tool", "function")
        if is_tool and not apply_to_tool_results:
            out.append(msg)
            continue
        if not is_tool:
            if not apply_to_cli_content:
                out.append(msg)
                continue
            if detect_filter(content[:4000]) is None and detect_command_type(content) is None:
                out.append(msg)
                continue

        command_hint = None
        if isinstance(msg.get("name"), str):
            command_hint = msg["name"]
        processed = process_rtk_text(content, command=command_hint, intensity=intensity)
        if processed.compressed:
            techniques.extend(processed.techniques_used)
            rules.extend(processed.rules_applied)
            if processed.filter_id:
                matched_filters.append(processed.filter_id)
            out.append({**msg, "content": processed.text})
        else:
            out.append(msg)

    return (
        out,
        list(dict.fromkeys(techniques)),
        list(dict.fromkeys(rules)),
        list(dict.fromkeys(matched_filters)),
    )
