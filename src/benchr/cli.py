"""benchr CLI entry point."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchr.grammar.benchmark import bench
from benchr.grammar.context import add_dataclass_args, build_dataclass
from benchr.core.metric import Time
from benchr.grammar.suite import Suite, suite
from benchr.report.formatter import DefaultSummary
from benchr.report.reporter import (
    CompositeReporter,
    CsvReporter,
    DirReporter,
    JsonReporter,
    ProgressReporter,
    Reporter,
    SummaryReporter,
    console,
)
from benchr.utils import print_exception
from benchr.core.sample import Report, report_from_json
from benchr.report.stats import build_summary
from benchr.runner.base import (
    Runner,
    SuiteMaterializationError,
    plan,
)
from benchr.runner.dry import Dry
from benchr.runner.parallel import Parallel
from benchr.runner.sequential import Sequential


# TODO: should be just Any -> list[Suite]
type SuiteFactory = Callable[[Any], Suite | list[Suite]]


@dataclasses.dataclass(frozen=True, slots=True)
class Benchr:
    """Top-level run container: static suites + deferred suite factories.

    Mirrors `Suite` one level up. A `Suite` combines static benchmarks with
    `.factory` producers and resolves them at `materialize`; a `Benchr` combines
    static suites with `.factory` producers and resolves them at `run`. Because
    factories run *after* CLI parsing, suite discovery can depend on `params`
    (e.g. globbing a directory passed via `--spec-root`) without the script
    parsing argv itself.
    """

    suites: tuple[Suite, ...] = ()
    factories: tuple[SuiteFactory, ...] = ()
    params: type | None = None
    reporter: Reporter | None = None

    def add_suite(self, s: Suite) -> Benchr:
        """Register a suite."""
        return dataclasses.replace(self, suites=self.suites + (s,))

    def add_suites(self, *ss: Suite) -> Benchr:
        """Register several suites."""
        return dataclasses.replace(self, suites=self.suites + ss)

    def factory(self, fn: SuiteFactory) -> Benchr:
        """Register a deferred `(params) -> Suite | [Suite]` producer.

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
            collected.extend([produced] if isinstance(produced, Suite) else produced)

        return _do_run(collected, cli_args, self.reporter, build_params)


def run(
    suites: Suite | list[Suite] | SuiteFactory,
    *,
    params: type | None = None,
    reporter: Reporter | None = None,
    argv: list[str] | None = None,
) -> Report:
    """
    The entrypoint for benchmarking.
    Parse argv, build the ctx, run the benchmark, emit reports.

    Args:
        suites: The suite(s) to run. Either a `Suite`, a list of `Suite`, or a
            deferred `(params) -> Suite | [Suite]` producer that is called after
            CLI parsing (for suite discovery that depends on `params`). To
            combine static and discovered suites, build a `Benchr` directly.
        params: The user's @dataclass that declares additional CLI flags and forms the user-defined context. Defaults to `None` if omitted.
        reporter: The reporter to be used for process the result. Defaults to `SummaryReporter`
        argv: The command-line parameters that will be parsed and use to fill the user-defined context.

    Returns:
        The report of running all the benchmarks
    """
    app = Benchr(params=params, reporter=reporter)
    if callable(suites):  # a Suite / list is never callable
        app = app.factory(suites)
    elif isinstance(suites, Suite):
        app = app.add_suite(suites)
    else:
        app = app.add_suites(*suites)

    return app.run(argv)


def _do_run(
    suites: list[Suite],
    cli_args: argparse.Namespace,
    reporter: Reporter | None,
    build_params: Any,
) -> Report:
    """Run already-parsed benchmarks: build the reporter, plan the suites,
    apply CLI overrides and hand off to the runner."""
    if reporter is None:
        reporter = SummaryReporter(DefaultSummary())

    reporter = _build_reporter(
        cli_args, reporter, with_progress=not cli_args.no_progress
    )

    try:
        planned = plan(suites, build_params)
    except SuiteMaterializationError as e:
        print_exception(e)
        sys.exit(1)

    runner = _make_runner(cli_args, reporter)

    try:
        return runner.run(planned, build_params)
    except KeyboardInterrupt:
        console.print("[benchr.failure]Interrupted[/]")
        sys.exit(1)


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

    if ns.compare and isinstance(summary_reporter, SummaryReporter):
        summary_reporter.set_baseline([Path(p) for p in ns.compare])

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
    p = argparse.ArgumentParser(prog="benchr")
    _add_user_params(p, params)
    _add_runtime_flags(p.add_argument_group("benchr flags"))
    return p


