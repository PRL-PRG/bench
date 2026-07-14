"""Harness benchmarks: one execution, many run records."""

import time
from pathlib import Path

from bench import (
    CoefficientOfVariation,
    Dry,
    FloatPerLine,
    JsonReporter,
    Parallel,
    Regex,
    Sequential,
    Time,
    bench,
    line_monitor,
    report_from_json,
    suite,
)
from bench.runner.base import plan


def _echo_lines(*values) -> list[str]:
    return ["sh", "-c", ";".join(f"echo {v}" for v in values)]


def _harness_suite(command, *, warmup=0, runs=1, metric=None):
    return (
        suite("H", bench("a").with_command(command))
        .with_cwd(Path("/tmp"))
        .with_metric(metric or FloatPerLine("ms", metric="runtime").lower_is_better())
        .with_warmup(warmup)
        .with_runs(runs)
        .with_harness()
    )


# ----- resolution / validation ----------------------------------------------


def test_suite_with_harness_reaches_every_benchmark():
    s = _harness_suite(_echo_lines("1.0"))
    assert all(b.harness for b in s.materialize(None))


def test_bench_level_with_harness_in_command_suite():
    s = suite(
        "S",
        bench("h").with_command(["true"]).with_harness(),
        bench("c").with_command(["true"]),
    ).with_process_metric(Time())
    h, c = s.materialize(None)
    assert h.harness and not c.harness


def test_harness_monitor_fn_builds_from_context():
    # Mirrors with_filter/with_filter_fn: with_harness(monitor=...) takes a
    # direct monitor, with_monitor_fn takes a (ctx) -> HarnessMonitor factory
    # for when the monitor needs ctx.
    def make_monitor(ctx):
        def read(handle):
            yield from line_monitor(handle)

        return read

    s = (
        suite(
            "H",
            bench("a")
            .with_command(_echo_lines("1.0", "2.0"))
            .with_monitor_fn(make_monitor),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms", metric="runtime").lower_is_better())
        .with_runs(2)
    )
    report = Sequential().run(plan([s], None), None)
    assert [o.samples[0].value for o in report.executions[0].iterations] == [1.0, 2.0]


# ----- streaming kill integration ---------------------------------


def test_harness_killed_when_policy_converges():
    # emits constant 1.0 ~20/sec, CoV warmup converges fast, then FixedRuns(2) measured.
    # A streaming harness never exits on its own, so kill_on_convergence=True is
    # required: the Controller kills it once the policy converges.
    cmd = ["sh", "-c", "for i in $(seq 1000); do echo 1.0; sleep 0.05; done"]
    s = (
        suite("H", bench("a").with_command(cmd).with_harness(kill_on_convergence=True))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("", metric="runtime").lower_is_better())
        .with_warmup(
            CoefficientOfVariation("runtime", threshold=0.01, window=3, min_runs=3)
        )
        .with_runs(2)
    )
    t = time.monotonic()
    report = Sequential().run(plan([s], None), None)
    elapsed = time.monotonic() - t
    assert len(report.executions) < 50, (
        f"expected early stop, got {len(report.executions)} runs"
    )
    assert elapsed < 20, f"expected fast kill, took {elapsed:.1f}s"
    assert report.failures == []


# ----- fan-out ---------------------------------------------------------------


def test_one_execution_is_one_run_with_observations():
    s = _harness_suite(_echo_lines("1.0", "2.0", "3.0", "4.0", "5.0"), warmup=2, runs=3)
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    run = report.executions[0]
    assert [o.samples[0].value for o in run.iterations] == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert [o.warmup for o in run.iterations] == [True, True, False, False, False]
    assert report.failures == []


def test_multi_metric_iterations_pair_up():
    cmd = ["sh", "-c", "echo 't: 1.0 m: 10'; echo 't: 2.0 m: 20'"]
    s = _harness_suite(cmd, runs=2, metric=FloatPerLine("ms", metric="runtime"))
    s = s.with_metric(Regex("t", r"t: ([\d.]+)"), Regex("m", r"m: ([\d.]+)"))
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    obs = report.executions[0].iterations
    assert [(smp.metric, smp.value) for smp in obs[0].samples] == [
        ("t", 1.0),
        ("m", 10.0),
    ]


def test_failed_execution_is_one_failed_record():
    s = _harness_suite(["sh", "-c", "exit 3"], runs=5)
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert report.executions[0].failure == "exit code 3"


def test_timeout_is_one_failed_record():
    s = _harness_suite(["sleep", "5"], runs=2).with_timeout(0.1)
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert report.executions[0].returncode == 124


def test_no_parsable_output_is_a_loud_failure():
    s = _harness_suite(["sh", "-c", "echo hello"], runs=3)
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert "no iterations parsed" in (report.executions[0].failure or "")


