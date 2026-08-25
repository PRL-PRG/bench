"""The `BenchAppBuilder` abstraction and the `run(...)` benchmarking pipeline.

Named `run` so that `from bench.run import run` re-binds the public `run`
symbol on the package, keeping `from bench import run` pointing at the
function rather than at this submodule.
"""

from __future__ import annotations, generators

import argparse
import dataclasses
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence, cast

from rich.text import Text
from rich.tree import Tree

from bench.builder.base import (
    BenchmarkPred,
    BuilderBase,
    as_build,
    const,
    merge_sequence,
)
from bench.builder.benchmark import Benchmark
from bench.builder.context import (
    Params,
    SharedBenchParams,
    SharedReporterParams,
    SharedRunnerParams,
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
from bench.core.invocation import format_benchmark, format_variant
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
)
from bench.report.reporter import (
    print_diagnostics as do_print_diagnostics,
)
from bench.report.summary import summarize
from bench.report.theme import error_console
from bench.runner.base import (
    Runner,
    plan,
)
from bench.runner.dry import DryRunner
from bench.runner.parallel import Parallel
from bench.runner.sequential import SequentialRunner
from bench.utils import BenchError, print_exception

# HACK: The argument should be "Params or its child" but this is the best
# we have for now
type ParamFactory[T] = Callable[[Any], T]
"""A factory that produces based on the parameters."""

type SuiteGenerator = ParamFactory[Sequence[SuiteBuilder]]


class NoBenchmarksMatchedError(BenchError):
    """No benchmark matched the --include/--exclude selection."""


