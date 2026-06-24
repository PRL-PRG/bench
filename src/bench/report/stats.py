"""Grouping, statistics, ratios, geometric means.

By default `group(report)` excludes each benchmark variant's warmup runs,
the first `report.warmups[key]` runs, from the groups (and therefore from
stats). Raw outputs (CsvReporter, JsonReporter, DirReporter) keep every run.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from bench.core.execution import Variant
from bench.core.sample import Report, Run, report_from_json


type MetricKey = tuple[str, str]                # (metric, unit)
type BenchKey = tuple[str, str]                 # (suite, benchmark)
type BenchmarkId = tuple[str, str, Variant]     # (suite, benchmark, variant)


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
    metrics: dict[MetricKey, list[float]] = field(default_factory=dict[MetricKey, list[float]])
    run_counts: RunCounts = field(default_factory=RunCounts)


@dataclass(slots=True)
class GroupedReport:
    name: str  # display label (e.g. "current" or a JSON file stem)
    groups: list[BenchmarkGroup]
    lower_is_better: dict[MetricKey, bool]


def group(report: Report, *, name: str = "current",
          include_warmup: bool = False) -> GroupedReport:
    """Reshape a Report for stats/comparison.

    Flattens every Run's Observations per benchmark variant and collects their
    samples by (metric, unit). The first `report.warmups[key]` observations of
    a variant are warmup and excluded by default. A run that failed before
    producing any observation (spawn / zero-delivery) counts as one failure.
    Benchmarks that only ever failed still appear (zero successes).
    """
    groups: dict[BenchmarkId, BenchmarkGroup] = {}  # insertion-ordered
    lib: dict[MetricKey, bool] = {}
    seen: dict[BenchmarkId, int] = {}  # observations seen per variant (for warmup)

    def ensure(r: Run) -> BenchmarkGroup:
        bid: BenchmarkId = (r.suite, r.benchmark, r.variant)
        g = groups.get(bid)
        if g is None:
            g = groups[bid] = BenchmarkGroup(
                suite=r.suite, benchmark=r.benchmark, variant=r.variant)
        if r.variant_label and not g.variant_label:
            g.variant_label = r.variant_label
        return g

    for r in report.runs:
        bid = (r.suite, r.benchmark, r.variant)
        # A run that failed before producing any observation (spawn /
        # zero-delivery) is one failure, never warmup.
        if not r.observations and r.is_failure():
            ensure(r).run_counts.failures += 1
            continue

        warmup = report.warmups.get(r.key(), 0)
        for obs in r.observations:
            idx = seen.get(bid, 0)
            seen[bid] = idx + 1
            if not include_warmup and idx < warmup:
                continue
            g = ensure(r)
            if obs.is_failure():
                g.run_counts.failures += 1
            else:
                g.run_counts.successes += 1
            for s in obs.samples:
                mk = (s.metric, s.unit)
                if s.lower_is_better is not None:
                    lib[mk] = s.lower_is_better
                g.metrics.setdefault(mk, []).append(s.value)

    return GroupedReport(name=name, groups=list(groups.values()),
                         lower_is_better=lib)


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


@dataclass(slots=True)
class GroupStats:
    suite: str
    benchmark: str
    variant: Variant
    variant_label: str
    run_counts: RunCounts
    metrics: dict[MetricKey, MetricStats]


def metric_stats(values: list[float], metric: str, unit: str,
                 lower_is_better: bool | None) -> MetricStats:
    n = len(values)
    return MetricStats(
        metric=metric, unit=unit, lower_is_better=lower_is_better,
        n=n,
        mean=statistics.mean(values),
        median=statistics.median(values),
        stdev=statistics.stdev(values) if n >= 2 else 0.0,
        min=min(values),
        max=max(values),
    )


def group_stats(g: BenchmarkGroup, lib_map: dict[MetricKey, bool]) -> GroupStats:
    return GroupStats(
        suite=g.suite, benchmark=g.benchmark, variant=g.variant,
        variant_label=g.variant_label, run_counts=g.run_counts,
        metrics={
            mk: metric_stats(vs, mk[0], mk[1], lib_map.get(mk))
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
    raw_ratio: float       # current_center / baseline_center
    display_ratio: float   # > 1 means current is better
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
        metric=current.metric, unit=current.unit, lower_is_better=lib,
        raw_ratio=raw, display_ratio=display, sigma=sigma,
        baseline_center=bl_c, baseline_stdev=bl_sd,
        current_center=cur_c, current_stdev=cur_sd,
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
        default_factory=dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]])
    geomeans: dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]] = field(
        default_factory=dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]])


def _all_ratios(
    baseline: GroupedReport, comparee: GroupedReport
) -> dict[BenchmarkId, dict[MetricKey, MetricRatio]]:
    lib: dict[MetricKey, bool] = {}
    lib.update(baseline.lower_is_better)
    lib.update(comparee.lower_is_better)

    bl_index: dict[BenchmarkId, BenchmarkGroup] = {
        (g.suite, g.benchmark, g.variant): g for g in baseline.groups
    }
    out: dict[BenchmarkId, dict[MetricKey, MetricRatio]] = {}
    for cg in comparee.groups:
        bid: BenchmarkId = (cg.suite, cg.benchmark, cg.variant)
        bg = bl_index.get(bid)
        if bg is None:
            continue
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
    comparee: GroupedReport,
) -> dict[str, dict[MetricKey, GeoMeanRatio]]:
    comp_index = {(g.suite, g.benchmark, g.variant): g for g in comparee.groups}
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
                metric=mk[0], unit=mk[1],
                lower_is_better=mrs[0].lower_is_better,
                display_ratio=geo, sigma=sigma,
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
    for c, cname in zip(comparees, comparee_names):
        br = _all_ratios(base, c)
        ratios[cname] = br
        geomeans[cname] = _per_suite_geomean(br, c)

    return SummaryData(
        groups=current_stats,
        baseline=base, comparees=comparees, comparee_names=comparee_names,
        ratios=ratios, geomeans=geomeans,
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
