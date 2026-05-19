import dataclasses
import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from benchr.types import Execution


@dataclass
class Measurement:
    execution: Execution
    metric: str
    value: float
    unit: str = ""
    # True = lower is better, False = higher is better, None = not comparable
    lower_is_better: Optional[bool] = None

    @staticmethod
    def runtime(
        execution: Execution,
        value: float,
        unit: str,
    ) -> "Measurement":
        return Measurement(
            execution=execution,
            metric="runtime",
            value=value,
            unit=unit,
        )


@dataclass
class ExecutionResult:
    measurements: list[Measurement] = dataclasses.field(default_factory=list)

    def info_columns(self) -> list[str]:
        """
        Get all info categories on all Executions
        """
        return list(
            dict.fromkeys(col for m in self.measurements for col in m.execution.info)
        )

    def metrics(self) -> list[str]:
        """
        Get all metrics in the result
        """
        return list(dict.fromkeys(m.metric for m in self.measurements))

    def to_data_frame(self, pivoted: bool = False, units: Optional[bool] = None):
        import pandas as pd

        if units is None:
            units = not pivoted

        info_cols = self.info_columns()

        rows = []
        for m in self.measurements:
            row: dict[str, Any] = {
                "benchmark": m.execution.benchmark_name,
                "suite": m.execution.suite,
                "run": m.execution.run,
            }

            for col in info_cols:
                row[col] = m.execution.info.get(col, "")

            row["lower_is_better"] = m.lower_is_better

            if pivoted:
                row[m.metric] = m.value
                if units:
                    row[m.metric + "_unit"] = m.unit
            else:
                row["metric"] = m.metric
                row["value"] = m.value
                if units:
                    row["unit"] = m.unit

            rows.append(row)

        index_cols = ["benchmark", "suite", "run"] + info_cols + ["lower_is_better"]

        df = pd.DataFrame(rows)
        if pivoted:
            df = df.groupby(index_cols).agg(
                lambda x: x.dropna().iloc[0] if len(x.dropna()) > 0 else float("nan")
            )
        else:
            df.set_index(index_cols, inplace=True)
        return df


def execution_result_to_json(result: ExecutionResult) -> str:
    """Serialize an ExecutionResult to a JSON string (indented for diffability)."""
    return json.dumps(_execution_result_to_dict(result), indent=2)


def execution_result_from_json(text: str) -> ExecutionResult:
    """Load an ExecutionResult from a JSON string produced by `execution_result_to_json`."""
    return _dict_to_execution_result(json.loads(text))


def _execution_result_to_dict(result: ExecutionResult) -> dict:
    # Group measurements by execution object to avoid repeating execution data
    grouped: dict[int, tuple[Execution, list[Measurement]]] = {}
    order: list[int] = []
    for m in result.measurements:
        key = id(m.execution)
        if key not in grouped:
            grouped[key] = (m.execution, [])
            order.append(key)
        grouped[key][1].append(m)

    return {
        "executions": [
            {
                "benchmark_name": exc.benchmark_name,
                "suite": exc.suite,
                "command": exc.command,
                "working_directory": str(exc.working_directory),
                "env": exc.env,
                "timeout": exc.timeout,
                "info": exc.info,
                "run": exc.run,
                "measurements": [
                    {
                        "metric": m.metric,
                        "value": m.value,
                        **({"unit": m.unit} if m.unit else {}),
                        **(
                            {"lower_is_better": m.lower_is_better}
                            if m.lower_is_better is not None
                            else {}
                        ),
                    }
                    for m in measurements
                ],
            }
            for exc, measurements in (grouped[k] for k in order)
        ]
    }


def _dict_to_execution_result(d: dict) -> ExecutionResult:
    measurements: list[Measurement] = []
    for ed in d["executions"]:
        execution = Execution(
            benchmark_name=ed["benchmark_name"],
            suite=ed["suite"],
            parser=None,  # loaded results are never re-executed
            command=ed["command"],
            working_directory=Path(ed["working_directory"]),
            env=ed["env"],
            timeout=ed["timeout"],
            info=ed["info"],
            run=ed.get("run", 1),
        )
        for md in ed["measurements"]:
            measurements.append(
                Measurement(
                    execution=execution,
                    metric=md["metric"],
                    value=md["value"],
                    unit=md.get("unit", ""),
                    lower_is_better=md.get("lower_is_better"),
                )
            )
    return ExecutionResult(measurements=measurements)


