"""One stat per benchmark"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from functools import cached_property

from bench.core.stats.base import (
    Counts,
    Ratio,
    Scale,
    Stat,
    Statistics,
    geomean_ratio,
    scale_unit,
)


@dataclass(frozen=True)
class ByMetricBlock:
    """One metric's benchmarks, sorted by name, plus their geomean when they are
    several and every mean is positive.

    Each row is one benchmark variant, paired with that variant's counters."""

    metric: str
    unit: str
    rows: list[tuple[Stat, Counts]]

    @property
    def has_range(self) -> bool:
        return any(s.n >= 2 for s, _ in self.rows)

    @cached_property
    def geomean(self) -> tuple[float, float] | None:
        if len(self.rows) > 1 and all(s.mean > 0 for s, _ in self.rows):
            return geomean_ratio([Ratio(s.mean, s.stdev) for s, _ in self.rows])
        return None

    @cached_property
    def scale(self) -> Scale:
        return scale_unit(
            statistics.mean(map(lambda r: r[0].mean, self.rows)),
            self.unit,
        )


@dataclass(frozen=True, slots=True)
class ByMetricStats:
    blocks: list[ByMetricBlock] = field(default_factory=list[ByMetricBlock])


def compute_by_metric(stats: Statistics) -> ByMetricStats:
    """One row per benchmark per metric - the terse CI / commit-message view."""
    blocks: list[ByMetricBlock] = []
    for mk, grp in stats.by_metric().items():
        ordered = sorted(grp, key=lambda s: s.id.benchmark)
        blocks.append(
            ByMetricBlock(
                metric=mk.metric,
                unit=mk.unit,
                rows=[(s, stats.counts.get(s.id, Counts())) for s in ordered],
            )
        )
    return ByMetricStats(blocks)
