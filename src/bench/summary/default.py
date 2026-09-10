from __future__ import annotations

from bench.summary.base import (
    CompositeSummary,
)
from bench.summary.by_benchmark_metric import ByBenchmarkMetricSummary
from bench.summary.comparison import ComparisonSummary


class DefaultSummary(CompositeSummary):
    """The standard report: ByBenchmarkMetricSummary + ComparisonSummary."""

    def __init__(self) -> None:
        super().__init__(ByBenchmarkMetricSummary(), ComparisonSummary())
