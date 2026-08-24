"""Harness-shaped benchmarks: one execution, many Iterations.

The `with_harness()` concept is gone, but what it did survives: a metric that
stamps `Sample.iteration` makes a single subprocess produce many `Iteration`s,
which is exactly the "one process runs all the iterations and prints one
measurement per line" pattern (a JIT-warming VM, Renaissance, AWFY, ReBench).

`FloatPerLine` indexes per line by default; `Regex` needs `iterate=True`. What
used to be a `HarnessMonitor` - framing the process output into iterations - is
now the metric's own parsing, and a metric that must read something other than
stdout/stderr (a logfile the harness wrote, a JSON dump) supplies its own
`MetricSource`.

The differences from the old harness, both covered below: nothing is killed on
convergence (the process runs to completion and every iteration it delivered is
kept), and warmup counts whole executions rather than leading iterations.
"""

from pathlib import Path

import pytest

from bench import (
    DryRunner,
    FloatPerLine,
    JsonReporter,
    Parallel,
    RegexMetric,
    SequentialRunner,
    Time,
    bench,
    report_from_json,
    suite,
)
from bench.builder.context import Params
from bench.core.metric import StdoutMetricSource
from bench.runner.base import plan


def _echo_lines(*values) -> list[str]:
    """`sh -c` printing one value per line. `echo` is a shell builtin, so this
    needs no PATH in the (empty) benchmark environment."""
    return ["sh", "-c", ";".join(f"echo {v}" for v in values)]


def _runtime_metric() -> FloatPerLine:
    return FloatPerLine(StdoutMetricSource, "runtime", unit="ms").lower_is_better()


def _iteration_suite(command, *, warmup=0, runs=1, metric=None):
    """One process, many iterations: `runs` counts *processes*, and the metric
    turns each line of one process's output into its own Iteration."""
    return (
        suite("H", bench("a").with_command(command))
        .with_cwd(Path("/tmp"))
        .with_metric(metric or _runtime_metric())
        .with_warmup(warmup)
        .with_runs(runs)
    )


def _values(execution) -> list[float]:
    return [s.value for it in execution.iterations for s in it.samples]


# ----- fan-out: one execution, many iterations ------------------------------


def test_one_execution_yields_one_iteration_per_line():
    s = _iteration_suite(_echo_lines("1.0", "2.0", "3.0", "4.0", "5.0"))
    report = SequentialRunner().run(plan([s], Params()))
    assert len(report.executions) == 1
    execution = report.executions[0]
    assert _values(execution) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [s.iteration for it in execution.iterations for s in it.samples] == [
        0,
        1,
        2,
        3,
        4,
    ]
    assert report.failures == []


@pytest.mark.skip(
    reason="warmup counts whole executions, not leading iterations (see BUGS.md)"
)
def test_leading_iterations_can_be_marked_warmup():
    s = _iteration_suite(_echo_lines("1.0", "2.0", "3.0", "4.0", "5.0"), warmup=2)
    report = SequentialRunner().run(plan([s], Params()))
    assert len(report.executions) == 1
    assert [it.warmup for it in report.executions[0].iterations] == [
        True,
        True,
        False,
        False,
        False,
    ]


def test_multi_metric_iterations_pair_up():
    # Two metrics over the same output: each indexes its own matches in order,
    # so match N of both lands in Iteration N.
    cmd = ["sh", "-c", "echo 't: 1.0 m: 10'; echo 't: 2.0 m: 20'"]
    s = _iteration_suite(
        cmd,
        metric=RegexMetric("t", r"t: ([\d.]+)", StdoutMetricSource, iterate=True),
    ).with_metric(RegexMetric("m", r"m: ([\d.]+)", StdoutMetricSource, iterate=True))
    report = SequentialRunner().run(plan([s], Params()))
    assert len(report.executions) == 1
    iterations = report.executions[0].iterations
    assert [(s.metric, s.value) for s in iterations[0].samples] == [
        ("t", 1.0),
        ("m", 10.0),
    ]
    assert [(s.metric, s.value) for s in iterations[1].samples] == [
        ("t", 2.0),
        ("m", 20.0),
    ]


