"""The `Bench` abstraction and the `run(...)` benchmarking pipeline.

Named `run` so that `from bench.run import run` re-binds the public `run`
symbol on the package, keeping `from bench import run` pointing at the
function rather than at this submodule.
"""

from __future__ import annotations

import argparse
import dataclasses
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.text import Text
from rich.tree import Tree

from bench.grammar.benchmark import Benchmark
from bench.grammar.context import add_dataclass_args, build_dataclass
from bench.core.checks import run_checks
from bench.core.environment import (
    EnvironmentCollector,
    NoEnvironment,
)
from bench.core.execution import record_key
from bench.denoise import (
    STATE_PATH,
    denoise_session,
    is_root,
)
from bench.grammar.suite import SuiteBuilder
from bench.report.formatter import DefaultSummary
from bench.report.reporter import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    JsonReporter,
    ProgressReporter,
    Reporter,
    SummaryReporter,
    console,
    print_diagnostics,
)
from bench.utils import print_exception
from bench.core.sample import Report
from bench.runner.base import (
    Runner,
    SuiteMaterializationError,
    plan,
    select,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential


type SuiteFactory = Callable[[Any], list[SuiteBuilder]]


@dataclasses.dataclass(frozen=True, slots=True)
class Bench:
    """Top-level run container: static suites + deferred suite factories."""

    suites: tuple[SuiteBuilder, ...] = ()
    factories: tuple[SuiteFactory, ...] = ()
    params: type | None = None
    reporter: Reporter | None = None
    environment: EnvironmentCollector = NoEnvironment()
    denoise: bool = False

    def add_suite(self, s: SuiteBuilder) -> Bench:
        """Register a suite."""
        return dataclasses.replace(self, suites=self.suites + (s,))

    def add_suites(self, *ss: SuiteBuilder) -> Bench:
        """Register several suites."""
        return dataclasses.replace(self, suites=self.suites + ss)

    def factory(self, fn: SuiteFactory) -> Bench:
        """Register a deferred `(params) -> [SuiteBuilder]` producer.

        Resolved at `run` once params are parsed. Its suites are appended after
        any manually added ones.
        """
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def run(self, argv: list[str] | None = None) -> Report:
        """Parse argv, resolve factories, run every collected suite."""
        parser = _make_run_parser(self.params)
        cli_args = parser.parse_args(argv)
        build_params = (
            build_dataclass(self.params, cli_args) if self.params is not None else None
        )

        collected = list(self.suites)
        for f in self.factories:
            produced = f(build_params)
            collected.extend(produced)

        return do_run(
            collected,
            cli_args,
            self.reporter,
            build_params,
            environment=self.environment,
            denoise=self.denoise,
        )


def run(
    suites: SuiteBuilder | list[SuiteBuilder] | SuiteFactory,
    *,
    params: type | None = None,
    reporter: Reporter | None = None,
    argv: list[str] | None = None,
    environment: EnvironmentCollector | None = None,
    denoise: bool = False,
) -> Report:
    """
    The entrypoint for benchmarking.
    Parse argv, build the ctx, run the benchmark, emit reports.

    Args:
        suites: The suite(s) to run. Either a `SuiteBuilder`, a list of `SuiteBuilder`, or a
            deferred `(params) -> [SuiteBuilder]` producer that is called after
            CLI parsing (for suite discovery that depends on `params`). To
            combine static and discovered suites, build a `Bench` directly.
        params: The user's @dataclass that declares additional CLI flags and forms the user-defined context. Defaults to `None` if omitted.
        reporter: The reporter to be used for process the result. Defaults to `SummaryReporter`
        argv: The command-line parameters that will be parsed and use to fill the user-defined context.
        environment: Strategy collecting the machine snapshot recorded with the
            report and driving the checks. Defaults to `NoEnvironment()` (off);
            pass `SystemEnvironment()` to record the snapshot and run the checks.
        denoise: When True, minimize system noise for the run and restore it afterward.
            Requires root on Linux.

    Returns:
        The report of running all the benchmarks
    """
    app = Bench(
        params=params,
        reporter=reporter,
        environment=environment or NoEnvironment(),
        denoise=denoise,
    )
    if callable(suites):  # a SuiteBuilder / list is never callable
        app = app.factory(suites)
    elif isinstance(suites, SuiteBuilder):
        app = app.add_suite(suites)
    else:
        app = app.add_suites(*suites)

    return app.run(argv)


def do_run(
    suites: list[SuiteBuilder],
    cli_args: argparse.Namespace,
    reporter: Reporter | None,
    build_params: Any,
    *,
    environment: EnvironmentCollector = NoEnvironment(),
    denoise: bool = False,
) -> Report:
    """Run already-parsed benchmarks: build the reporter, plan the suites,
    apply CLI overrides and hand off to the runner."""
    if reporter is None:
        reporter = SummaryReporter(DefaultSummary())

    if denoise and not is_root():
        console.print(
            "[bench.failure]--denoise requires root "
            "(try: sudo bench run --denoise ...)[/]"
        )
        sys.exit(2)

    env = environment.collect()
    env_diagnostics = run_checks(env) if env is not None else []

    reporter = _build_reporter(
        cli_args,
        reporter,
        with_progress=not cli_args.no_progress,
    )
    reporter.set_environment(env, env_diagnostics)
    if getattr(cli_args, "compare", None):
        reporter.set_baseline([Path(p) for p in cli_args.compare])

    try:
        planned = plan(suites, build_params)
    except SuiteMaterializationError as e:
        print_exception(e)
        sys.exit(1)

    includes = getattr(cli_args, "include", None)
    excludes = getattr(cli_args, "exclude", None)
    try:
        planned = select(planned, includes, excludes)
    except re.error as e:
        console.print(f"[bench.failure]Invalid --include/--exclude regex: {e}[/]")
        sys.exit(2)

    if getattr(cli_args, "list_plan", False):
        console.print(_list_planned_benchmarks(planned))
        return Report()

    if (includes or excludes) and not planned:
        console.print(
            "[bench.failure]No benchmarks matched --include/--exclude "
            "(run with --list to see what's available).[/]"
        )
        sys.exit(1)

    print_diagnostics(env_diagnostics, "Environment checks")

    runner = _make_runner(cli_args, reporter)

    try:
        if denoise:
            with denoise_session() as applied:
                console.print(
                    f"[bench.label]Denoise:[/] minimized {len(applied)} knob(s); "
                    f"state saved to {STATE_PATH}"
                )
                report = runner.run(planned, build_params)
        else:
            report = runner.run(planned, build_params)
    except KeyboardInterrupt:
        console.print("[bench.failure]Interrupted[/]")
        sys.exit(1)

    report.environment = env
    report.diagnostics = env_diagnostics
    return report


def _build_reporter(
    ns: argparse.Namespace,
    summary_reporter: Reporter,
    *,
    with_progress: bool,
) -> Reporter:
    sinks: list[Reporter] = []
    if with_progress:
        sinks.append(ProgressReporter())

    sinks.append(summary_reporter)

    if ns.json:
        sinks.append(JsonReporter(Path(ns.json)))

    if ns.csv:
        sinks.append(CsvReporter(Path(ns.csv)))

    if ns.dir:
        sinks.append(DirReporter(Path(ns.dir)))

    return sinks[0] if len(sinks) == 1 else CompositeReporter(*sinks)


def _make_runner(ns: argparse.Namespace, reporter: Reporter) -> Runner:
    if ns.dry:
        return Dry(verbose=ns.verbose)

    if ns.jobs > 1:
        return Parallel(workers=ns.jobs, reporter=reporter, verbose=ns.verbose)

    return Sequential(reporter=reporter, verbose=ns.verbose)


# ---------------------------------------------------------------------------
# argparse builders
# ---------------------------------------------------------------------------


def _make_run_parser(params: type | None) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench")
    _add_user_params(p, params)
    add_runtime_flags(p.add_argument_group("bench flags"))
    _add_selection_flags(p.add_argument_group("selection"))
    return p


def _add_user_params(parser: argparse.ArgumentParser, params: type | None) -> None:
    if params is None:
        return
    group_ = parser.add_argument_group("context parameters")
    add_dataclass_args(group_, params)


def add_runtime_flags(
    # argparse exposes no public name for the add_argument_group() return type.
    g: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
) -> None:
    g.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=1,
        metavar="N",
        help="Run up to N benchmarks in parallel (default: 1, sequential).",
    )
    g.add_argument(
        "--no-progress",
        action="store_true",
        help="Suppress the progress bar.",
    )
    g.add_argument(
        "--dry",
        action="store_true",
        help="Show what shall happen but without running anything.",
    )
    g.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Verbose output.",
    )
    g.add_argument(
        "--json",
        type=str,
        default=None,
        metavar="FILE",
        help="Write a JSON report of every sample to FILE.",
    )
    g.add_argument(
        "--csv",
        type=str,
        default=None,
        metavar="FILE",
        help="Write a CSV report of every sample to FILE.",
    )
    g.add_argument(
        "--dir",
        type=str,
        default=None,
        metavar="DIR",
        help="Write a per-execution tree (stdout/stderr/exitcode/seq) under DIR.",
    )
    g.add_argument(
        "--compare",
        action="append",
        default=None,
        metavar="JSON",
        help="Compare against a baseline JSON report (repeat to add more)."
        "First is the baseline.",
    )


