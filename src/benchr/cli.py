"""benchr CLI: ``bench``, ``run`` (programmatic), ``compare``, ``show``.

The ``run(...)`` function in this module is the main entry point used by
benchmark scripts. ``benchr`` (the CLI) covers the hyperfine-style
``benchr bench``, plus ``benchr compare`` and ``benchr show`` for inspecting
JSON outputs offline.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from benchr.grammar.benchmark import bench
from benchr.grammar.context import add_dataclass_args, build_dataclass
from benchr.grammar.processor import P
from benchr.grammar.suite import Suite, suite
from benchr.report.formatter import DefaultSummary, Formatter
from benchr.report.reporter import (
    Csv as CsvReporter,
    Dir as DirReporter,
    Json as JsonReporter,
    Mixed,
    Progress as ProgressReporter,
    Reporter,
    Summary as SummaryReporter,
    console,
)
from benchr.report.sample import Report, report_from_json
from benchr.report.stats import build_summary
from benchr.runner.base import Runner
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
    (--json/--csv/--dir) are *additional* Mixed sinks alongside.
    ``formatter`` overrides the default summary formatter (used for the
    terminal summary; defaults to ``DefaultSummary``).

    Returns the Report the runner accumulated — every Sample plus a RunRecord
    per execution (so ``report.failures`` is populated) — for callers that want
    to do follow-up analysis after the side-effecting reporters have run.
    """
    if isinstance(suites, Suite):
        suites = [suites]

    parser = _make_run_parser(params)
    ns = parser.parse_args(argv)
    ctx = build_dataclass(params, ns) if params is not None else None

    summary_reporter = (
        reporter if reporter is not None else SummaryReporter(formatter=formatter)
    )
    final_reporter = _assemble(
        ns, summary_reporter, with_progress=not ns.dry and not ns.quiet
    )

    # Optionally override warmup/measure across all benchmarks (unconditional:
    # these flags mean "for every benchmark", overriding per-benchmark values).
    if ns.runs is not None:
        suites = [s.with_runs(int(ns.runs), force=True) for s in suites]
    if ns.warmup is not None:
        suites = [s.with_warmup(int(ns.warmup), force=True) for s in suites]

    runner = _make_runner(ns, final_reporter)
    try:
        return runner.run(suites, ctx)
    except KeyboardInterrupt:
        console.print("[benchr.failure]Interrupted[/]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Shared reporter / runner assembly (used by both run() and `benchr bench`)
# ---------------------------------------------------------------------------


def _assemble(
    ns: argparse.Namespace,
    summary_reporter: Reporter,
    *,
    with_progress: bool,
) -> Reporter:
    """Build the live reporter: optional progress, the summary reporter, plus
    any --json/--csv/--dir sinks, fanned out via Mixed. Wires --compare onto
    the summary reporter when it is a SummaryReporter."""
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
    return sinks[0] if len(sinks) == 1 else Mixed(*sinks)


def _make_runner(ns: argparse.Namespace, reporter: Reporter) -> Runner:
    verbose = getattr(ns, "verbose", False)
    if getattr(ns, "dry", False):
        return Dry(verbose=verbose)  # prints the plan only; ignores reporters / sinks
    if ns.jobs > 1:
        return Parallel(workers=ns.jobs, reporter=reporter, verbose=verbose)
    return Sequential(reporter=reporter, verbose=verbose)


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
    g = parser.add_argument_group("benchr flags")
    g.add_argument("--runs", type=int, default=None, metavar="N",
                   help="Override the measure-phase run count for every benchmark.")
    g.add_argument("--warmup", type=int, default=None, metavar="N",
                   help="Override the warmup-phase run count for every benchmark.")
    g.add_argument("--jobs", "-j", type=int, default=1, metavar="N",
                   help="Run up to N benchmarks in parallel (default: 1, sequential).")
    g.add_argument("--quiet", "-q", action="store_true",
                   help="Suppress the live progress reporter (summary still prints).")
    g.add_argument("--dry", action="store_true",
                   help="Print the planned executions and exit without running anything.")
    g.add_argument("--verbose", "-v", action="store_true",
                   help="Echo each benchmark's full command / cwd / env / run plan "
                        "before running it (and the only output under --dry -v).")
    g.add_argument("--json", type=str, default=None, metavar="FILE",
                   help="Write a JSON report of every sample to FILE.")
    g.add_argument("--csv", type=str, default=None, metavar="FILE",
                   help="Write a CSV report of every sample to FILE.")
    g.add_argument("--dir", type=str, default=None, metavar="DIR",
                   help="Write a per-execution tree (stdout/stderr/exitcode/rusage) under DIR.")
    g.add_argument("--compare", action="append", default=None, metavar="JSON",
                   help="Compare against a baseline JSON report (repeat to add more; "
                        "first is the baseline, last is the current run).")


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

    _bench_subparser(sub.add_parser(
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
    ))
    _compare_subparser(sub.add_parser(
        "compare",
        help="Compare JSON reports from prior runs.",
        description=(
            "Summarize one or more JSON reports and print ratios against the "
            "first one as a baseline. With a single file, just pretty-prints "
            "its summary."
        ),
    ))
    _show_subparser(sub.add_parser(
        "show",
        help="Pretty-print a JSON report.",
        description="Re-render the summary block of a previously saved JSON report.",
    ))

    ns = parser.parse_args(argv)
    return ns._func(ns)


# ----- bench --------------------------------------------------------------


def _bench_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("commands", nargs="+", metavar="CMD",
                   help="One or more shell commands to benchmark (each split with shlex).")
    p.add_argument("--runs", type=int, default=10, metavar="N",
                   help="Number of measured runs per command (default: 10).")
    p.add_argument("--warmup", type=int, default=0, metavar="N",
                   help="Number of warmup runs executed but excluded from stats (default: 0).")
    p.add_argument("--timeout", type=float, default=None, metavar="SECONDS",
                   help="Kill a run that takes longer than SECONDS (treated as a failure).")
    p.add_argument("--jobs", "-j", type=int, default=1, metavar="N",
                   help="Run up to N benchmarks in parallel (default: 1, sequential).")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="Suppress the live progress reporter (summary still prints).")
    p.add_argument("--dry", action="store_true",
                   help="Print the planned executions and exit without running anything.")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Echo each benchmark's full command / cwd / env / run plan "
                        "before running it (and the only output under --dry -v).")
    p.add_argument("--json", type=str, default=None, metavar="FILE",
                   help="Write a JSON report of every sample to FILE.")
    p.add_argument("--csv", type=str, default=None, metavar="FILE",
                   help="Write a CSV report of every sample to FILE.")
    p.add_argument("--dir", type=str, default=None, metavar="DIR",
                   help="Write a per-execution tree (stdout/stderr/exitcode/rusage) under DIR.")
    p.add_argument("--compare", action="append", default=None, metavar="JSON",
                   help="Compare against a baseline JSON report (repeat to add more; "
                        "first is the baseline, last is the current run).")
    p.add_argument("--metric", type=str, default="elapsed", metavar="NAME",
                   help="Metric to highlight in the comparison summary (default: elapsed).")
    p.set_defaults(_func=_run_bench)


def _run_bench(ns: argparse.Namespace) -> int:
    import shlex

    argvs = [tuple(shlex.split(cmd)) for cmd in ns.commands]
    b = (
        bench("bench")
        .with_matrix(command=argvs)
        .with_label(lambda bb: " ".join(bb.data["command"]))
        .with_cwd(Path.cwd())
        .with_process(P.time())
        .runs(ns.runs)
    )
    if ns.timeout is not None:
        b = b.with_timeout(ns.timeout)
    if ns.warmup > 0:
        b = b.with_warmup(ns.warmup)
    s = suite("bench", b)

    metrics = {ns.metric} if ns.metric else None
    summary_reporter = SummaryReporter(formatter=DefaultSummary(metrics=metrics))
    rep = _assemble(ns, summary_reporter, with_progress=not ns.dry and not ns.quiet)

    runner = _make_runner(ns, rep)
    try:
        runner.run([s], ctx=None)
    except KeyboardInterrupt:
        console.print("[benchr.failure]Interrupted[/]")
        return 1
    return 0


# ----- compare ------------------------------------------------------------


def _compare_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+")
    p.add_argument("--metric", type=str, default=None,
                   help="Comma-separated metric filter (e.g. runtime,max_rss)")
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