_META_METRICS = {"failed"}


def extract_unique_names(paths: list[Path]) -> list[str]:
    """
    Extract short, unique display names from a list of file paths.
    Strips extension and any common path prefix/suffix components.
    Example: ['vm1/results.csv', 'vm2/results.csv'] -> ['vm1', 'vm2']
    """
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


MetricKey = tuple[str, str]  # (metric_name, unit)

# A hashable, canonically ordered snapshot of the `execution.info` entries
# that identify one benchmark variant.
#
# Stored as a tuple of ``(key, value)`` pairs sorted by key, so two variants
# with the same logical info compare equal regardless of the original
# insertion order in `Execution.info`. The tuple form is required because
# this value is used as a dict key when grouping/indexing variants for
# summary and comparison (see `group_execution_result`, `compare_and_print`).
VariantInfo = tuple[tuple[str, str], ...]


def _variant_info_of(execution: "Execution") -> VariantInfo:
    """Build the `VariantInfo` identity from an execution's info dict."""
    return tuple(sorted(execution.info.items()))


@dataclass
class BenchmarkRunCounts:
    """Outcome counts for all runs of a single benchmark variant."""

    failures: int  # runs that did not produce a successful result (crashes and timeouts alike)
    successes: int  # runs that produced parsed measurements


@dataclass
class BenchmarkGroup:
    """
    All runs of one benchmark variant, aggregated for summary/comparison.

    A "variant" is a unique (suite, benchmark, info) triple: for a benchmark
    with `.runs(2)` there is one BenchmarkGroup containing values for both
    runs; for a MatrixSuite there is one BenchmarkGroup per matrix cell.
    """

    suite: str
    benchmark: str
    # Info items that disambiguate this variant. See `VariantInfo` for the
    # exact shape and rationale.
    info: VariantInfo
    # Measured values across runs, grouped by (metric, unit).
    metrics: dict[MetricKey, list[float]]
    run_counts: BenchmarkRunCounts


@dataclass
class GroupedResult:
    """
    An ExecutionResult reshaped for summary and comparison.
    """

    name: str  # display label (e.g. JSON filename stem, or "current")
    benchmarks: list[BenchmarkGroup]  # one entry per unique benchmark variant
    # Per-metric direction: True = lower is better, False = higher is better.
    # Metrics absent from this map are not comparable.
    lower_is_better: dict[MetricKey, bool]


def group_execution_result(result: ExecutionResult, name: str) -> GroupedResult:
    """
    Reshape an ExecutionResult into a GroupedResult for comparison/summary.

    Groups measurements by (suite, benchmark, info), folds the
    meta-metric "failed" into run counts, and collects per-metric
    `lower_is_better` annotations.
    """
    # Scratch buckets keyed by (suite, benchmark, info)
    variant_order: list[tuple] = []
    variant_metrics: dict[tuple, dict[MetricKey, list[float]]] = {}
    variant_runs: dict[tuple, set[int]] = {}  # observed run numbers
    variant_fails: dict[tuple, int] = {}
    lower_is_better: dict[MetricKey, bool] = {}

    def remember(identity: tuple) -> None:
        if identity not in variant_metrics:
            variant_metrics[identity] = {}
            variant_order.append(identity)

    for m in result.measurements:
        variant_info = _variant_info_of(m.execution)
        identity = (m.execution.suite, m.execution.benchmark_name, variant_info)
        remember(identity)

        variant_runs.setdefault(identity, set()).add(m.execution.run)

        if m.metric == "failed" and m.value == 1:
            variant_fails[identity] = variant_fails.get(identity, 0) + 1

        if m.metric in _META_METRICS:
            continue

        metric_key = (m.metric, m.unit)
        if m.lower_is_better is not None:
            lower_is_better[metric_key] = m.lower_is_better

        per_variant = variant_metrics[identity]
        per_variant.setdefault(metric_key, []).append(m.value)

    benchmarks: list[BenchmarkGroup] = []
    for identity in variant_order:
        suite, bench, info = identity
        total = len(variant_runs.get(identity, set())) or 1
        failed = variant_fails.get(identity, 0)
        benchmarks.append(
            BenchmarkGroup(
                suite=suite,
                benchmark=bench,
                info=info,
                metrics=variant_metrics[identity],
                run_counts=BenchmarkRunCounts(
                    failures=failed,
                    successes=total - failed,
                ),
            )
        )

    return GroupedResult(
        name=name, benchmarks=benchmarks, lower_is_better=lower_is_better
    )