def as_param_build[T](value: T | ParamFactory[T]) -> ParamFactory[T]:
    if callable(value):
        return cast(ParamFactory[T], value)
    return const(value)


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
    suites: Sequence[SuiteGenerator] = ()

    params: type[Params] | None = None

    reporter: ParamFactory[Reporter] | None = None
    summary: ParamFactory[Reporter] | None = None
    runner: ParamFactory[Runner] | None = None
    environment: EnvironmentCollector = NoEnvironment()
    denoise: bool = False

    # ----- producers -------------------------------------------------

    def add(self, *ss: SuiteBuilder) -> BenchAppBuilder:
        """Register suite(s)."""
        return self.replace(
            "suites",
            tuple(const((s,)) for s in ss),
            override=False,
            merge=merge_sequence,
        )

    def generator(self, fn: SuiteGenerator) -> BenchAppBuilder:
        """Register a deferred suite producer."""
        return self.replace(
            "suites",
            (fn,),
            override=False,
            merge=merge_sequence,
        )

    # ----- Run setters -------------------------------------------------
    def with_params(
        self, params: type[Params], override: bool = True
    ) -> BenchAppBuilder:
        """Replace the params dataclass whose fields become the CLI flags.

        Lets one app be reused with a different parameter set - e.g. a profiling
        variant that swaps in its own flags while inheriting the suites and the
        shared `with_*` configuration."""
        return self.replace("params", params, override=override)

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

    def with_environment(
        self, environment: EnvironmentCollector, override: bool = True
    ) -> BenchAppBuilder:
        """Set the environment collector (snapshot + diagnostics)."""
        return self.replace(
            "environment",
            environment,
            override=override,
        )

    def with_denoise(
        self, value: bool = True, override: bool = True
    ) -> BenchAppBuilder:
        """Minimize system noise knobs around the run (requires root)."""
        return self.replace(
            "denoise",
            value,
            override=override,
        )

    # ----- instantiate benchmarks -----------

    def plan_benchmarks(
        self,
        build_params: Params,
        *,
        use_defaults: bool = False,
    ):
        overlay = self
        if use_defaults:
            overlay = self.with_filter(default_filter(build_params))

        suites = [
            s.inherit_from(overlay)
            for generator in overlay.suites
            for s in generator(build_params)
        ]

        return plan(suites, build_params)

    # ----- instantiate reporter -----------
    def get_reporter(
        self, build_params: Params, *, use_defaults: bool
    ) -> Reporter | None:
        if self.reporter is not None:
            reporter = self.reporter(build_params)
            if self.summary is not None:
                reporter = CompositeReporter(reporter, self.summary(build_params))
        elif use_defaults:
            reporter = default_reporter(build_params)

            if self.summary is not None:
                summary = self.summary(build_params)
            else:
                summary = SummaryReporter(DefaultSummary())

            if reporter is None:
                reporter = summary
            else:
                reporter = CompositeReporter(reporter, summary)
        else:
            return None

        return reporter

    # ----- run -----------

    def run(
        self,
        build_params: Params,
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
        reporter = self.get_reporter(build_params, use_defaults=use_defaults)
        if reporter is None:
            raise ValueError("No reporter is defined")

        # Get runner
        if self.runner is not None:
            runner = self.runner(build_params)
        elif use_defaults:
            runner = default_runner(build_params)
            if runner is None:
                raise ValueError(
                    "Cannot instantiate default runner without SharedRunnerParams parameters"
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
            raise NoBenchmarksMatchedError("No benchmark planned")

        # Run
        if self.denoise:
            if not is_root():
                raise BenchError(
                    "Denoise requires root "
                    "(try running with `sudo` ONLY IF YOU TRUST THE SUITE)",
                    exit_code=2,
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
            return self.do_show_report(build_params, show_path)

        planned = self.plan_benchmarks(build_params, use_defaults=True)

        # --list
        if getattr(cli_args, "list_plan", False):
            # TODO: list_plan should be a parameter
            console.print(_list_planned_benchmarks(planned))
            return Report()

        return self.run(
            build_params, planned, use_defaults=True, print_diagnostics=True
        )

    def main(self, args: list[str] | argparse.Namespace | None = None) -> int:
        """`run_cli` as a process exit code: user-facing errors become a clean
        stderr message instead of a traceback. The entry point a `__main__` wants."""
        try:
            self.run_cli(args)
            return 0
        except BenchError as e:
            print_exception(e, with_traceback=False)
            return e.exit_code
        except KeyboardInterrupt:
            error_console.print("[bench.failure]Interrupted[/]")
            return 130

    def do_show_report(self, build_params: Params, path: str):
        report = report_from_json(Path(path).read_text())

        reporter = self.get_reporter(build_params, use_defaults=True)

        if reporter is not None:
            for r in report.executions:
                reporter.execution_done(r)
            reporter.finalize(report)

        DefaultSummary()(summarize(report))
        return report


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


def bench_app[P: Params](
    name: str = "",
    *,
    params: type[P] | None = None,
    reporter: Reporter | Callable[[P], Reporter] | None = None,
    summary: Reporter | Callable[[P], Reporter] | None = None,
    environment: EnvironmentCollector | None = None,
    denoise: bool = False,
) -> BenchAppBuilder:
    """Top-level builder combining suites with common settings.

    Pass `summary` to swap the summary while keeping the default progress bar and
    the --json/--csv/--dir sinks. Pass `reporter` to replace the whole reporter
    (progress included).
    """

    reporter = cast(ParamFactory[Reporter], reporter)
    summary = cast(ParamFactory[Reporter], summary)

    return BenchAppBuilder(
        name=name,
        params=params,
        reporter=as_param_build(reporter) if reporter is not None else None,
        summary=as_param_build(summary) if summary is not None else None,
        environment=environment or NoEnvironment(),
        denoise=denoise,
    )


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def default_reporter(
    params: Params,
    *,
    json: str | Path | JsonReporter | None = None,
    csv: str | Path | CsvReporter | None = None,
    dir: str | Path | DirReporter | None = None,
) -> Reporter | None:
    """Assemble the builtin reporter bundle: a progress bar and the json, csv and
    dir output sinks, plus `summary` if one is given.

    Each of `summary`/`json`/`csv`/`dir` is the value to use when the matching CLI
    flag is unset: the flag wins, else this default, else the sink stays off. An
    app that always wants a sink supplies its default here, e.g.
    `with_reporter(lambda p: default_reporter(p, dir=...))`; a non-builtin sink is
    added by composition, e.g. `CompositeReporter(default_reporter(p), MyReporter())`.
    """
    is_params = isinstance(params, SharedReporterParams)

    sinks: list[Reporter] = []
    if is_params and params.progress:
        sinks.append(ProgressReporter())

    # A reporter instance is authoritative: the app took control of that sink
    # (e.g. a DirReporter shared with `perf` via `output_dir`, or a JsonReporter
    # built with `include_output=True`), so it already folded in the flag.
    # Otherwise the flag wins over a path default.
    if isinstance(json, JsonReporter):
        sinks.append(json)
    elif j := ((is_params and params.json) or json):
        sinks.append(JsonReporter(Path(j)))

    if isinstance(csv, CsvReporter):
        sinks.append(csv)
    elif c := ((is_params and params.csv) or csv):
        sinks.append(CsvReporter(Path(c)))

    if isinstance(dir, DirReporter):
        sinks.append(dir)
    elif d := ((is_params and params.dir) or dir):
        sinks.append(DirReporter(Path(d)))

    if len(sinks) == 0:
        return None
    elif len(sinks) == 1:
        return sinks[0]
    else:
        return CompositeReporter(*sinks)


def default_runner(params: Params) -> Runner | None:
    if not isinstance(params, SharedRunnerParams):
        return None

    if params.dry:
        return DryRunner(verbose=params.verbose)
    if params.jobs > 1:
        return Parallel(workers=params.jobs, verbose=params.verbose)
    return SequentialRunner(verbose=params.verbose)


def default_filter(params: Params) -> BenchmarkPred:
    if not isinstance(params, SharedSelectionParams):
        return lambda _: True

    inc = [re.compile(pat) for pat in (params.include or [])]
    exc = [re.compile(pat) for pat in (params.exclude or [])]

    def keep(b: Benchmark) -> bool:
        # Both spellings of the same variant: the canonical `(k=v, ...)` key and,
        # when the app sets one, the label the reports show. A pattern written
        # against what the terminal prints then selects what the user expects,
        # without the `k=v` form ceasing to work.
        keys = [format_benchmark(b.suite, b.name, b.variant)]
        if b.variant_label:
            keys.append(format_benchmark(b.suite, b.name, b.variant, b.variant_label))
        if inc and not any(r.search(k) for k in keys for r in inc):
            return False
        return not any(r.search(k) for k in keys for r in exc)

    return keep


# ---------------------------------------------------------------------------
# Argparse builders
# ---------------------------------------------------------------------------


# TODO: This should live somewhere else
def _make_run_parser(params: type, description: str = "") -> argparse.ArgumentParser:
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
                    bench_node.add(
                        Text(b.variant_label or format_variant(b.variant).strip())
                    )
            else:
                b = variants[0]
                node.add(
                    Text(format_benchmark(b.name, b.name, b.variant, b.variant_label))
                )
    return root
