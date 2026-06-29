"""Sequential, Parallel, and Dry runners."""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest

from bench import (
    CoefficientOfVariation,
    CompositeReporter,
    CsvReporter,
    Dry,
    FloatPerLine,
    JsonReporter,
    Parallel,
    Reporter,
    Sequential,
    SuiteMaterializationError,
    Time,
    bench,
    report_from_json,
    run,
    suite,
)
from bench.runner.base import plan


def _run_samples(r):
    yield from (s for o in r.iterations for s in o.samples)
    yield from r.process_samples


def _all_samples(report):
    return [s for r in report.runs for s in _run_samples(r)]


def _runs_with_samples(report):
    return [(r, s) for r in report.runs for s in _run_samples(r)]


def _sleep_suite(name: str = "S", duration: float = 0.05, runs: int = 2):
    return suite(
        name,
        *[
            bench(f"b{i}")
            .with_command(["sh", "-c", f"sleep {duration}"])
            .with_cwd(Path("/tmp"))
            .with_process_metric(Time())
            .with_runs(runs)
            for i in range(2)
        ],
    )


def test_sequential_basic():
    report = Sequential().run(plan([_sleep_suite()], None), None)
    assert len(_all_samples(report)) == 4  # 2 benchmarks x 2 runs


def test_sequential_three_runs_yields_three_samples():
    s = suite(
        "X",
        bench("p")
        .with_command(["sh", "-c", "echo 0.5"])
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("s").lower_is_better())
        .with_runs(3),
    )
    report = Sequential().run(plan([s], None), None)
    pairs = _runs_with_samples(report)
    assert len(pairs) == 3
    assert [r.run for r, _ in pairs] == [1, 2, 3]


