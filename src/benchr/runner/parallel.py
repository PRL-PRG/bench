"""Parallel runner.

A flat work queue across N workers. Every ``(benchmark, run, phase)`` execution
is materialized up front and spread across one thread pool — the fastest way to
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
from benchr.grammar.execution import ExecutionResult, ScheduledExecution
from benchr.grammar.suite import Suite
from benchr.report.sample import Report, Sample
from benchr.runner.base import (
    _INTERRUPTED,
    Runner,
    execute,
    format_scheduled,
    install_sigint_handler,
    judge,
    plan,
)


class Parallel(Runner):
    """N-worker thread pool over a flat list of executions.

    Requires every benchmark to use a bounded, order-independent stopping
    policy (e.g. ``FixedRuns``). Convergence-driven or order-dependent policies
    are rejected — run those with ``Sequential``, or force a fixed run count
    with ``--runs N``.
    """

    def __init__(self, workers: int, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
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
        with self._lock:
            report.record(sched, result, samples)
        # Reporters guard their own state; no need to hold the lock.
        self.reporter.sample(sched, result, samples)

    @staticmethod
    def _parallelizable(b: Benchmark) -> bool:
        # Need both: independent (runs can be reordered) AND a known total
        # (we have to pre-materialize the execution list).
        return all(
            p.independent() and p.max_runs() is not None
            for p in (b.warmup_policy(), b.measure_policy())
        )

    def run(self, suites: Suite | list[Suite], ctx: Any = None) -> Report:
        planned = plan(suites, ctx)

        # Validate up front: a flat work queue can only hold runs we can count
        # ahead of time and reorder freely.
        for p in planned:
            if not self._parallelizable(p.benchmark):
                raise ValueError(
                    f"Parallel cannot run benchmark {p.benchmark.name!r}: its "
                    f"stopping policy is unbounded or order-dependent "
                    f"(e.g. CoefficientOfVariation), so its runs can't be fanned "
                    f"out across workers. Run it with Sequential, or override "
                    f"the stopping policy with --runs N (forces FixedRuns)."
                )

        self.reporter.start([p.benchmark for p in planned])
        report = Report()

        # Flatten every (benchmark, run, phase) into one work list.
        work: list[tuple[Benchmark, ScheduledExecution]] = []
        for p in planned:
            b = p.benchmark
            first: ScheduledExecution | None = None
            for phase, policy in (("warmup", b.warmup_policy()), ("measure", b.measure_policy())):
                for i in range(1, policy.max_runs() + 1):
                    sched = b.schedule(ctx, suite=p.suite, run=i, phase=phase)
                    if first is None:
                        first = sched
                    work.append((b, sched))
            if self.verbose and first is not None:
                with self._lock:
                    print(format_scheduled(first, b))

        def _do(item: tuple[Benchmark, ScheduledExecution]):
            # Don't spawn anything once a Ctrl+C has fired — the kill sweep has
            # already run, so a process started now would be orphaned.
            if _INTERRUPTED.is_set():
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
                        # Same judge+parse step as the sequential pump; a failed
                        # run emits no metrics, only a RunRecord.
                        result, samples = judge(b, sched, result)
                        self._record(report, sched, result, samples)
                if _INTERRUPTED.is_set():
                    raise KeyboardInterrupt
            return report
        finally:
            self.reporter.finalize()
