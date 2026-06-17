"""Small helpers shared across the benchr codebase."""

from __future__ import annotations

from rich.markup import escape
from rich.traceback import Traceback

from benchr.report.theme import error_console


def print_exception(error: BaseException, *, with_traceback: bool = True) -> None:
    if with_traceback:
        error_console.print(
            Traceback.from_exception(type(error), error, error.__traceback__)
        )
    else:
        error_console.print(f"[benchr.failure]{escape(str(error))}[/]")