def _add_selection_flags(
    g: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
) -> None:
    g.add_argument(
        "--list",
        action="store_true",
        dest="list_plan",
        help="List the suite/benchmark/variant tree and exit (run nothing).",
    )
    g.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="REGEX",
        help="Keep only benchmarks whose key matches REGEX, where the key is "
        "'suite/benchmark (k=v, ...)'. Repeatable (OR semantics).",
    )
    g.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="REGEX",
        help="Drop benchmarks whose key matches REGEX. Repeatable; wins over --include.",
    )


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _list_planned_benchmarks(planned: list[Benchmark]) -> Tree:
    """Group planned benchmarks into a `suite -> benchmark -> variant` tree.

    A benchmark with several variants becomes a node whose leaves are the
    per-variant labels; a benchmark with a single variant stays a leaf labeled
    `name (k=v, ...)`. The root carries a one-line count summary. This is what
    `--list` prints.
    """
    n_suites = len({b.suite for b in planned})
    n_benchmarks = len({(b.suite, b.name) for b in planned})
    n_variants = len(planned)  # each runnable instance is a variant

    def plural(n: int, word: str) -> str:
        return f"{n} {word}{'' if n == 1 else 's'}"

    header = ", ".join(
        (
            plural(n_suites, "suite"),
            plural(n_benchmarks, "benchmark"),
            plural(n_variants, "variant"),
        )
    )
    root = Tree(Text(header, style="bench.label"))

    by_suite: dict[str, list[Benchmark]] = {}
    for b in planned:
        by_suite.setdefault(b.suite, []).append(b)

    for s, bs in by_suite.items():
        node = root.add(Text(s, style="bench.label"))
        by_name: dict[str, list[Benchmark]] = {}
        for b in bs:
            by_name.setdefault(b.name, []).append(b)
        for name, variants in by_name.items():
            if len(variants) > 1:
                bench_node = node.add(Text(name, style="bench.label"))
                for b in variants:
                    bench_node.add(Text(b.variant_label))
            else:
                b = variants[0]
                node.add(Text(record_key(b.name, b.name, b.variant)))
    return root
