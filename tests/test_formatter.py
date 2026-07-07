"""Formatter output: the rendered tables for each view."""

import re
from io import StringIO

from rich.console import Console

from bench import (
    Compact,
    DefaultSummary,
    GroupedSummary,
    Iteration,
    Report,
    Results,
    Execution,
    Sample,
    Summary,
    SummaryReporter,
)
from bench.report.summary import Stat, summarize
from bench.report.theme import BENCHR_THEME


def _smp(
    metric: str = "runtime",
    value: float = 0.5,
    unit: str = "s",
    lower_is_better: bool | None = True,
) -> Sample:
    return Sample(
        metric=metric, value=value, unit=unit, lower_is_better=lower_is_better
    )


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
        variant=variant,
        run=run,
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
    lower_is_better: bool | None = True,
) -> Execution:
    return _ok(
        run,
        bench=bench,
        suite=suite,
        variant=(("k", label),),
        variant_label=label,
        samples=[_smp(metric, value, unit=unit, lower_is_better=lower_is_better)],
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


def _data(report: Report) -> list[Stat]:
    """The `list[Stat]` a formatter consumes (mirrors SummaryReporter)."""
    return summarize(report)


def _strip(s: str) -> str:
    """Render like console.print: drop bench tags, turn `\\[` back into `[`."""
    return re.sub(r"\[bench\.[a-z]+\]|\[/\]", "", s).replace("\\[", "[")


def _render(markup: str) -> str:
    buf = StringIO()
    Console(file=buf, force_terminal=False, width=200).print(markup)
    return buf.getvalue()


# ----- Results ---------------------------------------------------------------


def test_results_warns_on_outliers():
    r = Report(
        executions=[
            _ok(1, samples=[_smp("runtime", 1.0)]),
            _ok(2, samples=[_smp("runtime", 1.0)]),
            _ok(
                3,
                samples=[
                    Sample(
                        "runtime", 100.0, unit="s", lower_is_better=True, outlier=True
                    )
                ],
            ),
        ]
    )
    out = Results()(_data(r))
    assert "outlier" in out.lower() and "runtime" in out
    Console(file=StringIO(), theme=BENCHR_THEME).print(out)  # styles must exist


def test_results_unit_label_survives_rich_markup():
    r = Report(executions=[_ok(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)])
    assert "runtime [ms]" in _render(Results()(_data(r)))


def test_results_shows_sample_count_when_multiple_samples_per_run():
    # One execution can yield several samples in one go (e.g. a regex matching
    # multiple lines of output) - the sample count must be visible alongside
    # the run count, or a multi-sample min...max range next to "(1 run)" reads
    # as if that single run were internally inconsistent.
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
    out = _strip(Results()(_data(r)))
    assert "3 samples, 1 run" in out


def test_results_omits_sample_count_when_it_matches_run_count():
    # Common case (one sample per run): unchanged, no redundant count shown.
    r = Report(executions=[_ok(i, samples=[_smp("runtime", 0.5)]) for i in range(1, 4)])
    out = _strip(Results()(_data(r)))
    assert "3 runs" in out
    assert "samples" not in out


def test_results_shows_warmup_count_when_bench_discarded_runs():
    # bench's own warmup policy discards whole runs (not just values within
    # one run, unlike e.g. cpython.py's old pyperformance-level warmup) - the
    # discarded count is shown alongside samples/runs, with no singular form.
    r = Report(
        executions=[
            _ok(1, samples=[_smp("runtime", 9.9)], warmup=True),
            _ok(2, samples=[_smp("runtime", 0.5), _smp("runtime", 0.52)]),
            _ok(3, samples=[_smp("runtime", 0.48)]),
        ]
    )
    out = _strip(Results()(_data(r)))
    assert "3 samples, 1 warmup, 2 runs" in out


def test_results_shows_variant_in_rows():
    r = Report(
        executions=[
            _ok(i, variant=(("vm", "python3.14"),), samples=[_smp("elapsed", 0.5)])
            for i in range(1, 4)
        ]
    )
    out = _strip(Results()(_data(r)))
    assert "S/b" in out and "vm=python3.14" in out


# ----- Summary (within-benchmark ranking) ------------------------------------


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
                lower_is_better=False,
            )
        )
        runs.append(
            _vrun(
                100.0,
                run=i,
                label="slow",
                metric="throughput",
                unit="iter/s",
                lower_is_better=False,
            )
        )
    out = _strip(Summary()(_data(Report(executions=runs))))
    assert "fast was" in out and "2.00" in out and "× better than" in out
    assert "higher" not in out and "lower" not in out and "worse" not in out


def test_ranking_empty_for_single_variant():
    r = Report(
        executions=[_ok(i, samples=[_smp("elapsed", 0.10)]) for i in range(1, 4)]
    )
    assert Summary()(_data(r)) == ""


def test_ranking_empty_across_distinct_benchmarks():
    runs = []
    for i in range(1, 4):
        runs.append(_ok(i, bench="a", samples=[_smp("elapsed", 0.10)]))
        runs.append(_ok(i, bench="b", samples=[_smp("elapsed", 0.20)]))
    assert Summary()(_data(Report(executions=runs))) == ""


# ----- GroupedSummary (within-run axis ranking) ------------------------------


def test_grouped_summary_about_the_same():
    r = _axis_report({"a": {"b1": 1.0}, "b": {"b1": 1.0}})
    out = _strip(GroupedSummary(axis="interp", metric="elapsed")(_data(r)))
    assert "about the same" in out
    assert "1.00×" not in out


# ----- DefaultSummary + composition ------------------------------------------


def test_default_summary_composes_results_and_ranking():
    runs = []
    for i in range(1, 4):
        runs.append(_vrun(0.10, run=i, label="fast"))
        runs.append(_vrun(0.20, run=i, label="slow"))
    out = _strip(DefaultSummary()(_data(Report(executions=runs))))
    assert "S/b" in out  # Results block
    assert "Summary - S/b" in out  # ranking block


def test_summary_reporter_renders_composed_formatter():
    buf = StringIO()
    rep = SummaryReporter(
        Results() & GroupedSummary(axis="interp", metric="elapsed"),
        target_console=Console(file=buf, force_terminal=False, width=200),
    )
    for run in _axis_report({"a": {"x": 4.0}, "b": {"x": 1.0}}).executions:
        rep.execution_done(run)
    rep.finalize()
    out = buf.getvalue()
    assert "S/x" in out  # Results
    assert "Summary (geomean) - interp" in out  # GroupedSummary


# ----- Compact ---------------------------------------------------------------


def test_compact_filters_by_metric():
    r = Report(
        executions=[
            _ok(i, samples=[_smp("runtime", 0.5), _smp("max_rss", 1024.0, unit="kB")])
            for i in range(1, 4)
        ]
    )
    out = _strip(Compact("runtime")(_data(r)))
    assert "runtime" in out and "max_rss" not in out
