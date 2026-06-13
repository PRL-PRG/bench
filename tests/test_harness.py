"""Harness benchmarks: one execution, many run records."""

from pathlib import Path

import pytest

from benchr import (
    CoefficientOfVariation, Context, Dry, FloatPerLine, Parallel, Regex,
    Sample, Sequential, Time, bench, plan, run, suite,
)
from benchr.runner.base import split_iterations


def _echo_lines(*values) -> list[str]:
    return ["sh", "-c", ";".join(f"echo {v}" for v in values)]


def _harness_suite(command, *, warmup=0, runs=1, metric=None):
    return (
        suite("H", bench("a").with_command(command))
        .with_cwd(Path("/tmp"))
        .with_metric(metric or FloatPerLine("ms").lower_is_better())
        .with_warmup(warmup)
        .with_runs(runs)
        .with_harness()
    )


# ----- split_iterations -----------------------------------------------------


def _smp(metric: str, value: float) -> Sample:
    return Sample(metric=metric, value=value, unit="s")


def test_split_empty():
    assert split_iterations([]) == []


def test_split_single_metric_one_group_per_sample():
    groups = split_iterations([_smp("t", 1.0), _smp("t", 2.0)])
    assert [[s.value for s in g] for g in groups] == [[1.0], [2.0]]


def test_split_zips_metrics_positionally():
    groups = split_iterations(
        [_smp("t", 1.0), _smp("m", 10.0), _smp("t", 2.0), _smp("m", 20.0)])
    assert [[(s.metric, s.value) for s in g] for g in groups] == [
        [("t", 1.0), ("m", 10.0)], [("t", 2.0), ("m", 20.0)]]


def test_split_uneven_groups_stop_contributing():
    groups = split_iterations([_smp("t", 1.0), _smp("t", 2.0), _smp("m", 10.0)])
    assert [[(s.metric, s.value) for s in g] for g in groups] == [
        [("t", 1.0), ("m", 10.0)], [("t", 2.0)]]


# ----- resolution / validation ----------------------------------------------


def test_suite_with_harness_reaches_every_benchmark():
    s = _harness_suite(_echo_lines("1.0"))
    assert all(b.harness for b in s.materialize(None))


def test_bench_level_with_harness_in_command_suite():
    s = (suite("S", bench("h").with_command(["true"]).with_harness(),
               bench("c").with_command(["true"]))
         .with_metric(Time()))
    h, c = s.materialize(None)
    assert h.harness and not c.harness


def test_harness_requires_bounded_policies():
    s = _harness_suite(_echo_lines("1.0")).with_runs(
        CoefficientOfVariation("runtime"))
    with pytest.raises(ValueError, match="bounded"):
        s.materialize(None)


# ----- fan-out ---------------------------------------------------------------


def test_one_execution_fans_out_into_run_records():
    s = _harness_suite(_echo_lines("1.0", "2.0", "3.0", "4.0", "5.0"),
                       warmup=2, runs=3)
    report = Sequential().run(plan([s], None), None)
    assert [r.run for r in report.runs] == [1, 2, 3, 4, 5]
    assert [r.samples[0].value for r in report.runs] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert report.warmups == {"H/a": 2}
    assert report.failures == []


def test_multi_metric_iterations_pair_up():
    cmd = ["sh", "-c", "echo 't: 1.0 m: 10'; echo 't: 2.0 m: 20'"]
    s = _harness_suite(cmd, runs=2, metric=FloatPerLine("ms"))
    s = s.with_metric(Regex("t", r"t: ([\d.]+)"), Regex("m", r"m: ([\d.]+)"))
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 2
    assert [(smp.metric, smp.value) for smp in report.runs[0].samples] == [
        ("t", 1.0), ("m", 10.0)]


def test_failed_execution_is_one_failed_record():
    s = _harness_suite(["sh", "-c", "exit 3"], runs=5)
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    assert report.runs[0].failure == "exit code 3"


def test_timeout_is_one_failed_record():
    s = _harness_suite(["sleep", "5"], runs=2).with_timeout(0.1)
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    assert report.runs[0].returncode == 124


def test_no_parsable_output_is_a_loud_failure():
    s = _harness_suite(["sh", "-c", "echo hello"], runs=3)
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    assert "no iterations parsed" in (report.runs[0].failure or "")


def test_under_delivery_records_trailing_failure():
    s = _harness_suite(_echo_lines("1.0", "2.0"), runs=3)
    report = Sequential().run(plan([s], None), None)
    assert [r.run for r in report.runs] == [1, 2, 3]
    assert report.runs[2].failure == "harness produced 2 iterations, expected 3"
    assert len(report.failures) == 1


def test_over_delivery_keeps_extra_iterations():
    s = _harness_suite(_echo_lines("1.0", "2.0", "3.0", "4.0"), runs=2)
    report = Sequential().run(plan([s], None), None)
    assert [r.run for r in report.runs] == [1, 2, 3, 4]
    assert report.failures == []


def test_runs_flag_reaches_harness_command_fn():
    def harness_cmd(ctx: Context[object]) -> list[str]:
        w, r = ctx.warmup.max_runs(), ctx.runs.max_runs()
        assert w is not None and r is not None  # harness policies are bounded
        return ["sh", "-c", f"seq {w + r}"]

    s = (
        suite("H", bench("a").with_command(harness_cmd))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("").lower_is_better())
        .with_runs(5)
        .with_harness()
    )
    report = run(s, argv=["--runs", "2", "--warmup", "1", "--quiet"])
    assert [r.run for r in report.runs] == [1, 2, 3]
    assert report.warmups == {"H/a": 1}
    assert report.failures == []


# ----- runners ----------------------------------------------------------------


def test_parallel_runs_harness_benchmarks():
    s = (
        suite("H",
              bench("a").with_command(_echo_lines("1.0", "2.0")),
              bench("b").with_command(_echo_lines("3.0", "4.0")))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms").lower_is_better())
        .with_warmup(1)
        .with_runs(1)
        .with_harness()
    )
    report = Parallel(workers=2).run(plan([s], None), None)
    by_bench = {}
    for r in report.runs:
        by_bench.setdefault(r.benchmark, []).append(r.samples[0].value)
    assert by_bench == {"a": [1.0, 2.0], "b": [3.0, 4.0]}
    assert report.warmups == {"H/a": 1, "H/b": 1}


def test_dry_prints_one_harness_line(capsys):
    s = _harness_suite(_echo_lines("1.0"), warmup=2, runs=3)
    Dry().run(plan([s], None), None)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].endswith("[harness]")
    assert "H/a #1" in lines[0]
