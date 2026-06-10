"""Dry runner: enumerates planned executions without spawning subprocesses.

Prints the plan straight to stdout and ignores any reporter / output sink.

Unbounded policies (e.g. CoefficientOfVariation) have no run count to
enumerate — they would loop forever — so we print one line with an
``[unbounded]`` marker instead.
"""

from __future__ import annotations

from typing import Any

from benchr.report.sample import Report
from benchr.runner.base import PlannedBenchmark, Runner, format_scheduled_verbose


class Dry(Runner):
    """Enumerate planned Executions per Benchmark; do not subprocess."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)

    def run(
        self, planned: list[PlannedBenchmark], ctx: Any = None
    ) -> Report:
        for p in planned:
            self._print_executions(p, ctx)
        return Report()

    def _print_executions(self, p: PlannedBenchmark, ctx: Any) -> None:
        b = p.benchmark
        bounded = (
            b.warmup.max_runs() is not None
            and b.runs.max_runs() is not None
        )
        gen = b.compile(ctx, suite=p.suite)
        try:
            sched = next(gen)
        except StopIteration:
            return
        try:
            if not bounded:
                self._print_one(sched, b, unbounded=True)
                return
            while True:
                self._print_one(sched, b, unbounded=False)
                try:
                    sched = gen.send([])
                except StopIteration:
                    return
        finally:
            gen.close()

    def _print_one(self, sched, benchmark, *, unbounded: bool) -> None:
        if self.verbose:
            block = format_scheduled_verbose(sched, benchmark)
        else:
            cmd = " ".join(sched.execution.command)
            block = f"{sched.identifier()}: {cmd}"

        print(f"{block} [unbounded]" if unbounded else block)
