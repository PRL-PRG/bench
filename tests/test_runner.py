"""Sequential, Parallel, and Dry runners."""

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from benchr import (
    CompositeReporter, CsvReporter, Dry, FloatPerLine, JsonReporter, Parallel, Sequential,
    Time, bench, report_from_json, suite,
)


def _all_samples(report):
    return [s for r in report.runs for s in r.samples]


def _runs_with_samples(report):
    return [(r, s) for r in report.runs for s in r.samples]


def _sleep_suite(name: str = "S", duration: float = 0.05, runs: int = 2):
    return suite(name, *[
        bench(f"b{i}")
            .with_command(["sh", "-c", f"sleep {duration}"])
            .with_cwd(Path("/tmp"))
            .with_metric(Time())
            .runs(runs)
        for i in range(2)
    ])


def test_sequential_basic():
    report = Sequential().run([_sleep_suite()], ctx=None)
    assert len(_all_samples(report)) == 4  # 2 benchmarks × 2 runs


def test_sequential_three_runs_yields_three_samples():
    s = suite("X", bench("p")
              .with_command(["sh", "-c", "echo 0.5"])
              .with_cwd(Path("/tmp"))
              .with_metric(FloatPerLine("s").lower_is_better())
              .runs(3))
    report = Sequential().run([s], ctx=None)
    pairs = _runs_with_samples(report)
    assert len(pairs) == 3
    assert [r.run for r, _ in pairs] == [1, 2, 3]


