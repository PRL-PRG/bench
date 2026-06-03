"""Parallel runner.

Two-tier parallelism:

  ``Parallel(n)``                         n workers; each worker drives one
                                          full Benchmark coroutine end-to-end.
                                          Within one benchmark, runs are still
                                          sequential — required for any policy
                                          that observes per-run state.

  ``Parallel(n, fanout=True)``            opt-in: for benchmarks whose ``measure``
                                          policy is exactly ``FixedRuns(N)`` and
                                          whose ``warmup`` is exactly
                                          ``FixedRuns(M)``, fan out the
                                          (warmup → measure) sequence across
                                          workers. Convergence-driven policies
                                          stay sequential.
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Any

from benchr.grammar.benchmark import Benchmark
from benchr.grammar.processor import stamp
from benchr.grammar.suite import Suite
from benchr.report.sample import Sample
from benchr.runner.base import (
    PlannedBenchmark,
    Runner,
    default_success,
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
        self._lock = Lock()

    def run(self, suites: list[Suite], ctx: Any) -> list[Sample]:
        planned = plan(suites, ctx)
        self.reporter.start([p.benchmark for p in planned])
        all_samples: list[Sample] = []
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._dispatch, p, ctx) for p in planned]
                for f in futures:
                    all_samples.extend(f.result())
            return all_samples
        finally:
            self.reporter.finalize()

    def _dispatch(self, p: PlannedBenchmark, ctx: Any) -> list[Sample]:
        if self.fanout and self._fanout_eligible(p.benchmark):
            return self._run_fanout(p, ctx)
        return self._run_benchmark_locked(p, ctx)

    def _run_benchmark_locked(self, p: PlannedBenchmark, ctx: Any) -> list[Sample]:
        # The base pump is single-threaded per benchmark; we only need to lock
        # around mutations of shared reporter state which the Reporter
        # implementations themselves must guard.
        return self._run_benchmark(p, ctx)

    # ----- fan-out path for order-independent, bounded benchmarks -----

    @staticmethod
    def _fanout_eligible(b: Benchmark) -> bool:
        # Need both: independent (runs can be reordered) AND a known total
        # (we have to pre-materialize the execution list).
        return all(
            p.independent() and p.max_runs() is not None
            for p in (b.warmup, b.measure)
        )

    def _run_fanout(self, p: PlannedBenchmark, ctx: Any) -> list[Sample]:
        b = p.benchmark
        if b.processor is None:
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

        out: list[Sample] = []
        # This runs inside an outer-pool worker, so cap the inner pool at the
        # number of executions to avoid spawning idle threads (the outer pool
        # already provides cross-benchmark parallelism).
        inner_workers = max(1, min(self.workers, len(all_sched)))
        with ThreadPoolExecutor(max_workers=inner_workers) as pool:
            for sched, pr in zip(all_sched, pool.map(lambda s: execute(s.execution), all_sched)):
                # Failed runs emit no metrics; the Reporter records them from pr.
                reason = (b.success or default_success)(sched.execution, pr)
                if reason is not None and pr.failure is None:
                    pr = dataclasses.replace(pr, failure=reason)
                if not pr.is_failure():
                    samples = list(stamp(b.processor.process(pr), sched))
                else:
                    samples = []
                out.extend(samples)
                with self._lock:
                    self.reporter.sample(sched, pr, samples)
        return out
