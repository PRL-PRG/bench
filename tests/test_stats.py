"""stats core: summarize (the Report->Stat reduction) + analysis math."""

from __future__ import annotations

import math
from io import StringIO

from rich.console import Console, RenderableType

from bench import Execution, Iteration, Report, Sample
from bench.console.styling import Styling
from bench.console.theme import BENCHR_THEME
from bench.core.stats import (
    Counts,
    Delta,
    Ratio,
    Scale,
    Statistics,
    compute_by_axis,
    compute_by_benchmark_metric,
    compute_by_metric,
    compute_ranking,
    geomean_ratio,
    merge_reports,
    ratio,
    scale_unit,
    summarize,
)
from bench.model.benchmark import Variant
from bench.model.results import Direction
from bench.summary import (
    format_by_benchmark_metric,
    format_by_metric,
    format_comparison,
    stat_line,
)


def _render(renderable: RenderableType) -> str:
    """The text a real console would print - the views return renderables now, so
    layout (and any markup the theme swallows) only shows after rendering."""
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=200, theme=BENCHR_THEME).print(
        renderable
    )
    return buf.getvalue()


def _smp(
    metric: str, value: float, *, unit: str = "s", direction: Direction = "lower better"
) -> Sample:
    return Sample(metric=metric, value=value, unit=unit, direction=direction)


def _run(
    run: int = 1,
    *,
    failure: str | None = None,
    bench: str = "b",
    suite: str = "S",
    variant: tuple[tuple[str, str], ...] = (),
    samples: list[Sample] | None = None,
    warmup: bool = False,
    process_samples: list[Sample] | None = None,
) -> Execution:
    it = Iteration(samples=list(samples) if samples else [], warmup=warmup)
    return Execution(
        suite=suite,
        benchmark=bench,
        variant=Variant(tuple(variant)),
        run=run,
        runtime=0.5,
        command=("x",),
        failure=failure,
        iterations=[it],
        process_samples=list(process_samples) if process_samples else [],
    )


def _fail(run: int, *, warmup: bool = False) -> Execution:
    # `Iteration` no longer carries a failure - the Execution does, and a failed
    # run yields no iterations at all.
    return Execution(
        suite="S",
        benchmark="b",
        run=run,
        runtime=0.0,
        command=("x",),
        returncode=7,
        failure="boom",
        iterations=[Iteration(warmup=True)] if warmup else [],
    )


def _only(stats: Statistics, metric: str = "runtime"):
    found = [s for s in stats if s.metric_key.metric == metric]
    assert len(found) == 1, f"expected one {metric} stat, got {len(found)}"
    return found[0]


def _metric(stats: Statistics, metric: str) -> Statistics:
    """Narrowing to one metric is the caller's job - the same comprehension the
    summaries do in `MetricFilterSummary.scoped`, counters carried along."""
    return Statistics([s for s in stats if s.metric_key.metric == metric], stats.counts)


def _counts(stats: Statistics, metric: str = "runtime") -> Counts:
    """The counters of the variant behind `metric`'s stat. They live on the
    `Statistics`, keyed by variant, since every metric shares the same runs."""
    return stats.counts[_only(stats, metric).id]


# ----- summarize: warmup / process / failures --------------------------------


def test_summarize_excludes_warmup():
    # A warmup execution contributes no values but is still a run, so
    # `warmup_runs` counts a subset of `runs` rather than sitting beside it.
    r = Report(
        executions=[
            _run(1, samples=[_smp("runtime", 1.0)], warmup=True),
            _run(2, samples=[_smp("runtime", 0.5)]),
        ]
    )
    stats = summarize(r)
    s = _only(stats)
    assert s.n == 1 and s.mean == 0.5
    assert _counts(stats) == Counts(runs=2, failures=0, warmups=1)


def test_summarize_process_samples_not_counted_as_runs():
    r = Report(
        executions=[
            Execution(
                suite="S",
                benchmark="b",
                run=1,
                runtime=0.5,
                command=("x",),
                iterations=[
                    Iteration(samples=[_smp("runtime", 1.0)]),
                    Iteration(samples=[_smp("runtime", 2.0)]),
                ],
                process_samples=[_smp("max_rss", 2048.0, unit="kB")],
            )
        ]
    )
    stats = summarize(r)
    rt = _only(stats, "runtime")
    rss = _only(stats, "max_rss")
    assert rt.n == 2 and rss.n == 1
    # One execution is one run however many samples it yields, and one set of
    # counters serves both metrics of the variant.
    assert rt.id == rss.id
    assert _counts(stats, "runtime") == Counts(runs=1, failures=0)


