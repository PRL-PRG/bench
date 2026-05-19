"""Sequential, Parallel, and Dry runners."""

import time
from pathlib import Path

import pytest

from benchr import (
    Csv, Dry, FixedRuns, Json, Mixed, P, Parallel, Sequential,
    bench, suite,
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
    samples = Sequential().run([_sleep_suite()], ctx=None)
    assert len(samples) == 4  # 2 benchmarks × 2 runs


def test_sequential_three_runs_yields_three_samples():
    s = suite("X", bench("p")
              .with_command(["sh", "-c", "echo 0.5"])
              .with_cwd(Path("/tmp"))
              .with_process(P.float_per_line("s").lower_is_better())
              .runs(3))
    samples = Sequential().run([s], ctx=None)
    assert len(samples) == 3
    assert [s.run for s in samples] == [1, 2, 3]


def test_sequential_aborts_on_consecutive_failures():
    s = suite("F", bench("bad")
              .with_command(["false"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time().on_failure(P.constant("failed", 1.0)))
              .runs(10))
    samples = Sequential(max_consecutive_failures=3).run([s], ctx=None)
    # 3 attempts, each emitting one 'failed' sample.
    assert sum(1 for x in samples if x.metric == "failed") == 3


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
    out = Dry().run([s], ctx=None)
    assert out == []


def test_mixed_reporter_lifecycle(tmp_path: Path):
    json_path = tmp_path / "r.json"
    csv_path = tmp_path / "r.csv"
    sinks = Mixed(Json(json_path), Csv(csv_path))
    Sequential(reporter=sinks).run([_sleep_suite(runs=1)], ctx=None)
    assert json_path.exists() and csv_path.exists()
    assert json_path.read_text().count('"metric"') >= 2
