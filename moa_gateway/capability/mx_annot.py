"""mx_annot - A-39 MX + A-40 fan-in + A-44 Moai-sidechain mx CLI
Source: 06 moai-adk-multiagent
Real implementation: 6 MX tags + multi-language parsing + reference counting + CLI
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class MXTag(str, Enum):
    """6 MX annotation tags"""

    NOTE = "NOTE"
    WARN = "WARN"
    ANCHOR = "ANCHOR"
    REASON = "REASON"
    TODO = "TODO"
    SPEC = "SPEC"


@dataclass
class MXAnnotation:
    """Single MX annotation"""

    tag: MXTag
    content: str
    file_path: str
    line_number: int
    language: str = "python"

    def to_dict(self) -> dict:
        return {
            "tag": self.tag.value,
            "content": self.content,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "language": self.language,
        }


# MX syntax per language
MX_SYNTAX = {
    "python": re.compile(r"#\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)(?:\*/)?$"),
    "go": re.compile(r"//\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)(?:\*/)?$"),
    "java": re.compile(r"//\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)(?:\*/)?$"),
    "js": re.compile(r"//\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)(?:\*/)?$"),
    "c": re.compile(r"/\*\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)\*/?$"),
    "cpp": re.compile(r"/\*\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)\*/?$"),
    "rust": re.compile(r"//\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)(?:\*/)?$"),
    "html": re.compile(r"<!--\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)\s*-->$"),
    "xml": re.compile(r"<!--\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)\s*-->$"),
    "markdown": re.compile(r"<!--\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)\s*-->$"),
    "yaml": re.compile(r"#\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)$"),
    "ruby": re.compile(r"#\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)$"),
    "shell": re.compile(r"#\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)$"),
    "bash": re.compile(r"#\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)$"),
    "sql": re.compile(r"--\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)$"),
    "lua": re.compile(r"--\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)$"),
}


def parse_mx_annotations(text, file_path, language="python"):
    # Universal: match ANY of the prefix styles in the line
    # # ... or // ... or /* ... or <!-- ... or -- ...
    universal = re.compile(
        r"(?:#|//|/\*|<!--|--)\s*mx:(NOTE|WARN|ANCHOR|REASON|TODO|SPEC)\s*:\s*(.+?)(?:\*/|-->)?\s*$"
    )
    annotations = []
    for i, line in enumerate(text.splitlines(), start=1):
        m = universal.match(line.strip())
        if m:
            tag_name, content = m.group(1), m.group(2).strip()
            try:
                tag = MXTag(tag_name)
            except ValueError:
                continue
            annotations.append(
                MXAnnotation(
                    tag=tag,
                    content=content,
                    file_path=file_path,
                    line_number=i,
                    language=language,
                )
            )
    return annotations


def compute_fanin(annotations):
    """fan-in reference count: group by content keyword, count references."""
    counts = {}
    for a in annotations:
        key = a.content.split()[0] if a.content else a.tag.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def mx_cli(annotations, command):
    """Moai-sidechain mx CLI: list/count/find"""
    parts = command.strip().split()
    if not parts:
        return ""
    cmd = parts[0].lower()
    if cmd == "list":
        if not annotations:
            return "no MX annotations"
        return "\n".join(
            f"{a.file_path}:{a.line_number} [{a.tag.value}] {a.content[:50]}" for a in annotations
        )
    elif cmd == "count":
        if len(parts) < 2:
            return "usage: count <TAG>"
        target = parts[1].upper()
        return str(sum(1 for a in annotations if a.tag.value == target))
    elif cmd == "find":
        if len(parts) < 2:
            return "usage: find <keyword>"
        keyword = " ".join(parts[1:]).lower()
        matches = [a for a in annotations if keyword in a.content.lower()]
        if not matches:
            return "0 matches"
        return "\n".join(
            f"{a.file_path}:{a.line_number} [{a.tag.value}] {a.content[:50]}" for a in matches
        )
    return f"unknown command: {cmd}"


def annotations_to_json(annotations):
    import json

    return json.dumps([a.to_dict() for a in annotations], ensure_ascii=False)