def test_summarize_process_only_counts_once():
    r = Report(
        executions=[
            Execution(
                suite="S",
                benchmark="b",
                run=1,
                runtime=0.5,
                command=("x",),
                iterations=[],
                process_samples=[_smp("max_rss", 1024.0, unit="kB")],
            )
        ]
    )
    stats = summarize(r)
    assert _only(stats, "max_rss").n == 1
    assert _counts(stats, "max_rss") == Counts(runs=1, failures=0)


def test_summarize_warmup_process_samples_excluded():
    r = Report(
        executions=[
            _run(1, warmup=True, process_samples=[_smp("elapsed", 100.0)]),
            _run(2, process_samples=[_smp("elapsed", 10.0)]),
            _run(3, process_samples=[_smp("elapsed", 12.0)]),
        ]
    )
    stats = summarize(r)
    s = _only(stats, "elapsed")
    # The warmup execution's process sample is dropped, but its run is counted.
    assert _counts(stats, "elapsed") == Counts(runs=3, failures=0, warmups=1)
    assert sorted([s.min, s.max]) == [10.0, 12.0]


def test_summarize_failures_count_into_the_variant_counters():
    r = Report(executions=[_run(2, samples=[_smp("runtime", 1.0)]), _fail(1)])
    stats = summarize(r)
    assert _only(stats).n == 1
    # Like warmups, failures are a subset of the runs attempted.
    assert _counts(stats) == Counts(runs=2, failures=1)


def test_summarize_all_failed_yields_no_rows():
    # Behavior change from the old `group`: a fully-failed variant produces no
    # Stat rows (it surfaces in the reporter's Failures block instead).
    assert not summarize(Report(executions=[_fail(1), _fail(2)]))


def test_summarize_warmup_failure_excluded():
    assert not summarize(Report(executions=[_fail(1, warmup=True)]))


def test_summarize_outliers_stay_in_stats_but_are_counted():
    r = Report(
        executions=[
            _run(1, samples=[_smp("runtime", 1.0)]),
            _run(2, samples=[_smp("runtime", 1.0)]),
            _run(
                3,
                samples=[
                    Sample(
                        "runtime",
                        100.0,
                        unit="s",
                        direction="lower better",
                        extra={"outlier": True},
                    )
                ],
            ),
        ]
    )
    s = _only(summarize(r))
    assert s.n == 3 and s.max == 100.0 and s.outliers == 1


# ----- stat values -----------------------------------------------------------


def test_stat_basic():
    s = _only(
        summarize(
            Report(
                executions=[
                    _run(i, samples=[_smp("runtime", float(i))]) for i in (1, 2, 3)
                ]
            )
        )
    )
    assert s.n == 3 and s.mean == 2.0 and s.median == 2.0
    assert s.min == 1.0 and s.max == 3.0


def test_stat_single_value_zero_stdev():
    s = _only(summarize(Report(executions=[_run(1, samples=[_smp("runtime", 5.0)])])))
    assert s.stdev == 0.0


# ----- math ------------------------------------------------------------------


def _stat(values: list[float], *, direction: Direction = "lower better"):
    r = Report(
        executions=[
            _run(i + 1, samples=[_smp("rt", v, direction=direction)])
            for i, v in enumerate(values)
        ]
    )
    return _only(summarize(r), "rt")


def test_ratio_lower_is_better_speedup():
    out = ratio(_stat([1.0, 1.0, 1.0]), _stat([0.5, 0.5, 0.5]))
    assert out is not None and abs(out[0] - 2.0) < 1e-9


def test_ratio_higher_is_better():
    out = ratio(
        _stat([100.0], direction="higher better"),
        _stat([200.0], direction="higher better"),
    )
    assert out is not None and abs(out[0] - 2.0) < 1e-9


def test_ratio_zero_returns_none():
    assert ratio(_stat([0.0]), _stat([1.0])) is None


def test_delta_keeps_better_for_ge_one():
    assert Delta.of(2.0, 0.1) == Delta(2.0, 0.1, True)


