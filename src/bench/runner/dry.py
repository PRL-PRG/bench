"""Dry runner: enumerates planned executions without spawning subprocesses.

Prints the plan straight to stdout, ignoring any reporter. What it shows is the
plan's *upper bound* - a convergence-driven policy may stop earlier, and an
unbounded one has nothing to enumerate at all, so it prints a single
`[unbounded]` line.
"""

from __future__ import annotations

from bench.model.benchmark import Benchmark, format_benchmark_verbose, format_identifier
from bench.model.invocation import format_command
from bench.model.results import Report
from bench.report import Reporter
from bench.runner.base import (
    Runner,
)


class DryRunner(Runner):
    """Enumerate planned Executions per Benchmark. Do not subprocess.

    The `reporter` is ignored - a dry run produces no results to report.
    """

    def run_with_report(
        self, planned: list[Benchmark], reporter: Reporter, report: Report
    ) -> None:
        for b in planned:
            self._print_executions(b)

    def _print_executions(self, b: Benchmark) -> None:
        warmup, runs = b.warmup.max_runs(), b.runs.max_runs()
        if warmup is None or runs is None:
            self._print_one(b, 1, marker="[unbounded]")
            return
        for run in range(1, warmup + runs + 1):
            self._print_one(b, run)

    def _print_one(self, b: Benchmark, run: int, *, marker: str = "") -> None:
        if self.verbose:
            block = format_benchmark_verbose(b, run)
        else:
            identifier = format_identifier(
                b.suite, b.name, b.variant, run, b.variant_label
            )
            block = f"{identifier}: `{format_command(b.invocation)}`"

        print(f"{block} {marker}" if marker else block)
