"""End-to-end: real subprocess + full pipeline through Sample stats."""

from pathlib import Path

import pytest

from benchr import (
    CoefficientOfVariation, FixedRuns, P, Sequential, bench, suite,
)


def test_e2e_sleep_runs_produce_expected_count():
    s = suite("S", bench("a")
              .with_command(["sleep", "0.02"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time())
              .runs(3))
    samples = Sequential().run([s], ctx=None)
    elapsed = [s.value for s in samples if s.metric == "elapsed"]
    assert len(elapsed) == 3
    assert all(0.01 < v < 0.5 for v in elapsed)


def test_e2e_warmup_then_measure():
    s = suite("S", bench("a")
              .with_command(["sh", "-c", "echo 0.01"])
              .with_cwd(Path("/tmp"))
              .with_process(P.float_per_line("s").lower_is_better())
              .with_warmup(2)
              .runs(2))
    samples = Sequential().run([s], ctx=None)
    phases = [s.phase for s in samples]
    assert phases == ["warmup", "warmup", "measure", "measure"]


def test_e2e_command_not_found_marks_failure():
    s = suite("F", bench("missing")
              .with_command(["/no_such_binary_xyzzy"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time().on_failure(P.constant("failed", 1.0)))
              .runs(3))
    samples = Sequential(max_consecutive_failures=3).run([s], ctx=None)
    assert all(s.metric == "failed" for s in samples)
    assert len(samples) == 3


def test_e2e_timeout_marks_failure():
    s = suite("F", bench("hang")
              .with_command(["sh", "-c", "sleep 5"])
              .with_cwd(Path("/tmp"))
              .with_process(P.time().on_failure(P.constant("failed", 1.0)))
              .with_timeout(0.05)
              .runs(1))
    # 1 successful run required → infinite retries until cap. Use max_consec=2.
    samples = Sequential(max_consecutive_failures=2).run([s], ctx=None)
    assert all(s.metric == "failed" for s in samples)
