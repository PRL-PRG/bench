"""Report -> Stats -> views: the whole analysis layer.

`summarize(report)` reduces raw runs to a flat `list[Stat]` (one row per
benchmark-variant x metric). Every view - ranking within a benchmark, ranking a
matrix axis - is a small query over that flat list. Comparing report files is
`merge_reports` tagging each file as a `compare` axis and reusing the views.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field, replace

from bench.core.invocation import Variant, format_benchmark
from bench.core.results import Report, Execution, Sample
from bench.report.render import RICH, Cell, Renderer, cell, cells, table, tag

type MetricKey = tuple[str, str]  # (metric, unit)
type BenchKey = tuple[str, str]  # (suite, benchmark)


# ---------------------------------------------------------------------------
# Stat: the reduced unit of data
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Stat:
    """Stats for one (benchmark variant, metric), reduced from a Report.

    `runs`/`failures` are the variant's successful/failed iteration counts (shared
    by every metric of that variant). Outliers are counted but kept in the stats.
    """

    suite: str
    benchmark: str
    variant: Variant
    variant_label: str
    metric: str
    unit: str
    lower_is_better: bool | None
    n: int
    mean: float
    median: float
    stdev: float  # 0.0 when n < 2
    min: float
    max: float
    runs: int
    warmups: int
    failures: int
    outliers: int

    @property
    def bench(self) -> BenchKey:
        return (self.suite, self.benchmark)

    @property
    def mk(self) -> MetricKey:
        return (self.metric, self.unit)


@dataclass(slots=True)
class _Acc:
    """Mutable accumulator for one benchmark variant while walking the executions."""

    variant_label: str = ""
    runs: int = 0
    warmups: int = 0
    failures: int = 0
    values: dict[MetricKey, list[float]] = field(
        default_factory=dict[MetricKey, list[float]]
    )
    outliers: dict[MetricKey, int] = field(default_factory=dict[MetricKey, int])


def summarize(report: Report) -> list[Stat]:
    """Reduce a Report to a flat per-(variant, metric) `list[Stat]`.

    Warmup iterations are excluded from the stats but counted (`Stat.warmups`).
    Iteration and whole-process samples both feed the stats; whole-process
    samples add to the run count only for a process-only execution (no
    iterations). A variant that only ever failed yields no rows here - it
    surfaces in the reporter's Failures block.
    """
    accs: dict[tuple[str, str, Variant], _Acc] = {}  # insertion-ordered
    lib: dict[MetricKey, bool] = {}

    def ensure(r: Execution) -> _Acc:
        key = (r.suite, r.benchmark, r.variant)
        a = accs.get(key)
        if a is None:
            a = accs[key] = _Acc()
        if r.variant_label and not a.variant_label:
            a.variant_label = r.variant_label
        return a

    def add(a: _Acc, s: Sample) -> None:
        mk = (s.metric, s.unit)
        if s.lower_is_better is not None:
            lib[mk] = s.lower_is_better
        a.values.setdefault(mk, []).append(s.value)
        if s.extra.get("outlier", False):
            a.outliers[mk] = a.outliers.get(mk, 0) + 1

    for r in report.executions:
        if not r.iterations and r.is_failure():
            ensure(r).failures += 1
            continue
        measured = 0
        for it in r.iterations:
            a = ensure(r)
            if it.warmup:
                a.warmups += 1
                continue
            measured += 1
            if it.is_failure():
                a.failures += 1
            else:
                a.runs += 1
            for s in it.samples:
                add(a, s)
        # Whole-process samples: collected once, never a run - unless the run had
        # no iterations (process-only -> one run). A run whose only iterations were
        # warmup (measured == 0 with iterations present) is itself warmup.
        if r.process_samples and not (r.iterations and measured == 0):
            a = ensure(r)
            if measured == 0:
                a.runs += 1
            for s in r.process_samples:
                add(a, s)

    out: list[Stat] = []
    for (suite, benchmark, variant), a in accs.items():
        for mk, values in a.values.items():
            out.append(_stat(suite, benchmark, variant, a, mk, values, lib.get(mk)))
    return out


def _stat(
    suite: str,
    benchmark: str,
    variant: Variant,
    a: _Acc,
    mk: MetricKey,
    values: list[float],
    lower_is_better: bool | None,
) -> Stat:
    n = len(values)
    return Stat(
        suite=suite,
        benchmark=benchmark,
        variant=variant,
        variant_label=a.variant_label,
        metric=mk[0],
        unit=mk[1],
        lower_is_better=lower_is_better,
        n=n,
        mean=statistics.mean(values),
        median=statistics.median(values),
        stdev=statistics.stdev(values) if n >= 2 else 0.0,
        min=min(values),
        max=max(values),
        runs=a.runs,
        warmups=a.warmups,
        failures=a.failures,
        outliers=a.outliers.get(mk, 0),
    )


# ---------------------------------------------------------------------------
# Ratio / geomean math (pure functions over Stats / numbers)
# ---------------------------------------------------------------------------


def ratio(ref: Stat, other: Stat) -> tuple[float, float] | None:
    """`(display, sigma)` comparing `other` against `ref` by their medians, where
    `display > 1` means `other` performs better. `None` when either side lacks a
    direction or has a zero/NaN median."""
    if ref.lower_is_better is None or other.lower_is_better is None:
        return None
    rc, oc = ref.median, other.median
    if rc == 0 or oc == 0 or math.isnan(rc) or math.isnan(oc):
        return None
    raw = oc / rc
    display = (rc / oc) if other.lower_is_better else raw
    rel_sq = 0.0
    if ref.stdev > 0:
        rel_sq += (ref.stdev / rc) ** 2
    if other.stdev > 0:
        rel_sq += (other.stdev / oc) ** 2
    return display, display * math.sqrt(rel_sq)


def orient(display: float, sigma: float) -> tuple[float, float, str]:
    """Flip a sub-1 ratio so it always reads >= 1 and pick better/worse. The single
    source of truth for the comparison word."""
    if display >= 1:
        return display, sigma, "better"
    return 1.0 / display, sigma / (display**2), "worse"


def geomean(xs: list[float]) -> float:
    """Geometric mean of positive values."""
    return math.exp(statistics.mean(math.log(x) for x in xs))


def geomean_ratio(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Geomean of `(display, sigma)` ratios, with propagated absolute sigma."""
    if not pairs:
        return 1.0, 0.0
    displays = [d for d, _ in pairs]
    geo = geomean(displays)
    rel = [s / d for d, s in pairs]
    return geo, geo * math.sqrt(sum(e * e for e in rel)) / len(pairs)


