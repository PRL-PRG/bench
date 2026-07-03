"""bench CLI entry point."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from bench.run import add_runtime_flags, bench_app, default_reporter
from bench.grammar.benchmark import Benchmark, bench
from bench.grammar.context import Context
from bench.core.checks import run_checks
from bench.core.environment import NoEnvironment, SystemEnvironment
from bench.core.metric import Time
from bench.core.policy import FixedRuns, MaxDuration
from bench.denoise import (
    STATE_PATH,
    is_root,
    minimize,
    restore,
    status,
)
from bench.grammar.suite import suite
from bench.core.sample import Report, report_from_json
from bench.report.formatter import (
    DefaultSummary,
    Results,
    Summary,
)
from bench.report.reporter import SummaryReporter, console, print_diagnostics
from bench.report.summary import merge_reports, summarize


# ---------------------------------------------------------------------------
# `bench` CLI: run / compare
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench",
        description=(
            "bench - run, compare, and inspect command-line benchmarks. "
            "See `bench <sub> --help` for the detailed flag set of each "
            "subcommand."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_pkg_version('bench')}",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    _run_subparser(
        sub.add_parser(
            "run",
            help="Benchmark one or more shell commands.",
            description=(
                "Benchmark one or more shell commands. It creates a single "
                "benchmark with each CMD acting as one variant. The "
                "results are thus compared and summarized."
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
    )
    _show_subparser(
        sub.add_parser(
            "show",
            help="Summarize a single JSON report from a prior run.",
            description=("Load a saved JSON report and print its default summary."),
        )
    )
    _compare_subparser(
        sub.add_parser(
            "compare",
            help="Compare several JSON reports side by side.",
            description=(
                "Merge the reports into a synthetic `compare` matrix axis (one "
                "value per file) and summarize it. The first file is the "
                "baseline reference."
            ),
        )
    )
    _doctor_subparser(
        sub.add_parser(
            "doctor",
            help="Inspect the machine for benchmarking noise sources.",
            description=(
                "Print the environment snapshot and the noise checks. "
                "Exits non-zero if any high-severity issue is found."
            ),
        )
    )
    _denoise_subparser(
        sub.add_parser(
            "denoise",
            help="Minimize/restore system noise knobs (requres linux with root access).",
            description=(
                "Set the CPU governor to performance, disable turbo, and quiet "
                "perf/swap/ASLR, saving the originals so `restore` can revert "
                "them (even after a crash). `status` only reports current values. "
                "minimize/restore require root."
            ),
        )
    )

    ns = parser.parse_args(argv)
    return ns._func(ns)


# ----- run ----------------------------------------------------------------


def _run_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "commands",
        nargs="+",
        metavar="CMD",
        help="One or more shell commands to benchmark.",
    )
    add_runtime_flags(p)
    p.add_argument(
        "--runs",
        type=int,
        default=10,
        metavar="N",
        help="Max measured runs per command (default: %(default)s).",
    )
    p.add_argument(
        "--time",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Also stop after SECONDS of cumulative command runtime (whichever comes first with --runs). 0 disables, the default (default: %(default)s).",
    )
    p.add_argument(
        "--warmup",
        type=int,
        default=0,
        metavar="N",
        help="Warmup runs executed but excluded from stats (default: %(default)s).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Kill a run that takes longer than SECONDS (default: %(default)s).",
    )
    p.add_argument(
        "--metric",
        type=str,
        default="elapsed",
        metavar="NAME",
        help="Metric to highlight in the comparison summary (default: %(default)s).",
    )
    p.add_argument(
        "-M",
        metavar=("NAME", "VALUES"),
        nargs=2,
        action="append",
        dest="matrix",
        default=None,
        help="Add a matrix dimension NAME with comma-separated VALUES; "
        "reference values as {NAME} in the command. "
        "Repeatable. Place before the command.",
    )
    p.add_argument(
        "--check-environment",
        action="store_true",
        help="Record the environment snapshot and run the noise checks "
        "(off by default).",
    )
    p.add_argument(
        "--denoise",
        action="store_true",
        help="Minimize system noise (governor, turbo, ...) for the run, then "
        "restore it. Linux + root.",
    )
    p.set_defaults(_func=_cmd_run)


def _cmd_run(ns: argparse.Namespace) -> int:
    import shlex

    argvs = [tuple(shlex.split(cmd)) for cmd in ns.commands]
    runs_policy = FixedRuns(ns.runs)
    if ns.time and ns.time > 0:
        runs_policy |= MaxDuration(ns.time)

    matrix_args: list[list[str]] = ns.matrix or []
    matrix_dims = {name: tuple(values.split(",")) for name, values in matrix_args}
    names = list(matrix_dims)

    def cmd(ctx: Context[Any]) -> list[str]:
        argv = list(ctx.data.command)
        if not names:
            return argv
        subst = {n: getattr(ctx.data, n) for n in names}
        return [tok.format(**subst) for tok in argv]

    def label(bm: Benchmark) -> str:
        argv = list(bm.data["command"])
        if not names:
            return " ".join(argv)
        subst = {n: bm.data[n] for n in names}
        return " ".join(tok.format(**subst) for tok in argv)

    b = (
        bench("run")
        .with_matrix(command=argvs)
        .with_command(cmd)
        .with_label(label)
        .with_cwd(Path.cwd())
        .with_process_metric(Time())
        .with_runs(runs_policy)
    )

    if ns.timeout is not None:
        b = b.with_timeout(ns.timeout)
    if ns.warmup > 0:
        b = b.with_warmup(ns.warmup)
    if matrix_dims:
        b = b.add_matrix(**matrix_dims)

    s = suite("run", b)

    metrics = {ns.metric} if ns.metric else None
    reporter = SummaryReporter(DefaultSummary(metrics=metrics))
    environment = SystemEnvironment() if ns.check_environment else NoEnvironment()

    app = (
        bench_app("bench", environment=environment, denoise=ns.denoise)
        .add(s)
        .with_reporter(lambda ctx: default_reporter(ctx, summary=reporter))
    )
    app.run(cli_args=ns)
    return 0


# ----- show ----------------------------------------------------------------


def _show_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("file", help="A JSON report to summarize.")
    p.add_argument(
        "--metric",
        type=str,
        default=None,
        help="Comma-separated metric filter (e.g. elapsed,max_rss).",
    )
    p.set_defaults(_func=_cmd_show)


def _cmd_show(ns: argparse.Namespace) -> int:
    path = Path(ns.file)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        return 1
    metrics = set(ns.metric.split(",")) if ns.metric else None
    stats = summarize(report_from_json(path.read_text()))
    out = DefaultSummary(metrics)(stats)
    if out:
        console.print(out)
    return 0


# ----- compare ------------------------------------------------------------


def _compare_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument("files", nargs="+")
    p.add_argument(
        "--metric",
        type=str,
        default=None,
        help="Comma-separated metric filter (e.g. elapsed,max_rss).",
    )
    p.set_defaults(_func=_cmd_compare)


def _cmd_compare(ns: argparse.Namespace) -> int:
    metrics = set(ns.metric.split(",")) if ns.metric else None
    # Name each report by the path as given (e.g. `a.json`) and fold them into
    # one report tagged by a synthetic `compare` axis, then reuse the ordinary
    # views over it — the first file is the baseline.
    named: list[tuple[str, Report]] = []
    for arg in ns.files:
        path = Path(arg)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            return 1
        named.append((arg, report_from_json(path.read_text())))
    stats = summarize(merge_reports(named))
    # Per-benchmark a-vs-b: fold each benchmark's inner matrix and compare the
    # files. The first file is the baseline reference.
    formatter = Results(metrics) & Summary(metrics, axis="compare", ref=named[0][0])
    out = formatter(stats)
    if out:
        console.print(out)
    return 0


# ----- doctor --------------------------------------------------------------


def _doctor_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--json",
        action="store_true",
        help="Print the environment snapshot as JSON instead of a report.",
    )
    p.set_defaults(_func=_cmd_doctor)


def _cmd_doctor(ns: argparse.Namespace) -> int:
    env = SystemEnvironment().collect()
    if env is None:
        console.print("No environment information available.")
        return 0
    diagnostics = run_checks(env)
    exit_code = 1 if any(d.severity == "high" for d in diagnostics) else 0

    if ns.json:
        print(json.dumps(dataclasses.asdict(env), indent=2))
        return exit_code

    console.print("[bench.label]Environment:[/]")
    for name, value in env.display_items():
        console.print(f"  {name}: {value}")
    if diagnostics:
        print_diagnostics(diagnostics, "Checks")
    else:
        console.print("\n[bench.success]No noise sources detected.[/]")
    return exit_code


# ----- denoise -------------------------------------------------------------


def _denoise_subparser(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "action",
        choices=("minimize", "restore", "status"),
        help="minimize: quiet the knobs; restore: revert; status: show current.",
    )
    p.set_defaults(_func=_cmd_denoise)


def _cmd_denoise(ns: argparse.Namespace) -> int:
    if ns.action in ("minimize", "restore") and not is_root():
        console.print(
            f"[bench.failure]denoise {ns.action} requires root "
            f"(try: sudo bench denoise {ns.action})[/]"
        )
        return 2
    if ns.action == "minimize":
        applied = minimize()
        console.print(
            f"Minimized {len(applied)} setting(s); state saved to {STATE_PATH}."
        )
    elif ns.action == "restore":
        restored = restore()
        console.print(f"Restored {len(restored)} setting(s) from {STATE_PATH}.")
    else:
        snapshot = status()
        if not snapshot:
            console.print("No controllable knobs on this platform.")
        for path, value in snapshot.items():
            console.print(f"  {path}: {value}")
    return 0
