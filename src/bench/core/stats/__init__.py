"""Report -> Statistics -> view models: the numeric half of the analysis layer."""

from bench.core.stats.base import (
    BenchKey,
    BenchmarkId,
    Counts,
    Delta,
    MetricKey,
    Ratio,
    Scale,
    Stat,
    Statistics,
    StatType,
    geomean_ratio,
    merge_reports,
    ratio,
    scale_unit,
    summarize,
)
from bench.core.stats.by_benchmark_metric import (
    ByBenchmarkMetricBlock,
    ByBenchmarkMetricStats,
    ByBenchmarkMetricVariant,
    compute_by_benchmark_metric,
)
from bench.core.stats.by_metric import (
    ByMetricBlock,
    ByMetricStats,
    compute_by_metric,
)
from bench.core.stats.comparison import (
    AxisIssue,
    AxisIssueReason,
    ComparisonBlock,
    ComparisonEntry,
    ComparisonStats,
    compute_by_axis,
    compute_ranking,
)

__all__ = [
    # bench.core.stats.base
    "MetricKey",
    "BenchKey",
    "BenchmarkId",
    "StatType",
    "Stat",
    "Statistics",
    "Scale",
    "Delta",
    "Counts",
    "Ratio",
    "summarize",
    "ratio",
    "geomean_ratio",
    "scale_unit",
    "merge_reports",
    # bench.core.stats.by_benchmark_metric
    "compute_by_benchmark_metric",
    "ByBenchmarkMetricStats",
    "ByBenchmarkMetricBlock",
    "ByBenchmarkMetricVariant",
    # bench.core.stats.comparison
    "compute_ranking",
    "compute_by_axis",
    "ComparisonStats",
    "ComparisonBlock",
    "ComparisonEntry",
    "AxisIssue",
    "AxisIssueReason",
    # bench.core.stats.by_metric
    "compute_by_metric",
    "ByMetricStats",
    "ByMetricBlock",
]
