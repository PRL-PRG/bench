"""summary.py core: summarize (the Report->Stat reduction) + analysis math."""

from __future__ import annotations

import math

import re

from bench import Iteration, Report, Execution, Sample
from bench.report.render import RICH
from bench.report.summary import (
    by_axis,
    compact,
    geomean,
    geomean_ratio,
    merge_reports,
    orient,
    ranking,
    ratio,
    results,
    scale_unit,
    summarize,
)


def _strip(lines: list[str]) -> str:
    """Render like console.print: drop bench tags, turn `\\[` back into `[`."""
    text = re.sub(r"\[bench\.[a-z]+\]|\[/\]", "", "\n".join(lines))
    return text.replace("\\[", "[")


def _smp(
    metric: str, value: float, *, unit: str = "s", lower_is_better: bool | None = True
) -> Sample:
    return Sample(
        metric=metric, value=value, unit=unit, lower_is_better=lower_is_better
    )


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
        variant=variant,
        run=run,
        command=("x",),
        failure=failure,
        iterations=[it],
        process_samples=list(process_samples) if process_samples else [],
    )


def _fail(run: int, *, warmup: bool = False) -> Execution:
    return Execution(
        suite="S",
        benchmark="b",
        run=run,
        command=("x",),
        returncode=7,
        failure="boom",
        iterations=[Iteration(failure="boom", warmup=warmup)],
    )


def _only(stats: list, metric: str = "runtime"):
    found = [s for s in stats if s.metric == metric]
    assert len(found) == 1, f"expected one {metric} stat, got {len(found)}"
    return found[0]


# ----- summarize: warmup / process / failures --------------------------------


def test_summarize_excludes_warmup():
    r = Report(
        executions=[
            _run(1, samples=[_smp("runtime", 1.0)], warmup=True),
            _run(2, samples=[_smp("runtime", 0.5)]),
        ]
    )
    s = _only(summarize(r))
    assert s.n == 1 and s.mean == 0.5 and s.runs == 1 and s.warmups == 1


