"""Parallel runner.

Two-tier parallelism:

  ``Parallel(n)``                         n workers; each worker drives one
                                          full Benchmark coroutine end-to-end.
                                          Within one benchmark, runs are still
                                          sequential — required for any policy
                                          that observes per-run state.

  ``Parallel(n, fanout=True)``            opt-in: for benchmarks whose ``warmup``
                                          and ``measure`` policies are both
                                          ``independent()`` and bounded
                                          (``max_runs()`` is not ``None`` — e.g.
                                          ``FixedRuns``), fan the individual runs
                                          out across workers. Order-dependent or
                                          unbounded (convergence-driven) policies
                                          stay sequential.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.suite import Suite
from benchr.report.sample import Report
from benchr.runner.base import (
    PlannedBenchmark,
    Runner,
    judge,
    execute,
    plan,
)


class Parallel(Runner):
    """N-worker thread pool across benchmarks (and optionally runs)."""

    def __init__(
        self,
        workers: int,
        *args,
        fanout: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.workers = workers
        self.fanout = fanout

    def run(self, suites: list[Suite], ctx: Any) -> Report:
        planned = plan(suites, ctx)
        self.reporter.start([p.benchmark for p in planned])
        report = Report()
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._dispatch, p, ctx, report) for p in planned]
                for f in futures:
                    f.result()
            return report
        finally:
            self.reporter.finalize()

    def _dispatch(self, p: PlannedBenchmark, ctx: Any, report: Report) -> None:
        # The base pump is single-threaded per benchmark; shared Report and
        # Reporter mutations are guarded inside ``_record`` / the reporters.
        if self.fanout and self._fanout_eligible(p.benchmark):
            self._run_fanout(p, ctx, report)
        else:
            self._run_benchmark(p, ctx, report)

    # ----- fan-out path for order-independent, bounded benchmarks -----

    @staticmethod
    def _fanout_eligible(b: Benchmark) -> bool:
        # Need both: independent (runs can be reordered) AND a known total
        # (we have to pre-materialize the execution list).
        return all(
            p.independent() and p.max_runs() is not None
            for p in (b.warmup, b.measure)
        )

    def _run_fanout(self, p: PlannedBenchmark, ctx: Any, report: Report) -> None:
        b = p.benchmark
        if not b.processors:
            raise ValueError(f"Benchmark {b.name!r} has no processor")
        n_warm = b.warmup.max_runs()
        n_meas = b.measure.max_runs()
        assert n_warm is not None and n_meas is not None  # gated by _fanout_eligible

        # Pre-materialize scheduled executions; the policy promised the runs
        # are order-independent, so we can submit them in parallel.
        warm = [
            b.schedule(ctx, suite=p.suite, run=i, phase="warmup")
            for i in range(1, n_warm + 1)
        ]
        meas = [
            b.schedule(ctx, suite=p.suite, run=i, phase="measure")
            for i in range(1, n_meas + 1)
        ]
        all_sched = warm + meas

        # This runs inside an outer-pool worker, so cap the inner pool at the
        # number of executions to avoid spawning idle threads (the outer pool
        # already provides cross-benchmark parallelism).
        inner_workers = max(1, min(self.workers, len(all_sched)))
        with ThreadPoolExecutor(max_workers=inner_workers) as pool:
            for sched, result in zip(all_sched, pool.map(lambda s: execute(s.execution), all_sched)):
                # Same judge+parse step as the sequential pump; a failed run
                # emits no metrics, only a RunRecord.
                result, samples = judge(b, sched, result)
                self._record(report, sched, result, samples)
