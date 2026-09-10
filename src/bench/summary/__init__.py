"""View models -> console output: the rendering half of the analysis layer."""

from bench.summary.base import (
    MEAN_HEADER_LONG,
    MEAN_HEADER_SHORT,
    MIN_MAX_HEADER,
    CompositeSummary,
    MetricFilterSummary,
    Summary,
    bench_label,
    counts_cell,
    mean_cells,
    mean_cells_raw,
    range_runs_cells,
    stat_line,
)
from bench.summary.by_benchmark_metric import (
    ByBenchmarkMetricSummary,
    format_by_benchmark_metric,
)
from bench.summary.by_metric import (
    ByMetricSummary,
    format_by_metric,
)
from bench.summary.comparison import (
    ComparisonSummary,
    GeomeanComparisonSummary,
    format_comparison,
)
from bench.summary.default import (
    DefaultSummary,
)
from bench.summary.failures import format_failures

__all__ = [
    # bench.summary.base
    "Summary",
    "CompositeSummary",
    "MetricFilterSummary",
    "bench_label",
    "MEAN_HEADER_LONG",
    "MEAN_HEADER_SHORT",
    "MIN_MAX_HEADER",
    "mean_cells",
    "mean_cells_raw",
    "counts_cell",
    "range_runs_cells",
    "stat_line",
    # bench.summary.by_benchmark_metric
    "ByBenchmarkMetricSummary",
    "format_by_benchmark_metric",
    # bench.summary.comparison
    "ComparisonSummary",
    "GeomeanComparisonSummary",
    "format_comparison",
    # bench.summary.by_metric
    "ByMetricSummary",
    "format_by_metric",
    # bench.summary.default
    "DefaultSummary",
    # bench.summary.failures
    "format_failures",
]
