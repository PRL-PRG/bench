"""Benchmark: builders, expansion, schedule()."""

from pathlib import Path

import pytest

from benchr import FixedRuns, FloatPerLine, Time, bench, suite
from benchr.grammar.benchmark import UNSET


def _mat(b):
    """Resolve one benchmark against an otherwise-default suite."""
    return suite("S", b).materialize(ctx=None)[0]


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


def test_schedule_stamps_identity():
    sched = _mat(_base()).schedule(None, suite="S", run=3)
    assert (sched.suite, sched.benchmark, sched.run) == ("S", "b", 3)
    assert sched.execution.command == ("sh", "-c", "echo 1")


def test_missing_command_raises_on_schedule():
    with pytest.raises(RuntimeError, match="materialize"):
        bench("x").schedule(None, suite="S", run=1)


def test_unexpanded_axes_raise_on_schedule():
    b = bench("x").with_command(["true"]).with_matrix(vm=["v8", "jsc"])
    with pytest.raises(ValueError, match="unexpanded matrix axes"):
        b.schedule(None, suite="S", run=1)


def test_bench_kwargs_attach_to_data():
    b = bench("z", path=Path("zoo.lox"), size=42)
    assert b.path == Path("zoo.lox")
    assert b.size == 42


def test_immutability_via_with_methods():
    a = _base().with_runs(3)
    b = a.with_runs(5)
    assert a.runs != b.runs


def test_policy_defaults_resolve_via_suite():
    b = bench("x")
    assert b.warmup is UNSET and b.runs is UNSET
    m = _mat(b.with_command(["true"]))
    assert m.warmup == FixedRuns(0)
    assert m.runs == FixedRuns(1)


def test_unresolved_cwd_raises_on_schedule():
    b = bench("x").with_command(["true"])  # never materialized
    with pytest.raises(RuntimeError, match="materialize"):
        b.schedule(None, suite="s", run=1)


def test_unset_raises_on_any_use():
    with pytest.raises(RuntimeError, match="materialize"):
        UNSET(bench("x"), None)  # calling (command/env/label fns)
    with pytest.raises(RuntimeError, match="materialize"):
        UNSET.start()  # attribute access (policies)
    with pytest.raises(RuntimeError, match="materialize"):
        bool(UNSET)  # truth-testing (harness flag)


def test_with_stdin_str_is_encoded():
    b = _mat(bench("x").with_command(["cat"]).with_stdin("hello"))
    sched = b.schedule(None, suite="s", run=1)
    assert sched.execution.stdin == b"hello"


def test_with_stdin_bytes_passthrough():
    b = _mat(bench("x").with_command(["cat"]).with_stdin(b"\x00\x01"))
    sched = b.schedule(None, suite="s", run=1)
    assert sched.execution.stdin == b"\x00\x01"


def test_default_cwd_is_invokers_cwd():
    b = _mat(bench("x").with_command(["true"]))
    sched = b.schedule(None, suite="s", run=1)
    assert sched.execution.cwd == Path.cwd()


def test_with_metric_replaces_not_appends():
    b = bench("x").with_metric(Time()).with_metric(FloatPerLine("s"))
    assert len(b.metrics) == 1 and isinstance(b.metrics[0], FloatPerLine)


def test_with_metric_takes_several_in_one_call():
    b = bench("x").with_metric(Time(), FloatPerLine("s"))
    assert len(b.metrics) == 2


def test_with_matrix_replaces_axes():
    b = bench("x").with_matrix(a=[1]).with_matrix(b=[2])
    assert [n for n, _ in b.axes] == ["b"]


def test_add_matrix_skip_unions_rules_on_one_benchmark():
    b = (bench("x").with_command(["true"])
         .with_matrix(vm=["v8", "jsc"], size=[100, 500])
         .add_matrix_skip(vm="v8", size=500)
         .add_matrix_skip(vm="jsc", size=100))
    bs = suite("S", b).materialize(ctx=None)
    assert {(x.vm, x.size) for x in bs} == {("v8", 100), ("jsc", 500)}
