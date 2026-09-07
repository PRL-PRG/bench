"""Parallel runner: up to N benchmark `Controller`s at once (`--jobs N`).

Parallelism is per benchmark, not per run, so each keeps its own sequential
loop and convergence-driven policies still work. Timing under contention is
meaningless, so this is for work where time is **not** the metric: test suites,
smoke runs, or getting through a batch faster.

The shared `Report` and reporter are mutated from worker threads, hence the
lock-guarded proxies below.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from bench.core.process import interrupted
from bench.model.benchmark import Benchmark
from bench.model.results import Execution, Report
from bench.report import Reporter
from bench.runner.base import Runner


class _LockedReporter(Reporter):
    def __init__(self, reporter: Reporter, lock: threading.Lock) -> None:
        self._reporter = reporter
        self._lock = lock

    def benchmark_start(self, b: Benchmark) -> None:
        with self._lock:
            self._reporter.benchmark_start(b)

    def execution_done(self, execution: Execution) -> None:
        with self._lock:
            self._reporter.execution_done(execution)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        with self._lock:
            self._reporter.benchmark_done(b, executions)


class ParallelRunner(Runner):
    """Run up to N benchmark `Controller`s concurrently on a thread pool."""

    def __init__(
        self,
        workers: int,
        *,
        verbose: bool = False,
    ) -> None:
        super().__init__(verbose=verbose)
        self.workers = workers

    def run_with_report(
        self, planned: list[Benchmark], reporter: Reporter, report: Report
    ) -> None:
        lock = threading.Lock()
        locked_reporter = _LockedReporter(reporter, lock)

        def _one(p: Benchmark) -> None:
            # Don't start a benchmark once Ctrl+C has fired. The kill sweep
            # has already run, so a process started now would be orphaned.
            if interrupted():
                return

            exs = p.controller.run_benchmark(p, locked_reporter, self.verbose)
            with lock:
                report.add_all(exs)

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            list(pool.map(_one, planned))