def _add_user_params(parser: argparse.ArgumentParser, params: type | None) -> None:
    if params is None:
        return
    group_ = parser.add_argument_group("context parameters")
    add_dataclass_args(group_, params)


def _add_runtime_flags(
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


# ---------------------------------------------------------------------------
# `benchr` CLI: bench / compare
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchr",
        description=(
            "benchr — run, compare, and inspect command-line benchmarks. "
            "See `benchr <sub> --help` for the detailed flag set of each "
            "subcommand."
        ),
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    _bench_subparser(
        sub.add_parser(
            "bench",
            help="Time one or more shell commands (hyperfine-style).",
            description=(
                "Time one or more shell commands. Each positional CMD is split with "
                "shlex and benchmarked as its own benchmark; results are summarized "
                "side by side. Example:\n\n"
                "    benchr bench --runs 20 --warmup 2 'sleep 0.1' 'sleep 0.2'\n\n"
                "Use --json / --csv / --dir to persist outputs and --compare to diff "
                "against a previously saved JSON baseline."
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    )
    _compare_subparser(
        sub.add_parser(
            "compare",
            help="Summarize or compare JSON reports from prior runs.",
            description=(
                "Summarize one or more JSON reports and print ratios against the "
                "first one as a baseline. With a single file, just pretty-prints "
                "its summary."
            ),
        )
    )

    ns = parser.parse_args(argv)
    return ns._func(ns)


# ----- bench --------------------------------------------------------------


def _bench_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "commands",
        nargs="+",
        metavar="CMD",
        help="One or more shell commands to benchmark (each split with shlex).",
    )
    _add_runtime_flags(p)
    p.add_argument(
        "--runs",
        type=int,
        default=10,
        metavar="N",
        help="Measured run count for every command (default: 10).",
    )
    p.add_argument(
        "--warmup",
        type=int,
        default=0,
        metavar="N",
        help="Warmup runs executed but excluded from stats (default: 0).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Kill a run that takes longer than SECONDS (treated as a failure).",
    )
    p.add_argument(
        "--metric",
        type=str,
        default="elapsed",
        metavar="NAME",
        help="Metric to highlight in the comparison summary (default: elapsed).",
    )
    p.set_defaults(_func=_cmd_bench)


def _cmd_bench(ns: argparse.Namespace) -> int:
    import shlex

    argvs = [tuple(shlex.split(cmd)) for cmd in ns.commands]
    b = (
        bench("bench")
        .with_matrix(command=argvs)
        .with_command(lambda ctx: list(ctx.matrix.command))
        .with_label(lambda bb: " ".join(bb.data["command"]))
        .with_cwd(Path.cwd())
        .with_metric(Time())
        .with_runs(ns.runs)
    )

    # TODO: `bench` defaults to a fixed 10 runs (set above). A time-bounded
    # default (hyperfine-style "10 runs or 3 seconds") would need a duration
    # stopping policy; not implemented.
    if ns.timeout is not None:
        b = b.with_timeout(ns.timeout)
    if ns.warmup > 0:
        b = b.with_warmup(ns.warmup)
    s = suite("bench", b)

    metrics = {ns.metric} if ns.metric else None
    reporter = SummaryReporter(formatter=DefaultSummary(metrics=metrics))

    _do_run([s], ns, reporter, None)
    return 0


# ----- compare ------------------------------------------------------------


def _compare_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+")
    p.add_argument(
        "--metric",
        type=str,
        default=None,
        help="Comma-separated metric filter (e.g. runtime,max_rss)",
    )
    p.set_defaults(_func=_cmd_compare)


def _cmd_compare(ns: argparse.Namespace) -> int:
    files = [Path(f) for f in ns.files]
    for f in files:
        if not f.exists():
            print(f"Error: file not found: {f}", file=sys.stderr)
            return 1
    metrics = set(ns.metric.split(",")) if ns.metric else None
    if len(files) == 1:
        # No comparison; just summarize.
        r = report_from_json(files[0].read_text())
        data = build_summary(r, [])
        out = DefaultSummary(metrics=metrics).format(data)
        if out:
            console.print(out)
        return 0
    # TODO: why do we need current - the idea is that first is a baseline to which we compare the other ones?
    # First file is the baseline; rest are comparees. Summarize the *last*
    # file ("current") against the baseline, plus all intermediates as
    # additional comparees.
    current = report_from_json(files[-1].read_text())
    data = build_summary(current, files[:-1])
    out = DefaultSummary(metrics=metrics).format(data)
    if out:
        console.print(out)
    return 0
