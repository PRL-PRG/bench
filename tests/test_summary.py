"""Summary output: the rendered tables for each view."""

from io import StringIO

import pytest
from rich.console import Console, RenderableType

from bench import (
    BenchError,
    ByBenchmarkMetricSummary,
    ByMetricSummary,
    ComparisonSummary,
    DefaultSummary,
    Execution,
    GeomeanComparisonSummary,
    Iteration,
    Report,
    Sample,
    format_failures,
)
from bench.console.theme import BENCHR_THEME
from bench.core.stats import Statistics, summarize
from bench.model.benchmark import Variant
from bench.model.invocation import SPAWN_FAIL_RC, TIMEOUT_RC
from bench.model.results import Direction


def _smp(
    metric: str = "runtime",
    value: float = 0.5,
    unit: str = "s",
    direction: Direction = "lower better",
) -> Sample:
    return Sample(metric=metric, value=value, unit=unit, direction=direction)


def _ok(
    run: int = 1,
    *,
    bench: str = "b",
    suite: str = "S",
    variant=(),
    variant_label: str = "",
    samples: list[Sample] | None = None,
    warmup: bool = False,
) -> Execution:
    return Execution(
        suite=suite,
        benchmark=bench,
        variant=Variant(tuple(variant)),
        run=run,
        runtime=0.5,
        command=("x",),
        variant_label=variant_label,
        iterations=[Iteration(samples=list(samples) if samples else [], warmup=warmup)],
    )


def _vrun(
    value: float,
    *,
    run: int,
    label: str,
    bench: str = "b",
    suite: str = "S",
    metric: str = "elapsed",
    unit: str = "s",
    direction: Direction = "lower better",
) -> Execution:
    return _ok(
        run,
        bench=bench,
        suite=suite,
        variant=(("k", label),),
        variant_label=label,
        samples=[_smp(metric, value, unit=unit, direction=direction)],
    )


def _axis_report(values: dict[str, dict[str, float]]) -> Report:
    """values[axis_value][benchmark] = elapsed, one run each."""
    runs = []
    for value, benches in values.items():
        for b, elapsed in benches.items():
            runs.append(
                _ok(
                    1,
                    bench=b,
                    variant=(("interp", value),),
                    samples=[_smp("elapsed", elapsed)],
                )
            )
    return Report(executions=runs)


def _matrix_report(values: dict[tuple[str, str], dict[str, float]]) -> Report:
    """values[(interp, mode)][benchmark] = elapsed, one run each."""
    return Report(
        executions=[
            _ok(
                1,
                bench=b,
                variant=(("interp", interp), ("mode", mode)),
                samples=[_smp("elapsed", elapsed)],
            )
            for (interp, mode), benches in values.items()
            for b, elapsed in benches.items()
        ]
    )


def _matrix_data() -> Statistics:
    return _data(
        _matrix_report(
            {
                ("a", "on"): {"b1": 1.0, "b2": 1.0},
                ("a", "off"): {"b1": 2.0, "b2": 8.0},  # geomean 4x a/on
                ("b", "on"): {"b1": 2.0, "b2": 2.0},  # geomean 2x a/on
                ("b", "off"): {"b1": 6.0, "b2": 6.0},  # geomean 6x a/on
            }
        )
    )


def _data(report: Report) -> Statistics:
    """The `Statistics` a summary consumes (mirrors what the app hands it)."""
    return summarize(report)


def _render(renderable: RenderableType) -> str:
    """The text a real console would print. The summaries return renderables, so
    the table layout (and any markup the theme swallows) only shows up here."""
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=200, theme=BENCHR_THEME).print(
        renderable
    )
    return buf.getvalue()


# ----- ByBenchmarkMetricSummary ----------------------------------------------------