def test_sequential_runs_bounded_policy_to_completion_despite_failures(tmp_path: Path):
    # Bounded policy (FixedRuns) IS the contract: a crashing benchmark must
    # still complete N attempts. The consecutive-failure cap exists only as a
    # backstop for unbounded policies.
    s = suite("F", bench("bad")
              .with_command(["false"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .runs(10))
    out = tmp_path / "r.json"
    report = Sequential(reporter=JsonReporter(out), max_consecutive_failures=3).run([s], ctx=None)
    assert _all_samples(report) == []  # failed runs emit no metrics
    r = report_from_json(out.read_text())
    assert len(r.failures) == 10
    assert all(f.returncode != 0 for f in r.failures)


def test_sequential_aborts_unbounded_policy_on_consecutive_failures(tmp_path: Path):
    from benchr import CoefficientOfVariation
    s = suite("F", bench("bad")
              .with_command(["false"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_measure(CoefficientOfVariation("elapsed")))  # unbounded
    out = tmp_path / "r.json"
    report = Sequential(reporter=JsonReporter(out), max_consecutive_failures=3).run([s], ctx=None)
    assert _all_samples(report) == []
    r = report_from_json(out.read_text())
    assert len(r.failures) == 3
    assert all(f.returncode != 0 for f in r.failures)


def test_parallel_runs_faster_than_sequential():
    s = _sleep_suite(duration=0.1, runs=2)
    t0 = time.monotonic()
    Sequential().run([s], ctx=None)
    seq_t = time.monotonic() - t0
    t0 = time.monotonic()
    Parallel(workers=4).run([s], ctx=None)
    par_t = time.monotonic() - t0
    assert par_t < seq_t * 0.7, f"parallel must be faster: {par_t=:.2f}, {seq_t=:.2f}"


def test_parallel_parallelizable_only_for_bounded_independent():
    from benchr.runner.parallel import Parallel as P_
    fr = bench("a").with_command(["true"]).with_cwd(Path("/tmp")).with_metric(Time()).runs(3)
    assert P_._parallelizable(fr)
    from benchr import CoefficientOfVariation
    cov_b = fr.with_measure(CoefficientOfVariation("elapsed").at_most(5))
    assert not P_._parallelizable(cov_b)


def test_parallel_records_every_run():
    s = _sleep_suite(duration=0.01, runs=3)  # 2 benchmarks × 3 runs
    report = Parallel(workers=4).run([s], ctx=None)
    assert len(_all_samples(report)) == 6


def test_parallel_rejects_unbounded_policy():
    import pytest
    from benchr import CoefficientOfVariation
    s = suite("U", bench("conv")
              .with_command(["true"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_measure(CoefficientOfVariation("elapsed")))  # unbounded
    with pytest.raises(ValueError, match="--runs"):
        Parallel(workers=2).run([s], ctx=None)


def test_dry_no_subprocess():
    # Use a non-existent command — Dry must not spawn anything.
    s = suite("X", bench("a")
              .with_command(["/nonexistent_binary_xyz"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .runs(5))
    out = _all_samples(Dry().run([s], ctx=None))
    assert out == []


def test_dry_compact_prints_one_line_per_execution(capsys):
    s = suite("X", bench("a")
              .with_command(["/bin/echo", "hi"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .runs(5))
    Dry().run([s], ctx=None)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 5
    for i, ln in enumerate(lines, start=1):
        assert ln == f"X/a #{i} [measure]: /bin/echo hi"
    assert "cwd:" not in out and "plan:" not in out


def test_dry_compact_enumerates_warmup_and_measure(capsys):
    s = suite("X", bench("a")
              .with_command(["/bin/echo", "hi"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_warmup(2)
              .runs(3))
    Dry().run([s], ctx=None)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 5
    assert lines[0] == "X/a #1 [warmup]: /bin/echo hi"
    assert lines[1] == "X/a #2 [warmup]: /bin/echo hi"
    assert lines[2] == "X/a #1 [measure]: /bin/echo hi"
    assert lines[3] == "X/a #2 [measure]: /bin/echo hi"
    assert lines[4] == "X/a #3 [measure]: /bin/echo hi"


def test_dry_compact_unbounded_policy_prints_single_marker(capsys):
    from benchr import CoefficientOfVariation
    s = suite("X", bench("a")
              .with_command(["/bin/echo", "hi"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .with_measure(CoefficientOfVariation("elapsed")))
    Dry().run([s], ctx=None)
    out = capsys.readouterr().out
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1
    assert "[unbounded]" in lines[0]


def test_dry_verbose_prints_full_block_per_execution(capsys):
    s = suite("X", bench("a")
              .with_command(["/bin/echo", "hi"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .runs(5))
    Dry(verbose=True).run([s], ctx=None)
    out = capsys.readouterr().out
    assert "command:    /bin/echo hi" in out
    assert "cwd:" in out
    assert "metrics:    Time" in out
    assert out.count("command:    /bin/echo hi") == 5
    assert out.count("X/a #1 [measure]") == 1
    assert out.count("X/a #5 [measure]") == 1


def test_sequential_verbose_echoes_block_and_still_runs(capsys):
    report = Sequential(verbose=True).run([_sleep_suite(runs=2)], ctx=None)
    out = capsys.readouterr().out
    assert "command:" in out and "warmup x0, measure x2" in out
    # Verbose is an echo only — the benchmarks still execute.
    assert len(_all_samples(report)) == 4


def test_sequential_quiet_prints_no_block(capsys):
    Sequential().run([_sleep_suite(runs=1)], ctx=None)
    out = capsys.readouterr().out
    assert "command:" not in out and "plan:" not in out


def test_mixed_reporter_lifecycle(tmp_path: Path):
    json_path = tmp_path / "r.json"
    csv_path = tmp_path / "r.csv"
    sinks = CompositeReporter(JsonReporter(json_path), CsvReporter(csv_path))
    Sequential(reporter=sinks).run([_sleep_suite(runs=1)], ctx=None)
    assert json_path.exists() and csv_path.exists()
    assert json_path.read_text().count('"metric"') >= 2


def _no_leftover_sleep_children() -> bool:
    """No child ``sleep`` processes survive under the current pid."""
    out = subprocess.run(
        ["pgrep", "-P", str(os.getpid()), "sleep"],
        capture_output=True, text=True,
    )
    return out.stdout.strip() == ""


def test_sigint_kills_subprocesses_sequential(tmp_path: Path):
    import pytest
    s = suite("S", bench("slow")
              .with_command(["sleep", "10"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .runs(3))
    json_path = tmp_path / "r.json"

    t = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT))
    t.start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        Sequential(reporter=JsonReporter(json_path)).run([s], ctx=None)
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
    import pytest
    s = suite("S", *[
        bench(f"slow{i}")
            .with_command(["sleep", "10"])
            .with_cwd(Path("/tmp"))
            .with_metric(Time())
            .runs(1)
        for i in range(4)
    ])
    json_path = tmp_path / "r.json"

    t = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
    t.start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        Parallel(workers=4, reporter=JsonReporter(json_path)).run([s], ctx=None)
    elapsed = time.monotonic() - t0
    t.cancel()

    assert elapsed < 3.0, f"parallel runner did not unwind after SIGINT: {elapsed:.2f}s"
    time.sleep(0.1)
    assert _no_leftover_sleep_children()

    r = report_from_json(json_path.read_text())
    assert all(f.failure == "interrupted" for f in r.failures)


def test_sigint_kills_shell_wrapped_subtree():
    import pytest
    s = suite("S", bench("wrapped")
              .with_command(["sh", "-c", "sleep 10"])
              .with_cwd(Path("/tmp"))
              .with_metric(Time())
              .runs(1))

    t = threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGINT))
    t.start()
    t0 = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        Sequential().run([s], ctx=None)
    elapsed = time.monotonic() - t0
    t.cancel()

    assert elapsed < 3.0
    time.sleep(0.1)
    # Neither the wrapping sh nor the inner sleep survive.
    out = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        capture_output=True, text=True,
    )
    assert out.stdout.strip() == "", f"leftover children: {out.stdout!r}"


def test_relative_cmd_resolves_independently_of_subprocess_cwd(tmp_path: Path, monkeypatch):
    """Regression: a relative command (e.g. ``./build/bin``) must still resolve
    after Popen chdir's into a different cwd. Without abspath() the OS would
    look for the binary relative to the *subprocess's* cwd and fail."""
    bin_dir = tmp_path / "tools"
    bin_dir.mkdir()
    binary = bin_dir / "echo_hi"
    binary.write_text('#!/bin/sh\necho hi\n')
    binary.chmod(0o755)

    other_dir = tmp_path / "elsewhere"
    other_dir.mkdir()

    monkeypatch.chdir(tmp_path)  # invocation cwd has tools/echo_hi, but not elsewhere/
    s = suite("X", bench("a")
              .with_command(["tools/echo_hi"])
              .with_cwd(other_dir)            # subprocess runs from elsewhere/
              .with_metric(Time())          # emits one `elapsed` sample on success
              .runs(2))
    report = Sequential(max_consecutive_failures=2).run([s], ctx=None)
    samples = _all_samples(report)
    # If the relative path leaked through, every spawn would fail and we'd hit
    # max_consecutive_failures quickly. abspath() in execute() prevents that.
    assert len(samples) == 2
    assert all(s.metric == "elapsed" for s in samples)


def test_default_metric_is_time():
    s = suite("s", bench("x").with_command(["true"]))
    report = Sequential().run([s], ctx=None)
    assert [smp.metric for smp in report.runs[0].samples] == ["elapsed"]


def test_run_accepts_single_suite_and_default_ctx():
    s = suite("s", bench("a").with_command(["true"]))
    report = Sequential().run(s)
    assert len(report.runs) == 1
