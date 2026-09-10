import itertools
from collections.abc import Sequence

from rich.console import Group, RenderableType

from bench.console.styling import Cell, Span, Styling
from bench.model.invocation import SPAWN_FAIL_RC, TIMEOUT_RC
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
    if execution.returncode == TIMEOUT_RC:
        verdict = [
            Span(f"timeout (exit {TIMEOUT_RC})", "failure"),
        ]
    elif execution.returncode == SPAWN_FAIL_RC:
        verdict = [
            Span("spawn failed", "failure"),
            Span(": "),
            Span(execution.failure or "unknown"),
        ]
    else:
        verdict = [
            Span(f"exit {execution.returncode}", "failure"),
        ]

    return [
        Span("✗ ", "failure"),
        Span(execution.identifier()),
        Span(" - "),
        *verdict,
        Span(": "),
        Span(execution.message or "(no output)"),
        # f"[bench.failure]✗[/] {markup_escape(execution.identifier())}"
        # f" - {verdict}: {markup_escape(execution.message) or '(no output)'}"
    ]
