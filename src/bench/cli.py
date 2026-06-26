"""bench CLI entry point."""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.markup import escape as markup_escape
from rich.text import Text
from rich.tree import Tree

from bench.grammar.benchmark import Benchmark, bench
from bench.grammar.context import add_dataclass_args, build_dataclass
from bench.core.checks import run_checks
from bench.core.environment import (
    Diagnostic,
    Environment,
    EnvironmentCollector,
    NoEnvironment,
    SystemEnvironment,
)
from bench.core.execution import record_key
from bench.core.metric import Time
from bench.core.policy import FixedRuns, MaxDuration
from bench.denoise import (
    STATE_PATH,
    denoise_session,
    is_root,
    minimize,
    restore,
    status,
)
from bench.grammar.suite import SuiteBuilder, suite
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
from bench.utils import print_exception
from bench.core.sample import Report, report_from_json
from bench.report.stats import build_summary
from bench.runner.base import (
    Runner,
    SuiteMaterializationError,
    plan,
    select,
)
from bench.runner.dry import Dry
from bench.runner.parallel import Parallel
from bench.runner.sequential import Sequential


type SuiteFactory = Callable[[Any], list[SuiteBuilder]]


@dataclasses.dataclass(frozen=True, slots=True)
class Bench:
    """Top-level run container: static suites + deferred suite factories."""

    suites: tuple[SuiteBuilder, ...] = ()
    factories: tuple[SuiteFactory, ...] = ()
    params: type | None = None
    reporter: Reporter | None = None
    environment: EnvironmentCollector = SystemEnvironment()
    denoise: bool = False

    def add_suite(self, s: SuiteBuilder) -> Bench:
        """Register a suite."""
        return dataclasses.replace(self, suites=self.suites + (s,))

    def add_suites(self, *ss: SuiteBuilder) -> Bench:
        """Register several suites."""
        return dataclasses.replace(self, suites=self.suites + ss)

    def factory(self, fn: SuiteFactory) -> Bench:
        """Register a deferred `(params) -> [SuiteBuilder]` producer.

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
            collected.extend(produced)

        return _do_run(
            collected,
            cli_args,
            self.reporter,
            build_params,
            environment=self.environment,
            denoise=self.denoise,
        )


def run(
    suites: SuiteBuilder | list[SuiteBuilder] | SuiteFactory,
    *,
    params: type | None = None,
    reporter: Reporter | None = None,
    argv: list[str] | None = None,
    environment: EnvironmentCollector | None = None,
    denoise: bool = False,
) -> Report:
    """
    The entrypoint for benchmarking.
    Parse argv, build the ctx, run the benchmark, emit reports.

    Args:
        suites: The suite(s) to run. Either a `SuiteBuilder`, a list of `SuiteBuilder`, or a
            deferred `(params) -> [SuiteBuilder]` producer that is called after
            CLI parsing (for suite discovery that depends on `params`). To
            combine static and discovered suites, build a `Bench` directly.
        params: The user's @dataclass that declares additional CLI flags and forms the user-defined context. Defaults to `None` if omitted.
        reporter: The reporter to be used for process the result. Defaults to `SummaryReporter`
        argv: The command-line parameters that will be parsed and use to fill the user-defined context.
        environment: Strategy collecting the machine snapshot recorded with the
            report and driving the checks.
        denoise: When True, minimize system noise for the run and restore it afterward.
            Requires root on Linux.

    Returns:
        The report of running all the benchmarks
    """
    app = Bench(
        params=params,
        reporter=reporter,
        environment=environment or SystemEnvironment(),
        denoise=denoise,
    )
    if callable(suites):  # a SuiteBuilder / list is never callable
        app = app.factory(suites)
    elif isinstance(suites, SuiteBuilder):
        app = app.add_suite(suites)
    else:
        app = app.add_suites(*suites)

    return app.run(argv)


def _do_run(
    suites: list[SuiteBuilder],
    cli_args: argparse.Namespace,
    reporter: Reporter | None,
    build_params: Any,
    *,
    environment: EnvironmentCollector = SystemEnvironment(),
    denoise: bool = False,
) -> Report:
    """Run already-parsed benchmarks: build the reporter, plan the suites,
    apply CLI overrides and hand off to the runner."""
    if reporter is None:
        reporter = SummaryReporter(DefaultSummary())

    if denoise and not is_root():
        console.print(
            "[bench.failure]--denoise requires root "
            "(try: sudo bench run --denoise ...)[/]"
        )
        sys.exit(2)

    env = environment.collect()
    env_diagnostics = run_checks(env) if env is not None else []

    reporter = _build_reporter(
        cli_args,
        reporter,
        with_progress=not cli_args.no_progress,
        environment=env,
        diagnostics=env_diagnostics,
    )

    try:
        planned = plan(suites, build_params)
    except SuiteMaterializationError as e:
        print_exception(e)
        sys.exit(1)

    includes = getattr(cli_args, "include", None)
    excludes = getattr(cli_args, "exclude", None)
    try:
        planned = select(planned, includes, excludes)
    except re.error as e:
        console.print(f"[bench.failure]Invalid --include/--exclude regex: {e}[/]")
        sys.exit(2)

    if getattr(cli_args, "list_plan", False):
        console.print(_list_planned_benchmarks(planned))
        return Report()

    if (includes or excludes) and not planned:
        console.print(
            "[bench.failure]No benchmarks matched --include/--exclude "
            "(run with --list to see what's available).[/]"
        )
        sys.exit(1)

    _print_diagnostics(env_diagnostics, "Environment checks")

    runner = _make_runner(cli_args, reporter)

    try:
        if denoise:
            with denoise_session() as applied:
                console.print(
                    f"[bench.label]Denoise:[/] minimized {len(applied)} knob(s); "
                    f"state saved to {STATE_PATH}"
                )
                report = runner.run(planned, build_params)
        else:
            report = runner.run(planned, build_params)
    except KeyboardInterrupt:
        console.print("[bench.failure]Interrupted[/]")
        sys.exit(1)

    report.environment = env
    report.diagnostics = env_diagnostics
    return report


def _print_diagnostics(diagnostics: list[Diagnostic], title: str) -> None:
    if not diagnostics:
        return
    console.print(f"\n[bench.label]{title}:[/]")
    for d in diagnostics:
        tag = "[bench.failure]✗[/]" if d.severity == "high" else "[bench.warning]⚠[/]"
        console.print(f"  {tag} {markup_escape(d.message)}")
        if d.fix:
            console.print(f"      [dim]fix:[/] {markup_escape(d.fix)}")


def _build_reporter(
    ns: argparse.Namespace,
    summary_reporter: Reporter,
    *,
    with_progress: bool,
    environment: Environment | None = None,
    diagnostics: list[Diagnostic] | None = None,
) -> Reporter:
    sinks: list[Reporter] = []
    if with_progress:
        sinks.append(ProgressReporter())

    sinks.append(summary_reporter)

    if ns.json:
        sinks.append(
            JsonReporter(
                Path(ns.json), environment=environment, diagnostics=diagnostics
            )
        )

    if ns.csv:
        sinks.append(CsvReporter(Path(ns.csv), environment=environment))

    if ns.dir:
        sinks.append(
            DirReporter(Path(ns.dir), environment=environment, diagnostics=diagnostics)
        )

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
    p = argparse.ArgumentParser(prog="bench")
    _add_user_params(p, params)
    _add_runtime_flags(p.add_argument_group("bench flags"))
    _add_selection_flags(p.add_argument_group("selection"))
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


def _add_selection_flags(
    g: argparse.ArgumentParser | argparse._ArgumentGroup,  # pyright: ignore[reportPrivateUsage]
) -> None:
    g.add_argument(
        "--list",
        action="store_true",
        dest="list_plan",
        help="List the suite/benchmark/variant tree and exit (run nothing).",
    )
    g.add_argument(
        "--include",
        action="append",
        default=None,
        metavar="REGEX",
        help="Keep only benchmarks whose key matches REGEX, where the key is "
        "'suite/benchmark (k=v, ...)'. Repeatable (OR semantics).",
    )
    g.add_argument(
        "--exclude",
        action="append",
        default=None,
        metavar="REGEX",
        help="Drop benchmarks whose key matches REGEX. Repeatable; wins over --include.",
    )


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
    _add_runtime_flags(p)
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
    _do_run([s], ns, reporter, None, environment=environment, denoise=ns.denoise)
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
        _print_diagnostics(diagnostics, "Checks")
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


# ----- list ------------------------------------------------------------


def _list_planned_benchmarks(planned: list[Benchmark]) -> Tree:
    """Group planned benchmarks into a `suite → benchmark (variant)` tree.

    Each leaf is one resolved benchmark, labeled `name (k=v, …)`, with the
    explicit `variant_label` (if any) shown dimmed. The root carries a one-line
    count summary. This is what `--list` prints.
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
        for b in bs:
            leaf = Text(record_key(b.name, b.name, b.variant))
            node.add(leaf)
    return root
