"""bench CLI entry point."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from dataclasses import field
from importlib.metadata import version as _pkg_version
from pathlib import Path

from bench.builder import Context, bench, bench_app, suite
from bench.builder.app import bench_app_params_type, show_report
from bench.console.theme import console, error_console
from bench.core.denoise import (
    DENOISE_DEFAULT_STATE_PATH,
    Denoise,
    is_root,
)
from bench.core.diagnostic import print_diagnostics, run_checks
from bench.core.fingerprint import NoProbe, SystemProbe
from bench.core.metric import Time
from bench.core.policy import FixedRuns, MaxDuration
from bench.core.stats import merge_reports, summarize
from bench.error import BenchError, print_exception
from bench.model.benchmark import Benchmark
from bench.model.results import Report, report_from_json
from bench.params import (
    SHOW_DESCRIPTION,
    SHOW_HELP,
    Params,
    ParamsGroup,
    SharedReporterParams,
    SharedRunnerParams,
    ShowParams,
    add_params,
    build_params,
)
from bench.summary import (
    ByBenchmarkMetricSummary,
    ComparisonSummary,
    DefaultSummary,
)

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

    _subcommand(
        sub.add_parser(
            "run",
            help="Benchmark one or more shell commands.",
            description=(
                "Benchmark one or more shell commands. It creates a single "
                "benchmark with each CMD acting as one variant. The "
                "results are thus compared and summarized."
            ),
            formatter_class=argparse.RawDescriptionHelpFormatter,
        ),
        RunAppParams,
        _cmd_run,
    )
    _subcommand(
        sub.add_parser(
            "show",
            help=SHOW_HELP,
            description=SHOW_DESCRIPTION,
        ),
        ShowParams,
        _cmd_show,
    )
    _subcommand(
        sub.add_parser(
            "compare",
            help="Compare several JSON reports side by side.",
            description=(
                "Merge the reports into a synthetic `compare` matrix axis (one "
                "value per file) and summarize it. The first file is the "
                "baseline reference."
            ),
        ),
        CompareParams,
        _cmd_compare,
    )
    _subcommand(
        sub.add_parser(
            "doctor",
            help="Inspect the machine for benchmarking noise sources.",
            description=(
                "Print the machine fingerprint and the noise checks. "
                "Exits non-zero if any high-severity issue is found."
            ),
        ),
        DoctorParams,
        _cmd_doctor,
    )
    _subcommand(
        sub.add_parser(
            "denoise",
            help="Minimize/restore system noise knobs (requres linux with root access).",
            description=(
                "Set the CPU governor to performance, disable turbo, and quiet "
                "perf/swap/ASLR, saving the originals so `restore` can revert "
                "them (even after a crash). `status` only reports current values. "
                "minimize/restore require root."
            ),
        ),
        DenoiseParams,
        _cmd_denoise,
    )

    ns = parser.parse_args(argv)
    try:
        return ns._func(ns)
    except BenchError as e:
        print_exception(e, with_traceback=False)
        return e.exit_code
    except KeyboardInterrupt:
        error_console.print("[bench.failure]Interrupted[/]")
        return 130


def _subcommand(
    p: argparse.ArgumentParser,
    params: type[Params],
    func: Callable[[argparse.Namespace], int],
) -> None:
    """Wire a subparser to its params class and its `_cmd_*` implementation."""
    add_params(p, params)
    p.set_defaults(_func=func)


# ----- run ----------------------------------------------------------------

EXECUTION_GROUP = ParamsGroup("control execution")
MATRIX_GROUP = ParamsGroup("matrix")
INSTRUMENT_GROUP = ParamsGroup("instrument")


class RunParams(SharedRunnerParams, SharedReporterParams):
    """`bench run` params: the shared runtime and reporter flags plus the
    ad-hoc benchmark description. No selection flags - `bench run` benchmarks
    exactly the commands it is handed."""

    commands: list[str] = field(
        metadata={
            "positional": True,
            "metavar": "CMD",
            "help": "One or more shell commands to benchmark.",
        }
    )

    runs: int = field(
        default=10,
        metadata={
            "group": EXECUTION_GROUP,
            "metavar": "N",
            "help": "Max measured runs per command.",
        },
    )

    time: float = field(
        default=0.0,
        metadata={
            "group": EXECUTION_GROUP,
            "metavar": "SECONDS",
            "help": "Also stop after SECONDS of cumulative command runtime "
            "(whichever comes first with --runs). 0 disables.",
        },
    )

    warmup: int = field(
        default=0,
        metadata={
            "group": EXECUTION_GROUP,
            "metavar": "N",
            "help": "Warmup runs executed but excluded from stats.",
        },
    )

    timeout: float | None = field(
        default=None,
        metadata={
            "group": EXECUTION_GROUP,
            "metavar": "SECONDS",
            "help": "Kill a run that takes longer than SECONDS.",
        },
    )

    metric: str = field(
        default="elapsed",
        metadata={
            "group": EXECUTION_GROUP,
            "metavar": "NAME",
            "help": "Metric to highlight in the comparison summary.",
        },
    )

    matrix: list[list[str]] | None = field(
        default=None,
        metadata={
            "group": MATRIX_GROUP,
            "flags": ("-M",),
            "action": "append",
            "nargs": 2,
            "type": str,
            "metavar": ("NAME", "VALUES"),
            "help": "Add a matrix dimension NAME with comma-separated VALUES; "
            "reference values as {NAME} in the command. "
            "Repeatable. Place before the command.",
        },
    )

    check_environment: bool = field(
        default=False,
        metadata={
            "group": INSTRUMENT_GROUP,
            "action": "store_true",
            "help": "Record the machine fingerprint and run the noise checks.",
        },
    )

    denoise: bool = field(
        default=False,
        metadata={
            "group": INSTRUMENT_GROUP,
            "action": "store_true",
            "help": "Minimize system noise (governor, turbo, ...) for the run, "
            "then restore it. Linux + root.",
        },
    )


RunAppParams = bench_app_params_type(RunParams)


def _cmd_run(ns: argparse.Namespace) -> int:
    import shlex

    params = build_params(ns, RunAppParams)

    argvs = [tuple(shlex.split(cmd)) for cmd in params.commands]
    runs_policy = FixedRuns(params.runs)
    if params.time > 0:
        runs_policy |= MaxDuration(params.time)

    matrix_args: list[list[str]] = params.matrix or []
    matrix_dims = {name: tuple(values.split(",")) for name, values in matrix_args}
    names = list(matrix_dims)

    def cmd(ctx: Context[Params]) -> list[str]:
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
        .with_metric(Time())
        .with_runs(runs_policy)
    )

    if params.timeout is not None:
        b = b.with_timeout(params.timeout)
    if params.warmup > 0:
        b = b.with_warmup(params.warmup)
    if matrix_dims:
        b = b.with_matrix(**matrix_dims)

    s = suite("run", b)

    metrics = [params.metric] if params.metric else None
    probe = SystemProbe() if params.check_environment else NoProbe()

    summary = DefaultSummary()
    if metrics is not None:
        summary = summary.on_metrics(metrics)

    return (
        bench_app(
            "bench",
            params=RunParams,
            probe=probe,
            denoise=params.denoise,
            summary=summary,
        )
        .add(s)
        .main(params)
    )


# ----- show ----------------------------------------------------------------


def _cmd_show(ns: argparse.Namespace) -> int:
    params = build_params(ns, ShowParams)
    show_report(params, DefaultSummary())
    return 0


# ----- compare ------------------------------------------------------------


class CompareParams(Params):
    files: list[str] = field(
        metadata={
            "positional": True,
            "metavar": "FILE",
            "help": "The JSON reports to compare; the first one is the baseline.",
        }
    )

    metric: str | None = field(
        default=None,
        metadata={
            "help": "Comma-separated metric filter (e.g. elapsed,max_rss).",
        },
    )


# TODO: Probably move to app
def _cmd_compare(ns: argparse.Namespace) -> int:
    params = build_params(ns, CompareParams)

    # Name each report by the path as given (e.g. `a.json`) and fold them into
    # one report tagged by a synthetic `compare` axis, then reuse the ordinary
    # views over it - the first file is the baseline.
    named: list[tuple[str, Report]] = []
    for arg in params.files:
        path = Path(arg)
        if not path.exists():
            raise BenchError(f"File not found: {path}")
        named.append((arg, report_from_json(path.read_text())))

    stats = summarize(merge_reports(named))

    # Per-benchmark a-vs-b: fold each benchmark's inner matrix and compare the
    # files. The first file is the baseline reference.
    formatter = ByBenchmarkMetricSummary() & ComparisonSummary(
        axis="compare", ref=named[0][0]
    )
    if params.metric:
        formatter = formatter.on_metrics(params.metric.split(","))

    out = formatter(stats)
    if out.renderables:
        console.print(out)
    return 0


# ----- doctor --------------------------------------------------------------


class DoctorParams(Params):
    json: bool = field(
        default=False,
        metadata={
            "action": "store_true",
            "help": "Print the machine fingerprint as JSON instead of a report.",
        },
    )


def _cmd_doctor(ns: argparse.Namespace) -> int:
    params = build_params(ns, DoctorParams)

    fingerprint = SystemProbe().collect()
    if fingerprint is None:
        console.print("No fingerprint information available.")
        return 0
    diagnostics = run_checks(fingerprint)
    exit_code = 1 if any(d.severity == "high" for d in diagnostics) else 0

    if params.json:
        json.dump(fingerprint.data, sys.stdout, indent=2)
        return exit_code

    console.print("[bench.label]Fingerprint:[/]")
    for name, value in fingerprint.items():
        console.print(f"  {name}: {value}")
    if diagnostics:
        print_diagnostics(diagnostics, "Checks")
    else:
        console.print("\n[bench.success]No noise sources detected.[/]")
    return exit_code


# ----- denoise -------------------------------------------------------------


class DenoiseParams(Params):
    action: str = field(
        metadata={
            "positional": True,
            "choices": ("minimize", "restore", "status"),
            "help": "minimize: quiet the knobs; restore: revert; status: show current.",
        }
    )

    path: Path = field(
        default=DENOISE_DEFAULT_STATE_PATH,
        metadata={
            "help": "Where to save/load the state",
        },
    )


def _cmd_denoise(ns: argparse.Namespace) -> int:
    params = build_params(ns, DenoiseParams)

    if params.action in ("minimize", "restore") and not is_root():
        raise BenchError(
            f"denoise {params.action} requires root "
            f"(try: sudo bench denoise {params.action})",
            exit_code=2,
        )

    denoise = Denoise(state_path=params.path)
    if params.action == "minimize":
        applied = denoise.minimize()
        console.print(
            f"Minimized {len(applied)} setting(s); state saved to {denoise.state_path}."
        )
    elif params.action == "restore":
        restored = denoise.restore()
        console.print(f"Restored {len(restored)} setting(s) from {denoise.state_path}.")
    else:
        snapshot = denoise.status()
        if not snapshot:
            console.print("No controllable knobs on this platform.")
        for path, value in snapshot.items():
            console.print(f"  {path}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