def test_by_benchmark_metric_warns_on_outliers():
    r = Report(
        executions=[
            _ok(1, samples=[_smp("runtime", 1.0)]),
            _ok(2, samples=[_smp("runtime", 1.0)]),
            _ok(
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
    # Rendering with the real theme also proves every style name exists.
    out = _render(ByBenchmarkMetricSummary()(_data(r)))
    assert "outlier" in out.lower() and "runtime" in out


def test_by_benchmark_metric_names_the_metric_and_its_unit():
    r = Report(executions=[_ok(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)])
    out = _render(ByBenchmarkMetricSummary()(_data(r)))
    assert "(runtime)" in out  # block header
    assert "ms" in out  # scaled unit column


def test_by_benchmark_metric_counts_runs_not_samples():
    # One execution can yield several samples in one go (e.g. a regex matching
    # multiple lines of output). Only the run count is reported, so a
    # multi-sample range sits next to the single run that produced it.
    r = Report(
        executions=[
            _ok(
                1,
                samples=[
                    _smp("runtime", 0.5),
                    _smp("runtime", 0.52),
                    _smp("runtime", 0.48),
                ],
            )
        ]
    )
    out = _render(ByBenchmarkMetricSummary()(_data(r)))
    assert "(1 runs)" in out
    assert "samples" not in out
    assert "480.00 … 520.00" in out  # the range still spans all three


def test_by_benchmark_metric_shows_warmup_count_when_bench_discarded_runs():
    # bench's own warmup policy discards whole runs (not just values within
    # one run, unlike e.g. cpython.py's old pyperformance-level warmup) - the
    # discarded count is shown alongside the runs, with no singular form. The
    # run count includes it: 3 runs of which 1 contributed nothing.
    r = Report(
        executions=[
            _ok(1, samples=[_smp("runtime", 9.9)], warmup=True),
            _ok(2, samples=[_smp("runtime", 0.5), _smp("runtime", 0.52)]),
            _ok(3, samples=[_smp("runtime", 0.48)]),
        ]
    )
    out = _render(ByBenchmarkMetricSummary()(_data(r)))
    assert "(1 warmup, 3 runs)" in out


def test_by_benchmark_metric_shows_variant_in_rows():
    r = Report(
        executions=[
            _ok(
                i,
                variant=(("vm", "python3.14"),),
                variant_label="vm=python3.14",
                samples=[_smp("elapsed", 0.5)],
            )
            for i in range(1, 4)
        ]
    )
    out = _render(ByBenchmarkMetricSummary()(_data(r)))
    assert "S/b" in out and "vm=python3.14" in out


# ----- ComparisonSummary (within-benchmark ranking) --------------------------


def test_ranking_uses_better_worse_for_higher_is_better():
    runs = []
    for i in range(1, 4):
        runs.append(
            _vrun(
                200.0,
                run=i,
                label="fast",
                metric="throughput",
                unit="iter/s",
                direction="higher better",
            )
        )
        runs.append(
            _vrun(
                100.0,
                run=i,
                label="slow",
                metric="throughput",
                unit="iter/s",
                direction="higher better",
            )
        )
    out = _render(ComparisonSummary()(_data(Report(executions=runs))))
    assert "fast was" in out and "2.00" in out and "× better than" in out
    assert "higher" not in out and "lower" not in out and "worse" not in out


def test_ranking_empty_for_single_variant():
    r = Report(
        executions=[_ok(i, samples=[_smp("elapsed", 0.10)]) for i in range(1, 4)]
    )
    assert ComparisonSummary()(_data(r)).renderables == []


def test_ranking_empty_across_distinct_benchmarks():
    runs = []
    for i in range(1, 4):
        runs.append(_ok(i, bench="a", samples=[_smp("elapsed", 0.10)]))
        runs.append(_ok(i, bench="b", samples=[_smp("elapsed", 0.20)]))
    stats = _data(Report(executions=runs))
    assert ComparisonSummary()(stats).renderables == []


# ----- GeomeanComparisonSummary (within-run axis ranking) --------------------


def test_grouped_summary_about_the_same():
    r = _axis_report({"a": {"b1": 1.0}, "b": {"b1": 1.0}})
    out = _render(
        GeomeanComparisonSummary(axis="interp").on_metrics("elapsed")(_data(r))
    )
    assert "about the same as b" in out
    assert "1.00×" not in out


# ----- GeomeanComparisonSummary over a composite axis ------------------------


def test_grouped_summary_ranks_the_cells_of_a_composite_axis():
    out = _render(
        GeomeanComparisonSummary(axis=["interp", "mode"]).on_metrics("elapsed")(
            _matrix_data()
        )
    )
    assert "Comparison - interp, mode - S" in out
    assert "interp=a, mode=on was" in out
    assert "2.00× better than interp=b, mode=on" in out
    assert "4.00× better than interp=a, mode=off" in out
    assert "6.00× better than interp=b, mode=off" in out


def test_grouped_summary_composite_axis_folds_nothing_into_the_geomean():
    """One axis of the same matrix averages the other one in; both axes don't."""
    out = _render(
        GeomeanComparisonSummary(axis="interp").on_metrics("elapsed")(_matrix_data())
    )
    assert "a was" in out
    # geomean of the four pairwise ratios, both modes mixed in: 2, 2, 3, 0.75.
    assert "1.73× better than b" in out


def test_grouped_summary_composite_axis_ref_pins_one_cell():
    out = _render(
        GeomeanComparisonSummary(
            axis=["interp", "mode"], ref="interp=b, mode=off"
        ).on_metrics("elapsed")(_matrix_data())
    )
    assert "interp=b, mode=off was" in out
    assert "6.00× worse than interp=a, mode=on" in out


def test_grouped_summary_composite_axis_ref_ignores_the_order_of_the_names():
    out = _render(
        GeomeanComparisonSummary(
            axis=["interp", "mode"], ref="mode=off,interp=b"
        ).on_metrics("elapsed")(_matrix_data())
    )
    assert "is not a value of axis" not in out
    assert "interp=b, mode=off was" in out


def test_grouped_summary_single_axis_ref_takes_either_form():
    for ref in ("b", "interp=b"):
        out = _render(
            GeomeanComparisonSummary(axis="interp", ref=ref).on_metrics("elapsed")(
                _matrix_data()
            )
        )
        assert "is not a value of axis" not in out
        assert "b was" in out


def test_grouped_summary_composite_axis_keeps_empty_values_distinct():
    """`interp=x, mode=` and `interp=, mode=x` are two cells, not one."""
    r = _matrix_report({("x", ""): {"b1": 1.0}, ("", "x"): {"b1": 100.0}})
    out = _render(
        GeomeanComparisonSummary(axis=["interp", "mode"]).on_metrics("elapsed")(
            _data(r)
        )
    )
    assert "interp=x, mode= was" in out
    assert "100.00× better than interp=, mode=x" in out


def test_grouped_summary_missing_part_of_a_composite_axis_warns():
    r = _axis_report({"a": {"b1": 1.0}, "b": {"b1": 2.0}})
    out = _render(
        GeomeanComparisonSummary(axis=["interp", "mode"]).on_metrics("elapsed")(
            _data(r)
        )
    )
    assert "axis 'interp, mode' incomplete: 'mode' not present" in out


def test_grouped_summary_missing_axis_of_a_composite_warns():
    r = _axis_report({"a": {"b1": 1.0}, "b": {"b1": 2.0}})
    out = _render(
        GeomeanComparisonSummary(axis=["vm", "mode"]).on_metrics("elapsed")(_data(r))
    )
    assert "axis 'vm, mode' not present in any benchmark" in out


def test_grouped_summary_composite_axis_never_combined_warns():
    r = Report(
        executions=[
            _ok(1, variant=(("interp", "a"),), samples=[_smp("elapsed", 1.0)]),
            _ok(1, variant=(("mode", "on"),), samples=[_smp("elapsed", 2.0)]),
        ]
    )
    out = _render(
        GeomeanComparisonSummary(axis=["interp", "mode"]).on_metrics("elapsed")(
            _data(r)
        )
    )
    assert "axis 'interp, mode' never combined in one benchmark" in out


def test_grouped_summary_unknown_ref_warns_and_falls_back():
    out = _render(
        GeomeanComparisonSummary(axis=["interp", "mode"], ref="interp=nope").on_metrics(
            "elapsed"
        )(_matrix_data())
    )
    assert "reference axis 'interp=nope' is not a value of axis 'interp, mode'" in out
    assert "interp=a, mode=on was" in out


def test_grouped_summary_bare_ref_on_a_composite_axis_warns():
    """A bare value cannot say which cell it means once the axis is composite."""
    out = _render(
        GeomeanComparisonSummary(axis=["interp", "mode"], ref="b").on_metrics(
            "elapsed"
        )(_matrix_data())
    )
    assert "reference axis 'b' is not a value of axis 'interp, mode'" in out


def test_grouped_summary_ref_absent_from_one_group_is_silent():
    """The ref may legitimately be missing from a suite; only an unknown one warns."""
    out = _render(
        GeomeanComparisonSummary(axis="interp", ref="b").on_metrics("elapsed")(
            _data(
                Report(
                    executions=[
                        *_axis_report({"a": {"b1": 1.0}, "b": {"b1": 2.0}}).executions,
                        *[
                            _ok(
                                1,
                                suite="T",
                                bench="b2",
                                variant=(("interp", v),),
                                samples=[_smp("elapsed", e)],
                            )
                            for v, e in (("a", 1.0), ("c", 3.0))
                        ],
                    ]
                )
            )
        )
    )
    assert "is not a value of axis" not in out
    assert "b was" in out  # suite S, pinned
    assert "a was" in out  # suite T, best performer


def test_grouped_summary_empty_axis_is_an_error():
    with pytest.raises(BenchError, match="at least one matrix dimension"):
        GeomeanComparisonSummary(axis=[]).on_metrics("elapsed")(_matrix_data())


# ----- DefaultSummary + composition ------------------------------------------


def test_default_summary_composes_by_benchmark_metric_and_ranking():
    runs = []
    for i in range(1, 4):
        runs.append(_vrun(0.10, run=i, label="fast"))
        runs.append(_vrun(0.20, run=i, label="slow"))
    out = _render(DefaultSummary()(_data(Report(executions=runs))))
    assert "S/b" in out  # by-benchmark block
    assert "Comparison - S/b" in out  # ranking block


def test_composed_summary_renders_every_part():
    summary = ByBenchmarkMetricSummary() & GeomeanComparisonSummary(
        axis="interp"
    ).on_metrics("elapsed")
    out = _render(summary(_data(_axis_report({"a": {"x": 4.0}, "b": {"x": 1.0}}))))
    assert "S/x" in out  # ByBenchmarkMetricSummary
    assert "Comparison - interp" in out  # GeomeanComparisonSummary


# ----- format_failures -------------------------------------------------------


def _failed(
    benchmark: str, *, returncode: int, failure: str, message: str = ""
) -> Execution:
    return Execution(
        suite="S",
        benchmark=benchmark,
        runtime=0.0,
        returncode=returncode,
        failure=failure,
        message=message,
    )


def test_format_failures_names_every_failed_run_and_its_verdict():
    out = _render(
        format_failures(
            [
                _failed("bad", returncode=7, failure="exit 7", message="trouble"),
                _failed(
                    "hangs", returncode=TIMEOUT_RC, failure="timeout", message="killed"
                ),
                _failed(
                    "missing",
                    returncode=SPAWN_FAIL_RC,
                    failure="No such file or directory",
                ),
            ]
        )
    )
    assert "Failures" in out
    assert "S/bad" in out and "exit 7" in out and "trouble" in out
    assert "timeout (exit 124)" in out and "killed" in out
    assert "spawn failed" in out and "No such file or directory" in out
    assert "(no output)" in out  # the spawn failure carried no message
    # one line per failure, not one run-together line
    assert len([ln for ln in out.splitlines() if "✗" in ln]) == 3


def test_format_failures_is_empty_without_failures():
    assert _render(format_failures([])).strip() == ""


# ----- ByMetricSummary -------------------------------------------------------


def test_by_metric_filters_by_metric():
    r = Report(
        executions=[
            _ok(i, samples=[_smp("runtime", 0.5), _smp("max_rss", 1024.0, unit="kB")])
            for i in range(1, 4)
        ]
    )
    out = _render(ByMetricSummary("runtime")(_data(r)))
    assert "runtime" in out and "max_rss" not in out
