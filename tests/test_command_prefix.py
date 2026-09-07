"""`with_command_prefix`, the `--numa` prefix, and `with_diagnostics`."""

from dataclasses import dataclass
from pathlib import Path

from bench import (
    Diagnostic,
    SharedBenchParams,
    bench,
    bench_app,
    suite,
)
from bench.runner.base import plan
from bench.denoise import prefix


def _suite(name="S", cmd=("true",)):
    return suite(name, bench("b").with_command(list(cmd))).with_cwd(Path("/tmp"))


# ----- prefix -----------------------------------------------------------------


def test_prefix_is_empty_without_a_node():
    assert prefix() == []


def test_prefix_binds_both_cpus_and_memory():
    assert prefix(numa=3) == ["numactl", "--cpunodebind=3", "--membind=3"]


def test_aslr_is_a_denoise_knob_and_not_a_prefix():
    """One control for one property: `minimize()` owns ASLR, the prefix does not."""
    from bench.denoise import ASLR, _knobs

    aslr = Path("/proc") / ASLR
    if aslr.exists():  # Linux only
        assert aslr in [p for p, _, _ in _knobs(Path("/"))]


def test_command_prefix_precedes_the_command():
    s = _suite(cmd=["echo", "hi"])
    app = bench_app().add(s).with_command_prefix(["nice", "-n5"])
    (b,) = plan([app.overlay(s)], SharedBenchParams())
    assert b.invocation.command == ("nice", "-n5", "echo", "hi")


def test_command_prefix_is_optional():
    s = _suite(cmd=["echo", "hi"])
    (b,) = plan([s], SharedBenchParams())
    assert b.invocation.command == ("echo", "hi")


def test_command_prefix_may_read_params():
    @dataclass(frozen=True)
    class P(SharedBenchParams):
        tool: str = "chrt"

    s = _suite()
    app = (
        bench_app()
        .with_params(P)
        .add(s)
        .with_command_prefix(lambda ctx: [ctx.params.tool])
    )
    (b,) = plan([app.overlay(s)], P(tool="taskset"))
    assert b.invocation.command == ("taskset", "true")


def test_bench_app_leaves_the_command_alone_without_numa():
    s = _suite()
    app = bench_app().add(s)
    (b,) = plan([app.overlay(s)], SharedBenchParams())
    assert b.invocation.command == ("true",)


def test_numa_flag_reaches_the_prefix():
    s = _suite()
    app = bench_app().add(s)
    (b,) = plan([app.overlay(s)], SharedBenchParams(numa=1))
    assert b.invocation.command == (*prefix(1), "true")


# ----- a run is never privileged ----------------------------------------------


def test_a_run_has_no_denoise_flag():
    """Quieting the machine outlives the run and needs root, so it is a separate
    step (`bench denoise minimize`); a run that did it would own every file it
    wrote."""
    assert not hasattr(SharedBenchParams(), "denoise")
    assert not hasattr(bench_app(), "denoise")
    assert not hasattr(bench_app(), "with_denoise")


# ----- app diagnostics --------------------------------------------------------


def test_diagnostics_reach_the_report():
    s = _suite()
    report = (
        bench_app()
        .add(s)
        .with_runs(1)
        .with_diagnostics(lambda p: [Diagnostic("info", f"jobs={p.jobs}")])
        .run(["--no-progress", "--jobs", "2"])
    )
    assert Diagnostic("info", "jobs=2") in report.diagnostics


def test_no_diagnostics_hook_leaves_the_report_alone():
    s = _suite()
    report = bench_app().add(s).with_runs(1).run(["--no-progress"])
    assert all(d.severity != "info" for d in report.diagnostics)
