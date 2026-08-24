"""SuiteBuilder: lazy defaults, resolution, matrix, from_files, filter, materialize."""

from pathlib import Path

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
from bench.builder.context import Params
from bench.core.invocation import Variant
from bench.core.metric import StdoutMetricSource


def _b(name: str):
    return bench(name)


def _mat(s):
    return s.materialize(None)


# ----- defaults resolve at materialize ------------------------------------


def test_runs_propagates():
    s = suite("S", _b("a"), _b("b")).with_command(["true"]).with_runs(7)
    assert all(b.runs.max_runs() == 7 for b in _mat(s))


def test_runs_preserves_benchmark_override():
    a = _b("a").with_runs(FixedRuns(5))
    s = suite("S", a, _b("b")).with_command(["true"]).with_runs(10)
    # The benchmark's explicit value wins; the suite default only fills "b".
    assert [b.runs.max_runs() for b in _mat(s)] == [5, 10]


# ----- suite defaults accept (ctx) -> value builders ----------------------


def test_with_runs_accepts_ctx_callable():
    class P(Params):
        n: int

    s = (
        suite("S", _b("a"))
        .with_command(["true"])
        .with_runs(lambda ctx: FixedRuns(ctx.params.n))
    )
    b = s.materialize(P(n=4))[0]
    assert b.runs.max_runs() == 4


def test_with_warmup_accepts_ctx_callable():
    class P(Params):
        w: int

    s = (
        suite("S", _b("a"))
        .with_command(["true"])
        .with_warmup(lambda ctx: FixedRuns(ctx.params.w))
    )
    b = s.materialize(P(w=2))[0]
    assert b.warmup.max_runs() == 2


def test_with_timeout_accepts_ctx_callable():
    class P(Params):
        t: int

    s = (
        suite("S", _b("a"))
        .with_command(["true"])
        .with_timeout(lambda ctx: float(ctx.params.t))
    )
    b = s.materialize(P(t=30))[0]
    assert b.invocation.timeout == 30.0


def test_with_metric_accepts_ctx_callable():
    # A metric factory resolves to one metric, not a tuple of them.
    m = FloatPerLine(StdoutMetricSource, "runtime", unit="s")
    s = suite("S", _b("a")).with_command(["true"]).with_metric(lambda ctx: m)
    b = s.materialize(Params())[0]
    assert list(b.metrics) == [m]


def test_suite_callable_runs_still_loses_to_benchmark_override():
    class P(Params):
        n: int

    a = _b("a").with_runs(FixedRuns(5))
    s = (
        suite("S", a, _b("b"))
        .with_command(["true"])
        .with_runs(lambda ctx: FixedRuns(ctx.params.n))
    )
    # A callable suite default still loses to the benchmark's explicit value.
    assert [b.runs.max_runs() for b in s.materialize(P(n=9))] == [5, 9]


def test_with_command_propagates_when_unset():
    s = suite("S", _b("a")).with_command(["x"])
    b = _mat(s)[0]
    assert b.invocation.command == ("x",)


def test_with_command_order_independent():
    before = suite("S").with_command(["x"]).add(_b("a"))
    after = suite("S").add(_b("a")).with_command(["x"])
    b1, b2 = _mat(before)[0], _mat(after)[0]
    assert b1.invocation.command == b2.invocation.command == ("x",)


def test_defaults_reach_factory_benchmarks():
    s = (
        suite("S")
        .with_command(["true"])
        .with_runs(4)
        .generator(lambda ctx: [bench("f")])
    )
    b = _mat(s)[0]
    assert b.runs.max_runs() == 4
    assert b.invocation.command == ("true",)


def test_materialize_missing_command_fails_fast():
    with pytest.raises(ValueError, match="missing a command"):
        suite("S", _b("a")).materialize(Params())


def test_with_env_merges():
    a = _b("a").with_command(["true"]).with_env({"X": "1", "Y": "from_b"})
    s = suite("S", a).with_env({"Y": "from_s", "Z": "1"})
    b = _mat(s)[0]
    env = b.invocation.env
    # env merges per key, and the benchmark's value wins on a collision.
    assert env["X"] == "1" and env["Y"] == "from_b" and env["Z"] == "1"


def test_env_merge_both_callable():
    a = (
        _b("a")
        .with_command(["true"])
        .with_env(lambda ctx: {"X": ctx.benchmark or "", "Y": "from_b"})
    )
    s = suite("S", a).with_env(lambda ctx: {"Y": "from_s", "Z": "1"})
    b = _mat(s)[0]
    # Two env factories merge the same way: benchmark wins on a collision.
    assert b.invocation.env == {"X": "a", "Y": "from_b", "Z": "1"}


