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
    Reporter,
    Summary as SummaryReporter,
    console,
)
from benchr.report.sample import Report, report_from_json
from benchr.report.stats import build_summary
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

    Returns a Report containing every Sample emitted, for callers that want
    to do follow-up analysis after the side-effecting reporters have run.
    """
    if isinstance(suites, Suite):
        suites = [suites]

    parser = _make_run_parser(params)
    ns = parser.parse_args(argv)
    ctx = build_dataclass(params, ns) if params is not None else None

    sinks: list[Reporter] = []
    summary_reporter = (
        reporter
        if reporter is not None
        else SummaryReporter(formatter=formatter)
    )
    sinks.append(summary_reporter)
    if ns.json:
        sinks.append(JsonReporter(Path(ns.json)))
    if ns.csv:
        sinks.append(CsvReporter(Path(ns.csv)))
    if ns.dir:
        sinks.append(DirReporter(Path(ns.dir)))

    final_reporter: Reporter = sinks[0] if len(sinks) == 1 else Mixed(*sinks)

    if ns.compare and isinstance(summary_reporter, SummaryReporter):
        summary_reporter.set_baseline([Path(p) for p in ns.compare])

    # Optionally override warmup/measure across all benchmarks.
    if ns.runs is not None:
        suites = [s.with_runs(int(ns.runs)) for s in suites]
    if ns.warmup is not None:
        suites = [s.with_warmup(int(ns.warmup)) for s in suites]

    runner_cls = Dry if ns.dry else (
        (lambda **kw: Parallel(workers=ns.jobs, **kw)) if ns.jobs > 1 else Sequential
    )
    runner = runner_cls(reporter=final_reporter)
    try:
        samples = runner.run(suites, ctx)
    except KeyboardInterrupt:
        console.print("[benchr.failure]Interrupted[/]")
        sys.exit(1)
    return Report(samples=list(samples))


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
    g.add_argument("--runs", type=int, default=None, help="Override measure runs for all suites")
    g.add_argument("--warmup", type=int, default=None, help="Override warmup runs for all suites")
    g.add_argument("--jobs", "-j", type=int, default=1, help="(default: 1)")
    g.add_argument("--dry", action="store_true", help="Print plan; don't execute")
    g.add_argument("--json", type=str, default=None, metavar="FILE", help="Write JSON report")
    g.add_argument("--csv", type=str, default=None, metavar="FILE", help="Write CSV report")
    g.add_argument("--dir", type=str, default=None, metavar="DIR", help="Per-execution tree")
    g.add_argument("--compare", action="append", default=None, metavar="JSON",
                   help="Compare against baseline JSON; repeat to add more (first is baseline)")


# ---------------------------------------------------------------------------
# `benchr` CLI: bench / compare / show
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchr", description="benchmark runner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    _bench_subparser(sub.add_parser("bench", help="Run commands hyperfine-style"))
    _compare_subparser(sub.add_parser("compare", help="Compare JSON reports"))
    _show_subparser(sub.add_parser("show", help="Pretty-print a JSON report"))

    ns = parser.parse_args(argv)
    return ns._func(ns)


# ----- bench --------------------------------------------------------------


def _bench_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("commands", nargs="+", help="Shell commands to benchmark")
    p.add_argument("--runs", type=int, default=10)
    p.add_argument("--warmup", type=int, default=0)
    p.add_argument("--timeout", type=float, default=None)
    p.add_argument("--jobs", "-j", type=int, default=1)
    p.add_argument("--json", type=str, default=None, metavar="FILE")
    p.add_argument("--csv", type=str, default=None, metavar="FILE")
    p.add_argument("--dir", type=str, default=None, metavar="DIR")
    p.add_argument("--compare", action="append", default=None, metavar="JSON",
                   help="Compare against baseline JSON; repeat to add more (first is baseline)")
    p.add_argument("--metric", type=str, default="elapsed")
    p.set_defaults(_func=_run_bench)


def _run_bench(ns: argparse.Namespace) -> int:
    import shlex

    benchmarks = []
    for cmd in ns.commands:
        argv = shlex.split(cmd)
        b = (
            bench(cmd)
            .with_command(argv)
            .with_cwd(Path.cwd())
            .with_process(P.time())
        )
        if ns.timeout is not None:
            b = b.with_timeout(ns.timeout)
        if ns.warmup > 0:
            b = b.with_warmup(ns.warmup)
        b = b.runs(ns.runs)
        benchmarks.append(b)
    s = suite("bench", *benchmarks)

    summary_reporter = SummaryReporter()
    sinks: list[Reporter] = [summary_reporter]
    if ns.json:
        sinks.append(JsonReporter(Path(ns.json)))
    if ns.csv:
        sinks.append(CsvReporter(Path(ns.csv)))
    if ns.dir:
        sinks.append(DirReporter(Path(ns.dir)))
    rep: Reporter = sinks[0] if len(sinks) == 1 else Mixed(*sinks)

    if ns.compare:
        summary_reporter.set_baseline([Path(p) for p in ns.compare])

    runner = Parallel(workers=ns.jobs, reporter=rep) if ns.jobs > 1 else Sequential(reporter=rep)
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
    # First file is the baseline; rest are comparees.
    baseline = files[0]
    # We summarize the *last* file ("current") against the baseline, plus all
    # intermediates as additional comparees.
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