def test_summarize_process_samples_not_counted_as_runs():
    r = Report(
        executions=[
            Execution(
                suite="S",
                benchmark="b",
                run=1,
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
    assert rt.runs == 2 and rt.n == 2  # process sample is not an extra run
    assert rss.runs == 2 and rss.n == 1


def test_summarize_process_only_counts_once():
    r = Report(
        executions=[
            Execution(
                suite="S",
                benchmark="b",
                run=1,
                command=("x",),
                iterations=[],
                process_samples=[_smp("max_rss", 1024.0, unit="kB")],
            )
        ]
    )
    rss = _only(summarize(r), "max_rss")
    assert rss.runs == 1 and rss.n == 1


def test_summarize_warmup_process_samples_excluded():
    r = Report(
        executions=[
            _run(1, warmup=True, process_samples=[_smp("elapsed", 100.0)]),
            _run(2, process_samples=[_smp("elapsed", 10.0)]),
            _run(3, process_samples=[_smp("elapsed", 12.0)]),
        ]
    )
    s = _only(summarize(r), "elapsed")
    assert s.runs == 2 and sorted([s.min, s.max]) == [10.0, 12.0]


def test_summarize_failures_count_into_stat():
    r = Report(executions=[_run(2, samples=[_smp("runtime", 1.0)]), _fail(1)])
    s = _only(summarize(r))
    assert s.runs == 1 and s.failures == 1


def test_summarize_all_failed_yields_no_rows():
    # Behavior change from the old `group`: a fully-failed variant produces no
    # Stat rows (it surfaces in the reporter's Failures block instead).
    assert summarize(Report(executions=[_fail(1), _fail(2)])) == []


def test_summarize_warmup_failure_excluded():
    assert summarize(Report(executions=[_fail(1, warmup=True)])) == []


def test_summarize_outliers_stay_in_stats_but_are_counted():
    r = Report(
        executions=[
            _run(1, samples=[_smp("runtime", 1.0)]),
            _run(2, samples=[_smp("runtime", 1.0)]),
            _run(
                3,
                samples=[
                    Sample(
                        "runtime", 100.0, unit="s", lower_is_better=True, outlier=True
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


def _stat(values: list[float], *, lower_is_better: bool | None = True):
    r = Report(
        executions=[
            _run(i + 1, samples=[_smp("rt", v, lower_is_better=lower_is_better)])
            for i, v in enumerate(values)
        ]
    )
    return _only(summarize(r), "rt")


def test_ratio_lower_is_better_speedup():
    out = ratio(_stat([1.0, 1.0, 1.0]), _stat([0.5, 0.5, 0.5]))
    assert out is not None and abs(out[0] - 2.0) < 1e-9


def test_ratio_higher_is_better():
    out = ratio(
        _stat([100.0], lower_is_better=False), _stat([200.0], lower_is_better=False)
    )
    assert out is not None and abs(out[0] - 2.0) < 1e-9


def test_ratio_zero_returns_none():
    assert ratio(_stat([0.0]), _stat([1.0])) is None


def test_orient_keeps_better_for_ge_one():
    assert orient(2.0, 0.1) == (2.0, 0.1, "better")


def test_orient_flips_sub_one_to_worse():
    mag, sig, word = orient(0.5, 0.1)
    assert abs(mag - 2.0) < 1e-9 and word == "worse" and abs(sig - 0.4) < 1e-9


def test_geomean():
    assert abs(geomean([2.0, 8.0]) - 4.0) < 1e-9


def test_geomean_ratio_propagates_error():
    geo, sigma = geomean_ratio([(2.0, 0.2), (8.0, 1.6)])
    assert abs(geo - 4.0) < 1e-9
    assert abs(sigma - math.sqrt(0.1**2 + 0.2**2) / 2 * geo) < 1e-9


def test_scale_unit_seconds_to_ms():
    assert scale_unit(0.5, "s") == (1e3, "ms")


def test_scale_unit_kb_to_mb():
    sc, unit = scale_unit(2048.0, "kB")
    assert unit == "MB" and abs(sc - 1 / 1024) < 1e-12


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


def test_results_groups_by_benchmark_and_metric():
    out = _strip(results(summarize(_matrix()), RICH))
    assert "S/fib" in out
    assert "elapsed [s]" in out  # literal bracket survives rendering
    assert (
        "matrix" in out and "mean ± σ" in out and "min … max" in out
    )  # column headers
    assert "(3 runs, 0 failed)" in out  # per-row run count
    assert "vm=python3.9" in out


def test_ranking_uses_better_worse_not_lower_higher():
    out = _strip(ranking(summarize(_matrix()), RICH))
    assert "Summary - S/fib" in out
    assert "was" in out and "× better than" in out
    assert "worse" not in out and "lower" not in out and "higher" not in out
    # the run/failed count shares its rendering with the Results line
    assert "runs, 0 failed)" in out


def test_ranking_best_first():
    out = _strip(ranking(summarize(_matrix()), RICH))
    assert out.index("vm=python3.14") < out.index("vm=python3.9")
    assert "2.00" in out  # 2x worse


def test_ranking_skips_single_variant():
    r = Report(
        executions=[
            _run(i, bench="solo", samples=[_smp("elapsed", 1.0)]) for i in (1, 2, 3)
        ]
    )
    assert ranking(summarize(r), RICH) == []


def test_ranking_axis_folds_residual_within_each_benchmark():
    # bench b, matrix vm x a. "fast" is 2x quicker at every a. ranking(axis="vm")
    # folds a (geomean) and compares the vm values within the benchmark.
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
    out = _strip(ranking(summarize(Report(executions=runs)), RICH, axis="vm"))
    assert "Summary (geomean) - vm - S/b" in out  # per-benchmark header
    assert "fast was" in out and "2.00" in out and "× better than" in out
    assert "a=1" not in out and "a=2" not in out  # the a axis is folded away


def test_by_axis_ranks_values_best_first():
    out = _strip(by_axis(summarize(_matrix()), "vm", RICH, metrics={"elapsed"}))
    assert "Summary (geomean) - vm - S" in out
    assert out.index("python3.14") < out.index("python3.9")
    assert "2.00" in out and "× better than" in out


def test_by_axis_missing_is_explicit():
    out = _strip(by_axis(summarize(_matrix()), "nope", RICH))
    assert "not present" in out and "nope" in out


def test_by_axis_ref_pins_reference():
    # Without ref, python3.14 (fastest) is the subject. ref pins python3.9 as the
    # baseline so it becomes the subject and reads as the worse one.
    out = _strip(
        by_axis(summarize(_matrix()), "vm", RICH, metrics={"elapsed"}, ref="python3.9")
    )
    assert "python3.9 was" in out
    assert "× worse than" in out and "2.00" in out


# ----- merge_reports: files become a `compare` axis --------------------------


def test_merge_reports_tags_each_run_with_compare_axis():
    a = Report(executions=[_run(1, bench="fib", samples=[_smp("elapsed", 2.0)])])
    b = Report(executions=[_run(1, bench="fib", samples=[_smp("elapsed", 1.0)])])
    merged = merge_reports([("a", a), ("b", b)])
    assert len(merged.executions) == 2
    assert {dict(run.variant)["compare"] for run in merged.executions} == {"a", "b"}
    # Summarized over the compare axis, the two files rank against each other.
    out = _strip(by_axis(summarize(merged), "compare", RICH, metrics={"elapsed"}))
    assert "Summary (geomean) - compare - S" in out
    assert "× better than" in out and "2.00" in out


def test_merge_reports_keeps_files_distinguishable_in_labels():
    a = Report(executions=[_run(1, samples=[_smp("elapsed", 1.0)])])
    b = Report(executions=[_run(1, samples=[_smp("elapsed", 1.0)])])
    merged = merge_reports([("a", a), ("b", b)])
    out = _strip(results(summarize(merged), RICH))
    assert "compare=a" in out and "compare=b" in out


def test_compact_no_baseline_has_geomean_and_unit():
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
    out = _strip(compact(summarize(r), RICH))
    assert "fib:" in out and "hanoi:" in out
    assert "geomean:" in out and "ms" in out


def test_stat_line_matches_summary_format():
    from bench.report.render import PLAIN
    from bench.report.summary import stat_line

    r = Report(
        executions=[
            Execution(
                suite="S",
                benchmark="b",
                run=i,
                iterations=[Iteration(samples=[Sample("elapsed", float(i), unit="s")])],
            )
            for i in (1, 2, 3)
        ]
    )
    (s,) = summarize(r)
    assert stat_line(s, PLAIN) == "2.00 ± 1.00 s (1.00 … 3.00) (3 runs, 0 failed)"