BenchmarkId = tuple[str, str, VariantInfo]


def scale_unit(mean_val: float, unit: str) -> tuple[float, str]:
    """Choose a human-friendly scale and unit suffix."""
    abs_val = abs(mean_val)
    if unit == "s":
        if 0 < abs_val < 0.001:
            return 1e6, "\u00b5s"
        if 0 < abs_val < 1:
            return 1e3, "ms"
    elif unit == "kB":
        if abs_val >= 1024 * 1024:
            return 1 / (1024 * 1024), "GB"
        if abs_val >= 1024:
            return 1 / 1024, "MB"
    return 1, unit


@dataclass
class MetricStats:
    """Computed statistics for one metric across runs of one benchmark."""

    metric: str
    unit: str
    lower_is_better: Optional[bool]
    n: int
    mean: float
    median: float
    stdev: float  # 0.0 if n < 2
    min: float
    max: float
    values: list[float]


@dataclass
class GroupStats:
    """Computed statistics for one benchmark variant."""

    suite: str
    benchmark: str
    info: VariantInfo
    run_counts: BenchmarkRunCounts
    metrics: dict[MetricKey, MetricStats]


@dataclass
class MetricRatio:
    """Ratio of current vs baseline for one metric on one benchmark.

    Works for any metric direction (runtime, memory, throughput, ...).
    ``display_ratio > 1`` means the current run is better than the baseline.
    """

    metric: str
    unit: str
    lower_is_better: bool
    raw_ratio: float  # current_center / baseline_center
    display_ratio: float  # >1 = current is better
    sigma: float
    baseline_center: float
    baseline_stdev: float
    current_center: float
    current_stdev: float


@dataclass
class GeoMeanRatio:
    """Geometric mean of ``MetricRatio.display_ratio`` across benchmarks."""

    metric: str
    unit: str
    lower_is_better: bool
    display_ratio: float  # >1 = current is better
    sigma: float
    n_benchmarks: int
    runs_per_benchmark: int


@dataclass
class SummaryData:
    """Pre-computed statistics ready for formatting.

    *groups* always describes the current run.  When baselines are supplied,
    *ratios* and *geomeans* contain comparison data.
    """

    groups: list[GroupStats]
    # Comparison data (empty/None when no baselines)
    baseline: Optional[GroupedResult]
    comparees: list[GroupedResult]
    comparee_names: list[str]
    # [comparee_name][BenchmarkId][MetricKey] -> MetricRatio
    ratios: dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]]
    # [suite][comparee_name][MetricKey] -> GeoMeanRatio
    geomeans: dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]]


def _compute_metric_stats(
    values: list[float],
    metric: str,
    unit: str,
    lower_is_better: Optional[bool],
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
        values=list(values),
    )


def _compute_group_stats(
    group: BenchmarkGroup,
    lower_is_better_map: dict[MetricKey, bool],
) -> GroupStats:
    metrics: dict[MetricKey, MetricStats] = {}
    for metric_key, values in group.metrics.items():
        name, unit = metric_key
        lib = lower_is_better_map.get(metric_key)
        metrics[metric_key] = _compute_metric_stats(values, name, unit, lib)
    return GroupStats(
        suite=group.suite,
        benchmark=group.benchmark,
        info=group.info,
        run_counts=group.run_counts,
        metrics=metrics,
    )


