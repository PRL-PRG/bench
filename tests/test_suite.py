"""SuiteBuilder: lazy defaults, resolution, matrix, from_files, filter, materialize."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from bench import (
    FixedRuns,
    FloatPerLine,
    SharedBenchParams,
    Time,
    bench,
    from_files,
    suite,
)


def _b(name: str):
    return bench(name)


def _mat(s):
    return s.materialize(None)


# ----- defaults resolve at materialize ------------------------------------


def test_runs_propagates():
    s = suite("S", _b("a"), _b("b")).with_command(["true"]).with_runs(7)
    assert all(b.runs == FixedRuns(7) for b in _mat(s))


def test_runs_preserves_benchmark_override():
    a = _b("a").with_runs(FixedRuns(5))
    s = suite("S", a, _b("b")).with_command(["true"]).with_runs(10)
    assert [b.runs for b in _mat(s)] == [FixedRuns(5), FixedRuns(10)]


# ----- suite defaults accept (ctx) -> value builders ----------------------


def test_with_runs_accepts_ctx_callable():
    s = (
        suite("S", _b("a"))
        .with_command(["true"])
        .with_runs(lambda ctx: FixedRuns(ctx.params.n))
    )
    b = s.materialize(SimpleNamespace(n=4))[0]
    assert b.runs == FixedRuns(4)


def test_with_warmup_accepts_ctx_callable():
    s = (
        suite("S", _b("a"))
        .with_command(["true"])
        .with_warmup(lambda ctx: FixedRuns(ctx.params.w))
    )
    b = s.materialize(SimpleNamespace(w=2))[0]
    assert b.warmup == FixedRuns(2)


def test_with_timeout_accepts_ctx_callable():
    s = (
        suite("S", _b("a"))
        .with_command(["true"])
        .with_timeout(lambda ctx: float(ctx.params.t))
    )
    b = s.materialize(SimpleNamespace(t=30))[0]
    assert b.execution.timeout == 30.0


def test_with_metric_accepts_ctx_callable():
    m = FloatPerLine("s")
    s = suite("S", _b("a")).with_command(["true"]).with_metric(lambda ctx: (m,))
    b = s.materialize(None)[0]
    assert [im for im, _src in b.iteration_metrics] == [m]


def test_suite_callable_runs_still_loses_to_benchmark_override():
    a = _b("a").with_runs(FixedRuns(5))
    s = (
        suite("S", a, _b("b"))
        .with_command(["true"])
        .with_runs(lambda ctx: FixedRuns(ctx.params.n))
    )
    assert [b.runs for b in s.materialize(SimpleNamespace(n=9))] == [
        FixedRuns(5),
        FixedRuns(9),
    ]


def test_with_command_propagates_when_unset():
    s = suite("S", _b("a")).with_command(["x"])
    b = _mat(s)[0]
    assert b.execution.command == ("x",)


def test_with_command_order_independent():
    before = suite("S").with_command(["x"]).add(_b("a"))
    after = suite("S").add(_b("a")).with_command(["x"])
    b1, b2 = _mat(before)[0], _mat(after)[0]
    assert b1.execution.command == b2.execution.command == ("x",)


def test_defaults_reach_factory_benchmarks():
    s = suite("S").with_command(["true"]).with_runs(4).factory(lambda ctx: [bench("f")])
    b = _mat(s)[0]
    assert b.runs == FixedRuns(4)
    assert b.execution.command == ("true",)


def test_materialize_missing_command_fails_fast():
    with pytest.raises(ValueError, match="no command"):
        suite("S", _b("a")).materialize(None)


def test_with_env_merges():
    a = _b("a").with_command(["true"]).with_env({"X": "1", "Y": "from_b"})
    s = suite("S", a).with_env({"Y": "from_s", "Z": "1"})
    b = _mat(s)[0]
    env = b.execution.env
    # benchmark wins for Y
    assert env["X"] == "1" and env["Y"] == "from_b" and env["Z"] == "1"


def test_env_merge_both_callable():
    a = (
        _b("a")
        .with_command(["true"])
        .with_env(lambda ctx: {"X": ctx.benchmark or "", "Y": "from_b"})
    )
    s = suite("S", a).with_env(lambda ctx: {"Y": "from_s", "Z": "1"})
    b = _mat(s)[0]
    assert b.execution.env == {"X": "a", "Y": "from_b", "Z": "1"}


def test_suite_warmup_respects_explicit_zero():
    b = bench("x").with_warmup(0)
    s = suite("s", b).with_command(["true"]).with_warmup(3)
    assert _mat(s)[0].warmup == FixedRuns(0)


def test_suite_measure_respects_explicit_one():
    b = bench("x").with_runs(1)
    s = suite("s", b).with_command(["true"]).with_runs(9)
    assert _mat(s)[0].runs == FixedRuns(1)


def test_suite_with_success_propagates_and_respects_override():
    suite_fn = lambda r: None
    bench_fn = lambda r: "nope"
    s = (
        suite("s", bench("a"), bench("b").with_success(bench_fn))
        .with_command(["true"])
        .with_success(suite_fn)
    )
    resolved = _mat(s)
    assert resolved[0].success is suite_fn
    assert resolved[1].success is bench_fn


def test_suite_with_label_propagates_and_respects_override():
    suite_label = lambda b: "suite"
    bench_label = lambda b: "bench"
    s = (
        suite("s", bench("a"), bench("b").with_label(bench_label))
        .with_command(["true"])
        .with_label(suite_label)
    )
    resolved = _mat(s)
    assert resolved[0].variant_label == "suite"
    assert resolved[1].variant_label == "bench"


# ----- producers -----------------------------------------------------------


def test_filter():
    # Deferred: applies after expansion (per-variant) and is order-independent
    # (added before the benchmark it filters).
    s = (
        suite("S")
        .filter(lambda b: b.size != 500)
        .add(_b("c").with_matrix(size=[100, 500]))
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    assert sorted(b.size for b in _mat(s)) == [100]


def test_from_files(tmp_path: Path):
    (tmp_path / "a.lox").write_text("")
    (tmp_path / "b.lox").write_text("")
    (tmp_path / "skip.txt").write_text("")
    s = (
        suite("X", *from_files(tmp_path, pattern=r"\.lox$"))
        .with_command(["true"])
        .with_cwd(tmp_path)
        .with_process_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(None))
    assert names == ["a", "b"]


def test_from_files_recursive_with_exclude(tmp_path: Path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "a.lox").write_text("")
    (sub / "nested.lox").write_text("")
    (tmp_path / "excl.lox").write_text("")
    s = (
        suite("X", *from_files(tmp_path, pattern=r"\.lox$", exclude={"excl"}))
        .with_command(["true"])
        .with_cwd(tmp_path)
        .with_process_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(None))
    assert names == ["a", "sub/nested"]


def test_from_files_ctx_root_via_factory(tmp_path: Path):
    (tmp_path / "p.lox").write_text("")
    s = (
        suite("X")
        .factory(lambda ctx: from_files(ctx.params, pattern=r"\.lox$"))
        .with_command(["true"])
        .with_cwd(tmp_path)
        .with_process_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(tmp_path))
    assert names == ["p"]


# ----- matrix / skip --------------------------------------------------------


def test_with_matrix_expands_and_stamps_variant():
    s = (
        suite(
            "M",
            _b("compute")
            .with_command(lambda ctx: ["x", "-" + ctx.data.opt])
            .with_matrix(opt=["O0", "O2"]),
        )
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    benchmarks = list(s.materialize(None))
    assert len(benchmarks) == 2
    assert sorted(b.opt for b in benchmarks) == ["O0", "O2"]
    # The variant is stamped on the resolved benchmark.
    assert benchmarks[0].variant == (("opt", "O0"),)


def test_suite_with_matrix_applies_to_all_benchmarks():
    s = (
        suite(
            "M",
            _b("a").with_command(lambda ctx: ["x", ctx.data.vm]),
            _b("b").with_command(lambda ctx: ["y", ctx.data.vm]),
        )
        .with_matrix(vm=["v8", "jsc"])
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    bs = list(s.materialize(None))
    names_vms = sorted((b.name, b.vm) for b in bs)
    assert names_vms == [("a", "jsc"), ("a", "v8"), ("b", "jsc"), ("b", "v8")]


def test_suite_dimensions_append_after_benchmark_dimensions():
    s = suite("M", _b("a").with_command(["true"]).with_matrix(size=[1, 2])).with_matrix(
        vm=["v8", "jsc"]
    )
    bs = _mat(s)
    assert len(bs) == 4
    # Benchmark dimensions expand first, suite dimensions after (stamped in that order).
    assert [k for k in bs[0].data if not k.startswith("_")] == ["size", "vm"]


def test_suite_dimension_collision_with_benchmark_dimension_raises():
    s = suite("M", _b("a").with_command(["true"]).with_matrix(vm=["a"])).with_matrix(
        vm=["b"]
    )
    with pytest.raises(ValueError, match="already declared"):
        s.materialize(None)


def test_with_skip_kwargs_drops_variant():
    s = (
        suite(
            "M",
            _b("c")
            .with_command(lambda ctx: ["x", ctx.data.vm, str(ctx.data.size)])
            .with_matrix(vm=["v8", "jsc"], size=[100, 500])
            .add_matrix_skip(vm="v8", size=500),
        )
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    bs = list(s.materialize(None))
    assert len(bs) == 3
    assert ("v8", 500) not in {(b.vm, b.size) for b in bs}


def test_with_skip_predicate_drops_variant():
    s = (
        suite(
            "M",
            _b("c")
            .with_command(lambda ctx: ["x", ctx.data.vm, str(ctx.data.size)])
            .with_matrix(vm=["v8", "jsc"], size=[100, 500])
            .add_matrix_skip(lambda b: b.vm != "jsc"),
        )
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    bs = list(s.materialize(None))
    assert all(b.vm == "jsc" for b in bs)
    assert sorted(b.size for b in bs) == [100, 500]


def test_suite_skip_unions_with_benchmark_skip():
    s = suite(
        "M",
        _b("c")
        .with_command(lambda ctx: ["x", ctx.data.vm, str(ctx.data.size)])
        .with_matrix(vm=["v8", "jsc"], size=[100, 500])
        .add_matrix_skip(vm="v8", size=500),
    ).add_matrix_skip(vm="jsc", size=100)
    bs = _mat(s)
    assert {(b.vm, b.size) for b in bs} == {("v8", 100), ("jsc", 500)}


def test_with_label_overrides_default():
    s = (
        suite(
            "M",
            _b("c")
            .with_command(lambda ctx: ["true"])
            .with_matrix(arg=["one", "two"])
            .with_label(lambda b: f"<{b.arg}>"),
        )
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    bs = list(s.materialize(None))
    assert sorted(b.variant_label for b in bs) == ["<one>", "<two>"]


def test_command_via_matrix_builder():
    """Per-variant command is wired explicitly via a builder reading ctx.data."""
    s = (
        suite(
            "M",
            _b("c")
            .with_matrix(cmd=[["echo", "a"], ["echo", "b"]])
            .with_command(lambda ctx: list(ctx.data.cmd)),
        )
        .with_cwd(Path("/tmp"))
        .with_process_metric(Time())
    )
    bs = list(s.materialize(None))
    assert sorted(b.execution.command for b in bs) == [("echo", "a"), ("echo", "b")]


# ----- CLI state reaches builder contexts ---------------------------------


def test_context_carries_cli_state():
    seen: list[tuple[bool, int]] = []

    def cmd(ctx):
        seen.append((ctx.cli.verbose, ctx.cli.jobs))
        return ["true"]

    s = suite("S", _b("a")).with_command(cmd)
    s.materialize(None, cli=SharedBenchParams(verbose=True, jobs=4))
    s.materialize(None)  # defaults
    assert seen == [(True, 4), (False, 1)]
