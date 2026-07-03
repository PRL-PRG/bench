"""The `BenchAppBuilder` abstraction and the `run(...)` benchmarking pipeline.

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
from bench.grammar.builder import Factory, BuilderBase, as_build, const
from bench.grammar.context import (
    Context,
    Data,
    SharedBenchParams,
    SharedSelectionParams,
    add_dataclass_args,
    build_dataclass,
)
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
from bench.core.sample import Report, report_from_json
from bench.runner.base import (
    Runner,
    SuiteMaterializationError,
    plan,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential


type SuiteFactory = Callable[[Any], list[SuiteBuilder]]
type ReporterFactory = Factory[Reporter]
type RunnerFactory = Factory[Runner]
type Filter = Callable[[Benchmark], bool]
type FilterFactory = Factory[Filter]


@dataclasses.dataclass(frozen=True, slots=True)
class BenchAppBuilder(BuilderBase):
    """Top-level builder: static suites + deferred suite factories, plus common
    settings applied to every suite.

    The third builder level after `bench()`/`suite()`, sharing the same
    `BuilderBase`. The inheritable `.with_*` settings declared here are the
    weakest layer: they fill fields a suite or benchmark left unset, and a more
    specific level overrides them (`overlay`). `name` is shown as the description
    in `--help`.
    """

    name: str = ""
    suites: tuple[SuiteBuilder, ...] = ()
    factories: tuple[SuiteFactory, ...] = ()
    params: type | None = None
    reporter: ReporterFactory | None = None
    runner: RunnerFactory | None = None
    filter: FilterFactory | None = None
    environment: EnvironmentCollector = NoEnvironment()
    denoise: bool = False

    def add(self, s: SuiteBuilder) -> BenchAppBuilder:
        """Register a suite."""
        return dataclasses.replace(self, suites=self.suites + (s,))

    def add_all(self, *ss: SuiteBuilder) -> BenchAppBuilder:
        """Register several suites."""
        return dataclasses.replace(self, suites=self.suites + ss)

    def factory(self, fn: SuiteFactory) -> BenchAppBuilder:
        """Register a deferred `(params) -> [SuiteBuilder]` producer.

        Resolved at `run` once params are parsed. Its suites are appended after
        any manually added ones.
        """
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def with_reporter(self, reporter: Reporter | ReporterFactory) -> BenchAppBuilder:
        """Set the reporter. A bare `Reporter` is wrapped as `lambda _: reporter`
        (used as-is); pass `(ctx) -> Reporter` to build it from the context."""
        return dataclasses.replace(self, reporter=as_build(reporter))

    def with_runner(self, runner: Runner | RunnerFactory) -> BenchAppBuilder:
        """Set the runner. A bare `Runner` is wrapped as `lambda _: runner`;
        pass `(ctx) -> Runner` to build it from the context."""
        return dataclasses.replace(self, runner=as_build(runner))

    def with_filter(self, keep: Filter) -> BenchAppBuilder:
        """Set the selection filter predicate `(Benchmark) -> bool` (wrapped as a
        constant factory). Use `with_filter_fn` for a `(ctx) -> predicate`."""
        return dataclasses.replace(self, filter=const(keep))

    def with_filter_fn(self, fn: FilterFactory) -> BenchAppBuilder:
        """Set the selection filter factory `(ctx) -> (Benchmark -> bool)`."""
        return dataclasses.replace(self, filter=fn)

    def run(
        self,
        argv: list[str] | None = None,
        *,
        cli_args: argparse.Namespace | None = None,
    ) -> Report:
        """Resolve factories, apply app defaults, and run every suite.

        Parses `argv` — or uses an already-parsed `cli_args` (e.g. from the CLI) —
        then resolves the reporter/runner/filter stages against the context, plans
        and filters the suites, and hands off to the runner."""
        if cli_args is None:
            parser = _make_run_parser(self.params, description=self.name)
            cli_args = parser.parse_args(argv)
        build_params = (
            build_dataclass(self.params, cli_args) if self.params is not None else None
        )

        collected = list(self.suites)
        for f in self.factories:
            collected.extend(f(build_params))
        suites = [self.overlay(s) for s in collected]

        cli = build_dataclass(SharedBenchParams, cli_args)
        ctx: Context[Any] = Context(
            params=build_params,
            suite="",
            benchmark=None,
            data=Data(),
            cli=cli,
        )

        # `--show FILE`: replay a saved report through the configured summary and
        # exit — no progress bar, no re-export, nothing run.
        show = getattr(cli_args, "show", None)
        if show:
            report = report_from_json(Path(show).read_text())
            if self.reporter is None:
                show_reporter: Reporter = SummaryReporter(DefaultSummary())
            else:
                show_reporter = self.reporter(ctx)
            show_reporter.set_environment(report.environment, report.diagnostics)
            for r in report.runs:
                show_reporter.run_done(r)
            show_reporter.finalize()
            return report

        # TODO: check sudo access
        if self.denoise and not is_root():
            console.print(
                "[bench.failure]--denoise requires root "
                "(try: sudo bench run --denoise ...)[/]"
            )
            sys.exit(2)

        env = self.environment.collect()
        env_diagnostics = run_checks(env) if env is not None else []

        active_reporter = (
            default_reporter(ctx) if self.reporter is None else self.reporter(ctx)
        )
        active_reporter.set_environment(env, env_diagnostics)

        try:
            planned = plan(suites, build_params, cli=cli)
        except SuiteMaterializationError as e:
            print_exception(e)
            sys.exit(1)

        try:
            keep = (self.filter or default_filter)(ctx)
        except re.error as e:
            console.print(f"[bench.failure]Invalid --include/--exclude regex: {e}[/]")
            sys.exit(2)
        planned = [b for b in planned if keep(b)]

        if getattr(cli_args, "list_plan", False):
            console.print(_list_planned_benchmarks(planned))
            return Report()

        if (cli.include or cli.exclude) and not planned:
            console.print(
                "[bench.failure]No benchmarks matched --include/--exclude "
                "(run with --list to see what's available).[/]"
            )
            sys.exit(1)

        print_diagnostics(env_diagnostics, "Environment checks")

        active_runner = (self.runner or default_runner)(ctx)
        active_runner.reporter = active_reporter

        try:
            if self.denoise:
                with denoise_session() as applied:
                    console.print(
                        f"[bench.label]Denoise:[/] minimized {len(applied)} knob(s); "
                        f"state saved to {STATE_PATH}"
                    )
                    report = active_runner.run(planned, build_params)
            else:
                report = active_runner.run(planned, build_params)
        except KeyboardInterrupt:
            console.print("[bench.failure]Interrupted[/]")
            sys.exit(1)

        report.environment = env
        report.diagnostics = env_diagnostics
        return report


def run(
    suites: SuiteBuilder | list[SuiteBuilder] | SuiteFactory,
    *,
    params: type | None = None,
    reporter: Reporter | ReporterFactory | None = None,
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
            combine static and discovered suites, build a `BenchAppBuilder` directly.
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
    app = bench_app(
        params=params,
        reporter=reporter,
        environment=environment,
        denoise=denoise,
    )

    if callable(suites):
        app = app.factory(suites)
    elif isinstance(suites, SuiteBuilder):
        app = app.add(suites)
    else:
        app = app.add_all(*suites)

    return app.run(argv)


def bench_app(
    name: str = "",
    *,
    params: type | None = None,
    reporter: Reporter | ReporterFactory | None = None,
    environment: EnvironmentCollector | None = None,
    denoise: bool = False,
) -> BenchAppBuilder:
    """Top-level builder combining suites with common settings."""

    return BenchAppBuilder(
        name=name,
        params=params,
        reporter=as_build(reporter) if reporter is not None else None,
        environment=environment or NoEnvironment(),
        denoise=denoise,
    )


def default_reporter(ctx: Context[Any], summary: Reporter | None = None) -> Reporter:
    sinks: list[Reporter] = []
    cli = ctx.cli
    if cli.progress:
        sinks.append(ProgressReporter())

    sinks.append(summary or SummaryReporter(DefaultSummary()))

    if cli.json:
        sinks.append(JsonReporter(Path(cli.json)))
    if cli.csv:
        sinks.append(CsvReporter(Path(cli.csv)))
    if cli.dir:
        sinks.append(DirReporter(Path(cli.dir)))

    return sinks[0] if len(sinks) == 1 else CompositeReporter(*sinks)


def default_runner(ctx: Context[Any]) -> Runner:
    cli = ctx.cli
    if cli.dry:
        return Dry(verbose=cli.verbose)
    if cli.jobs > 1:
        return Parallel(workers=cli.jobs, verbose=cli.verbose)
    return Sequential(verbose=cli.verbose)


def default_filter(ctx: Context[Any]) -> Callable[[Benchmark], bool]:
    inc = [re.compile(p) for p in (ctx.cli.include or [])]
    exc = [re.compile(p) for p in (ctx.cli.exclude or [])]

    def keep(b: Benchmark) -> bool:
        key = record_key(b.suite, b.name, b.variant)
        if inc and not any(r.search(key) for r in inc):
            return False
        return not any(r.search(key) for r in exc)

    return keep


# ---------------------------------------------------------------------------
# argparse builders
# ---------------------------------------------------------------------------


def _make_run_parser(
    params: type | None, description: str = ""
) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bench", description=description or None)
    _add_user_params(p, params)
    add_dataclass_args(
        p.add_argument_group("bench flags"),
        SharedBenchParams,
        skip={"include", "exclude"},
    )
    sel = p.add_argument_group("selection")
    add_dataclass_args(sel, SharedSelectionParams)
    sel.add_argument(
        "--list",
        action="store_true",
        dest="list_plan",
        help="List the suite/benchmark/variant tree and exit (run nothing).",
    )
    p.add_argument(
        "--show",
        type=str,
        default=None,
        metavar="JSON",
        help="Render a previously saved JSON report with the default summary, "
        "then exit (run nothing).",
    )
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
    """Add the shared bench runtime flags (jobs/progress/dry/verbose/json/csv/dir)
    to an argument group. Used by the `bench run` console subcommand, which has no
    selection flags of its own."""
    add_dataclass_args(g, SharedBenchParams, skip={"include", "exclude"})


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
