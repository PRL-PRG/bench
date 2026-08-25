"""User-facing errors and their rendering."""

from __future__ import annotations

from rich.markup import escape
from rich.traceback import Traceback

from bench.console.theme import error_console


class BenchError(Exception):
    """A user-facing error: reported as a clean stderr message, no traceback."""

    def __init__(self, *args: object, exit_code: int = 1) -> None:
        super().__init__(*args)
        self.exit_code = exit_code


def print_exception(error: BaseException, *, with_traceback: bool = True) -> None:
    if with_traceback:
        error_console.print(
            Traceback.from_exception(type(error), error, error.__traceback__)
        )
    else:
        error_console.print(f"[bench.failure]{escape(str(error))}[/]")
