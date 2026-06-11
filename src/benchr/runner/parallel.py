"""Parallel runner.

A flat work queue across N workers. Every ``(benchmark, run)`` execution is
materialized up front and spread across one thread pool — the fastest way to
get through a batch of tasks.

This is the *only* sound use of parallelism in a benchmark tool: wall-clock
timing under contention is meaningless, so ``Parallel`` is for work where time
is **not** the metric — test suites (pass/fail), smoke runs ("does everything
execute"). Convergence-driven (unbounded) or order-dependent policies cannot be
fanned out and are rejected up front; use ``Sequential`` for those.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.core.execution import ExecutionResult, ScheduledExecution
from benchr.core.process import execute, install_sigint_handler, interrupted
from benchr.core.sample import Report, RunRecord, Sample
from benchr.report.reporter import Reporter
from benchr.runner.base import (
    PlannedBenchmark,
    Runner,
    format_scheduled_verbose,
    judge,
)


class Parallel(Runner):
    """N-worker thread pool over a flat list of executions.

    Requires every benchmark to use a bounded, order-independent stopping
    policy (e.g. ``FixedRuns``). Convergence-driven or order-dependent policies
    are rejected — run those with ``Sequential``, or force a fixed run count
    with ``--runs N``.
    """

    def __init__(self, workers: int, reporter: Reporter | None = None,
                 **kwargs: Any) -> None:
        super().__init__(reporter, **kwargs)
        self.workers = workers
        # Guards the shared Report + serializes verbose blocks across workers.
        self._lock = threading.Lock()

    def _record(
        self,
        report: Report,
        sched: ScheduledExecution,
        result: ExecutionResult,
        samples: list[Sample],
    ) -> None:
        rec = RunRecord.from_result(sched, result, samples)
        with self._lock:
            report.add(rec)
        # Reporters guard their own state; no need to hold the lock.
        self.reporter.record(rec, result)

    @staticmethod
    def _parallelizable(b: Benchmark) -> bool:
        # Need both: independent (runs can be reordered) AND a known total
        # (we have to pre-materialize the execution list).
        return all(
            p.independent() and p.max_runs() is not None
            for p in (b.warmup, b.runs)
        )

    def run(
        self, planned: list[PlannedBenchmark], ctx: Any = None
    ) -> Report:
        # Validate up front: a flat work queue can only hold runs we can count
        # ahead of time and reorder freely. A harness benchmark is exempt —
        # it is a single execution, so there is nothing to reorder.
        for p in planned:
            if not p.benchmark.harness and not self._parallelizable(p.benchmark):
                raise ValueError(
                    f"Parallel cannot run benchmark {p.benchmark.name!r}: its "
                    f"stopping policy is unbounded or order-dependent "
                    f"(e.g. CoefficientOfVariation), so its runs can't be fanned "
                    f"out across workers. Run it with Sequential, or override "
                    f"the stopping policy with --runs N (forces FixedRuns)."
                )

        self.reporter.start([p.benchmark for p in planned])
        report = Report()

        # Flatten every (benchmark, run) into one work list; runs are numbered
        # continuously (warmup 1..W, measured W+1..W+R). A harness benchmark
        # is one work item: its single execution produces all its records.
        work: list[tuple[Benchmark, ScheduledExecution]] = []
        for p in planned:
            b = p.benchmark
            # Bounded policies are guaranteed (validation above / materialize).
            warmup_runs, measured = b.warmup.max_runs(), b.runs.max_runs()
            assert warmup_runs is not None and measured is not None
            total = 1 if b.harness else warmup_runs + measured
            first: ScheduledExecution | None = None
            for i in range(1, total + 1):
                sched = b.schedule(ctx, suite=p.suite, run=i)
                if first is None:
                    first = sched
                work.append((b, sched))
            if first is not None:
                if not b.harness:
                    self._warmup(report, first, warmup_runs)
                if self.verbose:
                    with self._lock:
                        print(format_scheduled_verbose(first, b))

        def _do(item: tuple[Benchmark, ScheduledExecution]):
            # Don't spawn anything once a Ctrl+C has fired — the kill sweep has
            # already run, so a process started now would be orphaned.
            if interrupted():
                return None
            b, sched = item
            return b, sched, execute(sched.execution)

        try:
            with install_sigint_handler():
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    for out in pool.map(_do, work):
                        if out is None:
                            continue
                        b, sched, result = out
                        if b.harness:
                            self._record_harness(b, sched, result, report)
                            continue
                        # Same judge+parse step as the sequential pump; a failed
                        # run emits no metrics, only a RunRecord.
                        result, samples = judge(b, result)
                        self._record(report, sched, result, samples)
                if interrupted():
                    raise KeyboardInterrupt
            return report
        finally:
            self.reporter.finalize()
