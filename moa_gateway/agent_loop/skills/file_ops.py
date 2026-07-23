"""File operation skills — read, write, list."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Sandbox root — restrict file operations to this directory tree.
# Set to None to disable sandboxing (not recommended in production).
_SANDBOX_ROOT: str | None = os.environ.get(
    "AGENT_SANDBOX_ROOT",
    str(Path.cwd()),
)


def _validate_path(path: str) -> Path:
    """Validate that *path* is within the sandbox root."""
    p = Path(path).resolve()
    if _SANDBOX_ROOT:
        root = Path(_SANDBOX_ROOT).resolve()
        try:
            p.relative_to(root)
        except ValueError:
            raise PermissionError(
                f"Path '{path}' is outside the allowed sandbox root '{root}'"
            )
    return p


async def file_read(path: str) -> str:
    """Read and return the contents of a file.

    Args:
        path: Path to the file to read.

    Returns:
        The file contents as a string.
    """
    logger.info("file_read: %s", path)
    try:
        p = _validate_path(path)
        if not p.exists():
            return f"Error: file not found: {path}"
        if not p.is_file():
            return f"Error: not a file: {path}"
        content = p.read_text(encoding="utf-8", errors="replace")
        # Truncate very large files
        if len(content) > 50_000:
            content = content[:50_000] + "\n... (truncated, file too large)"
        return content
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error reading file: {exc}"


async def file_write(path: str, content: str) -> str:
    """Write content to a file.

    Args:
        path: Path to the file to write.
        content: The content to write.

    Returns:
        A confirmation message with bytes written.
    """
    logger.info("file_write: %s (%d chars)", path, len(content))
    try:
        p = _validate_path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {path}"
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error writing file: {exc}"


async def file_list(directory: str = ".") -> str:
    """List files in a directory.

    Args:
        directory: Path to the directory to list.

    Returns:
        A formatted listing of files and subdirectories.
    """
    logger.info("file_list: %s", directory)
    try:
        p = _validate_path(directory)
        if not p.exists():
            return f"Error: directory not found: {directory}"
        if not p.is_dir():
            return f"Error: not a directory: {directory}"

        entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        lines: list[str] = [f"Contents of {p}:"]
        for entry in entries:
            if entry.is_dir():
                lines.append(f"  [DIR]  {entry.name}/")
            else:
                size = entry.stat().st_size
                lines.append(f"  [FILE] {entry.name} ({size} bytes)")

        if len(entries) == 0:
            lines.append("  (empty directory)")

        return "\n".join(lines)
    except PermissionError as exc:
        return f"Error: {exc}"
    except Exception as exc:  # noqa: BLE001
        return f"Error listing directory: {exc}"
