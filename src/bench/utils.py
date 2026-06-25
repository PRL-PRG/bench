"""Small helpers shared across the bench codebase."""

from __future__ import annotations

from pathlib import Path

from rich.markup import escape
from rich.traceback import Traceback

from bench.report.theme import error_console


def print_exception(error: BaseException, *, with_traceback: bool = True) -> None:
    if with_traceback:
        error_console.print(
            Traceback.from_exception(type(error), error, error.__traceback__)
        )
    else:
        error_console.print(f"[bench.failure]{escape(str(error))}[/]")


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
    """Write `value` (newline-terminated) to a file; `False` on failure."""
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
