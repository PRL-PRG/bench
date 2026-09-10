"""Report -> Statistics -> view models: the numeric half of the analysis layer.

`summarize(report)` reduces raw runs to a `Statistics` - one `Stat` per
benchmark-variant x metric, holding the samples themselves. Every view is a
`compute_*` query over that flat list returning a model of plain numbers, which
`bench.summary` turns into console output. Comparing report files is
`merge_reports` tagging each file as a `compare` axis and reusing the views.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Literal, NamedTuple

from bench.model.benchmark import Variant, format_variant_pairs
from bench.model.results import Direction, Report, Sample

# ---------------------------------------------------------------------------
# Stat helper models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MetricKey:
    """What was measured, and in what unit."""

    metric: str
    unit: str
    direction: Direction


@dataclass(frozen=True, slots=True)
class BenchKey:
    """Which benchmark, of which suite."""

    suite: str
    benchmark: str


@dataclass(frozen=True, slots=True)
class BenchmarkId:
    """Which benchmark variant a `Stat` belongs to."""

    suite: str
    benchmark: str
    variant: Variant = Variant()
    variant_label: str = ""

    @property
    def bench(self) -> BenchKey:
        return BenchKey(self.suite, self.benchmark)

    def residual(self, axes: tuple[str, ...]) -> Variant:
        """The variant with `axes` projected out - what is folded over when an axis
        is compared."""
        return Variant(tuple((k, v) for k, v in self.variant if k not in axes))


# ---------------------------------------------------------------------------
# Stat
# ---------------------------------------------------------------------------
type StatType = Literal["iteration", "process"]


@dataclass(frozen=True)
class Stat:
    """The samples of one (benchmark variant, metric, source), reduced from a
    Report. Iteration and whole-process samples of the same metric are separate
    `Stat`s, told apart by `type`.

    How many runs produced them is the variant's, not this row's - see
    `Statistics.counts`. Outliers are counted but kept in `values`.
    """

    id: BenchmarkId
    metric_key: MetricKey
    type: StatType

    values: list[float]
    outliers: int = 0

    @cached_property
    def n(self) -> int:
        return len(self.values)

    @cached_property
    def mean(self) -> float:
        return statistics.mean(self.values)

    @cached_property
    def median(self) -> float:
        return statistics.median(self.values)

    @cached_property
    def stdev(self) -> float:
        """0.0 for a single sample, which has no spread."""
        return statistics.stdev(self.values) if self.n >= 2 else 0.0

    @cached_property
    def min(self) -> float:
        return min(self.values)

    @cached_property
    def max(self) -> float:
        return max(self.values)


@dataclass(frozen=True, slots=True)
class Counts:
    """How many runs a variant contributed."""

    runs: int = 0
    failures: int = 0
    warmups: int = 0


@dataclass(frozen=True, slots=True)
class Statistics:
    """A whole report reduced to `Stat`s, with the grouping the views query it by.

    Every filter returns a new `Statistics`; first-seen order is preserved
    throughout, so the report's execution order drives the output order.
    """

    stats: Sequence[Stat] = field(default_factory=list[Stat])
    counts: Mapping[BenchmarkId, Counts] = field(
        default_factory=dict[BenchmarkId, Counts]
    )

    def __iter__(self) -> Iterator[Stat]:
        return (self.stats).__iter__()

    def __len__(self) -> int:
        return self.stats.__len__()

    def __bool__(self) -> bool:
        return bool(self.stats)

    # TODO: try to remove
    @property
    def first(self) -> Stat:
        return self.stats[0]

    def group_by[K: Hashable](self, key: Callable[[Stat], K]) -> dict[K, Statistics]:
        out: dict[K, list[Stat]] = {}
        for s in self.stats:
            out.setdefault(key(s), []).append(s)
        return {k: Statistics(v) for k, v in out.items()}

    def by_benchmark(self) -> dict[BenchKey, Statistics]:
        return self.group_by(lambda s: s.id.bench)

    def by_metric(self) -> dict[MetricKey, Statistics]:
        return self.group_by(lambda s: s.metric_key)


# ---------------------------------------------------------------------------
# Construct the Stat
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Acc:
    """Mutable accumulator for one benchmark variant while walking the executions."""

    runs: int = 0
    warmup_runs: int = 0
    failures: int = 0


def summarize(report: Report) -> Statistics:
    """Reduce a Report to per-(variant, metric, source) `Statistics`.

    One run is one execution. Iteration and whole-process samples both feed the
    stats, as separate rows. A warmup execution - one whose every iteration was
    flagged - contributes no values at all, and neither does a failed one; both
    still count towards the variant's runs (`Statistics.counts`), and a variant
    that only ever failed yields no rows here.
    """
    values: dict[tuple[BenchmarkId, MetricKey, StatType], list[float]] = {}
    outliers: dict[tuple[BenchmarkId, MetricKey, StatType], int] = {}
    accs: dict[BenchmarkId, _Acc] = {}

    def add(id: BenchmarkId, stat_type: StatType, s: Sample) -> None:
        k = (id, MetricKey(s.metric, s.unit, s.direction), stat_type)

        values.setdefault(k, []).append(s.value)
        outliers.setdefault(k, 0)
        if s.extra.get("outlier", False):
            outliers[k] += 1

    for ex in report.executions:
        id = BenchmarkId(
            ex.suite,
            ex.benchmark,
            ex.variant,
            ex.variant_label or format_variant_pairs(ex.variant),
        )
        acc = accs.setdefault(id, _Acc())
        acc.runs += 1

        if ex.is_failure():
            acc.failures += 1
            continue

        measured = False
        for it in ex.iterations:
            if not it.warmup:
                measured = True
                for s in it.samples:
                    add(id, "iteration", s)

        # Whole-process samples - collected only if there are either no
        # iteration samples or at least one of them is not warmup,
        # i.e., all iteration samples are warmup -> run is warmup
        # FIXME: sync when (if ?) iteration/execution warmup split lands
        if ex.iterations and not measured:
            acc.warmup_runs += 1
        else:
            for s in ex.process_samples:
                add(id, "process", s)

    return Statistics(
        [
            Stat(
                id=id,
                metric_key=mk,
                type=typ,
                values=values,
                outliers=outliers[(id, mk, typ)],
            )
            for (id, mk, typ), values in values.items()
        ],
        {
            id: Counts(runs=a.runs, failures=a.failures, warmups=a.warmup_runs)
            for id, a in accs.items()
        },
    )


# ---------------------------------------------------------------------------
# Ratio / geomean math
# ---------------------------------------------------------------------------


class Ratio(NamedTuple):
    display: float
    sigma: float


def ratio(ref: Stat, other: Stat) -> Ratio | None:
    """`(display, sigma)` comparing `other` against `ref` by their medians, where
    `display > 1` means `other` performs better. `None` when either side lacks a
    direction or has a zero/NaN median."""

    if ref.metric_key != other.metric_key:
        raise ValueError(
            f"Ratio between uncomparable metrics: {ref.metric_key} and {other.metric_key}"
        )

    dir = ref.metric_key.direction
    if dir == "uncomparable":
        return None

    rc, oc = ref.median, other.median
    if rc == 0 or oc == 0 or math.isnan(rc) or math.isnan(oc):
        return None

    display = (rc / oc) if dir == "lower better" else (oc / rc)

    rel_sq = 0.0
    if ref.stdev > 0:
        rel_sq += (ref.stdev / rc) ** 2
    if other.stdev > 0:
        rel_sq += (other.stdev / oc) ** 2
    return Ratio(display, display * math.sqrt(rel_sq))


def geomean_ratio(ratios: list[Ratio]) -> Ratio:
    """Geomean of `(display, sigma)` ratios, with propagated absolute sigma."""
    if not ratios:
        return Ratio(1.0, 0.0)

    geo = statistics.geometric_mean(r.display for r in ratios)
    rel = [s / d for d, s in ratios]
    return Ratio(geo, geo * math.sqrt(sum(e * e for e in rel)) / len(ratios))


# ---------------------------------------------------------------------------
# Scale
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scale:
    """A display multiplier and the unit it converts to."""

    factor: float
    unit: str

    def __call__(self, value: float) -> float:
        return value * self.factor


def scale_unit(value: float, unit: str) -> Scale:
    """A human-friendly multiplier and unit string for `value`."""
    a = abs(value)
    if unit == "s":
        if 0 < a < 0.001:
            return Scale(1e6, "µs")
        if 0 < a < 1:
            return Scale(1e3, "ms")
    elif unit == "kB":
        if a >= 1024 * 1024:
            return Scale(1 / (1024 * 1024), "GB")
        if a >= 1024:
            return Scale(1 / 1024, "MB")
    elif unit == "B":
        if a >= 1024**3:
            return Scale(1 / 1024**3, "GB")
        if a >= 1024**2:
            return Scale(1 / 1024**2, "MB")
        if a >= 1024:
            return Scale(1 / 1024, "kB")
    return Scale(1.0, unit)


# ---------------------------------------------------------------------------
# Merge reports
# ---------------------------------------------------------------------------


def merge_reports(named: list[tuple[str, Report]], axis: str = "compare") -> Report:
    """Fold `(name, Report)` pairs into one Report, tagging every execution with
    an extra `axis` dimension set to the report's name, so comparing files is just
    summarizing over that synthetic axis.

    The tag goes first, so the file reads as the outermost dimension."""
    merged = Report()
    for name, report in named:
        for execution in report.executions:
            variant = Variant(((axis, name),) + execution.variant.pairs)
            label = (
                f"{axis}={name}, {execution.variant_label}"
                if execution.variant_label
                else ""
            )
            merged.add(replace(execution, variant=variant, variant_label=label))
    return merged


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Delta:
    """An oriented ratio: `magnitude` always reads >= 1, `better` says which side
    of the comparison it favours."""

    magnitude: float
    sigma: float
    better: bool

    @staticmethod
    def of(display: float, sigma: float) -> Delta:
        if display >= 1:
            return Delta(display, sigma, True)

        return Delta(1.0 / display, sigma / (display**2), False)