def test_under_delivery_records_what_was_delivered():
    # The harness produced fewer iterations than the runs policy wanted. We
    # keep what was delivered (no synthetic short-delivery failure).
    s = _harness_suite(_echo_lines("1.0", "2.0"), runs=3)
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert [o.samples[0].value for o in report.executions[0].iterations] == [1.0, 2.0]
    assert report.failures == []


def test_monitor_exception_fails_the_run():
    def boom(handle):
        raise RuntimeError("boom")

    s = (
        suite(
            "H",
            bench("a")
            .with_command(_echo_lines("1.0", "2.0", "3.0"))
            .with_harness(monitor=boom),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms", metric="runtime").lower_is_better())
        .with_runs(3)
    )
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert len(report.failures) == 1
    assert "boom" in (report.executions[0].failure or "")


def test_monitor_exception_after_delivery_records_one_failed_run():
    def boom_after_two(handle):
        n = 0
        for line in line_monitor(handle):
            if n >= 2:
                raise ValueError("bad output")
            n += 1
            yield line

    s = (
        suite(
            "H",
            bench("a")
            .with_command(_echo_lines("1.0", "2.0", "3.0", "4.0"))
            .with_harness(monitor=boom_after_two),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms", metric="runtime").lower_is_better())
        .with_runs(5)
    )
    report = Sequential().run(plan([s], None), None)
    # One run: failed (the monitor broke), but the two delivered observations
    # are kept.
    assert len(report.executions) == 1
    run = report.executions[0]
    good = [o for o in run.iterations if not o.is_failure()]
    assert len(good) == 2
    assert report.failures == [run]
    assert "bad output" in (run.failure or "")


def test_over_delivery_stops_at_policy():
    # Once the runs policy converges the Controller stops pulling, so iterations
    # the harness delivers beyond the policy count are not kept.
    s = _harness_suite(_echo_lines("1.0", "2.0", "3.0", "4.0"), runs=2)
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert len(report.executions[0].iterations) == 2
    assert report.failures == []


def test_no_kill_harness_exits_cleanly_not_killed():
    # A harness that produces a fixed number of iterations and exits on its own
    # must not be killed on convergence: with kill_on_convergence=False the
    # framework waits for it and records its real exit code (0), not a kill. The
    # sleep recreates the "about to exit" window where the default (kill) would
    # SIGKILL it (reporting 124).
    cmd = ["sh", "-c", "echo 1.0; echo 2.0; sleep 0.2; exit 0"]
    s = (
        suite("H", bench("a").with_command(cmd).with_harness(kill_on_convergence=False))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("", metric="runtime").lower_is_better())
        .with_runs(2)
    )
    report = Sequential().run(plan([s], None), None)
    assert len(report.executions) == 1
    assert report.executions[0].returncode == 0
    assert report.failures == []


def test_harness_process_metric_reaches_json_file(tmp_path: Path):
    # Whole-process metrics travel through the Reporter chain to file sinks.
    out = tmp_path / "r.json"
    s = (
        suite("H", bench("a").with_command(_echo_lines("1.0", "2.0")))
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms", metric="runtime"))
        .with_process_metric(Time())
        .with_runs(2)
        .with_harness()
    )
    Sequential(reporter=JsonReporter(out)).run(plan([s], None), None)
    loaded = report_from_json(out.read_text())
    assert any(
        s.metric == "elapsed" for run in loaded.executions for s in run.process_samples
    )


# ----- runners ----------------------------------------------------------------


def test_parallel_runs_harness_benchmarks():
    s = (
        suite(
            "H",
            bench("a").with_command(_echo_lines("1.0", "2.0")),
            bench("b").with_command(_echo_lines("3.0", "4.0")),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("ms", metric="runtime").lower_is_better())
        .with_warmup(1)
        .with_runs(1)
        .with_harness()
    )
    report = Parallel(workers=2).run(plan([s], None), None)
    by_bench = {}
    for r in report.executions:
        by_bench.setdefault(r.benchmark, []).extend(
            s.value for o in r.iterations for s in o.samples
        )
    assert by_bench == {"a": [1.0, 2.0], "b": [3.0, 4.0]}
    # Each benchmark's first iteration is flagged warmup.
    for r in report.executions:
        assert r.iterations[0].warmup and not r.iterations[1].warmup


def test_dry_prints_one_harness_line(capsys):
    s = _harness_suite(_echo_lines("1.0"), warmup=2, runs=3)
    Dry().run(plan([s], None), None)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0].endswith("[harness, warmup 2, runs 3]")
    assert "H/a #1" in lines[0]
    assert "`cd /tmp && sh -c 'echo 1.0'`" in lines[0]
