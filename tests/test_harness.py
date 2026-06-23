"""Harness benchmarks: one execution, many run records."""

import time
from pathlib import Path

from benchr import (
    CoefficientOfVariation, Dry, FloatPerLine, JsonReporter, Parallel,
    Regex, Sequential, Time, bench, line_monitor, plan, report_from_json,
    suite,
)


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



# ----- streaming kill integration ---------------------------------


def test_harness_killed_when_policy_converges():
    # emits constant 1.0 ~20/sec; CoV warmup converges fast, then FixedRuns(2) measured
    cmd = ["sh", "-c", "for i in $(seq 1000); do echo 1.0; sleep 0.05; done"]
    s = (
        suite("H", bench("a").with_command(cmd).with_harness())
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("").lower_is_better())
        .with_warmup(CoefficientOfVariation("runtime", threshold=0.01, window=3, min_runs=3))
        .with_runs(2)
    )
    t = time.monotonic()
    report = Sequential().run(plan([s], None), None)
    elapsed = time.monotonic() - t
    assert len(report.runs) < 50, f"expected early stop, got {len(report.runs)} runs"
    assert elapsed < 20, f"expected fast kill, took {elapsed:.1f}s"
    assert report.failures == []


# ----- fan-out ---------------------------------------------------------------


def test_one_execution_is_one_run_with_observations():
    s = _harness_suite(_echo_lines("1.0", "2.0", "3.0", "4.0", "5.0"),
                       warmup=2, runs=3)
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    run = report.runs[0]
    assert [o.samples[0].value for o in run.observations] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert report.warmups == {"H/a": 2}
    assert report.failures == []


def test_multi_metric_iterations_pair_up():
    cmd = ["sh", "-c", "echo 't: 1.0 m: 10'; echo 't: 2.0 m: 20'"]
    s = _harness_suite(cmd, runs=2, metric=FloatPerLine("ms"))
    s = s.with_metric(Regex("t", r"t: ([\d.]+)"), Regex("m", r"m: ([\d.]+)"))
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    obs = report.runs[0].observations
    assert [(smp.metric, smp.value) for smp in obs[0].samples] == [
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


def test_under_delivery_records_what_was_delivered():
    # The harness produced fewer iterations than the runs policy wanted; we
    # keep what was delivered (no synthetic short-delivery failure).
    s = _harness_suite(_echo_lines("1.0", "2.0"), runs=3)
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    assert [o.samples[0].value for o in report.runs[0].observations] == [1.0, 2.0]
    assert report.failures == []


def test_monitor_exception_fails_the_run():
    def boom(handle):
        raise RuntimeError("boom")

    s = (
        suite("H", bench("a").with_command(_echo_lines("1.0", "2.0", "3.0"))
              .with_harness(monitor=boom))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms").lower_is_better())
        .with_runs(3)
    )
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    assert len(report.failures) == 1
    assert "boom" in (report.runs[0].failure or "")


def test_monitor_exception_after_delivery_records_one_failed_run():
    def boom_after_two(handle):
        n = 0
        for line in line_monitor(handle):
            if n >= 2:
                raise ValueError("bad output")
            n += 1
            yield line

    s = (
        suite("H", bench("a").with_command(_echo_lines("1.0", "2.0", "3.0", "4.0"))
              .with_harness(monitor=boom_after_two))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms").lower_is_better())
        .with_runs(5)
    )
    report = Sequential().run(plan([s], None), None)
    # One run: failed (the monitor broke), but the two delivered observations
    # are kept.
    assert len(report.runs) == 1
    run = report.runs[0]
    good = [o for o in run.observations if not o.is_failure()]
    assert len(good) == 2
    assert report.failures == [run]
    assert "bad output" in (run.failure or "")


def test_over_delivery_stops_at_policy():
    # Under streaming, once the runs policy converges the Controller stops/kills
    # the harness, so extra iterations are not kept.
    s = _harness_suite(_echo_lines("1.0", "2.0", "3.0", "4.0"), runs=2)
    report = Sequential().run(plan([s], None), None)
    assert len(report.runs) == 1
    assert len(report.runs[0].observations) == 2
    assert report.failures == []


def test_harness_process_metric_becomes_trailing_observation():
    s = (suite("H", bench("a").with_command(_echo_lines("1.0", "2.0")))
         .with_cwd(Path("/tmp")).with_metric(FloatPerLine("ms"), Time())
         .with_runs(2).with_harness())
    report = Sequential().run(plan([s], None), None)
    run = report.runs[0]
    # per-iteration observations carry FloatPerLine (not elapsed); a trailing
    # observation carries the whole-process Time (elapsed).
    per_iter = run.observations[:2]
    assert all(all(s.metric != "elapsed" for s in o.samples) for o in per_iter)
    assert any(s.metric == "elapsed" for o in run.observations for s in o.samples)


def test_harness_process_metric_reaches_json_file(tmp_path: Path):
    # Whole-process metrics travel through the Reporter chain to file sinks.
    out = tmp_path / "r.json"
    s = (suite("H", bench("a").with_command(_echo_lines("1.0", "2.0")))
         .with_cwd(Path("/tmp")).with_metric(FloatPerLine("ms"), Time())
         .with_runs(2).with_harness())
    Sequential(reporter=JsonReporter(out)).run(plan([s], None), None)
    loaded = report_from_json(out.read_text())
    assert any(s.metric == "elapsed"
               for run in loaded.runs for o in run.observations for s in o.samples)


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
        by_bench.setdefault(r.benchmark, []).extend(
            s.value for o in r.observations for s in o.samples)
    assert by_bench == {"a": [1.0, 2.0], "b": [3.0, 4.0]}
    assert report.warmups == {"H/a": 1, "H/b": 1}


def test_dry_prints_one_harness_line(capsys):
    s = _harness_suite(_echo_lines("1.0"), warmup=2, runs=3)
    Dry().run(plan([s], None), None)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].endswith("[harness]")
    assert "H/a #1" in lines[0]
