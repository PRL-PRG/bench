"""Absolute stats per benchmark and metric, one row per variant"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from bench.core.stats.base import (
    BenchKey,
    Counts,
    Scale,
    Stat,
    Statistics,
    scale_unit,
)


@dataclass(frozen=True, slots=True)
class ByBenchmarkMetricVariant:
    """One variant of the block's benchmark, with that variant's counters."""

    label: str
    stat: Stat
    counts: Counts


@dataclass(frozen=True, slots=True)
class ByBenchmarkMetricBlock:
    """One benchmark's variants for one metric."""

    bench: BenchKey
    metric: str
    unit: str
    rows: list[ByBenchmarkMetricVariant]
    outliers: int

    @property
    def scale(self) -> Scale:
        return scale_unit(
            statistics.mean(map(lambda r: r.stat.mean, self.rows)) if self.rows else 0,
            self.unit,
        )

    @property
    def has_labels(self) -> bool:
        return any(r.label for r in self.rows)

    @property
    def has_range(self) -> bool:
        """A single sample has no spread, so with none of the rows holding two the
        `± σ` and `min … max` columns have nothing to show."""
        return any(r.stat.n >= 2 for r in self.rows)


@dataclass(frozen=True, slots=True)
class ByBenchmarkMetricStats:
    blocks: list[ByBenchmarkMetricBlock] = field(
        default_factory=list[ByBenchmarkMetricBlock]
    )


def compute_by_benchmark_metric(stats: Statistics) -> ByBenchmarkMetricStats:
    """The absolute numbers, one block per (benchmark, metric)."""

    def variant(s: Stat) -> ByBenchmarkMetricVariant:
        # The counters are the variant's, so they come from the Statistics the
        # view was handed rather than from the Stat.
        return ByBenchmarkMetricVariant(
            s.id.variant_label, s, stats.counts.get(s.id, Counts())
        )

    blocks: list[ByBenchmarkMetricBlock] = []
    for bench, grp in stats.by_benchmark().items():
        for mk, rows in grp.by_metric().items():
            blocks.append(
                ByBenchmarkMetricBlock(
                    bench=bench,
                    metric=mk.metric,
                    unit=mk.unit,
                    rows=[variant(s) for s in rows],
                    outliers=sum(s.outliers for s in rows),
                )
            )
    return ByBenchmarkMetricStats(blocks)
