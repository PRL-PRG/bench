"""Parallel runner.

Runs up to N benchmark `Controller`s concurrently (per-benchmark
parallelism, not per-run). Each benchmark drives its own internal sequential
feedback loop (or a streaming harness with its own reader thread), so
convergence-driven (CoV) and order-dependent policies run fine here, each on
its own worker.

This is the *only* sound use of parallelism in a benchmark tool: wall-clock
timing under contention is meaningless, so `Parallel` is for work where time
is **not** the metric: test suites (pass/fail), smoke runs ("does everything
execute"), or just getting through a batch faster.

`--jobs N` means "up to N benchmarks at once." The shared `Report` is
mutated from worker threads, so both it and the reporter are wrapped in
lock-guarded proxies to keep concurrent `add` writes from tearing.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from bench.core.process import interrupted
from bench.core.model import Iteration, Report, Execution
from bench.grammar.benchmark import Benchmark
from bench.report.reporter import Reporter
from bench.runner.base import Runner
from bench.runner.controller import Controller


class _LockedReport(Report):
    def __init__(self, report: Report, lock: threading.Lock) -> None:
        super().__init__()
        self._report = report
        self._lock = lock

    def add(self, execution: Execution) -> None:
        with self._lock:
            self._report.add(execution)


class _LockedReporter(Reporter):
    def __init__(self, reporter: Reporter, lock: threading.Lock) -> None:
        self._reporter = reporter
        self._lock = lock

    def benchmark_start(self, b: Benchmark) -> None:
        with self._lock:
            self._reporter.benchmark_start(b)

    def iteration(self, it: Iteration, label: str) -> None:
        with self._lock:
            self._reporter.iteration(it, label)

    def execution_done(self, execution: Execution) -> None:
        with self._lock:
            self._reporter.execution_done(execution)

    def benchmark_done(self, b: Benchmark, executions: list[Execution]) -> None:
        with self._lock:
            self._reporter.benchmark_done(b, executions)


class Parallel(Runner):
    """Run up to N benchmark `Controller`s concurrently on a thread pool."""

    def __init__(
        self, workers: int, reporter: Reporter | None = None, **kwargs: Any
    ) -> None:
        super().__init__(reporter, **kwargs)
        self.workers = workers

    def run(self, planned: list[Benchmark], params: Any = None) -> Report:
        with self._session(planned) as report:
            lock = threading.Lock()
            locked_report = _LockedReport(report, lock)
            locked_reporter = _LockedReporter(self.reporter, lock)

            def _one(p: Benchmark) -> None:
                # Don't start a benchmark once Ctrl+C has fired. The kill sweep
                # has already run, so a process started now would be orphaned.
                if interrupted():
                    return
                Controller(
                    locked_reporter,
                    verbose=self.verbose,
                ).run_benchmark(p, locked_report)

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                list(pool.map(_one, planned))
        return report
