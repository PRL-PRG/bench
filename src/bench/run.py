"""The `BenchAppBuilder` abstraction and the `run(...)` benchmarking pipeline.

Named `run` so that `from bench.run import run` re-binds the public `run`
symbol on the package, keeping `from bench import run` pointing at the
function rather than at this submodule.
"""

from __future__ import annotations, generators

import argparse
import dataclasses
import itertools
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence

from rich.text import Text
from rich.tree import Tree

from bench.builder.base import BenchmarkPred, BuilderBase, as_build, merge_sequence
from bench.builder.benchmark import Benchmark
from bench.builder.context import (
    SharedBenchParams,
    SharedSelectionParams,
    add_dataclass_args,
    build_dataclass,
)
from bench.builder.suite import SuiteBuilder
from bench.core.checks import run_checks
from bench.core.environment import (
    EnvironmentCollector,
    NoEnvironment,
)
from bench.core.invocation import format_benchmark
from bench.core.results import Report, report_from_json
from bench.denoise import (
    STATE_PATH,
    denoise_session,
    is_root,
)
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
    print_diagnostics as do_print_diagnostics,
)
from bench.report.summary import summarize
from bench.runner.base import (
    Runner,
    plan,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential

type ParamFactory[T] = Callable[[Any], T]
"""A factory that produces based on the parameters"""

type SuiteGenerator = ParamFactory[Sequence[SuiteBuilder]]


class NoBenchmarksMatchedError(Exception):
    """No benchmark matched the --include/--exclude selection."""


@dataclasses.dataclass(frozen=True, slots=True)
class BenchAppBuilder(BuilderBase):
    """Top-level builder: static suites + deferred suite generators, plus common
    settings applied to every suite.

    The third builder level after `bench()`/`suite()`, sharing the same
    `BuilderBase`. The inheritable `.with_*` settings declared here are the
    weakest layer: they fill fields a suite or benchmark left unset, and a more
    specific level overrides them (`inherit_from`). `name` is shown as the description
    in `--help`.
    """

    name: str = ""
    suites: Sequence[SuiteBuilder] = ()
    generators: Sequence[SuiteGenerator] = ()

    params: type | None = None

    reporter: ParamFactory[Reporter] | None = None
    summary: ParamFactory[Reporter] | None = None
    runner: ParamFactory[Runner] | None = None
    environment: EnvironmentCollector = NoEnvironment()
    denoise: bool = False

    # ----- producers -------------------------------------------------

    def add(self, *ss: SuiteBuilder) -> BenchAppBuilder:
        """Register suite(s)."""
        """Register several suites."""
        return self.replace(
            "suites",
            ss,
            override=False,
            merge=merge_sequence,
        )

    def generator(self, fn: SuiteGenerator) -> BenchAppBuilder:
        """Register a deferred suite producer."""
        return self.replace(
            "generators",
            (fn,),
            override=False,
            merge=merge_sequence,
        )

    # ----- Run setters -------------------------------------------------

    def with_reporter(
        self, reporter: Reporter | ParamFactory[Reporter], override: bool = False
    ) -> BenchAppBuilder:
        """Set the reporter."""
        return self.replace(
            "reporter",
            as_build(reporter),
            override=override,
        )

    def with_summary(
        self, summary: Reporter | ParamFactory[Reporter], override: bool = False
    ) -> BenchAppBuilder:
        """Swap the summary while keeping the default progress bar and the
        --json/--csv/--dir sinks. Ignored when a full reporter is set."""
        return self.replace(
            "summary",
            as_build(summary),
            override=override,
        )

    def with_runner(
        self, runner: Runner | ParamFactory[Runner], override: bool = False
    ) -> BenchAppBuilder:
        """Set the runner."""
        return self.replace(
            "runner",
            as_build(runner),
            override=override,
        )

    # ----- Instantiate benchmarks -----------

    def plan_benchmarks(
        self,
        build_params: Any,
        *,
        use_defaults: bool = False,
    ):
        suites = [
            s.inherit_from(self)
            for s in itertools.chain(
                self.suites, *(gen(build_params) for gen in self.generators)
            )
        ]

        planned = plan(suites, build_params)
        if use_defaults:
            pred = default_filter(build_params)
            planned = [b for b in planned if pred(b)]

        return planned

    # ----- run -----------

    def run(
        self,
        build_params: Any,
        planned: list[Benchmark] | None = None,
        *,
        use_defaults: bool = False,
        print_diagnostics: bool = True,
    ) -> Report:
        # Setup environment
        env = self.environment.collect()
        env_diagnostics = run_checks(env) if env is not None else []

        if print_diagnostics:
            do_print_diagnostics(env_diagnostics, "Environment checks")

        # Setup reporters
        if self.reporter is not None:
            reporter = self.reporter(build_params)
        elif use_defaults:
            reporter = default_reporter(build_params)
            if reporter is None:
                raise ValueError(
                    "Cannot instantiate default reporters without SharedBenchParams parameters"
                )
        else:
            raise ValueError("No reporter is defined")

        # Add summary
        if self.summary is not None:
            reporter = CompositeReporter(reporter, self.summary(build_params))
        elif use_defaults:
            reporter = CompositeReporter(reporter, SummaryReporter(DefaultSummary()))

        # Get runner
        if self.runner is not None:
            runner = self.runner(build_params)
        elif use_defaults:
            runner = default_runner(build_params)
            if runner is None:
                raise ValueError(
                    "Cannot instantiate default runner without SharedBenchParams parameters"
                )
        else:
            raise ValueError("No runner is defined")

        # Get benchmarks
        if planned is None:
            planned = self.plan_benchmarks(
                build_params,
                use_defaults=use_defaults,
            )

        if len(planned) == 0:
            raise ValueError("No benchmark planned")

        # Run
        if self.denoise:
            if not is_root():
                raise PermissionError(
                    "Denoise requires root (try running witg `sudo` ONLY IF YOU TRUST THE SUITE)"
                )
            with denoise_session() as applied:
                console.print(
                    f"[bench.label]Denoise:[/] minimized {len(applied)} knob(s); "
                    f"state saved to {STATE_PATH}"
                )
                return runner.run(planned, reporter, env, env_diagnostics)
        else:
            return runner.run(planned, reporter, env, env_diagnostics)

    # ----- run_cli -----------

    def run_cli(self, args: list[str] | argparse.Namespace | None = None) -> Report:
        """Resolve generators, apply app defaults, and run every suite."""

        params = self.params if self.params is not None else SharedBenchParams

        if isinstance(args, argparse.Namespace):
            cli_args = args
        else:
            parser = _make_run_parser(params, description=self.name)
            cli_args = parser.parse_args(args)

        build_params = build_dataclass(params, cli_args)

        # --show
        # TODO: This should be higher
        show_path = getattr(cli_args, "show", None)
        if show_path is not None:
            return show_report(default_reporter(build_params), show_path)

        planned = self.plan_benchmarks(build_params, use_defaults=True)

        # --list
        if getattr(cli_args, "list_plan", False):
            # TODO: list_plan should be a parameter
            console.print(_list_planned_benchmarks(planned))
            return Report()

        return self.run(
            build_params, planned, use_defaults=True, print_diagnostics=True
        )


# ---------------------------------------------------------------------------
# Shorthand constructors
# ---------------------------------------------------------------------------


def run(*suites: SuiteBuilder) -> Report:
    """Run one or more suites with default settings.

    Lightweight sugar for `bench_app(<script>).add_all(*suites).run()`. For
    anything richer build a `bench_app(...)` directly.

    Returns:
        The report of running all the benchmarks.
    """
    return bench_app(Path(sys.argv[0]).stem).add(*suites).run_cli()


def bench_app(
    name: str = "",
    *,
    params: type | None = None,
    reporter: Reporter | ParamFactory[Reporter] | None = None,
    summary: Reporter | ParamFactory[Reporter] | None = None,
    environment: EnvironmentCollector | None = None,
    denoise: bool = False,
) -> BenchAppBuilder:
    """Top-level builder combining suites with common settings.

    Pass `summary` to swap the summary while keeping the default progress bar and
    the --json/--csv/--dir sinks. Pass `reporter` to replace the whole reporter
    (progress included).
    """

    return BenchAppBuilder(
        name=name,
        params=params,
        reporter=as_build(reporter) if reporter is not None else None,
        summary=as_build(summary) if summary is not None else None,
        environment=environment or NoEnvironment(),
        denoise=denoise,
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_reporter(params: Any) -> Reporter | None:
    if not isinstance(params, SharedBenchParams):
        return None

    sinks: list[Reporter] = []
    if params.progress:
        sinks.append(ProgressReporter())
    if params.json:
        sinks.append(JsonReporter(Path(params.json)))
    if params.csv:
        sinks.append(CsvReporter(Path(params.csv)))
    if params.dir:
        sinks.append(DirReporter(Path(params.dir)))

    return sinks[0] if len(sinks) == 1 else CompositeReporter(*sinks)


def default_runner(params: Any) -> Runner | None:
    if not isinstance(params, SharedBenchParams):
        return None

    if params.dry:
        return Dry(verbose=params.verbose)
    if params.jobs > 1:
        return Parallel(workers=params.jobs, verbose=params.verbose)
    return Sequential(verbose=params.verbose)


def default_filter(params: Any) -> BenchmarkPred:
    if not isinstance(params, SharedSelectionParams):
        return lambda _: True

    inc = [re.compile(pat) for pat in (params.include or [])]
    exc = [re.compile(pat) for pat in (params.exclude or [])]

    def keep(b: Benchmark) -> bool:
        key = format_benchmark(b.suite, b.name, b.variant)
        if inc and not any(r.search(key) for r in inc):
            return False
        return not any(r.search(key) for r in exc)

    return keep


# ---------------------------------------------------------------------------
# Argparse builders
# ---------------------------------------------------------------------------


# TODO: This should live somewhere else
def _make_run_parser(
    params: type, description: str = ""
) -> argparse.ArgumentParser:
    # No prog= override: argparse derives it from sys.argv[0], so a user script
    # shows its own name (the `bench` console subcommands set their own prog).
    p = argparse.ArgumentParser(description=description or None)

    # The effective params type carries every flag: the user's own fields plus,
    # via inheritance, the shared bench/selection flags. Route each field to a
    # `--help` group by which base declares it (fields the user's type doesn't
    # inherit simply have no group). Missing groups are skipped entirely.
    all_names = {f.name for f in dataclasses.fields(params)}
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
                p.add_argument_group(title), params, skip=all_names - names
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
# Pretty-printing helpers
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


def show_report(reporter: Reporter | None, path: str) -> Report:
    report = report_from_json(Path(path).read_text())

    if reporter is not None:
        for r in report.executions:
            reporter.execution_done(r)
        reporter.finalize(report)

    DefaultSummary()(summarize(report))
    return report
