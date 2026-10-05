import itertools
from collections.abc import Sequence

from rich.console import Group, RenderableType

from bench.console.styling import Cell, Span, Styling
from bench.model.results import Execution


def format_failures(
    failures: Sequence[Execution], styling: Styling = Styling.RICH
) -> RenderableType:
    if not failures:
        return Group()

    return styling.collapse_cell(
        Cell(
            Span("Failures", "label"),
            *itertools.chain.from_iterable(
                [
                    Span("\n"),
                    *_failure_span(f),
                ]
                for f in failures
            ),
        )
    )


def _failure_span(execution: Execution) -> list[Span]:
    return [
        Span("✗ ", "failure"),
        Span(execution.identifier()),
        Span(" - "),
        Span(execution.failure or "failed", "failure"),
        Span(": "),
        Span(execution.message or "(no output)"),
        # f"[bench.failure]✗[/] {markup_escape(execution.identifier())}"
        # f" - {verdict}: {markup_escape(execution.message) or '(no output)'}"
    ]
