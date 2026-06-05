"""Dry runner: enumerates planned executions without spawning subprocesses.

Prints the plan straight to stdout and ignores any reporter / output sink
(``--json`` / ``--csv`` / ``--dir`` have no effect under ``--dry``). Without
``verbose`` it prints one compact line per scheduled execution
(``suite/benchmark (variant) #run [phase]: command``); with ``verbose`` it
prints the full per-execution block once per benchmark (the same one
``--verbose`` echoes for the real runners) plus the run plan summary.

Unbounded policies (e.g. CoefficientOfVariation) have no run count to
enumerate — they would loop forever — so we print one line with an
``[unbounded]`` marker instead.
"""

from __future__ import annotations

from typing import Any

from benchr.grammar.suite import Suite
from benchr.report.sample import Report
from benchr.runner.base import PlannedBenchmark, Runner, format_scheduled, plan


class Dry(Runner):
    """Enumerate planned Executions per Benchmark; do not subprocess."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)

    def run(self, suites: list[Suite], ctx: Any) -> Report:
        planned = plan(suites, ctx)
        for p in planned:
            self._print_executions(p, ctx)
        return Report()

    def _print_executions(self, p: PlannedBenchmark, ctx: Any) -> None:
        b = p.benchmark
        bounded = (
            b.warmup.max_runs() is not None and b.measure.max_runs() is not None
        )
        gen = b.compile(ctx, suite=p.suite)
        try:
            sched = next(gen)
        except StopIteration:
            return
        try:
            if not bounded:
                # Convergence-driven policy: emit a single representative entry
                # with an [unbounded] marker rather than spinning forever.
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
            # Dry enumerates every scheduled execution — the per-block plan
            # summary becomes redundant (phase + run # already in the header).
            block = format_scheduled(sched, benchmark, include_plan=False)
            print(f"{block} [unbounded]" if unbounded else block)
        else:
            cmd = " ".join(sched.execution.command)
            line = f"{sched.identifier()}: {cmd}"
            print(f"{line} [unbounded]" if unbounded else line)