def test_delta_flips_sub_one_to_worse():
    d = Delta.of(0.5, 0.1)
    assert abs(d.magnitude - 2.0) < 1e-9 and not d.better
    assert abs(d.sigma - 0.4) < 1e-9


def test_geomean_ratio_propagates_error():
    geo, sigma = geomean_ratio([Ratio(2.0, 0.2), Ratio(8.0, 1.6)])
    assert abs(geo - 4.0) < 1e-9
    assert abs(sigma - math.sqrt(0.1**2 + 0.2**2) / 2 * geo) < 1e-9


def test_scale_unit_seconds_to_ms():
    assert scale_unit(0.5, "s") == Scale(1e3, "ms")


def test_scale_unit_kb_to_mb():
    sc = scale_unit(2048.0, "kB")
    assert sc.unit == "MB" and abs(sc.factor - 1 / 1024) < 1e-12


def test_scale_unit_bytes_step_up_by_1024():
    # A raw-byte counter climbs kB -> MB -> GB; below 1 kiB it stays in "B".
    assert scale_unit(512.0, "B") == Scale(1.0, "B")
    for value, exponent, unit in [
        (4096.0, 1, "kB"),
        (5 * 1024**2, 2, "MB"),
        (3 * 1024**3, 3, "GB"),
    ]:
        sc = scale_unit(value, "B")
        assert sc.unit == unit and abs(sc.factor - 1 / 1024**exponent) < 1e-18


# ----- views -----------------------------------------------------------------


def _matrix() -> Report:
    """vm x {fib, hanoi}: python3.14 is uniformly 2x faster."""
    runs = []
    for vm, fib, hanoi in [("python3.9", 2.0, 4.0), ("python3.14", 1.0, 2.0)]:
        for i in (1, 2, 3):
            runs.append(
                _run(
                    i,
                    bench="fib",
                    variant=(("vm", vm),),
                    samples=[_smp("elapsed", fib)],
                )
            )
            runs.append(
                _run(
                    i,
                    bench="hanoi",
                    variant=(("vm", vm),),
                    samples=[_smp("elapsed", hanoi)],
                )
            )
    return Report(executions=runs)


def test_by_benchmark_metric_groups_by_benchmark_and_metric():
    # No execution here sets variant_label, so the rows are named from the
    # variant pairs alone.
    out = _render(
        format_by_benchmark_metric(compute_by_benchmark_metric(summarize(_matrix())))
    )
    assert "S/fib" in out
    assert "elapsed" in out
    # column headers, each its own table column - padding sits between them
    assert "matrix" in out and "mean" in out and "± σ" in out
    assert "min" in out and "… max" in out
    assert "(3 runs)" in out  # per-row run count
    assert "vm=python3.9" in out


def test_by_benchmark_metric_single_sample_header_is_value_not_mean_sigma():
    # One run per variant -> no spread: the mean cell is a bare value and the
    # range cell is just the count, so the headers must be "value" (not
    # "mean ± σ") and the "min … max" header must be gone entirely.
    report = Report(
        executions=[
            _run(0, bench="b", variant=(("vm", "x"),), samples=[_smp("elapsed", 520.0)])
        ]
    )
    out = _render(
        format_by_benchmark_metric(compute_by_benchmark_metric(summarize(report)))
    )
    assert "value" in out
    assert "mean" not in out
    assert "min" not in out and "max" not in out
    assert "(1 runs)" in out


def test_ranking_uses_better_worse_not_lower_higher():
    out = _render(format_comparison(compute_ranking(summarize(_matrix()))))
    assert "Comparison - S/fib" in out
    assert "was" in out and "× better than" in out
    assert "worse" not in out and "lower" not in out and "higher" not in out
    # the run count shares its rendering with the by-benchmark line
    assert "(3 runs)" in out


def test_ranking_best_first():
    out = _render(format_comparison(compute_ranking(summarize(_matrix()))))
    assert out.index("vm=python3.14") < out.index("vm=python3.9")
    assert "2.00" in out  # 2x worse


def test_ranking_skips_single_variant():
    r = Report(
        executions=[
            _run(i, bench="solo", samples=[_smp("elapsed", 1.0)]) for i in (1, 2, 3)
        ]
    )
    assert format_comparison(compute_ranking(summarize(r))).renderables == []