def scale_unit(value: float, unit: str) -> tuple[float, str]:
    """A human-friendly multiplier and unit string for `value`."""
    a = abs(value)
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


def group_by[K: Hashable](
    stats: list[Stat], key: Callable[[Stat], K]
) -> dict[K, list[Stat]]:
    """Partition `stats` by `key`, preserving first-seen order."""
    out: dict[K, list[Stat]] = {}
    for s in stats:
        out.setdefault(key(s), []).append(s)
    return out


def merge_reports(named: list[tuple[str, Report]], axis: str = "compare") -> Report:
    """Fold `(name, Report)` pairs into one Report, tagging every execution with
    an extra `axis` dimension set to the report's name, so comparing files is just
    summarizing over that synthetic axis.

    The `axis=name` tag goes first (in the variant tuple, and at the front of a
    preset label) so the file reads as the outermost dimension. An empty label
    stays empty, recomputed later from the now axis-carrying variant."""
    merged = Report()
    for name, report in named:
        for execution in report.executions:
            variant = ((axis, name),) + execution.variant
            label = (
                f"{axis}={name}, {execution.variant_label}"
                if execution.variant_label
                else ""
            )
            merged.add(replace(execution, variant=variant, variant_label=label))
    return merged


# ---------------------------------------------------------------------------
# Views: list[Stat] -> rendered lines. Results is a table. Ranking and axis
# share the "<subject> was / N× better than <target>" sentence form.
# ---------------------------------------------------------------------------


def bench_label(suite: str, benchmark: str) -> str:
    """`suite/benchmark`, collapsing the stutter when the two names match."""
    return format_benchmark(suite, benchmark, ())


