from __future__ import annotations

from collections.abc import Sequence

from rich.console import Group

from bench.console.styling import (
    INDENT,
    Cell,
    Span,
    Styling,
    indented,
    join_blocks,
    num,
)
from bench.core.stats import (
    AxisIssue,
    BenchKey,
    ComparisonBlock,
    ComparisonStats,
    Counts,
    Delta,
    Statistics,
    compute_by_axis,
    compute_ranking,
)
from bench.summary.base import (
    MetricFilterSummary,
    bench_label,
    counts_cell,
)


class ComparisonSummary(MetricFilterSummary):
    """Rank the variants within each benchmark, best first. With `axis`, fold the
    other (residual) variants within each benchmark and compare the values of that
    axis instead (e.g. `ComparisonSummary(axis="vm")`). Several axis names are one composite
    axis, whose values are their combinations (e.g. `axis=["version", "mode"]`).
    `ref` pins one value as baseline."""

    def __init__(
        self,
        metrics: set[str] | None = None,
        *,
        axis: str | Sequence[str] | None = None,
        ref: str | None = None,
    ) -> None:
        super().__init__(metrics)
        self.axis = axis
        self.ref = ref

    def __call__(self, stats: Statistics) -> Group:
        return format_comparison(
            compute_ranking(self.scoped(stats), axis=self.axis, ref=self.ref)
        )


class GeomeanComparisonSummary(MetricFilterSummary):
    """Rank the values of a matrix `axis` by the geometric mean over benchmarks.
    Several axis names are one composite axis, whose values are their
    combinations (e.g. `axis=["version", "mode"]`). `ref` pins one value as the
    baseline instead of the best performer."""

    def __init__(
        self,
        *,
        axis: str | Sequence[str],
        metrics: str | set[str] | None = None,
        ref: str | None = None,
    ) -> None:
        super().__init__({metrics} if isinstance(metrics, str) else metrics)
        self.axis = axis
        self.ref = ref

    def __call__(self, stats: Statistics) -> Group:
        return format_comparison(
            compute_by_axis(self.scoped(stats), axis=self.axis, ref=self.ref)
        )


def format_comparison(model: ComparisonStats, styling: Styling = Styling.RICH) -> Group:
    """Both rankings: the variants within a benchmark, and the values of an axis."""
    return join_blocks(
        [
            *(_issue_line(i, styling) for i in model.issues),
            *(_comparison_block(b, styling) for b in model.blocks),
        ]
    )


def _axis_name(axes: tuple[str, ...]) -> str:
    return ", ".join(axes)


def _issue_line(issue: AxisIssue, styling: Styling) -> str:
    name = _axis_name(issue.axes)
    match issue.reason:
        case "absent":
            why = f"axis {name!r} not present in any benchmark"
        case "incomplete":
            missing = ", ".join(map(repr, issue.missing))
            why = f"axis {name!r} incomplete: {missing} not present"
        case "never_combined":
            why = f"axis {name!r} never combined in one benchmark"
        case "bad_ref":
            why = (
                f"reference axis {issue.ref!r} is not a value of axis {name!r}"
                " - using the best performer"
            )

    return styling.collapse_cell(
        Cell(
            Span(f"Comparison - {name}", "label"),
            Span(" "),
            Span(f"({why})", "warning"),
        )
    )


def _comparison_header(block: ComparisonBlock, styling: Styling) -> str:
    scope = (
        bench_label(block.scope) if isinstance(block.scope, BenchKey) else block.scope
    )
    title = [Span("Comparison", "label")]

    if block.axes:
        title.append(
            Span(
                f" - {_axis_name(block.axes)}",
                "label",
            )
        )

    title.append(Span(f" - {scope}", "label"))

    return styling.collapse_cell(
        Cell(
            *title,
            Span(" ("),
            Span(block.metric, "metric"),
            Span(")"),
        )
    )


def _delta_than_cell(
    delta: Delta,
    comparison: str,
    counts: Counts | None,
    *,
    show_counts: bool,
    p: int = 2,
) -> Cell:
    """`1.43 ± 0.02× worse than <target>` (or `about the same as <target>`): the
    delta, the connective and the target label as one line."""

    if num(delta.magnitude, p) == num(1.0, p):
        return Cell(Span("about the same as "), Span(comparison, "name"))

    counts_spans = []
    if counts is not None:
        counts_spans = [Span(" "), *counts_cell(counts).spans]
    elif show_counts:
        counts_spans = [Span("")]

    word = "better" if delta.better else "worse"
    return Cell(
        Span(num(delta.magnitude, p), "value"),
        *(
            [Span(" ± "), Span(num(delta.sigma, p), "adjustment")]
            if delta.sigma > 0
            else []
        ),
        Span("× "),
        Span(word, word),
        Span(" than "),
        Span(comparison, "name"),
        *(counts_spans),
    )


def _comparison_block(block: ComparisonBlock, styling: Styling) -> Group:
    show_counts = any(e.counts is not None for e in block.entries)

    header = _comparison_header(block, styling)

    subject = styling.collapse_cell(
        Cell(
            Span(" " * INDENT),
            Span(block.subject, "name"),
            Span(" was"),
        )
    )

    comparison = Group(
        *(
            styling.collapse_cell(
                _delta_than_cell(
                    entry.delta, entry.target, entry.counts, show_counts=show_counts
                )
            )
            for entry in block.entries
        )
    )

    return Group(header, subject, indented(comparison))