def test_sequential_runs_bounded_policy_to_completion_despite_failures(tmp_path: Path):
    # Bounded policy (FixedRuns) IS the contract: a crashing benchmark must
    # still complete N attempts.
    s = suite(
        "F",
        bench("bad")
        .with_command(["false"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(10),
    )
    out = tmp_path / "r.json"
    report = Sequential(reporter=JsonReporter(out)).run(plan([s], None), None)
    assert _all_samples(report) == []  # failed runs emit no metrics
    r = report_from_json(out.read_text())
    assert len(r.failures) == 10
    assert all(f.returncode != 0 for f in r.failures)


def test_parallel_runs_faster_than_sequential():
    s = _sleep_suite(duration=0.1, runs=2)
    t0 = time.monotonic()
    Sequential().run(plan([s], None), None)
    seq_t = time.monotonic() - t0
    t0 = time.monotonic()
    Parallel(workers=4).run(plan([s], None), None)
    par_t = time.monotonic() - t0
    # Two benchmarks across 4 workers should overlap, so parallel is clearly
    # faster than sequential. The threshold is loose (vs. an ideal ~0.5) to
    # tolerate process-spawn overhead and CI jitter. With no overlap the ratio
    # would be ~1.0, so this still catches a broken Parallel.
    assert par_t < seq_t * 0.8, f"parallel must be faster: {par_t=:.2f}, {seq_t=:.2f}"


def test_parallel_records_every_run():
    s = _sleep_suite(duration=0.01, runs=3)  # 2 benchmarks x 3 runs
    report = Parallel(workers=4).run(plan([s], None), None)
    assert len(_all_samples(report)) == 6


def test_parallel_runs_convergence_benchmarks():
    # Per-benchmark parallelism: each CoV benchmark drives its own Controller,
    # so convergence-driven policies run fine under Parallel (the old
    # "rejects unbounded" rule is gone).
    s = suite(
        "U",
        *[
            bench(f"conv{i}")
            .with_command(["sh", "-c", "echo 1.0"])
            .with_cwd(Path("/tmp"))
            .with_metric(FloatPerLine("s").lower_is_better())
            .with_runs(
                CoefficientOfVariation("runtime", threshold=0.5, window=2, min_runs=2)
            )
            for i in range(2)
        ],
    )
    report = Parallel(workers=2).run(plan([s], None), None)
    by_bench = {}
    for r in report.runs:
        by_bench.setdefault(r.benchmark, []).append(r)
    # Both benchmarks produced records and converged (constant 1.0 -> CoV 0).
    assert set(by_bench) == {"conv0", "conv1"}
    assert all(records for records in by_bench.values())
    assert report.failures == []


def test_parallel_shared_report_not_corrupted_under_concurrency():
    # Many benchmarks fanned across workers all mutate one shared Report. The
    # locked wrappers must keep run counts and per-benchmark grouping exact.
    n_bench, n_runs = 8, 5
    s = suite(
        "C",
        *[
            bench(f"b{i}")
            .with_command(["sh", "-c", "echo 1.0"])
            .with_cwd(Path("/tmp"))
            .with_metric(FloatPerLine("s").lower_is_better())
            .with_runs(n_runs)
            for i in range(n_bench)
        ],
    )
    report = Parallel(workers=4).run(plan([s], None), None)
    assert len(report.runs) == n_bench * n_runs
    by_bench = {}
    for r in report.runs:
        by_bench.setdefault(r.benchmark, []).append(r)
    assert set(by_bench) == {f"b{i}" for i in range(n_bench)}
    assert all(len(records) == n_runs for records in by_bench.values())
    # Run numbers are 1..n_runs per benchmark, no torn/duplicated entries.
    for records in by_bench.values():
        assert sorted(r.run for r in records) == list(range(1, n_runs + 1))


def test_dry_no_subprocess():
    # Use a non-existent command. Dry must not spawn anything.
    s = suite(
        "X",
        bench("a")
        .with_command(["/nonexistent_binary_xyz"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(5),
    )
    out = _all_samples(Dry().run(plan([s], None), None))
    assert out == []


def test_dry_compact_prints_one_line_per_execution(capsys):
    s = suite(
        "X",
        bench("a")
        .with_command(["/bin/echo", "hi"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(5),
    )
    Dry().run(plan([s], None), None)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 5
    for i, ln in enumerate(lines, start=1):
        assert ln == f"X/a #{i}: `cd /tmp && /bin/echo hi`"
    assert "cwd:" not in out and "plan:" not in out


def test_dry_compact_enumerates_warmup_and_measure(capsys):
    s = suite(
        "X",
        bench("a")
        .with_command(["/bin/echo", "hi"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_warmup(2)
        .with_runs(3),
    )
    Dry().run(plan([s], None), None)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 5
    # Continuous numbering: warmup runs are #1..#2, measured runs #3..#5.
    for i, ln in enumerate(lines, start=1):
        assert ln == f"X/a #{i}: `cd /tmp && /bin/echo hi`"


def test_dry_compact_unbounded_policy_prints_single_marker(capsys):
    s = suite(
        "X",
        bench("a")
        .with_command(["/bin/echo", "hi"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(CoefficientOfVariation("elapsed")),
    )
    Dry().run(plan([s], None), None)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "[unbounded]" in lines[0]


def test_dry_verbose_prints_full_block_per_execution(capsys):
    s = suite(
        "X",
        bench("a")
        .with_command(["/bin/echo", "hi"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(5),
    )
    Dry(verbose=True).run(plan([s], None), None)
    out = capsys.readouterr().out
    assert "command:    /bin/echo hi" in out
    assert "cwd:" in out
    assert "metrics:    Time" in out
    assert "warmup:" in out and "runs:" in out
    assert out.count("command:    /bin/echo hi") == 5
    assert out.count("X/a #1") == 1
    assert out.count("X/a #5") == 1


def test_sequential_quiet_prints_no_block(capsys):
    Sequential().run(plan([_sleep_suite(runs=1)], None), None)
    out = capsys.readouterr().out
    assert "command:" not in out and "plan:" not in out


def test_mixed_reporter_lifecycle(tmp_path: Path):
    json_path = tmp_path / "r.json"
    csv_path = tmp_path / "r.csv"
    sinks = CompositeReporter(JsonReporter(json_path), CsvReporter(csv_path))
    Sequential(reporter=sinks).run(plan([_sleep_suite(runs=1)], None), None)
    assert json_path.exists() and csv_path.exists()
    assert json_path.read_text().count('"metric"') >= 2


def _no_leftover_sleep_children() -> bool:
    """No child ``sleep`` processes survive under the current pid."""
    out = subprocess.run(
        ["pgrep", "-P", str(os.getpid()), "sleep"],
        capture_output=True,
        text=True,
    )
    return out.stdout.strip() == ""


def test_sigint_kills_subprocesses_sequential(tmp_path: Path):
    s = suite(
        "S",
        bench("slow")
        .with_command(["sleep", "10"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(3),
    )
    json_path = tmp_path / "r.json"

    t = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT))
    t.start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        Sequential(reporter=JsonReporter(json_path)).run(plan([s], None), None)
    elapsed = time.monotonic() - t0
    t.cancel()

    assert elapsed < 3.0, f"runner did not unwind after SIGINT: {elapsed:.2f}s"
    time.sleep(0.1)  # give the OS a moment to reap
    assert _no_leftover_sleep_children()

    # Reporter.finalize ran in the finally block, so the partial JSON exists
    # and the one in-flight run is recorded as interrupted.
    r = report_from_json(json_path.read_text())
    assert len(r.failures) >= 1
    assert r.failures[0].failure == "interrupted"
    assert len(r.runs) == 1  # later scheduled runs never started


def test_sigint_kills_subprocesses_parallel(tmp_path: Path):
    s = suite(
        "S",
        *[
            bench(f"slow{i}")
            .with_command(["sleep", "10"])
            .with_cwd(Path("/tmp"))
            .with_process_metric(Time())
            .with_runs(1)
            for i in range(4)
        ],
    )
    json_path = tmp_path / "r.json"

    t = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
    t.start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        Parallel(workers=4, reporter=JsonReporter(json_path)).run(plan([s], None), None)
    elapsed = time.monotonic() - t0
    t.cancel()

    assert elapsed < 3.0, f"parallel runner did not unwind after SIGINT: {elapsed:.2f}s"
    time.sleep(0.1)
    assert _no_leftover_sleep_children()

    r = report_from_json(json_path.read_text())
    assert all(f.failure == "interrupted" for f in r.failures)


def test_sigint_kills_shell_wrapped_subtree():
    s = suite(
        "S",
        bench("wrapped")
        .with_command(["sh", "-c", "sleep 10"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
        .with_runs(1),
    )

    t = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT))
    t.start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        Sequential().run(plan([s], None), None)
    elapsed = time.monotonic() - t0
    t.cancel()

    assert elapsed < 3.0
    time.sleep(0.1)
    # Neither the wrapping sh nor the inner sleep survive.
    out = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "", f"leftover children: {out.stdout!r}"


def test_relative_cmd_resolves_independently_of_subprocess_cwd(
    tmp_path: Path, monkeypatch
):
    """Regression: a relative command (e.g. ``./build/bin``) must still resolve
    after Popen chdir's into a different cwd. Without abspath() the OS would
    look for the binary relative to the *subprocess's* cwd and fail."""
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir()
    binary = bin_dir / "echo_hi"
    binary.write_text("#!/bin/sh\necho hi\n")
    binary.chmod(0o755)

    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()

    monkeypatch.chdir(tmp_path)  # invocation cwd has tools/echo_hi, but not elsewhere/
    s = suite(
        "X",
        bench("a")
        .with_command(["tools/echo_hi"])
        .with_cwd(other_dir)  # subprocess runs from elsewhere/
        .with_process_metric(Time())  # emits one `elapsed` sample on success
        .with_runs(2),
    )
    report = Sequential().run(plan([s], None), None)
    samples = _all_samples(report)
    # If the relative path leaked through, every spawn would fail.
    # abspath() in execute() prevents that.
    assert len(samples) == 2
    assert all(s.metric == "elapsed" for s in samples)


def test_default_metric_is_time():
    s = suite("s", bench("x").with_command(["true"]))
    report = Sequential().run(plan([s], None), None)
    # Time is a process metric, so the default `elapsed` lands in process_samples.
    assert [s.metric for s in report.runs[0].process_samples] == ["elapsed"]


def test_plan_default_params():
    s = suite("s", bench("a").with_command(["true"]))
    report = Sequential().run(plan([s]))
    assert len(report.runs) == 1


def test_plan_wraps_factory_failure_with_suite_name_and_command_output():
    err = subprocess.CalledProcessError(
        1, ["java", "-jar", "x.jar", "--list"], output=b"Error: could not open jar\n"
    )

    def boom(ctx):
        raise err

    s = suite("Renaissance Suite").factory(boom)
    with pytest.raises(SuiteMaterializationError) as ei:
        plan([s], None)

    msg = str(ei.value)
    assert "Renaissance Suite" in msg  # which suite failed
    assert "could not open jar" in msg  # captured subprocess output surfaced
    assert ei.value.__cause__ is err  # original chained for uncaught tracebacks


def test_run_resolves_reporter_factory_with_cli_state():
    seen: dict[str, object] = {}

    class _Rec(Reporter):
        pass

    def factory(ctx):
        seen["verbose"] = ctx.cli.verbose
        seen["dry"] = ctx.cli.dry
        seen["params"] = ctx.params
        return _Rec()

    s = suite("S", bench("a")).with_command(["true"]).with_process_metric(Time())
    run(s, reporter=factory, argv=["--dry", "--verbose"])
    assert seen == {"verbose": True, "dry": True, "params": None}

    seen.clear()
    run(s, reporter=factory, argv=["--dry"])
    assert seen["verbose"] is False
