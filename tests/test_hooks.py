"""Hooks: user code run around each measured execution.

`with_setup` / `with_teardown` / `with_hook` attach a `Hook` at any builder
level; the Controller calls `setup` before it spawns the process and `teardown`
after the process has been evaluated, once per execution.
"""

from pathlib import Path

from bench import (
    Benchmark,
    Controller,
    FixedRuns,
    Hook,
    Reporter,
    SetupHook,
    TearDownHook,
    bench,
    suite,
)
from bench.builder.suite import plan
from bench.params import Params


class _Record(Hook):
    """A hook that logs its own calls into a shared list."""

    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    def setup(self, benchmark: Benchmark) -> None:
        self.events.append(f"setup:{self.name}")

    def teardown(self, benchmark: Benchmark) -> None:
        self.events.append(f"teardown:{self.name}")


def _planned(b, s=None) -> Benchmark:
    s = (s or suite("S")).add(b).with_cwd(Path("/tmp")).with_runs(FixedRuns(1))
    return plan([s], Params())[0]


def test_setup_and_teardown_bracket_each_execution():
    events: list[str] = []
    b = (
        bench("b")
        .with_command(["true"])
        .with_setup(lambda _: events.append("setup"))
        .with_teardown(lambda _: events.append("teardown"))
    )

    Controller().run_benchmark(_planned(b), Reporter(), False)

    assert events == ["setup", "teardown"]


def test_hooks_run_once_per_run():
    events: list[str] = []
    b = (
        bench("b")
        .with_command(["true"])
        .with_setup(lambda _: events.append("setup"))
        .with_teardown(lambda _: events.append("teardown"))
        .with_runs(FixedRuns(3))
    )

    Controller().run_benchmark(_planned(b), Reporter(), False)

    assert events == ["setup", "teardown"] * 3


def test_setup_runs_before_the_process_and_teardown_after(tmp_path: Path):
    # The command only succeeds while the marker exists, so a passing execution
    # proves setup landed first; teardown removing it proves it ran after.
    marker = tmp_path / "marker"

    def create(_: Benchmark) -> None:
        marker.write_text("x")

    b = (
        bench("b")
        .with_command(["sh", "-c", f"test -f {marker}"])
        .with_setup(create)
        .with_teardown(lambda _: marker.unlink())
        .with_runs(FixedRuns(2))
    )

    executions = Controller().run_benchmark(_planned(b), Reporter(), False)

    assert [e.returncode for e in executions] == [0, 0]
    assert not marker.exists()


def test_hooks_are_set_up_in_declaration_order():
    events: list[str] = []
    b = (
        bench("b")
        .with_command(["true"])
        .with_hook(_Record(events, "first"))
        .with_hook(_Record(events, "second"))
    )

    Controller().run_benchmark(_planned(b), Reporter(), False)

    assert events[:2] == ["setup:first", "setup:second"]


def test_hooks_are_torn_down_in_reverse_order():
    # A hook that acquires something in `setup` and releases it in `teardown`
    # only composes if the pairs nest.
    events: list[str] = []
    b = (
        bench("b")
        .with_command(["true"])
        .with_hook(_Record(events, "first"))
        .with_hook(_Record(events, "second"))
    )

    Controller().run_benchmark(_planned(b), Reporter(), False)

    assert events[2:] == ["teardown:second", "teardown:first"]


def test_suite_hooks_wrap_the_benchmarks_own():
    # The outer level brackets the inner one: inherited hooks set up first.
    events: list[str] = []
    s = suite("S").with_hook(_Record(events, "suite"))
    b = bench("b").with_command(["true"]).with_hook(_Record(events, "bench"))

    Controller().run_benchmark(_planned(b, s), Reporter(), False)

    assert events[:2] == ["setup:suite", "setup:bench"]


def test_suite_hooks_reach_the_benchmark_at_all():
    events: list[str] = []
    s = suite("S").with_hook(_Record(events, "suite"))
    b = bench("b").with_command(["true"])

    Controller().run_benchmark(_planned(b, s), Reporter(), False)

    assert events == ["setup:suite", "teardown:suite"]


def test_with_hook_accepts_a_factory():
    # Like every other builder field, a hook may be built per variant from the
    # resolved context rather than shared across them.
    events: list[str] = []
    b = (
        bench("b")
        .with_command(["true"])
        .with_hook(lambda ctx: _Record(events, ctx.benchmark))
    )

    Controller().run_benchmark(_planned(b), Reporter(), False)

    assert events == ["setup:b", "teardown:b"]


def test_the_hook_receives_the_benchmark_it_brackets():
    seen: list[str] = []
    b = bench("b").with_command(["true"]).with_setup(lambda bm: seen.append(bm.name))

    Controller().run_benchmark(_planned(b), Reporter(), False)

    assert seen == ["b"]


def test_a_function_hook_only_implements_its_own_half():
    # SetupHook/TearDownHook are the one-sided shorthands `with_setup` and
    # `with_teardown` wrap; the other half inherits Hook's no-op.
    calls: list[str] = []
    benchmark = _planned(bench("b").with_command(["true"]))

    setup_only = SetupHook(lambda _: calls.append("setup"))
    setup_only.teardown(benchmark)
    teardown_only = TearDownHook(lambda _: calls.append("teardown"))
    teardown_only.setup(benchmark)

    assert calls == []

    setup_only.setup(benchmark)
    teardown_only.teardown(benchmark)
    assert calls == ["setup", "teardown"]


def test_a_benchmark_without_hooks_has_none():
    assert list(_planned(bench("b").with_command(["true"])).hooks) == []
