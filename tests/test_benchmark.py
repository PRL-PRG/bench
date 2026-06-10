"""Benchmark + compile() coroutine."""

from pathlib import Path

import pytest

from benchr import (
    CoefficientOfVariation, FixedRuns, FloatPerLine, Sample, Time, bench, suite,
)
from benchr.grammar.policy import UNSET_POLICY


def _mat(b):
    """Resolve one benchmark against an otherwise-default suite."""
    return suite("S", b).materialize(ctx=None)[0]


def _pump(b, fake_value=1.0):
    """Helper: materialize ``b``, then drive its compile() coroutine to
    exhaustion, feeding back one Sample per yield. Returns the list of
    ScheduledExecutions seen."""
    out = []
    gen = _mat(b).compile(ctx=None, suite="S")
    try:
        sched = next(gen)
        while True:
            out.append(sched)
            s = Sample(metric="runtime", value=fake_value, unit="s",
                       lower_is_better=True)
            sched = gen.send([s])
    except StopIteration:
        pass
    return out


def _base():
    return (
        bench("b")
        .with_command(["sh", "-c", "echo 1"])
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("s"))
    )


def test_runs_sugar_equivalent_to_fixed_runs():
    a = _base().with_runs(3)
    b = _base().with_runs(FixedRuns(3))
    assert a.runs == b.runs


def test_compile_yields_three_measures():
    items = _pump(_base().with_runs(3))
    assert [s.phase for s in items] == ["runs", "runs", "runs"]
    assert [s.run for s in items] == [1, 2, 3]


def test_warmup_then_measure():
    items = _pump(_base().with_warmup(2).with_runs(3))
    assert [s.phase for s in items] == ["warmup", "warmup", "runs", "runs", "runs"]


def test_fixed_runs_zero_skips_phase():
    items = _pump(_base().with_warmup(0).with_runs(2))
    assert [s.phase for s in items] == ["runs", "runs"]


def test_missing_command_raises():
    with pytest.raises(ValueError, match="no command"):
        list(bench("x").compile(ctx=None, suite="S"))


def test_bench_kwargs_attach_to_data():
    b = bench("z", path=Path("zoo.lox"), size=42)
    assert b.path == Path("zoo.lox")
    assert b.size == 42


def test_cov_warmup_then_fixed_measure():
    cov = CoefficientOfVariation("runtime", threshold=0.0, window=3, min_runs=3)
    items = _pump(_base().with_warmup(cov).with_runs(2))
    # 3 warmup runs at value=1.0 saturate CoV (stdev=0), then 2 measure runs.
    assert [s.phase for s in items] == ["warmup"]*3 + ["runs"]*2


def test_immutability_via_with_methods():
    a = _base().with_runs(3)
    b = a.with_runs(5)
    assert a.runs != b.runs


def test_policy_defaults_resolve_via_suite():
    b = bench("x")
    assert b.warmup is UNSET_POLICY and b.runs is UNSET_POLICY
    m = _mat(b.with_command(["true"]))
    assert m.warmup == FixedRuns(0)
    assert m.runs == FixedRuns(1)


def test_unresolved_policy_raises_on_compile():
    b = bench("x").with_command(["true"])  # never materialized
    with pytest.raises(RuntimeError, match="materialize"):
        list(b.compile(ctx=None, suite="S"))


def test_unresolved_cwd_raises_on_schedule():
    b = bench("x").with_command(["true"])  # never materialized
    with pytest.raises(RuntimeError, match="cwd is unset"):
        b.schedule(None, suite="s", run=1, phase="runs")


def test_unset_command_raises_on_call():
    from benchr.grammar.benchmark import UNSET_COMMAND

    with pytest.raises(ValueError, match="no command"):
        UNSET_COMMAND(bench("x"), None)


def test_with_stdin_str_is_encoded():
    b = _mat(bench("x").with_command(["cat"]).with_stdin("hello"))
    sched = b.schedule(None, suite="s", run=1, phase="runs")
    assert sched.execution.stdin == b"hello"


def test_with_stdin_bytes_passthrough():
    b = _mat(bench("x").with_command(["cat"]).with_stdin(b"\x00\x01"))
    sched = b.schedule(None, suite="s", run=1, phase="runs")
    assert sched.execution.stdin == b"\x00\x01"


def test_default_cwd_is_invokers_cwd():
    b = _mat(bench("x").with_command(["true"]))
    sched = b.schedule(None, suite="s", run=1, phase="runs")
    assert sched.execution.cwd == Path.cwd()
