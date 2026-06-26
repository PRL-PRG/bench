"""bench CLI entry point."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from importlib.metadata import version as _pkg_version
from pathlib import Path

from bench.run import add_runtime_flags, do_run
from bench.grammar.benchmark import bench
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
from bench.report.formatter import DefaultSummary
from bench.report.reporter import SummaryReporter, console, print_diagnostics
from bench.core.sample import report_from_json
from bench.report.stats import build_summary


# ---------------------------------------------------------------------------
# `bench` CLI: run / compare
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench",
        description=(
            "bench — run, compare, and inspect command-line benchmarks. "
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
            help="Time one or more shell commands (hyperfine-style).",
            description=(
                "Time one or more shell commands. Each positional CMD is split with "
                "shlex and benchmarked as its own benchmark; results are summarized "
                "side by side. Example:\n\n"
                "    bench run --runs 20 --warmup 2 'sleep 0.1' 'sleep 0.2'\n\n"
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
    _doctor_subparser(
        sub.add_parser(
            "doctor",
            help="Inspect the machine for benchmarking noise sources.",
            description=(
                "Print the environment snapshot and the noise checks. "
                "Exits non-zero if any high-severity issue is found, "
                "so it can gate a benchmarking session in CI."
            ),
        )
    )
    _denoise_subparser(
        sub.add_parser(
            "denoise",
            help="Minimize/restore system noise knobs (Linux + root).",
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
        default=3.0,
        metavar="SECONDS",
        help="Stop measuring after SECONDS of command runtime, or after --runs, whichever comes first. 0 disables (default: %(default)s).",
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
        "--no-check",
        action="store_true",
        help="Skip the environment snapshot and noise checks.",
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

    b = (
        bench("run")
        .with_matrix(command=argvs)
        .with_command(lambda ctx: list(ctx.matrix.command))
        .with_label(lambda b: " ".join(b.data["command"]))
        .with_cwd(Path.cwd())
        .with_process_metric(Time())
        .with_runs(runs_policy)
    )

    if ns.timeout is not None:
        b = b.with_timeout(ns.timeout)
    if ns.warmup > 0:
        b = b.with_warmup(ns.warmup)
    s = suite("run", b)

    metrics = {ns.metric} if ns.metric else None
    reporter = SummaryReporter(formatter=DefaultSummary(metrics=metrics))

    environment = NoEnvironment() if ns.no_check else SystemEnvironment()
    do_run([s], ns, reporter, None, environment=environment, denoise=ns.denoise)
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
        # No comparison, just summarize.
        r = report_from_json(files[0].read_text())
        data = build_summary(r, [])
        out = DefaultSummary(metrics=metrics).format(data)
        if out:
            console.print(out)
        return 0
    # First file is the baseline; all others are comparees.
    data = build_summary(None, files)
    out = DefaultSummary(metrics=metrics).format(data)
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
