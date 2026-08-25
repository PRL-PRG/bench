from __future__ import annotations

from typing import TYPE_CHECKING

from rich.console import Console
from rich.markup import escape as markup_escape

from bench.console.theme import console
from bench.model.invocation import (
    SPAWN_FAIL_RC,
    TIMEOUT_RC,
)
from bench.model.results import Execution, Report
from bench.report.base import Reporter

if TYPE_CHECKING:
    from bench.summary.formatter import Formatter


class SummaryReporter(Reporter):
    """Summarize the report and render it on `finalize()`.

    Takes a single `Formatter` (compose several with `&`), defaulting to
    `DefaultSummary`. After the formatter output, appends a `Failures:` block
    listing every failed run.
    """

    def __init__(
        self,
        formatter: Formatter | None = None,
        *,
        target_console: Console | None = None,
    ) -> None:
        from bench.summary.formatter import DefaultSummary

        super().__init__()
        self._formatter: Formatter = formatter or DefaultSummary()
        self._console = target_console or console

    def finalize(self, report: Report) -> None:
        from bench.summary.summary import summarize

        out = self._formatter(summarize(report))
        if out:
            self._console.print(out)
        if report.failures:
            self._console.print()
            self._console.print("[bench.label]Failures:[/]")
            for execution in report.failures:
                self._console.print("  " + self._failure_line(execution))

    @staticmethod
    def _failure_line(execution: Execution) -> str:
        if execution.returncode == TIMEOUT_RC:
            verdict = f"[bench.failure]timeout (exit {TIMEOUT_RC})[/]"
        elif execution.returncode == SPAWN_FAIL_RC:
            verdict = (
                f"[bench.failure]spawn failed[/]: {execution.failure or 'unknown'}"
            )
        else:
            verdict = f"[bench.failure]exit {execution.returncode}[/]"
        return (
            f"[bench.failure]✗[/] {markup_escape(execution.identifier())}"
            f" - {verdict}: {markup_escape(execution.message) or '(no output)'}"
        )
