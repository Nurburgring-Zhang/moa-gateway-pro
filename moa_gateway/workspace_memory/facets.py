"""Workspace memory facets — real facet scripts + markdown artifacts (M11).

Source: MemoraX Code (https://github.com/memorax-ai/memorax-code, MIT),
``scripts/repo-memory/prepare_repo_memory.py`` and
``scripts/repo-memory/git_commit_facets.py``: repository memory is built
from *facets*; each facet is a script that scans the repository and emits a
markdown artifact.  The supervisor executes the scripts as real subprocesses
and collects the artifacts.

Built-in facets (each script below is a complete, self-contained,
stdlib-only python program — it is written to ``.moa_memory/facets/<name>.py``
and executed with ``sys.executable``; its output lands in
``.moa_memory/facets/<name>.md``):

- ``project-overview`` : file inventory, language mix, README excerpt;
- ``conventions``      : detected tooling/config and the real settings read
                         from them (line length, indent style, CI files...);
- ``decisions``        : git history digest + decision-marker lines in docs;
- ``open-questions``   : TODO/FIXME/XXX/HACK markers found in source files.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

FACET_SCRIPT_TIMEOUT_SECONDS = 120.0
_STDERR_TAIL_CHARS = 800


@dataclass(frozen=True)
class FacetSpec:
    name: str
    title: str
    description: str
    script_source: str


@dataclass
class FacetRunResult:
    name: str
    ok: bool
    exit_code: int | None
    duration_ms: float
    artifact_chars: int = 0
    artifact_sha256: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "duration_ms": round(self.duration_ms, 2),
            "artifact_chars": self.artifact_chars,
            "artifact_sha256": self.artifact_sha256,
            "stderr_tail": self.stderr_tail,
        }


# ---------------------------------------------------------------------------
# Built-in facet scripts (written to disk and executed for real)
# ---------------------------------------------------------------------------
_PROJECT_OVERVIEW_SCRIPT = '''#!/usr/bin/env python3
"""Facet script: project overview. Stdlib only; read-only workspace scan.

Usage: python project-overview.py <workspace_root> <output_markdown_path>
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".moa_memory", "__pycache__", ".venv", "venv", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode", ".hg", ".svn",
}
MAX_FILES = 3000
README_NAMES = ("readme.md", "readme.rst", "readme.txt", "readme")
README_EXCERPT_LINES = 40
EXT_LANG = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
    ".tsx": "TypeScript (React)", ".jsx": "JavaScript (React)",
    ".java": "Java", ".go": "Go", ".rs": "Rust", ".c": "C", ".cpp": "C++",
    ".h": "C/C++ Header", ".cs": "C#", ".rb": "Ruby", ".php": "PHP",
    ".kt": "Kotlin", ".swift": "Swift", ".sh": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".json": "JSON",
    ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML", ".md": "Markdown",
    ".axaml": "Avalonia XAML", ".xaml": "XAML", ".vue": "Vue",
}


def walk_files(root: Path):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            yield Path(dirpath) / name
            count += 1
            if count >= MAX_FILES:
                return


