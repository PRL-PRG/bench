"""Grouping, statistics, ratios, geometric means.

By default `group(report)` excludes warmup iterations (those flagged
`Iteration.warmup`) from the groups, and therefore from stats. Raw outputs
(CsvReporter, JsonReporter, DirReporter) keep every iteration.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from bench.core.execution import Variant
from bench.core.sample import Report, Run, Sample, report_from_json


type MetricKey = tuple[str, str]  # (metric, unit)
type BenchKey = tuple[str, str]  # (suite, benchmark)
type BenchmarkId = tuple[str, str, Variant]  # (suite, benchmark, variant)


# ---------------------------------------------------------------------------
# Grouping
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RunCounts:
    failures: int = 0
    successes: int = 0


@dataclass(slots=True)
class BenchmarkGroup:
    """All measured samples for one benchmark variant, by (metric, unit)."""

    suite: str
    benchmark: str
    variant: Variant
    variant_label: str = ""
    metrics: dict[MetricKey, list[float]] = field(
        default_factory=dict[MetricKey, list[float]]
    )
    # Outliers stay in `metrics` (stats are unchanged); this only counts them.
    outliers: dict[MetricKey, int] = field(default_factory=dict[MetricKey, int])
    run_counts: RunCounts = field(default_factory=RunCounts)


@dataclass(slots=True)
class GroupedReport:
    name: str  # display label (e.g. "current" or a JSON file stem)
    groups: list[BenchmarkGroup]
    lower_is_better: dict[MetricKey, bool]


def group(
    report: Report, *, name: str = "current", include_warmup: bool = False
) -> GroupedReport:
    """Reshape a Report for stats/comparison.

    Flattens every Run's Iterations per benchmark variant and collects their
    samples by (metric, unit), plus each run's whole-process samples. Warmup
    iterations (flagged `Iteration.warmup`) are excluded by default. A run that
    failed before producing any iteration (spawn / zero-delivery) counts as one
    failure. Benchmarks that only ever failed still appear (zero successes).
    """
    groups: dict[BenchmarkId, BenchmarkGroup] = {}  # insertion-ordered
    lib: dict[MetricKey, bool] = {}

    def ensure(r: Run) -> BenchmarkGroup:
        bid: BenchmarkId = (r.suite, r.benchmark, r.variant)
        g = groups.get(bid)
        if g is None:
            g = groups[bid] = BenchmarkGroup(
                suite=r.suite, benchmark=r.benchmark, variant=r.variant
            )
        if r.variant_label and not g.variant_label:
            g.variant_label = r.variant_label
        return g

    def add_sample(g: BenchmarkGroup, s: Sample) -> None:
        mk = (s.metric, s.unit)
        if s.lower_is_better is not None:
            lib[mk] = s.lower_is_better
        g.metrics.setdefault(mk, []).append(s.value)
        if s.outlier:
            g.outliers[mk] = g.outliers.get(mk, 0) + 1

    for r in report.runs:
        # A run that failed before producing any iteration (spawn /
        # zero-delivery) is one failure, never warmup.
        if not r.iterations and r.is_failure():
            ensure(r).run_counts.failures += 1
            continue

        measured = 0
        for it in r.iterations:
            if it.warmup and not include_warmup:
                continue
            g = ensure(r)
            measured += 1
            if it.is_failure():
                g.run_counts.failures += 1
            else:
                g.run_counts.successes += 1
            for s in it.samples:
                add_sample(g, s)

        # Whole-process samples are collected once, never counted as a run —
        # unless there were no measured iterations at all (a process-only
        # benchmark), in which case the session counts as one run.
        if r.process_samples:
            g = ensure(r)
            if measured == 0:
                g.run_counts.successes += 1
            for s in r.process_samples:
                add_sample(g, s)

    return GroupedReport(name=name, groups=list(groups.values()), lower_is_better=lib)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def scale_unit(mean: float, unit: str) -> tuple[float, str]:
    """Choose a human-friendly multiplier and unit string."""
    a = abs(mean)
    if unit == "s":
        if 0 < a < 0.001:
            return 1e6, "µs"
        if 0 < a < 1:
            return 1e3, "ms"
    elif unit == "kB":
        if a >= 1024 * 1024:
            return 1 / (1024 * 1024), "GB"
        if a >= 1024:
            return 1 / 1024, "MB"
    return 1.0, unit


@dataclass(slots=True)
class MetricStats:
    metric: str
    unit: str
    lower_is_better: bool | None
    n: int
    mean: float
    median: float
    stdev: float  # 0.0 if n < 2
    min: float
    max: float
    n_outliers: int = 0  # values flagged by outlier detection (still in stats)


@dataclass(slots=True)
class GroupStats:
    suite: str
    benchmark: str
    variant: Variant
    variant_label: str
    run_counts: RunCounts
    metrics: dict[MetricKey, MetricStats]


def metric_stats(
    values: list[float],
    metric: str,
    unit: str,
    lower_is_better: bool | None,
    n_outliers: int = 0,
) -> MetricStats:
    n = len(values)
    return MetricStats(
        metric=metric,
        unit=unit,
        lower_is_better=lower_is_better,
        n=n,
        mean=statistics.mean(values),
        median=statistics.median(values),
        stdev=statistics.stdev(values) if n >= 2 else 0.0,
        min=min(values),
        max=max(values),
        n_outliers=n_outliers,
    )


def group_stats(g: BenchmarkGroup, lib_map: dict[MetricKey, bool]) -> GroupStats:
    return GroupStats(
        suite=g.suite,
        benchmark=g.benchmark,
        variant=g.variant,
        variant_label=g.variant_label,
        run_counts=g.run_counts,
        metrics={
            mk: metric_stats(vs, mk[0], mk[1], lib_map.get(mk), g.outliers.get(mk, 0))
            for mk, vs in g.metrics.items()
        },
    )


# ---------------------------------------------------------------------------
# Ratios + geomean (baselines comparison)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MetricRatio:
    metric: str
    unit: str
    lower_is_better: bool
    raw_ratio: float  # current_center / baseline_center
    display_ratio: float  # > 1 means current is better
    sigma: float
    baseline_center: float
    baseline_stdev: float
    current_center: float
    current_stdev: float


@dataclass(slots=True)
class GeoMeanRatio:
    metric: str
    unit: str
    lower_is_better: bool
    display_ratio: float
    sigma: float
    n_benchmarks: int
    runs_per_benchmark: int | None  # None = inconsistent across benchmarks


def metric_ratio(baseline: MetricStats, current: MetricStats) -> MetricRatio | None:
    if baseline.lower_is_better is None or current.lower_is_better is None:
        return None
    lib = current.lower_is_better

    bl_c = baseline.median
    cur_c = current.median
    bl_sd = baseline.stdev
    cur_sd = current.stdev

    if bl_c == 0 or cur_c == 0 or math.isnan(bl_c) or math.isnan(cur_c):
        return None

    raw = cur_c / bl_c
    display = (bl_c / cur_c) if lib else raw

    rel_err_sq = 0.0
    if bl_sd > 0:
        rel_err_sq += (bl_sd / bl_c) ** 2
    if cur_sd > 0:
        rel_err_sq += (cur_sd / cur_c) ** 2
    sigma = display * math.sqrt(rel_err_sq)

    return MetricRatio(
        metric=current.metric,
        unit=current.unit,
        lower_is_better=lib,
        raw_ratio=raw,
        display_ratio=display,
        sigma=sigma,
        baseline_center=bl_c,
        baseline_stdev=bl_sd,
        current_center=cur_c,
        current_stdev=cur_sd,
    )


def geomean(xs: list[float]) -> float:
    """Geometric mean of positive values."""
    return math.exp(statistics.mean(math.log(x) for x in xs))


def geomean_with_sigma(mrs: list[MetricRatio]) -> tuple[float, float]:
    """Geometric mean of display_ratio with propagated error."""
    N = len(mrs)
    geo = geomean([mr.display_ratio for mr in mrs])
    rel_errs_sq: list[float] = []
    for mr in mrs:
        r = 0.0
        if mr.baseline_stdev > 0:
            r += (mr.baseline_stdev / mr.baseline_center) ** 2
        if mr.current_stdev > 0:
            r += (mr.current_stdev / mr.current_center) ** 2
        rel_errs_sq.append(r)
    sigma_log = math.sqrt(sum(rel_errs_sq)) / N if rel_errs_sq else 0.0
    return geo, geo * sigma_log


# ---------------------------------------------------------------------------
# SummaryData: pre-computed stats consumed by Formatters
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SummaryData:
    groups: list[GroupStats]
    baseline: GroupedReport | None = None
    comparees: list[GroupedReport] = field(default_factory=list[GroupedReport])
    comparee_names: list[str] = field(default_factory=list[str])
    # Both keyed comparee-first: ratios[comparee][benchmark_id][metric],
    # geomeans[comparee][suite][metric].
    ratios: dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]] = field(
        default_factory=dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]]
    )
    geomeans: dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]] = field(
        default_factory=dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]]
    )
    # Per comparee: the comparee group paired to each baseline benchmark id
    # (keyed baseline-first, matching `ratios`). The shared alignment that both
    # the ratios and the formatter read, so run counts line up across files.
    comparee_group_by_bid: dict[str, dict[BenchmarkId, BenchmarkGroup]] = field(
        default_factory=dict[str, dict[BenchmarkId, BenchmarkGroup]]
    )


def align_groups(
    baseline: GroupedReport, comparee: GroupedReport
) -> list[tuple[BenchmarkGroup, BenchmarkGroup]]:
    """Pair baseline groups with comparee groups for comparison.

    Within each `(suite, benchmark)`: when both sides have exactly one variant,
    pair them regardless of the variant — this is comparing two different
    commands/binaries on the same workload (e.g. `bench compare clox.json
    krikafil.json`, where the command lives in the variant). Otherwise pair by
    exact variant, so matrix variants line up by their parameters.
    """

    def by_bench(
        groups: list[BenchmarkGroup],
    ) -> dict[BenchKey, list[BenchmarkGroup]]:
        out: dict[BenchKey, list[BenchmarkGroup]] = {}
        for g in groups:
            out.setdefault((g.suite, g.benchmark), []).append(g)
        return out

    comparee_by_bench = by_bench(comparee.groups)
    pairs: list[tuple[BenchmarkGroup, BenchmarkGroup]] = []
    for bk, bgs in by_bench(baseline.groups).items():
        cgs = comparee_by_bench.get(bk)
        if not cgs:
            continue
        if len(bgs) == 1 and len(cgs) == 1:
            pairs.append((bgs[0], cgs[0]))
            continue
        cidx = {g.variant: g for g in cgs}
        for bg in bgs:
            cg = cidx.get(bg.variant)
            if cg is not None:
                pairs.append((bg, cg))
    return pairs


def _all_ratios(
    baseline: GroupedReport,
    comparee: GroupedReport,
    pairs: list[tuple[BenchmarkGroup, BenchmarkGroup]],
) -> dict[BenchmarkId, dict[MetricKey, MetricRatio]]:
    """Per-benchmark metric ratios, keyed by the *baseline* group's id so the
    formatter (which iterates baseline groups) can look them up directly."""
    lib: dict[MetricKey, bool] = {}
    lib.update(baseline.lower_is_better)
    lib.update(comparee.lower_is_better)

    out: dict[BenchmarkId, dict[MetricKey, MetricRatio]] = {}
    for bg, cg in pairs:
        bid: BenchmarkId = (bg.suite, bg.benchmark, bg.variant)
        per_metric: dict[MetricKey, MetricRatio] = {}
        for mk, cur_vals in cg.metrics.items():
            if mk not in lib:
                continue
            bl_vals = bg.metrics.get(mk)
            if not bl_vals:
                continue
            bl_ms = metric_stats(bl_vals, mk[0], mk[1], lib[mk])
            cur_ms = metric_stats(cur_vals, mk[0], mk[1], lib[mk])
            r = metric_ratio(bl_ms, cur_ms)
            if r is not None:
                per_metric[mk] = r
        if per_metric:
            out[bid] = per_metric
    return out


def _per_suite_geomean(
    bench_ratios: dict[BenchmarkId, dict[MetricKey, MetricRatio]],
    pairs: list[tuple[BenchmarkGroup, BenchmarkGroup]],
) -> dict[str, dict[MetricKey, GeoMeanRatio]]:
    # bench_ratios is keyed by the baseline bid; map those to the paired
    # comparee group so run counts come from the comparee.
    comp_index = {(bg.suite, bg.benchmark, bg.variant): cg for bg, cg in pairs}
    by_suite: dict[str, dict[MetricKey, list[tuple[BenchmarkId, MetricRatio]]]] = {}
    for bid, m in bench_ratios.items():
        for mk, mr in m.items():
            by_suite.setdefault(bid[0], {}).setdefault(mk, []).append((bid, mr))

    out: dict[str, dict[MetricKey, GeoMeanRatio]] = {}
    for suite, metric_map in by_suite.items():
        out[suite] = {}
        for mk, entries in metric_map.items():
            mrs = [e[1] for e in entries]
            if any(mr.display_ratio <= 0 for mr in mrs):
                continue
            geo, sigma = geomean_with_sigma(mrs)
            run_counts = {
                comp_index[e[0]].run_counts.successes
                for e in entries
                if e[0] in comp_index
            }
            if len(run_counts) != 1:
                # Inconsistent run counts across the benchmarks, skip the
                # aggregated number rather than fabricating one.
                continue
            out[suite][mk] = GeoMeanRatio(
                metric=mk[0],
                unit=mk[1],
                lower_is_better=mrs[0].lower_is_better,
                display_ratio=geo,
                sigma=sigma,
                n_benchmarks=len(mrs),
                runs_per_benchmark=run_counts.pop(),
            )
    return out


def build_summary(
    report: Report | None,
    baselines: list[Path] | None = None,
) -> SummaryData:
    """Build the bundle of pre-computed stats consumed by Formatters.

    `report` is the in-memory run to summarize (the live path), grouped as
    "current" and folded in as an extra comparee. Pass `None` to compare only
    loaded files (the `compare` subcommand): then `baselines[0]` is the baseline
    and the rest are comparees, all named uniformly. Warmup is excluded from
    grouping. Baselines are loaded via report_from_json.
    """
    baselines = baselines or []
    if report is not None:
        current = group(report, name="current")
        current_stats = [
            group_stats(g, current.lower_is_better) for g in current.groups
        ]
    else:
        current = None
        current_stats = []

    if not baselines:
        return SummaryData(groups=current_stats)

    names = _unique_names(baselines)
    loaded = [report_from_json(p.read_text()) for p in baselines]
    grouped = [group(r, name=n) for r, n in zip(loaded, names)]

    base = grouped[0]
    comparees = grouped[1:] + ([current] if current is not None else [])
    comparee_names = names[1:] + (["current"] if current is not None else [])

    ratios: dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]] = {}
    geomeans: dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]] = {}
    comparee_group_by_bid: dict[str, dict[BenchmarkId, BenchmarkGroup]] = {}
    for c, cname in zip(comparees, comparee_names):
        pairs = align_groups(base, c)
        comparee_group_by_bid[cname] = {
            (bg.suite, bg.benchmark, bg.variant): cg for bg, cg in pairs
        }
        br = _all_ratios(base, c, pairs)
        ratios[cname] = br
        geomeans[cname] = _per_suite_geomean(br, pairs)

    return SummaryData(
        groups=current_stats,
        baseline=base,
        comparees=comparees,
        comparee_names=comparee_names,
        ratios=ratios,
        geomeans=geomeans,
        comparee_group_by_bid=comparee_group_by_bid,
    )


def _unique_names(paths: list[Path]) -> list[str]:
    """Shortest distinguishing display names: strip path components shared by
    every input from the front and back, keep what differs."""
    if not paths:
        return []
    if len(paths) == 1:
        return [paths[0].stem]
    parts_list = [list(p.with_suffix("").parts) for p in paths]
    while all(len(p) > 1 for p in parts_list) and len({p[0] for p in parts_list}) == 1:
        for p in parts_list:
            p.pop(0)
    while all(len(p) > 1 for p in parts_list) and len({p[-1] for p in parts_list}) == 1:
        for p in parts_list:
            p.pop()
    return ["/".join(p) for p in parts_list]
