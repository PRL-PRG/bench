"""Parallel runner.

Runs up to N benchmark ``Controller``s concurrently — per-benchmark
parallelism, not per-run. Each benchmark drives its own internal sequential
feedback loop (or a streaming harness with its own reader thread), so
convergence-driven (CoV) and order-dependent policies run fine here, each on
its own worker.

This is the *only* sound use of parallelism in a benchmark tool: wall-clock
timing under contention is meaningless, so ``Parallel`` is for work where time
is **not** the metric — test suites (pass/fail), smoke runs ("does everything
execute"), or just getting through a batch faster.

``--jobs N`` means "up to N benchmarks at once." The shared ``Report`` is
mutated from worker threads, so both it and the reporter are wrapped in
lock-guarded proxies to keep concurrent ``add``/``warmup``/``metadata`` writes
from tearing.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from benchr.core.process import install_sigint_handler, interrupted
from benchr.core.sample import Report, RunRecord, Sample
from benchr.report.reporter import Reporter
from benchr.runner.base import PlannedBenchmark, Runner
from benchr.runner.controller import Controller


class _LockedReport(Report):
    """Write-only lock-guarded wrapper over a shared ``Report``.

    The Controller never *reads* report state mid-run — it only ``add``s
    records, marks ``warmup`` counts, and sets one ``metadata`` entry. Guard
    exactly those three mutation points so concurrent benchmarks can't tear
    ``Report.runs`` / ``warmups`` / ``metadata``. (Its own inherited slots are
    unused — all writes delegate to the shared ``report``.)"""

    def __init__(self, report: Report, lock: threading.Lock) -> None:
        super().__init__()
        self._report = report
        self._lock = lock

    def add(self, rec: RunRecord) -> None:
        with self._lock:
            self._report.add(rec)

    def warmup(self, key: str, runs: int) -> None:
        with self._lock:
            self._report.warmup(key, runs)

    def set_metadata(self, key: str, samples: list[Sample]) -> None:
        with self._lock:
            self._report.set_metadata(key, samples)


class _LockedReporter(Reporter):
    """Lock-guarded wrapper over the real reporter.

    Serializes ``record`` / ``process_done`` / ``warmup`` so events from
    benchmarks running on different workers don't interleave inside a single
    reporter call (e.g. a ``CompositeReporter`` fan-out). ``start`` /
    ``finalize`` are called once by ``Parallel`` itself, not per-controller."""

    def __init__(self, reporter: Reporter, lock: threading.Lock) -> None:
        self._reporter = reporter
        self._lock = lock

    def record(self, rec: RunRecord) -> None:
        with self._lock:
            self._reporter.record(rec)

    def process_done(self, sched: Any, result: Any) -> None:
        with self._lock:
            self._reporter.process_done(sched, result)

    def warmup(self, key: str, runs: int) -> None:
        with self._lock:
            self._reporter.warmup(key, runs)


class Parallel(Runner):
    """Run up to N benchmark ``Controller``s concurrently on a thread pool.

    Each planned benchmark gets its own ``Controller`` (an internal sequential
    feedback loop), so any stopping policy — fixed, convergence-driven, or
    order-dependent — runs fine; ``--jobs N`` just bounds how many run at once.
    """

    def __init__(self, workers: int, reporter: Reporter | None = None,
                 **kwargs: Any) -> None:
        super().__init__(reporter, **kwargs)
        self.workers = workers

    def run(
        self, planned: list[PlannedBenchmark], params: Any = None
    ) -> Report:
        self.reporter.start([p.benchmark for p in planned])
        report = Report()
        lock = threading.Lock()
        locked_report = _LockedReport(report, lock)
        locked_reporter = _LockedReporter(self.reporter, lock)

        def _one(p: PlannedBenchmark) -> None:
            # Don't start a benchmark once Ctrl+C has fired — the kill sweep
            # has already run, so a process started now would be orphaned.
            if interrupted():
                return
            Controller(
                locked_reporter,
                max_runs_per_policy=self.max_runs_per_policy,
                max_consecutive_failures=self.max_consecutive_failures,
                verbose=self.verbose,
            ).run_benchmark(p, params, locked_report)

        try:
            with install_sigint_handler():
                with ThreadPoolExecutor(max_workers=self.workers) as pool:
                    list(pool.map(_one, planned))
                if interrupted():
                    raise KeyboardInterrupt
            return report
        finally:
            self.reporter.finalize()