def test_benchmark_env_without_suite_env_materializes():
    a = _b("a").with_command(["true"]).with_env({"X": "1"})
    assert _mat(suite("S", a))[0].invocation.env == {"X": "1"}


def test_suite_warmup_respects_explicit_zero():
    b = bench("x").with_warmup(0)
    s = suite("s", b).with_command(["true"]).with_warmup(3)
    # An explicit 0 is a value, not an absence: the suite default cannot fill it.
    assert _mat(s)[0].warmup.max_runs() == 0


def test_suite_measure_respects_explicit_one():
    b = bench("x").with_runs(1)
    s = suite("s", b).with_command(["true"]).with_runs(9)
    # Same for an explicit 1 run against a larger suite default.
    assert _mat(s)[0].runs.max_runs() == 1


def test_suite_with_success_propagates_and_respects_override():
    def suite_fn(r):
        return None

    def bench_fn(r):
        return "nope"

    s = (
        suite("s", bench("a"), bench("b").with_success(bench_fn))
        .with_command(["true"])
        .with_success(suite_fn)
    )
    resolved = _mat(s)
    # The suite predicate fills "a"; "b" keeps its own.
    assert resolved[0].success is suite_fn
    assert resolved[1].success is bench_fn


def test_suite_with_label_propagates_and_respects_override():
    def suite_label(b):
        return "suite"

    def bench_label(b):
        return "bench"

    s = (
        suite("s", bench("a"), bench("b").with_label(bench_label))
        .with_command(["true"])
        .with_label(suite_label)
    )
    resolved = _mat(s)
    # The suite labeller fills "a"; "b" keeps its own.
    assert resolved[0].variant_label == "suite"
    assert resolved[1].variant_label == "bench"


# ----- producers -----------------------------------------------------------


def test_filter():
    # Deferred: applies after expansion (per-variant) and is order-independent
    # (added before the benchmark it filters). A filter KEEPS what it matches.
    s = (
        suite("S")
        .with_filter(lambda b: b.data["size"] != 500)
        .add(_b("c").with_matrix(size=[100, 500]))
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
    )
    assert sorted(b.data["size"] for b in _mat(s)) == [100]


def test_filter_without_matrix_drops_variant():
    s = (
        suite("S")
        .add(_b("keep"))
        .add(_b("drop"))
        .with_filter(lambda b: b.name == "keep")
        .with_command(["true"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
    )
    assert [b.name for b in _mat(s)] == ["keep"]


def test_from_files(tmp_path: Path):
    (tmp_path / "a.lox").write_text("")
    (tmp_path / "b.lox").write_text("")
    (tmp_path / "skip.txt").write_text("")
    s = (
        suite("X", *from_files(tmp_path, pattern=r"\.lox$"))
        .with_command(["true"])
        .with_cwd(tmp_path)
        .with_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(Params()))
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
        .with_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(Params()))
    assert names == ["a", "sub/nested"]


def test_from_files_ctx_root_via_factory(tmp_path: Path):
    class P(Params):
        path: Path

    (tmp_path / "p.lox").write_text("")
    s = (
        suite("X")
        .generator(lambda ctx: from_files(ctx.params.path, pattern=r"\.lox$"))
        .with_command(["true"])
        .with_cwd(tmp_path)
        .with_metric(Time())
    )
    names = sorted(b.name for b in s.materialize(P(path=tmp_path)))
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
        .with_metric(Time())
    )
    benchmarks = list(s.materialize(Params()))
    assert len(benchmarks) == 2
    assert sorted(b.data["opt"] for b in benchmarks) == ["O0", "O2"]
    # The variant is stamped on the resolved benchmark.
    assert {b.variant for b in benchmarks} == {
        Variant.of({"opt": "O0"}),
        Variant.of({"opt": "O2"}),
    }


def test_suite_with_matrix_applies_to_all_benchmarks():
    s = (
        suite(
            "M",
            _b("a").with_command(lambda ctx: ["x", ctx.data.vm]),
            _b("b").with_command(lambda ctx: ["y", ctx.data.vm]),
        )
        .with_matrix(vm=["v8", "jsc"])
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
    )
    bs = list(s.materialize(Params()))
    names_vms = sorted((b.name, b.data["vm"]) for b in bs)
    assert names_vms == [("a", "jsc"), ("a", "v8"), ("b", "jsc"), ("b", "v8")]


