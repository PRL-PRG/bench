"""Suite: propagation, matrix, from_files, filter, materialize."""

import tempfile
from pathlib import Path

import pytest

from benchr import (
    Benchmark, FixedRuns, P, bench, benchmark_info, suite,
)


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
        .with_command(["true"]).with_cwd(tmp_path).with_process(P.time())
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
        .with_command(["true"]).with_cwd(tmp_path).with_process(P.time())
    )
    names = sorted(b.name for b in s.materialize(ctx=None))
    assert names == ["a", "sub/nested"]


def test_from_files_callable_root(tmp_path: Path):
    (tmp_path / "p.lox").write_text("")
    s = (
        suite("X").from_files(lambda ctx: ctx, pattern=r"\.lox$")
        .with_command(["true"]).with_cwd(tmp_path).with_process(P.time())
    )
    names = sorted(b.name for b in s.materialize(ctx=tmp_path))
    assert names == ["p"]


def test_matrix_expands_and_stamps_info():
    s = (
        suite("M", _b("compute"))
        .with_cwd(Path("/tmp")).with_process(P.time())
        .matrix("opt", ["O0", "O2"], command=lambda b, ctx, v: ["x", "-" + v])
    )
    benchmarks = list(s.benchmarks)
    assert len(benchmarks) == 2
    assert all(b.opt in {"O0", "O2"} for b in benchmarks)
    # Compile and check info is stamped.
    g = benchmarks[0].compile(ctx=None, suite="M")
    sched = next(g)
    g.close()
    assert sched.info == (("opt", "O0"),)


def test_matrix_info_callback_overrides_default():
    s = (
        suite("M", _b("c"))
        .with_cwd(Path("/tmp")).with_process(P.time())
        .matrix(
            "axis", [("gcc", "O2")],
            command=lambda b, ctx, v: ["x"],
            info=lambda v: {"compiler": v[0], "opt": v[1]},
        )
    )
    g = s.benchmarks[0].compile(ctx=None, suite="M")
    sched = next(g)
    g.close()
    assert dict(sched.info) == {"compiler": "gcc", "opt": "O2"}