def _compute_metric_ratio(
    baseline: MetricStats,
    current: MetricStats,
) -> Optional[MetricRatio]:
    """Compute ratio of current vs baseline for one metric.

    Returns None if either side is uncomparable or has zero/NaN center.
    """
    if baseline.lower_is_better is None or current.lower_is_better is None:
        return None
    lower_is_better = current.lower_is_better

    bl_c = baseline.median
    cur_c = current.median
    bl_sd = baseline.stdev
    cur_sd = current.stdev

    if bl_c == 0 or cur_c == 0 or math.isnan(bl_c) or math.isnan(cur_c):
        return None

    raw = cur_c / bl_c
    display = (bl_c / cur_c) if lower_is_better else raw

    rel_err_sq = 0.0
    if bl_sd > 0:
        rel_err_sq += (bl_sd / bl_c) ** 2
    if cur_sd > 0:
        rel_err_sq += (cur_sd / cur_c) ** 2
    sigma = display * math.sqrt(rel_err_sq)

    return MetricRatio(
        metric=current.metric,
        unit=current.unit,
        lower_is_better=lower_is_better,
        raw_ratio=raw,
        display_ratio=display,
        sigma=sigma,
        baseline_center=bl_c,
        baseline_stdev=bl_sd,
        current_center=cur_c,
        current_stdev=cur_sd,
    )


def _compute_all_ratios(
    baseline: GroupedResult,
    comparee: GroupedResult,
) -> dict[BenchmarkId, dict[MetricKey, MetricRatio]]:
    """Compute per-benchmark, per-metric ratios of comparee vs baseline."""
    all_lib: dict[MetricKey, bool] = {}
    all_lib.update(baseline.lower_is_better)
    all_lib.update(comparee.lower_is_better)

    bl_index = {(g.suite, g.benchmark, g.info): g for g in baseline.benchmarks}
    result: dict[BenchmarkId, dict[MetricKey, MetricRatio]] = {}

    for comp_g in comparee.benchmarks:
        bid: BenchmarkId = (comp_g.suite, comp_g.benchmark, comp_g.info)
        bl_g = bl_index.get(bid)
        if bl_g is None:
            continue

        per_metric: dict[MetricKey, MetricRatio] = {}
        for mk, comp_vals in comp_g.metrics.items():
            if mk not in all_lib:
                continue
            bl_vals = bl_g.metrics.get(mk)
            if not bl_vals:
                continue
            lib = all_lib[mk]
            bl_ms = _compute_metric_stats(bl_vals, mk[0], mk[1], lib)
            comp_ms = _compute_metric_stats(comp_vals, mk[0], mk[1], lib)
            ratio = _compute_metric_ratio(bl_ms, comp_ms)
            if ratio is not None:
                per_metric[mk] = ratio

        if per_metric:
            result[bid] = per_metric

    return result


def geomean_with_sigma(mrs: list[MetricRatio]) -> tuple[float, float]:
    """Compute geometric mean of display_ratios and propagated sigma error."""
    N = len(mrs)
    geo = math.exp(statistics.mean(math.log(mr.display_ratio) for mr in mrs))
    rel_errs_sq: list[float] = []
    for mr in mrs:
        rsq = 0.0
        if mr.baseline_stdev > 0:
            rsq += (mr.baseline_stdev / mr.baseline_center) ** 2
        if mr.current_stdev > 0:
            rsq += (mr.current_stdev / mr.current_center) ** 2
        rel_errs_sq.append(rsq)
    sigma_log = math.sqrt(sum(rel_errs_sq)) / N if rel_errs_sq else 0.0
    return geo, geo * sigma_log


