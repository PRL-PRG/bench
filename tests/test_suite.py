"""Suite: propagation, matrix, from_files, filter, materialize."""

from pathlib import Path

from benchr import FixedRuns, Time, bench, suite


def _b(name: str):
    return bench(name)


def test_with_runs_propagates():
    s = suite("S", _b("a"), _b("b")).runs(7)
    assert all(b.measure == FixedRuns(7) for b in s.benchmarks)


def test_with_runs_preserves_benchmark_override():
    a = _b("a").with_measure(FixedRuns(5))
    s = suite("S", a, _b("b")).runs(10)
    measure_values = [b.measure for b in s.benchmarks]
    assert measure_values[0] == FixedRuns(5)
    assert measure_values[1] == FixedRuns(10)


def test_with_command_propagates_when_unset():
    s = suite("S", _b("a")).with_command(["x"])
    assert s.benchmarks[0].command == ["x"]


def test_with_env_merges():
    from collections.abc import Mapping

    a = _b("a").with_env({"X": "1", "Y": "from_b"})
    s = suite("S", a).with_env({"Y": "from_s", "Z": "1"})
    env = s.benchmarks[0].env
    assert isinstance(env, Mapping)
    # benchmark wins for Y
    assert env["X"] == "1" and env["Y"] == "from_b" and env["Z"] == "1"


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