def read_text_safe(path: Path, limit: int = 65536) -> str | None:
    try:
        raw = path.open("rb").read(limit)
    except OSError:
        return None
    if b"\\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    lines = ["# Project Overview", ""]
    if not root.is_dir():
        out.write_text("\\n".join(lines + ["_workspace root is not a directory_"]),
                       encoding="utf-8")
        return 1

    files = list(walk_files(root))
    total_bytes = 0
    ext_counts: dict[str, int] = {}
    for path in files:
        try:
            total_bytes += path.stat().st_size
        except OSError:
            pass
        ext = path.suffix.lower() or "(none)"
        ext_counts[ext] = ext_counts.get(ext, 0) + 1

    lines += [
        f"- Workspace: `{root}`",
        f"- Files scanned: {len(files)}",
        f"- Total size: {total_bytes / (1024 * 1024):.2f} MiB",
        "",
        "## Top-level entries",
        "",
    ]
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        entries = []
    for entry in entries[:40]:
        suffix = "/" if entry.is_dir() else ""
        lines.append(f"- `{entry.name}{suffix}`")
    lines += ["", "## Language / file-type mix", "", "| Type | Language | Files |", "| --- | --- | --- |"]
    ranked = sorted(ext_counts.items(), key=lambda kv: (-kv[1], kv[0]))
    for ext, count in ranked[:15]:
        lines.append(f"| `{ext}` | {EXT_LANG.get(ext, 'Other')} | {count} |")

    readme_path = None
    for name in README_NAMES:
        candidate = root / name
        if candidate.is_file():
            readme_path = candidate
            break
        candidate_upper = root / name.upper().replace("README", "README")
        if candidate_upper.is_file():
            readme_path = candidate_upper
            break
    if readme_path is None:
        for candidate in root.iterdir() if root.is_dir() else []:
            if candidate.is_file() and candidate.name.lower() in README_NAMES:
                readme_path = candidate
                break
    if readme_path is not None:
        text = read_text_safe(readme_path)
        lines += ["", f"## README excerpt (`{readme_path.name}`)", ""]
        if text:
            excerpt = text.splitlines()[:README_EXCERPT_LINES]
            lines.extend(excerpt)
        else:
            lines.append("_README is binary or unreadable_")
    else:
        lines += ["", "## README excerpt", "", "_no README found_"]
    lines.append("")
    out.write_text("\\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_CONVENTIONS_SCRIPT = '''#!/usr/bin/env python3
"""Facet script: conventions. Detects tooling config files and reports the
real settings read from them. Stdlib only; read-only workspace scan.

Usage: python conventions.py <workspace_root> <output_markdown_path>
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".moa_memory", "__pycache__", ".venv", "venv", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode", ".hg", ".svn",
}
CONFIG_MARKERS = [
    "pyproject.toml", "setup.cfg", "setup.py", "requirements.txt",
    ".editorconfig", "tsconfig.json", "package.json", "ruff.toml",
    ".flake8", "tox.ini", ".prettierrc", ".prettierrc.json", ".eslintrc",
    ".eslintrc.json", ".eslintrc.js", "Makefile", "justfile",
    "AGENTS.md", "CLAUDE.md", "CODESTYLE.md", "CONTRIBUTING.md",
    ".pre-commit-config.yaml", "Dockerfile", "docker-compose.yml",
]
CI_DIRS = [".github/workflows", ".gitlab/ci", ".circleci"]
SECTION_RE = re.compile(r"^\\s*\\[([^\\]]+)\\]\\s*$")
KEY_VALUE_RE = re.compile(r"^\\s*([A-Za-z0-9_.\\-]+)\\s*=\\s*(.+?)\\s*$")


def read_text_safe(path: Path, limit: int = 131072) -> str | None:
    try:
        raw = path.open("rb").read(limit)
    except OSError:
        return None
    if b"\\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def pyproject_findings(text: str) -> list[str]:
    findings: list[str] = []
    section = ""
    wanted = {
        "tool.ruff": ("line-length", "target-version", "select", "exclude"),
        "tool.ruff.lint": ("select", "ignore"),
        "tool.black": ("line-length", "target-version"),
        "tool.pytest.ini_options": ("addopts", "testpaths"),
        "tool.mypy": ("strict", "python_version"),
        "project": ("requires-python", "name"),
    }
    for line in text.splitlines():
        match = SECTION_RE.match(line)
        if match:
            section = match.group(1).strip()
            continue
        keys = wanted.get(section)
        if not keys:
            continue
        kv = KEY_VALUE_RE.match(line)
        if kv and kv.group(1) in keys:
            findings.append(f"`[{section}] {kv.group(1)} = {kv.group(2)}`")
    return findings


def editorconfig_findings(text: str) -> list[str]:
    findings: list[str] = []
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped
            continue
        kv = KEY_VALUE_RE.match(line)
        if kv and kv.group(1).lower() in ("indent_style", "indent_size",
                                          "end_of_line", "charset",
                                          "insert_final_newline", "max_line_length"):
            findings.append(f"`{section or '[*]'} {kv.group(1)} = {kv.group(2)}`")
    return findings


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    lines = ["# Conventions", ""]
    if not root.is_dir():
        out.write_text("\\n".join(lines + ["_workspace root is not a directory_"]),
                       encoding="utf-8")
        return 1

    found: list[str] = []
    details: list[str] = []
    for name in CONFIG_MARKERS:
        for candidate in (root / name, root / name.lower()):
            if candidate.is_file():
                found.append(candidate.name)
                break
        else:
            pattern_matches = sorted(root.glob(name)) if "*" in name else []
            found.extend(p.name for p in pattern_matches if p.is_file())
    found = sorted(set(found))

    for ci_dir in CI_DIRS:
        ci_path = root / ci_dir
        if ci_path.is_dir():
            try:
                ci_files = sorted(p.name for p in ci_path.iterdir() if p.is_file())
            except OSError:
                ci_files = []
            if ci_files:
                found.append(f"{ci_dir}/ ({len(ci_files)} files)")

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        text = read_text_safe(pyproject)
        if text:
            details.extend(pyproject_findings(text))
    editorconfig = root / ".editorconfig"
    if editorconfig.is_file():
        text = read_text_safe(editorconfig)
        if text:
            details.extend(editorconfig_findings(text))
    package_json = root / "package.json"
    if package_json.is_file():
        text = read_text_safe(package_json)
        if text:
            import json as _json
            try:
                data = _json.loads(text)
            except ValueError:
                data = None
            if isinstance(data, dict):
                scripts = data.get("scripts")
                if isinstance(scripts, dict) and scripts:
                    names = ", ".join(sorted(scripts)[:12])
                    details.append(f"`package.json` scripts: {names}")
                engines = data.get("engines")
                if isinstance(engines, dict) and engines:
                    details.append(f"`package.json` engines: "
                                   + ", ".join(f"{k} {v}" for k, v in sorted(engines.items())))

    lines.append(f"- Detected configuration markers ({len(found)}): "
                 + (", ".join(f"`{f}`" for f in found) if found else "none"))
    if details:
        lines += ["", "## Extracted settings", ""]
        lines.extend(f"- {d}" for d in details[:40])
    else:
        lines += ["", "## Extracted settings", "", "_no machine-readable settings found_"]

    style_docs = [name for name in ("AGENTS.md", "CLAUDE.md", "CODESTYLE.md",
                                    "CONTRIBUTING.md") if (root / name).is_file()]
    if style_docs:
        lines += ["", "## Human-written convention docs", ""]
        for name in style_docs:
            text = read_text_safe(root / name, 8192) or ""
            head = [l for l in text.splitlines() if l.strip()][:5]
            lines.append(f"- `{name}` opening lines:")
            lines.extend(f"  > {l.strip()}" for l in head)
    lines.append("")
    out.write_text("\\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_DECISIONS_SCRIPT = '''#!/usr/bin/env python3
"""Facet script: decisions. Summarizes git history (read-only) and extracts
decision-marker lines from markdown docs. Stdlib only.

Usage: python decisions.py <workspace_root> <output_markdown_path>
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".moa_memory", "__pycache__", ".venv", "venv", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode", ".hg", ".svn",
}
DECISION_RE = re.compile(
    r"(decision|decided|we agreed|agreed to|adr[- ]?\\d|conclusion|"
    r"决定|决议|结论|决策)",
    re.IGNORECASE,
)
MAX_DOC_FILES = 300
MAX_MATCHES = 100


