"""benchr CLI: ``bench``, ``run`` (programmatic), ``compare``, ``show``.

The ``run(...)`` function in this module is the main entry point used by
benchmark scripts. ``benchr`` (the CLI) covers the straightforward benchmarking
tasks ``benchr bench``, ``benchr compare`` and ``benchr show`` for inspecting
JSON outputs offline."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from benchr.grammar.benchmark import bench
from benchr.grammar.context import add_dataclass_args, build_dataclass
from benchr.core.metric import Time
from benchr.core.policy import FixedRuns
from benchr.grammar.suite import Suite, suite
from benchr.report.formatter import DefaultSummary, Formatter
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
from benchr.core.sample import Report, report_from_json
from benchr.report.stats import build_summary
from benchr.runner.base import (
    PlannedBenchmark,
    Runner,
    SuiteMaterializationError,
    plan,
)
from benchr.runner.dry import Dry
from benchr.runner.parallel import Parallel
from benchr.runner.sequential import Sequential


# ---------------------------------------------------------------------------
# run(): the main entry point for benchmark scripts
# ---------------------------------------------------------------------------


def run(
    suites: list[Suite] | Suite,
    *,
    params: type | None = None,
    reporter: Reporter | None = None,
    formatter: Formatter | None = None,
    argv: list[str] | None = None,
) -> Report:
    """Parse argv, build the ctx, run the benchmark, emit reports.

    ``suites`` is one Suite or a list.
    ``params`` is the user's @dataclass that declares CLI flags. If omitted,
    no user flags are added and ``ctx = None`` is passed to builders.
    ``reporter`` overrides the default Summary reporter; output flags
    (--json/--csv/--dir) are *additional* CompositeReporter sinks alongside.
    ``formatter`` overrides the default summary formatter (used for the
    terminal summary; defaults to ``DefaultSummary``).

    Returns the Report the runner accumulated — every Sample plus a RunRecord
    per execution (so ``report.failures`` is populated) — for callers that want
    to do follow-up analysis after the side-effecting reporters have run.
    """
    if isinstance(suites, Suite):
        suites = [suites]

    parser = _make_run_parser(params)
    cli_args = parser.parse_args(argv)
    build_params = build_dataclass(params, cli_args) if params is not None else None

    reporter = reporter or SummaryReporter(formatter=formatter)
    reporter = _build_reporter(
        cli_args,
        reporter,
        with_progress=not cli_args.dry and not cli_args.quiet,
    )

    try:
        planned = plan(suites, build_params)
    except SuiteMaterializationError as e:
        # TODO: there should be a better place for this
        print(e, file=sys.stderr)
        sys.exit(1)
    benchmarks = _apply_cli_overrides(planned, cli_args)
    runner = _make_runner(cli_args, reporter)
    try:
        return runner.run(benchmarks, build_params)
    except KeyboardInterrupt:
        console.print("[benchr.failure]Interrupted[/]")
        sys.exit(1)


def _apply_cli_overrides(
    planned: list[PlannedBenchmark], ns: argparse.Namespace
) -> list[PlannedBenchmark]:
    overrides = {}
    if ns.runs is not None:
        overrides["runs"] = FixedRuns(ns.runs)
    if ns.warmup is not None:
        overrides["warmup"] = FixedRuns(ns.warmup)

    if overrides:
        planned = [
            dataclasses.replace(
                p, benchmark=dataclasses.replace(p.benchmark, **overrides)
            )
            for p in planned
        ]
    return planned


def _build_reporter(
    ns: argparse.Namespace,
    summary_reporter: Reporter,
    *,
    with_progress: bool,
) -> Reporter:
    """Build the live reporter: optional progress, the summary reporter, plus
    any --json/--csv/--dir sinks, fanned out via CompositeReporter. Wires
    --compare onto the summary reporter when it is a SummaryReporter."""
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
    p = argparse.ArgumentParser(prog="benchr-script")
    _add_user_params(p, params)
    _add_benchr_flags(p)
    return p


def _add_user_params(parser: argparse.ArgumentParser, params: type | None) -> None:
    if params is None:
        return
    group_ = parser.add_argument_group("Script parameters")
    add_dataclass_args(group_, params)


def _add_benchr_flags(parser: argparse.ArgumentParser) -> None:
    _add_shared_flags(parser.add_argument_group("benchr flags"))


def _add_shared_flags(
    # argparse exposes no public name for the add_argument_group() return type.
    g: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
    *,
    runs_default: int | None = None,
    warmup_default: int | None = None,
) -> None:
    """The flag set shared by ``benchr.run()`` scripts and ``benchr bench``.

    A ``None`` default for --runs/--warmup means "no override: each benchmark
    keeps its own stopping policy" (the script case); ``benchr bench`` passes
    concrete counts since its ad-hoc benchmarks have no policy of their own.
    """
    policy_note = "each benchmark's own policy"
    g.add_argument(
        "--runs",
        type=int,
        default=runs_default,
        metavar="N",
        help="Measured run count for every benchmark (default: "
        f"{runs_default if runs_default is not None else policy_note}).",
    )
    g.add_argument(
        "--warmup",
        type=int,
        default=warmup_default,
        metavar="N",
        help="Warmup runs executed but excluded from stats (default: "
        f"{warmup_default if warmup_default is not None else policy_note}).",
    )
    g.add_argument(
        "--jobs",
        "-j",
        type=int,
        default=1,
        metavar="N",
        help="Run up to N benchmarks in parallel (default: 1, sequential).",
    )
    g.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Suppress the live progress reporter (summary still prints).",
    )
    g.add_argument(
        "--dry",
        action="store_true",
        help="Print the planned executions and exit without running anything.",
    )
    g.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Echo each benchmark's full command / cwd / env / run plan "
        "before running it (and the only output under --dry -v).",
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
        help="Write a per-execution tree (stdout/stderr/exitcode/rusage) under DIR.",
    )
    g.add_argument(
        "--compare",
        action="append",
        default=None,
        metavar="JSON",
        help="Compare against a baseline JSON report (repeat to add more; "
        "first is the baseline, last is the current run).",
    )


# ---------------------------------------------------------------------------
# `benchr` CLI: bench / compare / show
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
            help="Compare JSON reports from prior runs.",
            description=(
                "Summarize one or more JSON reports and print ratios against the "
                "first one as a baseline. With a single file, just pretty-prints "
                "its summary."
            ),
        )
    )
    _show_subparser(
        sub.add_parser(
            "show",
            help="Pretty-print a JSON report.",
            description="Re-render the summary block of a previously saved JSON report.",
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
    _add_shared_flags(p, runs_default=10, warmup_default=0)
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
    p.set_defaults(_func=_run_bench)


def _run_bench(ns: argparse.Namespace) -> int:
    import shlex

    argvs = [tuple(shlex.split(cmd)) for cmd in ns.commands]
    b = (
        bench("bench")
        .with_matrix(command=argvs)
        .with_label(lambda bb: " ".join(bb.data["command"]))
        .with_cwd(Path.cwd())
        .with_metric(Time())
        .with_runs(ns.runs)
    )
    if ns.timeout is not None:
        b = b.with_timeout(ns.timeout)
    if ns.warmup > 0:
        b = b.with_warmup(ns.warmup)
    s = suite("bench", b)

    metrics = {ns.metric} if ns.metric else None
    summary_reporter = SummaryReporter(formatter=DefaultSummary(metrics=metrics))
    rep = _build_reporter(
        ns, summary_reporter, with_progress=not ns.dry and not ns.quiet
    )

    runner = _make_runner(ns, rep)
    try:
        runner.run(plan([s], None), None)
    except KeyboardInterrupt:
        console.print("[benchr.failure]Interrupted[/]")
        return 1
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
    p.set_defaults(_func=_run_compare)


def _run_compare(ns: argparse.Namespace) -> int:
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
    # First file is the baseline; rest are comparees. Summarize the *last*
    # file ("current") against the baseline, plus all intermediates as
    # additional comparees.
    current = report_from_json(files[-1].read_text())
    data = build_summary(current, files[:-1])
    out = DefaultSummary(metrics=metrics).format(data)
    if out:
        console.print(out)
    return 0


# ----- show ---------------------------------------------------------------


def _show_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("file")
    p.add_argument("--metric", type=str, default=None)
    p.set_defaults(_func=_run_show)


def _run_show(ns: argparse.Namespace) -> int:
    path = Path(ns.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1
    r = report_from_json(path.read_text())
    metrics = set(ns.metric.split(",")) if ns.metric else None
    data = build_summary(r, [])
    out = DefaultSummary(metrics=metrics).format(data)
    if out:
        console.print(out)
    return 0