def _vlabel(s: Stat) -> str:
    if s.variant_label:
        return s.variant_label
    return ", ".join(f"{k}={v}" for k, v in s.variant)


def _axis_value(s: Stat, axis: str) -> str | None:
    return next((v for k, v in s.variant if k == axis), None)


def _residual(s: Stat, axis: str) -> Variant:
    return tuple((k, v) for k, v in s.variant if k != axis)


def _num(x: float, p: int = 2) -> str:
    return f"{x:.{p}f}"


def _mean_cell(s: Stat, scale: float, unit: str = "", p: int = 2) -> Cell:
    suffix = f" {unit}" if unit else ""
    if s.n < 2:
        return cells((_num(s.mean * scale, p), "value"), (suffix, None))
    return cells(
        (_num(s.mean * scale, p), "value"),
        (" ± ", None),
        (_num(s.stdev * scale, p), "success"),
        (suffix, None),
    )


def _counts_text(
    runs: int, failures: int, *, samples: int | None = None, warmups: int = 0
) -> str:
    """`(N runs, M failed)` count suffix for the summary and ranking lines.
    Sample count appears only when given and different from the run count (one
    run's metric can yield several samples); warmup count only when any were
    discarded."""
    parts: list[str] = []
    if samples is not None and samples != runs:
        parts.append(f"{samples} samples")
    if warmups:
        parts.append(f"{warmups} warmup")
    parts.append(f"{runs} runs")
    parts.append(f"{failures} failed")
    return f"({', '.join(parts)})"


def _range_runs_cell(s: Stat, scale: float) -> Cell:
    """`(min … max) (N runs, M failed)` - the range (dropped for fewer than 2
    samples) then the shared count suffix (see `_counts_text`)."""
    parts: list[tuple[str, str | None]] = []
    if s.n >= 2:
        parts += [
            ("(", None),
            (_num(s.min * scale), "min"),
            (" … ", None),
            (_num(s.max * scale), "max"),
            (") ", None),
        ]
    parts += [(_counts_text(s.runs, s.failures, samples=s.n, warmups=s.warmups), None)]
    return cells(*parts)


def stat_line(s: Stat, r: Renderer = RICH) -> str:
    """One-line `mean ± σ unit (min … max) (N runs, M failed)` for a single Stat,
    matching the final summary. The unit is rendered inline."""
    scale, ushow = scale_unit(s.mean, s.unit)
    row = [_mean_cell(s, scale, ushow), _range_runs_cell(s, scale)]
    return table(r, [row], indent="", gap=1)[0]


def _delta_than_cell(display: float, sigma: float, p: int = 2) -> Cell:
    """`1.43 ± 0.02× worse than` (or `about the same as`): the delta plus the
    connective, so the target label follows in the next column."""
    mag, sig, word = orient(display, sigma)
    if _num(mag, p) == "1.00":
        return cell("about the same as")
    spans: list[tuple[str, str | None]] = [(_num(mag, p), "value")]
    if sig > 0:
        spans += [(" ± ", None), (_num(sig, p), "success")]
    spans += [("× ", None), (word, word), (" than", None)]
    return cells(*spans)


def _join_blocks(blocks: list[list[str]]) -> list[str]:
    out: list[str] = []
    for b in blocks:
        if not b:
            continue
        if out:
            out.append("")
        out.extend(b)
    return out


def _keep(metric: str, metrics: set[str] | None) -> bool:
    return metrics is None or metric in metrics


def _was_block(
    r: Renderer,
    header: str,
    subject: str,
    entries: list[tuple[float, float, str, int, int]],
    *,
    show_runs: bool,
) -> list[str]:
    """`<header>` / `<subject> was` / one `N× better than <target> (runs, failed)`
    line per entry. `entries` are `(display, sigma, target_label, runs, failures)`.
    `display > 1` means the subject is the better one."""
    rows: list[list[Cell]] = []
    for display, sigma, target, runs, failures in entries:
        row = [_delta_than_cell(display, sigma), cell(target, "name")]
        if show_runs:
            row.append(cell(_counts_text(runs, failures)))
        rows.append(row)
    return [
        header,
        "  " + tag(r, "name", subject) + " was",
        *table(r, rows, indent="  ", gap=1),
    ]