def test_suite_dimensions_append_after_benchmark_dimensions():
    s = suite("M", _b("a").with_command(["true"]).with_matrix(size=[1, 2])).with_matrix(
        vm=["v8", "jsc"]
    )
    bs = _mat(s)
    assert len(bs) == 4
    # The benchmark's own dimensions are stamped first, the suite's appended.
    assert [k for k in bs[0].data if not k.startswith("_")] == ["size", "vm"]


def test_suite_dimension_collision_with_benchmark_dimension_raises():
    s = suite("M", _b("a").with_command(["true"]).with_matrix(vm=["a"])).with_matrix(
        vm=["b"]
    )
    with pytest.raises(ValueError, match="Duplicate matrix axis"):
        s.materialize(Params())


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
        .with_metric(Time())
    )
    bs = list(s.materialize(Params()))
    # The skip drops the one cell matching all its kwargs, nothing else.
    assert len(bs) == 3
    assert ("v8", 500) not in {(b.data["vm"], b.data["size"]) for b in bs}


def test_with_filter_predicate_keeps_matching_variants():
    # `add_matrix_skip` is kwargs-only now; a predicate goes through
    # `with_filter`, which KEEPS (rather than drops) what it matches.
    s = (
        suite(
            "M",
            _b("c")
            .with_command(lambda ctx: ["x", ctx.data.vm, str(ctx.data.size)])
            .with_matrix(vm=["v8", "jsc"], size=[100, 500])
            .with_filter(lambda b: b.data["vm"] == "jsc"),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
    )
    bs = list(s.materialize(Params()))
    assert all(b.data["vm"] == "jsc" for b in bs)
    assert sorted(b.data["size"] for b in bs) == [100, 500]


def test_suite_skip_unions_with_benchmark_skip():
    s = suite(
        "M",
        _b("c")
        .with_command(lambda ctx: ["x", ctx.data.vm, str(ctx.data.size)])
        .with_matrix(vm=["v8", "jsc"], size=[100, 500])
        .add_matrix_skip(vm="v8", size=500),
    ).add_matrix_skip(vm="jsc", size=100)
    bs = _mat(s)
    # A suite skip and a benchmark skip union: each drops its own cell.
    assert {(b.data["vm"], b.data["size"]) for b in bs} == {("v8", 100), ("jsc", 500)}


def test_with_label_overrides_default():
    s = (
        suite(
            "M",
            _b("c")
            .with_command(lambda ctx: ["true"])
            .with_matrix(arg=["one", "two"])
            .with_label(lambda b: f"<{b.data['arg']}>"),
        )
        .with_cwd(Path("/tmp"))
        .with_metric(Time())
    )
    bs = list(s.materialize(Params()))
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
        .with_metric(Time())
    )
    bs = list(s.materialize(Params()))
    assert sorted(tuple(b.invocation.command) for b in bs) == [
        ("echo", "a"),
        ("echo", "b"),
    ]


# ----- CLI state reaches builder contexts ---------------------------------


def test_context_carries_cli_state():
    seen: list[tuple[bool, int]] = []

    def cmd(ctx):
        seen.append((ctx.params.verbose, ctx.params.jobs))
        return ["true"]

    s = suite("S", _b("a")).with_command(cmd)
    s.materialize(SharedBenchParams(verbose=True, jobs=4))
    s.materialize(SharedBenchParams())  # defaults
    assert seen == [(True, 4), (False, 1)]


# ----- subsuites -----------------------------------------------------------


def test_subsuite_name_is_path_joined():
    s = suite("P", _b("a"), suite("C", _b("c"))).with_command(["true"])
    assert [(b.suite, b.name) for b in _mat(s)] == [("P", "a"), ("P/C", "c")]


def test_subsuites_nest_arbitrarily_deep():
    s = suite("A", suite("B", suite("C", _b("x")))).with_command(["true"])
    assert [(b.suite, b.name) for b in _mat(s)] == [("A/B/C", "x")]


def test_own_benchmarks_materialize_before_subsuite_benchmarks():
    # Argument order across the two kinds does not matter: a suite's own
    # benchmarks come first, its sub-suites' after.
    s = suite("P", suite("C", _b("c")), _b("a")).with_command(["true"])
    assert [b.name for b in _mat(s)] == ["a", "c"]


def test_subsuite_inherits_parent_defaults():
    s = suite("P", suite("C", _b("c"))).with_command(["true"]).with_runs(7)
    assert [b.runs.max_runs() for b in _mat(s)] == [7]


def test_subsuite_setting_beats_parent_default():
    s = suite("P", suite("C", _b("c")).with_runs(3)).with_command(["true"]).with_runs(7)
    assert [b.runs.max_runs() for b in _mat(s)] == [3]


