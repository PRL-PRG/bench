"""Benchmark + compile() coroutine."""

from pathlib import Path

import pytest

from benchr import (
    CoefficientOfVariation, FixedRuns, P, Sample, bench,
)


def _pump(b, fake_value=1.0):
    """Helper: drive a compile() coroutine to exhaustion, feeding back one
    Sample per yield. Returns the list of ScheduledExecutions seen."""
    out = []
    gen = b.compile(ctx=None, suite="S")
    try:
        sched = next(gen)
        while True:
            out.append(sched)
            s = Sample(
                suite="S", benchmark=b.name, variant=sched.variant, run=sched.run,
                phase=sched.phase, metric="runtime", value=fake_value,
                unit="s", lower_is_better=True,
            )
            sched = gen.send([s])
    except StopIteration:
        pass
    return out


def _base():
    return (
        bench("b")
        .with_command(["sh", "-c", "echo 1"])
        .with_cwd(Path("/tmp"))
        .with_process(P.float_per_line("s"))
    )


def test_runs_sugar_equivalent_to_fixed_runs():
    a = _base().runs(3)
    b = _base().with_measure(FixedRuns(3))
    assert a.measure == b.measure


def test_compile_yields_three_measures():
    items = _pump(_base().runs(3))
    assert [s.phase for s in items] == ["measure", "measure", "measure"]
    assert [s.run for s in items] == [1, 2, 3]


def test_warmup_then_measure():
    items = _pump(_base().with_warmup(2).runs(3))
    assert [s.phase for s in items] == ["warmup", "warmup", "measure", "measure", "measure"]


def test_fixed_runs_zero_skips_phase():
    items = _pump(_base().with_warmup(0).runs(2))
    assert [s.phase for s in items] == ["measure", "measure"]


def test_missing_command_raises():
    with pytest.raises(ValueError, match="no command"):
        list(bench("x").compile(ctx=None, suite="S"))


def test_missing_cwd_raises():
    with pytest.raises(ValueError, match="no cwd"):
        list(bench("x").with_command(["true"]).with_process(P.time()).compile(ctx=None, suite="S"))


def test_missing_processor_raises():
    with pytest.raises(ValueError, match="no processor"):
        list(bench("x").with_command(["true"]).with_cwd(Path("/tmp")).compile(ctx=None, suite="S"))


def test_bench_kwargs_attach_to_data():
    b = bench("z", path=Path("zoo.lox"), size=42)
    assert b.path == Path("zoo.lox")
    assert b.size == 42


def test_cov_warmup_then_fixed_measure():
    cov = CoefficientOfVariation("runtime", threshold=0.0, window=3, min_runs=3)
    items = _pump(_base().with_warmup(cov).runs(2))
    # 3 warmup runs at value=1.0 saturate CoV (stdev=0), then 2 measure runs.
    assert [s.phase for s in items] == ["warmup"]*3 + ["measure"]*2


def test_immutability_via_with_methods():
    a = _base().runs(3)
    b = a.runs(5)
    assert a.measure != b.measure
