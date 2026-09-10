from __future__ import annotations

from rich.console import Group

from bench.console.styling import (
    INDENT,
    Cell,
    Span,
    Styling,
    compose_table,
    indented,
    join_blocks,
)
from bench.core.stats import (
    ByBenchmarkMetricBlock,
    ByBenchmarkMetricStats,
    Statistics,
    compute_by_benchmark_metric,
)
from bench.summary.base import (
    MEAN_HEADER_LONG,
    MEAN_HEADER_SHORT,
    MIN_MAX_HEADER,
    Summary,
    bench_label,
    counts_cell,
    mean_cells,
    range_runs_cells,
)


class ByBenchmarkMetricSummary(Summary):
    """Absolute `mean ± σ (min … max)` per benchmark variant."""

    def __call__(self, stats: Statistics) -> Group:
        return format_by_benchmark_metric(compute_by_benchmark_metric(stats))


def format_by_benchmark_metric(
    model: ByBenchmarkMetricStats, styling: Styling = Styling.RICH
) -> Group:
    return join_blocks([_by_benchmark_metric_block(b, styling) for b in model.blocks])


def _by_benchmark_metric_block(
    block: ByBenchmarkMetricBlock, styling: Styling
) -> Group:
    # Header
    group_header = styling.collapse_cell(
        Cell(
            Span(bench_label(block.bench), "label"),
            Span(" ("),
            Span(block.metric, "metric"),
            Span(")"),
        )
    )

    # Table header
    table_headers: list[Cell] = []
    if block.has_labels:
        table_headers.append(Cell(Span("matrix")))

    if block.has_range:
        table_headers.extend(MEAN_HEADER_LONG)
        table_headers.extend(MIN_MAX_HEADER)
    else:
        table_headers.extend(MEAN_HEADER_SHORT)
        table_headers.append(Cell(Span("")))  # Counts

    # Table cells
    cells: list[list[Cell]] = []
    for row in block.rows:
        cells.append(
            [
                *([Cell(Span(row.label))] if block.has_labels else []),
                *mean_cells(row.stat, block.scale, extend=block.has_range),
                *range_runs_cells(row.stat, block.scale),
                counts_cell(row.counts),
            ]
        )

    # Table
    table = indented(
        compose_table(
            styling,
            cells,
            headers=table_headers,
            gap=1,
        )
    )

    # Outliers
    outliers = None
    if block.outliers:
        outliers = styling.collapse_cell(
            Cell(
                Span(" " * INDENT),
                Span(
                    f"!! {block.outliers} statistical outlier(s) in {block.metric} !!",
                    "warning",
                ),
            )
        )

    return Group(group_header, table, *([outliers] if outliers is not None else []))
