"""Dry runner: enumerates planned executions without spawning subprocesses.

Prints the plan straight to stdout and ignores any reporter / output sink.

A dry run shows the plan's *upper bound*: bounded policies enumerate runs
`1..warmup.max_runs() + runs.max_runs()` (a convergence-driven policy may
stop earlier on real observations). Unbounded policies (e.g.
CoefficientOfVariation) have no bound to enumerate, so they print one line
with an `[unbounded]` marker. A harness benchmark is one execution, so it
prints one line with a `[harness]` marker.
"""

from __future__ import annotations

from typing import Any

from bench.core.execution import format_identifier
from bench.core.sample import Report
from bench.grammar.benchmark import Benchmark
from bench.runner.base import (
    Runner,
    format_benchmark_verbose,
    format_command,
    format_policy,
)


class Dry(Runner):
    """Enumerate planned Executions per Benchmark. Do not subprocess.

    The `reporter` accepted by `Runner.__init__` is ignored. A dry run
    produces no results to report.
    """

    def run(self, planned: list[Benchmark], params: Any = None) -> Report:
        for b in planned:
            self._print_executions(b)
        return Report()

    def _print_executions(self, b: Benchmark) -> None:
        if b.harness:
            marker = (
                f"[harness, warmup {format_policy(b.warmup)}, "
                f"runs {format_policy(b.runs)}]"
            )
            self._print_one(b, 1, marker=marker)
            return
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