def read_text_safe(path: Path, limit: int = 131072) -> str | None:
    try:
        raw = path.open("rb").read(limit)
    except OSError:
        return None
    if b"\\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def git_log(root: Path) -> list[str] | None:
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "log", "--oneline", "--no-decorate", "-n", "50"],
            capture_output=True, text=True, timeout=20,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    lines = ["# Decisions", ""]
    if not root.is_dir():
        out.write_text("\\n".join(lines + ["_workspace root is not a directory_"]),
                       encoding="utf-8")
        return 1

    log = git_log(root)
    if log:
        lines += [f"## Recent git history ({len(log)} commits)", ""]
        lines.extend(f"- `{entry}`" for entry in log)
    else:
        lines += ["## Recent git history", "", "_no git history available_"]

    matches: list[str] = []
    doc_count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            if not name.lower().endswith((".md", ".rst", ".txt")):
                continue
            path = Path(dirpath) / name
            text = read_text_safe(path)
            if text is None:
                continue
            doc_count += 1
            rel = path.relative_to(root).as_posix()
            for lineno, line in enumerate(text.splitlines(), start=1):
                if DECISION_RE.search(line) and line.strip():
                    matches.append(f"`{rel}:{lineno}` {line.strip()[:200]}")
                    if len(matches) >= MAX_MATCHES:
                        break
            if len(matches) >= MAX_MATCHES:
                break
        if len(matches) >= MAX_MATCHES or doc_count >= MAX_DOC_FILES:
            break

    lines += ["", f"## Decision markers in docs ({len(matches)} found)", ""]
    if matches:
        lines.extend(f"- {m}" for m in matches)
    else:
        lines.append("_no decision-marker lines found_")
    lines.append("")
    out.write_text("\\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

_OPEN_QUESTIONS_SCRIPT = '''#!/usr/bin/env python3
"""Facet script: open questions. Scans source files for TODO/FIXME/XXX/HACK
markers and reports them with file:line locations. Stdlib only; read-only.

Usage: python open-questions.py <workspace_root> <output_markdown_path>
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

EXCLUDE_DIRS = {
    ".git", ".moa_memory", "__pycache__", ".venv", "venv", "node_modules",
    ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".idea", ".vscode", ".hg", ".svn",
}
SOURCE_EXTS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".go", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".kt", ".swift", ".sh", ".sql",
    ".yaml", ".yml", ".toml", ".md", ".vue", ".axaml", ".xaml",
}
MARKER_RE = re.compile(r"\\b(TODO|FIXME|XXX|HACK|QUESTION)\\b\\s*:?(.*)$")
MAX_FILES = 3000
MAX_FILE_BYTES = 524_288
MAX_MATCHES = 150


def read_text_safe(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = path.open("rb").read()
    except OSError:
        return None
    if b"\\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace")


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    out = Path(sys.argv[2])
    lines = ["# Open Questions", ""]
    if not root.is_dir():
        out.write_text("\\n".join(lines + ["_workspace root is not a directory_"]),
                       encoding="utf-8")
        return 1

    matches: list[str] = []
    scanned = 0
    per_marker: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in EXCLUDE_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.suffix.lower() not in SOURCE_EXTS:
                continue
            text = read_text_safe(path)
            if text is None:
                continue
            scanned += 1
            rel = path.relative_to(root).as_posix()
            for lineno, line in enumerate(text.splitlines(), start=1):
                match = MARKER_RE.search(line)
                if match:
                    marker = match.group(1)
                    per_marker[marker] = per_marker.get(marker, 0) + 1
                    if len(matches) < MAX_MATCHES:
                        note = match.group(2).strip()[:160] or "(no text)"
                        matches.append(f"`{rel}:{lineno}` **{marker}** {note}")
            if scanned >= MAX_FILES:
                break
        if scanned >= MAX_FILES:
            break

    summary = ", ".join(f"{k}: {v}" for k, v in sorted(per_marker.items())) or "none"
    lines += [
        f"- Source files scanned: {scanned}",
        f"- Markers found: {sum(per_marker.values())} ({summary})",
        f"- Listed below: {len(matches)} (capped at {MAX_MATCHES})",
        "",
    ]
    if matches:
        lines.extend(f"- {m}" for m in matches)
    else:
        lines.append("_no open-question markers found_")
    lines.append("")
    out.write_text("\\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


BUILTIN_FACETS: tuple[FacetSpec, ...] = (
    FacetSpec(
        name="project-overview",
        title="Project Overview",
        description="File inventory, language mix and README excerpt of the workspace.",
        script_source=_PROJECT_OVERVIEW_SCRIPT,
    ),
    FacetSpec(
        name="conventions",
        title="Conventions",
        description="Detected tooling/config markers and real settings extracted from them.",
        script_source=_CONVENTIONS_SCRIPT,
    ),
    FacetSpec(
        name="decisions",
        title="Decisions",
        description="Git history digest plus decision-marker lines extracted from docs.",
        script_source=_DECISIONS_SCRIPT,
    ),
    FacetSpec(
        name="open-questions",
        title="Open Questions",
        description="TODO/FIXME/XXX/HACK markers found in source files, with locations.",
        script_source=_OPEN_QUESTIONS_SCRIPT,
    ),
)


def script_filename(spec: FacetSpec) -> str:
    return f"{spec.name}.py"


def artifact_filename(spec: FacetSpec) -> str:
    return f"{spec.name}.md"


def write_facet_scripts(facets_dir: Path, specs: tuple[FacetSpec, ...] = BUILTIN_FACETS) -> list[Path]:
    """Materialize facet scripts into ``.moa_memory/facets`` (idempotent)."""
    facets_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec in specs:
        script_path = facets_dir / script_filename(spec)
        script_path.write_text(spec.script_source, encoding="utf-8", newline="\n")
        written.append(script_path)
    return written


def run_facet(
    spec: FacetSpec,
    script_path: Path,
    workspace: Path,
    artifact_path: Path,
    timeout: float = FACET_SCRIPT_TIMEOUT_SECONDS,
) -> FacetRunResult:
    """Execute one facet script as a real subprocess and collect its artifact."""
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(script_path), str(workspace), str(artifact_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return FacetRunResult(
            name=spec.name,
            ok=False,
            exit_code=None,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            stderr_tail=f"facet script timed out after {timeout}s",
        )
    except OSError as exc:
        return FacetRunResult(
            name=spec.name,
            ok=False,
            exit_code=None,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            stderr_tail=f"facet script could not be launched: {exc}",
        )
    duration_ms = (time.perf_counter() - started) * 1000.0
    artifact_chars = 0
    artifact_sha = ""
    if artifact_path.is_file():
        try:
            content = artifact_path.read_text(encoding="utf-8")
            artifact_chars = len(content)
            artifact_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        except (OSError, UnicodeDecodeError) as exc:
            logger.warning("facet %s artifact unreadable: %s", spec.name, exc)
    ok = proc.returncode == 0 and artifact_chars > 0
    stderr_tail = (proc.stderr or "")[-_STDERR_TAIL_CHARS:]
    if not ok:
        logger.warning(
            "facet %s failed: exit=%s artifact_chars=%d stderr=%r",
            spec.name,
            proc.returncode,
            artifact_chars,
            stderr_tail,
        )
    return FacetRunResult(
        name=spec.name,
        ok=ok,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        artifact_chars=artifact_chars,
        artifact_sha256=artifact_sha,
        stderr_tail=stderr_tail,
    )