def test_regex_without_iterate_stays_a_process_sample():
    # `iterate=False` (the Regex default) keeps every match a whole-process
    # sample, so nothing is framed into iterations.
    cmd = ["sh", "-c", "echo 'x: 1.0'; echo 'x: 2.0'"]
    s = _iteration_suite(
        cmd, metric=RegexMetric("x", r"x: ([\d.]+)", StdoutMetricSource)
    )
    execution = SequentialRunner().run(plan([s], Params())).executions[0]
    assert execution.iterations == []
    assert [s.value for s in execution.process_samples] == [1.0, 2.0]


def test_metric_reads_a_source_other_than_stdout(tmp_path: Path):
    # A harness that writes its measurements to a file instead of streaming
    # them: what used to be a log-tailing HarnessMonitor is now just a
    # MetricSource, read once after the process exits.
    log = tmp_path / "harness.log"

    def from_log(_result) -> str:
        return log.read_text()

    s = _iteration_suite(
        ["sh", "-c", f"printf '1.5\\n2.5\\n3.5\\n' > {log}"],
        metric=FloatPerLine(from_log, "runtime", unit="ms").lower_is_better(),
    )
    execution = SequentialRunner().run(plan([s], Params())).executions[0]
    assert _values(execution) == [1.5, 2.5, 3.5]


# ----- delivery / failure ----------------------------------------------------


def test_every_delivered_iteration_is_kept():
    # The old harness stopped pulling once the runs policy converged, dropping
    # the surplus. A process now runs to completion and everything it printed is
    # recorded - the runs policy bounds *processes*, not iterations.
    s = _iteration_suite(_echo_lines("1.0", "2.0", "3.0", "4.0"))
    execution = SequentialRunner().run(plan([s], Params())).executions[0]
    assert _values(execution) == [1.0, 2.0, 3.0, 4.0]


def test_failed_execution_is_one_failed_record():
    s = _iteration_suite(["sh", "-c", "exit 3"])
    report = SequentialRunner().run(plan([s], Params()))
    assert len(report.executions) == 1
    assert report.executions[0].failure == "exit code 3"


def test_timeout_is_one_failed_record():
    s = _iteration_suite(["sleep", "5"]).with_timeout(0.1)
    report = SequentialRunner().run(plan([s], Params()))
    assert len(report.executions) == 1
    assert report.executions[0].returncode == 124


def test_unparsable_output_yields_no_iterations_and_no_failure():
    # The harness used to synthesize a "no iterations parsed" failure. A metric
    # that matches nothing is simply silent: the run succeeded, it just carries
    # no samples.
    s = _iteration_suite(["sh", "-c", "echo hello"])
    report = SequentialRunner().run(plan([s], Params()))
    assert len(report.executions) == 1
    assert report.executions[0].iterations == []
    assert report.failures == []


def test_process_metric_travels_alongside_iterations(tmp_path: Path):
    # A whole-process metric coexists with the per-iteration ones and reaches
    # the file sinks as `process_samples`.
    out = tmp_path / "r.json"
    s = _iteration_suite(_echo_lines("1.0", "2.0")).with_metric(Time())
    SequentialRunner().run(plan([s], Params()), reporter=JsonReporter(out))
    loaded = report_from_json(out.read_text())
    assert any(
        s.metric == "elapsed" for run in loaded.executions for s in run.process_samples
    )
    assert _values(loaded.executions[0]) == [1.0, 2.0]


# ----- runners ---------------------------------------------------------------


def test_parallel_runs_iteration_benchmarks():
    s = (
        suite(
            "H",
            bench("a").with_command(_echo_lines("1.0", "2.0")),
            bench("b").with_command(_echo_lines("3.0", "4.0")),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(_runtime_metric())
        .with_runs(1)
    )
    report = Parallel(workers=2).run(plan([s], Params()))
    by_bench = {r.benchmark: _values(r) for r in report.executions}
    assert by_bench == {"a": [1.0, 2.0], "b": [3.0, 4.0]}


def test_dry_prints_one_line_per_planned_execution(capsys):
    # A harness benchmark is one process, so `--runs 1` is one dry line - the
    # iteration count lives inside the process and a dry run cannot know it.
    s = _iteration_suite(_echo_lines("1.0"))
    DryRunner().run(plan([s], Params()))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "H/a #1" in lines[0]
    assert "cd /tmp && sh -c 'echo 1.0'" in lines[0]