def test_benchmark_beats_subsuite_beats_parent():
    s = (
        suite("P", suite("C", _b("c").with_runs(1), _b("d")).with_runs(3))
        .with_command(["true"])
        .with_runs(7)
    )
    # Innermost level that set the field wins, all the way down the chain.
    assert [b.runs.max_runs() for b in _mat(s)] == [1, 3]


def test_subsuite_env_merges_through_three_levels():
    s = (
        suite(
            "P",
            suite("C", _b("c").with_env({"X": "bench"})).with_env(
                {"X": "sub", "Y": "sub"}
            ),
        )
        .with_command(["true"])
        .with_env({"X": "parent", "Y": "parent", "Z": "parent"})
    )
    # Per key: the innermost level that sets it wins, the others merge in.
    assert _mat(s)[0].invocation.env == {"X": "bench", "Y": "sub", "Z": "parent"}


def test_parent_filter_applies_to_subsuite_benchmarks():
    s = (
        suite("P", suite("C", _b("keep"), _b("drop")))
        .with_filter(lambda b: b.name == "keep")
        .with_command(["true"])
    )
    assert [b.name for b in _mat(s)] == ["keep"]


def test_parent_matrix_accumulates_into_subsuite_benchmarks():
    s = (
        suite("P", suite("C", _b("c").with_matrix(size=[1, 2])))
        .with_matrix(vm=["v8"])
        .with_command(["true"])
    )
    bs = _mat(s)
    assert [(b.data["vm"], b.data["size"]) for b in bs] == [("v8", 1), ("v8", 2)]
    # The innermost dimensions are still stamped first.
    assert [k for k in bs[0].data if not k.startswith("_")] == ["size", "vm"]


def test_add_suites_accumulates():
    s = (
        suite("P")
        .add_suites(suite("C1", _b("x")))
        .add_suites(suite("C2", _b("y")))
        .with_command(["true"])
    )
    assert [(b.suite, b.name) for b in _mat(s)] == [("P/C1", "x"), ("P/C2", "y")]


def test_add_suite_generator_defers_and_sees_the_resolved_parent_name():
    seen: list[str] = []

    def gen(ctx):
        seen.append(ctx.suite)
        return [suite("G", _b("g"))]

    s = suite("A", suite("P").add_suite_generator(gen)).with_command(["true"])
    assert not seen  # nothing runs until materialize
    assert [(b.suite, b.name) for b in _mat(s)] == [("A/P/G", "g")]
    # The generator sees the path its suite resolved to, not the bare name.
    assert seen == ["A/P"]


def test_subsuite_generator_reads_params():
    class P(Params):
        n: int

    s = (
        suite("P")
        .add_suite_generator(lambda ctx: [suite(f"C{ctx.params.n}", _b("g"))])
        .with_command(["true"])
    )
    assert [b.suite for b in s.materialize(P(n=2))] == ["P/C2"]


def test_generated_subsuites_inherit_parent_defaults():
    s = (
        suite("P")
        .add_suite_generator(lambda ctx: [suite("C", _b("g"))])
        .with_command(["true"])
        .with_runs(5)
    )
    assert [b.runs.max_runs() for b in _mat(s)] == [5]


def test_shuffle_covers_subsuite_benchmarks():
    own = [f"b{i}" for i in range(4)]
    subs = [f"c{i}" for i in range(4)]

    def make():
        return (
            suite("P", *(_b(n) for n in own), suite("C", *(_b(n) for n in subs)))
            .with_command(["true"])
            .with_shuffle(seed=1)
        )

    first = [b.name for b in _mat(make())]
    assert first == [b.name for b in _mat(make())]  # same seed -> same order
    assert sorted(first) == sorted(own + subs)  # same set
    # The shuffle is over the flattened list, so the two groups interleave
    # instead of each sub-suite being shuffled within its own block.
    assert min(i for i, n in enumerate(first) if n in subs) < max(
        i for i, n in enumerate(first) if n in own
    )


def test_unnamed_parent_suite_does_not_prefix_its_subsuites():
    s = suite("", suite("C", _b("x"))).with_command(["true"])
    assert [b.suite for b in _mat(s)] == ["C"]


def test_unnamed_subsuite_does_not_add_a_path_component():
    # RED ON PURPOSE: BUG-1 - an empty *parent* name collapses (the test above)
    # but an empty *sub-suite* name does not, so an unnamed grouping suite leaves
    # a dangling separator in the suite path and in every selection key.
    s = suite("P", suite("", _b("x"))).with_command(["true"])
    assert [b.suite for b in _mat(s)] == ["P"]