def test_ranking_axis_folds_residual_within_each_benchmark():
    # bench b, matrix vm x a. "fast" is 2x quicker at every a.
    # compute_ranking(axis="vm") folds a (geomean) and compares the vm values
    # within the benchmark.
    runs = []
    for vm, base in [("fast", 1.0), ("slow", 2.0)]:
        for a in ("1", "2"):
            for i in (1, 2, 3):
                runs.append(
                    _run(
                        i,
                        variant=(("vm", vm), ("a", a)),
                        samples=[_smp("elapsed", base)],
                    )
                )
    out = _render(
        format_comparison(
            compute_ranking(summarize(Report(executions=runs)), axis="vm")
        )
    )
    assert "Comparison - vm - S/b" in out  # per-benchmark header
    assert "fast was" in out and "2.00" in out and "× better than" in out
    assert "a=1" not in out and "a=2" not in out  # the a axis is folded away


def test_by_axis_ranks_values_best_first():
    elapsed = _metric(summarize(_matrix()), "elapsed")
    out = _render(format_comparison(compute_by_axis(elapsed, axis="vm")))
    assert "Comparison - vm - S" in out
    assert out.index("python3.14") < out.index("python3.9")
    assert "2.00" in out and "× better than" in out


def test_by_axis_missing_is_explicit():
    out = _render(format_comparison(compute_by_axis(summarize(_matrix()), axis="nope")))
    assert "not present" in out and "nope" in out


def test_by_axis_ref_pins_reference():
    # Without ref, python3.14 (fastest) is the subject. ref pins python3.9 as the
    # baseline so it becomes the subject and reads as the worse one.
    elapsed = _metric(summarize(_matrix()), "elapsed")
    out = _render(format_comparison(compute_by_axis(elapsed, axis="vm", ref="python3.9")))
    assert "python3.9 was" in out
    assert "× worse than" in out and "2.00" in out


# ----- merge_reports: files become a `compare` axis --------------------------


def test_merge_reports_tags_each_run_with_compare_axis():
    a = Report(executions=[_run(1, bench="fib", samples=[_smp("elapsed", 2.0)])])
    b = Report(executions=[_run(1, bench="fib", samples=[_smp("elapsed", 1.0)])])
    merged = merge_reports([("a", a), ("b", b)])
    assert len(merged.executions) == 2
    assert {run.variant.as_dict()["compare"] for run in merged.executions} == {"a", "b"}
    # Summarized over the compare axis, the two files rank against each other.
    elapsed = _metric(summarize(merged), "elapsed")
    out = _render(format_comparison(compute_by_axis(elapsed, axis="compare")))
    assert "Comparison - compare - S" in out
    assert "× better than" in out and "2.00" in out


def test_merge_reports_keeps_files_distinguishable_in_labels():
    # merge_reports leaves the label empty for an unlabelled execution, so the
    # `compare=<file>` tag has to come out of the synthetic axis itself.
    a = Report(executions=[_run(1, samples=[_smp("elapsed", 1.0)])])
    b = Report(executions=[_run(1, samples=[_smp("elapsed", 1.0)])])
    merged = merge_reports([("a", a), ("b", b)])
    out = _render(
        format_by_benchmark_metric(compute_by_benchmark_metric(summarize(merged)))
    )
    assert "compare=a" in out and "compare=b" in out


def test_by_metric_no_baseline_has_geomean_and_unit():
    r = Report(
        executions=[
            *[
                _run(i, bench="fib", samples=[_smp("elapsed", v)])
                for i, v in enumerate((0.22, 0.23, 0.24), 1)
            ],
            *[
                _run(i, bench="hanoi", samples=[_smp("elapsed", v)])
                for i, v in enumerate((0.39, 0.40, 0.41), 1)
            ],
        ]
    )
    out = _render(format_by_metric(compute_by_metric(summarize(r)), Styling.RICH))
    assert "S/fib" in out and "S/hanoi" in out
    assert "geomean" in out and "ms" in out


def test_stat_line_matches_by_benchmark_metric_format():
    r = Report(
        executions=[
            Execution(
                suite="S",
                benchmark="b",
                run=i,
                runtime=float(i),
                iterations=[Iteration(samples=[Sample("elapsed", float(i), unit="s")])],
            )
            for i in (1, 2, 3)
        ]
    )
    stats = summarize(r)
    (s,) = stats
    assert stat_line(stats, s, Styling.PLAIN) == "2.00 ± 1.00 s (1.00 … 3.00) (3 runs)"
