"""Sequential, Parallel, and Dry runners."""

import time
from pathlib import Path

import pytest

from benchr import (
    Csv, Dry, FixedRuns, Json, Mixed, P, Parallel, Sequential,
    bench, report_from_json, suite,
)


def _sleep_suite(name: str = "S", duration: float = 0.05, runs: int = 2):
    return suite(name, *[
        bench(f"b{i}")
            .with_command(["sh", "-c", f"sleep {duration}"])
            .with_cwd(Path("/tmp"))
            .with_process(P.time())
            .runs(runs)
        for i in range(2)
    ])


def test_sequential_basic():
    samples = Sequential().run([_sleep_suite()], ctx=None).samples
    assert len(samples) == 4  # 2 benchmarks × 2 runs


def test_sequential_three_runs_yields_three_samples():
    s = suite("X", bench("p")
              .with_command(["sh", "-c", "echo 0.5"])
              .with_cwd(Path("/tmp"))
              .with_process(P.float_per_line("s").lower_is_better())
              .runs(3))
    samples = Sequential().run([s], ctx=None).samples
    assert len(samples) == 3
    assert [s.run for s in samples] == [1, 2, 3]


def test_sequential_aborts_on_consecutive_failures(tmp_path: Path):
    s = suite("F", bench("bad")
              .with_command(["false"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(10))
    out = tmp_path / "r.json"
    samples = Sequential(reporter=Json(out), max_consecutive_failures=3).run([s], ctx=None).samples
    assert samples == []  # failed runs emit no metrics
    # Aborted after 3 consecutive failures (before reaching runs(10)).
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


def test_parallel_fanout_eligible_only_for_fixed_runs():
    from benchr.runner.parallel import Parallel as P_
    fr = bench("a").with_command(["true"]).with_cwd(Path("/tmp")).with_process(P.time()).runs(3)
    assert P_._fanout_eligible(fr)
    from benchr import CoefficientOfVariation
    cov_b = fr.with_measure(CoefficientOfVariation("elapsed").at_most(5))
    assert not P_._fanout_eligible(cov_b)


def test_dry_runs_once_per_benchmark_no_subprocess():
    # Use a non-existent command — Dry must not spawn anything.
    s = suite("X", bench("a")
              .with_command(["/nonexistent_binary_xyz"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(5))
    out = Dry().run([s], ctx=None).samples
    assert out == []


def test_dry_compact_prints_command_only(capsys):
    s = suite("X", bench("a")
              .with_command(["/bin/echo", "hi"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(5))
    Dry().run([s], ctx=None)
    out = capsys.readouterr().out
    assert "X/a: /bin/echo hi" in out
    assert "cwd:" not in out and "plan:" not in out


def test_dry_verbose_prints_full_block(capsys):
    s = suite("X", bench("a")
              .with_command(["/bin/echo", "hi"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(5))
    Dry(verbose=True).run([s], ctx=None)
    out = capsys.readouterr().out
    assert "command: /bin/echo hi" in out
    assert "cwd:" in out
    assert "plan:    measure x5" in out


def test_sequential_verbose_echoes_block_and_still_runs(capsys):
    report = Sequential(verbose=True).run([_sleep_suite(runs=2)], ctx=None)
    out = capsys.readouterr().out
    assert "command:" in out and "plan:    measure x2" in out
    # Verbose is an echo only — the benchmarks still execute.
    assert len(report.samples) == 4


def test_sequential_quiet_prints_no_block(capsys):
    Sequential().run([_sleep_suite(runs=1)], ctx=None)
    out = capsys.readouterr().out
    assert "command:" not in out and "plan:" not in out


def test_mixed_reporter_lifecycle(tmp_path: Path):
    json_path = tmp_path / "r.json"
    csv_path = tmp_path / "r.csv"
    sinks = Mixed(Json(json_path), Csv(csv_path))
    Sequential(reporter=sinks).run([_sleep_suite(runs=1)], ctx=None)
    assert json_path.exists() and csv_path.exists()
    assert json_path.read_text().count('"metric"') >= 2


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
              .with_process(P.time())          # emits one `elapsed` sample on success
              .runs(2))
    samples = Sequential(max_consecutive_failures=2).run([s], ctx=None).samples
    # If the relative path leaked through, every spawn would fail and we'd hit
    # max_consecutive_failures quickly. abspath() in execute() prevents that.
    assert len(samples) == 2
    assert all(s.metric == "elapsed" for s in samples)
