"""BenchmarkBuilder: builders, create()/materialize, per-variant builder fields."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from bench import FixedRuns, FloatPerLine, bench, suite
from bench.builder.context import Params
from bench.core.metric import StderrMetricSource, StdoutMetricSource


def _mat(b):
    """Resolve one factory against an otherwise-default suite."""
    return suite("S", b).materialize(Params())[0]


def _base():
    return (
        bench("b")
        .with_command(["sh", "-c", "echo 1"])
        .with_cwd(Path("/tmp"))
        .with_metric(FloatPerLine(StdoutMetricSource, "runtime", unit="s"))
    )


def test_runs_sugar_equivalent_to_fixed_runs():
    # `.runs` is stored as a builder now, and policies compare by identity, so
    # compare the resolved bound instead of the policy objects.
    a = _mat(_base().with_runs(3))
    b = _mat(_base().with_runs(FixedRuns(3)))
    assert a.runs.max_runs() == b.runs.max_runs() == 3


def test_materialize_stamps_identity():
    b = _mat(_base())
    assert (b.suite, b.name) == ("S", "b")
    assert b.invocation.command == ("sh", "-c", "echo 1")


def test_missing_command_raises_on_materialize():
    with pytest.raises(ValueError, match="missing a command"):
        suite("S", bench("x")).materialize(Params())


def test_bench_kwargs_attach_to_data():
    b = bench("z", path=Path("zoo.lox"), size=42)
    assert b.data["path"] == Path("zoo.lox")
    assert b.data["size"] == 42


def test_policy_defaults_resolve_via_suite():
    b = bench("x")
    # An unconfigured field is `None`; the resolver substitutes the default.
    assert b.warmup is None and b.runs is None
    m = _mat(b.with_command(["true"]))
    assert isinstance(m.warmup, FixedRuns) and m.warmup.max_runs() == 0
    assert isinstance(m.runs, FixedRuns) and m.runs.max_runs() == 1


def test_with_stdin_str_is_encoded():
    b = _mat(bench("x").with_command(["cat"]).with_stdin("hello"))
    assert b.invocation.stdin == b"hello"


def test_with_stdin_bytes_passthrough():
    b = _mat(bench("x").with_command(["cat"]).with_stdin(b"\x00\x01"))
    assert b.invocation.stdin == b"\x00\x01"


def test_default_cwd_is_invokers_cwd():
    b = _mat(bench("x").with_command(["true"]))
    assert b.invocation.cwd == Path.cwd()


def test_with_metric_accumulates():
    # `with_metric` now ACCUMULATES across calls (it does not replace).
    b = _mat(
        bench("x")
        .with_command(["true"])
        .with_metric(FloatPerLine(StdoutMetricSource, "runtime", unit="ms"))
        .with_metric(FloatPerLine(StdoutMetricSource, "runtime", unit="s"))
    )
    assert len(b.metrics) == 2
    assert [m.unit for m in b.metrics if isinstance(m, FloatPerLine)] == ["ms", "s"]


def test_with_metric_takes_several_in_one_call():
    b = _mat(
        bench("x")
        .with_command(["true"])
        .with_metric(
            FloatPerLine(StdoutMetricSource, "runtime", unit="ms"),
            FloatPerLine(StdoutMetricSource, "runtime", unit="s"),
        )
    )
    assert len(b.metrics) == 2


def test_add_metric_appends_with_source():
    b = _mat(
        bench("x")
        .with_command(["true"])
        .with_metric(FloatPerLine(StdoutMetricSource, "runtime", unit="ms"))
        .with_metric(FloatPerLine(StderrMetricSource, "runtime", unit="s"))
    )
    assert len(b.metrics) == 2


def test_with_matrix_accumulates_dimensions():
    # `with_matrix` merges across calls rather than replacing.
    b = bench("x").with_matrix(a=[1]).with_matrix(b=[2])
    assert sorted(b.matrix) == ["a", "b"]


def test_add_matrix_skip_unions_rules_on_one_benchmark():
    # Multiple skips compose as OR: each drops the cell matching all of its own
    # kwargs.
    b = (
        bench("x")
        .with_command(["true"])
        .with_matrix(vm=["v8", "jsc"], size=[100, 500])
        .add_matrix_skip(vm="v8", size=500)
        .add_matrix_skip(vm="jsc", size=100)
    )
    bs = suite("S", b).materialize(Params())
    assert {(x.data["vm"], x.data["size"]) for x in bs} == {("v8", 100), ("jsc", 500)}


def test_matrix_axis_callable_expands_to_returned_values():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(vm=lambda ctx: ["clox", "jlox"])
    )
    bs = suite("S", b).materialize(Params())
    assert {x.data["vm"] for x in bs} == {"clox", "jlox"}
    assert {x.variant_label for x in bs} == {"vm=clox", "vm=jlox"}


def test_matrix_axis_callable_reads_params():
    class P(Params):
        sizes: list

    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(size=lambda ctx: ctx.params.sizes)
    )
    bs = suite("S", b).materialize(P(sizes=[1, 2, 3]))
    assert {x.data["size"] for x in bs} == {1, 2, 3}


def test_matrix_axis_callable_mixes_with_static():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(vm=lambda ctx: ["clox", "jlox"], size=[100, 200])
    )
    bs = suite("S", b).materialize(Params())
    assert {(x.data["vm"], x.data["size"]) for x in bs} == {
        ("clox", 100),
        ("clox", 200),
        ("jlox", 100),
        ("jlox", 200),
    }


def test_matrix_axis_callable_sees_benchmark_name():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(tag=lambda ctx: [ctx.benchmark])
    )
    bs = suite("S", b).materialize(Params())
    assert {x.data["tag"] for x in bs} == {"x"}


def test_with_matrix_accepts_callable_axis():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(size=[100])
        .with_matrix(vm=lambda ctx: ["clox", "jlox"])
    )
    bs = suite("S", b).materialize(Params())
    assert {(x.data["vm"], x.data["size"]) for x in bs} == {
        ("clox", 100),
        ("jlox", 100),
    }


def test_suite_level_matrix_axis_callable_applies_per_benchmark():
    s = (
        suite("S")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .generator(lambda _ctx: [bench("a"), bench("b")])
        .with_matrix(tag=lambda ctx: [ctx.benchmark])
    )
    bs = s.materialize(Params())
    assert {(x.name, x.data["tag"]) for x in bs} == {("a", "a"), ("b", "b")}


def test_matrix_axis_callable_empty_yields_no_variants():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(vm=lambda ctx: [])
    )
    bs = suite("S", b).materialize(Params())
    assert bs == []


# ----- builder (per-variant) fields ---------------------------------------


def test_value_field_bare_callable_resolved_per_variant():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(size=[100, 200])
        .with_timeout(lambda ctx: ctx.data.size / 1000)
    )
    bs = suite("S", b).materialize(Params())
    assert {x.invocation.timeout for x in bs} == {0.1, 0.2}


def test_dynamic_runs_resolved_per_variant():
    b = (
        bench("x")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_matrix(n=[2, 5])
        .with_runs(lambda ctx: FixedRuns(ctx.data.n))
    )
    bs = suite("S", b).materialize(Params())
    assert {x.runs.max_runs() for x in bs} == {2, 5}


def test_behavior_field_bare_callable_is_the_value_not_a_builder():
    def fn(_r):
        return None

    b = _mat(bench("x").with_command(["true"]).with_success(fn))
    assert b.success is fn


def test_with_success_fn_resolves_factory_against_ctx():
    def marker(_r):
        return None

    def factory(ctx):
        assert ctx.benchmark == "x"
        return marker

    b = _mat(bench("x").with_command(["true"]).with_success_factory(factory))
    assert b.success is marker  # factory(ctx) result is used as the policy
