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

from bench.builder.benchmark import Benchmark
from bench.builder.base import Factory, BuilderBase, as_build, const
from bench.builder.context import (
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
from bench.core.invocation import format_benchmark
from bench.denoise import (
    STATE_PATH,
    denoise_session,
    is_root,
)
from bench.builder.suite import SuiteBuilder
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
from bench.core.results import Report, report_from_json
from bench.runner.base import (
    Runner,
    plan,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential
from bench.utils import BenchError, print_exception
from bench.report.theme import error_console


type SuiteFactory = Callable[[Any], list[SuiteBuilder]]
type ReporterFactory = Factory[Reporter]
type RunnerFactory = Factory[Runner]
type Filter = Callable[[Benchmark], bool]
type FilterFactory = Factory[Filter]


class NoBenchmarksMatchedError(BenchError):
    """No benchmark matched the --include/--exclude selection."""


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
        """Register a deferred suite producer."""
        return dataclasses.replace(self, factories=self.factories + (fn,))

    def with_reporter(self, reporter: Reporter | ReporterFactory) -> BenchAppBuilder:
        """Set the reporter."""
        return dataclasses.replace(self, reporter=as_build(reporter))

    def with_params(self, params: type) -> BenchAppBuilder:
        """Replace the params dataclass whose fields become the CLI flags.

        Lets one app be reused with a different parameter set - e.g. a profiling
        variant that swaps in its own flags while inheriting the suites and the
        shared `with_*` configuration."""
        return dataclasses.replace(self, params=params)

    def with_runner(self, runner: Runner | RunnerFactory) -> BenchAppBuilder:
        """Set the runner."""
        return dataclasses.replace(self, runner=as_build(runner))

    def with_filter(self, keep: Filter) -> BenchAppBuilder:
        """Set the selection filter predicate."""
        return dataclasses.replace(self, filter=const(keep))

    def with_filter_fn(self, fn: FilterFactory) -> BenchAppBuilder:
        """Set the selection filter factory `(ctx) -> (Benchmark -> bool)`."""
        return dataclasses.replace(self, filter=fn)

    def with_environment(self, environment: EnvironmentCollector) -> BenchAppBuilder:
        """Set the environment collector (snapshot + diagnostics)."""
        return dataclasses.replace(self, environment=environment)

    def with_denoise(self, value: bool = True) -> BenchAppBuilder:
        """Minimize system noise knobs around the run (requires root)."""
        return dataclasses.replace(self, denoise=value)

    def run(self, args: list[str] | argparse.Namespace | None = None) -> Report:
        """Resolve factories, apply app defaults, and run every suite."""

        if isinstance(args, argparse.Namespace):
            cli_args = args
        else:
            parser = _make_run_parser(self.params, description=self.name)
            cli_args = parser.parse_args(args)
        # A user's params type is the single source of settings. When they
        # declare none, SharedBenchParams is the effective type, so the builtin
        # flags are still generated and honored.
        effective = self.params if self.params is not None else SharedBenchParams
        build_params = build_dataclass(effective, cli_args)

        collected = list(self.suites)
        for f in self.factories:
            collected.extend(f(build_params))
        suites = [self.overlay(s) for s in collected]

        ctx: Context[Any] = Context(
            params=build_params,
            suite="",
            benchmark=None,
            data=Data(),
        )

        env = self.environment.collect()
        env_diagnostics = run_checks(env) if env is not None else []

        reporter = (self.reporter or default_reporter)(ctx)
        reporter.set_environment(env, env_diagnostics)

        # --show
        show = getattr(cli_args, "show", None)
        if show:
            return self._do_show(reporter, show)

        planned = plan(suites, build_params)

        # --list
        if getattr(cli_args, "list_plan", False):
            return self._do_list(planned)

        planned = self._filter_benchmarks(ctx, planned)
        selecting = isinstance(build_params, SharedSelectionParams) and (
            build_params.include or build_params.exclude
        )
        if selecting and not planned:
            raise NoBenchmarksMatchedError(
                "No benchmarks matched --include/--exclude "
                "(run with --list to see what's available)."
            )

        print_diagnostics(env_diagnostics, "Environment checks")

        runner = (self.runner or default_runner)(ctx)
        runner.reporter = reporter

        if self.denoise:
            if not is_root():
                raise BenchError(
                    "--denoise requires root (try: sudo bench run --denoise ...)",
                    exit_code=2,
                )
            with denoise_session() as applied:
                console.print(
                    f"[bench.label]Denoise:[/] minimized {len(applied)} knob(s); "
                    f"state saved to {STATE_PATH}"
                )
                report = runner.run(planned, build_params)
        else:
            report = runner.run(planned, build_params)

        report.environment = env
        report.diagnostics = env_diagnostics
        return report

    def main(self, args: list[str] | argparse.Namespace | None = None) -> int:
        try:
            self.run(args)
            return 0
        except BenchError as e:
            print_exception(e, with_traceback=False)
            return e.exit_code
        except KeyboardInterrupt:
            error_console.print("[bench.failure]Interrupted[/]")
            return 130

    def _do_show(self, reporter: Reporter, path: str) -> Report:
        report = report_from_json(Path(path).read_text())
        reporter.set_environment(report.environment, report.diagnostics)
        for r in report.executions:
            reporter.execution_done(r)
        reporter.finalize()
        return report

    def _do_list(self, planned: list[Benchmark]) -> Report:
        console.print(_list_planned_benchmarks(planned))
        return Report()

    def _filter_benchmarks(
        self, ctx: Context[Any], planned: list[Benchmark]
    ) -> list[Benchmark]:
        keep = (self.filter or default_filter)(ctx)
        return [b for b in planned if keep(b)]


def run(*suites: SuiteBuilder) -> Report:
    """Run one or more suites with default settings.

    Lightweight sugar for `bench_app(<script>).add_all(*suites).run()`. For
    anything richer build a `bench_app(...)` directly.

    Returns:
        The report of running all the benchmarks.
    """
    return bench_app(Path(sys.argv[0]).stem).add_all(*suites).run()


def bench_app(name: str = "") -> BenchAppBuilder:
    """Top-level builder; `name` is shown as the description in `--help`.

    All configuration is applied through the builder's `with_*`/`add*` methods
    (e.g. `with_params`, `with_reporter`, `with_environment`, `with_denoise`), so
    the constructor carries only the name. To swap just the summary while keeping
    the progress bar and the `--json`/`--csv`/`--dir` sinks, set a reporter built
    from `default_reporter`:
    `bench_app().with_reporter(lambda ctx: default_reporter(ctx, summary=...))`.
    """
    return BenchAppBuilder(name=name)


def default_reporter(
    ctx: Context[Any],
    *,
    summary: Reporter | None = None,
    json: str | Path | JsonReporter | None = None,
    csv: str | Path | CsvReporter | None = None,
    dir: str | Path | DirReporter | None = None,
) -> Reporter:
    """Assemble the builtin reporter bundle: a progress bar, a summary, and the
    json, csv and dir output sinks.

    This is the default `ReporterFactory` and the single place tailored to the
    builtin sinks (those with flags on `SharedBenchParams`). Each of
    `summary`/`json`/`csv`/`dir` is the value to use when the matching CLI flag
    is unset: the flag wins, else this default, else the sink stays off. An app
    that always wants a sink supplies its default here, e.g.
    `with_reporter(lambda ctx: default_reporter(ctx, dir=...))`; a non-builtin
    sink is added by composition, e.g.
    `CompositeReporter(default_reporter(ctx), MyReporter())`.
    """
    sinks: list[Reporter] = []
    p = ctx.params
    if p.progress:
        sinks.append(ProgressReporter())

    sinks.append(summary or SummaryReporter(DefaultSummary()))

    # A reporter instance is authoritative: the app took control of that sink
    # (e.g. a DirReporter shared with `perf` via `output_dir`, or a JsonReporter
    # built with `include_output=True`), so it already folded in the flag.
    # Otherwise the flag wins over a path default.
    if isinstance(json, JsonReporter):
        sinks.append(json)
    elif j := (p.json or json):
        sinks.append(JsonReporter(Path(j)))
    if isinstance(csv, CsvReporter):
        sinks.append(csv)
    elif c := (p.csv or csv):
        sinks.append(CsvReporter(Path(c)))
    if isinstance(dir, DirReporter):
        sinks.append(dir)
    elif d := (p.dir or dir):
        sinks.append(DirReporter(Path(d)))

    return sinks[0] if len(sinks) == 1 else CompositeReporter(*sinks)


def default_runner(ctx: Context[Any]) -> Runner:
    p = ctx.params
    if p.dry:
        return Dry(verbose=p.verbose)
    if p.jobs > 1:
        return Parallel(workers=p.jobs, verbose=p.verbose)
    return Sequential(verbose=p.verbose)


def default_filter(ctx: Context[Any]) -> Callable[[Benchmark], bool]:
    p = ctx.params
    # selection is opt-in
    if not isinstance(p, SharedSelectionParams):
        return lambda _b: True
    inc = [re.compile(pat) for pat in (p.include or [])]
    exc = [re.compile(pat) for pat in (p.exclude or [])]

    def keep(b: Benchmark) -> bool:
        key = format_benchmark(b.suite, b.name, b.variant)
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
    # No prog= override: argparse derives it from sys.argv[0], so a user script
    # shows its own name (the `bench` console subcommands set their own prog).
    p = argparse.ArgumentParser(description=description or None)

    # The effective params type carries every flag: the user's own fields plus,
    # via inheritance, the shared bench/selection flags. Route each field to a
    # `--help` group by which base declares it (fields the user's type doesn't
    # inherit simply have no group). Missing groups are skipped entirely.
    effective = params if params is not None else SharedBenchParams
    all_names = {f.name for f in dataclasses.fields(effective)}
    selection_names = {
        f.name for f in dataclasses.fields(SharedSelectionParams)
    } & all_names
    runtime_names = (
        {f.name for f in dataclasses.fields(SharedBenchParams)} - selection_names
    ) & all_names
    user_names = all_names - selection_names - runtime_names

    for title, names in (
        ("context parameters", user_names),
        ("bench flags", runtime_names),
        ("selection", selection_names),
    ):
        if names:
            add_dataclass_args(
                p.add_argument_group(title), effective, skip=all_names - names
            )

    p.add_argument(
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


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def _list_planned_benchmarks(planned: list[Benchmark]) -> Tree:
    """Group planned benchmarks into a `suite -> benchmark -> variant` tree.

    A benchmark with several variants becomes a node whose leaves are the
    per-variant labels. A benchmark with a single variant stays a leaf labeled
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
                node.add(Text(format_benchmark(b.name, b.name, b.variant)))
    return root
