"""Small helpers shared across the bench codebase."""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Best-effort file I/O (read sysfs/proc knobs, write denoise settings). These
# swallow OSError so a missing or unwritable path is never fatal.
# ---------------------------------------------------------------------------


def read_text(path: Path) -> str | None:
    """Read and strip a file, or `None` if it cannot be read."""
    try:
        return path.read_text().strip()
    except OSError:
        return None


def write_text(path: Path, value: str) -> bool:
    """Write `value` (newline-terminated) to a file, returning `False` on failure."""
    try:
        path.write_text(value + "\n")
        return True
    except OSError:
        return False


def to_int(value: str | None) -> int | None:
    """Parse an int, or `None` if `value` is `None` or not an integer."""
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def read_int(path: Path) -> int | None:
    """Read a file and parse it as an int, or `None`."""
    return to_int(read_text(path))


def read_bracketed(path: Path) -> str | None:
    """Read a sysfs multi-choice file (`a [b] c`) and return the selected token
    inside the brackets, or `None`."""
    text = read_text(path)
    if text is None:
        return None
    m = re.search(r"\[(\w+)\]", text)
    return m.group(1) if m else None