def _compute_geomean_ratios(
    bench_ratios: dict[BenchmarkId, dict[MetricKey, MetricRatio]],
    comparee: GroupedResult,
) -> dict[str, dict[MetricKey, GeoMeanRatio]]:
    """Compute per-suite geometric means. Returns {suite: {mk: GeoMeanRatio}}."""
    comp_index = {(g.suite, g.benchmark, g.info): g for g in comparee.benchmarks}

    by_suite_metric: dict[
        str, dict[MetricKey, list[tuple[BenchmarkId, MetricRatio]]]
    ] = {}
    for bid, metrics in bench_ratios.items():
        suite = bid[0]
        for mk, mr in metrics.items():
            by_suite_metric.setdefault(suite, {}).setdefault(mk, []).append((bid, mr))

    result: dict[str, dict[MetricKey, GeoMeanRatio]] = {}
    for suite, metric_map in by_suite_metric.items():
        result[suite] = {}
        for mk, entries in metric_map.items():
            mrs = [e[1] for e in entries]
            bids = [e[0] for e in entries]
            if any(mr.display_ratio <= 0 for mr in mrs):
                continue

            geo, sigma = geomean_with_sigma(mrs)

            run_counts = set()
            for bid in bids:
                g = comp_index.get(bid)
                if g is not None:
                    run_counts.add(g.run_counts.successes)
            if len(run_counts) != 1:
                raise ValueError(
                    f"Inconsistent run counts across benchmarks in suite "
                    f"{suite!r} for metric {mk[0]!r}: {run_counts}"
                )

            result[suite][mk] = GeoMeanRatio(
                metric=mk[0],
                unit=mk[1],
                lower_is_better=mrs[0].lower_is_better,
                display_ratio=geo,
                sigma=sigma,
                n_benchmarks=len(mrs),
                runs_per_benchmark=run_counts.pop(),
            )

    return result


def _build_comparison_data(
    baseline: GroupedResult,
    comparees: list[GroupedResult],
    comparee_names: list[str],
) -> tuple[
    dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]],
    dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]],
]:
    """Build ratios and geomeans for all comparees vs baseline."""
    all_ratios: dict[str, dict[BenchmarkId, dict[MetricKey, MetricRatio]]] = {}
    all_geomeans: dict[str, dict[str, dict[MetricKey, GeoMeanRatio]]] = {}
    for comp, cname in zip(comparees, comparee_names):
        bench_ratios = _compute_all_ratios(baseline, comp)
        all_ratios[cname] = bench_ratios
        suite_gm = _compute_geomean_ratios(bench_ratios, comp)
        for suite, gm in suite_gm.items():
            all_geomeans.setdefault(suite, {})[cname] = gm
    return all_ratios, all_geomeans


def build_summary_data(
    result: ExecutionResult,
    baseline_paths: list[Path],
) -> SummaryData:
    """Build pre-computed summary statistics from a run and optional baselines."""
    current_grouped = group_execution_result(result, name="current")
    current_stats = [
        _compute_group_stats(g, current_grouped.lower_is_better)
        for g in current_grouped.benchmarks
    ]

    if not baseline_paths:
        return SummaryData(
            groups=current_stats,
            baseline=None,
            comparees=[],
            comparee_names=[],
            ratios={},
            geomeans={},
        )

    names = extract_unique_names(baseline_paths)
    loaded = [execution_result_from_json(p.read_text()) for p in baseline_paths]
    grouped = [group_execution_result(r, n) for r, n in zip(loaded, names)]

    the_baseline = grouped[0]
    comparees = grouped[1:] + [current_grouped]
    comparee_names = names[1:] + ["current"]

    all_ratios, all_geomeans = _build_comparison_data(
        the_baseline, comparees, comparee_names
    )

    return SummaryData(
        groups=current_stats,
        baseline=the_baseline,
        comparees=comparees,
        comparee_names=comparee_names,
        ratios=all_ratios,
        geomeans=all_geomeans,
    )


def build_summary_data_from_grouped(
    datasets: list[GroupedResult],
) -> SummaryData:
    """Build SummaryData from pre-grouped results (for compare_and_print).

    datasets[0] is the baseline, datasets[1:] are comparees.
    """
    baseline = datasets[0]
    comparees = datasets[1:]
    comparee_names = [d.name for d in comparees]

    all_ratios, all_geomeans = _build_comparison_data(
        baseline, comparees, comparee_names
    )

    return SummaryData(
        groups=[],
        baseline=baseline,
        comparees=comparees,
        comparee_names=comparee_names,
        ratios=all_ratios,
        geomeans=all_geomeans,
    )
