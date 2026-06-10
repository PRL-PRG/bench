"""Suite: lazy defaults, resolution, matrix, from_files, filter, materialize."""

from pathlib import Path

import pytest

from benchr import FixedRuns, Time, bench, suite


def _b(name: str):
    return bench(name)


def _mat(s):
    return s.materialize(ctx=None)


# ----- defaults resolve at materialize ------------------------------------


def test_runs_propagates():
    s = suite("S", _b("a"), _b("b")).with_command(["true"]).runs(7)
    assert all(b.measure == FixedRuns(7) for b in _mat(s))


def test_runs_preserves_benchmark_override():
    a = _b("a").with_measure(FixedRuns(5))
    s = suite("S", a, _b("b")).with_command(["true"]).runs(10)
    assert [b.measure for b in _mat(s)] == [FixedRuns(5), FixedRuns(10)]


def test_with_command_propagates_when_unset():
    s = suite("S", _b("a")).with_command(["x"])
    b = _mat(s)[0]
    assert b.command(b, None) == ("x",)


def test_with_command_order_independent():
    before = suite("S").with_command(["x"]).add(_b("a"))
    after = suite("S").add(_b("a")).with_command(["x"])
    b1, b2 = _mat(before)[0], _mat(after)[0]
    assert b1.command(b1, None) == b2.command(b2, None) == ("x",)


def test_defaults_reach_factory_benchmarks():
    s = suite("S").with_command(["true"]).runs(4).factory(lambda ctx: [bench("f")])
    b = _mat(s)[0]
    assert b.measure == FixedRuns(4)
    assert b.command(b, None) == ("true",)


def test_materialize_missing_command_fails_fast():
    with pytest.raises(ValueError, match="no command"):
        suite("S", _b("a")).materialize(ctx=None)


def test_with_env_merges():
    a = _b("a").with_command(["true"]).with_env({"X": "1", "Y": "from_b"})
    s = suite("S", a).with_env({"Y": "from_s", "Z": "1"})
    b = _mat(s)[0]
    env = b.env(b, None)
    # benchmark wins for Y
    assert env["X"] == "1" and env["Y"] == "from_b" and env["Z"] == "1"


def test_env_merge_both_callable():
    a = _b("a").with_command(["true"]).with_env(lambda b, ctx: {"X": b.name, "Y": "from_b"})
    s = suite("S", a).with_env(lambda b, ctx: {"Y": "from_s", "Z": "1"})
    b = _mat(s)[0]
    assert b.env(b, None) == {"X": "a", "Y": "from_b", "Z": "1"}


def test_suite_warmup_respects_explicit_zero():
    b = bench("x").with_warmup(0)
    s = suite("s", b).with_command(["true"]).with_warmup(3)
    assert _mat(s)[0].warmup == FixedRuns(0)


def test_suite_measure_respects_explicit_one():
    b = bench("x").with_measure(1)
    s = suite("s", b).with_command(["true"]).with_measure(9)
    assert _mat(s)[0].measure == FixedRuns(1)


def test_suite_with_success_propagates_and_respects_override():
    suite_fn = lambda e, r: None
    bench_fn = lambda e, r: "nope"
    s = (
        suite("s", bench("a"), bench("b").with_success(bench_fn))
        .with_command(["true"]).with_success(suite_fn)
    )
    resolved = _mat(s)
    assert resolved[0].success is suite_fn
    assert resolved[1].success is bench_fn


def test_suite_with_label_propagates_and_respects_override():
    suite_label = lambda b: "suite"
    bench_label = lambda b: "bench"
    s = (
        suite("s", bench("a"), bench("b").with_label(bench_label))
        .with_command(["true"]).with_label(suite_label)
    )
    resolved = _mat(s)
    assert resolved[0].label_fn is suite_label
    assert resolved[1].label_fn is bench_label


# ----- producers -----------------------------------------------------------


def test_filter():
    s = suite("S", _b("keep_me"), _b("skip"), _b("keep_too"))
    out = s.filter(lambda b: "skip" not in b.name)
    assert [b.name for b in out.benchmarks] == ["keep_me", "keep_too"]


def test_from_files(tmp_path: Path):
    (tmp_path / "a.lox").write_text("")
    (tmp_path / "b.lox").write_text("")
    (tmp_path / "skip.txt").write_text("")
    s = (
        suite("X").from_files(tmp_path, pattern=r"\.lox$")
        .with_command(["true"]).with_cwd(tmp_path).with_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(ctx=None))
    assert names == ["a", "b"]