# ----- Results: absolute stats per benchmark --------------------------------


def results(
    stats: list[Stat], r: Renderer, *, metrics: set[str] | None = None
) -> list[str]:
    blocks: list[list[str]] = []
    for (suite, bench), grp in group_by(stats, lambda s: s.bench).items():
        for (metric, unit), rows_stats in group_by(grp, lambda s: s.mk).items():
            if not _keep(metric, metrics):
                continue
            scale, ushow = scale_unit(rows_stats[0].mean, unit)
            metric_part = f"{metric} [{ushow}]" if ushow else metric
            header = (
                tag(r, "label", bench_label(suite, bench))
                + "   "
                + tag(r, "metric", metric_part)
            )
            has_labels = any(_vlabel(s) for s in rows_stats)
            col_header = ([cell("matrix")] if has_labels else []) + [
                cell("mean ± σ"),
                cell("min … max"),
            ]
            body: list[list[Cell]] = [col_header]
            for s in rows_stats:
                row = [cell(_vlabel(s))] if has_labels else []
                row += [_mean_cell(s, scale), _range_runs_cell(s, scale)]
                body.append(row)
            block = [header, *table(r, body)]
            n_out = sum(s.outliers for s in rows_stats)
            if n_out:
                block.append(
                    "  "
                    + tag(
                        r,
                        "warning",
                        f"!! {n_out} statistical outlier(s) in {metric} !!",
                    )
                )
            blocks.append(block)
    return _join_blocks(blocks)


# ----- Axis fold: geomean the residual variants, rank the axis values --------


def _axis_missing(r: Renderer, axis: str) -> list[str]:
    return [
        tag(r, "label", f"Summary (geomean) - {axis}")
        + " "
        + tag(r, "warning", f"(axis {axis!r} not present in any benchmark)")
    ]


def _axis_block(
    grp: list[Stat], axis: str, r: Renderer, header: str, *, ref: str | None
) -> list[str]:
    """One `_was_block`: fold `grp` by `axis` (geomean over the residual variants,
    matched pairwise) and compare the axis values best-first, or against `ref` if
    that value is present. Empty when fewer than two axis values line up."""
    # axis value -> {(benchmark, residual variant): Stat}
    byval: dict[str, dict[tuple[str, Variant], Stat]] = {}
    for s in grp:
        v = _axis_value(s, axis)
        assert v is not None
        byval.setdefault(v, {})[(s.benchmark, _residual(s, axis))] = s
    if len(byval) < 2:
        return []
    lib = grp[0].lower_is_better if grp[0].lower_is_better is not None else True
    scores = {v: geomean([st.median for st in m.values()]) for v, m in byval.items()}
    if ref is not None and ref in byval:
        ref_val = ref
    else:
        ref_val = (min if lib else max)(scores, key=lambda v: scores[v])
    ref_map = byval[ref_val]

    entries: list[tuple[float, float, str, int, int]] = []
    for v, m in byval.items():
        if v == ref_val:
            continue
        pairs = [
            p for k, st in m.items() if k in ref_map and (p := ratio(st, ref_map[k]))
        ]
        if not pairs:
            continue
        geo, sig = geomean_ratio(pairs)
        entries.append((geo, sig, v, 0, 0))
    entries.sort(key=lambda e: e[0])  # closest to the reference first
    if not entries:
        return []
    return _was_block(r, header, ref_val, entries, show_runs=False)


# ----- Ranking: variants within a benchmark, best first ---------------------


