"""Dry runner: enumerates planned executions without spawning subprocesses.

Prints the plan straight to stdout and ignores any reporter / output sink.

A dry run shows the plan's *upper bound*: bounded policies enumerate runs
``1..warmup.max_runs() + runs.max_runs()`` (a convergence-driven policy may
stop earlier on real observations). Unbounded policies (e.g.
CoefficientOfVariation) have no bound to enumerate, so they print one line
with an ``[unbounded]`` marker. A harness benchmark is one execution, so it
prints one line with a ``[harness]`` marker — ``[harness ≤N]`` when a
``max_iterations`` bound is set.
"""

from __future__ import annotations

from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.core.execution import ScheduledExecution
from benchr.core.sample import Report
from benchr.runner.base import PlannedBenchmark, Runner, format_scheduled_verbose


class Dry(Runner):
    """Enumerate planned Executions per Benchmark; do not subprocess.

    The ``reporter`` accepted by ``Runner.__init__`` is ignored — a dry run
    produces no results to report.
    """

    def run(
        self, planned: list[PlannedBenchmark], params: Any = None
    ) -> Report:
        for p in planned:
            self._print_executions(p, params)
        return Report()

    def _print_executions(self, p: PlannedBenchmark, params: Any) -> None:
        b = p.benchmark
        if b.harness:
            marker = (
                f"[harness ≤{b.max_iterations}]"
                if b.max_iterations is not None
                else "[harness]"
            )
            self._print_one(b.schedule(params, suite=p.suite, run=1), b,
                            marker=marker)
            return
        warmup, runs = b.warmup.max_runs(), b.runs.max_runs()
        if warmup is None or runs is None:
            self._print_one(b.schedule(params, suite=p.suite, run=1), b,
                            marker="[unbounded]")
            return
        for run in range(1, warmup + runs + 1):
            self._print_one(b.schedule(params, suite=p.suite, run=run), b)

    def _print_one(self, sched: ScheduledExecution, benchmark: Benchmark,
                   *, marker: str = "") -> None:
        if self.verbose:
            block = format_scheduled_verbose(sched, benchmark)
        else:
            cmd = " ".join(sched.execution.command)
            block = f"{sched.identifier()}: {cmd}"

        print(f"{block} {marker}" if marker else block)