def test_from_files_recursive_with_exclude(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.lox").write_text("")
    (sub / "nested.lox").write_text("")
    (tmp_path / "excl.lox").write_text("")
    s = (
        suite("X").from_files(tmp_path, pattern=r"\.lox$", exclude={"excl"})
        .with_command(["true"]).with_cwd(tmp_path).with_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(ctx=None))
    assert names == ["a", "sub/nested"]


def test_from_files_callable_root(tmp_path: Path):
    (tmp_path / "p.lox").write_text("")
    s = (
        suite("X").from_files(lambda ctx: ctx, pattern=r"\.lox$")
        .with_command(["true"]).with_cwd(tmp_path).with_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(ctx=tmp_path))
    assert names == ["p"]


# ----- matrix / skip --------------------------------------------------------


def test_with_matrix_expands_and_stamps_variant():
    s = (
        suite("M", _b("compute")
              .with_command(lambda b, ctx: ["x", "-" + b.opt])
              .with_matrix(opt=["O0", "O2"]))
        .with_cwd(Path("/tmp")).with_metric(Time())
    )
    benchmarks = list(s.materialize(ctx=None))
    assert len(benchmarks) == 2
    assert sorted(b.opt for b in benchmarks) == ["O0", "O2"]
    # Compile and check variant is stamped.
    g = benchmarks[0].compile(ctx=None, suite="M")
    sched = next(g)
    g.close()
    assert sched.variant == (("opt", "O0"),)


def test_suite_with_matrix_applies_to_all_benchmarks():
    s = (
        suite("M",
              _b("a").with_command(lambda b, ctx: ["x", b.vm]),
              _b("b").with_command(lambda b, ctx: ["y", b.vm]))
        .with_matrix(vm=["v8", "jsc"])
        .with_cwd(Path("/tmp")).with_metric(Time())
    )
    bs = list(s.materialize(ctx=None))
    names_vms = sorted((b.name, b.vm) for b in bs)
    assert names_vms == [("a", "jsc"), ("a", "v8"), ("b", "jsc"), ("b", "v8")]


def test_suite_axes_append_after_benchmark_axes():
    s = (
        suite("M", _b("a").with_command(["true"]).with_matrix(size=[1, 2]))
        .with_matrix(vm=["v8", "jsc"])
    )
    bs = _mat(s)
    assert len(bs) == 4
    # Benchmark axes expand first, suite axes after (stamped in that order).
    assert [k for k in bs[0].data if not k.startswith("_")] == ["size", "vm"]


def test_suite_axis_collision_with_benchmark_axis_raises():
    s = (
        suite("M", _b("a").with_command(["true"]).with_matrix(vm=["a"]))
        .with_matrix(vm=["b"])
    )
    with pytest.raises(ValueError, match="already declared"):
        s.materialize(ctx=None)


def test_with_skip_kwargs_drops_variant():
    s = (
        suite("M", _b("c")
              .with_command(lambda b, ctx: ["x", b.vm, str(b.size)])
              .with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .with_skip(vm="v8", size=500))
        .with_cwd(Path("/tmp")).with_metric(Time())
    )
    bs = list(s.materialize(ctx=None))
    assert len(bs) == 3
    assert ("v8", 500) not in {(b.vm, b.size) for b in bs}


def test_with_skip_predicate_drops_variant():
    s = (
        suite("M", _b("c")
              .with_command(lambda b, ctx: ["x", b.vm, str(b.size)])
              .with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .with_skip(lambda b: b.vm != "jsc"))
        .with_cwd(Path("/tmp")).with_metric(Time())
    )
    bs = list(s.materialize(ctx=None))
    assert all(b.vm == "jsc" for b in bs)
    assert sorted(b.size for b in bs) == [100, 500]


def test_suite_skip_unions_with_benchmark_skip():
    s = (
        suite("M", _b("c")
              .with_command(lambda b, ctx: ["x", b.vm, str(b.size)])
              .with_matrix(vm=["v8", "jsc"], size=[100, 500])
              .with_skip(vm="v8", size=500))
        .with_skip(vm="jsc", size=100)
    )
    bs = _mat(s)
    assert {(b.vm, b.size) for b in bs} == {("v8", 100), ("jsc", 500)}


def test_with_label_overrides_default():
    s = (
        suite("M", _b("c")
              .with_command(lambda b, ctx: ["true"])
              .with_matrix(arg=["one", "two"])
              .with_label(lambda b: f"<{b.arg}>"))
        .with_cwd(Path("/tmp")).with_metric(Time())
    )
    bs = list(s.materialize(ctx=None))
    assert sorted(b.variant_label() for b in bs) == ["<one>", "<two>"]


def test_command_axis_default_builder():
    """When axis name is `command` and no with_command set, axis value becomes cmd."""
    s = (
        suite("M", _b("c").with_matrix(command=[["echo", "a"], ["echo", "b"]]))
        .with_cwd(Path("/tmp")).with_metric(Time())
    )
    bs = list(s.materialize(ctx=None))
    scheds = [b.schedule(ctx=None, suite="M", run=1, phase="measure") for b in bs]
    assert sorted(s.execution.command for s in scheds) == [("echo", "a"), ("echo", "b")]


def test_command_axis_beats_suite_default():
    s = (
        suite("M", _b("c").with_matrix(command=[["echo", "axis"]]))
        .with_command(["echo", "suite"])
    )
    b = _mat(s)[0]
    assert b.command(b, None) == ("echo", "axis")