def ranking(
    stats: list[Stat],
    r: Renderer,
    *,
    metrics: set[str] | None = None,
    axis: str | None = None,
    ref: str | None = None,
) -> list[str]:
    """Per benchmark: rank the variants best-first. With `axis`, instead fold the
    other (residual) variants within each benchmark by geomean and compare the
    values of that axis (e.g. python3.14 vs python3.9)."""
    if axis is not None:
        return _axis_view(
            stats,
            axis,
            r,
            key=lambda s: (s.suite, s.benchmark, s.mk),
            head=lambda s: bench_label(s.suite, s.benchmark),
            metrics=metrics,
            ref=ref,
        )
    blocks: list[list[str]] = []
    for (suite, bench), grp in group_by(stats, lambda s: s.bench).items():
        for (metric, _unit), rows_stats in group_by(grp, lambda s: s.mk).items():
            if not _keep(metric, metrics):
                continue
            ranked = [s for s in rows_stats if s.lower_is_better is not None]
            if len(ranked) < 2:
                continue
            lib = ranked[0].lower_is_better
            ranked.sort(key=lambda s: s.median, reverse=not lib)
            best = ranked[0]
            header = (
                tag(r, "label", f"Summary - {bench_label(suite, bench)}")
                + " ("
                + tag(r, "metric", metric)
                + ")"
            )
            entries: list[tuple[float, float, str, int, int]] = []
            for s in ranked[1:]:
                rr = ratio(s, best)  # best relative to s -> reads "better"
                if rr is None:
                    continue
                entries.append((rr[0], rr[1], _vlabel(s), s.runs, s.failures))
            if entries:
                blocks.append(
                    _was_block(r, header, _vlabel(best), entries, show_runs=True)
                )
    return _join_blocks(blocks)


def _axis_view(
    stats: list[Stat],
    axis: str,
    r: Renderer,
    *,
    key: Callable[[Stat], Hashable],
    head: Callable[[Stat], str],
    metrics: set[str] | None = None,
    ref: str | None = None,
) -> list[str]:
    """Shared engine for the axis views: group axial stats by `key`, then emit one
    `_axis_block` per group headed by `head`. Drives both the per-benchmark
    ranking-by-axis and the per-suite `by_axis`."""
    axial = [s for s in stats if _axis_value(s, axis) is not None]
    if not axial:
        return _axis_missing(r, axis)
    blocks: list[list[str]] = []
    for grp in group_by(axial, key).values():
        metric_ = grp[0].metric
        if not _keep(metric_, metrics):
            continue
        header = (
            tag(r, "label", f"Summary (geomean) - {axis} - {head(grp[0])}")
            + " ("
            + tag(r, "metric", metric_)
            + ")"
        )
        blocks.append(_axis_block(grp, axis, r, header, ref=ref))
    return _join_blocks(blocks)


# ----- By axis: geomean over benchmarks, rank the axis values ----------------


def by_axis(
    stats: list[Stat],
    axis: str,
    r: Renderer,
    *,
    metrics: set[str] | None = None,
    ref: str | None = None,
) -> list[str]:
    return _axis_view(
        stats,
        axis,
        r,
        key=lambda s: (s.suite, s.mk),
        head=lambda s: s.suite,
        metrics=metrics,
        ref=ref,
    )


# ----- Compact: terse plain-text for CI / commit messages -------------------


def compact(
    stats: list[Stat],
    r: Renderer,
    *,
    metrics: set[str] | None = None,
    precision: int = 2,
) -> list[str]:
    blocks: list[list[str]] = []
    for (metric, unit), grp in group_by(stats, lambda s: s.mk).items():
        if not _keep(metric, metrics):
            continue
        scale, ushow = scale_unit(grp[0].mean, unit)
        n = grp[0].runs
        header = f"{metric}   mean ± σ ({n} {'run' if n == 1 else 'runs'})"
        ordered = sorted(grp, key=lambda s: s.benchmark)
        rows = [
            [cell(f"{s.benchmark}:"), _mean_cell(s, scale, ushow, precision)]
            for s in ordered
        ]
        if len(ordered) > 1 and all(s.mean > 0 for s in ordered):
            geo, sig = geomean_ratio([(s.mean, s.stdev) for s in ordered])
            usuffix = f" {ushow}" if ushow else ""
            if ordered[0].n >= 2:
                gcell = cells(
                    (_num(geo * scale, precision), "value"),
                    (" ± ", None),
                    (_num(sig * scale, precision), "success"),
                    (usuffix, None),
                )
            else:
                gcell = cells((_num(geo * scale, precision), "value"), (usuffix, None))
            rows.append([cell("geomean:"), gcell])
        blocks.append([tag(r, None, header), *table(r, rows, indent="")])
    return _join_blocks(blocks)
