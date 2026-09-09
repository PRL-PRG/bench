from __future__ import annotations

from rich.console import Group
from rich.rule import Rule
from rich.table import Table

from bench.console.styling import (
    Cell,
    Span,
    Styling,
    compose_table,
    join_blocks,
)
from bench.core.stats import (
    ByMetricBlock,
    ByMetricStats,
    Statistics,
    compute_by_metric,
    scale_unit,
)
from bench.core.stats.base import BenchKey
from bench.summary.base import (
    MEAN_HEADER_LONG,
    MetricFilterSummary,
    bench_label,
    counts_cell,
    mean_cells,
    mean_cells_raw,
)


class ByMetricSummary(MetricFilterSummary):
    """One-line-per-benchmark plain-text format for commit messages / CI logs."""

    def __init__(
        self,
        metric: str | list[str],
        *,
        suite: str | None = None,
        precision: int = 2,
    ) -> None:
        super().__init__(
            {metric} if isinstance(metric, str) else set(metric), suite=suite
        )
        self._precision = precision

    def __call__(self, stats: Statistics) -> Group:
        return format_by_metric(
            compute_by_metric(self.scoped(stats)),
            Styling.PLAIN,
            precision=self._precision,
        )


def format_by_metric(
    model: ByMetricStats, styling: Styling = Styling.PLAIN, precision: int = 2
) -> Group:
    return join_blocks([_by_metric_block(b, styling, precision) for b in model.blocks])


def _by_metric_block(block: ByMetricBlock, styling: Styling, precision: int) -> Table:
    headers = [
        Cell(Span(block.metric, "metric")),
        Cell(Span("")),  # Variant
        *MEAN_HEADER_LONG,
        Cell(Span("")),  # Count
    ]

    cells = [
        [
            Cell(Span(bench_label(BenchKey(stat.id.suite, stat.id.benchmark)))),
            Cell(Span(stat.id.variant_label, "name")),
            *mean_cells(
                stat,
                scale_unit(stat.mean, stat.metric_key.unit),
                precision,
                extend=True,
            ),
            counts_cell(counts),
        ]
        for stat, counts in block.rows
    ]
    table = compose_table(styling, cells, headers, gap=1)

    if block.geomean is not None:
        table.add_row(Rule())

        geo, sigma = block.geomean
        table.add_row(
            *map(
                styling.collapse_cell,
                [
                    Cell(Span("geomean")),
                    Cell(Span("")),  # variant
                    *mean_cells_raw(geo, sigma, block.scale),
                ],
            )
        )

    return table
