"""`BenchAppBuilder`: the top builder level, plus the benchmarking pipeline it
drives - plan the suites, build the reporter and runner, run, report."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Sequence, cast

from rich.text import Text
from rich.tree import Tree

from bench.builder.base import (
    BuilderBase,
    as_build,
    const,
    merge_sequence,
)
from bench.builder.default import default_filter, default_reporter, default_runner
from bench.builder.suite import SuiteBuilder, plan
from bench.console.theme import console, error_console
from bench.core.denoise import (
    STATE_PATH,
    denoise_session,
    is_root,
)
from bench.core.diagnostic import (
    print_diagnostics as do_print_diagnostics,
)
from bench.core.diagnostic import run_checks
from bench.core.fingerprint import (
    NoProbe,
    Probe,
)
from bench.error import BenchError, print_exception
from bench.model.benchmark import Benchmark, format_benchmark, format_variant
from bench.model.results import Report, report_from_json
from bench.params import (
    Params,
    ParamsGroup,
    SharedBenchParams,
    add_dataclass_args,
    build_dataclass,
)
from bench.report import (
    CompositeReporter,
    Reporter,
    SummaryReporter,
)
from bench.runner import (
    Runner,
)
from bench.summary.formatter import DefaultSummary
from bench.summary.summary import summarize

# ---------------------------------------------------------------------------
# Base types
# ---------------------------------------------------------------------------

# HACK: The argument should be "Params or its child" but this is the best
# we have for now
type ParamFactory[T] = Callable[[Any], T]
"""Builds a `T` from the resolved params object."""

type SuiteGenerator = ParamFactory[Sequence[SuiteBuilder]]


class NoBenchmarksMatchedError(BenchError):
    """No benchmark matched the --include/--exclude selection."""


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def as_param_build[T](value: T | ParamFactory[T]) -> ParamFactory[T]:
    if callable(value):
        return cast(ParamFactory[T], value)
    return const(value)


# ---------------------------------------------------------------------------
# The builder
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class BenchAppBuilder(BuilderBase):
    """Top-level builder: static suites + deferred suite generators, plus common
    settings applied to every suite.

    Its inheritable `.with_*` settings are the weakest layer - they only fill
    what a suite or benchmark left unset. `name` is the `--help` description.
    """

    name: str = ""
    suites: Sequence[SuiteGenerator] = ()

    params: type[Params] | None = None

    reporter: ParamFactory[Reporter] | None = None
    summary: ParamFactory[Reporter] | None = None
    runner: ParamFactory[Runner] | None = None
    probe: Probe = NoProbe()
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
        """Replace the params class whose fields become the CLI flags, so one
        app can be reused with a different parameter set."""
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

    def with_probe(self, probe: Probe, override: bool = True) -> BenchAppBuilder:
        """Set the probe that snapshots the machine (fingerprint + diagnostics)."""
        return self.replace(
            "probe",
            probe,
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
        # Setup probe
        fingerprint = self.probe.collect()
        diagnostics = run_checks(fingerprint) if fingerprint is not None else []

        if print_diagnostics:
            do_print_diagnostics(diagnostics, "Machine checks")

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
                return runner.run(planned, reporter, fingerprint, diagnostics)
        else:
            return runner.run(planned, reporter, fingerprint, diagnostics)

    # ----- run_cli -----------

    def run_cli(self, args: list[str] | argparse.Namespace | None = None) -> Report:
        """Resolve generators, apply app defaults, and run every suite."""

        params = (
            BenchAppParams(self.params)
            if self.params is not None
            else SharedBenchAppParams
        )

        if isinstance(args, argparse.Namespace):
            cli_args = args
        else:
            parser = argparse.ArgumentParser(description=self.name)
            add_dataclass_args(parser, params)
            cli_args = parser.parse_args(args)

        build_params = build_dataclass(params, cli_args)

        # --show
        if build_params.show is not None:
            return self.do_show_report(build_params, build_params.show)

        planned = self.plan_benchmarks(build_params, use_defaults=True)

        # --list
        if build_params.list:
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
    """Run one or more suites with default settings, returning their report.

    Sugar for `bench_app(<script>).add(*suites).run_cli()`. For anything richer
    build a `bench_app(...)` directly.
    """
    return bench_app(Path(sys.argv[0]).stem).add(*suites).run_cli()


def bench_app[P: Params](
    name: str = "",
    *,
    params: type[P] | None = None,
    reporter: Reporter | Callable[[P], Reporter] | None = None,
    summary: Reporter | Callable[[P], Reporter] | None = None,
    probe: Probe | None = None,
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
        probe=probe or NoProbe(),
        denoise=denoise,
    )


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------


def BenchAppParams[T: Params](t: type[T]) -> type[T]:
    default_actions_group = ParamsGroup("default actions")

    class Ps(t):
        list: bool = dataclasses.field(
            default=False,
            metadata={
                "group": default_actions_group,
                "action": "store_true",
                "help": "List the suite/benchmark/variant tree and exit (run nothing).",
            },
        )

        show: str | None = dataclasses.field(
            default=None,
            metadata={
                "group": default_actions_group,
                "metavar": "JSON",
                "help": "Render a previously saved JSON report with the default summary, then exit (run nothing).",
            },
        )

    return cast(type[T], Ps)


SharedBenchAppParams = BenchAppParams(SharedBenchParams)

# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------


# TODO: This should live somewhere else
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
