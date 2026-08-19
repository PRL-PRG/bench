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
from typing import Any, Sequence

from rich.text import Text
from rich.tree import Tree

from bench.builder.base import BuilderBase, as_build, merge_sequence
from bench.builder.benchmark import Benchmark
from bench.builder.context import (
    Context,
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
    print_diagnostics,
)
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

    # ----- with_* setters (shared ones live on BuilderBase) -----------

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

    # ----- run -----------

    def run(self, args: list[str] | argparse.Namespace | None = None) -> Report:
        """Resolve generators, apply app defaults, and run every suite."""

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
        for f in self.generators:
            collected.extend(f(build_params))
        suites = [s.inherit_from(self) for s in collected]

        env = self.environment.collect()
        env_diagnostics = run_checks(env) if env is not None else []

        if self.reporter is not None:
            reporter = self.reporter(build_params)
        else:
            summary = self.summary(build_params) if self.summary is not None else None
            reporter = default_reporter(build_params, summary)
        reporter.set_environment(env, env_diagnostics)

        # --show
        show = getattr(cli_args, "show", None)
        if show:
            return self._do_show(reporter, show)

        planned = plan(suites, build_params)

        # --list
        if getattr(cli_args, "list_plan", False):
            return self._do_list(planned)

        planned = self._filter_benchmarks(build_params, planned)
        selecting = isinstance(build_params, SharedSelectionParams) and (
            build_params.include or build_params.exclude
        )
        if selecting and not planned:
            raise NoBenchmarksMatchedError(
                "No benchmarks matched --include/--exclude "
                "(run with --list to see what's available)."
            )

        print_diagnostics(env_diagnostics, "Environment checks")

        runner = (self.runner or default_runner)(build_params)
        runner.reporter = reporter

        if self.denoise:
            if not is_root():
                raise PermissionError(
                    "--denoise requires root (try: sudo bench run --denoise ...)"
                )
            with denoise_session() as applied:
                console.print(
                    f"[bench.label]Denoise:[/] minimized {len(applied)} knob(s); "
                    f"state saved to {STATE_PATH}"
                )
                report = runner.run(planned)
        else:
            report = runner.run(planned)

        report.environment = env
        report.diagnostics = env_diagnostics
        return report

    def _do_show(self, reporter: Reporter, path: str) -> Report:
        report = report_from_json(Path(path).read_text())
        reporter.set_environment(report.environment, report.diagnostics)
        for r in report.executions:
            reporter.execution_done(r)
        reporter.finalize(report)
        return report

    def _do_list(self, planned: list[Benchmark]) -> Report:
        console.print(_list_planned_benchmarks(planned))
        return Report()

    def _filter_benchmarks(
        self, ctx: Context[Any], planned: list[Benchmark]
    ) -> list[Benchmark]:
        pred = default_filter(ctx)
        return [b for b in planned if pred(b)]


def run(*suites: SuiteBuilder) -> Report:
    """Run one or more suites with default settings.

    Lightweight sugar for `bench_app(<script>).add_all(*suites).run()`. For
    anything richer build a `bench_app(...)` directly.

    Returns:
        The report of running all the benchmarks.
    """
    return bench_app(Path(sys.argv[0]).stem).add(*suites).run()


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


def default_reporter(params: Any, summary: Reporter | None = None) -> Reporter:
    sinks: list[Reporter] = []
    if params.progress:
        sinks.append(ProgressReporter())

    sinks.append(summary or SummaryReporter(DefaultSummary()))

    if params.json:
        sinks.append(JsonReporter(Path(params.json)))
    if params.csv:
        sinks.append(CsvReporter(Path(params.csv)))
    if params.dir:
        sinks.append(DirReporter(Path(params.dir)))

    return sinks[0] if len(sinks) == 1 else CompositeReporter(*sinks)


def default_runner(params: Any) -> Runner:
    if params.dry:
        return Dry(verbose=params.verbose)
    if params.jobs > 1:
        return Parallel(workers=params.jobs, verbose=params.verbose)
    return Sequential(verbose=params.verbose)


def default_filter(params: Any) -> Callable[[Benchmark], bool]:
    # selection is opt-in
    if not isinstance(params, SharedSelectionParams):
        return lambda _b: True

    inc = [re.compile(pat) for pat in (params.include or [])]
    exc = [re.compile(pat) for pat in (params.exclude or [])]

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
