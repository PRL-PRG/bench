"""BenchmarkBuilder: builders, create()/materialize, per-variant builder fields."""

from pathlib import Path

import pytest

from benchr import FixedRuns, FloatPerLine, Time, bench, suite
from benchr.grammar.benchmark import UNSET


def _mat(b):
    """Resolve one factory against an otherwise-default suite."""
    return suite("S", b).materialize(None)[0]


def _base():
    return (
        bench("b")
        .with_command(["sh", "-c", "echo 1"])
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine("s"))
    )


def test_runs_sugar_equivalent_to_fixed_runs():
    # `.runs` is stored as a builder now, so compare the resolved values.
    a = _mat(_base().with_runs(3))
    b = _mat(_base().with_runs(FixedRuns(3)))
    assert a.runs == b.runs == FixedRuns(3)


def test_materialize_stamps_identity():
    b = _mat(_base())
    assert (b.suite, b.name) == ("S", "b")
    assert b.execution.command == ("sh", "-c", "echo 1")


def test_missing_command_raises_on_materialize():
    with pytest.raises(ValueError, match="no command"):
        suite("S", bench("x")).materialize(None)


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


def test_unresolved_cwd_raises_on_create():
    # never materialized: command set but cwd/env still UNSET
    b = bench("x").with_command(["true"])
    with pytest.raises(RuntimeError, match="unset"):
        list(b.create(None, suite="s"))


def test_unset_raises_on_any_use():
    with pytest.raises(RuntimeError, match="unset"):
        UNSET(None)  # calling (command/env/label fns)
    with pytest.raises(RuntimeError, match="unset"):
        UNSET.start()  # attribute access (policies)
    with pytest.raises(RuntimeError, match="unset"):
        bool(UNSET)  # truth-testing (harness flag)


def test_with_stdin_str_is_encoded():
    b = _mat(bench("x").with_command(["cat"]).with_stdin("hello"))
    assert b.execution.stdin == b"hello"


def test_with_stdin_bytes_passthrough():
    b = _mat(bench("x").with_command(["cat"]).with_stdin(b"\x00\x01"))
    assert b.execution.stdin == b"\x00\x01"


def test_default_cwd_is_invokers_cwd():
    b = _mat(bench("x").with_command(["true"]))
    assert b.execution.cwd == Path.cwd()


def test_with_metric_replaces_not_appends():
    b = _mat(
        bench("x").with_command(["true"]).with_metric(Time()).with_metric(FloatPerLine("s"))
    )
    assert len(b.metrics) == 1 and isinstance(b.metrics[0], FloatPerLine)


def test_with_metric_takes_several_in_one_call():
    b = _mat(bench("x").with_command(["true"]).with_metric(Time(), FloatPerLine("s")))
    assert len(b.metrics) == 2


def test_with_matrix_replaces_dimensions():
    b = bench("x").with_matrix(a=[1]).with_matrix(b=[2])
    assert list(b.matrix) == ["b"]


def test_add_matrix_skip_unions_rules_on_one_benchmark():
    b = (bench("x").with_command(["true"])
         .with_matrix(vm=["v8", "jsc"], size=[100, 500])
         .add_matrix_skip(vm="v8", size=500)
         .add_matrix_skip(vm="jsc", size=100))
    bs = suite("S", b).materialize(None)
    assert {(x.vm, x.size) for x in bs} == {("v8", 100), ("jsc", 500)}


# ----- builder (per-variant) fields ---------------------------------------


def test_value_field_bare_callable_resolved_per_variant():
    b = (bench("x").with_command(["true"]).with_cwd(Path("/tmp"))
         .with_matrix(size=[100, 200])
         .with_timeout(lambda ctx: ctx.matrix.size / 1000))
    bs = suite("S", b).materialize(None)
    assert {x.execution.timeout for x in bs} == {0.1, 0.2}


def test_dynamic_runs_resolved_per_variant():
    b = (bench("x").with_command(["true"]).with_cwd(Path("/tmp"))
         .with_matrix(n=[2, 5])
         .with_runs(lambda ctx: FixedRuns(ctx.matrix.n)))
    bs = suite("S", b).materialize(None)
    assert {x.runs.max_runs() for x in bs} == {2, 5}


def test_behavior_field_bare_callable_is_the_value_not_a_builder():
    def fn(_r):
        return None

    b = _mat(bench("x").with_command(["true"]).with_success(fn))
    assert b.success is fn
